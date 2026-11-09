# Changelog

All notable user-facing changes are recorded here. Skyrun uses semantic versions;
the provider extension contract may evolve between minor releases before 1.0.

## Unreleased

### Fixed

- Concurrent sync clients now hold a local project lock across conflict checks,
  file replacement, permissions, and reporting, preventing silent overwrites.
- Source snapshots use the same lock so they cannot copy a partially applied
  sync. Cloud execution and artifact downloads remain parallel.
- Windows lock metadata is excluded from source uploads and sync selections.
- Added independent-process regressions for concurrent writers, snapshot/sync
  coordination, lock release after exceptions or process termination, and
  independence between projects.

## 0.1.0 — Unreleased

### Added

- Installable `skyrun` package, console entry point, `python -m skyrun`, and Python API.
- Provider-neutral execution/download interface and third-party entry points.
- Dedicated TOML and `[tool.skyrun]` configuration with local validation.
- Project `.modalignore`, packaged baseline rules, bounded immutable snapshots,
  executable mode preservation, and content-addressed source caching on Modal.
- Optional run-boundary bidirectional sync with hash verification, local conflict
  detection, explicit resolution policies, and retained remote versions.
- Wheel-distributed hello, training, and inference projects exposed by `skyrun init`.
- Offline regression suite, opt-in live CPU smoke, CI, distribution checks,
  contributor files, and TestPyPI/PyPI publishing workflows and documentation.

### Execution

- PTY output, unbuffered raw streaming, complete console logs, process-group cleanup,
  graceful checkpoint allowance, and escalating stop requests.
- Workload exit codes, timeout/cancel status, isolated per-run outputs, model caching,
  streamed artifact downloads, and GPU-free recovery of committed files.

### Fixed

- Hardcoded remote source location that could diverge from the selected project.
- Predictable download temporary filenames and insufficient ancestor symlink checks.
- Source snapshots now retain executable bits and report files modified during copying.
- Trusted Modal mountpoints are resolved before applying symlink checks to their contents.

### Repository cleanup

- Repository links, package metadata, badges, clone commands, and Trusted Publishing
  settings target `akaliutau/skyrun`.
- The package CLI/API is the sole execution interface; examples have one canonical
  location under `src/skyrun/examples/`.
- Removed standalone compatibility scripts, obsolete configuration aliases,
  duplicate examples/dependency files, and the separate starter archive builder.
- Releases contain the standard Python wheel and source distribution.
