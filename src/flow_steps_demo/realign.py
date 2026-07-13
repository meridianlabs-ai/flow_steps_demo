"""Realign eval logs to a changed task so Flow matches them again.

Relies on private inspect_flow / inspect_ai APIs, validated against
inspect-flow 0.10.0 / inspect-ai 0.3.246. If an upgrade breaks an import
here, re-check these modules first.
"""

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
from inspect_ai._util.file import absolute_file_path
from inspect_ai.log import EvalLog
from inspect_ai.model import GenerateConfig, get_model
from inspect_flow._config.load import ConfigOptions, int_load_spec
from inspect_flow._runner.instantiate import instantiate_tasks
from inspect_flow._runner.resolve import resolve_spec
from inspect_flow._store.store import is_better_log
from inspect_flow._types.flow_types import FlowOptions
from inspect_flow._util.not_given import default_none
from pydantic_core import to_json

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


def log_fields(header: EvalLog) -> dict[str, Any]:
    """Identifier-relevant fields as stored in a log header."""
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

    Mirrors the ResolvedTask branch of inspect_ai's task_identifier with the
    same EvalSetArgs Flow passes (empty GenerateConfig, no solver, no limits).
    """
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
    """Serialize a field value the way task_identifier hashes it."""
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

    Follows the same resolution path flow run/check uses
    (inspect_flow._runner.logs.get_task_ids_to_tasks), but keeps the
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
