"""CLI entry point: run the OCP conformance suite against a server."""
from __future__ import annotations

import subprocess
import sys


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="OCP-0002 conformance test runner")
    parser.add_argument(
        "--server-cmd",
        default="ocp-server",
        help="Command to launch the OCP server under test (default: ocp-server)",
    )
    parser.add_argument(
        "--level",
        choices=["core", "coordination", "full"],
        default="full",
        help="Conformance level to test (default: full)",
    )
    parser.add_argument("pytest_args", nargs="*", help="Extra arguments passed to pytest")
    args = parser.parse_args()

    import os
    env = {**os.environ, "OCP_SERVER_CMD": args.server_cmd}

    suite_dir = str(__import__("pathlib").Path(__file__).parent / "suite")
    markers: list[str] = []
    if args.level == "core":
        markers = ["-k", "not coordination and not events"]
    elif args.level == "coordination":
        markers = ["-k", "not events"]

    cmd = [sys.executable, "-m", "pytest", "-v", suite_dir] + markers + args.pytest_args
    result = subprocess.run(cmd, env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
