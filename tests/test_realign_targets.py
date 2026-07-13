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
        "cohort",
    }
