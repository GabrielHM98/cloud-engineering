#!/usr/bin/env python3
"""Reconcile one or more CompTox exports against the complete CPDat ID list."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import zipfile

import validate_structures as v

ROW_FIELDS = v.ROW_FIELDS + ["source_csv", "source_csv_sha256"]
SUBSTANCE_FIELDS = ROW_FIELDS + ["export_record_count", "source_csvs", "identifier_checks"]


def reconcile(records, expected):
    """Resolve globally; an accepted identity never overrides a conflicting record."""
    grouped = defaultdict(list)
    wanted = set(expected)
    for row in records:
        if row["input_dtxsid"] in wanted:
            grouped[row["input_dtxsid"]].append(row)
    substances = []
    for sid in expected:
        candidates = grouped.get(sid, [])
        if not candidates:
            item = dict.fromkeys(ROW_FIELDS, "")
            item.update(input_dtxsid=sid, status="missing_export_record", structural_pass=False)
        else:
            # Prefer an exact cross-check when compatible accepted duplicates differ
            # only by the explicitly allowed S/N flag. Ties are file-order invariant.
            item = dict(min(candidates, key=lambda r: (
                r["identifier_check"] != "exact_match", r["source_csv_sha256"],
                r["source_csv"], r["csv_record_number"])))
            signatures = set()
            for row in candidates:
                if row["status"] == "accepted":
                    signature = ("accepted", row["returned_dtxsid"], row["molecule_id"])
                else:
                    signature = (row["status"], row["returned_dtxsid"],
                                 row["original_smiles"], row["source_inchikey"])
                signatures.add(signature)
            if len(signatures) != 1:
                item.update(status="conflicting_export_records", structural_pass=False,
                            molecule_id="", canonical_smiles="", computed_inchikey="",
                            identifier_check="conflicting_export_records")
        item.update(export_record_count=len(candidates),
                    source_csvs=json.dumps(sorted({r["source_csv"] for r in candidates})),
                    identifier_checks=json.dumps(sorted({r["identifier_check"] for r in candidates})))
        substances.append(item)
    return substances


def validate_batches(paths, expected, policy):
    inputs = []
    seen = set()
    for path in paths:
        sha = v.digest(path)
        if sha in seen:
            raise ValueError("The same export content was supplied more than once; remove duplicate file arguments.")
        seen.add(sha)
        inputs.append({"name": path.name, "sha256": sha, "size_bytes": path.stat().st_size, "path": path})
    records = []
    for source in sorted(inputs, key=lambda r: (r["sha256"], r["name"])):
        rows, _ = v.validate_export(source["path"], expected, policy)
        for row in rows:
            row.update(source_csv=source["name"], source_csv_sha256=source["sha256"])
        source["records"] = len(rows)
        records.extend(rows)
    return records, reconcile(records, expected), [
        {k: value for k, value in source.items() if k != "path"} for source in inputs]


def role_coverage(substances, labels, vocabulary):
    byid = {r["input_dtxsid"]: r for r in substances}
    roles = defaultdict(set)
    for sid, values in labels.items():
        for role in values:
            roles[role].add(sid)
    rows = []
    for role, ids in sorted(roles.items()):
        seen = {sid for sid in ids if byid[sid]["export_record_count"]}
        accepted = {sid for sid in ids if byid[sid]["status"] == "accepted"}
        rows.append({"role": role, "role_id": vocabulary[role],
                     "requested_substances": len(ids), "exported_substances": len(seen),
                     "pending_export_substances": len(ids - seen), "accepted_substances": len(accepted),
                     "accepted_unique_structures": len({byid[s]["molecule_id"] for s in accepted}),
                     "accepted_fraction_of_requested": f"{len(accepted)/len(ids):.6f}",
                     "accepted_fraction_of_exported": f"{len(accepted)/len(seen):.6f}" if seen else ""})
    return rows


def metrics_for(records, substances, molecules, pairs, evidence, labels):
    present = sum(r["export_record_count"] > 0 for r in substances)
    accepted = [r for r in substances if r["status"] == "accepted"]
    return {"scope": "complete_requested_id_list", "training_status": "not_trained",
            "export_coverage_complete": present == len(substances),
            "requested_ids": len(substances), "export_records": len(records),
            "requested_ids_present": present, "pending_export_ids": len(substances)-present,
            "unexpected_export_records": sum(r["status"] == "unexpected_input_id" for r in records),
            "duplicate_requested_id_records": sum(max(0, r["export_record_count"]-1) for r in substances),
            "ids_with_multiple_export_records": sum(r["export_record_count"] > 1 for r in substances),
            "conflicting_export_substances": sum(r["status"] == "conflicting_export_records" for r in substances),
            "structurally_eligible_substances": sum(bool(r["structural_pass"]) for r in substances),
            "accepted_substances": len(accepted), "accepted_unique_structures": len(molecules),
            "accepted_structure_role_pairs": len(pairs), "accepted_evidence_records": len(evidence),
            "substance_statuses": dict(sorted(Counter(r["status"] for r in substances).items())),
            "accepted_identifier_checks_representative": dict(sorted(Counter(r["identifier_check"] for r in accepted).items())),
            "requested_ids_without_retained_cpdat_labels": sorted({r["input_dtxsid"] for r in substances}-set(labels))}


def report_text(metrics, coverage, inputs):
    m = metrics
    lines = ["# Full-list structure audit", "",
             "Status: " + ("all requested IDs have export records." if m["export_coverage_complete"]
                           else "partial export coverage; missing export records remain pending."), "",
             "Complete export coverage does not mean every substance has a usable structure.", "",
             "| Measure | Count |", "|---|---:|"]
    for label, key in [("Requested substance IDs", "requested_ids"), ("Export CSV records", "export_records"),
                       ("Requested IDs present", "requested_ids_present"), ("Pending export IDs", "pending_export_ids"),
                       ("Repeated records for requested IDs", "duplicate_requested_id_records"),
                       ("Conflicting export mappings", "conflicting_export_substances"),
                       ("Structurally eligible substances before key cross-check", "structurally_eligible_substances"),
                       ("Accepted substances after key cross-check", "accepted_substances"),
                       ("Accepted unique molecular structures", "accepted_unique_structures"),
                       ("Unique accepted structure–role pairs", "accepted_structure_role_pairs"),
                       ("Supporting CPDat annotation records", "accepted_evidence_records")]:
        lines.append(f"| {label} | {m[key]:,} |")
    lines += ["", "## Inputs", "", "| Original file | CSV records | SHA-256 |", "|---|---:|---|"]
    lines += [f"| {r['name']} | {r['records']:,} | `{r['sha256']}` |" for r in inputs]
    lines += ["", "Overlapping exports are reconciled by DTXSID before joining CPDat evidence. Compatible repeated records count once. Conflicting records are quarantined. Molecular identities are deduplicated globally across IDs and files.", "",
              "## Substance decisions", "", "| Decision | Substances |", "|---|---:|"]
    lines += [f"| {key} | {count:,} |" for key, count in m["substance_statuses"].items()]
    lines += ["", "## Accepted identifier cross-checks", "", "| Representative check | Substances |", "|---|---:|"]
    lines += [f"| {key} | {count:,} |" for key, count in m["accepted_identifier_checks_representative"].items()]
    lines += ["", "When compatible duplicates differ only by the permitted InChIKey S/N flag, the per-substance representative prefers an exact match. The complete per-record checks and both source keys remain in export_validation.csv. The flag exception requires identical hash blocks, version and protonation indicator; all other key differences are quarantined.", "",
              "## Role coverage", "", "These counts describe annotated substances retained under this structure policy; they are not model scores. A structure may have several roles. Repeated supporting records are not independent molecules.", "",
              "| Role | Requested IDs | Exported IDs | Accepted IDs | Unique structures |", "|---|---:|---:|---:|---:|"]
    lines += [f"| {r['role']} | {r['requested_substances']} | {r['exported_substances']} | {r['accepted_substances']} | {r['accepted_unique_structures']} |"
              for r in sorted(coverage, key=lambda r: (-r["accepted_unique_structures"], r["role"]))]
    lines += ["", "## Policy and interpretation", "",
              "The pinned policy accepts single connected carbon-containing graphs with H, C, N, O, F, P, S, Cl, Br and I. Supplied stereochemistry, isotopes and charges are preserved. Counterions are not stripped, tautomers are not merged and absent stereochemistry is not invented. Extended notations, radicals and unsupported chemistries remain reviewable exclusions, not blanket claims of chemical invalidity. Source QC levels and notes are preserved without a QC threshold.", "",
              "Original exports and the pinned CPDat archive remain unchanged. Missing annotations remain unknown. Molecular identity acceptance does not verify a documented function, performance, safety or suitability. This is the pinned historical CPDat v4.0 snapshot, article version 5.", "",
              "## Next milestone", "",
              "Measure molecular-family and fingerprint diversity for each supported role, examine source concentration and excluded chemistry, then select the label set. Broad or nonfunctional categories such as 'No specific technical function' require review. Freeze family-aware train/validation/test partitions before comparing baselines. Structure counts alone do not establish family diversity. No classifier has been trained.", ""]
    return "\n".join(lines)


def run(paths, expected_path, archive_path, output_root):
    policy = json.loads((v.ROOT / "structure_policy.json").read_text())
    v.validate_policy(policy)
    if importlib.metadata.version("rdkit") != policy["required_rdkit_distribution"]:
        raise ValueError("RDKit version differs from structure_policy.json; install requirements-structures.txt.")
    expected = v.load_expected(expected_path)
    lock = json.loads((v.ROOT / "source-lock.json").read_text())
    v.verify_archive(archive_path, lock)
    records, substances, inputs = validate_batches(paths, expected, policy)
    accepted = {r["input_dtxsid"]: r for r in substances if r["status"] == "accepted"}
    vocabulary, labels, evidence = v.collect_annotations(archive_path, lock, expected, accepted)
    molecules, pairs, _ = v.aggregate(substances, labels, vocabulary, evidence)
    coverage = role_coverage(substances, labels, vocabulary)
    metrics = metrics_for(records, substances, molecules, pairs, evidence, labels)
    assert sum(metrics["substance_statuses"].values()) == len(expected)
    data = output_root / "data/processed/structure_full"
    reports = output_root / "reports/structure_full"
    data.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    v.write_csv(data / "export_validation.csv", records, ROW_FIELDS)
    v.write_csv(data / "substance_validation.csv", substances, SUBSTANCE_FIELDS)
    v.write_csv(data / "review.csv", [r for r in substances if r["status"] not in {"accepted", "missing_export_record"}], SUBSTANCE_FIELDS)
    v.write_csv(data / "pending_exports.csv", [r for r in substances if r["status"] == "missing_export_record"], SUBSTANCE_FIELDS)
    v.write_csv(data / "accepted_molecules.csv", molecules, ["molecule_id", "canonical_smiles", "computed_inchikey", "heavy_atoms", "formal_charge", "source_dtxsids"])
    v.write_csv(data / "accepted_role_pairs.csv", pairs, ["molecule_id", "role_id", "role", "source_dtxsids", "evidence_records", "distinct_documents"])
    v.write_csv(data / "accepted_evidence.csv", evidence, ["molecule_id", "dtxsid", "role_id", "role", "source", "source_url", "document_id", "document_url", "raw_functional_use"])
    v.write_csv(reports / "role_structure_coverage.csv", coverage, ["role", "role_id", "requested_substances", "exported_substances", "pending_export_substances", "accepted_substances", "accepted_unique_structures", "accepted_fraction_of_requested", "accepted_fraction_of_exported"])
    v.write_json(reports / "structure_audit.json", metrics)
    (reports / "structure_audit.md").write_text(report_text(metrics, coverage, inputs), encoding="utf-8")
    v.write_json(reports / "structure_manifest.json", {
        "generated_at_utc": v.utc_now(), "python_version": platform.python_version(),
        "rdkit_engine": v.rdBase.rdkitVersion,
        "dependencies": {name: importlib.metadata.version(name) for name in ["rdkit", "numpy", "Pillow"]},
        "policy": policy, "policy_sha256": v.policy_hash(policy),
        "inputs": {"structure_csvs": inputs, "expected_ids": {"name": expected_path.name, "sha256": v.digest(expected_path)},
                   "cpdat_archive": {"name": archive_path.name, "sha256": lock["sha256"]}},
        "code_sha256": {name: v.digest(v.ROOT / name) for name in ["import_structure_batches.py", "validate_structures.py", "audit_cpdat.py"]},
        "retrieval_date_note": "Export filenames are retained; no independently verified download timestamp is embedded in the CSVs.",
        "scope": "complete requested list; inspect export_coverage_complete in structure_audit.json"})
    print(json.dumps(metrics, indent=2))
    print(f"Read: {reports / 'structure_audit.md'}")
    return metrics


def completion_exit_code(metrics, require_complete):
    return 2 if require_complete and not metrics["export_coverage_complete"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", nargs="+", required=True, type=Path)
    parser.add_argument("--expected-ids", type=Path, default=v.ROOT / "data/interim/dtxsids_all.txt")
    parser.add_argument("--archive", type=Path, default=v.ROOT / "data/raw/cpdat_v4.0.zip")
    parser.add_argument("--output-dir", type=Path, default=v.ROOT)
    parser.add_argument("--require-complete", action="store_true", help="Write audit, then exit 2 if any requested ID lacks an export record; not a chemistry quality gate.")
    args = parser.parse_args()
    try:
        metrics = run([p.resolve() for p in args.csv], args.expected_ids.resolve(), args.archive.resolve(), args.output_dir.resolve())
    except (OSError, ValueError, zipfile.BadZipFile, csv.Error) as exc:
        print(f"Batch import stopped: {exc}", file=sys.stderr)
        return 1
    return completion_exit_code(metrics, args.require_complete)


if __name__ == "__main__":
    raise SystemExit(main())
