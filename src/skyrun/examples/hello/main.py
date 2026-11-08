"""A standard-library-only CPU smoke test. Run unchanged with 'python main.py'."""

import json
import platform
import sys
from pathlib import Path


def main():
    output = Path("outputs")
    output.mkdir(exist_ok=True)
    report = {
        "message": "Hello from skyrun",
        "python": sys.version,
        "platform": platform.platform(),
    }
    (output / "hello.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(report["message"], flush=True)
    print("Saved outputs/hello.json", flush=True)


if __name__ == "__main__":
    main()
