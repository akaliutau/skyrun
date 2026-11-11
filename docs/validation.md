# Validation

## Recorded refactor checks — 2026-11-08

Local checks used Python 3.12.15, Modal SDK 1.3.1, and pathspec 1.1.1. The offline
suite, Ruff checks, isolated sdist/wheel builds, strict Twine validation, and a
clean wheel installation with all packaged examples passed.

| Live check | Run ID | Observed result |
| --- | --- | --- |
| CPU hello | `20261108T214418Z-3f9ab0450d11` | Exit 0; live output and four saved files; zero new source blobs on repeat |
| T4, selected-file sync, recovery | `20261108T214547Z-48a6fd6f03d9` | Tesla T4, 15,360 MiB; exit 0; local JSON updated; all six files recovered without another workload |
| Pinned Qwen inference | `20261108T215431Z-caf2da8207a6` | T4, `cuda:0`, float16; eight generated tokens; exit 0; all six files downloaded |
| PyTorch training with real Ctrl-C | `20261108T215529Z-dc66b6bf8bb9` | T4, `cuda:0`; checkpoint at step 78; unforced cancellation exit 130; subprocess exit 0; five saved files downloaded |

The initial CPU smoke exposed Modal's symlink-backed Volume mountpoints. The
provider now resolves trusted mount roots before applying path checks inside
them, with regression tests for both trusted mounts and forbidden inner links.
The inference and training scripts live in the packaged examples and use pinned
GPU requirements. Their recorded live checks exercised the same execution engine
used by the CLI and Python API. Inference ran with `--require-cuda --max-new-tokens 8` and a
180-second budget. Training used `--require-cuda --steps 1000 --delay 0.2` and a
90-second budget, followed by one actual terminal Ctrl-C after readiness. Its
downloaded checkpoint was loaded locally on CPU with `weights_only=True` and
verified to contain matching step, model, and optimizer state. The complete raw
logs and results are retained under these IDs in `runs/` and on the configured
artifact Volume. Training reused all source blobs without additional uploads.

## Offline regression suite

The offline suite checks behavior without Modal credentials or billed compute:

```bash
python -m pip install -e '.[modal,dev]'
python -m pytest
python -m pytest --cov=skyrun --cov-report=term-missing
python -m ruff check .
python -m ruff format --check .
```

Coverage reports focus on infrastructure code; model samples are exercised by
explicit example smokes rather than downloaded model weights in the unit suite.
Tests include source boundaries, ignore negation, deterministic frozen bytes and
bundles, file modes, transfer interruption, cache reuse/corruption, local symlink
and traversal rejection, and sync conflicts. Real POSIX subprocess tests exercise
PTY/pipes, carriage returns, split Unicode, full logs under backpressure, ordinary
failure, missing executables, graceful SIGINT saves, forced kills, timeout cleanup,
pre-start cancellation, and descendant cleanup.

Provider transport tests use fakes at the cloud boundary and exercise the installed
Modal SDK's real output context to check quiet progress display and raw workload
streaming. The complete local orchestrator is also exercised with real subprocess
execution and local artifact transport. These checks do not establish that a
particular Modal deployment, GPU,
image build, or network path works; that requires the following live checks.

## Distribution validation

```bash
python -m build
python -m twine check --strict dist/*
python tools/check_dist.py
```

CI runs the offline suite on Python 3.11–3.14 with the latest allowed Modal 1.3 SDK
and checks Modal 1.3.1–1.3.4 separately on Python 3.12. It checks style, builds both
package formats, and installs the wheel into a clean environment to verify the CLI,
packaged policies, and sample initialization without Modal installed.

## Live Modal CPU smoke

Authenticate your account, then explicitly opt in:

```bash
SKYRUN_TEST_MODAL=1 python -m pytest -m integration -v
```

Expected: exit `0`, a committed raw console log, and `outputs/hello.json`. This
allocates CPU compute and persistent storage in your Modal account. Normal test
runs skip this check. Remove test Volumes with Modal's tools when no longer needed.

## Bounded GPU, sync, and recovery smoke

```bash
SKYRUN_TEST_GPU=1 python -m pytest tests/test_integration.py::test_live_modal_gpu_sync_and_recovery -v -s
```

This separately enabled check requests one T4 with a 60-second workload budget,
uses `nvidia-smi` to confirm the device, round-trips a selected JSON file, and
recovers its saved artifacts without another compute invocation. It uses the
standard library and does not download a model. It incurs GPU/storage charges.
Setting `SKYRUN_TEST_MODAL` alone does not enable this GPU test.

## GPU release checklist

1. Create `inference` using `skyrun init --example inference`, run its default
   command, and inspect the actual GPU/dtype in `outputs/inference.json`.
2. Repeat without changes and confirm zero new source blobs and reuse of the
   model cache. Change one source file and confirm only its new content uploads.
3. Create `training`, wait for `TRAINING_READY`, press Ctrl-C once, and verify exit
   `130`, an unforced stop, the checkpoint, and the summary's completed step.
4. Run a workload that ignores SIGINT; press Ctrl-C twice and confirm forced
   process-group cleanup while saved artifacts and logs still download.
5. Run with a short timeout and a workload cleanup handler; verify exit `124`
   and saved cleanup outputs. Also verify ordinary nonzero exits retain artifacts.
6. Recover an earlier run into a different local parent and verify its files
   without starting compute.
7. Enable a selected file round trip; verify remote changes apply. Edit that file
   locally while the workload runs and confirm conflict exit `3` preserves both
   versions. Retry with the explicit local/remote policies.

Record actual SDK/Python versions, hardware, run IDs, exit codes, and artifact
checks in release notes or the PR. Never claim an unexecuted live check passed.
