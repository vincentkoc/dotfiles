#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$repo/tests/tt-safety-test.py" RecoveryTests
printf 'tt_recovery_test=passed\n'
