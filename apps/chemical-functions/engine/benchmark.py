#!/usr/bin/env python3
"""Prepare a frozen annotation benchmark, compare models, and rank a SMILES query."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time
import warnings

try:
    import joblib
    import numpy as np
    from scipy.sparse import csr_matrix
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score
    from threadpoolctl import threadpool_limits
except ImportError as exc:
    raise SystemExit("Install benchmark dependencies: python -m pip install -r requirements-benchmark.txt") from exc

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator
import audit_diversity as d
import validate_structures as v

PARTITIONS = ("train", "validation", "test")


def dependency_versions():
    versions = {}
    for filename in ["requirements-structures.txt", "requirements-benchmark.txt"]:
        for line in (v.ROOT / filename).read_text().splitlines():
            if not line or line.startswith(("#", "-r")):
                continue
            name, required = line.split("==")
            actual = importlib.metadata.version(name)
            if actual != required:
                raise ValueError(f"Expected {name}=={required}; found {actual}. Install requirements-benchmark.txt.")
            versions[name] = actual
    return versions


def check_policy(policy):
    if (policy.get("target") != "recorded_function_annotations_not_verified_industrial_suitability"
            or policy.get("unannotated_target_encoding") != "zero_means_not_recorded_not_confirmed_negative"
            or policy.get("split_rule") != "sha256_protocol_seed_group_first_64_bits"
            or policy.get("refit_winner_on") != "train_and_validation"
            or policy.get("selection_metric") != "validation_macro_average_precision_on_recorded_annotations"
            or policy.get("test_policy") != "evaluate_selected_refitted_winner_once_per_run_directory"):
        raise ValueError("Unsupported benchmark semantics; implement and version the protocol first.")
    labels = policy["labels"]
    if not labels or len(set(labels)) != len(labels):
        raise ValueError("Labels must be nonempty and unique.")
    if not 0 < policy["train_fraction"] < policy["train_fraction"] + policy["validation_fraction"] < 1:
        raise ValueError("Invalid partition fractions.")


def partition_for(group, policy):
    payload = f"{policy['protocol_id']}|{policy['split_seed']}|{group}"
    fraction = int(hashlib.sha256(payload.encode()).hexdigest()[:16], 16) / 2**64
    return ("train" if fraction < policy["train_fraction"] else
            "validation" if fraction < policy["train_fraction"] + policy["validation_fraction"] else "test")


def bit_vectors(X):
    return [DataStructs.CreateFromBinaryText(row.tobytes())
            for row in np.packbits(X, axis=1, bitorder="little")]


def cross_split_similarity(X, partitions, threshold):
    fps = bit_vectors(X)
    checks = []
    for i, left in enumerate(PARTITIONS):
        for right in PARTITIONS[i+1:]:
            a = np.flatnonzero(partitions == left)
            b = np.flatnonzero(partitions == right)
            if len(a) > len(b):
                a, b = b, a
            reference = [fps[j] for j in b]
            maximum, violations = 0.0, 0
            for j in a:
                scores = np.asarray(DataStructs.BulkTanimotoSimilarity(fps[j], reference))
                maximum = max(maximum, float(scores.max()))
                violations += int(np.count_nonzero(scores >= threshold))
            checks.append({"partition_a": left, "partition_b": right,
                           "max_tanimoto": maximum, "pairs_at_or_above_threshold": violations})
    if any(row["pairs_at_or_above_threshold"] for row in checks):
        raise ValueError("Cross-partition similarity violates the selected grouping threshold.")
    return checks


def label_support(A, ids, groups, partitions, labels, policy):
    output = []
    for split in PARTITIONS:
        mask = partitions == split
        if not mask.any():
            raise ValueError(f"Empty partition: {split}.")
        for j, role in enumerate(labels):
            positive = mask & (A[:, j] == 1)
            n = int(positive.sum())
            ng = len(set(groups[positive]))
            if (n < policy["min_observed_positives_per_label_per_split"]
                    or ng < policy["min_positive_groups_per_label_per_split"] or n == int(mask.sum())):
                raise ValueError(f"Insufficient observed-positive/group or unannotated support for {role} in {split}; review the protocol, not model test scores.")
            output.append({"partition": split, "role": role, "molecules": int(mask.sum()),
                           "observed_positive_molecules": n, "positive_similarity_groups": ng,
                           "unannotated_molecules": int(mask.sum())-n})
    return output


def prepare(root, output):
    versions = dependency_versions()
    policy = json.loads((v.ROOT / "benchmark_policy.json").read_text())
    check_policy(policy)
    manifest_path = root / "reports/diversity/diversity_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    # Verify both the audit's inputs and its generated groups/features before use.
    for relative, sha in {**manifest["input_sha256"], **manifest["output_sha256"]}.items():
        if v.digest(root / relative) != sha:
            raise ValueError(f"Diversity input/output changed since its audit: {relative}.")
    if manifest["rdkit_engine"] != v.rdBase.rdkitVersion:
        raise ValueError("RDKit engine differs from the diversity audit.")
    expected_diversity = json.loads((v.ROOT / "diversity_policy.json").read_text())
    expected_structure = json.loads((v.ROOT / "structure_policy.json").read_text())
    if (manifest["diversity_policy_sha256"] != v.policy_hash(expected_diversity)
            or manifest["structure_policy_sha256"] != v.policy_hash(expected_structure)):
        raise ValueError("Structure/diversity policy changed since auditing; regenerate upstream data.")
    group_path = root / "data/processed/diversity/molecule_groups.csv"
    cache_path = root / "data/processed/diversity/morgan_fingerprints.npz"
    pair_path = root / "data/processed/structure_full/accepted_role_pairs.csv"
    paths = [manifest_path, group_path, cache_path, pair_path,
             root / "data/processed/structure_full/accepted_molecules.csv",
             root / "data/processed/structure_full/accepted_evidence.csv"]
    contract = {"policy": policy, "inputs": {str(p.relative_to(root)): v.digest(p) for p in paths},
                "code_sha256": v.digest(Path(__file__)), "dependencies": versions}
    contract_sha = v.policy_hash(contract)
    frozen_path = output / "dataset_manifest.json"
    if frozen_path.exists():
        old = json.loads(frozen_path.read_text())
        if old["contract_sha256"] != contract_sha:
            raise ValueError("Frozen benchmark differs from current inputs/code/policy. Use a versioned --dataset-dir; do not overwrite the existing benchmark.")
        for name, sha in old["output_sha256"].items():
            if v.digest(output / name) != sha:
                raise ValueError("Frozen benchmark output was modified.")
        print("Frozen dataset already matches this contract; reusing it.")
        return old
    if output.exists() and any(output.iterdir()):
        raise ValueError("Dataset directory contains incomplete/unrecognized outputs; use a new --dataset-dir.")
    group_rows = d.read_csv(group_path, ["molecule_id", policy["group_column"]])
    byid = {r["molecule_id"]: r for r in group_rows}
    with np.load(cache_path, allow_pickle=False) as cache:
        ids, X = cache["molecule_ids"].copy(), cache["morgan_bits"].copy()
    if (len(byid) != len(group_rows) or len(set(ids)) != len(ids) or set(byid) != set(ids)
            or X.shape != (len(ids), expected_diversity["morgan"]["fpSize"])
            or X.dtype != np.uint8 or not np.all((X == 0) | (X == 1))):
        raise ValueError("Cached feature keys, shape or binary values are inconsistent.")
    groups = np.asarray([byid[mid][policy["group_column"]] for mid in ids])
    partitions = np.asarray([partition_for(group, policy) for group in groups])
    labels = policy["labels"]
    columns, index = {role: j for j, role in enumerate(labels)}, {mid: i for i, mid in enumerate(ids)}
    A = np.zeros((len(ids), len(labels)), dtype=np.uint8)
    pairs = d.read_csv(pair_path, ["molecule_id", "role"])
    for row in pairs:
        if row["role"] in columns:
            A[index[row["molecule_id"]], columns[row["role"]]] = 1
    support = label_support(A, ids, groups, partitions, labels, policy)
    threshold = float(policy["group_column"].split("_t")[-1])
    leakage = cross_split_similarity(X, partitions, threshold)
    split_summary = [{"partition": split, "molecules": int(np.count_nonzero(partitions == split)),
                      "similarity_groups": len(set(groups[partitions == split])),
                      "molecules_with_any_selected_annotation": int(np.count_nonzero(A[partitions == split].sum(axis=1)))}
                     for split in PARTITIONS]
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output / "dataset.npz", molecule_ids=ids, X=X, annotations=A,
                        groups=groups, partitions=partitions, labels=np.asarray(labels))
    d.write_table(output / "partitions.csv", [{"molecule_id": str(mid), "group_id": str(group), "partition": str(split)}
                  for mid, group, split in zip(ids, groups, partitions)])
    d.write_table(output / "annotation_targets.csv", [dict(molecule_id=str(mid), **{role: int(A[i, j]) for j, role in enumerate(labels)})
                  for i, mid in enumerate(ids)])
    d.write_table(output / "label_support.csv", support)
    d.write_table(output / "split_summary.csv", split_summary)
    d.write_table(output / "cross_split_similarity.csv", leakage)
    result = {"status": "frozen", "created_at_utc": v.utc_now(), "contract_sha256": contract_sha,
              "contract": contract, "feature_policy": expected_diversity["morgan"],
              "structure_policy": expected_structure, "split_summary": split_summary,
              "cross_split_checks": leakage, "test_scores_used_for_split_selection": False,
              "output_sha256": {p.name: v.digest(p) for p in sorted(output.glob("*")) if p.is_file()}}
    v.write_json(frozen_path, result)
    print(json.dumps({"split_summary": split_summary, "cross_split_checks": leakage}, indent=2))
    return result


def load_dataset(dataset_dir):
    manifest = json.loads((dataset_dir / "dataset_manifest.json").read_text())
    for name, sha in manifest["output_sha256"].items():
        if v.digest(dataset_dir / name) != sha:
            raise ValueError("Frozen benchmark output hash mismatch.")
    with np.load(dataset_dir / "dataset.npz", allow_pickle=False) as cache:
        arrays = {name: cache[name].copy() for name in cache.files}
    return manifest, arrays


def annotation_metrics(A, scores, labels):
    if A.shape != scores.shape or not np.isfinite(scores).all():
        raise ValueError("Prediction score shape or values are invalid.")
    per_role = []
    for j, role in enumerate(labels):
        if not 0 < A[:, j].sum() < len(A):
            raise ValueError("Every evaluated role requires observed-positive and unannotated examples.")
        per_role.append({"role": str(role), "observed_positives": int(A[:, j].sum()),
                         "observed_annotation_average_precision": float(average_precision_score(A[:, j], scores[:, j])),
                         "annotation_prevalence_baseline_ap": float(A[:, j].mean())})
    has_annotations = A.sum(axis=1) > 0
    top = np.argsort(-scores, axis=1, kind="stable")[:, :min(3, len(labels))]
    observed_found = np.take_along_axis(A, top, axis=1).sum(axis=1)
    return {"macro_observed_annotation_ap": float(np.mean([r["observed_annotation_average_precision"] for r in per_role])),
            "mean_observed_recall_at_3_on_annotated_queries": float(np.mean(observed_found[has_annotations] / A[has_annotations].sum(axis=1))),
            "annotated_queries_for_recall_at_3": int(has_annotations.sum()),
            "queries_with_no_selected_annotation": int((~has_annotations).sum()), "per_role": per_role}


def candidates(policy):
    return ([{"id": "prevalence", "kind": "prevalence"}, {"id": "nearest_positive", "kind": "nearest_positive"}]
            + [{"id": f"logistic_C{C:g}", "kind": "logistic", "C": C} for C in policy["logistic_C"]]
            + [{"id": f"forest_leaf{leaf}", "kind": "forest", "min_samples_leaf": leaf} for leaf in policy["forest_min_samples_leaf"]])


def fit_candidate(spec, X, A, policy):
    kind = spec["kind"]
    if kind == "prevalence":
        return {"kind": kind, "prevalence": A.mean(axis=0)}
    if kind == "nearest_positive":
        return {"kind": kind, "X_reference": X, "annotations_reference": A}
    models = []
    for j in range(A.shape[1]):
        if kind == "logistic":
            model = LogisticRegression(C=spec["C"], solver="liblinear", class_weight="balanced",
                                       max_iter=2000, random_state=policy["random_seed"])
        elif kind == "forest":
            model = RandomForestClassifier(n_estimators=policy["forest_estimators"],
                        min_samples_leaf=spec["min_samples_leaf"], max_features="sqrt", class_weight="balanced",
                        random_state=policy["random_seed"]+j, n_jobs=policy["cpu_threads"])
        else:
            raise ValueError(f"Unknown model family: {kind}.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(csr_matrix(X), A[:, j])
        models.append(model)
    return {"kind": kind, "models": models}


def score_candidate(model, X):
    if model["kind"] == "prevalence":
        return np.tile(model["prevalence"], (len(X), 1))
    if model["kind"] == "nearest_positive":
        reference = bit_vectors(model["X_reference"])
        positives = [np.flatnonzero(model["annotations_reference"][:, j]) for j in range(model["annotations_reference"].shape[1])]
        result = np.zeros((len(X), len(positives)))
        for i, fp in enumerate(bit_vectors(X)):
            sims = np.asarray(DataStructs.BulkTanimotoSimilarity(fp, reference))
            for j, indices in enumerate(positives):
                result[i, j] = float(sims[indices].max())
        return result
    return np.column_stack([m.predict_proba(csr_matrix(X))[:, list(m.classes_).index(1)] for m in model["models"]])


def select_winner(validation_results):
    return min(validation_results, key=lambda row: (-row["metrics"]["macro_observed_annotation_ap"], row["candidate"]["id"]))


def reference_catalog(root, reference_ids, labels):
    wanted = set(reference_ids)
    catalog = {}
    for row in d.read_csv(root / "data/processed/structure_full/accepted_molecules.csv", ["molecule_id", "canonical_smiles", "source_dtxsids"]):
        if row["molecule_id"] in wanted:
            catalog[row["molecule_id"]] = {"canonical_smiles": row["canonical_smiles"],
                                           "dtxsids": json.loads(row["source_dtxsids"]), "recorded_roles": {}}
    evidence = d.read_csv(root / "data/processed/structure_full/accepted_evidence.csv", ["molecule_id", "role", "source", "document_id", "document_url"])
    for row in sorted(evidence, key=lambda r: (r["source"], r["document_id"], r["dtxsid"])):
        identity, role = row["molecule_id"], row["role"]
        if identity in wanted and role in labels:
            refs = catalog[identity]["recorded_roles"].setdefault(role, [])
            ref = {key: row[key] for key in ["dtxsid", "source", "document_id", "document_url", "raw_functional_use"]}
            if len(refs) < 3 and ref not in refs:
                refs.append(ref)
    if set(catalog) != wanted:
        raise ValueError("Reference catalog does not cover the refit pool.")
    return catalog


def benchmark_report(result, validation_results):
    lines = ["# First annotation-ranking benchmark", "",
             "Task: rank recorded CPDat function annotations for the accepted single-component organic scope. A zero target means no recorded annotation, not a verified negative. Scores are not calibrated probabilities of real-world usefulness.", "",
             "## Validation model comparison", "", "All candidates were fitted on training data only; selection used validation macro average precision.", "",
             "| Candidate | Validation macro annotation AP |", "|---|---:|"]
    lines += [f"| {r['candidate']['id']} | {r['metrics']['macro_observed_annotation_ap']:.4f} |" for r in validation_results]
    lines += ["", f"Selected: **{result['selected_candidate']['id']}**. Refit on train + validation, then evaluated on the held-out test partition.", "",
              f"**Test macro observed-annotation AP: {result['test_metrics']['macro_observed_annotation_ap']:.4f}.**", "",
              "| Function | Test annotated examples | Test annotation AP | Constant-score AP reference |", "|---|---:|---:|---:|"]
    lines += [f"| {r['role']} | {r['observed_positives']} | {r['observed_annotation_average_precision']:.4f} | {r['annotation_prevalence_baseline_ap']:.4f} |" for r in result["test_metrics"]["per_role"]]
    lines += ["", "Average precision summarizes how well annotated examples rank above unannotated examples across score thresholds. It is not accuracy. Unannotated useful molecules may count against this metric; missing annotations and source sampling bias affect it. The macro average gives each of the nine functions equal weight.", "",
              "The constant-score AP reference equals each test role's annotation prevalence. The model was selected without comparing candidates on test scores. Its final refit uses train and validation data, so validation and test scores concern different fitted estimators and samples.", "",
              "## Scope and limits", "",
              "Partitions hold whole similarity components together. Explicit all-pairs checks confirm the configured fingerprint-similarity boundary across partitions; see the frozen dataset's cross_split_similarity.csv. This is a similarity-group holdout, not a source holdout or an experimental test of molecular performance. Sources and ring scaffolds can span partitions. The representation and acceptance policies still exclude salts, mixtures, polymers and other unsupported inputs.", "",
              "Role support is unequal, and some test roles have few annotated examples. One split gives a preliminary benchmark, not a stable scientific generalization estimate. No score calibration, true-negative validation or industrial experiments were performed. The annotation target encodes missing labels as zero for this limited supervised proxy; it is not a fully specified positive-unlabelled estimator of true-use probabilities.", "",
              "The artifact contains the selected model plus train/validation-only reference chemistry and truncated source references for CLI explanations. Held-out test molecules are excluded from that reference catalog. A known reference match is explicitly reported at prediction time and is not evidence of novel-molecule generalization.", "",
              f"Total benchmark wall time on this machine: {result['elapsed_seconds']:.1f} seconds. CPU only; timings depend on hardware.", "",
              "Completed run directories are immutable to this command: rerunning displays the existing result. A different experiment needs a versioned run and an explicit protocol; do not repeatedly select changes by looking at this test set.", ""]
    return "\n".join(lines)


def train(root, dataset_dir, run_dir):
    versions = dependency_versions()
    manifest, arrays = load_dataset(dataset_dir)
    policy = manifest["contract"]["policy"]
    check_policy(policy)
    if versions != manifest["contract"]["dependencies"] or v.digest(Path(__file__)) != manifest["contract"]["code_sha256"]:
        raise ValueError("Code/dependencies differ from the frozen dataset contract; prepare a versioned dataset.")
    if run_dir.exists():
        completed = run_dir / "run_manifest.json"
        if not completed.exists():
            raise ValueError("Run directory is incomplete or unrecognized. Inspect it and use a new --run-dir rather than silently repeating evaluation.")
        old = json.loads(completed.read_text())
        if old["dataset_contract_sha256"] != manifest["contract_sha256"]:
            raise ValueError("Run belongs to a different dataset contract.")
        for name, sha in old["output_sha256"].items():
            if v.digest(run_dir/name) != sha:
                raise ValueError("Completed run artifact hash mismatch.")
        print((run_dir / "benchmark_report.md").read_text())
        return json.loads((run_dir / "test_results.json").read_text())
    # Recheck catalog inputs before a refit artifact can include them.
    for relative, sha in manifest["contract"]["inputs"].items():
        if v.digest(root / relative) != sha:
            raise ValueError(f"Benchmark source changed: {relative}.")
    run_dir.mkdir(parents=True)
    v.write_json(run_dir / "RUNNING.json", {"started_at_utc": v.utc_now(), "dataset_contract_sha256": manifest["contract_sha256"]})
    started = time.perf_counter()
    X, A, splits, ids, labels = (arrays[name] for name in ["X", "annotations", "partitions", "molecule_ids", "labels"])
    train_mask, val_mask, test_mask = (splits == split for split in PARTITIONS)
    validation_results = []
    with threadpool_limits(limits=policy["cpu_threads"]):
        for spec in candidates(policy):
            print(f"Training/validating {spec['id']}...", flush=True)
            model = fit_candidate(spec, X[train_mask], A[train_mask], policy)
            scores = score_candidate(model, X[val_mask])
            metrics = annotation_metrics(A[val_mask], scores, labels)
            validation_results.append({"candidate": spec, "metrics": metrics})
            print(f"  validation macro annotation AP: {metrics['macro_observed_annotation_ap']:.4f}", flush=True)
        winner = select_winner(validation_results)
        # Save the selection before evaluating any test prediction.
        v.write_json(run_dir / "validation_results.json", validation_results)
        v.write_json(run_dir / "selection.json", {"selection_uses": "validation_only", "selected_candidate": winner["candidate"]})
        print(f"Refitting selected candidate: {winner['candidate']['id']}", flush=True)
        refit_mask = train_mask | val_mask
        final_model = fit_candidate(winner["candidate"], X[refit_mask], A[refit_mask], policy)
        catalog = reference_catalog(root, ids[refit_mask], set(labels))
        bundle = {"model": final_model, "selected_candidate": winner["candidate"], "labels": labels.tolist(),
                  "feature_policy": manifest["feature_policy"], "structure_policy": manifest["structure_policy"],
                  "reference_ids": ids[refit_mask], "reference_X": X[refit_mask],
                  "reference_annotations": A[refit_mask], "catalog": catalog,
                  "dataset_contract_sha256": manifest["contract_sha256"], "dependencies": versions}
        joblib.dump(bundle, run_dir / "model_bundle.joblib", compress=3)
        print("Evaluating the selected refitted model on the held-out test partition...", flush=True)
        test_scores = score_candidate(final_model, X[test_mask])
        test_metrics = annotation_metrics(A[test_mask], test_scores, labels)
    result = {"protocol_id": policy["protocol_id"], "selected_candidate": winner["candidate"],
              "selection_partition": "validation", "refit_partitions": ["train", "validation"],
              "test_molecules": int(test_mask.sum()), "test_metrics": test_metrics,
              "elapsed_seconds": time.perf_counter()-started,
              "target": policy["target"], "dataset_contract_sha256": manifest["contract_sha256"]}
    v.write_json(run_dir / "test_results.json", result)
    d.write_table(run_dir / "test_predictions.csv", [
        {"molecule_id": str(identity), "role": str(role), "recorded_annotation": int(A[test_mask][i, j]),
         "annotation_rank_score": float(test_scores[i, j])}
        for i, identity in enumerate(ids[test_mask]) for j, role in enumerate(labels)])
    (run_dir / "benchmark_report.md").write_text(benchmark_report(result, validation_results), encoding="utf-8")
    (run_dir / "RUNNING.json").unlink()
    v.write_json(run_dir / "run_manifest.json", {"completed_at_utc": v.utc_now(),
        "dataset_contract_sha256": manifest["contract_sha256"], "dependencies": versions,
        "code_sha256": v.digest(Path(__file__)), "test_evaluated_after_selection": True,
        "output_sha256": {p.name: v.digest(p) for p in sorted(run_dir.glob("*")) if p.is_file()}})
    print((run_dir / "benchmark_report.md").read_text())
    return result


def query_features(smiles, bundle):
    # A new user structure has no external source key. This checks graph scope,
    # not the substance-to-structure identity cross-check used by the data audit.
    assessment = v.assess_smiles(smiles.strip(), "", bundle["structure_policy"])
    if assessment["status"] != "missing_source_key":
        raise ValueError(f"Query is outside the supported graph policy: {assessment['status']}.")
    mol = Chem.MolFromSmiles(assessment["canonical_smiles"])
    fp = rdFingerprintGenerator.GetMorganGenerator(**bundle["feature_policy"]).GetFingerprint(mol)
    X = np.zeros((1, bundle["feature_policy"]["fpSize"]), dtype=np.uint8)
    DataStructs.ConvertToNumpyArray(fp, X[0])
    return assessment["canonical_smiles"], X, fp


def predict(run_dir, smiles, top_k):
    if top_k <= 0:
        raise ValueError("--top-k must be positive.")
    versions = dependency_versions()
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    model_path = run_dir / "model_bundle.joblib"
    if v.digest(model_path) != manifest["output_sha256"]["model_bundle.joblib"]:
        raise ValueError("Model artifact hash mismatch.")
    if versions != manifest["dependencies"]:
        raise ValueError("Prediction dependencies differ from the recorded model environment.")
    bundle = joblib.load(model_path)
    canonical, X, fp = query_features(smiles, bundle)
    scores = score_candidate(bundle["model"], X)[0]
    reference_ids = bundle["reference_ids"]
    similarities = np.asarray(DataStructs.BulkTanimotoSimilarity(fp, bit_vectors(bundle["reference_X"])))
    exact = [identity for identity in reference_ids if bundle["catalog"][identity]["canonical_smiles"] == canonical]
    ranking = []
    for j in np.argsort(-scores, kind="stable")[:min(top_k, len(scores))]:
        role = bundle["labels"][j]
        annotated = np.flatnonzero(bundle["reference_annotations"][:, j])
        neighbors = sorted(annotated, key=lambda i: (-similarities[i], str(reference_ids[i])))[:3]
        ranking.append({"role": role, "annotation_rank_score": float(scores[j]),
            "reference_chemicals": [dict(molecule_id=str(reference_ids[i]), tanimoto=float(similarities[i]),
                 canonical_smiles=bundle["catalog"][reference_ids[i]]["canonical_smiles"],
                 dtxsids=bundle["catalog"][reference_ids[i]]["dtxsids"],
                 evidence=bundle["catalog"][reference_ids[i]]["recorded_roles"][role]) for i in neighbors]})
    output = {"canonical_smiles": canonical, "source_identity_cross_check": "not_performed_for_user_query",
              "exact_reference_match": bool(exact),
              "recorded_selected_roles_for_exact_match": sorted({role for identity in exact for role in bundle["catalog"][identity]["recorded_roles"]}),
              "nearest_reference_tanimoto": float(similarities.max()), "selected_model": bundle["selected_candidate"]["id"],
              "score_meaning": "ranking of recorded annotations; not a calibrated probability of industrial usefulness",
              "evidence_scope": "train_and_validation_reference_chemicals_only; max_three_references_per_neighbor_role",
              "ranked_functions": ranking}
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["prepare", "train", "predict"]:
        command = sub.add_parser(name)
        command.add_argument("--project-dir", type=Path, default=v.ROOT)
        command.add_argument("--dataset-dir", type=Path, default=v.ROOT / "data/processed/benchmark-v1")
        command.add_argument("--run-dir", type=Path, default=v.ROOT / "runs/benchmark-v1")
        if name == "predict":
            command.add_argument("--smiles", required=True)
            command.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.project_dir.resolve(), args.dataset_dir.resolve())
        elif args.command == "train":
            train(args.project_dir.resolve(), args.dataset_dir.resolve(), args.run_dir.resolve())
        else:
            predict(args.run_dir.resolve(), args.smiles, args.top_k)
    except (OSError, ValueError, KeyError, RuntimeError, ConvergenceWarning) as exc:
        print(f"Benchmark stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
