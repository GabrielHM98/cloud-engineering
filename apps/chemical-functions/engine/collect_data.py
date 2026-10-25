#!/usr/bin/env python3
"""Capture pinned CPDat files, archive dashboard exports, or collect CTX structures.

Standard library only. This boundary captures source data and transports fields;
chemical parsing, identity decisions and annotation joins remain downstream.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager, redirect_stdout
import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import audit_cpdat as audit

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://comptox.epa.gov/ctx-api/chemical/detail/search/by-dtxsid/"
SID = re.compile(r"DTXSID[0-9]+")
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
CSV_FIELDS = ["INPUT", "FOUND_BY", "DTXSID", "PREFERRED_NAME", "DTXCID", "INCHIKEY", "CASRN", "SMILES", "QC_LEVEL", "QC_NOTES"]
EXPORT_FIELDS = CSV_FIELDS + ["COLLECTION_RAW_SHA256", "COLLECTION_RECORD_NUMBER"]
API_FIELDS = {"DTXSID": "dtxsid", "PREFERRED_NAME": "preferredName", "DTXCID": "dtxcid",
              "INCHIKEY": "inchikey", "CASRN": "casrn", "SMILES": "smiles", "QC_LEVEL": "qcLevel", "QC_NOTES": "qcNotes"}
TRANSIENT = {408, 429, 500, 502, 503, 504}


def sha_bytes(body):
    return hashlib.sha256(body).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def write_atomic(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".collection-", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def json_atomic(path, value):
    write_atomic(path, json.dumps(value, indent=2, ensure_ascii=False).encode()+b"\n")


def immutable_json(path, value):
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError(f"Immutable collection metadata differs: {path.name}.")
    else:
        json_atomic(path, value)


def store_bytes(store, body):
    sha = sha_bytes(body)
    path = store / "objects" / sha
    if path.exists():
        if sha_file(path) != sha:
            raise ValueError("Captured raw object checksum mismatch; existing object was changed.")
    else:
        write_atomic(path, body)
    return sha


def store_file(store, source):
    sha = sha_file(source)
    path = store / "objects" / sha
    if path.exists():
        if sha_file(path) != sha:
            raise ValueError("Captured raw object checksum mismatch.")
        return sha
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".collection-", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        if sha_file(temporary) != sha:
            raise ValueError("Source changed while being captured.")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return sha


@contextmanager
def run_lock(path):
    # WSL/Linux advisory locks are released by the OS if a collector crashes.
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another collector is using this run ID.") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def load_ids(path, limit=None):
    values = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not values or len(set(values)) != len(values) or any(not SID.fullmatch(sid) for sid in values):
        raise ValueError("ID file must contain unique DTXSIDs, one per line, without a header.")
    if limit is not None and limit <= 0:
        raise ValueError("--limit must be positive.")
    return sorted(values)[:limit]


def policy_load():
    policy = json.loads((ROOT / "collection_policy.json").read_text())
    if (policy.get("contract_version") != "chemical-source-collection-v1"
            or policy.get("adapter_version") != "ctx-chemicaldetailall-to-comptox-csv-v1"
            or policy.get("endpoint") != ENDPOINT or policy.get("projection") != "chemicaldetailall"
            or policy.get("api_key_environment_variable") != "CTX_API_KEY"):
        raise ValueError("Unsupported acquisition contract; version the adapter before changing source semantics.")
    checks = {"batch_size": (1, 200), "request_timeout_seconds": (1, 60),
              "minimum_request_interval_seconds": (0, 60), "maximum_attempts_per_batch": (1, 10),
              "maximum_retry_delay_seconds": (0, 60), "maximum_response_bytes": (1, 100*1024*1024)}
    for name, (low, high) in checks.items():
        value = policy.get(name)
        if type(value) not in {int, float} or not low <= value <= high:
            raise ValueError(f"Invalid collection setting: {name}.")
    for name in ["batch_size", "maximum_attempts_per_batch", "maximum_response_bytes"]:
        if type(policy[name]) is not int:
            raise ValueError(f"Collection setting must be an integer: {name}.")
    return policy


def contract_base(mode, policy):
    return {"mode": mode, "policy": policy,
            "collector_sha256": sha_file(Path(__file__)), "archive_audit_sha256": sha_file(ROOT / "audit_cpdat.py")}


def establish_run(store, run_id, contract):
    if not RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("Run ID must be a simple 1–64 character name; paths are not allowed.")
    run = store / "runs" / run_id
    immutable_json(run / "contract.json", contract)
    if "expected_ids" in contract:
        body = ("\n".join(contract["expected_ids"])+"\n").encode()
        path = run / "requested_ids.txt"
        if path.exists() and path.read_bytes() != body:
            raise ValueError("Captured requested-ID list differs from the run contract.")
        if not path.exists():
            write_atomic(path, body)
    return run


def event(run, **values):
    # Deliberately exclude request headers, credentials and provider error bodies.
    record = dict(at_utc=audit.utc_now(), **values)
    with (run / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False)+"\n")


def finish(run, manifest, output_rows=None):
    output_body = None
    if output_rows is not None:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
        output_body = stream.getvalue().encode()
        manifest["structures_csv_sha256"] = sha_bytes(output_body)
    manifest["contract_sha256"] = sha_file(run / "contract.json")
    if (run / "requested_ids.txt").exists():
        manifest["requested_ids_file_sha256"] = sha_file(run / "requested_ids.txt")
    path = run / "manifest.json"
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError("Completed acquisition metadata differs; use a new run ID.")
    if output_body is not None:
        write_atomic(run / "structures.csv", output_body)
    # The final manifest is immutable. A later invocation may verify/replay it.
    immutable_json(run / "manifest.json", manifest)
    summary = {"run_directory": str(run), "manifest_path": str(run / "manifest.json"),
               "collection_status": manifest["collection_status"], "mode": manifest["mode"],
               "raw_objects_count": len(manifest["raw_objects"])}
    for name in ["requested_ids", "export_rows", "matched_requested_ids", "repeated_input_records", "mapping_coverage_complete"]:
        if name in manifest:
            summary[name] = manifest[name]
    if output_rows is not None:
        summary["structures_csv"] = str(run / "structures.csv")
        summary["requested_ids_file"] = str(run / "requested_ids.txt")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return manifest


def coverage(rows, expected):
    wanted = set(expected)
    seen, matched, mismatched = set(), set(), set()
    for row in rows:
        sid, returned = row["INPUT"], row["DTXSID"]
        if sid not in wanted:
            raise ValueError("An export contains INPUT IDs outside the requested list.")
        seen.add(sid)
        if returned == sid:
            matched.add(sid)
        elif returned:
            mismatched.add(sid)
    return {"requested_ids": len(expected), "export_rows": len(rows),
            "requested_input_ids_present": len(seen), "matched_requested_ids": len(matched),
            "missing_requested_input_ids": sorted(wanted-seen),
            "unmatched_or_conflicting_requested_ids": sorted((wanted-matched) | mismatched),
            "repeated_input_records": len(rows)-len(seen),
            "mapping_coverage_complete": matched == wanted and not mismatched}


def capture_cpdat(store, run_id, cache, source_lock):
    policy = policy_load()
    lock = json.loads(source_lock.read_text())
    contract = dict(contract_base("pinned_cpdat_archive", policy), source_lock=lock)
    with run_lock(store / "locks" / f"{safe_run_id(run_id)}.lock"):
        run = establish_run(store, run_id, contract)
        # Public download needs no key; existing streaming/checksum code is reused.
        with redirect_stdout(sys.stderr):
            audit.obtain_archive(cache, lock)
        audit.verify_archive(cache, lock)
        sha = store_file(store, cache)
        if sha != lock["sha256"]:
            raise ValueError("Pinned archive changed after checksum validation; no completed snapshot was published.")
        receipt = {"source_url": lock["download_url"], "raw_sha256": sha,
                   "bytes": cache.stat().st_size, "captured_at_utc": audit.utc_now(),
                   "upstream_retrieval_time": "not_asserted; cached archive may predate this capture"}
        path = run / "archive_receipt.json"
        if path.exists():
            receipt = json.loads(path.read_text())
            if receipt["raw_sha256"] != sha:
                raise ValueError("Pinned archive receipt differs from captured bytes.")
        else:
            immutable_json(path, receipt)
        return finish(run, {"collection_status": "complete", "mode": contract["mode"],
                            "raw_objects": [receipt], "archive_sha256": sha,
                            "source_dataset": lock["dataset"], "source_is_pinned": True})


def safe_run_id(run_id):
    if not RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("Invalid run ID; use a simple name without path separators.")
    return run_id


def archive_csvs(store, run_id, paths, expected, source_url, retrieved_at=None):
    policy = policy_load()
    if retrieved_at is not None:
        parsed = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("--retrieved-at requires a timezone offset or Z.")
    inputs = [{"name": path.name, "sha256": sha_file(path)} for path in paths]
    expected_hashes = {path: info["sha256"] for path, info in zip(paths, inputs)}
    if len({r["sha256"] for r in inputs}) != len(inputs):
        raise ValueError("The same CSV content was supplied more than once.")
    contract = dict(contract_base("dashboard_csv_archive", policy), expected_ids=expected,
                    source_url=source_url, source_retrieved_at=retrieved_at,
                    input_files=sorted(inputs, key=lambda r: (r["sha256"], r["name"])))
    with run_lock(store / "locks" / f"{safe_run_id(run_id)}.lock"):
        run = establish_run(store, run_id, contract)
        rows, receipts = [], []
        for path in sorted(paths, key=lambda p: (sha_file(p), p.name)):
            sha = store_file(store, path)
            if sha != expected_hashes[path]:
                raise ValueError("CSV source changed after the collection contract was established.")
            receipt_path = run / "receipts" / f"{sha}.json"
            receipt = {"source_file_name": path.name, "source_url": source_url,
                       "source_retrieved_at": retrieved_at, "raw_sha256": sha,
                       "archived_at_utc": audit.utc_now()}
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
            else:
                immutable_json(receipt_path, receipt)
            receipts.append(receipt)
            raw = (store / "objects" / sha).read_bytes()
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
            if not set(CSV_FIELDS).issubset(reader.fieldnames or []):
                raise ValueError("Dashboard export is missing required structure/QC columns; original bytes are captured for review.")
            for number, record in enumerate(reader, 1):
                if None in record or any(value is None for value in record.values()):
                    raise ValueError("Malformed dashboard CSV; original bytes are captured for review.")
                item = {field: record[field] for field in CSV_FIELDS}
                item.update(COLLECTION_RAW_SHA256=sha, COLLECTION_RECORD_NUMBER=number)
                rows.append(item)
        stats = coverage(rows, expected)
        return finish(run, {"collection_status": "complete", "mode": contract["mode"],
                            "raw_objects": receipts, **stats}, rows)


def reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key in source response.")
        result[key] = value
    return result


def adapt_ctx(body, requested, raw_sha):
    payload = json.loads(body.decode("utf-8-sig"), object_pairs_hook=reject_duplicate_json_keys)
    if not isinstance(payload, list):
        raise ValueError("Expected a JSON array from the CTX batch details endpoint.")
    wanted = set(requested)
    rows, missing_optional = [], Counter()
    for number, source in enumerate(payload, 1):
        if not isinstance(source, dict) or not {"dtxsid", "smiles", "inchikey"}.issubset(source):
            raise ValueError("CTX schema changed: dtxsid, original smiles and inchikey keys are required (values may be null).")
        sid = source["dtxsid"]
        if not isinstance(sid, str) or sid not in wanted:
            raise ValueError("CTX returned a malformed or unrequested DTXSID.")
        item = {"INPUT": sid, "FOUND_BY": "CTX_API_DTXSID"}
        for field, key in API_FIELDS.items():
            value = source.get(key)
            if key not in source:
                missing_optional[key] += 1
            if value is not None and (type(value) not in {str, int, float} or (key != "qcLevel" and type(value) is not str)):
                raise ValueError(f"Unexpected CTX value type for {key}.")
            item[field] = "" if value is None else str(value)
        item.update(COLLECTION_RAW_SHA256=raw_sha, COLLECTION_RECORD_NUMBER=number)
        rows.append(item)
    return rows, dict(sorted(missing_optional.items()))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(request, timeout, max_bytes):
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        body = response.read(max_bytes+1)
        if len(body) > max_bytes:
            raise ValueError("CTX response exceeds the configured maximum size.")
        if response.status != 200:
            raise ValueError("Unexpected non-200 CTX success response.")
        return body


def retry_delay(error, attempt, policy, now=None):
    delay = min(2**(attempt-1), policy["maximum_retry_delay_seconds"])
    header = error.headers.get("Retry-After") if getattr(error, "headers", None) else None
    if header:
        try:
            wait = max(0, float(header))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(header)
                wait = max(0, parsed.timestamp()-(time.time() if now is None else now))
            except (ValueError, TypeError, OverflowError):
                wait = delay
        if wait > policy["maximum_retry_delay_seconds"]:
            raise ValueError("Provider Retry-After exceeds the local wait budget; leave this run resumable and retry later.")
        delay = max(delay, wait)
    return delay


def request_batch(ids, key, policy, run, batch_number, last_request, sender=transport, sleep=time.sleep, clock=time.monotonic):
    if not 8 <= len(key) <= 4096 or any(not 33 <= ord(char) <= 126 for char in key):
        raise ValueError("CTX_API_KEY has an invalid header format; the value was not logged.")
    url = policy["endpoint"] + "?" + urllib.parse.urlencode({"projection": policy["projection"]})
    request = urllib.request.Request(url, data=canonical_json(ids), method="POST",
                headers={"Accept": "application/json", "Content-Type": "application/json", "x-api-key": key,
                         "User-Agent": "industrial-use-collector/1"})
    for attempt in range(1, policy["maximum_attempts_per_batch"]+1):
        wait = policy["minimum_request_interval_seconds"]-(clock()-last_request[0])
        if wait > 0:
            sleep(wait)
        last_request[0] = clock()
        try:
            body = sender(request, policy["request_timeout_seconds"], policy["maximum_response_bytes"])
            if key.encode() in body:
                raise ValueError("Provider response contains the credential; refusing to archive it.")
            event(run, batch=batch_number, attempt=attempt, outcome="received", http_status=200, response_bytes=len(body))
            return body
        except urllib.error.HTTPError as exc:
            status = exc.code
            # Do not persist or print provider error bodies or request headers.
            event(run, batch=batch_number, attempt=attempt, outcome="http_error", http_status=status)
            if status not in TRANSIENT or attempt == policy["maximum_attempts_per_batch"]:
                raise ValueError(f"CTX request failed with HTTP {status}; no credential or error body was logged.") from None
            delay = retry_delay(exc, attempt, policy)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            event(run, batch=batch_number, attempt=attempt, outcome="transport_error")
            if attempt == policy["maximum_attempts_per_batch"]:
                raise ValueError("CTX transport retries exhausted; rerun the same run ID to resume.") from None
            delay = min(2**(attempt-1), policy["maximum_retry_delay_seconds"])
        sleep(delay)
    raise AssertionError("Unreachable retry state.")


def collect_ctx(store, run_id, expected, policy, key=None, sender=transport, sleep=time.sleep):
    contract = dict(contract_base("ctx_api_structures", policy), expected_ids=expected)
    with run_lock(store / "locks" / f"{safe_run_id(run_id)}.lock"):
        run = establish_run(store, run_id, contract)
        rows, receipts, missing_optional = [], [], Counter()
        last_request = [float("-inf")]
        batches = [expected[i:i+policy["batch_size"]] for i in range(0, len(expected), policy["batch_size"])]
        for number, ids in enumerate(batches, 1):
            receipt_path = run / "receipts" / f"batch-{number:05d}.json"
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if receipt["requested_ids"] != ids:
                    raise ValueError("Cached batch request does not match this run contract.")
                raw = store / "objects" / receipt["raw_sha256"]
                if sha_file(raw) != receipt["raw_sha256"]:
                    raise ValueError("Cached raw response checksum mismatch.")
                body = raw.read_bytes()
            else:
                if not key:
                    raise ValueError("Live CTX collection needs CTX_API_KEY in the environment. Existing completed batches remain resumable.")
                body = request_batch(ids, key, policy, run, number, last_request, sender=sender, sleep=sleep)
                sha = store_bytes(store, body)
                receipt = {"batch": number, "requested_ids": ids, "raw_sha256": sha,
                           "retrieved_at_utc": audit.utc_now(), "http_status": 200,
                           "source_url": ENDPOINT, "projection": policy["projection"], "response_bytes": len(body)}
                immutable_json(receipt_path, receipt)
            # Save the raw bytes and receipt before schema validation for replay.
            batch_rows, optional = adapt_ctx(body, ids, receipt["raw_sha256"])
            rows.extend(batch_rows)
            missing_optional.update(optional)
            receipts.append(receipt)
            print(f"Batch {number}/{len(batches)} available; {len(batch_rows)} source records.", file=sys.stderr, flush=True)
        stats = coverage(rows, expected)
        return finish(run, {"collection_status": "complete", "mode": contract["mode"],
                            "requested_batches": len(batches), "captured_batches": len(receipts),
                            "raw_objects": receipts, "missing_optional_field_occurrences": dict(sorted(missing_optional.items())),
                            "source_database_version": "not supplied by these response records",
                            **stats}, rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["cpdat", "archive-csv", "ctx"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--store", type=Path, default=ROOT / "data/raw/collection")
        cmd.add_argument("--run-id", required=True)
        cmd.add_argument("--allow-incomplete", action="store_true", help="Return 0 for completed acquisition with missing/conflicting identifier coverage; chemistry validation is separate.")
        if name == "cpdat":
            cmd.add_argument("--cache", type=Path, default=ROOT / "data/raw/cpdat_v4.0.zip")
            cmd.add_argument("--source-lock", type=Path, default=ROOT / "source-lock.json")
        else:
            cmd.add_argument("--ids", type=Path, default=ROOT / "data/interim/dtxsids_all.txt")
        if name == "archive-csv":
            cmd.add_argument("--csv", nargs="+", required=True, type=Path)
            cmd.add_argument("--source-url", default="https://comptox.epa.gov/dashboard/batch-search")
            cmd.add_argument("--retrieved-at", help="Known source retrieval timestamp with timezone; filenames/mtimes are not used to invent this.")
        if name == "ctx":
            cmd.add_argument("--limit", type=int, help="Smoke-test the first N sorted requested IDs.")
    args = parser.parse_args()
    try:
        if args.command == "cpdat":
            result = capture_cpdat(args.store.resolve(), args.run_id, args.cache.resolve(), args.source_lock.resolve())
        elif args.command == "archive-csv":
            result = archive_csvs(args.store.resolve(), args.run_id, [p.resolve() for p in args.csv],
                                  load_ids(args.ids), args.source_url, args.retrieved_at)
        else:
            policy = policy_load()
            result = collect_ctx(args.store.resolve(), args.run_id, load_ids(args.ids, args.limit), policy,
                                 key=os.environ.get(policy["api_key_environment_variable"]))
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        # Exception messages from HTTP transport have already been sanitized.
        print(f"Collection stopped: {exc}", file=sys.stderr)
        return 1
    if not args.allow_incomplete and result.get("mapping_coverage_complete") is False:
        print("Acquisition finished, but identifier coverage is incomplete. Review manifest.json; no missing source records were fabricated.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
