#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python -m unittest discover -s tests -v
python benchmark.py prepare
python benchmark.py train
