# Contributing to Skyrun

Thank you for helping make remote execution dependable and easy to use. Small,
well-tested fixes, clear documentation, and additional provider adapters are
welcome. Open an issue before a substantial API or behavior change so maintainers
can discuss compatibility and scope.

## Development environment

```bash
git clone https://github.com/akaliutau/skyrun.git
cd skyrun
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[modal,dev]'
```

Python 3.11+ is required. Tests run without Modal credentials or cloud compute by
default. Remote process tests use real POSIX subprocesses; CI runs on Linux across
Python 3.11–3.14. Runnable examples live in `src/skyrun/examples/` and ship with
every wheel. Update their scripts and configuration together.

## Before opening a pull request

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m build
python -m twine check --strict dist/*
python tools/check_dist.py
```

Use `python -m ruff format .` to apply formatting. Add a regression test for a bug
fix or behavior change. Prefer tests of externally visible behavior: process
cleanup, saved artifacts, path boundaries, transfer interruption, and file
conflicts. Avoid tests that simply repeat implementation details. Update the
relevant guide and `CHANGELOG.md` when user behavior changes.

Tests marked `integration` are skipped unless explicitly enabled. To exercise the
live CPU sample against your own authenticated Modal account:

```bash
SKYRUN_TEST_MODAL=1 python -m pytest -m integration -v
```

This allocates Modal CPU compute, creates persistent Volumes, and can incur
charges. Run it deliberately. A separate bounded GPU smoke checks T4 allocation,
selected-file sync, and GPU-free recovery, without model downloads:

```bash
SKYRUN_TEST_GPU=1 python -m pytest tests/test_integration.py::test_live_modal_gpu_sync_and_recovery -v
```

For model/training validation, follow the training and
inference recipes in [the example catalog](docs/examples.md) and record the GPU,
SDK version, run ID, exit code, and relevant saved artifacts in your PR. Never
commit credentials, model weights, run data, or private environment output.

## Compatibility requirements

Preserve the documented `skyrun` CLI, Python API, `skyrun.toml`, and
`[tool.skyrun]` configuration. Keep ordinary workload scripts free of runner
imports. Configuration and dry runs must work without a provider SDK or
authentication. Maintain live raw output,
first/second/third stop semantics, nonzero exit codes, process-group cleanup,
artifact recovery, and source isolation.

Two-way sync must remain opt-in, detect local changes, verify downloaded hashes,
and retain remote versions on conflict. Do not add automatic deletion or
overwriting to default sync behavior. Source and artifact paths must reject
traversal and symlinks. A backend must not retry an arbitrary workload without
explicit user configuration.

Read [architecture and extension points](docs/architecture.md) before adding a
provider. Keep SDK imports inside the provider boundary, use entry points for
extensions, and add transport contract tests. New dependencies should have a
clear purpose and support the declared Python versions.

## Pull requests and releases

Explain the concrete problem, resulting behavior, and meaningful validation.
Include a reproduction for bug fixes and identify any remaining live-cloud checks.
Keep unrelated formatting and redesigns out of a focused change. Maintainers
review contributions under the [code of conduct](CODE_OF_CONDUCT.md).

Contributions are licensed under the repository's MIT license. Add yourself to
[AUTHORS.md](AUTHORS.md) when your first contribution is merged, if you wish.
Maintainers publish following [the release guide](docs/publishing.md); do not push
release tags or upload distributions as part of an ordinary contribution.
