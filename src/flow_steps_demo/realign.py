"""Realign eval logs to a changed task so Flow matches them again.

Relies on private inspect_flow / inspect_ai APIs, validated against
inspect-flow 0.10.0 / inspect-ai 0.3.246. If an upgrade breaks an import
here, re-check these modules first.

Several functions deliberately mirror upstream code line-for-line so drift
can be spotted by diffing side by side (line numbers as of the versions
above):

- log_fields / target_fields / _norm mirror the two branches and the hash
  serialization of task_identifier, inspect_ai/_eval/evalset.py:1286-1429
- resolve_spec_targets mirrors get_task_ids_to_tasks,
  inspect_flow/_runner/logs.py:80-114, and the load->resolve->instantiate
  ordering of _prepare_run, inspect_flow/_runner/run.py:81-127
- the realign step's copy loop follows the built-in copy step's
  write_dirty/dry_run pattern, inspect_flow/_steps/copy.py

If the identifier algorithm changes upstream, apply_target_fields' verify
assert fails loudly rather than producing non-matching copies.
"""

import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from inspect_ai._eval.eval import eval_resolve_tasks
from inspect_ai._eval.evalset import (
    EvalSetArgsInTaskIdentifier,
    _GENERATE_CONFIG_FIELDS_TO_EXCLUDE,
    model_args_for_log,
    model_roles_to_model_roles_config,
    plan_to_eval_plan,
    resolve_plan,
    resolve_solver,
    task_identifier,
    to_json_safe,
)
from inspect_ai._eval.task.resolved import ResolvedTask
from inspect_ai._util.file import absolute_file_path, basename, copy_file
from inspect_ai.log import EvalLog, read_eval_log
from inspect_ai.model import GenerateConfig, get_model
from inspect_flow._config.load import ConfigOptions, int_load_spec
from inspect_flow._runner.instantiate import instantiate_tasks
from inspect_flow._runner.resolve import resolve_spec
from inspect_flow._steps.context import step_context
from inspect_flow._store.store import is_better_log
from inspect_flow._types.flow_types import FlowOptions
from inspect_flow._util.not_given import default_none
from pydantic_core import to_json
from rich.console import Console
from upath import UPath

from inspect_flow import step
from inspect_flow.api import metadata, tag

from flow_steps_demo.constants import TAG_REALIGNED

# All user-facing terminal output (explain report, dry-run lines) goes
# through this shared rich console.
_console = Console()

# Header fields that feed the task identifier, in display order. Model is
# deliberately absent: it is the pairing criterion, never rewritten.
IDENTIFIER_FIELDS = [
    "task_file",
    "task_args_passed",
    "task_version",
    "message_limit",
    "token_limit",
    "token_limit_type",
    "turn_limit",
    "time_limit",
    "working_limit",
    "cost_limit",
    "model_args",
    "model_generate_config",
    "model_roles",
    "plan",
    # Comparing token_limit and token_limit_type separately is equivalent
    # to the token_limit_hash_value(limit, type) encoding task_identifier uses.
]


@step
def realign(
    logs: list[EvalLog],
    spec: str,
    spec_args: dict[str, Any] | None = None,
    dest: str | None = None,
    task: str | None = None,
    model: str | None = None,
    realign_all: bool = False,
) -> list[EvalLog]:
    """Copy near-miss logs and rewrite their headers to match a changed spec.

    For each spec task without a perfect match, finds near-miss logs (same
    task and model, task args agreeing on all shared keys), copies the best
    one to `dest` with a `+realigned` filename suffix, rewrites every
    identifier-relevant header field from the spec, verifies the recomputed
    identifier matches exactly, and records provenance (tag + metadata).
    Originals are never modified. Without `dest`, prints the field-by-field
    mismatch report and writes nothing (explain mode).

    Explain why logs don't match:
        flow step realign --store @STORE_PATH \\
            --spec src/flow_steps_demo/alignment_probe/spec.py \\
            --spec-args model=openai/gpt-4o

    Realign (with --store the copies are imported automatically):
        flow step realign --store @STORE_PATH --spec ... --spec-args ... \\
            --dest @LOG_DIR_DEV/realigned

    Args:
        spec: Path to the spec file defining the target tasks.
        spec_args: Args forwarded to the spec function (e.g. model=...).
        dest: Directory for realigned copies (must differ from the source
            directory). Omit to run in explain mode.
        task: Only consider logs whose eval.task matches this glob.
        model: Only consider logs whose eval.model matches this glob.
        realign_all: Realign every near-miss instead of only the best one.
    """
    targets = resolve_spec_targets(spec, spec_args)
    plans = plan_realignment(
        targets, logs, task=task, model=model, realign_all=realign_all
    )
    _print_report(plans)
    if dest is None:
        return []

    # Validate every chosen log's source directory against dest up front,
    # before any copy happens, so a multi-directory input can't have some
    # copies land on disk before an error aborts the step partway through.
    dest_str = str(UPath(dest))
    for p in plans:
        for original in p.chosen:
            src_dir = str(UPath(original.location).parent)
            if dest_str == src_dir:
                raise ValueError(
                    f"dest must differ from the source directory: {src_dir}"
                )

    results: list[EvalLog] = []
    # Re-entrant context + upfront flush + dry_run gate follow the built-in
    # copy step (inspect_flow/_steps/copy.py @ 0.10.0). This loop is inlined
    # only because copy can't rename copies (the +realigned suffix); if
    # https://github.com/meridianlabs-ai/inspect_flow/issues/756 lands,
    # replace it with the built-in copy step + the rewrite pass.
    with step_context(logs) as context:
        context.write_dirty()
        for p in plans:
            for original in p.chosen:
                stem, ext = os.path.splitext(basename(original.location))
                dest_path = f"{str(dest).rstrip('/')}/{stem}+realigned{ext}"
                if UPath(dest_path).exists():
                    if context.dry_run:
                        _console.print(f"    (exists, would reuse: {dest_path})")
                        continue
                    # Reuse a copy from a previous run so re-runs are
                    # idempotent and downstream steps still see it - but only
                    # if it matches the current target (it may be stale from
                    # an older spec revision).
                    existing = read_eval_log(dest_path, header_only=True)
                    if task_identifier(existing, None) == p.target_id:
                        _console.print(f"    (exists, reusing: {dest_path})")
                        results.append(existing)
                    else:
                        _console.print(
                            f"    (exists but does not match the current "
                            f"target - created from an older spec? remove it "
                            f"or use a fresh --dest: {dest_path})"
                        )
                    continue
                if context.dry_run:
                    _console.print(
                        f"    would copy {original.location} -> {dest_path}"
                    )
                    continue
                copy_file(original.location, dest_path)
                copy_header = read_eval_log(dest_path, header_only=True)
                changed = apply_target_fields(copy_header, p.resolved, p.target_id)
                [copy_header] = metadata(
                    [copy_header],
                    set={
                        "realigned_from": original.location,
                        "realigned_from_identifier": task_identifier(original, None),
                        "realigned_fields": changed,
                    },
                )
                [copy_header] = tag(
                    [copy_header],
                    add=[TAG_REALIGNED],
                    reason="realigned to match spec: " + ", ".join(changed),
                )
                results.append(copy_header)
    return results


def log_fields(header: EvalLog) -> dict[str, Any]:
    """Identifier-relevant fields as stored in a log header.

    Mirrors the EvalLog branch of task_identifier
    (inspect_ai/_eval/evalset.py:1354-1374 @ 0.3.246).
    """
    e = header.eval
    return {
        "task_file": e.task_file or None,
        "task_args_passed": e.task_args_passed,
        "task_version": e.task_version,
        "message_limit": e.config.message_limit,
        "token_limit": e.config.token_limit,
        "token_limit_type": e.config.token_limit_type,
        "turn_limit": e.config.turn_limit,
        "time_limit": e.config.time_limit,
        "working_limit": e.config.working_limit,
        "cost_limit": e.config.cost_limit,
        "model_args": e.model_args,
        "model_generate_config": e.model_generate_config,
        "model_roles": e.model_roles or {},
        "plan": header.plan,
    }


def target_fields(resolved: ResolvedTask) -> dict[str, Any]:
    """Identifier-relevant fields of a resolved spec task, in header shape.

    Mirrors the ResolvedTask branch of inspect_ai's task_identifier
    (inspect_ai/_eval/evalset.py:1314-1353 @ 0.3.246) with the same
    EvalSetArgs Flow passes (empty GenerateConfig, no solver, no limits).
    """
    # Upstream, resolve_solver normalizes an eval-set-level solver override
    # (Solver/SolverSpec/Agent/list). Flow never passes one, so this is a
    # no-op returning None kept for 1:1 correspondence with the original.
    solver = resolve_solver(None)
    plan = resolve_plan(resolved.task, solver)
    eval_plan = plan_to_eval_plan(plan, resolved.task.config.merge(GenerateConfig()))
    return {
        "task_file": resolved.task_file or None,
        "task_args_passed": resolved.task_args,
        "task_version": resolved.task.version,
        "message_limit": resolved.task.message_limit,
        "token_limit": resolved.task.token_limit,
        "token_limit_type": resolved.task.token_limit_type,
        "turn_limit": resolved.task.turn_limit,
        "time_limit": resolved.task.time_limit,
        "working_limit": resolved.task.working_limit,
        "cost_limit": resolved.task.cost_limit,
        "model_args": model_args_for_log(resolved.model.model_args),
        "model_generate_config": resolved.model.config,
        "model_roles": model_roles_to_model_roles_config(resolved.model_roles) or {},
        "plan": eval_plan,
    }


def _norm(field_name: str, value: Any) -> bytes:
    """Serialize a field value the way task_identifier hashes it.

    Mirrors the hash-input serialization of task_identifier — plan stripped
    of finish/per-step params, generate-config transient fields excluded
    (inspect_ai/_eval/evalset.py:1375-1426 @ 0.3.246).
    """
    if field_name == "plan":
        value = value.model_copy(
            update={
                "finish": None,
                "steps": [
                    s.model_copy(update={"params": None}) for s in value.steps
                ],
            }
        )
        return to_json_safe(
            value, exclude={"config": _GENERATE_CONFIG_FIELDS_TO_EXCLUDE}
        )
    if field_name == "model_generate_config":
        return to_json_safe(value, exclude=_GENERATE_CONFIG_FIELDS_TO_EXCLUDE)
    return to_json(value, exclude_none=True, fallback=lambda _x: None)


@dataclass
class FieldDiff:
    field: str
    log_value: Any
    spec_value: Any


def diff_field_dicts(
    log_f: dict[str, Any], target_f: dict[str, Any]
) -> list[FieldDiff]:
    return [
        FieldDiff(name, log_f[name], target_f[name])
        for name in IDENTIFIER_FIELDS
        if _norm(name, log_f[name]) != _norm(name, target_f[name])
    ]


def diff_fields(header: EvalLog, resolved: ResolvedTask) -> list[FieldDiff]:
    """Field-by-field mismatch between a log header and a resolved spec task."""
    return diff_field_dicts(log_fields(header), target_fields(resolved))


def resolve_spec_targets(
    spec_file: str, spec_args: dict[str, Any] | None = None
) -> dict[str, ResolvedTask]:
    """Load a spec file and return {task_identifier: ResolvedTask}.

    Follows the same resolution path flow run/check uses — the
    load -> resolve_spec -> instantiate ordering of _prepare_run
    (inspect_flow/_runner/run.py:81-127 @ 0.10.0) and the
    eval_resolve_tasks/task_identifier calls of get_task_ids_to_tasks
    (inspect_flow/_runner/logs.py:80-114 @ 0.10.0) — but keeps the
    ResolvedTask so header field values can be extracted from it.
    """
    spec_path = absolute_file_path(spec_file)
    base_dir = str(Path(spec_path).parent)
    spec = int_load_spec(spec_path, options=ConfigOptions(args=spec_args or {}))
    spec = resolve_spec(spec, base_dir)
    instantiated = instantiate_tasks(spec, base_dir=base_dir)
    options = spec.options or FlowOptions()
    resolved, _ = eval_resolve_tasks(
        tasks=[t.task for t in instantiated],
        task_args={},
        models=[get_model("none")],
        model_roles=None,
        config=GenerateConfig(),
        approval=default_none(options.approval),
        sandbox=default_none(options.sandbox),
        sample_shuffle=default_none(options.sample_shuffle),
    )
    eval_set_args = EvalSetArgsInTaskIdentifier(config=GenerateConfig())
    return {task_identifier(rt, eval_set_args): rt for rt in resolved}


@dataclass
class RealignPlan:
    """Realignment decision for one spec task."""

    target_id: str
    resolved: ResolvedTask
    perfect: EvalLog | None = None
    chosen: list[EvalLog] = field(default_factory=list)
    skipped: list[EvalLog] = field(default_factory=list)
    incompatible: list[EvalLog] = field(default_factory=list)
    ambiguous: list[EvalLog] = field(default_factory=list)


def _args_compatible(
    log_args: dict[str, Any], target_args: dict[str, Any]
) -> bool:
    """True when the two arg dicts agree on every key they share.

    Added/removed keys are exactly what realignment rewrites; conflicting
    shared keys mean the log belongs to a different matrix combo.
    """
    return all(log_args[k] == target_args[k] for k in set(log_args) & set(target_args))


def plan_realignment(
    targets: dict[str, ResolvedTask],
    logs: list[EvalLog],
    task: str | None = None,
    model: str | None = None,
    realign_all: bool = False,
) -> list[RealignPlan]:
    """Pair each spec target with the near-miss logs that could be realigned to it.

    A log identifier-equal to a target is a perfect match for it; that plan's
    other buckets are left empty since a perfect match makes them moot.
    Otherwise, a log is a candidate for a (non-perfectly-matched) target only
    if it shares the target's task/model and every task arg key it shares
    with the target agrees - added/removed keys (e.g. a new matrix
    dimension) are exactly what realignment rewrites, conflicting shared
    keys mean the log belongs to a different matrix combo. A log compatible
    with more than one target this way is ambiguous: which target it really
    belongs to is a judgment call, so it is reported in `ambiguous` on every
    plan it matches rather than silently claimed by one.
    """
    log_ids: dict[str, str] = {}
    for log in logs:
        try:
            log_ids[log.location] = task_identifier(log, None)
        except Exception:
            continue  # malformed header: not a candidate

    plans: dict[str, RealignPlan] = {
        target_id: RealignPlan(target_id=target_id, resolved=resolved)
        for target_id, resolved in targets.items()
    }

    # Pass 1: perfect (identifier-equal) matches claim their target outright.
    # If two logs share the same log_id, keep the better one (is_better_log)
    # rather than letting iteration order silently pick the last one seen.
    claimed: set[str] = set()
    for log in logs:
        log_id = log_ids.get(log.location)
        if log_id in plans:
            existing = plans[log_id].perfect
            if existing is None or is_better_log(log, existing):
                plans[log_id].perfect = log
            claimed.add(log.location)

    non_perfect = [plan for plan in plans.values() if plan.perfect is None]
    candidates: dict[str, list[EvalLog]] = {plan.target_id: [] for plan in non_perfect}

    # Pass 2: for the remaining targets, work out how many of them each log is
    # compatible with before assigning it anywhere, so a log that fits more
    # than one target becomes ambiguous instead of being claimed by whichever
    # target happened to be visited first. Logs already claimed as a perfect
    # match in pass 1 must be excluded here entirely - otherwise a perfect
    # match for one target can leak into a sibling target's incompatible or
    # even chosen bucket.
    for log in logs:
        if log_ids.get(log.location) is None:
            continue
        if log.location in claimed:
            continue
        if task and not fnmatch(log.eval.task, task):
            continue
        if model and not fnmatch(str(log.eval.model), model):
            continue
        compatible: list[RealignPlan] = []
        for plan in non_perfect:
            resolved = plan.resolved
            if log.eval.task != resolved.task.name or str(log.eval.model) != str(
                resolved.model
            ):
                continue
            if _args_compatible(log.eval.task_args_passed, resolved.task_args):
                compatible.append(plan)
            else:
                plan.incompatible.append(log)
        if len(compatible) == 1:
            candidates[compatible[0].target_id].append(log)
        elif len(compatible) > 1:
            for plan in compatible:
                plan.ambiguous.append(log)

    for plan in non_perfect:
        pool = list(candidates[plan.target_id])
        if not pool:
            continue
        # order best-first using flow's own store selection rule
        ordered: list[EvalLog] = []
        while pool:
            best = None
            for c in pool:
                if is_better_log(c, best):
                    best = c
            ordered.append(best)
            pool.remove(best)
        plan.chosen = ordered if realign_all else ordered[:1]
        plan.skipped = [] if realign_all else ordered[1:]

    return list(plans.values())


def apply_target_fields(
    header: EvalLog, resolved: ResolvedTask, target_id: str
) -> list[str]:
    """Rewrite the header's identifier fields to match the resolved task.

    Mutates in place. Raises RuntimeError if the rewritten header's
    recomputed identifier does not equal target_id (the perfect-match
    guarantee). Returns the names of the fields that changed.
    """
    changed = [d.field for d in diff_fields(header, resolved)]
    tf = target_fields(resolved)
    e = header.eval
    removed = set(e.task_args_passed) - set(tf["task_args_passed"])
    e.task_args = {
        k: v
        for k, v in {**e.task_args, **tf["task_args_passed"]}.items()
        if k not in removed
    }
    e.task_args_passed = dict(tf["task_args_passed"])
    e.task_file = tf["task_file"]
    e.task_version = tf["task_version"]
    e.config.message_limit = tf["message_limit"]
    e.config.token_limit = tf["token_limit"]
    e.config.token_limit_type = tf["token_limit_type"]
    e.config.turn_limit = tf["turn_limit"]
    e.config.time_limit = tf["time_limit"]
    e.config.working_limit = tf["working_limit"]
    e.config.cost_limit = tf["cost_limit"]
    e.model_args = tf["model_args"]
    e.model_generate_config = tf["model_generate_config"]
    e.model_roles = tf["model_roles"] or None
    header.plan = tf["plan"]

    new_id = task_identifier(header, None)
    if new_id != target_id:
        raise RuntimeError(
            f"realign failed verification for {header.location}: "
            f"rewritten identifier\n  {new_id}\ndoes not equal target\n  {target_id}"
        )
    return changed


def _short(value: Any) -> str:
    text = _norm("_", value).decode() if not isinstance(value, str) else value
    return text if len(text) <= 60 else text[:57] + "..."


def _print_field_diff(d: FieldDiff) -> None:
    if d.field == "task_args_passed":
        _console.print("      task_args_passed:")
        old, new = d.log_value, d.spec_value
        for k in sorted(set(old) | set(new)):
            if k not in new:
                _console.print(f"        - {k}={old[k]!r} (removed)")
            elif k not in old:
                _console.print(f"        + {k}={new[k]!r} (added)")
            elif old[k] != new[k]:
                _console.print(f"        ~ {k}: {old[k]!r} -> {new[k]!r}")
    else:
        _console.print(
            f"      {d.field}: {_short(d.log_value)} -> {_short(d.spec_value)}"
        )


def _print_report(plans: list[RealignPlan]) -> None:
    matched = [p for p in plans if p.perfect is not None]
    to_realign = [p for p in plans if p.chosen]
    unmatched = [p for p in plans if p.perfect is None and not p.chosen]

    _console.print(
        f"\n[bold]realign:[/bold] {len(matched)} matched, "
        f"{len(to_realign)} realignable, {len(unmatched)} without candidates\n"
    )
    for p in to_realign:
        args = ", ".join(f"{k}={v!r}" for k, v in p.resolved.task_args.items())
        _console.print(
            f"  [yellow]≈[/yellow] {p.resolved.task.name} ({args}) {p.resolved.model}"
        )
        for log in p.chosen:
            _console.print(f"    candidate {log.location}:")
            for d in diff_fields(log, p.resolved):
                _print_field_diff(d)
        for log in p.skipped:
            _console.print(f"    (skipped duplicate: {log.location})")
        for log in p.ambiguous:
            _console.print(
                f"    (matches multiple targets — narrow the spec with "
                f"--spec-args or realign it against a single-target spec: "
                f"{log.location})"
            )
    if unmatched:
        _console.print(f"  [red]✗[/red] {len(unmatched)} task(s) have no candidate logs")

    # In a matrix spec every log is "incompatible" with all sibling combos,
    # so per-target listings drown the report (27 targets x 26 logs). Only a
    # log that matched NOWHERE (no perfect/chosen/skipped/ambiguous slot on
    # any target) is worth surfacing, once.
    placed = {
        log.location
        for p in plans
        for log in ([p.perfect] if p.perfect else []) + p.chosen + p.skipped + p.ambiguous
    }
    orphans: dict[str, None] = {}  # insertion-ordered unique locations
    for p in plans:
        for log in p.incompatible:
            if log.location not in placed:
                orphans[log.location] = None
    for location in orphans:
        _console.print(
            f"  (conflicting args with every target, pass its path "
            f"explicitly to force: {location})"
        )
