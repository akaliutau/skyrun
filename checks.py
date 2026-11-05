"""Focused CPU integration checks; no Modal credentials, model downloads or GPU needed."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modal_runner import run_workload

from sync_manifest import output_paths, snapshot

ROOT = Path(__file__).resolve().parent


def config():
    return tomllib.loads((ROOT / "gpu.toml").read_text())


class RunnerChecks(unittest.TestCase):
    def test_snapshot_never_scans_parent_or_uploads_credentials_caches_or_outputs(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as staged,
        ):
            root = Path(directory)
            for name in ("modal_runner.py", "sync_manifest.py", "requirements-gpu.txt"):
                (root / name).write_text("# runtime\n")
            for name in (
                "project/main.py",
                "project/input/data.csv",
                "project/.env",
                "project/.git/config",
                "project/venv/library.py",
                "project/.cache/weights.py",
                "project/models/model.safetensors",
                "project/outputs/result.json",
                "project/checkpoints/saved.py",
                "project/input/evaluation_solutions.json",
                "project/private.key",
                "parent-secret.txt",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder")
            (root / "project/linked.py").symlink_to(root / "parent-secret.txt")
            (root / "project/linked-dir").symlink_to(root, target_is_directory=True)
            scandir = os.scandir

            def guarded(path):
                self.assertNotEqual(Path(path), root)
                return scandir(path)

            with patch("os.scandir", guarded):
                manifest = snapshot(root, config(), staged)
            names = {file["path"] for file in manifest["files"]}
            self.assertEqual(
                names,
                {
                    "modal_runner.py",
                    "sync_manifest.py",
                    "requirements-gpu.txt",
                    "project/main.py",
                    "project/input/data.csv",
                },
            )
            (root / "project/main.py").write_text("edited after snapshot")
            self.assertEqual(
                (Path(staged) / "project/main.py").read_text(), "placeholder"
            )

    def test_unsafe_output_paths_and_source_root_are_rejected(self):
        for paths in (
            ["../out"],
            ["/tmp/out"],
            ["."],
            [".secret"],
            ["a", "a/b"],
            ["a", "a"],
            ["_runner/logs"],
        ):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                output_paths(paths)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for project_dir in (
                ".",
                "../other",
                "/tmp",
                "venv",
                "source/site-packages",
            ):
                settings = config()
                settings["sync"]["project_dir"] = project_dir
                with (
                    self.subTest(project_dir=project_dir),
                    self.assertRaises(ValueError),
                ):
                    snapshot(root, settings, root / "stage")

    def test_budget_fails_before_remote_submission(self):
        settings = config()
        settings["sync"]["max_upload_files"] = 1
        with (
            tempfile.TemporaryDirectory() as staged,
            self.assertRaisesRegex(ValueError, "budget"),
        ):
            snapshot(ROOT, settings, staged)

    def run_program(self, program, args=(), timeout=5):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "source"
        source.mkdir()
        (source / "main.py").write_text(program)
        spec = {
            "run_id": "cpu-check",
            "command": [sys.executable, "main.py", *args],
            "output_dirs": ["outputs", "checkpoints"],
            "timeout_seconds": timeout,
            "source": {"sha256": "test-snapshot"},
        }
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_workload(
                spec, source, root / "workspace", root / "artifacts", root / "cache"
            )
        return result, root

    def test_ordinary_python_arguments_relative_io_and_logs(self):
        result, root = self.run_program(
            "import os,sys,json\nfrom pathlib import Path\n"
            "print('live log')\n"
            "Path('outputs/nested').mkdir()\n"
            "Path('outputs/nested/argv.json').write_text(json.dumps(sys.argv[1:]))\n"
            "Path('checkpoints/weights.bin').write_bytes(b'checkpoint')\n"
            "assert os.environ['HF_HOME'].endswith('/cache/huggingface')\n",
            args=("hello world", "$(literal)", "--flag"),
        )
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            json.loads((root / "artifacts/outputs/nested/argv.json").read_text()),
            ["hello world", "$(literal)", "--flag"],
        )
        self.assertEqual(
            (root / "artifacts/checkpoints/weights.bin").read_bytes(), b"checkpoint"
        )
        self.assertIn("live log", (root / "artifacts/_runner/console.log").read_text())

    def test_failed_workload_keeps_outputs_and_exact_exit_code(self):
        result, root = self.run_program(
            "from pathlib import Path\nimport sys\n"
            "Path('outputs/partial.txt').write_text('keep me')\n"
            "print('failure details', file=sys.stderr)\nsys.exit(7)\n"
        )
        self.assertEqual((result["status"], result["returncode"]), ("failed", 7))
        self.assertEqual(
            (root / "artifacts/outputs/partial.txt").read_text(), "keep me"
        )
        self.assertIn(
            "failure details", (root / "artifacts/_runner/console.log").read_text()
        )

    def test_timeout_kills_subprocess_group_and_keeps_checkpoint(self):
        started = time.monotonic()
        result, root = self.run_program(
            "from pathlib import Path\nimport subprocess,sys,time\n"
            "Path('checkpoints/last.txt').write_text('saved')\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "time.sleep(60)\n",
            timeout=0.5,
        )
        self.assertEqual((result["status"], result["returncode"]), ("timed_out", 124))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((root / "artifacts/checkpoints/last.txt").read_text(), "saved")

    def test_python_exception_leaves_reviewable_failure(self):
        result, root = self.run_program("raise FileNotFoundError('test failure')\n")
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "test failure", (root / "artifacts/_runner/console.log").read_text()
        )
        self.assertEqual(
            json.loads((root / "artifacts/_runner/result.json").read_text())[
                "returncode"
            ],
            1,
        )

    def test_download_streams_nested_binary_and_hidden_outputs(self):
        from run import download

        files = {
            "runs/example/outputs/nested/weights.bin": [b"chunk1", b"\x00chunk2"],
            "runs/example/outputs/.metadata": [b"hidden file"],
            "runs/example/_runner/result.json": [b"{}"],
        }
        fake = SimpleNamespace(
            iterdir=lambda path, recursive: [
                SimpleNamespace(path=name, type=1) for name in files
            ],
            read_file=lambda name: iter(files[name]),
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(
                    sys.modules,
                    {
                        "modal.volume": SimpleNamespace(
                            FileEntryType=SimpleNamespace(FILE=1)
                        )
                    },
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                download(fake, "example", directory)
            self.assertEqual(
                (Path(directory) / "outputs/nested/weights.bin").read_bytes(),
                b"chunk1\x00chunk2",
            )
            self.assertEqual(
                (Path(directory) / "outputs/.metadata").read_text(), "hidden file"
            )

    def test_download_rejects_traversal_and_local_symlinks(self):
        from run import download

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outside").mkdir()
            (root / "local").mkdir()
            (root / "local/linked").symlink_to(
                root / "outside", target_is_directory=True
            )
            for name in ("runs/example/../outside.txt", "runs/example/linked/file.txt"):
                fake = SimpleNamespace(
                    iterdir=lambda path, recursive, name=name: [
                        SimpleNamespace(path=name, type=1)
                    ],
                    read_file=lambda path: iter([b"must not escape"]),
                )
                with (
                    patch.dict(
                        sys.modules,
                        {
                            "modal.volume": SimpleNamespace(
                                FileEntryType=SimpleNamespace(FILE=1)
                            )
                        },
                    ),
                    self.subTest(name=name),
                    self.assertRaises(ValueError),
                ):
                    download(fake, "example", root / "local")

    def test_archive_is_independent_and_excludes_generated_files(self):
        from build_archive import build

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "starter.zip"
            with contextlib.redirect_stdout(io.StringIO()):
                build(archive)
            with zipfile.ZipFile(archive) as zipped:
                self.assertTrue(
                    all(
                        name.startswith("modal-gpu-starter/")
                        for name in zipped.namelist()
                    )
                )
                self.assertFalse(
                    any(
                        "runs/" in name or "__pycache__" in name
                        for name in zipped.namelist()
                    )
                )
                zipped.extractall(root / "extracted")
            extracted = root / "extracted/modal-gpu-starter"
            process = subprocess.run(
                [sys.executable, str(extracted / "run.py"), "--dry-run"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            )
            report = json.loads(process.stdout)
            self.assertEqual(report["gpu"], "T4")
            self.assertEqual(report["command"], ["python", "demo.py", "--require-cuda"])

    def test_modal_suppressed_keyboard_interrupt_cancels_and_returns_130(self):
        from run import main

        @contextlib.contextmanager
        def modal_context():
            try:
                yield
            except KeyboardInterrupt:
                pass  # Modal 1.3.1 suppresses this after stopping the app.

        call = MagicMock(object_id="test-call")
        call.get.side_effect = KeyboardInterrupt
        function = MagicMock()
        function.spawn.return_value = call
        app = MagicMock()
        app.function.return_value.return_value = function
        app.run.side_effect = modal_context
        fake_modal = SimpleNamespace(
            Image=MagicMock(),
            Volume=MagicMock(),
            App=MagicMock(return_value=app),
            enable_output=contextlib.nullcontext,
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(sys.modules, {"modal": fake_modal}),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    ["--config", str(ROOT / "gpu.toml"), "--output-dir", directory]
                )
            self.assertEqual(code, 130)
            call.cancel.assert_called_once_with(terminate_containers=True)
            records = list(Path(directory).glob("*/interrupted.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(
                json.loads(records[0].read_text())["status"], "interrupted"
            )


if __name__ == "__main__":
    unittest.main()
