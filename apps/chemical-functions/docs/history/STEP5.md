# Step 5 — the first trained chemical-function benchmark

Your step-4 run completed successfully. This update freezes the first annotation target and partitions, compares baseline models, refits the validation winner, evaluates it on held-out structures and provides a working prediction command with supporting chemical references.

It is CPU-only. The complete training/validation/final-test run took about 23 seconds on the machine used to prepare this update; installation and dataset preparation are additional, and your timing will vary.

## Run in your WSL project

Extract this overlay into `~/src`, merging its `industrial-use-starter/` folder with the existing project. It adds `benchmark.py`, `benchmark_policy.json`, `requirements-benchmark.txt`, `run_benchmark.sh`, `tests/test_benchmark.py`, this document and separate example outputs. It does not contain a pretrained model or overwrite earlier raw data, audits, dependency pins or virtual environments.

```bash
cd ~/src/industrial-use-starter
source .venv/bin/activate
python -m pip install -r requirements-benchmark.txt
bash run_benchmark.sh
```

The new requirements include the existing structure pins unchanged, plus pinned scikit-learn, SciPy, joblib, threadpoolctl and their resolved supporting packages. These were installed and tested together on Python 3.12 / Linux x86_64. `pip check` found no broken requirements. The complete suite now has **44 passing tests**.

The shell command runs these three stages in order and stops on failure:

```bash
python -m unittest discover -s tests -v
python benchmark.py prepare
python benchmark.py train
```

Read your own generated result:

```bash
cat runs/benchmark-v1/benchmark_report.md
```

Then use the trained artifact:

```bash
python benchmark.py predict --smiles 'CCO' --top-k 3
```

This returns ranked functions, numeric annotation-ranking scores, and up to three nearby reference chemicals per function with supporting CPDat source references. It also reports exact reference matches. Ethanol is a demonstration query, not a claim of discovery or unseen-molecule performance.

To save the JSON:

```bash
python benchmark.py predict --smiles 'CCO' --top-k 3 > prediction.json
```

The parser accepts the same single-component organic graph scope as the data pipeline. Query structures do not need a CompTox identifier. Their graph is checked, but no independent substance-to-structure identity cross-check is claimed. Salts, mixtures and other out-of-scope structures fail with an explicit reason.

## What exactly is the learning target?

The first benchmark ranks these nine recorded functions: Fragrance, Biocide, Solvent, Antioxidant, Dye, Humectant, Surfactant (surface active agent), Plasticizer and Emulsifier.

The target matrix has one row per molecular identity and one column per function:

- **1:** the retained CPDat evidence contains that molecule-function annotation.
- **0:** no retained annotation for that molecule-function pair is recorded.

Zero does **not** mean the molecule cannot perform that function. We train a supervised proxy for annotation patterns, not a model with verified experimental negatives. All 8,335 accepted molecules are included, including those with no annotations for any of these nine functions. Other roles remain in the original evidence tables; they are not silently mapped into these labels.

This makes the first benchmark concrete and reproducible without inventing negatives. It also limits the claim: it measures recovery of recorded annotations under this data-collection process. It is not a calibrated positive-unlabelled estimator of true industrial-use probabilities, and even a recorded use is not experimental validation of performance or suitability.

The fingerprints are fixed representations, not learned embeddings. A future version can introduce a more explicit missing-label model or stronger labels without rewriting the earlier source audit.

## Frozen partitions and leakage checks

The protocol uses the step-4 connected components at Morgan/Tanimoto threshold 0.7. A hash of protocol ID, seed 42 and group ID assigns each entire group to a partition. Targets are nominally 70% train / 15% validation / 15% test. Group sizes mean molecule fractions differ from those targets.

| Partition | Molecules | Similarity groups |
|---|---:|---:|
| Train | 6,096 | 4,154 |
| Validation | 1,033 | 839 |
| Test | 1,206 | 880 |

Assignments do not use the function annotations, model scores or input row order. The first fixed assignment was retained. Support checks require at least 10 annotated examples and five positive similarity groups for every function in every partition. The smallest held-out annotation count is 18 for Emulsifier, so its metric is preliminary and sensitive to a small sample.

All cross-partition fingerprint pairs were checked directly. The maximum similarity was approximately **0.69697**, and no pair across any two partitions reached 0.7.

This checks the configured fingerprint boundary, not universal chemical dissimilarity or experimental independence. Ring scaffolds and source catalogs can span partitions. This is not a source-holdout benchmark. The grouping used the full set of unlabeled structures; no target-derived scaling, feature selection or embedding was fitted on held-out examples.

`prepare` verifies hashes for the preceding audit's inputs and outputs, aligns fingerprints and annotations by molecular identity, checks label support and writes the frozen dataset contract. Different source data, code, policy or dependency versions cannot silently replace an existing prepared dataset.

## Model comparison

Seven fixed candidates represent four approaches:

| Approach | What it learns or computes |
|---|---|
| Prevalence reference | Constant score equal to each function's training annotation frequency |
| Nearest annotated reference | Maximum Tanimoto similarity to a training molecule annotated with each function |
| Logistic regression | Nine binary linear classifiers, with C = 0.1, 1 or 10 and balanced class weights |
| Random forest | Nine binary forests, 150 trees each; compare minimum leaf sizes 1 and 3, with balanced class weights |

All candidates train only on the training partition. Selection uses **validation macro average precision**. Every function has equal weight in that macro average, so Fragrance does not dominate simply because it has more annotations. Ties break deterministically by candidate ID.

The winning configuration is then fitted again on train + validation data. Only that selected, refitted model is evaluated on test data. The model and its explanation catalog contain train/validation chemistry only; held-out test molecules are excluded from the reference catalog.

## Actual benchmark result

| Candidate | Validation macro annotation AP |
|---|---:|
| Constant prevalence | 0.0813 |
| Nearest annotated reference | 0.3236 |
| Logistic regression, C = 0.1 | 0.4407 |
| Logistic regression, C = 1 | 0.4133 |
| Logistic regression, C = 10 | 0.3656 |
| Random forest, minimum leaf 1 | 0.4709 |
| Random forest, minimum leaf 3 | **0.4785** |

The selected forest achieved **0.4782 test macro observed-annotation average precision**. The constant-score test reference averages about **0.0732** across functions. Validation and test scores refer to different fitted estimators because the winner was refitted before test evaluation.

| Function | Test annotated molecules | Test annotation AP |
|---|---:|---:|
| Fragrance | 407 | 0.7496 |
| Biocide | 96 | 0.6266 |
| Solvent | 51 | 0.3950 |
| Antioxidant | 60 | 0.4266 |
| Dye | 44 | 0.7261 |
| Humectant | 52 | 0.2485 |
| Surfactant (surface active agent) | 31 | 0.6605 |
| Plasticizer | 35 | 0.2982 |
| Emulsifier | 18 | 0.1728 |

**Average precision is not accuracy.** It summarizes how the model ranks annotated examples above unannotated examples across thresholds. A chemically useful but unannotated example can count against it. Source concentration, incomplete annotation and selection into CPDat remain limitations. A single split does not establish a stable generalization estimate.

The recall-at-three diagnostic considers queries with at least one selected annotation and reports how many of their recorded functions appear in the top three. Queries without selected annotations are excluded from that denominator and counted separately. There is no implication that unreturned or unannotated functions are impossible.

## Generated files

| File | Purpose |
|---|---|
| `data/processed/benchmark-v1/dataset.npz` | Keyed fingerprints, annotation targets, similarity groups, partitions and label order |
| `data/processed/benchmark-v1/partitions.csv` | Auditable molecule-to-partition assignments |
| `data/processed/benchmark-v1/annotation_targets.csv` | Recorded-annotation target matrix |
| `data/processed/benchmark-v1/label_support.csv` | Per-function counts and positive groups in each partition |
| `data/processed/benchmark-v1/cross_split_similarity.csv` | Direct checks of the fingerprint boundary |
| `data/processed/benchmark-v1/dataset_manifest.json` | Frozen input/policy/code/environment contract and output hashes |
| `runs/benchmark-v1/validation_results.json` | All candidate validation metrics |
| `runs/benchmark-v1/selection.json` | Winner chosen on validation data |
| `runs/benchmark-v1/model_bundle.joblib` | Selected refitted estimators, feature policies and reference catalog |
| `runs/benchmark-v1/test_predictions.csv` | Held-out ranking scores and recorded targets |
| `runs/benchmark-v1/test_results.json` | Final selected-model metrics |
| `runs/benchmark-v1/benchmark_report.md` | Readable results and limits |
| `runs/benchmark-v1/run_manifest.json` | Completed-run provenance and artifact hashes |

`example-benchmark-output/` contains actual split summaries, model-comparison results, the held-out report and example predictions from this implementation. It does not substitute for running the commands locally, and the package does not contain a fitted model binary or duplicate the training feature matrix. Input manifests in your run will differ in timestamps and possibly local provenance while the fixed data and partitions remain reproducible.

Completed dataset/run directories are guarded. Re-running `prepare` reuses an identical frozen contract; re-running `train` displays the existing result after checking hashes. It does not silently refit and retest. A failed partial run is left for inspection. For a deliberately different experiment, use versioned `--dataset-dir` and `--run-dir` paths and an explicit protocol. Do not reroll seeds or select model changes based on repeated inspection of this test set.

Keep generated `runs/` artifacts out of ordinary source commits; store them through an artifact registry when the pipeline is containerized. The earlier source archives and evidence remain unchanged.

## What comes after this?

You now have a full local path from source data to an evaluated artifact to an inference command. The next infrastructure step can package these existing boundaries: an offline data/training image and a runtime image exposing the prediction operation. A catalog service can later serve the evidence separately. CI can run the tests, prepare a versioned dataset, execute the benchmark and publish hashed artifacts; deployment can consume the chosen model version through your separate Ansible/GitOps workflow.

Before any model promotion, define the acceptance criteria and how uncertainty, source bias and incomplete labels will be presented. This prototype does not automatically publish or deploy a model based on one AP score.

## Primary references

- [scikit-learn average precision](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html)
- [Logistic regression](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html)
- [Random forest classifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestClassifier.html)
