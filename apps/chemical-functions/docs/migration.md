# Replace the earlier flat app folder

Use this version as a replacement, not an overlay: unzipping on top of the old layout leaves obsolete root files behind. Preserve a copy of your current app folder, including ignored runtime files and any local edits, outside the repository before replacing it. Keep the original `industrial-use-starter` working project until the new runtime verification succeeds.

The ZIP still contains `apps/chemical-functions/`, intended for the root of `app-releases`. After replacing the old source folder, import the runtime using the README commands. `scripts/runtime_artifacts.py pack --from-project ...` accepts both the original flat starter layout and this organized layout.

If you already seeded a `runtime/lock.json`, preserve it and its matching `.runtime/` archive. Restore that lock into the new source folder and run `scripts/runtime_artifacts.py install --archive /path/to/matching/archive.tar.gz`; the installer verifies it before writing files. Do not substitute the unseeded lock in this source package for an already approved runtime by mistake.

Path changes:

| Earlier path | Current path |
|---|---|
| Root data/model Python modules and policies | `engine/` |
| `requirements-services.txt` | `requirements/api.txt` |
| `requirements-dev.txt` | `requirements/dev.txt` |
| Model/structure requirement files | `engine/requirements-*.txt` |
| `containers/preflight.py` | `scripts/preflight.py` |
| `containers/smoke_test.py` | `scripts/smoke_test.py` |
| `containers/local_smoke.py` | `scripts/local_smoke.py` |
| Host unittest discovery command | `python scripts/run_tests.py` |
| Historical shell runners | `docs/history/` |

The HTTP API paths, three Docker target names, container ports, artifact paths (`data/catalog` and `runs/benchmark-v1`), and public `app.yaml` hook structure are preserved. The manifest's commands now use the new locations.

The benchmark source content/hash remains unchanged. Engine code stays colocated with its original policies and model dependency pins to preserve that contract. Preflight/runtime-import helpers understand both old and new locations. This is an organization change, not model retraining or a change to recorded chemical annotations.
