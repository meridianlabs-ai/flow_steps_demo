from pathlib import Path

from flow_steps_demo.realign import resolve_spec_targets, target_fields

SPEC = "src/flow_steps_demo/alignment_probe/spec.py"


def test_resolves_matrix_targets_with_unique_ids():
    targets = resolve_spec_targets(SPEC, {"model": "mockllm/model"})
    # 3 protocols x 3 misalignment types x 3 themes for one model
    assert len(targets) == 27
    for task_id, resolved in targets.items():
        assert "alignment_probe" in task_id
        assert "mockllm/model" in task_id
        assert str(resolved.model) == "mockllm/model"


def test_target_fields_shape():
    targets = resolve_spec_targets(SPEC, {"model": "mockllm/model"})
    resolved = next(iter(targets.values()))
    tf = target_fields(resolved)
    assert set(tf["task_args_passed"]) == {
        "training_protocol",
        "misalignment_type",
        "theme",
    }


def test_spec_defaults_are_applied(tmp_path):
    spec_file = tmp_path / "spec_with_defaults.py"
    spec_file.write_text(
        """
from inspect_flow import FlowDefaults, FlowSpec, FlowTask

def spec():
    return FlowSpec(
        log_dir="logs",
        defaults=FlowDefaults(task=FlowTask(args={"theme": "self_preservation"})),
        tasks=[
            FlowTask(
                name="flow_steps_demo/alignment_probe",
                model="mockllm/model",
            )
        ],
    )
"""
    )
    targets = resolve_spec_targets(str(spec_file))
    assert len(targets) == 1
    resolved = next(iter(targets.values()))
    # Default args should be applied to the task
    assert resolved.task_args["theme"] == "self_preservation"
