# Source collection contract v1

This is the acquisition boundary for the existing industrial-use project. It produces raw source captures, provenance, explicit requested identifiers and a transport-level CSV adapter. It does not parse molecular graphs, select labels, train or deploy models.

## Inputs and modes

| Mode | Input | Network behavior |
|---|---|---|
| `cpdat` | Explicit `source-lock.json` and optional verified cache | Downloads the pinned archive only when the cache is absent |
| `archive-csv` | One or more original dashboard exports plus expected-ID list | Offline |
| `ctx` | Expected-ID list, versioned collection policy and API key | Captures batched chemical-detail responses; completed batches replay offline |

The source ID list must contain unique syntactically valid DTXSIDs, one per line, with no header. This boundary does not validate DSSTox check digits. The `ctx --limit N` option takes the first N sorted IDs and records that exact subset. It is a transport smoke test, not a representative chemical sample.

## Successful outputs

Objects live under `data/raw/collection/objects/<sha256>` and are addressed by the SHA-256 of their original bytes. File extensions are not required: receipts identify their provenance and mode. Existing objects are checksum-verified before reuse.

A run lives under `data/raw/collection/runs/<run-id>/`:

| File | Meaning |
|---|---|
| `contract.json` | Source mode, policy, source inputs, exact requested IDs where applicable, and code hashes; excludes the API key |
| `requested_ids.txt` | Exact requested IDs in the adapter's expected-ID format; CSV/API modes |
| `receipts/*.json` | Captured file/API batch identities and raw-object references |
| `archive_receipt.json` | Pinned public archive capture metadata; CPDat mode |
| `events.jsonl` | Append-only request-attempt diagnostics; API mode |
| `structures.csv` | Regenerated adapter output for the existing structure importer; CSV/API modes |
| `manifest.json` | Completed acquisition status, source references, hashes and identifier-coverage counts |

The final manifest is written only after acquisition and adapter validation succeed. Its presence means collection completed; inspect `mapping_coverage_complete` separately. It does not establish chemical eligibility or training quality.

The CLI writes one concise JSON success summary to stdout, including paths and counts. Progress and errors go to stderr. Full request lists and detailed receipts stay in files, keeping full collection output manageable.

## Structure CSV schema

The adapter preserves the existing importer columns:

`INPUT, FOUND_BY, DTXSID, PREFERRED_NAME, DTXCID, INCHIKEY, CASRN, SMILES, QC_LEVEL, QC_NOTES`

It adds `COLLECTION_RAW_SHA256` and `COLLECTION_RECORD_NUMBER` to link rows to captured source bytes. The original dashboard file, including extra columns, remains byte-for-byte in the object store. The legacy importer uses its known columns; the collection CSV and manifests preserve this additional upstream lineage.

| API field | Importer field |
|---|---|
| Requested and returned `dtxsid` | `INPUT`, `DTXSID` |
| `preferredName` | `PREFERRED_NAME` |
| `dtxcid` | `DTXCID` |
| `inchikey` | `INCHIKEY` |
| `casrn` | `CASRN` |
| Original `smiles` | `SMILES` |
| `qcLevel`, `qcNotes` | `QC_LEVEL`, `QC_NOTES` |

`FOUND_BY` is labelled `CTX_API_DTXSID` for API acquisitions. API responses must be a JSON array, with `dtxsid`, `smiles` and `inchikey` keys on every returned record; structure/key values may be null. Null values become blank adapter fields. Missing optional metadata fields are counted in the manifest. Unexpected value types, duplicate JSON keys, malformed/unrequested returned IDs or missing required fields stop adaptation.

The endpoint uses `chemicaldetailall` to request original structure and QC fields. `msReadySmiles` and `qsarReadySmiles` are never used as fallback representations. Other attributes in the response remain in the raw capture and are not turned into annotation labels.

Repeated source records are preserved. The downstream importer decides whether duplicates agree or conflict; the collector does not silently choose one chemical mapping. Records omitted by the API remain explicitly missing in coverage counts. The collector does not fabricate provider rows for them.

## Versions and provenance

CPDat uses the existing pinned historical v4.0 archive, Figshare article version 5. This command does not discover the latest CPDat release automatically or change `source-lock.json`.

CTX captures record the endpoint, projection, retrieval time and response hash. A precise underlying DSSTox database release is not supplied by these response records, so it is marked unknown. The reference OpenAPI version is an API documentation version, not a database release identifier.

The documentation URL and checksum are recorded in `collection_policy.json`. The public contract was checked before implementation; authenticated success responses have not yet been tested with a personal API key. Unit tests use explicitly synthetic responses, and integration verification uses the real dashboard CSVs supplied for this project.

Source export retrieval dates remain unknown unless independently supplied with `--retrieved-at`. Capture time is not substituted for original download time, and dates are not inferred from filenames or modification times.

## Reruns, failure and concurrency

- Same run ID plus identical contract: verify existing captures and replay them. Completed API batches make no additional request.
- Changed IDs, source bytes, code or collection policy: use a new run ID. Existing run contracts refuse silent replacement.
- A failed API run retains successful batch receipts; retrying the same run resumes at uncaptured batches.
- A successful HTTP response is archived before parsing, allowing schema failures to be investigated offline. A captured schema failure is not automatically refetched on every retry.
- A new run ID starts an explicitly fresh acquisition. Cross-run per-ID cache expiry is not implemented in v1.
- Linux advisory locks prevent concurrent writers for the same run ID and are released by the OS on process exit. Temporary writes are atomically replaced. Completed manifests and raw objects are integrity-checked; adapter CSVs can be regenerated from captured inputs.
- The public CPDat archive reuses the original verified streaming download/cache behavior. Interrupted archive downloads restart; HTTP Range resumption is not implemented. Batch resumption applies to the CTX collection.

The API client retries declared transient HTTP statuses and transport failures within a bounded per-invocation attempt budget. It paces request starts and respects `Retry-After` values within its wait budget. A longer provider delay stops the run for later resumption. Authentication errors and schema failures do not receive transient retries. Authenticated redirects are not followed.

The key is read from `CTX_API_KEY` and sent only as a request header to the fixed HTTPS endpoint. It is excluded from contracts, receipts, stdout and events. Provider error bodies are not logged. A success body containing the literal supplied credential is refused before archival. Response sizes and timeouts are bounded.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Acquisition completed with full identifier mapping coverage, or incomplete coverage was explicitly allowed |
| `1` | Configuration, network, authentication, schema, integrity or contract failure; inspect logs and retained captures |
| `2` | Acquisition completed, but some requested IDs were absent, unmatched or conflicted |

`--allow-incomplete` changes the exit result for mapping coverage only. It does not suppress network, schema or integrity failures and does not accept excluded chemistry.

## Intended engineering boundary

The collector has a CLI, deterministic adapter behavior, versioned contracts, unit tests and offline replay. It can be wrapped by a container job without changing its chemical semantics. The next integration test is a five-ID authenticated CTX acquisition. Containerization, scheduling, object-store upload and source-release discovery are subsequent work, not advertised as implemented here.

References:

- https://www.epa.gov/comptox-tools/computational-toxicology-and-exposure-apis-about
- https://comptox.epa.gov/ctx-api/docs/chemical.html
- https://comptox.epa.gov/ctx-api/docs/chemical.json
- https://github.com/USEPA/ctx-python
