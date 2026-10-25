# Chemical functions

Application folder for the `app-releases` monorepo. Enter a molecular SMILES string, inspect recorded chemical uses, and rank candidate functions using a saved classifier. Source annotations and model suggestions are displayed separately.

| Component | Docker target | Owns |
|---|---|---|
| `web` | `web` | Browser interface and gateway API |
| `catalog` | `catalog` | Read-only SQLite catalog and source evidence |
| `predictor` | `predictor` | Molecular features, saved model, and training/validation references |

All three listen on port 8080 in their own containers. The web service calls the others over HTTP. `app.yaml` declares the Docker targets, readiness endpoints, resource limits, environment variables, preparation commands, and tests for a shared release pipeline.

The model is nine Random Forest classifiers (`forest_leaf3`) on 2,048-bit Morgan fingerprints. It scores recorded CPDat function annotations; it does not certify industrial performance. The current release reuses the fitted model and does not retrain it.

## Folder map

| Area | Responsibility |
|---|---|
| `services/` | HTTP APIs, model-serving adapter, and web UI |
| `engine/` | Frozen chemistry, data preparation, model training, policies, and model-specific dependency pins |
| `scripts/` | Runtime import, preflight checks, test runner, and smoke checks |
| `requirements/` | HTTP runtime and development dependency entry points |
| `tests/` | Automated verification |
| `runtime/` | Committed checksum lock for model/database artifacts |
| `docs/` | Pipeline contract, licensing, migration, and historical guides |
| `data/`, `runs/`, `.runtime/` | Ignored local/generated artifacts |

Root files are the README, `app.yaml`, Dockerfile, Compose configuration, and ignore rules. See [migration](docs/migration.md) when replacing the earlier flat folder.

## Add this folder to your repo

The ZIP contains `apps/chemical-functions/`. Extract it from the root of your existing `app-releases` clone:

```bash
cd ~/src/app-releases
unzip /path/to/chemical-functions-folder.zip
cd apps/chemical-functions
```

This is the complete app source folder, not an overlay for the old starter folder. Keep `~/src/industrial-use-starter` intact until you have imported its working runtime. No model, database, virtual environment, or credentials are included in this source package.

## Import the working runtime once

From `app-releases/apps/chemical-functions`:

```bash
python3 scripts/runtime_artifacts.py pack \
  --from-project ~/src/industrial-use-starter \
  --release-tag chemical-functions-runtime-v1

python3 scripts/runtime_artifacts.py install \
  --archive .runtime/chemical-functions-runtime-v1.tar.gz

python3 scripts/runtime_artifacts.py verify
```

These standard-library commands package the existing model/catalog, record archive and file checksums in `runtime/lock.json`, and install matching files into this new folder. They do not deserialize or retrain the model. Different existing artifacts are preserved rather than overwritten. The source benchmark implementation and exact dependency pins must match the model's run manifest.

The archive contains exactly:

```text
data/catalog/chemicals.sqlite3
runs/benchmark-v1/model_bundle.joblib
runs/benchmark-v1/run_manifest.json
```

The three binary/runtime files and `.runtime/` archive are ignored by Git. Commit `runtime/lock.json`. Archive checksums verify bytes, not the trustworthiness of arbitrary model pickles; seed this only from your trusted working project.

## Make the runtime available to GitHub Actions

The shared pipeline's `prepare` hook fetches the matching runtime Release asset from the same GitHub repo. Upload it **before pushing the app folder to a branch that automatically builds releases**. From this app directory, with GitHub CLI authenticated to your repo:

```bash
gh release create chemical-functions-runtime-v1 \
  .runtime/chemical-functions-runtime-v1.tar.gz \
  --target main --title "Chemical functions runtime v1" \
  --notes-file docs/runtime-release-notes.md
```

The remote repo must already have a `main` commit for that command. If your repo is completely empty, first push the shared-pipeline/bootstrap source without this app, create the runtime Release asset against that existing commit, then push this seeded app folder. Alternatively, keep automatic release disabled until the runtime upload is complete. A placeholder lock or missing asset intentionally fails preparation.

To inspect/apply this folder's source changes from the monorepo root:

```bash
cd ~/src/app-releases
git add apps/chemical-functions
git diff --cached --stat
git commit -m "Add chemical functions application"
git push
```

No GitHub Actions workflow is included **inside this app folder**. A shared root workflow must discover `app.yaml` and execute its contract. Merely committing this folder to an empty repo with no root workflow does not create a release pipeline. See [pipeline contract](docs/pipeline-contract.md).

## Run locally

After installing the runtime, from this app directory:

```bash
docker compose up --build -d --wait --wait-timeout 180
python3 scripts/smoke_test.py
```

Open http://localhost:8080. Search `ethanol` or submit `CCO` for a prediction. The model/database are embedded in their respective images; there is no extra database server or writable data volume.

Stop this app's local stack:

```bash
docker compose down
```

Your source files, database, saved model, and Docker images remain. `docker compose stop` retains the containers for a later `docker compose start`.

## Develop and test

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements/dev.txt
python scripts/run_tests.py
```

When the runtime is absent, the suite reports the real-artifact service tests as skipped. Release preparation must first run `runtime_artifacts.py verify`; releases must not substitute source-only tests for actual-model integration. The existing `scripts/smoke_test.py` checks the public request path through all three containers.

## Source and data scope

The app's catalog uses a pinned historical [CPDat v4.0 article version 5](https://epa.figshare.com/articles/dataset/The_Chemical_and_Products_Database_CPDat_MySQL_Data_File/5352997/5) joined to collected EPA dashboard exports. The accepted catalog contains 8,335 structures and 14,675 structure–function pairs, under the pinned chemistry policy. Unrecorded uses remain unknown. Source identity checks do not experimentally verify a function.

Preparation, benchmark, policy, and catalog helper files are grouped in `engine/`. They retain their original module names and model dependency pins for artifact compatibility. The older step-by-step guides are archived under `docs/history/`; they refer to the original starter layout and example files and are not this folder's current installation instructions. The historical `docs/history/run_collection_demo.sh` also requires its original example export files, which are not packaged here. Use `--help` on the preparation scripts for their current command contracts.

## Release boundary

The shared root pipeline should publish three images named `chemical-functions-web`, `chemical-functions-catalog`, and `chemical-functions-predictor` in the configured registry namespace. It should output exact registry digests for promotion to `paas-releases`. This app folder does not update that repo or access your cluster.

A new model/catalog needs a deliberately reviewed runtime version and updated lock. Model dependency changes may require new training and validation; do not rewrite expected hashes to accept unexplained differences.

## Licensing and verification

MIT was recommended for the project's own code, but no code license has been selected/applied in this package. Dependencies and datasets keep their own terms. See [licensing notes](docs/licensing.md).

Source/runtime-packaging tests were run in the preparation workspace; the WSL-trained model was unavailable there, so real-artifact service tests were skipped explicitly. Docker execution and registry publication were not performed there. Your first local container smoke test and shared-pipeline run verify those integration steps with the imported runtime.
