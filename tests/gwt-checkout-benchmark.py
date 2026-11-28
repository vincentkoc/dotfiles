#!/usr/bin/env python3
"""Compare native and shared .agents creation using an isolated committed fixture."""
import argparse
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import tarfile
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3, choices=range(1, 6))
    args = parser.parse_args()
    source_head = subprocess.run(["git", "-C", str(args.source), "rev-parse", "HEAD"],
                                 check=True, capture_output=True, text=True).stdout.strip()
    archive = subprocess.run(["git", "-C", str(args.source), "archive", source_head, ".agents"],
                             check=True, capture_output=True).stdout
    if len(archive) > 64 * 1024 * 1024:
        raise SystemExit("agent fixture exceeds the 64 MiB benchmark bound")
    with tempfile.TemporaryDirectory(prefix="gwt-checkout-benchmark-") as temporary:
        root = Path(temporary).resolve()
        source = root / "source"
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(source, filter="data")
        env = dict(os.environ, HOME=str(root / "home"), GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", TMUX="",
                   DOTFILES_WORKTREES_ROOT=str(root / "worktrees"),
                   XDG_CONFIG_HOME=str(root / "config"), XDG_STATE_HOME=str(root / "state"))
        for key in ("DOTFILES_GWT_LOADED", "DOTFILES_GIT_SPARSE_ROOT", "GIT_INDEX_FILE",
                    "GIT_DIR", "GIT_WORK_TREE"):
            env.pop(key, None)

        def run(command, cwd=source, **kwargs):
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                    text=True, timeout=120, **kwargs)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return result

        run(["git", "init", "-q", "-b", "main"])
        run(["git", "config", "user.name", "Checkout benchmark"])
        run(["git", "config", "user.email", "benchmark@example.invalid"])
        run(["git", "config", "commit.gpgsign", "false"])
        run(["git", "config", "gc.auto", "0"])
        run(["git", "remote", "add", "origin", "https://github.com/gwt-checkout/benchmark.git"])
        run(["git", "add", "."])
        run(["git", "commit", "-qm", "agent checkout fixture"])
        results = {"native": [], "shared": []}
        for iteration in range(args.runs):
            for mode in (("native", "shared") if iteration % 2 == 0 else ("shared", "native")):
                env["DOTFILES_GWT_AGENT_COW"] = "1" if mode == "shared" else "0"
                branch = f"{mode}-{iteration}"
                started = time.monotonic()
                result = run(["zsh", "-f", "-c", 'source "$1"; gwt new "$2" HEAD --full',
                              "benchmark", str(ROOT / "functions/gwt/gwt.zsh"), branch])
                elapsed = time.monotonic() - started
                target = root / "worktrees/gwt-checkout-benchmark" / branch
                if run(["git", "status", "--porcelain"], cwd=target).stdout:
                    raise RuntimeError("benchmark checkout is dirty")
                report = next((json.loads(line) for line in result.stdout.splitlines()
                               if line.startswith('{"agent_checkout"')), {})
                results[mode].append({"seconds": round(elapsed, 4), **report})
        print(json.dumps({"source_head": source_head, "results": results,
            "median_seconds": {mode: round(statistics.median(row["seconds"] for row in rows), 4)
                               for mode, rows in results.items()}}, indent=2))


if __name__ == "__main__":
    main()
