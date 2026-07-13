from flow_steps_demo.realign import plan_realignment, resolve_spec_targets

SPEC = "src/flow_steps_demo/alignment_probe/spec.py"
ARGS = {"training_protocol": "sft_baseline", "misalignment_type": "sycophancy"}


def targets_for_mockllm():
    return resolve_spec_targets(SPEC, {"model": "mockllm/model"})


def test_pairs_log_to_matching_matrix_combo(make_header):
    targets = targets_for_mockllm()
    # old log ran without task_version bump; shares all three matrix args
    # with exactly one target combo
    old = make_header(
        task_args={**ARGS, "theme": "baseline"},
        task_version=1,  # spec tasks default to version 0 -> near-miss
        location="logs/a.eval",
    )
    plans = plan_realignment(targets, [old])
    chosen_plans = [p for p in plans if p.chosen]
    assert len(chosen_plans) == 1
    resolved_args = chosen_plans[0].resolved.task_args
    assert resolved_args["theme"] == "baseline"
    assert resolved_args["training_protocol"] == "sft_baseline"


def test_conflicting_shared_args_are_incompatible(make_header):
    targets = targets_for_mockllm()
    old = make_header(
        task_args={**ARGS, "theme": "not_a_real_theme"},
        task_version=1,
        location="logs/b.eval",
    )
    plans = plan_realignment(targets, [old])
    assert all(not p.chosen for p in plans)
    assert any(p.incompatible for p in plans)


def test_wrong_model_is_not_a_candidate(make_header):
    targets = targets_for_mockllm()
    old = make_header(
        task_args={**ARGS, "theme": "baseline"},
        task_version=1,
        model="openai/gpt-4o",
        location="logs/c.eval",
    )
    plans = plan_realignment(targets, [old])
    assert all(not p.chosen and not p.incompatible for p in plans)


def test_best_candidate_chosen_and_rest_skipped(make_header):
    targets = targets_for_mockllm()
    older = make_header(
        task_args={**ARGS, "theme": "baseline"},
        task_version=1,
        completed_at="2026-06-01T00:00:00+00:00",
        location="logs/older.eval",
    )
    newer = make_header(
        task_args={**ARGS, "theme": "baseline"},
        task_version=1,
        completed_at="2026-07-01T00:00:00+00:00",
        location="logs/newer.eval",
    )
    plans = plan_realignment(targets, [older, newer])
    p = next(p for p in plans if p.chosen)
    assert [log.location for log in p.chosen] == ["logs/newer.eval"]
    assert [log.location for log in p.skipped] == ["logs/older.eval"]

    plans_all = plan_realignment(targets, [older, newer], realign_all=True)
    p_all = next(p for p in plans_all if p.chosen)
    assert len(p_all.chosen) == 2 and not p_all.skipped
