# Realigning logs after a task arg change

When you add a new arg to a `@task` and pass it in the spec, the **task identifier changes**, so Flow no longer recognises logs from earlier runs and wants to re-run everything, even though the existing results still hold. The `align_task_args` step makes a realigned **copy** of each old log (with the new arg injected) whose recomputed identifier matches the updated task, so the old results get reused.

## Why the identifier changes

Flow recomputes each log's identifier from its header on read. Part of it is a hash of `task_args_passed`:

```
{task}#{sha256(task_args_passed)}/{model}/{...}
```

Adding an arg (and passing it in the spec) changes that hash, so old logs (which lack the arg) no longer match.

## What the step does

For each matching log it copies the file to `dest`, injects `args` into the copy's `task_args`/`task_args_passed` (rebuilt in the task's signature order, since the hash is order-sensitive), tags it `realigned`, and records `realigned_from` / `realigned_args` in metadata. The **original log is left untouched** under its old identifier, so the operation is non-destructive and the store ends up with two correct entries (old id for the original, new id for the copy).

It only injects args that the updated task actually declares, so it is safe to point at a whole store: logs whose task is unknown or doesn't have the new arg are skipped. Only added or changed args are supported; **removing an arg is not**.

## Usage

Add the arg to the task and the spec first, then realign the old logs.

**Against the store** (copies the originals to `dest` and imports the copies):

```bash
flow step align_task_args --store @STORE_PATH --dest @LOG_DIR_DEV/realigned \
    --args cohort=pilot --task flow_steps_demo/alignment_probe
```

**Against a log dir or a single log file** (`PATH` and `--store` are mutually exclusive, so the store is not updated automatically — import the copies afterwards):

```bash
flow step align_task_args @LOG_DIR_DEV --dest @LOG_DIR_DEV/realigned --args cohort=pilot
flow store import @LOG_DIR_DEV/realigned --store @STORE_PATH -r
```

`dest` must differ from the source so the originals are preserved.

Verify the result with `flow check` against the copies:

```bash
flow check src/flow_steps_demo/alignment_probe/spec.py --arg model=openai/gpt-4o --log-dir @LOG_DIR_DEV/realigned
```
