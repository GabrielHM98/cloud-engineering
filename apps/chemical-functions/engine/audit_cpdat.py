#!/usr/bin/env python3
"""Download a pinned CPDat snapshot and audit its functional-use annotations."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import random
import re
import sys
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
POLICY = "functional-use-audit-v1"
REQUIRED = {
    "dtxsid", "function_category", "provisional_dtxsid",
    "data_source", "cpdat_data_document_id",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def verify_archive(path, lock):
    if path.stat().st_size != lock["size_bytes"]:
        raise ValueError(f"Archive size mismatch: {path}. Expected the pinned CPDat v4.0 snapshot.")
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            md5.update(block)
            sha256.update(block)
    if md5.hexdigest() != lock["md5"] or sha256.hexdigest() != lock["sha256"]:
        raise ValueError(f"Archive checksum mismatch: {path}. Preserve it for inspection and use a valid snapshot.")
    return sha256.hexdigest()


def obtain_archive(path, lock):
    if path.exists():
        print("Using cached archive; verifying its contents.", flush=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    request = urllib.request.Request(
        lock["download_url"], headers={"User-Agent": "industrial-use-starter/0.1"}
    )
    received = 0
    print("Downloading pinned CPDat v4.0 archive (~102 MiB).", flush=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            while block := response.read(4 * 1024 * 1024):
                received += len(block)
                if received > lock["size_bytes"]:
                    raise ValueError("The server returned more data than the pinned archive size.")
                output.write(block)
                if received % (16 * 1024 * 1024) == 0:
                    print(f"  {received / 1024**2:.0f} MiB received", flush=True)
        verify_archive(partial, lock)
        partial.replace(path)
        write_json(path.with_name("download_receipt.json"), {
            "downloaded_at_utc": utc_now(), "url": lock["download_url"],
            "archive_sha256": lock["sha256"],
        })
    finally:
        partial.unlink(missing_ok=True)


def member_name(archive, basename):
    matches = [name for name in archive.namelist() if Path(name).name == basename]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one archive member named {basename!r}; found {matches}.")
    return matches[0]


def read_vocabulary(stream):
    reader = csv.DictReader(stream)
    fields = {"function_category", "cpdat_function_category_id"}
    if not fields.issubset(reader.fieldnames or []):
        raise ValueError("Unexpected function vocabulary schema.")
    vocabulary = {}
    for row in reader:
        role = (row.get("function_category") or "").strip()
        if not role or role in vocabulary:
            raise ValueError(f"Missing or repeated function category: {role!r}.")
        vocabulary[role] = (row["cpdat_function_category_id"] or "").strip()
    return vocabulary


def audit_rows(stream, vocabulary):
    reader = csv.DictReader(stream)
    missing = REQUIRED - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"Function-data schema changed; missing columns: {sorted(missing)}")
    counts = Counter()
    flags = Counter()
    raw_ids = set()
    role_ids = defaultdict(set)
    role_sources = defaultdict(set)
    role_documents = defaultdict(set)
    role_rows = Counter()
    excluded_examples = []
    example_counts = Counter()
    total = 0
    for record_number, row in enumerate(reader, start=1):
        total += 1
        sid = (row.get("dtxsid") or "").strip()
        role = (row.get("function_category") or "").strip()
        flag = (row.get("provisional_dtxsid") or "").strip().lower()
        flags[flag or "missing"] += 1
        valid_id = bool(re.fullmatch(r"DTXSID[0-9]+", sid))
        if valid_id:
            raw_ids.add(sid)
        # First failing rule assigns one exclusive disposition to each CSV record.
        if None in row or any(value is None for value in row.values()):
            reason = "malformed_csv_record"
        elif not sid:
            reason = "missing_dtxsid"
        elif not valid_id:
            reason = "malformed_dtxsid"
        elif flag == "true":
            reason = "provisional_identifier"
        elif flag != "false":
            reason = "unknown_provisional_status"
        elif not role:
            reason = "missing_function"
        elif role not in vocabulary:
            reason = "function_not_in_vocabulary"
        else:
            reason = "retained"
            role_ids[role].add(sid)
            role_rows[role] += 1
            if row["data_source"].strip():
                role_sources[role].add(row["data_source"].strip())
            if row["cpdat_data_document_id"].strip():
                role_documents[role].add(row["cpdat_data_document_id"].strip())
        counts[reason] += 1
        if reason != "retained" and example_counts[reason] < 10:
            excluded_examples.append({
                "csv_record_number": record_number, "reason": reason,
                "dtxsid": sid, "function_category": role,
                "provisional_dtxsid": flag,
            })
            example_counts[reason] += 1
    eligible_ids = set().union(*role_ids.values()) if role_ids else set()
    ranking = sorted(role_ids, key=lambda role: (-len(role_ids[role]), role))
    metrics = {
        "policy": POLICY, "raw_records": total,
        "raw_distinct_syntactically_valid_dtxsids": len(raw_ids),
        "raw_provisional_flag_values": dict(sorted(flags.items())),
        "exclusive_row_dispositions": dict(sorted(counts.items())),
        "vocabulary_categories": len(vocabulary),
        "roles_with_retained_annotations": len(role_ids),
        "distinct_retained_substance_ids": len(eligible_ids),
        "distinct_substance_role_pairs": sum(map(len, role_ids.values())),
        "accepted_molecular_structures": None,
        "structure_resolution_status": "pending",
    }
    role_table = [{
        "role": role, "role_id": vocabulary[role],
        "unique_nonprovisional_substance_ids": len(role_ids[role]),
        "retained_annotation_records": role_rows[role],
        "distinct_sources": len(role_sources[role]),
        "distinct_source_documents": len(role_documents[role]),
    } for role in ranking]
    assert sum(counts.values()) == total
    return metrics, role_table, role_ids, excluded_examples


def structure_sample(role_ids, size=100, seed=42):
    """Diagnostic sample balanced across the ten largest roles, then filled globally."""
    rng = random.Random(seed)
    roles = sorted(role_ids, key=lambda role: (-len(role_ids[role]), role))[:10]
    pools = {role: sorted(role_ids[role]) for role in roles}
    for pool in pools.values():
        rng.shuffle(pool)
    selected = []
    seen = set()
    while len(selected) < size and any(pools.values()):
        for role in roles:
            while pools[role] and pools[role][-1] in seen:
                pools[role].pop()
            if pools[role] and len(selected) < size:
                value = pools[role].pop()
                selected.append(value)
                seen.add(value)
    universe = set().union(*role_ids.values()) if role_ids else set()
    remaining = sorted(universe - seen)
    rng.shuffle(remaining)
    return selected + remaining[:max(0, size - len(selected))]


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_ids(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(value + "\n" for value in values), encoding="utf-8")


def render_report(metrics, role_table, lock):
    text = [
        "# CPDat first data audit", "",
        "Status: functional-use annotation audit complete; structure resolution pending.", "",
        f"Source: [{lock['dataset']}, article version {lock['figshare_article_version']}]({lock['article_url']}).",
        "This is a pinned historical snapshot, not a claim about the latest release.", "",
        "## What was measured", "",
        "| Measure | Count |", "|---|---:|",
        f"| Raw CSV records | {metrics['raw_records']:,} |",
        f"| Distinct syntactically valid substance IDs in raw records | {metrics['raw_distinct_syntactically_valid_dtxsids']:,} |",
        f"| Distinct retained nonprovisional substance IDs | {metrics['distinct_retained_substance_ids']:,} |",
        f"| Distinct retained substance–role pairs | {metrics['distinct_substance_role_pairs']:,} |",
        f"| Vocabulary categories | {metrics['vocabulary_categories']:,} |",
        f"| Roles with retained annotations | {metrics['roles_with_retained_annotations']:,} |",
        "| Accepted molecular structures | Not measured yet |", "",
        "Counts refer to substance identifiers, not unique molecular structures. A syntactically valid ID has the expected shape; the audit does not validate its check digit or resolve it in DSSTox.", "",
        "## Row decisions", "",
        "Each CSV record receives the first failing rule in this order: malformed record, missing ID, malformed ID, provisional ID, unknown provisional status, missing role, unknown role; otherwise retained. These exclusive counts reconcile to the raw-record total. They are not independent missingness counts.", "",
        "| Decision | Records |", "|---|---:|",
    ]
    for reason, number in metrics["exclusive_row_dispositions"].items():
        text.append(f"| {reason} | {number:,} |")
    text += [
        "", "## Largest roles by distinct retained substances", "",
        "| Function | Distinct substance IDs | Source documents |",
        "|---|---:|---:|",
    ]
    for row in role_table[:15]:
        text.append(f"| {row['role']} | {row['unique_nonprovisional_substance_ids']:,} | {row['distinct_source_documents']:,} |")
    text += [
        "", "## Next action: resolve structures", "",
        "1. Open https://comptox.epa.gov/dashboard/ and choose Search → Batch Search.",
        "2. Select DSSTox Substance ID and paste data/interim/structure_sample_100.txt.",
        "3. Export the original substance-level SMILES and identifiers as CSV. Keep the original export, its date, and source URL.",
        "4. Join on DTXSID; inspect missing/conflicting mappings and validate structures before merging identities.",
        "5. After the sample establishes the route, resolve the full ID batches and measure coverage by role.", "",
        "The diagnostic sample balances selections across the ten largest roles (seed 42); it is not representative of all chemistry and cannot establish full mapping coverage.", "",
        "## Limits and milestone status", "",
        "This audits recorded functional roles across CPDat sources, not a complete set of industrial-sector labels. No classifier has been fitted. No molecule has yet been accepted as structurally usable. Missing annotations remain unknown, and repeated source records are not independent training molecules. Nonprovisional identifiers are not a guarantee of correct structures or validated application performance.", "",
        "Complete the first feasibility milestone by obtaining structures, documenting the salt/charge/stereochemistry policy, counting accepted unique structures and families by role, and selecting supported functions. Keep failed or excluded mappings with reasons.", "",
        "See README.md for the workflow and data/interim/function_vocabulary.csv for category definitions.", "",
    ]
    return "\n".join(text)


def run(root, archive_path=None):
    lock = json.loads((ROOT / "source-lock.json").read_text(encoding="utf-8"))
    path = archive_path or root / "data" / "raw" / lock["archive_name"]
    if archive_path is not None and not path.is_file():
        raise ValueError(f"Local archive does not exist: {path}")
    if archive_path is None:
        obtain_archive(path, lock)
    sha = verify_archive(path, lock)
    print("Verified checksum. Reading only function data and its vocabulary.", flush=True)
    with zipfile.ZipFile(path) as archive:
        vocab_name = member_name(archive, lock["vocabulary_member"])
        with archive.open(vocab_name) as binary:
            vocabulary_bytes = binary.read()
        vocabulary = read_vocabulary(io.StringIO(vocabulary_bytes.decode("utf-8-sig"), newline=""))
        with archive.open(member_name(archive, lock["functions_member"])) as binary:
            metrics, role_table, role_ids, examples = audit_rows(
                io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""), vocabulary
            )
    interim = root / "data" / "interim"
    reports = root / "reports"
    interim.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    (interim / "function_vocabulary.csv").write_bytes(vocabulary_bytes)
    eligible = sorted(set().union(*role_ids.values())) if role_ids else []
    sample = structure_sample(role_ids)
    write_ids(interim / "dtxsids_all.txt", eligible)
    write_ids(interim / "structure_sample_100.txt", sample)
    batches = interim / "batches"
    batches.mkdir(exist_ok=True)
    for previous in batches.glob("dtxsids_*.txt"):
        previous.unlink()
    for start in range(0, len(eligible), 10000):
        write_ids(batches / f"dtxsids_{start // 10000 + 1:03}.txt", eligible[start:start + 10000])
    write_csv(reports / "role_counts.csv", role_table, [
        "role", "role_id", "unique_nonprovisional_substance_ids", "retained_annotation_records",
        "distinct_sources", "distinct_source_documents",
    ])
    write_csv(reports / "excluded_row_examples.csv", examples, [
        "csv_record_number", "reason", "dtxsid", "function_category", "provisional_dtxsid",
    ])
    write_json(reports / "audit.json", metrics)
    write_json(reports / "source_manifest.json", {
        "source": lock, "archive_sha256": sha, "verified_at_utc": utc_now(),
        "python_version": platform.python_version(), "policy": POLICY,
        "pipeline_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sampling": {"seed": 42, "size": len(sample), "top_roles_balanced": 10},
    })
    (reports / "audit.md").write_text(render_report(metrics, role_table, lock), encoding="utf-8")
    print(f"Retained substance IDs: {len(eligible):,}", flush=True)
    print(f"Roles represented: {len(role_table)}", flush=True)
    print(f"Read: {reports / 'audit.md'}", flush=True)
    print(f"Next: submit {interim / 'structure_sample_100.txt'} to CompTox Batch Search.", flush=True)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use this local pinned archive without downloading.")
    parser.add_argument("--output-dir", type=Path, default=ROOT, help="Output/cache root (default: starter folder).")
    args = parser.parse_args()
    try:
        run(args.output_dir.resolve(), args.archive.resolve() if args.archive else None)
    except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile, csv.Error) as exc:
        print(f"Audit stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
