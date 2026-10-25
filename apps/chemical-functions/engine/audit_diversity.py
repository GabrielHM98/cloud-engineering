#!/usr/bin/env python3
"""Audit structure similarity groups, ring scaffolds and annotation concentration."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import zipfile

import numpy as np
from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator
from rdkit.Chem.Scaffolds import MurckoScaffold

import validate_structures as v


def read_csv(path, required):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f"Missing required columns in {path.name}.")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"Malformed CSV record in {path.name}.")
    return rows


def validate_policy(policy):
    fixed = {"policy_id": "morgan-family-audit-v1",
             "grouping": "connected_components_tanimoto_gte_threshold",
             "scaffold": "rdkit_murcko_nonisomeric", "acyclic_scaffold": "not_applicable"}
    if any(policy.get(key) != value for key, value in fixed.items()):
        raise ValueError("Unsupported diversity policy; implement and version the behavior first.")
    if policy.get("morgan") != {"radius": 2, "fpSize": 2048, "includeChirality": True,
                               "useBondTypes": True, "includeRingMembership": True, "countSimulation": False}:
        raise ValueError("Unsupported Morgan representation for this policy version.")
    thresholds = policy.get("similarity_thresholds", [])
    if (not thresholds or any(type(t) not in {float, int} or not 0 < t <= 1 for t in thresholds)
            or thresholds != sorted(set(thresholds)) or policy.get("primary_similarity_threshold") not in thresholds):
        raise ValueError("Similarity thresholds must be unique, increasing and in (0, 1].")


def representations(molecules, policy, structure_policy):
    generator = rdFingerprintGenerator.GetMorganGenerator(**policy["morgan"])
    rows, fingerprints = [], []
    bits = np.zeros((len(molecules), policy["morgan"]["fpSize"]), dtype=np.uint8)
    identities, canonical_seen = set(), set()
    for index, row in enumerate(sorted(molecules, key=lambda r: r["molecule_id"])):
        identity, smiles = row["molecule_id"], row["canonical_smiles"]
        mol = Chem.MolFromSmiles(smiles)
        if mol is None or not mol.GetNumAtoms():
            raise ValueError(f"Accepted input no longer parses: {identity}.")
        canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
        calculated = "mol_" + hashlib.sha256((v.policy_hash(structure_policy)+"|"+v.rdBase.rdkitVersion+"|"+canonical).encode()).hexdigest()
        if calculated != identity or canonical != smiles or identity in identities or canonical in canonical_seen:
            raise ValueError("Input identities, canonicalization or molecule uniqueness disagree with the pinned policy.")
        identities.add(identity)
        canonical_seen.add(canonical)
        fp = generator.GetFingerprint(mol)
        DataStructs.ConvertToNumpyArray(fp, bits[index])
        fingerprints.append(fp)
        scaffold = MurckoScaffold.GetScaffoldForMol(mol)
        rows.append({"molecule_id": identity, "canonical_smiles": smiles,
                     "ring_scaffold_smiles": Chem.MolToSmiles(scaffold, isomericSmiles=False) if scaffold.GetNumAtoms() else "",
                     "scaffold_status": "ring_scaffold" if scaffold.GetNumAtoms() else "acyclic_not_applicable"})
    return rows, fingerprints, bits


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, i):
        while i != self.parent[i]:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            if self.size[a] < self.size[b]:
                a, b = b, a
            self.parent[b] = a
            self.size[a] += self.size[b]


def similarity_groups(ids, fingerprints, thresholds, policy_sha, progress=False):
    """Exact all-pairs comparison, retaining one row of similarities at a time."""
    forests = {t: UnionFind(len(ids)) for t in thresholds}
    edges = Counter()
    nearest = np.zeros(len(ids), dtype=float)
    for i in range(1, len(ids)):
        sims = np.asarray(DataStructs.BulkTanimotoSimilarity(fingerprints[i], fingerprints[:i]))
        nearest[i] = max(nearest[i], float(sims.max()))
        np.maximum(nearest[:i], sims, out=nearest[:i])
        for j in np.flatnonzero(sims >= thresholds[0]):
            for threshold in thresholds:
                if sims[j] >= threshold:
                    forests[threshold].join(i, int(j))
                    edges[threshold] += 1
        if progress and i % 2000 == 0:
            print(f"Compared {i:,}/{len(ids):,} fingerprint rows.", flush=True)
    memberships, summaries = {}, []
    for threshold, forest in forests.items():
        groups = defaultdict(list)
        for i, identity in enumerate(ids):
            groups[forest.find(i)].append(identity)
        mapping = {}
        for members in groups.values():
            payload = policy_sha + "|" + str(threshold) + "|" + "|".join(sorted(members))
            group_id = "sim_" + hashlib.sha256(payload.encode()).hexdigest()
            mapping.update({identity: group_id for identity in members})
        memberships[threshold] = mapping
        sizes = [len(members) for members in groups.values()]
        summaries.append({"tanimoto_threshold": threshold, "components": len(sizes),
                          "singleton_components": sum(size == 1 for size in sizes),
                          "largest_component_molecules": max(sizes, default=0),
                          "edges_at_or_above_threshold": edges[threshold]})
    return memberships, summaries, nearest


def role_diagnostics(rows, pairs, evidence, memberships, primary):
    byid = {row["molecule_id"]: row for row in rows}
    role_members = defaultdict(set)
    role_ids = {}
    for pair in pairs:
        role, identity = pair["role"], pair["molecule_id"]
        if identity not in byid or (role in role_ids and role_ids[role] != pair["role_id"]):
            raise ValueError("Role-pair references or role IDs are inconsistent.")
        if identity in role_members[role]:
            raise ValueError("Repeated molecule-role pair; use the deduplicated step-3 output.")
        role_members[role].add(identity)
        role_ids[role] = pair["role_id"]
    sources, documents = defaultdict(set), defaultdict(set)
    seen_evidence_pairs = set()
    for record in evidence:
        role, identity = record["role"], record["molecule_id"]
        if identity not in role_members.get(role, set()) or record["role_id"] != role_ids[role]:
            raise ValueError("Evidence references an unknown molecule-role pair.")
        seen_evidence_pairs.add((identity, role))
        if record["source"]:
            sources[(role, record["source"])].add(identity)
        if record["document_id"]:
            documents[(role, record["document_id"])].add(identity)
    if seen_evidence_pairs != {(identity, role) for role, members in role_members.items() for identity in members}:
        raise ValueError("A molecule-role pair has no supporting evidence.")
    source_rows = [{"role": role, "source": source, "unique_molecules": len(values)} for (role, source), values in sorted(sources.items())]
    document_rows = [{"role": role, "document_id": doc, "unique_molecules": len(values)} for (role, doc), values in sorted(documents.items())]
    summary = []
    for role, members in sorted(role_members.items()):
        sizes = Counter(memberships[primary][identity] for identity in members)
        n = len(members)
        source_counts = {source: len(values) for (r, source), values in sources.items() if r == role}
        doc_counts = {doc: len(values) for (r, doc), values in documents.items() if r == role}
        top_source = min(source_counts, key=lambda x: (-source_counts[x], x)) if source_counts else ""
        top_doc = min(doc_counts, key=lambda x: (-doc_counts[x], x)) if doc_counts else ""
        summary.append({"role": role, "role_id": role_ids[role], "unique_molecules": n,
                        "acyclic_molecules": sum(not byid[i]["ring_scaffold_smiles"] for i in members),
                        "distinct_nonempty_ring_scaffolds": len({byid[i]["ring_scaffold_smiles"] for i in members if byid[i]["ring_scaffold_smiles"]}),
                        "similarity_components": len(sizes), "largest_component_molecules_with_role": max(sizes.values()),
                        "largest_component_role_fraction": max(sizes.values()) / n,
                        "effective_component_count": n*n / sum(size*size for size in sizes.values()),
                        "distinct_named_sources": len(source_counts), "top_source": top_source,
                        "top_source_molecule_fraction": source_counts.get(top_source, 0) / n,
                        "distinct_document_ids": len(doc_counts), "top_document_id": top_doc,
                        "top_document_molecule_fraction": doc_counts.get(top_doc, 0) / n,
                        "molecules_without_named_source": len(members-set().union(*(values for (r, _), values in sources.items() if r == role))),
                        "molecules_without_document_id": len(members-set().union(*(values for (r, _), values in documents.items() if r == role)))})
    overlap = []
    roles = sorted(role_members)
    for i, a in enumerate(roles):
        for b in roles[i+1:]:
            both = len(role_members[a] & role_members[b])
            if both:
                overlap.append({"role_a": a, "role_b": b, "shared_molecules": both,
                                "jaccard": both / len(role_members[a] | role_members[b]),
                                "fraction_of_a_annotated_b": both / len(role_members[a]),
                                "fraction_of_b_annotated_a": both / len(role_members[b])})
    return summary, source_rows, document_rows, overlap


def write_table(path, rows, fields=None):
    v.write_csv(path, rows, fields or list(rows[0]))


def report_text(metrics, groups, roles, candidates, primary):
    lines = ["# Chemical diversity and annotation audit", "",
             "Exploratory diagnostics only. No labels are frozen, no split is assigned and no model is trained.", "",
             f"Audited **{metrics['molecules']:,} unique structures** and **{metrics['molecule_role_pairs']:,} molecule-role pairs**.",
             f"Acyclic structures: **{metrics['acyclic_molecules']:,}**. Distinct nonempty ring scaffolds: **{metrics['nonempty_ring_scaffolds']:,}**.", "",
             "## Similarity sensitivity", "",
             "Morgan bit fingerprints: radius 2, 2,048 bits, supplied chirality included. Compare every unordered pair using Tanimoto similarity. An edge joins a pair at or above the threshold; groups are connected components over all accepted structures.", "",
             "| Threshold | Components | Singleton components | Largest component |", "|---|---:|---:|---:|"]
    lines += [f"| {r['tanimoto_threshold']} | {r['components']} | {r['singleton_components']} | {r['largest_component_molecules']} |" for r in groups]
    lines += ["", f"The primary diagnostic threshold is {primary}. This is an exploratory choice, not a validated chemical-family boundary. Components can chain through intermediate molecules; all members need not resemble one another above the threshold. Group membership depends on the dataset. These are similarity proxies, not proven independent chemical families.", "",
              "Ring scaffolds are an independent diagnostic, calculated without stereochemistry. Acyclic molecules have no ring scaffold and are never collapsed into a single empty-scaffold family. They still participate in fingerprint grouping. Molecular identities and supplied stereo/charge are unchanged.", "",
              "## Candidate functions for review", "",
              "| Function | Structures | Similarity components | Largest component share | Top source coverage |", "|---|---:|---:|---:|---:|"]
    for row in candidates:
        lines.append(f"| {row['role']} | {row['unique_molecules']} | {row['similarity_components']} | {row['largest_component_role_fraction']:.1%} | {row['top_source_molecule_fraction']:.1%} |")
    lines += ["", "Each role's component support counts its molecules within groups constructed from the entire accepted dataset; molecules annotated with other roles may provide connecting paths. Effective component count is 1 / sum(p_g squared), where p_g is the role's fraction in component g. It measures concentration under this grouping; it is not an estimate of independent experimental sample size.", "",
              "Source coverage uses unique role-annotated molecules, not repeated evidence rows. Sources can overlap, so their coverage fractions need not sum to one. Source names are compared literally, and multiple source records may repeat or derive from the same underlying claims. Document IDs are not guaranteed to be independent experiments.", "",
              "## Interpretation and next action", "",
              "Review role_summary.csv, role_source_coverage.csv, role_document_coverage.csv, role_overlap.csv and role_exclusion_counts.csv before choosing labels. A large function count can still be concentrated in related structures or one catalog. Low acceptance can reflect the representation policy, especially for salts and multicomponent materials.", "",
              "Use the sensitivity table and per-role support to choose and freeze a split grouping policy. An eventual evaluation can target new similarity groups, new scaffolds, new sources or a combination; those are different generalization questions. No train/test leakage guarantee is asserted here because no partition has been created. Scaffold diagnostics alone are not used as a split.", "",
              "The candidate-role list is a review proposal. No specific technical function, degradant/impurity and similarly broad categories should not silently become desired-use labels. Missing annotations remain unknown. Later evaluation must distinguish unannotated pairs from verified negatives and avoid interpreting annotation-ranking scores as validated industrial suitability.", "",
              "Cached fingerprints are keyed by molecular identity and include no labels. Their bits are a finite hashed representation, so equal fingerprints do not establish equal molecular identity. No target-derived feature selection, scaling or model fitting occurs in this step.", "",
              "## References", "",
              "- RDKit Morgan generator: https://www.rdkit.org/docs/source/rdkit.Chem.rdFingerprintGenerator.html",
              "- RDKit Murcko scaffolds: https://www.rdkit.org/docs/source/rdkit.Chem.Scaffolds.MurckoScaffold.html",
              "- RDKit Tanimoto API: https://www.rdkit.org/docs/source/rdkit.DataStructs.cDataStructs.html", ""]
    return "\n".join(lines)


def run(root, archive_path, output_root):
    policy_path = v.ROOT / "diversity_policy.json"
    policy = json.loads(policy_path.read_text())
    validate_policy(policy)
    if importlib.metadata.version("rdkit") != policy["required_rdkit_distribution"]:
        raise ValueError("Install the RDKit version pinned in requirements-structures.txt.")
    reports_in = root / "reports/structure_full"
    data_in = root / "data/processed/structure_full"
    metrics_in = json.loads((reports_in / "structure_audit.json").read_text())
    manifest_in = json.loads((reports_in / "structure_manifest.json").read_text())
    structure_policy = json.loads((v.ROOT / "structure_policy.json").read_text())
    v.validate_policy(structure_policy)
    if (not metrics_in["export_coverage_complete"] or manifest_in["policy_sha256"] != v.policy_hash(structure_policy)
            or manifest_in["rdkit_engine"] != v.rdBase.rdkitVersion):
        raise ValueError("Complete export coverage and matching structure policy/RDKit are required.")
    molecules = read_csv(data_in / "accepted_molecules.csv", ["molecule_id", "canonical_smiles", "source_dtxsids"])
    pairs = read_csv(data_in / "accepted_role_pairs.csv", ["molecule_id", "role", "role_id"])
    evidence = read_csv(data_in / "accepted_evidence.csv", ["molecule_id", "dtxsid", "role", "role_id", "source", "document_id"])
    substances = read_csv(data_in / "substance_validation.csv", ["input_dtxsid", "status"])
    expected_path = root / "data/interim/dtxsids_all.txt"
    expected = v.load_expected(expected_path)
    if v.digest(expected_path) != manifest_in["inputs"]["expected_ids"]["sha256"]:
        raise ValueError("Expected-ID list changed since the full-list import.")
    if (len(molecules) != metrics_in["accepted_unique_structures"] or len(pairs) != metrics_in["accepted_structure_role_pairs"]
            or len(evidence) != metrics_in["accepted_evidence_records"] or len(substances) != metrics_in["requested_ids"]):
        raise ValueError("Step-3 table counts differ from their audit; regenerate the full-list import.")
    statuses = {r["input_dtxsid"]: r["status"] for r in substances}
    if len(statuses) != len(expected) or set(statuses) != set(expected):
        raise ValueError("Substance ledger does not match the requested-ID list.")
    sid_molecules = {}
    for row in molecules:
        for sid in json.loads(row["source_dtxsids"]):
            if sid in sid_molecules or statuses.get(sid) != "accepted":
                raise ValueError("Accepted molecule-substance mappings are inconsistent.")
            sid_molecules[sid] = row["molecule_id"]
    if set(sid_molecules) != {sid for sid, status in statuses.items() if status == "accepted"}:
        raise ValueError("Accepted substance coverage differs between tables.")
    if any(sid_molecules.get(r["dtxsid"]) != r["molecule_id"] for r in evidence):
        raise ValueError("Evidence substance identity differs from its accepted molecule.")
    lock = json.loads((v.ROOT / "source-lock.json").read_text())
    v.verify_archive(archive_path, lock)
    vocabulary, labels, _ = v.collect_annotations(archive_path, lock, expected, {})
    expected_pairs = {(sid_molecules[sid], vocabulary[role], role) for sid, values in labels.items() if sid in sid_molecules for role in values}
    if expected_pairs != {(r["molecule_id"], r["role_id"], r["role"]) for r in pairs}:
        raise ValueError("Structure-role pairs disagree with the pinned annotation source.")
    rows, fingerprints, bits = representations(molecules, policy, structure_policy)
    ids = [r["molecule_id"] for r in rows]
    memberships, groups, nearest = similarity_groups(ids, fingerprints, policy["similarity_thresholds"], v.policy_hash(policy), progress=True)
    primary = policy["primary_similarity_threshold"]
    for row, score in zip(rows, nearest):
        row["nearest_other_molecule_tanimoto"] = float(score) if len(ids) > 1 else ""
        for threshold in policy["similarity_thresholds"]:
            row[f"similarity_component_t{threshold}"] = memberships[threshold][row["molecule_id"]]
    roles, sources, documents, overlap = role_diagnostics(rows, pairs, evidence, memberships, primary)
    by_role = {r["role"]: r for r in roles}
    missing_candidates = set(policy["candidate_roles_for_review"]) - set(by_role)
    if missing_candidates:
        raise ValueError(f"Candidate functions absent from accepted data: {sorted(missing_candidates)}")
    candidates = [by_role[role] for role in policy["candidate_roles_for_review"]]
    exclusions = Counter((role, statuses[sid]) for sid, values in labels.items() for role in values)
    exclusion_rows = [{"role": role, "substance_status": status, "substances": count} for (role, status), count in sorted(exclusions.items())]
    metrics = {"molecules": len(rows), "molecule_role_pairs": len(pairs),
               "acyclic_molecules": sum(not r["ring_scaffold_smiles"] for r in rows),
               "nonempty_ring_scaffolds": len({r["ring_scaffold_smiles"] for r in rows if r["ring_scaffold_smiles"]}),
               "primary_similarity_threshold": primary, "all_pairs_compared": len(rows)*(len(rows)-1)//2,
               "median_nearest_other_tanimoto": float(np.median(nearest)) if len(rows) > 1 else None,
               "similarity_sensitivity": groups, "role_count": len(roles),
               "label_selection_status": "proposed_for_review_not_frozen", "partition_status": "not_created", "training_status": "not_trained"}
    data = output_root / "data/processed/diversity"
    reports = output_root / "reports/diversity"
    data.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    write_table(data / "molecule_groups.csv", rows)
    np.savez_compressed(data / "morgan_fingerprints.npz", molecule_ids=np.asarray(ids), morgan_bits=bits)
    write_table(reports / "role_summary.csv", roles)
    write_table(reports / "role_source_coverage.csv", sources, ["role", "source", "unique_molecules"])
    write_table(reports / "role_document_coverage.csv", documents, ["role", "document_id", "unique_molecules"])
    write_table(reports / "role_overlap.csv", overlap, ["role_a", "role_b", "shared_molecules", "jaccard", "fraction_of_a_annotated_b", "fraction_of_b_annotated_a"])
    write_table(reports / "role_exclusion_counts.csv", exclusion_rows)
    write_table(reports / "similarity_sensitivity.csv", groups)
    v.write_json(reports / "label_review.json", {"status": "review_not_frozen", "candidate_functions": candidates})
    v.write_json(reports / "diversity_audit.json", metrics)
    (reports / "diversity_audit.md").write_text(report_text(metrics, groups, roles, candidates, primary), encoding="utf-8")
    input_paths = [data_in / name for name in ["accepted_molecules.csv", "accepted_role_pairs.csv", "accepted_evidence.csv", "substance_validation.csv"]]
    input_paths += [reports_in / "structure_audit.json", reports_in / "structure_manifest.json", expected_path]
    output_paths = sorted(data.glob("*")) + sorted(p for p in reports.glob("*") if p.name != "diversity_manifest.json")
    v.write_json(reports / "diversity_manifest.json", {
        "generated_at_utc": v.utc_now(), "python": platform.python_version(), "rdkit_engine": v.rdBase.rdkitVersion,
        "dependencies": {name: importlib.metadata.version(name) for name in ["rdkit", "numpy", "Pillow"]},
        "diversity_policy": policy, "diversity_policy_sha256": v.policy_hash(policy),
        "structure_policy_sha256": v.policy_hash(structure_policy), "cpdat_archive_sha256": lock["sha256"],
        "input_sha256": {str(p.relative_to(root)): v.digest(p) for p in input_paths},
        "code_sha256": {name: v.digest(v.ROOT/name) for name in ["audit_diversity.py", "validate_structures.py", "audit_cpdat.py"]},
        "output_sha256": {str(p.relative_to(output_root)): v.digest(p) for p in output_paths}})
    print(json.dumps(metrics, indent=2))
    print(f"Read: {reports/'diversity_audit.md'}")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=v.ROOT, help="Project containing step-3 data and reports.")
    parser.add_argument("--archive", type=Path, default=v.ROOT / "data/raw/cpdat_v4.0.zip")
    parser.add_argument("--output-dir", type=Path, default=v.ROOT)
    args = parser.parse_args()
    try:
        run(args.project_dir.resolve(), args.archive.resolve(), args.output_dir.resolve())
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, csv.Error) as exc:
        print(f"Diversity audit stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
