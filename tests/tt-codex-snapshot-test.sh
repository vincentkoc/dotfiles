#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$repo/tests/tt-safety-test.py" SnapshotTests ParserTests
printf 'tt_codex_snapshot_test=passed\n'
