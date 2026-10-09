#!/usr/bin/env bash
set -euo pipefail

runner_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$runner_dir/../.." && pwd)
input=${INPUT:-"$runner_dir/../test-vectors/txn/fixtures/0103115bd3ec4f27fcf4dbd7cee22a530e68f797_2135631.fix"}
binary=${BIN:-"$runner_dir/../agave/dev-bins/target-polytracker/x86_64-unknown-linux-gnu/debug/agave-polytracker-runner"}
trace=${TRACE:-"$runner_dir/runtime-execution-and-reconstruction.tdag"}
python=${POLYTRACKER_PYTHON:-"$repo_root/.venv/bin/python"}

if [[ ! -x "$python" && -x /tmp/polytracker-analysis/bin/python ]]; then
    python=/tmp/polytracker-analysis/bin/python
fi

POLYDB="$trace" "$binary" "$input"
"$python" "$runner_dir/analyze_fields.py" \
    "$trace" "$input" \
    --ignore-prefix input.bank.features
