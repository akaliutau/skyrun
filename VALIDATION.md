# Validation — 2026-11-05

The standalone starter passed remote T4 inference and automatic output downloads.
The run used a copy extracted under `/tmp`, outside the research repository.
No research source/data or local credentials were uploaded. Inference smoke
checks are execution evidence, not model quality or ARC accuracy measurements.

| Check | Result |
| --- | --- |
| Starter CPU integration checks | 11 passed using system Python without Modal installed |
| Existing research upload-policy checks | 4 passed; research upload manifest unchanged |
| Ruff | Passed |
| Archive extraction | Standalone `run.py --dry-run` works from an unrelated working directory |
| Remote Python failure | Exit 7 preserved; partial output and full run records downloaded |
| Remote command timeout | Exit 124 after 1.007 seconds; saved checkpoint downloaded |
| Independent recovery | Prior run downloaded using `--download`, without a GPU |
| Remote CPU inference | Qwen float32, 8 generated tokens, 20.122 seconds total |
| Remote GPU inference | T4, Qwen float16 on `cuda:0`, 48 generated tokens, 20.213 seconds total |

The CPU integration checks exercise source staging and exclusions, upload limits,
relative paths, literal command arguments, nested binary downloads, hidden output
files, path traversal and symlink rejection, partial outputs after failure,
process-group timeout cleanup, archive independence and Modal's suppressed
KeyboardInterrupt behavior. The latter exposed a cancellation bug during testing;
the launcher now records interruption and returns 130 when Modal suppresses it.

The successful T4 run requested one CPU, 4,096 MiB host memory and a 180-second
workload limit. The shipping defaults use the same resources with a 600-second
workload limit. An initial two-CPU / 8-GiB T4 request and an explicit L4 request
remained queued and were canceled. The existing launcher successfully ran a
bounded T4 `nvidia-smi` control. The starter then started successfully after
matching that control's smaller resource request. This does not isolate the
cause of the earlier queueing or establish a performance comparison with L4.

The measured GPU environment was Python 3.11.10, PyTorch 2.8.0+cu128,
Transformers 4.51.3, CUDA runtime 12.8, cuDNN 91002, NVIDIA driver 610.57.04 and one
Tesla T4 with compute capability 7.5. The model has 494,032,768 parameters and
uses public Apache 2.0 weights at revision
`7ae557604adf67be50417f59c2c2f167def9a775`.

Model/tokenizer download and loading took 11.985 seconds; synchronized GPU
generation took 1.737 seconds. Peak PyTorch GPU allocation was 1,008,180,224 bytes.
Generation was capped at 48 tokens, so the saved response is truncated. The
sample logs retain Transformers' sliding-window/SDPA and unused sampling-setting
warnings; they did not prevent inference. GPU validation used float16, and CPU
validation used float32; these runs are not an accuracy-versus-time comparison.

The environment report includes raw `/proc/meminfo` because the minimal image
does not install `free`. Container cgroup files can be absent, and filesystem
capacity reported by the virtualized sandbox can be a placeholder. Requested
CPU/RAM budgets are recorded separately in the run metadata.

Evidence retained alongside the source checkout:

| Run | ID / local result directory |
| --- | --- |
| T4 inference | `20261105T164623Z-e19d4a90`, `artifacts/modal-gpu-starter-t4/20261105T164623Z-e19d4a90/` |
| CPU inference | `20261105T164513Z-0e891337`, `artifacts/modal-gpu-starter-inference-cpu/20261105T164513Z-0e891337/` |
| Python failure | `20261105T163751Z-a6db1d3e`, `artifacts/modal-gpu-starter-failure/20261105T163751Z-a6db1d3e/` |
| Command timeout | `20261105T164218Z-124caa68`, `artifacts/modal-gpu-starter-timeout/20261105T164218Z-124caa68/` |

The tested upload is four files / 17,157 bytes, SHA256
`70015019f371848dc297776115b211ea67dee1a7740b99811b6a16e7ced5daf2`.
The archive also includes the local launcher, configuration, documentation,
archive builder and CPU checks, which are not part of the remote source upload.
Downloaded run artifacts and model weights are excluded from the archive.
