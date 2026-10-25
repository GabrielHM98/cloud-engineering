# Step 3 — import the complete structure exports

The two supplied exports cover the full list. The newer file contains all 15,336 requested DTXSIDs, including the 10,000 already in the earlier file. It is not merely a 5,336-record second batch. Both originals are included unchanged in `example-batch-input/`.

The joint importer reconciles all records by requested DTXSID before joining CPDat annotations. It does not add independent reports together.

## Actual results

| Measure | Count |
|---|---:|
| Input CSV records across both exports | 25,336 |
| Distinct requested IDs covered | 15,336 / 15,336 |
| Repeated requested-ID records | 10,000 |
| Conflicting mappings between export records | 0 |
| Structurally eligible substances before key cross-check | 8,443 |
| Accepted substances after key cross-check | 8,335 |
| Accepted unique canonical molecular structures | 8,335 |
| Unique accepted structure–role pairs | 14,675 |
| Supporting CPDat annotation records | 99,861 |

Of the accepted substances, 6,059 source InChIKeys match exactly and 2,276 pass the explicit standard/nonstandard flag-only exception. The remaining 108 structurally eligible substances have identifier hash conflicts and stay in review. These are source-key disagreements, distinct from disagreements between the two exports.

Excluded substances: 4,392 missing SMILES; 1,805 multiple components; 335 unsupported elements; 219 extended notations; 127 without carbon; 108 identifier hash conflicts; 9 invalid SMILES; 3 wildcard/query records; 3 radicals. These exclusive counts plus 8,335 accepted substances reconcile to 15,336. They describe this policy and source snapshot, not universal chemical validity.

The included `reports/structure_full/` and `data/processed/structure_full/` contain the actual combined results. No model has been trained.

## Use this package in WSL

The ZIP contains an updated `industrial-use-starter/` folder. It includes the code, tests, both original exports, the 100-ID example, and the actual audit outputs. It excludes the large CPDat source archive and any virtual environment. You can inspect it in a separate directory or merge its contents into your existing project while retaining your cached archive and `.venv`.

In your existing project, with the updated files present:

```bash
cd ~/src/industrial-use-starter
source .venv/bin/activate
python -m unittest discover -s tests -v
```

No new chemistry dependencies are needed beyond `requirements-structures.txt` from step 2. The pinned dependencies were tested with Python 3.12 on Linux x86_64; use that interpreter when creating a fresh environment. All calculations in this step run on the CPU.

If the pinned archive or ID lists are absent, recreate them with the existing annotation audit:

```bash
python audit_cpdat.py
```

Reproduce the combined audit with the original filenames bundled in this package:

```bash
python import_structure_batches.py \
  --csv example-batch-input/CCD-Batch-Search_2026-10-18_07_37_43.csv \
        example-batch-input/CCD-Batch-Search_2026-10-18_07_41_58.csv \
  --require-complete

cat reports/structure_full/structure_audit.md
```

Or use your own raw paths, quoting paths that contain spaces. `--csv` accepts one or more exports. The newer 15,336-record file alone is sufficient for complete coverage; supplying both also checks their overlap. The joint example's raw record counts and file manifest reflect both inputs.

The default requested-ID list is `data/interim/dtxsids_all.txt`. This command does not default to the 100-ID sample. Outputs in `structure_full` are replaced when rerunning; original source files remain unchanged. The sample output namespace is separate.

## What happens inside the importer

1. Verify the pinned CPDat archive's bytes and checksums, the expected-ID file, structure policy and RDKit version.
2. Hash each export. Supplying the same bytes twice is an error, even under different filenames.
3. Validate each CSV record with the same chemistry rules as step 2. Keep original identifiers, SMILES, InChIKey, CASRN, lookup method, QC metadata, source filename, file hash and CSV record number.
4. Reconcile all records for each requested DTXSID. Compatible accepted mappings must have the same molecular identity. Exact and permitted flag-only key checks can coexist. Changed identities, changed unresolved mappings, or accepted/unresolved disagreements go to `conflicting_export_records`; neither record silently wins.
5. Count absent export records separately as pending exports. A returned ID with no SMILES is an observed missing structure, not a pending export.
6. Join retained CPDat annotation records once per accepted DTXSID. Duplicate export records do not duplicate the evidence join.
7. Deduplicate molecules globally by the pinned identity policy, then deduplicate molecule–role pairs. Preserve the original supporting annotation records separately.
8. Write the decisions, role-specific coverage, report and reproducibility metadata.

The per-substance table selects one deterministic representative and lists all source filenames and identifier-check outcomes. All exported records, including conflicting ones and their QC notes, remain in `export_validation.csv`. Incompatible mappings never enter accepted molecule or role tables. A caught RDKit processing exception is a review record with the error text; malformed CSV/schema failures stop the import.

## Output files

| Path | Purpose |
|---|---|
| `data/processed/structure_full/export_validation.csv` | Every CSV record and its source-file provenance |
| `data/processed/structure_full/substance_validation.csv` | One decision per requested DTXSID |
| `data/processed/structure_full/review.csv` | Observed substances excluded or quarantined by the current policy |
| `data/processed/structure_full/pending_exports.csv` | Requested IDs absent from all exports; empty for this run |
| `data/processed/structure_full/accepted_molecules.csv` | One accepted canonical molecular identity per row |
| `data/processed/structure_full/accepted_role_pairs.csv` | Unique molecular identity–function pairs |
| `data/processed/structure_full/accepted_evidence.csv` | Supporting CPDat annotations and source references |
| `reports/structure_full/role_structure_coverage.csv` | Requested/exported/accepted counts and unique structures by role |
| `reports/structure_full/structure_audit.md` | Readable combined audit |
| `reports/structure_full/structure_audit.json` | Machine-readable metrics |
| `reports/structure_full/structure_manifest.json` | Input/code/policy hashes and environment versions |

Exit codes: **0** means execution succeeded; **1** means an input, schema, dependency or processing failure stopped the run; **2** means the report was generated but `--require-complete` found pending export IDs. The completeness flag is an export-coverage gate, not a training-quality gate. Review exclusions do not automatically fail it. Future training needs its own explicit quality criteria.

The manifest records validation time, not an invented export download date. Preserve independently known retrieval dates and the dashboard source URL with the raw files. The structure CSVs contain no independently verified retrieval timestamp. The pinned historical CPDat snapshot remains v4.0, article version 5.

## What to do next

We now have full-list structure coverage measurements. The next task is to check whether candidate functions have enough chemical diversity for a meaningful held-out evaluation.

| Candidate function | Accepted unique structures |
|---|---:|
| Fragrance | 2,987 |
| Biocide | 584 |
| Solvent | 444 |
| Antioxidant | 363 |
| Dye | 346 |
| Humectant | 338 |
| Surfactant (surface active agent) | 289 |
| Plasticizer | 222 |
| Emulsifier | 200 |

This is a candidate list for review, not a frozen model label set. Flavouring and nutrient has 3,072 structures but combines functions that need semantic review. Broad labels such as Pharmaceutical, and nonfunctional categories such as No specific technical function or Degradant/impurity, need separate treatment.

Count support by molecular families and source documents, inspect role overlap and source concentration, and define how acyclic molecules are grouped; a simple ring-scaffold rule can put many different acyclic chemicals into one empty scaffold. Use a reviewed family/similarity grouping to build partitions. Do not infer family diversity just from 8,335 unique structures.

Review exclusion patterns before deciding whether to expand the representation policy. For example, the current policy retains only 289 of 1,445 surfactant-annotated IDs and 200 of 1,081 emulsifier-annotated IDs. Do not assume retained single-component organic chemistry represents all industrial materials. Supporting salts or polymers requires a deliberate representation policy and new tests.

Then freeze an initial supported role set and family-aware partitions for the fingerprint similarity baseline and first classifiers. Keep unknown role annotations unknown; a held-out absence is not a confirmed negative. Define evaluation against recorded annotations and explain that limitation. No training, split creation or family-diversity analysis is included in this importer step.
