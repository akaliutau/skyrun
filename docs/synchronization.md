# File synchronization

Skyrun separates the source snapshot, per-run output directories, and optional
files that round-trip into the original project.

## Source uploads and `.modalignore`

Only `sync.project_dir` is scanned. Its `.modalignore` extends the
[packaged baseline](../src/skyrun/default.modalignore), using gitignore syntax
through `pathspec`. The baseline excludes hidden files, environments, caches,
weights, logs, private key extensions, and solution files. The policy is a
resource file rather than hardcoded filename sets.

```gitignore
# Project .modalignore
data/raw/
*.csv
!data/processed/summary.csv
```

Rules are relative to the project folder, including when configuration lives
elsewhere. Comments, anchored patterns, directory patterns, and negation work as
in gitignore. To reinclude a file beneath an ignored directory, first reinclude
the directory. `data/*` followed by `!data/keep.csv` works; `data/` prunes the
directory until it is explicitly reincluded.

Symlinks, `.git` directories, reserved `_runner`/`_sync` paths, and configured
output directories are structural exclusions. Ignore negation cannot enable
them. The selected project root and local download parents cannot be symlinks.
Ordinary non-hidden project files are inputs; keep private material outside the
project or list it in `.modalignore`. Use `skyrun --dry-run` to inspect the exact
paths and hashes before uploading.

Snapshots default to at most 1,024 source files and 64 MiB, with bounded chunked
copying and SHA-256 computed from the frozen bytes. Executable bits are preserved.
Files that change while being copied cause a retryable error. Requirements are
frozen separately for image construction. Missing source blobs upload to a
dedicated persistent Volume by content hash; repeated runs reuse identical
content, including files shared across snapshots. Reconstruction verifies every
blob before starting the workload.

## Output directories

```toml
[sync]
output_dirs = ["outputs", "checkpoints"]
local_dir = "runs"
```

Outputs start fresh on every run. The worker links these relative directories to
the run's artifact Volume directory, so code uses paths such as
`outputs/metrics.json`. Existing local contents are excluded from source uploads.
After execution, saved files download to `runs/<run-id>/`, including logs from
failed or interrupted commands. Empty directories are not transported.

Set `output_dirs = []` for a command that only needs logs. Output paths must be
distinct, non-overlapping relative directories and cannot use reserved names.

## Optional bidirectional files

```toml
[sync]
project_dir = "."
output_dirs = ["outputs", "checkpoints"]
bidirectional = ["settings.json", "data/processed"]
```

`bidirectional` selects literal file or directory paths, without globs. A selected
directory includes eligible descendants and new files created remotely. A path
may be absent locally if the workload will create it. Selections must not overlap
one another or `output_dirs`; they obey the same ignore rules and upload limits
as other source files. Opting in does not bypass `.modalignore`.

Hidden configuration files can be selected explicitly, but must also be
reincluded by the project ignore policy, for example `!.settings.json` in
`.modalignore` with `bidirectional = [".settings.json"]`. Git metadata remains
excluded regardless of ignore negation.

At launch, eligible selected files upload with the frozen source, and their
hashes/modes form the conflict baseline. After the process group stops, the worker
harvests new or modified eligible selected files into `_sync/files/` and writes
`_sync/manifest.json`. The client downloads those files into the run directory,
verifies their hashes, and applies them to the original project with atomic
per-file replacements. Unchanged content is not transferred back. Selected
changes are harvested after normal completion, nonzero exits, handled Ctrl-C,
and handled timeouts.

Use `--no-sync` to keep downloaded changes in the run directory for later review.
Recovery downloads artifacts without changing the working project unless you add
`--sync`.

## Conflicts and recovery

The default `error` policy compares each local file with its launch baseline. If
both local and remote versions changed, Skyrun leaves local files intact, retains
the remote copies in the run directory, writes `sync.json`, and exits with `3`.
Known conflicts are checked before applying any files. Each replacement rechecks
the local version; the overall operation is not a filesystem-wide transaction.
Pause local editing while applying recovered changes.

Resolve a conflict manually or choose an explicit policy:

```bash
# Download/retry and keep conflicting local files; apply nonconflicting changes.
skyrun download RUN_ID --sync --sync-conflict local

# Download/retry and replace conflicting regular local files with remote versions.
skyrun download RUN_ID --sync --sync-conflict remote
```

Already-applied versions are recognized, so recovery is idempotent. Deletions on
either side are never propagated automatically. Remote deletions are recorded as
`deleted_remote` in `sync.json`. Local deletions conflict with remote changes.
File/directory type conflicts require manual resolution. Remove or rename the
blocking path before retrying.

## Resumable checkpoints

For a workload that loads and saves `checkpoints/last.pt`, make checkpoints a
bidirectional selection rather than a fresh output directory:

```toml
[sync]
output_dirs = ["outputs"]
bidirectional = ["checkpoints"]
max_upload_mib = 512
```

Explicitly reinclude these files in the project `.modalignore`:

```gitignore
!checkpoints/
!checkpoints/**
```

Your script must load the checkpoint if it exists and save it on stop. The
included toy training example demonstrates saving; add your own resume/load logic.
For very large weights, prefer provider-native persistent storage or model cache
rather than repeatedly round-tripping them through the local project.

## Persistence limits

Sync takes place at run boundaries and does not continuously mirror a running
filesystem. Source edits take effect on the next run. A hard container kill,
preemption, or client disconnect may leave only previously committed artifacts.
No checkpointing mechanism can recover state that the workload has not saved.
Source blobs, model caches, and artifacts persist until removed through Modal's
Volume tools; Skyrun does not delete them automatically. Use distinct application
and Volume names for unrelated projects. Modal's
[Volume consistency model](https://modal.com/docs/guide/volumes) governs visibility
and concurrent writers; avoid concurrent writes to the same project files.
