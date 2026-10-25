# Step 6 — automate the source collection boundary

Starting with collection is the right next engineering step because the preparation/training prototype already works. The sequence here is **contract, adapter, tests, integration check, container packaging, CI, scheduling**. This establishes a repeatable local command before tying it to a scheduler.

The existing CPDat download was already automated. This update makes acquisition explicit, adds immutable source captures, and implements the CTX structure API adapter that can replace manual exports. It leaves the trained benchmark and its source code unchanged.

## Install the overlay

Extract the ZIP into `~/src`, merging its `industrial-use-starter/` directory with your existing project. It adds:

- `collect_data.py`
- `collection_policy.json`
- `run_collection_demo.sh`
- `tests/test_collection.py`
- `COLLECTION_CONTRACT.md`
- this document and separate example results

No new packages are required. Collection uses Python's standard library and the existing `audit_cpdat.py`. It targets your Linux/WSL environment; the concurrency lock uses Linux advisory locking.

## First, exercise collection without an API key

```bash
cd ~/src/industrial-use-starter
source .venv/bin/activate
bash run_collection_demo.sh
```

The demo runs 22 collection tests, verifies/captures the pinned CPDat archive and archives both original dashboard exports from `example-batch-input/`. It writes a normalized combined CSV without deduplicating away original rows. Your existing structure importer still makes the chemical decisions.

It expects the full ID list from your earlier audit. In a fresh project where those prerequisites are absent, run `python3 audit_cpdat.py` to generate them. If you renamed or moved the dashboard CSVs, use the individual command below with their actual paths.

The complete project suite now has **66 passing tests**. The collector's normalized CSV was also passed through the existing structure importer and reproduced the same 8,335 accepted structures, 14,675 structure-role pairs and 99,861 supporting records. This verification did not retrain or alter your saved model.

The demo performs these capture commands:

```bash
python3 collect_data.py cpdat --run-id cpdat-v4-source-v1

python3 collect_data.py archive-csv \
  --run-id dashboard-capture-v1 \
  --csv example-batch-input/CCD-Batch-Search_2026-10-18_07_37_43.csv \
        example-batch-input/CCD-Batch-Search_2026-10-18_07_41_58.csv
```

The first command reuses the verified archive if present; otherwise it downloads the pinned version. Capturing it stores another content-addressed copy under the collection store, requiring about 102 MiB initially. Repeated captures reuse the same raw object after verification. The large archive and original exports are not duplicated in this overlay ZIP.

Inspect the acquired structure data:

```bash
cat data/raw/collection/runs/dashboard-capture-v1/manifest.json
```

Expected counts: 25,336 original export records, 15,336 requested IDs matched and 10,000 repeated input records. Chemical acceptance is a separate downstream result.

## Then enable live structure collection

EPA requires a personal CTX API key. Its access instructions direct users to contact `ccte_api@epa.gov` for a free key:

https://www.epa.gov/comptox-tools/computational-toxicology-and-exposure-apis-about

Set it in the current Bash session without putting its value into the command itself:

```bash
read -r -s -p 'EPA CTX API key: ' CTX_API_KEY
echo
export CTX_API_KEY
```

Start with five IDs:

```bash
python3 collect_data.py ctx \
  --run-id ctx-smoke-001 \
  --ids data/interim/structure_sample_100.txt \
  --limit 5
```

This makes at most one initial batch request under the current policy, with bounded retries for transient failures. It captures the response and creates:

```text
data/raw/collection/runs/ctx-smoke-001/manifest.json
data/raw/collection/runs/ctx-smoke-001/requested_ids.txt
data/raw/collection/runs/ctx-smoke-001/structures.csv
```

The five-ID smoke test establishes live authentication, returned fields and downstream compatibility. It does not establish full-dataset coverage. No authenticated live CTX collection was executed while preparing this update, because no personal key was supplied. HTTP contract and failure tests use synthetic responses; the successful real-data integration used your existing dashboard exports.

If the request fails transiently, rerun the same command and run ID to resume. If a schema failure occurs, the successful HTTP body and receipt remain captured for inspection. An authentication failure logs its status without the key or provider error body.

If you deliberately change the requested subset or policy, choose a new run ID. Do not change `--limit 5` into a full-list request while keeping `ctx-smoke-001`: its contract is already fixed.

After the smoke test is verified, a new run can collect the full list:

```bash
python3 collect_data.py ctx \
  --run-id ctx-full-001 \
  --ids data/interim/dtxsids_all.txt
```

Requests are paced, so full collection can take minutes. The default batch size is a conservative 100 IDs with a minimum three-second interval between request starts; these are local choices, not a claim about your account's service quota. Missing source records are reported rather than invented. Current API data can differ from the historical dashboard exports, and any resulting dataset should receive its own version.

## Check the live collection with the existing importer

Use the exact requested-ID file exported for that collection, including when the run used `--limit`. Write to a separate output directory so the frozen benchmark remains untouched:

```bash
python import_structure_batches.py \
  --csv data/raw/collection/runs/ctx-smoke-001/structures.csv \
  --expected-ids data/raw/collection/runs/ctx-smoke-001/requested_ids.txt \
  --output-dir work/ctx-smoke-001 \
  --require-complete
```

The collection command checks source transport, schema and identifier coverage. The importer checks molecular graph scope, identity cross-checks and CPDat annotation joins. An ID can be successfully returned by the API while having missing or unusable chemistry.

Avoid directing new collection experiments into the old `reports/structure_full` or frozen benchmark directories. Keep generated `work/` checks out of source commits, along with raw collection data and fitted artifacts.

## What this step automates

| Capability | Implemented |
|---|---|
| Obtain the pinned CPDat archive and verify checksums | Yes; existing acquisition reused |
| Preserve dashboard exports byte-for-byte | Yes |
| Fetch CTX structure details through a documented API contract | Adapter implemented; authenticated smoke test still needed |
| Preserve raw responses and request provenance | Yes |
| Resume completed CTX batches | Yes |
| Detect source schema and immutable-run changes | Yes |
| Produce the existing importer CSV plus exact requested-ID list | Yes |
| Discover the latest source release automatically | No; source versions remain explicit |
| Cross-run per-ID refresh/TTL cache | No; each new API run is a fresh capture |
| Schedule jobs or deploy containers | Subsequent step |

The CPDat source remains the chosen historical snapshot. Running a collector repeatedly does not change that choice automatically. A new data source/release, chemistry policy or label definition remains a versioned change with its own checks.

## How this will fit the workflow engine

The stable acquisition outputs are the raw objects, completed manifest, requested IDs and structure CSV. A future workflow job can pass those paths to preparation and use the exit status to decide whether to continue.

Exit **0** means acquisition completed with full identifier mapping coverage, or incomplete coverage was explicitly allowed. Exit **1** means acquisition/configuration/schema/integrity failed. Exit **2** means acquisition finished but identifier coverage remains incomplete. No exit code claims model quality or chemical performance.

The collector emits a compact JSON summary on stdout and progress/errors on stderr. Each orchestrated execution should have a unique run ID, and retries of that execution should reuse it. An API key can then be injected as an environment variable from a cluster Secret. Your GitOps manifests can pin the collector image and collection policy independently of model deployment.

The next checkpoint is concrete: run the offline demo, then obtain the key and verify the five-ID live request. That completes the external integration check before packaging the collector as a scheduled container job.
