# Realigning logs after a task arg change

When you add a new arg to a `@task` and pass it in the spec, the **task identifier changes**, so Flow no longer recognises logs from earlier runs and wants to re-run everything, even though the existing results still hold. The `align_task_args` step backfills the new arg into existing log headers so their recomputed identifier matches the updated task, and the old logs get reused.

## Why the identifier changes

Flow recomputes each log's identifier from its header on read. Part of it is a hash of `task_args_passed`:

```
{task}#{sha256(task_args_passed)}/{model}/{...}
```

Adding an arg (and passing it in the spec) changes that hash, so old logs (which lack the arg) no longer match.

## Usage

Add the arg to the task and the spec first, then realign the old logs.

**Against the store** (patches the store's logs in place and re-imports them):

```bash
flow step align_task_args --store @STORE_PATH --args cohort=pilot \
    --task flow_steps_demo/alignment_probe
```

**Against a log dir or a single log file** (`PATH` and `--store` are mutually exclusive):

```bash
flow step align_task_args @LOG_DIR_DEV --args cohort=pilot
```

A `PATH` run only rewrites the log file(s); **the store is not updated**. Re-import the patched paths afterwards so re-runs match from the store too:

```bash
flow store import @LOG_DIR_DEV --store @STORE_PATH -r
```

Verify the result with `flow check`:

```bash
flow check src/flow_steps_demo/alignment_probe/spec.py --arg model=openai/gpt-4o --log-dir @LOG_DIR_DEV
```
