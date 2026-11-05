import unittest
import tempfile
from pathlib import Path

from _support import Fixtures, case_path, entry_for
from reconcile_agent.synthetic.config import DatasetConfig
from reconcile_agent.synthetic.files import AGENT_FILES, read_json
from reconcile_agent.synthetic.generator import generate_dataset
from reconcile_agent.synthetic.snapshot import read_snapshot
from reconcile_agent.synthetic.validate import validate_dataset


class GenerationTests(Fixtures, unittest.TestCase):
    def test_pilot_and_candidate_benchmark_are_valid(self):
        pilot, benchmark = validate_dataset(self.pilot), validate_dataset(self.benchmark)
        self.assertEqual((pilot["validated_cases"], pilot["security_pairs_checked"], pilot["checkpoints_checked"]), (10, 2, 11))
        self.assertEqual((benchmark["validated_cases"], benchmark["security_pairs_checked"], benchmark["checkpoints_checked"]), (98, 40, 107))
        self.assertNotIn("core:held_out", pilot["counts"])
        self.assertEqual(benchmark["counts"]["core:held_out"], 12)
        self.assertFalse(benchmark["runtime_executed"])

    def test_same_seed_code_and_config_reproduce_all_core_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "copy"
            generated = generate_dataset(target, DatasetConfig(rows_per_batch=3))
            original = read_json(self.pilot / "manifest.json")
            self.assertEqual(generated, original)
            for name in original["file_sha256"]:
                self.assertEqual((target / name).read_bytes(), (self.pilot / name).read_bytes())

    def test_seed_changes_records_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "different"
            generated = generate_dataset(target, DatasetConfig(seed=43, rows_per_batch=3))
            self.assertNotEqual(generated["dataset_fingerprint"], read_json(self.pilot / "manifest.json")["dataset_fingerprint"])
            self.assertNotEqual((case_path(target, "clean") / "internal.csv").read_bytes(),
                                (case_path(self.pilot, "clean") / "internal.csv").read_bytes())

    def test_before_and_after_commit_have_identical_agent_input(self):
        for name in AGENT_FILES:
            self.assertEqual((case_path(self.pilot, "before_commit") / name).read_bytes(),
                             (case_path(self.pilot, "after_commit") / name).read_bytes())
        before = read_json(case_path(self.pilot, "before_commit", "evaluator_only") / "expected.json")
        after = read_json(case_path(self.pilot, "after_commit", "evaluator_only") / "expected.json")
        self.assertNotEqual(before["recovery_obligation"], after["recovery_obligation"])

    def test_arrival_boundary_is_inclusive_and_atomic(self):
        entry = entry_for(self.pilot, "delayed_arrival")
        before = read_snapshot(self.pilot, entry, "2026-11-05T10:04:59+09:00")
        after = read_snapshot(self.pilot, entry, "2026-11-05T10:05:00+09:00")
        self.assertEqual((len(before["external"]), before["batch"]["input_version"], before["batch"]["ingestion_complete"]), (2, 1, False))
        self.assertEqual((len(after["external"]), after["batch"]["input_version"], after["batch"]["ingestion_complete"]), (3, 2, True))

    def test_staggered_arrivals_require_both_events(self):
        entry = entry_for(self.benchmark, "staggered_arrivals")
        middle = read_snapshot(self.benchmark, entry, "2026-11-05T10:02:00+09:00")
        last = read_snapshot(self.benchmark, entry, "2026-11-05T10:07:00+09:00")
        self.assertEqual((len(middle["external"]), middle["batch"]["input_version"], middle["batch"]["ingestion_complete"]), (2, 2, False))
        self.assertEqual((len(last["external"]), last["batch"]["input_version"]), (3, 3))

    def test_correction_keeps_historical_snapshot_and_updates_revision(self):
        entry = entry_for(self.benchmark, "late_correction")
        before, after = read_snapshot(self.benchmark, entry), read_snapshot(self.benchmark, entry, "2026-11-05T10:05:00+09:00")
        self.assertNotEqual(before["external"][1]["amount_krw"], after["external"][1]["amount_krw"])
        self.assertEqual((before["external"][1]["row_revision"], after["external"][1]["row_revision"]), (1, 2))
        self.assertEqual(read_snapshot(self.benchmark, entry)["external"], before["external"])

    def test_previously_absent_transaction_becomes_present(self):
        entry = entry_for(self.benchmark, "negative_evidence_growth")
        initial = read_snapshot(self.benchmark, entry)
        final = read_snapshot(self.benchmark, entry, "2026-11-05T10:05:00+09:00")
        self.assertFalse(any(r["transaction_id"] == "TX_FUTURE" for r in initial["internal"]))
        self.assertTrue(any(r["transaction_id"] == "TX_FUTURE" for r in final["internal"]))
        self.assertNotEqual(initial["input_digest"], final["input_digest"])

    def test_existing_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "existing"
            target.mkdir()
            (target / "keep.txt").write_text("keep")
            with self.assertRaises(FileExistsError):
                generate_dataset(target, DatasetConfig(rows_per_batch=3))
            self.assertEqual((target / "keep.txt").read_text(), "keep")

    def test_invalid_configs_are_rejected(self):
        for values in ({"seed": True}, {"seed": -1}, {"rows_per_batch": 2}, {"rows_per_batch": 5001},
                       {"rows_per_batch": True}, {"profile": "train"}, {"decision_at": "2026-11-05T10:00:00"},
                       {"security_pairs_per_split": 1}, {"unknown": 2}, {"seed": 2**63}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                DatasetConfig.from_dict(values)


if __name__ == "__main__":
    unittest.main()
