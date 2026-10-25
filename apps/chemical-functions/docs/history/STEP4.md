# Step 4 — chemical diversity before the first model

The full structure import is complete: 15,336 requested substances, 8,335 accepted unique structures and 14,675 distinct structure-function pairs. The next question is whether a proposed function is supported by varied chemistry and evidence, rather than many near-duplicates or one dominant catalog.

This step adds a CPU-only audit and a reusable molecular fingerprint cache. It uses the existing Python 3.12 environment and dependency pins. It does not train, partition the data, select features from labels or freeze the model label set.

## Install this update

This ZIP is a small overlay for the existing project, not a replacement for the earlier complete package. Place these files in `~/src/industrial-use-starter`:

- `audit_diversity.py`
- `diversity_policy.json`
- `STEP4.md`
- `tests/test_diversity.py`

The archive preserves the `industrial-use-starter/` directory structure. Extracting it into `~/src` adds those files and an `example-diversity-output/` folder. It does not contain or overwrite the existing structure CSVs, earlier validators, virtual environment or source archive. The example folder contains actual outputs from the supplied data; it is separate from the outputs your own run will generate.

Run in WSL:

```bash
cd ~/src/industrial-use-starter
source .venv/bin/activate
python -m unittest discover -s tests -v
python audit_diversity.py
cat reports/diversity/diversity_audit.md
```

All 32 tests passed when this update was prepared. No new dependencies, accounts or downloads are needed if step 3's tables, expected-ID list and cached CPDat archive are present. The script verifies the cached archive rather than extracting the large composition table. It prints progress while comparing fingerprints. Re-running replaces only the diversity output namespace; it leaves inputs unchanged.

Custom locations are supported with `--project-dir`, `--archive` and `--output-dir`. Input tables must have complete export coverage and the same structure policy and RDKit engine as the full-list import. Missing columns, inconsistent identities, duplicate molecule-role pairs and unsupported policy changes stop the audit. Output counts, keys, annotation references and accepted-substance mappings are checked before reporting.

## What is being computed?

### Molecular fingerprints

A molecule becomes a vector of 2,048 binary entries. RDKit's Morgan generator hashes local graph environments up to radius two, with atom/bond information, ring membership and supplied chirality included. A set bit signals a hashed environment. This is a fixed chemical representation, not a learned neural embedding.

Different environments can hash onto the same bit. Different structures can therefore have identical fingerprints; the original canonical structure and molecular identity remain separate. A fingerprint similarity of 1 is not proof of chemical identity. This audit does not erase stereochemistry, neutralize molecules or change the structure-acceptance policy.

### Similarity

For sets A and B of active fingerprint bits, Tanimoto similarity is:

    T(A, B) = |A intersection B| / |A union B|

Equivalently, for binary vectors a and b:

    T(a, b) = (a dot b) / (sum(a) + sum(b) - a dot b)

All 34,731,945 unordered pairs of the 8,335 molecules are compared. The implementation retains only one comparison row at a time, not a full square distance matrix. The cached features occupy 8,335 x 2,048 binary entries, stored as an unsigned-byte array in a compressed NPZ file.

### Similarity groups

At each threshold 0.6, 0.7 and 0.8, create an edge between molecules with similarity at or above that threshold. A group is a connected component of that graph. Group IDs are hashes of the versioned policy, threshold and sorted member identities; they are deterministic for the same inputs.

An intermediate molecule can connect two endpoints that do not themselves meet the threshold. This chaining behavior is deliberate and visible in the sensitivity report. Groups are similarity proxies, not proven independent chemical families. They are built over all accepted structures without labels; role-specific support then counts how many molecules of each role appear in those groups.

Threshold 0.7 is the primary **exploratory** setting. It is not claimed to be an empirically validated boundary. Before creating partitions, choose what generalization question matters and freeze the corresponding grouping protocol. Holding whole components in one partition would prevent pairs crossing that partition boundary from meeting the chosen fingerprint threshold under this exact representation. It would not establish experimental independence, remove every kind of chemical similarity or guarantee generalization.

### Ring scaffolds

RDKit Murcko scaffolds retain a molecule's ring framework and connecting linkers. This audit compares scaffold SMILES without stereochemistry as a separate diagnostic. It does not transform the accepted molecular identity.

Acyclic structures have no ring scaffold. They are recorded as `acyclic_not_applicable`, excluded from counts of nonempty scaffolds and still included in fingerprint grouping. They are not merged into one empty-scaffold family. Ring-scaffold groups and fingerprint components are different views and can disagree.

### Concentration and role overlap

For each function, the audit reports molecule count, similarity-component count, largest component share, and:

    effective component count = 1 / sum(p_g^2)

Here p_g is the fraction of that function's molecules in component g. Equal-sized groups increase this count; one dominant group reduces it. This describes grouping concentration, not an estimate of the number of independent experiments.

Source coverage counts unique molecules with the role and a named source, not repeated source rows. Document coverage likewise counts unique molecules per document ID. Multiple sources can annotate the same molecule, so source coverage fractions do not form a probability distribution or sum to one. Source names are compared literally. Separate documents and sources can still repeat underlying claims.

Role overlap counts molecules annotated with both roles. Its Jaccard value is intersection size divided by union size. The directional fractions describe recorded co-annotations, not probabilities of true industrial performance. Missing annotations remain unknown.

## Findings on the supplied data

There are 3,302 acyclic molecules and 1,366 distinct nonempty ring scaffolds. The median similarity to the nearest other molecule in the complete accepted set is about 0.645.

| Tanimoto threshold | Components | Largest component |
|---|---:|---:|
| 0.6 | 3,947 | 2,212 |
| 0.7 | 5,873 | 545 |
| 0.8 | 6,874 | 184 |

This sensitivity is a reason to document the grouping rule before model comparison. A stricter similarity threshold produces smaller, more numerous components, allowing less-similar relatives to fall into different groups.

At the primary threshold:

| Proposed function for review | Structures | Components containing that role |
|---|---:|---:|
| Fragrance | 2,987 | 2,222 |
| Biocide | 584 | 537 |
| Solvent | 444 | 301 |
| Antioxidant | 363 | 307 |
| Dye | 346 | 296 |
| Humectant | 338 | 255 |
| Surfactant (surface active agent) | 289 | 128 |
| Plasticizer | 222 | 108 |
| Emulsifier | 200 | 87 |

Counts across roles cannot be added as independent groups: molecules can have several roles and groups can contain molecules of several functions.

Source concentration needs particular review for fragrance: IFRA covers 83.6% of accepted fragrance molecules, and that same coverage appears under one document ID. Emulsifiers have 84% coverage from CosIng, although spread across documents. Those are properties of the annotation corpus, not evidence that the functions are wrong; they limit what an evaluation can establish about independent sources.

The overlap report also finds 112 accepted molecules annotated as both emulsifier and surfactant, representing 56% of emulsifiers. This supports keeping the task multilabel and reviewing function definitions, rather than forcing mutually exclusive classes.

## Outputs

| File under project root | Purpose |
|---|---|
| `data/processed/diversity/molecule_groups.csv` | Molecular identity, canonical SMILES, ring-scaffold status, similarity group at each threshold, nearest-other similarity |
| `data/processed/diversity/morgan_fingerprints.npz` | Keyed feature matrix for a later baseline |
| `reports/diversity/role_summary.csv` | Per-role structural and source concentration diagnostics |
| `reports/diversity/role_source_coverage.csv` | Unique molecules covered by each named source and role |
| `reports/diversity/role_document_coverage.csv` | Unique molecules covered by each document ID and role |
| `reports/diversity/role_overlap.csv` | Nonzero role co-annotations, Jaccard and directional fractions |
| `reports/diversity/role_exclusion_counts.csv` | Per-role structure-acceptance and exclusion decisions, including rejected substances |
| `reports/diversity/similarity_sensitivity.csv` | Global group sizes at each threshold |
| `reports/diversity/label_review.json` | Proposed functions and diagnostics; explicitly not a frozen training label set |
| `reports/diversity/diversity_audit.md` | Readable findings |
| `reports/diversity/diversity_audit.json` | Machine-readable audit metrics |
| `reports/diversity/diversity_manifest.json` | Input/output/code/policy hashes and environment versions |

Load the feature cache without pickled objects:

```python
import numpy as np

with np.load('data/processed/diversity/morgan_fingerprints.npz', allow_pickle=False) as cache:
    molecule_ids = cache['molecule_ids']
    X = cache['morgan_bits']
```

The molecule IDs align with feature-matrix rows. Join labels by ID, never by assuming another CSV happens to use the same order. The grouping and source diagnostics use the full corpus for feasibility analysis; do not tune model choices repeatedly against future held-out results. Feature selection, scaling, thresholds and hyperparameters later need training/validation discipline.

## Next checkpoint

Review the nine proposed functions, broad-category exclusions, concentration and representation limits. A practical first benchmark can start with a smaller subset and retain the remaining functions as expansion candidates. Then declare the prediction task as ranking *recorded functional annotations*, choose the grouping protocol, create partitions and audit per-role support in every partition.

Only after that should the project compare fingerprint similarity, linear classifiers and tree models. Unannotated molecule-role pairs must not be described as verified negatives. A model score should be presented as a suggestion grounded in this annotation corpus, not a calibrated probability of industrial suitability. The later pipeline must document its incomplete-label objective and evaluation assumptions.

## Primary references

- [Morgan fingerprint generator](https://www.rdkit.org/docs/source/rdkit.Chem.rdFingerprintGenerator.html)
- [Murcko scaffold implementation](https://www.rdkit.org/docs/source/rdkit.Chem.Scaffolds.MurckoScaffold.html)
- [RDKit similarity APIs](https://www.rdkit.org/docs/source/rdkit.DataStructs.cDataStructs.html)
