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
from flow_steps_demo.scanners import refusal_classifier

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

    def model_filter(log: EvalLog, name=None):
        """Helper to filter logs by model name."""
        if name:
            return log.eval.model == name
        return True

    target = [
        log
        for log in logs
        if model_filter(log, name=model) and TAG_QA_AUTO_NEEDED in log.tags
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

    # Read per-transcript results so we can attribute to individual logs
    scan_df = scan_results_df(status.location, scanner="refusal_classifier")
    df = scan_df.scanners["refusal_classifier"]

    # Write shared markdown report
    summary_path = UPath(scan_dir) / "qa_summary.md"
    existing = summary_path.read_text() if summary_path.exists() else "# QA Summary\n\n"

    results: list[EvalLog] = []
    for log in target:
        # Filter scan results to this log's transcripts.
        # Normalize: strip file:// scheme for local paths, keep s3:// etc as-is.
        loc = UPath(log.location)
        log_uri = loc.resolve().path if loc.protocol in ("", "file") else str(loc)
        log_rows = df[df["transcript_source_uri"] == log_uri]
        # llm_scanner returns letter codes: "A" = first answer (NO_REFUSAL)
        refusal_count = (
            int((log_rows["value"] != "A").sum()) if not log_rows.empty else 0
        )
        scan_count = len(log_rows)
        has_refusal = refusal_count > 0
        has_errors = (
            log_rows["scan_error"].notna().any()
            if "scan_error" in log_rows.columns and not log_rows.empty
            else False
        )

        # Add scan details and location to metadata
        [log] = metadata(
            [log],
            set={
                "scans": status.location,
                "scan_complete": status.complete,
                "scan_errors": bool(has_errors),
                "scan_has_refusal": has_refusal,
            },
        )

        # Append summary to shared markdown report
        args_lines = (
            "\n".join(f"  - **{k}:** {v}" for k, v in log.eval.task_args.items())
            or "  - (none)"
        )
        section = f"""## {log.eval.model} — {log.eval.task}
- **Log:** `{log.location}`
- **Task args:**
{args_lines}
- **Refusals:** {refusal_count}/{scan_count} (`refusal_classifier`)
- **Scan:** `{status.location}`
- **Scan errors:** {int(has_errors)}
- **Result:** {"REFUSAL DETECTED" if has_refusal else "PASS"}

"""
        existing += section

        # If scan doesn't have errors tag with `TAG_QA_AUTO_DONE` and mark for manual review
        if not has_errors:
            [log] = tag(
                [log],
                add=[TAG_QA_AUTO_DONE, TAG_QA_MANUAL_NEEDED],
                remove=[TAG_QA_AUTO_NEEDED],
            )

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
