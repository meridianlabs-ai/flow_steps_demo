import inspect
from dataclasses import dataclass
from typing import Any, Callable, cast

from inspect_ai.log import EvalLog
from inspect_ai._util.registry import registry_lookup

from inspect_flow import step
from inspect_flow.api import copy, scan, tag, metadata

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
    TAG_REALIGNED,
)
from flow_steps_demo.scanners import REFUSAL_CLASSIFIER, refusal_classifier

from inspect_scout import scan_results_df
from upath import UPath

from flow_steps_demo.filters import qa_done


def _ordered(args: dict[str, Any], signature_order: list[str]) -> dict[str, Any]:
    """Rebuild `args` with keys in task-signature order."""
    ordered = {k: args[k] for k in signature_order if k in args}
    ordered.update({k: v for k, v in args.items() if k not in ordered})
    return ordered


def _signature_order(task: str) -> list[str] | None:
    """Parameter order of a registered task, or None if it isn't importable here."""
    fn = cast(Callable[..., Any] | None, registry_lookup("task", task))
    if fn is None:
        return None
    return list(inspect.signature(fn).parameters)


@step
def align_task_args(
    logs: list[EvalLog],
    args: dict[str, Any],
    dest: str,
    task: str | None = None,
    source_prefix: str | None = None,
) -> list[EvalLog]:
    """Copy logs and realign their task args so an updated task recognises them.

    Adding an arg to a task (and passing it in the spec) changes the task
    identifier, so Flow stops matching older logs. This step copies each log to
    `dest`, injects `args` into the copy's `task_args`/`task_args_passed` in the
    task's signature order, and tags it `realigned`. The recomputed identifier of
    the copy matches the updated task, while the original log is left untouched
    under its old identifier (non-destructive). When run with `--store`, the
    copies are imported so re-runs match from the store too.

    Only handles added or changed args. Removing an arg is not supported.

    Run against the store (copies originals to `dest` and imports the copies):
        flow step align_task_args --store @STORE_PATH --dest @LOG_DIR_DEV/realigned \\
            --args cohort=pilot --task flow_steps_demo/alignment_probe

    Run against a log dir or file (store NOT updated; import the copies after):
        flow step align_task_args path/to/logs --dest path/to/realigned --args cohort=pilot
        flow store import path/to/realigned --store @STORE_PATH -r

    Args:
        args: Task args to inject, keyed by arg name (e.g. {"cohort": "pilot"}).
        dest: Directory to write the realigned copies to (must differ from the
            source so originals are preserved).
        task: If set, only process logs whose `eval.task` matches this registry name.
        source_prefix: Directory prefix to strip from source paths, preserving
            structure under `dest` (as in the `copy` step). Defaults to a flat copy.
    """
    if not args:
        raise ValueError("`args` must contain at least one task arg to inject.")

    # Select target logs first (task filter + signature guard) so we never copy
    # logs we won't realign.
    order_cache: dict[str, list[str] | None] = {}
    targets: list[tuple[EvalLog, list[str]]] = []
    for log in logs:
        log_task = log.eval.task
        if task is not None and log_task != task:
            continue
        order = order_cache.setdefault(log_task, _signature_order(log_task))
        if order is None or not all(k in order for k in args):
            continue
        targets.append((log, order))

    if not targets:
        return []

    # Copy the originals, then realign args on the copies (non-destructive).
    copied = copy([log for log, _ in targets], dest=dest, source_prefix=source_prefix)

    results: list[EvalLog] = []
    for (original, order), c in zip(targets, copied):
        c.eval.task_args = _ordered({**c.eval.task_args, **args}, order)
        c.eval.task_args_passed = _ordered({**c.eval.task_args_passed, **args}, order)
        # Provenance: record where the copy came from and what was injected.
        [c] = metadata(
            [c],
            set={"realigned_from": original.location, "realigned_args": args},
        )
        results.append(c)

    return tag(results, add=[TAG_REALIGNED])


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

    # Scan all target logs in a single batch call
    status = scan(
        target,
        scanners=[refusal_classifier()],
        scans=scan_dir,
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
