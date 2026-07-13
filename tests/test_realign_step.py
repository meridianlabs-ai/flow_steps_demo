from inspect_ai._eval.evalset import task_identifier
from inspect_ai.log import read_eval_log, write_eval_log

from flow_steps_demo.constants import TAG_REALIGNED
from flow_steps_demo.realign import realign, resolve_spec_targets

from .test_realign_apply import header_from_target

SPEC = "src/flow_steps_demo/alignment_probe/spec.py"


def write_near_miss(tmp_path, targets, target_id) -> str:
    """Write a log matching target_id, then bump its version so it near-misses."""
    header = header_from_target(targets[target_id])
    header.eval.task_version = 99
    (tmp_path / "src").mkdir(exist_ok=True)
    path = str(tmp_path / "src" / "2026-07-01T00-00-00+00-00_probe_x1.eval")
    write_eval_log(header, path)
    return path


def test_explain_mode_writes_nothing(tmp_path, capsys):
    targets = resolve_spec_targets(SPEC, {"model": "mockllm/model"})
    target_id = next(iter(targets))
    log_path = write_near_miss(tmp_path, targets, target_id)

    result = realign([log_path], spec=SPEC, spec_args={"model": "mockllm/model"})
    assert result == []
    out = capsys.readouterr().out
    assert "task_version" in out
    assert not (tmp_path / "realigned").exists()


def test_realign_produces_verified_tagged_copy(tmp_path):
    targets = resolve_spec_targets(SPEC, {"model": "mockllm/model"})
    target_id = next(iter(targets))
    log_path = write_near_miss(tmp_path, targets, target_id)
    dest = str(tmp_path / "realigned")

    result = realign(
        [log_path],
        spec=SPEC,
        spec_args={"model": "mockllm/model"},
        dest=dest,
    )
    assert len(result) == 1
    copy = result[0]
    # filename: original stem kept (timestamp intact), suffix appended
    assert copy.location.endswith("_probe_x1+realigned.eval")
    # original untouched
    original = read_eval_log(log_path, header_only=True)
    assert original.eval.task_version == 99
    # copy on disk matches the target identifier
    reread = read_eval_log(copy.location, header_only=True)
    assert task_identifier(reread, None) == target_id
    # provenance
    assert TAG_REALIGNED in reread.tags
    assert reread.metadata["realigned_from"] == original.location
    assert "task_version" in reread.metadata["realigned_fields"]


def test_dest_must_differ_from_source_dir(tmp_path):
    targets = resolve_spec_targets(SPEC, {"model": "mockllm/model"})
    target_id = next(iter(targets))
    log_path = write_near_miss(tmp_path, targets, target_id)

    import pytest

    with pytest.raises(ValueError, match="dest"):
        realign(
            [log_path],
            spec=SPEC,
            spec_args={"model": "mockllm/model"},
            dest=str(tmp_path / "src"),
        )


def test_dry_run_with_dest_writes_nothing(tmp_path):
    targets = resolve_spec_targets(SPEC, {"model": "mockllm/model"})
    target_id = next(iter(targets))
    log_path = write_near_miss(tmp_path, targets, target_id)
    dest = str(tmp_path / "realigned")

    result = realign(
        [log_path],
        spec=SPEC,
        spec_args={"model": "mockllm/model"},
        dest=dest,
        dry_run=True,
    )
    assert result == []
    import os
    assert not os.path.exists(dest) or os.listdir(dest) == []
    # and a subsequent real run is NOT blocked by dry-run leftovers
    result = realign(
        [log_path],
        spec=SPEC,
        spec_args={"model": "mockllm/model"},
        dest=dest,
    )
    assert len(result) == 1
