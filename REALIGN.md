# Realigning logs after a task change

## What, and why

Flow decides whether an existing log satisfies a spec task by comparing
**task identifiers**, not file contents. The identifier is built from:

```
{task_file}@{task}#{sha256(task_args_passed)}/{model}/{hash(plan, generate_config, model_args, version, limits)}
```

Every field that feeds that string — `task_args_passed`, `task_version`,
the resolved `plan` (solver/generate steps), the generate config, model
args, and the various limits (`message_limit`, `token_limit`,
`token_limit_type`, `turn_limit`, `time_limit`, `working_limit`,
`cost_limit`) — is baked into the hash. `model` itself sits outside the
hash but is still part of the identifier: it's the pairing key realign
uses to find candidates, never a field it rewrites.

Change any one of those inputs — add a task parameter, bump
`task_version`, tweak a limit, adjust the solver plan — and every log
produced under the old identifier stops matching. `flow run` and
`flow check` see the task as "not yet done" and will happily re-run
work whose *results* are still perfectly valid; the sample outputs
didn't change, only the bookkeeping around them did.

`realign` closes that gap without re-running anything. For each spec
task that has no exact match, it finds the closest surviving log (same
task, same model, task args agreeing on every key they still share),
makes a **new copy** with the identifier-relevant header fields
rewritten to match the current spec, and verifies the rewritten
identifier is byte-for-byte correct before keeping it. The original log
is never touched. The copy carries full provenance of what was changed
and from where (see [Provenance](#provenance) below), so nothing about
the rewrite is silent or unauditable.

## Seeing matches (before you realign anything)

You don't need `realign` to *see* whether logs still match — Flow's
existing tooling already tells you that:

- **`flow run spec.py --dry-run`** (add `--json` for machine-readable
  output) — the fastest sanity check. It resolves every task in the
  spec, looks for a matching log, and reports how many tasks would
  actually run vs. how many are already satisfied. `--json` gives you
  per-task detail, including duplicate logs found for the same task.
- **`flow check spec.py`** — a standalone completeness report against
  a log directory (or the store), independent of actually running
  anything.
- **`flow list log --task 'pattern' --model 'pattern'`** — browse the
  logs themselves: status, tags, task args, timestamps. Useful for
  spotting logs that look like they *should* match but don't.
- **`flow store list --filter ...`** — same idea, scoped to the store
  rather than a log directory.

Reach for `realign` once one of these tells you logs exist but Flow
doesn't recognize them — that's the signal a task change orphaned
otherwise-good logs.

## The workflow

1. **Confirm the gap.** `flow run spec.py --dry-run` — tasks that used
   to be complete now show up as "to run."
2. **Explain, don't write yet.** `flow step realign PATH --spec spec.py
   --spec-args ...` with no `--dest`. This is explain mode: it prints,
   for every spec task without a perfect match, which near-miss log it
   would use and a field-by-field diff of what would change
   (`+ field=value (added)`, `- field=value (removed)`,
   `~ field: old -> new`). Nothing is written to disk in this mode.
3. **Realign.** Re-run the same command with `--dest DIR` (or `--store
   ...` — see the note below). This copies each chosen log to `DIR`
   with a `+realigned` suffix, rewrites its header, and verifies the
   new identifier matches the spec target exactly. If verification
   fails, that copy is not silently kept — the step raises.
4. **Confirm reuse.** `flow check spec.py` or `flow run spec.py
   --dry-run` again, now pointed at (or reading from) a log dir/store
   that includes the realigned copies. Tasks that were "to run" should
   now show as complete, and a `--dry-run` should report 0 tasks left
   to run. Watch out for `log_dir_create_unique`: if the spec has it
   enabled (as this demo's does, by default), a plain `flow run
   --dry-run` previews a fresh, not-yet-existing timestamped
   subdirectory rather than checking the directory your previous runs
   actually live in — pass `--no-log-dir-create-unique` (or use `flow
   check`, which always scans the exact directory you point it at) to
   get an accurate completeness read.

`--dest` and explain mode are mutually exclusive by construction: pass
`--dest` to write copies, omit it to only see what *would* happen.
`--dry-run` on top of `--dest` (i.e. `flow step realign ... --dest DIR
--dry-run`) is a third, distinct mode — it exercises the same
copy-selection logic as a real run but only prints `would copy SRC ->
DST` lines instead of writing anything, which is a useful way to
preview exactly which destination paths would be created before
committing to a `--dest` run.

## Choosing among multiple matches, and ingesting judgment

Sometimes more than one surviving log is compatible with the same spec
target — same task, same model, no conflicting shared args — and Flow
has no principled way to know which one the human considers authoritative.
`realign`'s default tie-break is Flow's own store selection rule: prefer
the log with more valid samples, then the more recent one. That default
is a reasonable guess, not a judgment call, so three mechanisms let a
human's decision override or bypass it:

1. **Pass the log's path explicitly.** Instead of pointing `realign` at
   a directory, pass the exact log file as the `PATH` argument (or
   narrow with `--task`/`--model` globs). Only that log is considered,
   so there's no ambiguity to resolve.
2. **Tag the invalid log and exclude it.** Tag the log you don't want
   considered, then pass `--exclude that-tag-filter` to `realign` (or
   bake the exclusion into the spec's `FlowStoreConfig.filter` so it's
   excluded everywhere the spec is used, not just during realignment).
3. **Remove it from the store.** `flow store remove <path>` drops a log
   from consideration entirely if it's determined to be unusable.

Related but distinct from the tie-break case: when a log is compatible
with **more than one spec target at once** (e.g. it could plausibly
belong to two different matrix combinations because it's missing an arg
both targets added), `realign` refuses to guess. It reports the log
under every target it matches as `ambiguous` — "matches multiple
targets — narrow the spec with `--spec-args` or realign it against a
single-target spec" — and never auto-realigns it against any of them.
Resolving an ambiguous log means narrowing the spec (via `--spec-args`,
`--task`, `--model`, or a single-target spec) until only one candidate
target remains, then re-running `realign` — at which point it's an
ordinary near-miss, not an ambiguous one.

## Provenance

Nothing about a realigned copy pretends to be the original. Every copy
carries:

- **Filename**: the original name with a `+realigned` suffix inserted
  before the extension (e.g.
  `2025-01-01T00-00-00+00-00_task_abc123+realigned.eval`), so the
  timestamp in the name is preserved and the copy sorts next to its
  source.
- **Tag**: `realigned`, with a `reason` string listing exactly which
  identifier fields were rewritten (e.g. `"realigned to match spec:
  task_args_passed"`).
- **Metadata**:
  - `realigned_from` — the source log's location.
  - `realigned_from_identifier` — the source log's own task identifier,
    as it was before rewriting (i.e. what it used to match).
  - `realigned_fields` — the list of field names that were changed.

All of this is visible via `flow list log --provenance`, in the Viewer's
JSON tab, or by reading the log's metadata/tags directly — there's a
full audit trail from any realigned copy back to the exact source log
and the exact fields that changed, on top of Flow's standard
author/timestamp provenance for the tag and metadata edits themselves.

## Manual walkthrough

This uses `mockllm/model` throughout — no API keys required. Run these
commands manually from the repo root.

```bash
export FLOW_DEMO_BUCKET=./output-walkthrough

# 1. Run the sweep once (27 mockllm tasks, fast, no API keys).
uv run flow run src/flow_steps_demo/alignment_probe/spec.py --arg model=mockllm/model
# expect: 27 tasks run to completion.

# 2. Change the task: in src/flow_steps_demo/alignment_probe/task.py add a
#    `cohort: str = "pilot"` parameter to alignment_probe(), and in spec.py
#    add "cohort": "pilot" to each dict in the tasks_matrix `args` list.

# 3. Flow no longer recognises the old logs.
#    --no-log-dir-create-unique matters here: this spec's log_dir is
#    inherited as log_dir_create_unique=True (root _flow.py), so a plain
#    `flow run --dry-run` always previews a brand-new timestamped
#    subdirectory and checks completeness there instead of against the
#    directory holding the previous run's logs. Passing
#    --no-log-dir-create-unique pins the check to the actual log dir.
uv run flow run src/flow_steps_demo/alignment_probe/spec.py \
    --arg model=mockllm/model --dry-run --log-dir-allow-dirty \
    --no-log-dir-create-unique
# expect: 27 tasks to run; the old logs are on disk but don't count as matches.

# 4. Explain why (no --dest: nothing is written).
uv run flow step realign "$FLOW_DEMO_BUCKET/dev/logs/mockllm/model" \
    --spec src/flow_steps_demo/alignment_probe/spec.py \
    --spec-args model=mockllm/model
# expect: 27 realignable, each showing `+ cohort='pilot' (added)`.

# 5. Realign — write verified copies.
uv run flow step realign "$FLOW_DEMO_BUCKET/dev/logs/mockllm/model" \
    --spec src/flow_steps_demo/alignment_probe/spec.py \
    --spec-args model=mockllm/model \
    --dest "$FLOW_DEMO_BUCKET/dev/logs/mockllm/model/realigned"
# expect: 27 `+realigned` copies written under .../realigned/.

# 6. Confirm reuse (same --no-log-dir-create-unique reasoning as step 3;
#    `flow check` works too and doesn't need the flag, since it always
#    scans the exact directory you give it rather than previewing a new
#    run's log dir).
uv run flow run src/flow_steps_demo/alignment_probe/spec.py \
    --arg model=mockllm/model --dry-run --log-dir-allow-dirty \
    --no-log-dir-create-unique
# expect: 27 complete, 0 tasks to run.

# 7. Revert the task/spec edits and remove ./output-walkthrough when done.
#    This also removes the walkthrough's flow store: STORE_PATH derives from
#    FLOW_DEMO_BUCKET, so the store index lives at ./output-walkthrough/store
#    and every walkthrough run registered its logs there, not in your real
#    store.
git checkout -- src/flow_steps_demo/alignment_probe/
rm -rf ./output-walkthrough

# If any walkthrough command ran in a shell WITHOUT FLOW_DEMO_BUCKET set, its
# logs and store entries landed in the default ./output bucket instead. Check
# for strays and drop them from the real store:
#   uv run flow list log --task '*alignment_probe*' --model mockllm/model
#   uv run flow store remove <stray-log-dir> --store ./output/store
# (After deleting stray files by hand, `flow store remove --missing` clears
# the dangling index entries.)
```

**Note:** step 5 above uses a plain directory `PATH` for clarity, but the
production usage is `--store @STORE_PATH` in place of `PATH` — with a
store, matching logs are found across the whole store (not just one
directory) and the realigned copies are imported back into the store
automatically as part of the same step, rather than requiring a
separate import pass. To undo a realignment done this way, remove the
copies from the store index (`flow store remove <dest-dir> --store ...`)
before deleting the files — the originals were never touched, so nothing
else needs restoring.
