#!/usr/bin/env python3
"""Validate a CompTox CSV against requested IDs and join CPDat function evidence."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import re
import sys
import zipfile

try:
    from rdkit import Chem, rdBase
    from rdkit.Chem import inchi
except ImportError as exc:
    raise SystemExit("Install structure dependencies first: python -m pip install -r requirements-structures.txt") from exc

from audit_cpdat import ROOT, member_name, read_vocabulary, utc_now, verify_archive, write_csv, write_json

MISSING = {"", "n/a", "na", "null", "none"}
KEY_RE = re.compile(r"[A-Z]{14}-[A-Z]{8}[SN]A-[A-Z]")
ROW_FIELDS = [
    "csv_record_number", "input_dtxsid", "returned_dtxsid", "status", "structural_pass",
    "preferred_name", "original_smiles", "canonical_smiles", "molecule_id", "dtxcid",
    "source_inchikey", "computed_inchikey", "identifier_check", "qc_level", "qc_notes",
    "heavy_atoms", "formal_charge", "fragment_count", "source_casrn", "found_by", "processing_error",
]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            value.update(block)
    return value.hexdigest()


def policy_hash(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def text(value):
    return value.strip() if isinstance(value, str) else ""


def absent(value):
    return text(value).lower() in MISSING


def validate_policy(policy):
    # These are fixed behaviors in v1, not switches for unimplemented transforms.
    fixed = {"policy_id": "single-component-organic-v1", "require_carbon": True,
             "require_single_component": True, "reject_extended_smiles": True,
             "reject_query_atoms": True, "reject_radicals": True,
             "preserve_stereochemistry": True, "neutralize": False,
             "strip_counterions": False, "normalize_tautomers": False,
             "qc_level_filter": None}
    if any(policy.get(key) != value for key, value in fixed.items()):
        raise ValueError("Unsupported policy settings: implement and version the corresponding chemistry behavior first.")
    allowed = policy.get("allowed_atomic_numbers")
    if not isinstance(allowed, list) or 6 not in allowed or any(type(v) is not int or v <= 0 for v in allowed):
        raise ValueError("Invalid allowed_atomic_numbers policy.")
    if type(policy.get("allow_inchikey_standard_flag_only_difference")) is not bool:
        raise ValueError("The InChIKey standard-flag exception must be explicit.")
    if policy.get("source_inchikey_required") is not True:
        raise ValueError("This policy requires a source InChIKey for the identity cross-check.")


def compare_keys(source, computed):
    """Only position 24 may differ for the explicit standard/nonstandard exception."""
    if absent(source):
        return "missing_source_key"
    if not KEY_RE.fullmatch(source):
        return "malformed_source_key"
    if source == computed:
        return "exact_match"
    if source[:23] == computed[:23] and source[24:] == computed[24:] and {source[23], computed[23]} == {"N", "S"}:
        return "standard_flag_only"
    return "identifier_hash_conflict"


def assess_smiles(smiles, source_key, policy):
    result = {"status": "", "structural_pass": False, "canonical_smiles": "", "molecule_id": "",
              "computed_inchikey": "", "identifier_check": "not_checked", "heavy_atoms": "",
              "formal_charge": "", "fragment_count": ""}
    if absent(smiles):
        result["status"] = "missing_smiles"
        return result
    if "|" in smiles:
        result["status"] = "unsupported_extended_smiles"
        return result
    params = Chem.SmilesParserParams()
    params.parseName = False
    params.allowCXSMILES = False
    # Quiet parse diagnostics; the output records explicitly identify failures.
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(smiles, params)
    if mol is None or mol.GetNumAtoms() == 0:
        result["status"] = "invalid_smiles"
        return result
    result.update(heavy_atoms=mol.GetNumHeavyAtoms(), formal_charge=Chem.GetFormalCharge(mol),
                  fragment_count=len(Chem.GetMolFrags(mol)))
    if any(atom.GetAtomicNum() == 0 or atom.HasQuery() for atom in mol.GetAtoms()):
        result["status"] = "query_or_wildcard"
    elif result["fragment_count"] != 1:
        result["status"] = "multiple_components"
    elif not any(atom.GetAtomicNum() == 6 for atom in mol.GetAtoms()):
        result["status"] = "no_carbon"
    elif any(atom.GetAtomicNum() not in policy["allowed_atomic_numbers"] for atom in mol.GetAtoms()):
        result["status"] = "unsupported_elements"
    elif any(atom.GetNumRadicalElectrons() for atom in mol.GetAtoms()):
        result["status"] = "unsupported_radical"
    if result["status"]:
        return result
    result["structural_pass"] = True
    canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    result["canonical_smiles"] = canonical
    with rdBase.BlockLogs():
        computed = inchi.MolToInchiKey(mol)
    result["computed_inchikey"] = computed
    if not computed:
        result["status"] = "inchi_generation_failed"
        return result
    check = compare_keys(source_key, computed)
    result["identifier_check"] = check
    if check == "standard_flag_only" and not policy["allow_inchikey_standard_flag_only_difference"]:
        result["status"] = "identifier_format_review"
    elif check not in {"exact_match", "standard_flag_only"}:
        result["status"] = check
    else:
        result["status"] = "accepted"
        identity = policy_hash(policy) + "|" + rdBase.rdkitVersion + "|" + canonical
        result["molecule_id"] = "mol_" + hashlib.sha256(identity.encode()).hexdigest()
    return result


def load_expected(path):
    values = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not values or len(values) != len(set(values)) or any(not re.fullmatch(r"DTXSID[0-9]+", v) for v in values):
        raise ValueError("Expected-ID file must contain unique DTXSIDs, one per line, without a header.")
    return values


def validate_export(path, expected, policy):
    expected_set = set(expected)
    records = []
    grouped = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"INPUT", "DTXSID", "SMILES", "INCHIKEY"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Unexpected CompTox schema; missing columns: {sorted(missing)}")
        for number, raw in enumerate(reader, start=1):
            item = dict.fromkeys(ROW_FIELDS, "")
            item.update(csv_record_number=number, input_dtxsid=text(raw.get("INPUT")),
                        returned_dtxsid=text(raw.get("DTXSID")), preferred_name=text(raw.get("PREFERRED_NAME")),
                        original_smiles=text(raw.get("SMILES")), dtxcid=text(raw.get("DTXCID")),
                        source_inchikey=text(raw.get("INCHIKEY")), qc_level=text(raw.get("QC_LEVEL")),
                        qc_notes=text(raw.get("QC_NOTES")), source_casrn=text(raw.get("CASRN")),
                        found_by=text(raw.get("FOUND_BY")), structural_pass=False)
            if None in raw or any(v is None for v in raw.values()):
                item["status"] = "malformed_csv_record"
            elif item["input_dtxsid"] not in expected_set:
                item["status"] = "unexpected_input_id"
            elif absent(item["returned_dtxsid"]):
                item["status"] = "unmatched_identifier"
            elif item["input_dtxsid"] != item["returned_dtxsid"]:
                item["status"] = "input_returned_id_conflict"
            else:
                try:
                    item.update(assess_smiles(item["original_smiles"], item["source_inchikey"], policy))
                except (ValueError, RuntimeError, OverflowError) as exc:
                    item.update(status="structure_processing_error", structural_pass=False,
                                molecule_id="", processing_error=f"{type(exc).__name__}: {exc}")
            records.append(item)
            if item["input_dtxsid"] in expected_set:
                grouped[item["input_dtxsid"]].append(item)
    substances = []
    for sid in expected:
        candidates = grouped.get(sid, [])
        if not candidates:
            item = dict.fromkeys(ROW_FIELDS, "")
            item.update(input_dtxsid=sid, status="missing_export_record", structural_pass=False)
        else:
            item = dict(candidates[0])
            signatures = {(r["status"], r["returned_dtxsid"], r["canonical_smiles"], r["identifier_check"]) for r in candidates}
            if len(signatures) > 1:
                item.update(status="conflicting_export_records", structural_pass=False, molecule_id="")
            # Metadata for every duplicate remains available in export_validation.csv.
        item["export_record_count"] = len(candidates)
        substances.append(item)
    return records, substances


def collect_annotations(archive_path, lock, expected, accepted):
    expected_set = set(expected)
    labels = defaultdict(set)
    evidence = []
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(member_name(archive, lock["vocabulary_member"])) as stream:
            vocabulary = read_vocabulary(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
        with archive.open(member_name(archive, lock["functions_member"])) as binary:
            reader = csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))
            required = {"dtxsid", "provisional_dtxsid", "function_category", "data_source", "cpdat_data_document_id"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("CPDat functional-use schema is incompatible.")
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    continue
                sid, role = text(row.get("dtxsid")), text(row.get("function_category"))
                if sid not in expected_set or text(row.get("provisional_dtxsid")).lower() != "false" or role not in vocabulary:
                    continue
                labels[sid].add(role)
                if sid in accepted:
                    evidence.append({
                        "molecule_id": accepted[sid]["molecule_id"], "dtxsid": sid,
                        "role_id": vocabulary[role], "role": role,
                        "source": text(row.get("data_source")),
                        "source_url": text(row.get("data_source_url")),
                        "document_id": text(row.get("cpdat_data_document_id")),
                        "document_url": text(row.get("data_document_url")),
                        "raw_functional_use": text(row.get("raw_functional_use")),
                    })
    return vocabulary, labels, evidence


def aggregate(substances, labels, vocabulary, evidence):
    accepted = {r["input_dtxsid"]: r for r in substances if r["status"] == "accepted"}
    molecule_groups = defaultdict(list)
    for item in accepted.values():
        molecule_groups[item["molecule_id"]].append(item)
    molecules = []
    for identity in sorted(molecule_groups):
        group = molecule_groups[identity]
        first = group[0]
        if len({r["canonical_smiles"] for r in group}) != 1:
            raise ValueError("Molecule identity hash collision.")
        molecules.append({
            "molecule_id": identity, "canonical_smiles": first["canonical_smiles"],
            "computed_inchikey": first["computed_inchikey"], "heavy_atoms": first["heavy_atoms"],
            "formal_charge": first["formal_charge"],
            "source_dtxsids": json.dumps(sorted(r["input_dtxsid"] for r in group)),
        })
    pair_groups = defaultdict(list)
    for row in evidence:
        pair_groups[(row["molecule_id"], row["role_id"], row["role"])].append(row)
    pairs = [{
        "molecule_id": key[0], "role_id": key[1], "role": key[2],
        "source_dtxsids": json.dumps(sorted({r["dtxsid"] for r in rows})),
        "evidence_records": len(rows),
        "distinct_documents": len({r["document_id"] for r in rows if r["document_id"]}),
    } for key, rows in sorted(pair_groups.items())]
    roles = sorted(set().union(*labels.values())) if labels else []
    coverage = []
    for role in roles:
        denominator = {sid for sid, values in labels.items() if role in values}
        retained = denominator & set(accepted)
        coverage.append({
            "role": role, "role_id": vocabulary[role],
            "requested_sample_substances_with_role": len(denominator),
            "accepted_sample_substances_with_role": len(retained),
            "accepted_unique_structures_with_role": len({accepted[sid]["molecule_id"] for sid in retained}),
            "sample_retention_fraction": f"{len(retained)/len(denominator):.6f}",
        })
    return molecules, pairs, coverage


def report_text(metrics, coverage):
    lines = ["# Structure sample validation", "",
        "Diagnostic sample only; these rates are not estimates for all CPDat substances.", "",
        "| Measure | Count |", "|---|---:|",
        f"| Requested substance IDs | {metrics['requested_ids']} |",
        f"| Export CSV records | {metrics['export_records']} |",
        f"| Requested IDs present in export | {metrics['requested_ids_present']} |",
        f"| Structurally eligible substances | {metrics['structurally_eligible_substances']} |",
        f"| Accepted substances including identifier cross-check | {metrics['accepted_substances']} |",
        f"| Accepted unique molecular structures | {metrics['accepted_unique_structures']} |",
        f"| Unique accepted structure–role pairs | {metrics['accepted_structure_role_pairs']} |", "",
        "## Substance decisions", "", "| Status | Count |", "|---|---:|"]
    lines += [f"| {status} | {count} |" for status, count in sorted(metrics["substance_statuses"].items())]
    lines += ["", "## Identifier checks for accepted substances", "", "| Check | Count |", "|---|---:|"]
    lines += [f"| {status} | {count} |" for status, count in sorted(metrics["accepted_identifier_checks"].items())]
    lines += ["", "Both original keys and recomputed Standard InChIKeys are preserved. The explicit standard_flag_only exception allows a difference only at the standard/nonstandard flag; both hash blocks, the version, and protonation indicator must be identical. Other differences are quarantined. This is an identifier cross-check, not experimental verification of function.", "",
        "## Role coverage in this sample", "",
        "Each molecule can have several roles. Retention fractions use requested sample IDs known to have the role as the denominator; they are not model scores.", "",
        "| Role | Requested IDs | Accepted IDs | Unique accepted structures |", "|---|---:|---:|---:|"]
    lines += [f"| {r['role']} | {r['requested_sample_substances_with_role']} | {r['accepted_sample_substances_with_role']} | {r['accepted_unique_structures_with_role']} |" for r in coverage]
    lines += ["", "## Policy and limits", "",
        "This policy accepts a single connected carbon-containing graph composed of H, C, N, O, F, P, S, Cl, Br, I. Charges, isotopes, and supplied stereochemistry are preserved. Counterions are not stripped; tautomers are not merged; missing stereochemistry is not invented. Radicals, wildcard/query atoms, and extended CXSMILES are outside this first parser policy. Some extended notations describe definite molecules; they are routed to review rather than labelled chemically invalid.", "",
        "Salts and multicomponent materials may have useful industrial roles; exclusion here is a scope decision. Source QC levels and notes are retained without imposing a QC-level threshold. Acceptance does not establish source identity accuracy, performance, safety, or regulatory suitability.", "",
        "The original CSV and source archive remain unchanged. accepted_role_pairs.csv deduplicates training candidates by molecular structure and role; accepted_evidence.csv preserves their original supporting records. Unknown roles remain unlabelled.", "",
        "Next: resolve the complete ID batches, evaluate role-specific structural coverage and molecular-family diversity, and refine unsupported-chemistry handling before selecting the training label set. No classifier is trained by this step.", ""]
    return "\n".join(lines)


def run(csv_path, expected_path, archive_path, output_root):
    policy = json.loads((ROOT / "structure_policy.json").read_text())
    validate_policy(policy)
    if importlib.metadata.version("rdkit") != policy["required_rdkit_distribution"]:
        raise ValueError("RDKit version differs from the policy. Install requirements-structures.txt or version the policy explicitly.")
    expected = load_expected(expected_path)
    lock = json.loads((ROOT / "source-lock.json").read_text())
    verify_archive(archive_path, lock)
    records, substances = validate_export(csv_path, expected, policy)
    accepted = {r["input_dtxsid"]: r for r in substances if r["status"] == "accepted"}
    vocabulary, labels, evidence = collect_annotations(archive_path, lock, expected, accepted)
    molecules, pairs, coverage = aggregate(substances, labels, vocabulary, evidence)
    missing_annotations = sorted(set(expected) - set(labels))
    metrics = {
        "requested_ids": len(expected), "export_records": len(records),
        "requested_ids_present": sum(r["export_record_count"] > 0 for r in substances),
        "unexpected_export_records": sum(r["status"] == "unexpected_input_id" for r in records),
        "structurally_eligible_substances": sum(bool(r["structural_pass"]) for r in substances),
        "accepted_substances": len(accepted), "accepted_unique_structures": len(molecules),
        "accepted_structure_role_pairs": len(pairs), "accepted_evidence_records": len(evidence),
        "requested_ids_without_retained_cpdat_labels": missing_annotations,
        "substance_statuses": dict(Counter(r["status"] for r in substances)),
        "accepted_identifier_checks": dict(Counter(r["identifier_check"] for r in accepted.values())),
        "export_qc_level_counts": dict(Counter(r["qc_level"] or "missing" for r in records)),
        "scope": "diagnostic_sample", "training_status": "not_trained",
    }
    assert sum(metrics["substance_statuses"].values()) == len(expected)
    data = output_root / "data/processed/structure_sample"
    reports = output_root / "reports/structure_sample"
    data.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    write_csv(data / "export_validation.csv", records, ROW_FIELDS)
    write_csv(data / "substance_validation.csv", substances, ROW_FIELDS + ["export_record_count"])
    write_csv(data / "review.csv", [r for r in substances if r["status"] != "accepted"], ROW_FIELDS + ["export_record_count"])
    write_csv(data / "accepted_molecules.csv", molecules, ["molecule_id", "canonical_smiles", "computed_inchikey", "heavy_atoms", "formal_charge", "source_dtxsids"])
    write_csv(data / "accepted_role_pairs.csv", pairs, ["molecule_id", "role_id", "role", "source_dtxsids", "evidence_records", "distinct_documents"])
    write_csv(data / "accepted_evidence.csv", evidence, ["molecule_id", "dtxsid", "role_id", "role", "source", "source_url", "document_id", "document_url", "raw_functional_use"])
    write_csv(reports / "role_structure_coverage.csv", coverage, ["role", "role_id", "requested_sample_substances_with_role", "accepted_sample_substances_with_role", "accepted_unique_structures_with_role", "sample_retention_fraction"])
    write_json(reports / "structure_audit.json", metrics)
    write_json(reports / "structure_manifest.json", {
        "generated_at_utc": utc_now(), "python_version": platform.python_version(),
        "rdkit_engine": rdBase.rdkitVersion,
        "dependencies": {name: importlib.metadata.version(name) for name in ["rdkit", "numpy", "Pillow"]},
        "policy": policy, "policy_sha256": policy_hash(policy),
        "inputs": {"structure_csv": {"name": csv_path.name, "sha256": digest(csv_path)},
                   "expected_ids": {"name": expected_path.name, "sha256": digest(expected_path)},
                   "cpdat_archive": {"name": archive_path.name, "sha256": lock["sha256"]}},
        "validator_sha256": digest(Path(__file__)), "annotation_audit_sha256": digest(ROOT / "audit_cpdat.py"),
        "sample_bias": "Diagnostic sample from ten largest roles; not representative of all substances.",
    })
    (reports / "structure_audit.md").write_text(report_text(metrics, coverage), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    print(f"Read: {reports / 'structure_audit.md'}")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, help="Original CompTox export CSV.")
    parser.add_argument("--expected-ids", type=Path, default=ROOT / "data/interim/structure_sample_100.txt")
    parser.add_argument("--archive", type=Path, default=ROOT / "data/raw/cpdat_v4.0.zip")
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        run(args.csv.resolve(), args.expected_ids.resolve(), args.archive.resolve(), args.output_dir.resolve())
    except (OSError, ValueError, zipfile.BadZipFile, csv.Error) as exc:
        print(f"Validation stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
