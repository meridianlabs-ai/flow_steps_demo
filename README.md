# Flow Steps Demo

A walkthrough of [inspect_flow](https://github.com/meridianlabs-ai/inspect_flow) step-based workflows: running evaluations, scanning for refusals, reviewing results, and promoting logs to production — using `flow run`, `flow step`, `flow check`, and `flow list log`.

## What's in this demo?

This project implements a multi-stage QA pipeline for alignment evaluation. It demonstrates how to use Flow to:

- **Define a FlowSpec** that generates a parametric sweep of tasks across models, training protocols, and misalignment types
- **Run evaluations** with `flow run` and track completeness with `flow check`
- **Automate QA** with a custom step (`qa_auto`) that uses Scout's LLM-based scanner to detect refusals
- **Inspect results** with `flow list log` and filters to find logs matching specific criteria
- **Manage the review lifecycle** — tag logs through automated QA, manual review, and promotion to production

The scenario: we run a synthetic alignment probe against multiple models, automatically scan responses for refusals, manually review the results, and promote passing logs to a production directory.

## Prerequisites

1. **Clone and install**

   ```bash
   git clone https://github.com/meridianlabs-ai/flow_steps_demo.git
   cd flow_steps_demo
   uv sync
   ```

2. **Set model API keys** — you need at least one provider:

   ```bash
   export OPENAI_API_KEY=...
   export ANTHROPIC_API_KEY=...
   export XAI_API_KEY=...
   ```

3. **Configure a storage bucket** — Flow uses this for logs, scans, and the store. Set `FLOW_DEMO_BUCKET` to a local path or an S3 bucket:

   ```bash
   # Local:
   export FLOW_DEMO_BUCKET=./output

   # Or S3:
   export FLOW_DEMO_BUCKET=s3://your-bucket/flow-demo
   ```

   If using S3, ensure your AWS credentials are configured (`aws configure` or environment variables).

## Project structure

```
_flow.py                         # Registers steps and filters for Flow discovery
src/flow_steps_demo/
├── _flow.py                     # Default FlowSpec + inspect_ai task entry point
├── constants.py                 # Bucket paths, tags, models, parameter lists
├── scanners.py                  # Scout scanners (refusal_keywords, refusal_classifier)
├── filters.py                   # Log filters (qa_done, scan_has_refusal)
├── steps.py                     # Flow steps (qa_auto, manual_review_done, promote)
└── alignment_probe/
    ├── task.py                  # @task alignment_probe — synthetic eval task
    └── spec.py                  # FlowSpec factory — parametric sweep across models
```

## Walkthrough

Throughout this walkthrough we'll use a single model to keep things quick. The full spec covers 6 models x 3 protocols x 3 misalignment types x 3 themes = 162 tasks, but filtering to one model gives us 27.

> **Tip — `@CONST` shorthand.** `flow` resolves `@NAME` tokens in CLI args to module-level string constants found in any `_flow.py` walking up from your current directory. This demo exposes its constants in [_flow.py](_flow.py) — so `@LOG_DIR_DEV` becomes `$FLOW_DEMO_BUCKET/dev/logs`, `@TAG_QA_MANUAL_NEEDED` becomes `qa_manual_needed`, etc. Use `@file.py@NAME` to resolve from a specific file. Note: this is a `flow` CLI feature only — `inspect view` still wants the literal path.

### 1. Check completeness — nothing done yet

```bash
# Check against the dev log dir
flow check src/flow_steps_demo/alignment_probe/spec.py \
    --arg model=openai/gpt-4o \
    --log-dir @LOG_DIR_DEV

# Check against the prod log dir
flow check src/flow_steps_demo/alignment_probe/spec.py \
    --arg model=openai/gpt-4o \
    --log-dir @LOG_DIR_PROD
```

`flow check` compares the spec against existing logs in a given directory. On a fresh start both are empty — 0/27 tasks completed in dev and prod.

### 2. Run evaluations

```bash
flow run src/flow_steps_demo/alignment_probe/spec.py --arg model=openai/gpt-4o
```

Flow runs all 27 tasks against GPT-4o. Each task sends a single-sample alignment probe, scored by exact match and the `refusal_keywords` grep scanner. Logs are written to the dev log directory and indexed in the store. All logs are tagged `qa_auto_needed` (set in `src/flow_steps_demo/_flow.py`).

### 3. List the logs

```bash
flow list log @LOG_DIR_DEV
```

Shows all eval logs in the dev directory with their status, model, task args, and tags.

You can also follow logs live while `flow run` is still writing them — the display refreshes every N seconds:

```bash
flow list log @LOG_DIR_DEV --live 5
```

### 4. Run automated QA

```bash
flow step qa_auto @LOG_DIR_DEV
```

The `qa_auto` step scans every log tagged `qa_auto_needed` using Scout's `refusal_classifier` (an LLM-based scanner). For each log it:

- Records scan results (refusal detected or not) in log metadata
- Appends a section to a shared `qa_summary.md` report
- If no errors: tags the log `qa_auto_done` + `qa_manual_needed`

Every tag and metadata edit is recorded with provenance — by default the author is your git user (from `git config user.name` and `user.email`), along with a timestamp. You can see this in `log.log_updates` or in the Viewer (JSON tab).

### 5. Review logs in the Viewer

Open the Inspect Viewer to manually inspect the logs:

```bash
inspect view $FLOW_DEMO_BUCKET/dev/logs
```

### 6. Filter logs with refusals

Use the `scan_has_refusal` filter to find logs where the scanner detected a refusal:

```bash
flow list log @LOG_DIR_DEV --filter scan_has_refusal
```

Or find logs that passed automated QA and are waiting for manual review:

```bash
flow list log @LOG_DIR_DEV --tag @TAG_QA_MANUAL_NEEDED
```

### 7. Mark manual review as done

After reviewing in the Viewer, advance the logs past manual review:

```bash
flow step manual_review_done @LOG_DIR_DEV
```

This adds the `qa_manual_done` tag and removes `qa_manual_needed`.

### 8. Promote to production

```bash
flow step promote @LOG_DIR_DEV
```

The `promote` step filters to logs where both automated and manual QA are complete (`qa_done` filter), tags them as `promoted`, and copies them to the production log directory.

### 9. Check completeness again

```bash
flow check src/flow_steps_demo/alignment_probe/spec.py \
    --arg model=openai/gpt-4o \
    --log-dir @LOG_DIR_PROD
```

Promoted logs now live in the prod directory — the check should show 27/27 tasks completed.

### 10. Repeat with a new model

Run the same workflow for another model:

```bash
flow run src/flow_steps_demo/alignment_probe/spec.py --arg model=anthropic/claude-haiku-4-5-20251001
```
