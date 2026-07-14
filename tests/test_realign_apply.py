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


def _perturbations():
    from inspect_ai.log._log import EvalPlan, EvalPlanStep
    from inspect_ai.model import GenerateConfig
    from inspect_ai.model._model_config import ModelConfig

    def args(h):
        h.eval.task_args_passed = {**h.eval.task_args_passed, "extra": 1}
        h.eval.task_args = {**h.eval.task_args, "extra": 1}

    def version(h):
        h.eval.task_version = 99

    def task_file(h):
        h.eval.task_file = "somewhere/else.py"

    def message_limit(h):
        h.eval.config.message_limit = 5

    def token_limits(h):
        h.eval.config.token_limit = 1000
        h.eval.config.token_limit_type = "output"

    def turn_limit(h):
        h.eval.config.turn_limit = 3

    def time_limit(h):
        h.eval.config.time_limit = 60

    def working_limit(h):
        h.eval.config.working_limit = 30

    def cost_limit(h):
        h.eval.config.cost_limit = 1.5

    def model_args(h):
        h.eval.model_args = {"tensor_parallel_size": 4}

    def generate_config(h):
        h.eval.model_generate_config = GenerateConfig(temperature=0.7)

    def model_roles(h):
        h.eval.model_roles = {"grader": ModelConfig(model="mockllm/model")}

    def plan(h):
        h.plan = EvalPlan(steps=[EvalPlanStep(solver="some_other_solver")])

    return {
        "task_args_passed": args,
        "task_version": version,
        "task_file": task_file,
        "message_limit": message_limit,
        "token_limit+type": token_limits,
        "turn_limit": turn_limit,
        "time_limit": time_limit,
        "working_limit": working_limit,
        "cost_limit": cost_limit,
        "model_args": model_args,
        "model_generate_config": generate_config,
        "model_roles": model_roles,
        "plan": plan,
    }


@pytest.mark.parametrize("field", sorted(_perturbations()))
def test_apply_restores_match_for_every_identifier_field(targets, field):
    """For each identifier field: perturbing it breaks the match, the diff
    names it, and apply_target_fields restores the exact identifier."""
    target_id, resolved = next(iter(targets.items()))
    header = header_from_target(resolved)
    _perturbations()[field](header)

    assert task_identifier(header, None) != target_id, (
        f"perturbing {field} should change the identifier"
    )
    from flow_steps_demo.realign import diff_fields

    diff_names = {d.field for d in diff_fields(header, resolved)}
    expected = {"token_limit", "token_limit_type"} if field == "token_limit+type" else {field}
    assert expected <= diff_names

    changed = apply_target_fields(header, resolved, target_id)
    assert task_identifier(header, None) == target_id
    assert expected <= set(changed)
