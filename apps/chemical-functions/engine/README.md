# Frozen chemistry and data engine

This directory groups the original data preparation, catalog import, molecular features, and benchmark implementation with the policies and model dependency pins they require. These modules retain their original import names to preserve the fitted model's code/dependency contract.

In particular, `benchmark.py` is unchanged byte-for-byte. Moving it does not invalidate its recorded content hash. Its imported data helpers resolve their pinned policy and requirement files here.

Runtime HTTP entry points live in `../services/`. Model/database artifacts live in the app's top-level ignored `runs/` and `data/` directories. Docker and `scripts/run_tests.py` expose this directory on Python's import path.

Research CLI defaults resolve relative to this engine directory, preserving the original colocated policy/workspace convention. To operate on the original preparation project, pass its explicit `--project-dir` and dataset/run paths. No prepared dataset or original source exports are included here. Use the CLI's `--help` and the archived historical guides for research commands; app serving does not run those workflows.
