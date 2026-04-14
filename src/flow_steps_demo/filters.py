from inspect_ai.log import EvalLog
from inspect_flow import log_filter

from flow_steps_demo.constants import (
    TAG_QA_AUTO_NEEDED,
    TAG_QA_AUTO_DONE,
    TAG_QA_MANUAL_NEEDED,
    TAG_QA_MANUAL_DONE,
)


@log_filter
def qa_done(log: EvalLog):
    """True when a log has passed both automated and manual QA."""
    return (
        TAG_QA_AUTO_DONE in log.tags
        and TAG_QA_AUTO_NEEDED not in log.tags
        and TAG_QA_MANUAL_DONE in log.tags
        and TAG_QA_MANUAL_NEEDED not in log.tags
    )


@log_filter
def scan_has_refusal(log: EvalLog):
    """True when the refusal scanner flagged at least one refusal."""
    return log.metadata.get("scan_has_refusal", False)
