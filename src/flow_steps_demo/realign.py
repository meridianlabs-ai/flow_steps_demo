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
    """Pair each spec target with the near-miss logs that could be realigned to it."""
    log_ids: dict[str, str] = {}
    for log in logs:
        try:
            log_ids[log.location] = task_identifier(log, None)
        except Exception:
            continue  # malformed header: not a candidate

    plans: list[RealignPlan] = []
    for target_id, resolved in targets.items():
        plan = RealignPlan(target_id=target_id, resolved=resolved)
        target_args = resolved.task_args
        candidates: list[EvalLog] = []
        for log in logs:
            log_id = log_ids.get(log.location)
            if log_id is None:
                continue
            if log_id == target_id:
                plan.perfect = log
                break
            if task and not fnmatch(log.eval.task, task):
                continue
            if model and not fnmatch(str(log.eval.model), model):
                continue
            if log.eval.task != resolved.task.name or str(log.eval.model) != str(
                resolved.model
            ):
                continue
            if _args_compatible(log.eval.task_args_passed, target_args):
                candidates.append(log)
            else:
                plan.incompatible.append(log)

        if plan.perfect is None and candidates:
            # order best-first using flow's own store selection rule
            ordered: list[EvalLog] = []
            pool = list(candidates)
            while pool:
                best = None
                for c in pool:
                    if is_better_log(c, best):
                        best = c
                ordered.append(best)
                pool.remove(best)
            plan.chosen = ordered if realign_all else ordered[:1]
            plan.skipped = [] if realign_all else ordered[1:]
        plans.append(plan)
    return plans
