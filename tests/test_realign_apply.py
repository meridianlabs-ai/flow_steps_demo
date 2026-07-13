import pytest
from inspect_ai._eval.evalset import task_identifier
from inspect_ai.log import EvalLog
from inspect_ai.log._log import EvalConfig, EvalDataset, EvalSpec, EvalStats

from flow_steps_demo.realign import (
    apply_target_fields,
    resolve_spec_targets,
    target_fields,
)

SPEC = "src/flow_steps_demo/alignment_probe/spec.py"


def header_from_target(resolved) -> EvalLog:
    tf = target_fields(resolved)
    log = EvalLog(
        eval=EvalSpec(
            created="2026-07-01T00:00:00+00:00",
            task=resolved.task.name,
            task_file=tf["task_file"],
            task_version=tf["task_version"],
            task_args=dict(tf["task_args_passed"]),
            task_args_passed=dict(tf["task_args_passed"]),
            dataset=EvalDataset(),
            model=str(resolved.model),
            model_args=tf["model_args"],
            model_generate_config=tf["model_generate_config"],
            model_roles=tf["model_roles"] or None,
            config=EvalConfig(
                message_limit=tf["message_limit"],
                token_limit=tf["token_limit"],
                token_limit_type=tf["token_limit_type"],
                turn_limit=tf["turn_limit"],
                time_limit=tf["time_limit"],
                working_limit=tf["working_limit"],
                cost_limit=tf["cost_limit"],
            ),
        ),
        plan=tf["plan"],
        stats=EvalStats(completed_at="2026-07-01T00:00:00+00:00"),
    )
    log.location = "logs/synthetic.eval"
    return log


@pytest.fixture(scope="module")
def targets():
    return resolve_spec_targets(SPEC, {"model": "mockllm/model"})


def test_header_from_target_roundtrip(targets):
    for target_id, resolved in list(targets.items())[:3]:
        header = header_from_target(resolved)
        assert task_identifier(header, None) == target_id


def test_apply_realigns_across_arg_combos(targets):
    ids = list(targets)
    source, dest = ids[0], ids[1]  # two different matrix combos
    header = header_from_target(targets[source])
    changed = apply_target_fields(header, targets[dest], dest)
    assert task_identifier(header, None) == dest
    assert "task_args_passed" in changed


def test_apply_handles_removed_arg(targets):
    target_id, resolved = next(iter(targets.items()))
    header = header_from_target(resolved)
    # simulate an old log that passed an extra arg the task no longer takes
    header.eval.task_args_passed = {**header.eval.task_args_passed, "old_arg": 1}
    header.eval.task_args = {**header.eval.task_args, "old_arg": 1}
    changed = apply_target_fields(header, resolved, target_id)
    assert task_identifier(header, None) == target_id
    assert "old_arg" not in header.eval.task_args_passed
    assert "old_arg" not in header.eval.task_args
    assert changed == ["task_args_passed"]


def test_apply_raises_on_wrong_target_id(targets):
    target_id, resolved = next(iter(targets.items()))
    header = header_from_target(resolved)
    with pytest.raises(RuntimeError, match="verification"):
        apply_target_fields(header, resolved, "not-the-right-id")
