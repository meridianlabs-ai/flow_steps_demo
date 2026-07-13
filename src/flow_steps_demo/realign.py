"""Realign eval logs to a changed task so Flow matches them again.

Relies on private inspect_flow / inspect_ai APIs, validated against
inspect-flow 0.10.0 / inspect-ai 0.3.246. If an upgrade breaks an import
here, re-check these modules first.
"""

from dataclasses import dataclass
from typing import Any

from inspect_ai._eval.evalset import (
    _GENERATE_CONFIG_FIELDS_TO_EXCLUDE,
    model_args_for_log,
    model_roles_to_model_roles_config,
    plan_to_eval_plan,
    resolve_plan,
    resolve_solver,
    to_json_safe,
)
from inspect_ai._eval.task.resolved import ResolvedTask
from inspect_ai.log import EvalLog
from inspect_ai.model import GenerateConfig
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
