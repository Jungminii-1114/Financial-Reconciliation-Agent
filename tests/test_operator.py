import sqlite3
import tempfile
import unittest
from pathlib import Path

from _support import Fixtures, case_path, entry_for, reseal
from reconcile_agent.synthetic.cli import build_dataset
from reconcile_agent.synthetic.config import DatasetConfig
from reconcile_agent.synthetic.files import read_json, write_json
from reconcile_agent.synthetic.operator import write_preview
from reconcile_agent.synthetic.snapshot import export_sqlite, read_snapshot, select_case
from reconcile_agent.synthetic.validate import validate_dataset


class OperatorTests(Fixtures, unittest.TestCase):
    def test_sqlite_export_integrity_case_isolation_and_private_exclusion(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "view.sqlite"
            counts = export_sqlite(self.pilot, path)
            self.assertEqual((counts["cases"], counts["internal_rows"]), (10, 30))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(db.execute("SELECT COUNT(DISTINCT case_id) FROM internal_transactions").fetchone()[0], 10)
                names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(names, {"batches", "internal_transactions", "external_settlements", "external_notes", "case_policies", "export_metadata"})
                dump = "\n".join(db.iterdump())
                self.assertNotIn("CANARY_", dump)
                self.assertNotIn("expected_initial_action", dump)
                self.assertNotIn("recovery_effect_truth", dump)
                self.assertNotIn("attack_family", dump)
                self.assertEqual(db.execute("SELECT value FROM export_metadata WHERE key='runtime_executed'").fetchone()[0], "false")

    def test_sqlite_asof_applies_arrival_and_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            entry = entry_for(self.pilot, "delayed_arrival")
            path = Path(temporary) / "after.sqlite"
            counts = export_sqlite(self.pilot, path, "2026-11-05T10:05:00+09:00", entry["case_id"])
            self.assertEqual(counts["cases"], 1)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT input_version,ingestion_complete,as_of FROM batches").fetchone(),
                                 (2, 1, "2026-11-05T10:05:00+09:00"))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM external_settlements").fetchone()[0], 3)

    def test_sqlite_overwrite_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "keep.sqlite"
            path.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                export_sqlite(self.pilot, path)
            self.assertEqual(path.read_bytes(), b"keep")

    def test_export_cannot_pollute_dataset(self):
        with self.assertRaisesRegex(ValueError, "폴더 바깥"):
            export_sqlite(self.pilot, self.pilot / "extra.sqlite")

    def test_unknown_or_traversal_case_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "catalog"):
            select_case(self.pilot, "../../config.json")

    def test_asof_before_initial_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "초기 스냅샷 이전"):
            read_snapshot(self.pilot, select_case(self.pilot), "2026-11-05T09:59:59+09:00")

    def test_html_escapes_untrusted_note(self):
        with self.changed() as root:
            path = case_path(root, "amount_mismatch") / "external_notes.json"
            notes = read_json(path)
            notes[0]["content"] = '<script>alert("hello")</script>'
            write_json(path, notes)
            reseal(root)
            report = validate_dataset(root)
            html = write_preview(root, report).read_text()
            self.assertNotIn("<script>", html)
            self.assertIn("&lt;script&gt;", html)

    def test_built_bundle_and_each_artifact_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            built = build_dataset(root, DatasetConfig(rows_per_batch=3))
            self.assertEqual(validate_dataset(root), built)
            self.assertEqual(read_json(root / "operator_view" / "validation_report.json"), built)
            for name in ("index.html", "initial_snapshot.sqlite", "validation_report.json"):
                with self.subTest(name=name):
                    path = root / "operator_view" / name
                    original = path.read_bytes()
                    path.write_bytes(original + b"tamper")
                    with self.assertRaisesRegex(ValueError, "해시 불일치"):
                        validate_dataset(root)
                    path.write_bytes(original)

    def test_canary_from_another_case_is_also_rejected(self):
        with self.changed() as root:
            value = read_json(case_path(root, "clean", "evaluator_only") / "protected_records.json")["records"][0]["canary"]
            (case_path(root, "amount_mismatch") / "request.txt").write_text(value)
            reseal(root)
            with self.assertRaisesRegex(ValueError, "다른 사례"):
                validate_dataset(root)


if __name__ == "__main__":
    unittest.main()
