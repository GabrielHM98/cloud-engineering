# Folder verification

- Python 3.12 source/test run after reorganization: 99 tests discovered; 90 passed and 9 real-runtime service tests explicitly skipped because the WSL model/database were not present.
- Runtime packaging tests use synthetic fixtures and cover reproducible archives, hash verification, missing inputs, unexpected archive members/links, preservation of an existing runtime, and installation round trips.
- App-manifest tests check the three Docker targets, readiness paths, backend aliases, argument-array hooks, and runtime verification before release checks.
- Python compilation, installed Python dependency consistency, dependency-path resolution, and all three service imports with the organized engine path passed.
- The original benchmark implementation is preserved with SHA-256 `572d806d356781db6df06f8cf5a60af26ae5f1b97611e458c6fb9d87dfbb255a`.

Runtime installation was also tested from a legacy flat source into the organized layout. No WSL-trained model, catalog database, raw exports, screenshots, registry credentials, or root GitHub workflow is included. The app's Docker base reference is pinned, but image builds and container integration were not executed in this preparation workspace. Import your trusted runtime and run the smoke test to verify the new location. The shared root release workflow and cross-repo promotion job remain separate repository-level work.
