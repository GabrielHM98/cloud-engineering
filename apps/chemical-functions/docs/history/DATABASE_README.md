# Your chemical catalog

The database is already populated. You do not need an EPA API key, another download, or another training run to inspect it.

This package adds a local SQLite database to your existing project. SQLite stores tables in one file: `data/catalog/chemicals.sqlite3`. There is no database server to start. The inspection commands use Python's standard library; they do not require new pip packages.

## What you have been doing

Your product question is: **given a molecular structure, which industrial functions are worth investigating?**

You collected CPDat annotations about recorded chemical functions. Those annotations identify substances, so you exported their structures from the EPA dashboard. You checked whether the structures fit the project's chemistry policy, reconciled identifiers, kept the original evidence, and removed repeated structure–function pairs. You then compared models using chemical-family-aware partitions and saved a model for nine selected functions.

This database step puts the curated catalog into tables that application code can query. It does not redo the chemistry checks or retrain the model.

| Part | Its job | Example |
|---|---|---|
| Source files | Preserve what was collected | Original EPA CSV export and CPDat archive |
| Preparation scripts | Check identities and organize annotations | Accept ethanol as `CCO`; retain reasons for excluded substances |
| Database | Retrieve recorded annotations and their sources | Look up ethanol's recorded solvent use |
| Trained model | Score candidate functions from a structure | Score the nine selected functions for a supplied SMILES |
| Future app | Present catalog results and model suggestions separately | Search a chemical, inspect sources, then request predictions |

A database lookup and a model prediction answer different questions. The database asks, "What does this source snapshot record?" The model asks, "Which of the learned annotations does this structure resemble?" Neither establishes application performance.

## Open the populated database

Extract this ZIP into your existing project so `catalog_db.py` sits beside your other Python scripts and the database lands under `data/catalog/`. If your file manager creates an extra `industrial-use-starter` folder, copy its contents into your project.

In WSL:

```bash
cd ~/src/industrial-use-starter
python3 catalog_db.py summary
python3 catalog_db.py find ethanol
python3 catalog_db.py show DTXSID9020584 > ethanol_catalog.json
```

`summary` checks the database and reports its contents. `find` searches names or exact identifiers, CAS numbers, and stored canonical SMILES. It is a text/exact lookup, not a chemical-similarity search; equivalent alternative SMILES are not normalized by this command. `show` returns a substance's structure, recorded functions, and up to two source examples per function. The redirection saves that output to a file you can read in your editor.

To see the twenty best-supported vocabulary categories:

```bash
python3 catalog_db.py functions
```

These commands open the catalog read-only. Your model bundle remains separate at `runs/benchmark-v1/model_bundle.joblib` in your existing project; it is not needed for catalog lookups and is not included in this database package.

## What the tables contain

| Table | Meaning | Rows in this snapshot |
|---|---|---:|
| `molecule` | Accepted unique molecular structures under the pinned chemistry policy | 8,335 |
| `substance` | All reviewed substance IDs, with representative export metadata and validation status | 15,336 |
| `function_category` | CPDat vocabulary names and definitions | 130 |
| `recorded_use` | One accepted structure–function pair per row | 14,675 |
| `use_evidence` | Original supporting annotation records, including source/document references | 99,861 |
| `catalog_metadata` | Source versions, input hashes, chemistry policy, and import details | Metadata |

One molecule can have several recorded functions. Each structure–function pair can have many supporting annotation records. Those records remain separate so you can trace a catalog entry back to its sources; they are not separate training molecules.

The catalog has accepted annotations for 96 vocabulary categories. Some categories are broad or nonfunctional and remain useful source context rather than model targets. Your current model predicts only nine selected functions. That is why the database can display more recorded categories than the model can score.

The 7,001 substance IDs without accepted structures are retained with their validation status and a null accepted-molecule link. Their full original files and detailed per-export checks remain in the existing project. This database does not claim to contain usable molecular structures for those IDs, or their complete excluded annotation histories.

## How this fits into the app

For a known chemical, the app will query this catalog and display recorded uses with source references. For a structure to investigate, the prediction service will load the saved model and return function scores. A result page can show both, with clear labels for **recorded annotations** and **model suggestions**.

The database is the app's data layer. It does not contain the model weights and it does not perform predictions by itself. No web service or server deployment is included in this step.

For now, manual collection is an input method: keep each original export, run the existing preparation/audit workflow, then build a new catalog snapshot. Collection automation can be added later without changing that division of responsibilities. This local database gives us something concrete to inspect before adding an API, containers, or cluster deployment. PostgreSQL remains an option for the deployed system.

## Rebuild only when needed

The included database is ready to query. To reproduce it from your current curated CSV files and source metadata:

```bash
python3 catalog_db.py build
```

An unchanged import verifies and reuses the existing database. Different input files, schema, or loader code require a new filename; the command refuses to replace the existing snapshot:

```bash
python3 catalog_db.py build --database data/catalog/chemicals-v2.sqlite3
python3 catalog_db.py summary --database data/catalog/chemicals-v2.sqlite3
```

Complete export coverage, source-audit counts, vocabulary consistency, database integrity, foreign keys, and evidence totals are checked before a new database is published. Future data collection still needs to pass the upstream audit before this import step. Do not edit accepted structures or recorded uses directly in the database; update the source/preparation workflow and produce a new snapshot.

## Scope and provenance

This is the project's pinned historical **CPDat v4.0, Figshare article version 5** dataset, joined to the collected dashboard exports. It is not a claim about the latest CPDat release. Source information and original input hashes are stored inside the catalog metadata. Original archives, exports, and audit files are unchanged.

The chemistry policy accepts single connected carbon-containing graphs using H, C, N, O, F, P, S, Cl, Br, and I. Supplied charges, isotopes, and stereochemistry are preserved; counterions are not stripped and tautomers are not merged. Exclusions are scope decisions, not blanket declarations that a chemical is invalid.

Recorded functions are source annotations, not experimentally verified suitability. Missing annotations remain unknown. Counts of evidence records do not measure the strength of independent experimental confirmation. Source URLs are retained as collected and have not been checked for current availability by this import.

## Files added by this package

- `data/catalog/chemicals.sqlite3`: populated local database.
- `catalog_schema.sql`: table definitions, relationships, and indexes.
- `catalog_db.py`: importer and read-only inspection commands.
- `tests/test_catalog_db.py`: database relationship and inspection tests.
- `DATABASE_README.md`: this guide.

To run only the database tests:

```bash
python3 -m unittest discover -s tests -p test_catalog_db.py -v
```
