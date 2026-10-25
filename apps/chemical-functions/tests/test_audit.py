import csv
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from audit_cpdat import REQUIRED, audit_rows, structure_sample, verify_archive


def table(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=sorted(REQUIRED))
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    return output


def record(sid="DTXSID12345", role="Solvent", flag="False", source="source", document="doc"):
    return dict(dtxsid=sid, function_category=role, provisional_dtxsid=flag,
                data_source=source, cpdat_data_document_id=document)


class AuditTests(unittest.TestCase):
    def test_repeated_evidence_does_not_inflate_unique_substances(self):
        rows = [record(), record(), record(source="other", document="doc2"), record(role="Surfactant")]
        metrics, roles, _, _ = audit_rows(table(rows), {"Solvent":"1", "Surfactant":"2"})
        self.assertEqual(metrics["distinct_retained_substance_ids"], 1)
        self.assertEqual(metrics["distinct_substance_role_pairs"], 2)
        solvent = next(r for r in roles if r["role"] == "Solvent")
        self.assertEqual(solvent["retained_annotation_records"], 3)
        self.assertEqual(solvent["unique_nonprovisional_substance_ids"], 1)
        self.assertEqual(solvent["distinct_sources"], 2)

    def test_unknown_flags_and_provisional_ids_are_not_accepted(self):
        rows = [record(), record(sid="", flag="False"), record(sid="DTXSID23456", flag="True"),
                record(sid="DTXSID34567", flag=""), record(sid="DTXSID45678", role=""),
                record(sid="bad"), record(sid="DTXSID56789", role="Unmapped")]
        metrics, _, role_ids, _ = audit_rows(table(rows), {"Solvent":"1"})
        self.assertEqual(set(role_ids["Solvent"]), {"DTXSID12345"})
        self.assertEqual(sum(metrics["exclusive_row_dispositions"].values()), len(rows))
        self.assertEqual(metrics["exclusive_row_dispositions"]["provisional_identifier"], 1)
        self.assertIsNone(metrics["accepted_molecular_structures"])

    def test_missing_schema_fails_instead_of_producing_empty_report(self):
        with self.assertRaisesRegex(ValueError, "schema changed"):
            audit_rows(io.StringIO("name,role\nexample,Solvent\n"), {"Solvent":"1"})

    def test_bad_csv_width_is_quarantined_and_quoted_newline_is_valid(self):
        good = table([record(source="first\nsecond")]).getvalue()
        stream = io.StringIO(good + "too,few,columns\n")
        metrics, _, _, _ = audit_rows(stream, {"Solvent":"1"})
        self.assertEqual(metrics["raw_records"], 2)
        self.assertEqual(metrics["exclusive_row_dispositions"]["retained"], 1)
        self.assertEqual(metrics["exclusive_row_dispositions"]["malformed_csv_record"], 1)

    def test_sample_has_unique_ids_is_order_independent_and_never_uses_unknown_ids(self):
        one = {"Solvent": {f"DTXSID{x}" for x in range(100, 230)},
               "Surfactant": {f"DTXSID{x}" for x in range(200, 290)}}
        two = dict(reversed(list(one.items())))
        sample = structure_sample(one)
        self.assertEqual(sample, structure_sample(two))
        self.assertEqual(len(sample), 100)
        self.assertEqual(len(set(sample)), 100)
        self.assertTrue(set(sample).issubset(set.union(*one.values())))
        self.assertEqual(structure_sample({}), [])

    def test_cached_archive_tampering_is_detected(self):
        data = b"original"
        lock = {"size_bytes":len(data), "md5":hashlib.md5(data).hexdigest(),
                "sha256":hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "archive.zip"
            p.write_bytes(data)
            self.assertEqual(verify_archive(p, lock), lock["sha256"])
            p.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_archive(p, lock)


if __name__ == "__main__":
    unittest.main()
