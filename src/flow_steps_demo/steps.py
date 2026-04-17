from dataclasses import dataclass

from inspect_ai.log import EvalLog

from inspect_flow import step
from inspect_flow.api import copy, tag, metadata

from flow_steps_demo.constants import (
    STORE_PATH,
    LOG_DIR_DEV,
    LOG_DIR_PROD,
    SCAN_DIR,
    TAG_QA_AUTO_NEEDED,
    TAG_QA_AUTO_DONE,
    TAG_QA_MANUAL_NEEDED,
    TAG_QA_MANUAL_DONE,
    TAG_PROMOTED,
)
from flow_steps_demo.scanners import REFUSAL_CLASSIFIER, refusal_classifier

from inspect_scout import scan, scan_results_df, transcripts_from
from upath import UPath

from flow_steps_demo.filters import qa_done


@step
def qa_auto(
    logs: list[EvalLog],
    model: str | None = None,
    scan_model: str = "openai/gpt-4o",
) -> list[EvalLog]:
    """Scan logs for refusals and advance passing logs to manual review.

    Runs `refusal_classifier` via Scout, writes a markdown summary, and tags
    clean logs as `qa_auto_done` + `qa_manual_needed`.

    Args:
        model: Only process logs for this model. If None, process all.
        scan_model: Model used by the LLM scanner.
    """

    target = [
        log
        for log in logs
        if (not model or log.eval.model == model) and TAG_QA_AUTO_NEEDED in log.tags
    ]

    if not target:
        return []

    scan_dir = f"{SCAN_DIR}/{model}" if model else SCAN_DIR
    scanner = refusal_classifier()

    # Scan all target logs in a single batch call
    status = scan(
        scans=scan_dir,
        scanners=[scanner],
        transcripts=transcripts_from([log.location for log in target]),
        model=scan_model,
    )

    # Attribute scan results back to individual logs
    statuses = scan_status_per_log(status.location, target)

    # Write shared markdown report
    summary_path = UPath(scan_dir) / "qa_summary.md"
    existing = summary_path.read_text() if summary_path.exists() else "# QA Summary\n\n"

    results: list[EvalLog] = []
    for log in target:
        s = statuses[log.location]

        # Add scan details and location to metadata
        [log] = metadata(
            [log],
            set={
                "scans": status.location,
                "scan_complete": status.complete,
                "scan_errors": s.has_errors,
                "scan_has_refusal": s.has_refusal,
            },
        )

        # If scan doesn't have errors tag with `TAG_QA_AUTO_DONE` and mark for manual review
        if not s.has_errors:
            [log] = tag(
                [log],
                add=[TAG_QA_AUTO_DONE, TAG_QA_MANUAL_NEEDED],
                remove=[TAG_QA_AUTO_NEEDED],
            )

        existing += _qa_summary_section(log, s, status.location)
        results.append(log)

    summary_path.write_text(existing)

    return results


@step
def manual_review_done(logs: list[EvalLog]) -> list[EvalLog]:
    """Mark logs as manually reviewed. Run after inspecting logs in the Viewer."""
    return tag(
        logs,
        add=[TAG_QA_MANUAL_DONE],
        remove=[TAG_QA_MANUAL_NEEDED],
    )


@step
def promote(logs: list[EvalLog]) -> list[EvalLog]:
    """Promote fully-reviewed logs to production. Filters to `qa_done`, tags as promoted, and copies to the prod log dir."""
    logs = [log for log in logs if qa_done(log)]
    if not logs:
        return []

    logs = tag(
        logs,
        add=[TAG_PROMOTED],
        reason="Manually inspected by user",  # User defaults to git.user
    )
    return copy(
        logs,
        dest=LOG_DIR_PROD,
        source_prefix=f"{LOG_DIR_DEV}/",
        store=STORE_PATH,
    )


@dataclass
class LogScanStatus:
    refusal_count: int
    scan_count: int
    has_refusal: bool
    has_errors: bool


def scan_status_per_log(
    scan_location: str,
    logs: list[EvalLog],
) -> dict[str, LogScanStatus]:
    """Attribute refusal_classifier scan results back to individual logs.

    Reads the scan results DataFrame, normalizes each log's location URI,
    and returns a dict keyed by log location with per-log scan status.
    """
    scan_df = scan_results_df(scan_location, scanner=REFUSAL_CLASSIFIER)
    df = scan_df.scanners[REFUSAL_CLASSIFIER]

    result: dict[str, LogScanStatus] = {}
    for log in logs:
        # Normalize: strip file:// scheme for local paths, keep s3:// etc as-is.
        loc = UPath(log.location)
        log_uri = loc.resolve().path if loc.protocol in ("", "file") else str(loc)
        log_rows = df[df["transcript_source_uri"] == log_uri]

        # llm_scanner returns letter codes: "A" = first answer (NO_REFUSAL)
        refusal_count = (
            int((log_rows["value"] != "A").sum()) if not log_rows.empty else 0
        )
        scan_count = len(log_rows)
        has_errors = (
            log_rows["scan_error"].notna().any()
            if "scan_error" in log_rows.columns and not log_rows.empty
            else False
        )

        result[log.location] = LogScanStatus(
            refusal_count=refusal_count,
            scan_count=scan_count,
            has_refusal=refusal_count > 0,
            has_errors=bool(has_errors),
        )

    return result


def _qa_summary_section(
    log: EvalLog, status: LogScanStatus, scan_location: str
) -> str:
    """Render a markdown section for one log's QA result."""
    args_lines = (
        "\n".join(f"  - **{k}:** {v}" for k, v in log.eval.task_args.items())
        or "  - (none)"
    )
    return f"""## {log.eval.model} — {log.eval.task}
- **Log:** `{log.location}`
- **Task args:**
{args_lines}
- **Refusals:** {status.refusal_count}/{status.scan_count} (`{REFUSAL_CLASSIFIER}`)
- **Scan:** `{scan_location}`
- **Scan errors:** {int(status.has_errors)}
- **Result:** {"REFUSAL DETECTED" if status.has_refusal else "PASS"}

"""
