#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python3 -m unittest discover -s tests -p test_collection.py -v
python3 collect_data.py cpdat --run-id cpdat-v4-source-v1
python3 collect_data.py archive-csv \
  --run-id dashboard-capture-v1 \
  --csv example-batch-input/CCD-Batch-Search_2026-10-18_07_37_43.csv \
        example-batch-input/CCD-Batch-Search_2026-10-18_07_41_58.csv
