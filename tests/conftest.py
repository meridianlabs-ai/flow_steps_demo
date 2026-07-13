from typing import Any

import pytest
from inspect_ai.log import EvalLog
from inspect_ai.log._log import EvalConfig, EvalDataset, EvalPlan, EvalSpec, EvalStats


@pytest.fixture
def make_header():
    """Build a minimal synthetic header-only EvalLog."""

    def _make(
        task: str = "flow_steps_demo/alignment_probe",
        model: str = "mockllm/model",
        task_args: dict[str, Any] | None = None,
        task_version: int | str = 0,
        completed_at: str = "2026-07-01T00:00:00+00:00",
        message_limit: int | None = None,
        location: str = "logs/2026-07-01T00-00-00+00-00_probe_abc123.eval",
    ) -> EvalLog:
        args = task_args or {}
        log = EvalLog(
            eval=EvalSpec(
                created="2026-07-01T00:00:00+00:00",
                task=task,
                task_version=task_version,
                task_args=dict(args),
                task_args_passed=dict(args),
                dataset=EvalDataset(),
                model=model,
                config=EvalConfig(message_limit=message_limit),
            ),
            plan=EvalPlan(),
            stats=EvalStats(completed_at=completed_at),
        )
        log.location = location
        return log

    return _make
