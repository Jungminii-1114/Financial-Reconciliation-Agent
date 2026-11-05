import copy
import sqlite3
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from _support import Fixtures, entry_for
from reconcile_agent.business.connection import connect, transaction
from reconcile_agent.business.errors import AdapterError
from reconcile_agent.business.input_digest import action_digest, input_digest, utc_time
from reconcile_agent.business.repository import metadata
from reconcile_agent.synthetic.files import read_json
from reconcile_agent.trials import events
from reconcile_agent.trials.events import advance_trial
from reconcile_agent.trials.lifecycle import prepare_from_dataset, prepare_trial, resume_trial
from reconcile_agent.trials.source import ValidatedDataset


def findings(view):
    # Test-owned calculation from returned rows, not importer or runtime matching code.
    internal = {r["transaction_id"]: r for r in view["internal"]}
    external = view["external"]
    counts = Counter(r["transaction_id"] for r in external)
    return {"missing_ids": sorted(set(internal)-set(counts)), "unknown_ids": sorted(set(counts)-set(internal)),
            "duplicate_ids": sorted(k for k, v in counts.items() if v > 1),
            "amount_mismatch_ids": sorted({r["transaction_id"] for r in external if r["transaction_id"] in internal and r["amount_krw"] != internal[r["transaction_id"]]["amount_krw"]}),
            "status_mismatch_ids": sorted({r["transaction_id"] for r in external if r["transaction_id"] in internal and r["status"] != "SETTLED"}),
            "internal_count": len(internal), "external_count": len(external),
            "internal_total_krw": sum(r["amount_krw"] for r in internal.values()),
            "external_total_krw": sum(r["amount_krw"] for r in external)}


class EventTests(Fixtures, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def prepare(self, recipe, root=None):
        root = root or self.benchmark
        case_id = entry_for(root, recipe)["case_id"]
        return prepare_trial(root, case_id, recipe, Path(self.temp.name) / recipe)

    def assertCode(self, code, function):
        with self.assertRaises(AdapterError) as raised:
            function()
        self.assertEqual(raised.exception.code, code)

    def test_delay_boundaries_idempotent_redelivery_and_past_visibility(self):
        h = self.prepare("delayed_arrival")
        self.assertCode("ASOF_OUT_OF_RANGE", lambda: h.business_repository().snapshot(as_of="2026-11-05T10:05:00+09:00"))
        advance_trial(h, "2026-11-05T10:04:59+09:00")
        self.assertEqual(len(h.business_repository().snapshot()["external"]), 2)
        result = advance_trial(h, "2026-11-05T10:05:00+09:00")
        self.assertEqual(result["applied_group_ids"], ["G001"])
        view = h.business_repository().snapshot()
        self.assertEqual((len(view["external"]), view["batch"]["input_version"]), (3, 2))
        repeated = advance_trial(h, "2026-11-05T10:05:00+09:00")
        self.assertEqual(repeated["applied_group_ids"], [])
        self.assertEqual(repeated["replayed_group_ids"], ["G001"])
        self.assertEqual(repeated["input_version"], 2)
        self.assertEqual(len(h.business_repository().snapshot(as_of="2026-11-05T10:00:00+09:00")["external"]), 2)
        with connect(h.business_db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM input_mutations").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM applied_event_groups").fetchone()[0], 1)
        resume_trial(h.root, h.trial_id, h.case_id)

    def test_two_arrival_groups_are_ordered_and_each_increments_version_once(self):
        h = self.prepare("staggered_arrivals")
        self.assertEqual(len(h.business_repository().snapshot()["external"]), 1)
        advance_trial(h, "2026-11-05T10:02:00+09:00")
        mid = h.business_repository().snapshot()
        self.assertEqual((len(mid["external"]), mid["batch"]["input_version"], mid["batch"]["ingestion_complete"]), (2, 2, False))
        advance_trial(h, "2026-11-05T10:07:00+09:00")
        end = h.business_repository().snapshot()
        self.assertEqual((len(end["external"]), end["batch"]["input_version"], end["batch"]["ingestion_complete"]), (3, 3, True))

    def test_correction_keeps_old_revision_at_old_asof(self):
        h = self.prepare("late_correction")
        before = h.business_repository().snapshot()
        advance_trial(h, "2026-11-05T10:05:00+09:00")
        now = h.business_repository().snapshot()
        self.assertEqual(findings(now)["amount_mismatch_ids"], [])
        self.assertEqual(findings(before)["amount_mismatch_ids"], ["TX000002"])
        self.assertEqual(h.business_repository().snapshot(as_of=before["as_of"])["external"], before["external"])
        with connect(h.business_db) as db:
            revisions = list(db.execute("SELECT row_revision,superseded_known_at FROM settlement_history WHERE transaction_id='TX000002' ORDER BY row_revision"))
            self.assertEqual([r[0] for r in revisions], [1, 2])
            self.assertEqual(revisions[0][1], "2026-11-05T01:05:00.000000+00:00")
            self.assertIsNone(revisions[1][1])

    def test_duplicate_removal_closes_interval_without_erasing_history(self):
        h = self.prepare("duplicate_then_remove")
        advance_trial(h, "2026-11-05T10:05:00+09:00")
        self.assertEqual(len(h.business_repository().snapshot()["external"]), 3)
        self.assertEqual(len(h.business_repository().snapshot(as_of="2026-11-05T10:00:00+09:00")["external"]), 4)
        with connect(h.business_db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM settlement_history").fetchone()[0], 4)

    def test_negative_evidence_growth_adds_both_rows_and_two_mutations(self):
        h = self.prepare("negative_evidence_growth")
        before = h.business_repository().snapshot()
        self.assertNotIn("TX_FUTURE", [r["transaction_id"] for r in before["external"]])
        advance_trial(h, "2026-11-05T10:05:00+09:00")
        after = h.business_repository().snapshot()
        self.assertIn("TX_FUTURE", [r["transaction_id"] for r in after["external"]])
        with connect(h.business_db) as db:
            records = list(db.execute("SELECT old_version,new_version,affected_ids FROM input_mutations"))
            self.assertEqual(len(records), 2)
            self.assertTrue(all((r[0], r[1]) == (1, 2) for r in records))
            self.assertTrue(all("TX_FUTURE" in r[2] for r in records))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_whole_event_group_rolls_back_on_midgroup_failure(self):
        h = self.prepare("negative_evidence_growth")
        before = h.business_db.read_bytes()
        original = events.insert_financial_row
        def interrupted(db, case_id, row, *, external):
            if external:
                raise sqlite3.IntegrityError("injected second insert failure")
            return original(db, case_id, row, external=external)
        with patch.object(events, "insert_financial_row", side_effect=interrupted):
            self.assertCode("STORAGE_ERROR", lambda: advance_trial(h, "2026-11-05T10:05:00+09:00"))
        self.assertEqual(h.business_db.read_bytes(), before)
        self.assertEqual(h.business_repository().snapshot()["batch"]["input_version"], 1)
        self.assertEqual(advance_trial(h, "2026-11-05T10:05:00+09:00")["input_version"], 2)

    def test_same_event_id_different_payload_and_wrong_version_are_rejected(self):
        h = self.prepare("delayed_arrival")
        groups = read_json(self.benchmark / "cases" / h.case_id / "controller_only" / "scheduled_events.json")
        bad = copy.deepcopy(groups[0])
        bad["expected_input_version"] = 8
        with connect(h.business_db, writable=True) as db:
            self.assertCode("EVENT_VERSION_CONFLICT", lambda: self._in_transaction(db, bad))
        advance_trial(h, "2026-11-05T10:05:00+09:00")
        bad = copy.deepcopy(groups[0])
        bad["events"][0]["row"]["amount_krw"] += 1
        with connect(h.business_db, writable=True) as db:
            self.assertCode("EVENT_PAYLOAD_CONFLICT", lambda: self._in_transaction(db, bad))

    @staticmethod
    def _in_transaction(db, group):
        with transaction(db, write=True):
            events._apply_group(db, group, metadata(db))

    def test_clock_cannot_rewind_or_query_before_initial(self):
        h = self.prepare("clean")
        self.assertCode("CLOCK_BACKWARDS", lambda: advance_trial(h, "2026-11-05T09:59:59+09:00"))
        self.assertCode("ASOF_OUT_OF_RANGE", lambda: h.business_repository().snapshot(as_of="2026-11-05T09:59:59+09:00"))
        self.assertCode("INVALID_TIMESTAMP", lambda: advance_trial(h, "2026-11-05T10:01:00"))

    def test_concurrent_redelivery_applies_once(self):
        h = self.prepare("delayed_arrival")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: advance_trial(h, "2026-11-05T10:05:00+09:00"), range(2)))
        self.assertEqual(sum(len(r["applied_group_ids"]) for r in results), 1)
        self.assertEqual(h.business_repository().snapshot()["batch"]["input_version"], 2)

    def test_busy_writer_is_bounded_and_never_advances_clock(self):
        h = self.prepare("delayed_arrival")
        with sqlite3.connect(h.business_db, isolation_level=None) as holder:
            holder.execute("BEGIN IMMEDIATE")
            self.assertCode("SQLITE_BUSY", lambda: advance_trial(h, "2026-11-05T10:05:00+09:00"))
            holder.rollback()
        self.assertEqual(h.business_repository().snapshot()["batch"]["input_version"], 1)

    def test_resume_rejects_tampered_event_receipt_or_clock_skipping_due_event(self):
        h = self.prepare("delayed_arrival")
        with sqlite3.connect(h.business_db) as db:
            db.execute("UPDATE trial_metadata SET value='2026-11-05T01:05:00.000000+00:00' WHERE key='current_time'")
        self.assertCode("IMPORT_INVARIANT_ERROR", lambda: resume_trial(h.root, h.trial_id, h.case_id))
        with sqlite3.connect(h.business_db) as db:
            db.execute("UPDATE trial_metadata SET value='2026-11-05T01:00:00.000000+00:00' WHERE key='current_time'")
        advance_trial(h, "2026-11-05T10:05:00+09:00")
        with sqlite3.connect(h.business_db) as db:
            db.execute("UPDATE applied_event_groups SET group_payload_digest='changed'")
        self.assertCode("IMPORT_INVARIANT_ERROR", lambda: resume_trial(h.root, h.trial_id, h.case_id))

    def test_digest_binding_and_timezone_equivalence(self):
        h = self.prepare("clean")
        view = h.business_repository().snapshot()
        args = (h.case_id, h.batch_id, "krw-1to1-v1", view["internal"], view["external"], True)
        digest = input_digest(*args)
        shifted = copy.deepcopy(view)
        for row in shifted["internal"]+shifted["external"]:
            from datetime import datetime, timezone, timedelta
            for field in ("event_time", "known_at"):
                row[field] = datetime.fromisoformat(row[field]).astimezone(timezone(timedelta(hours=9))).isoformat()
        self.assertEqual(input_digest(h.case_id, h.batch_id, "krw-1to1-v1", shifted["internal"], shifted["external"], True), digest)
        self.assertNotEqual(input_digest("c_000000000000", *args[1:]), digest)
        self.assertNotEqual(action_digest(h.case_id,h.batch_id,1,digest,"krw-1to1-v1","mock-policy-v1"),
                            action_digest(h.case_id,h.batch_id,2,digest,"krw-1to1-v1","mock-policy-v1"))

    def test_all_benchmark_cases_and_past_checkpoints_round_trip(self):
        # All 98 conditions use one validated immutable snapshot. Expected labels are
        # read only by this test; the importer sees no expected action for mapping.
        checked_cases = checked_times = 0
        with ValidatedDataset(self.benchmark) as dataset:
            for i, entry in enumerate(dataset.entries):
                with self.subTest(case=entry["case_id"]):
                    h = prepare_from_dataset(dataset, entry["case_id"], f"matrix-{i}", Path(self.temp.name) / f"matrix-{i}")
                    expected = read_json(self.benchmark / "cases" / entry["case_id"] / "evaluator_only" / "expected.json")
                    advance_trial(h, expected["checkpoints"][-1]["at"])
                    for checkpoint in expected["checkpoints"]:
                        view = h.business_repository().snapshot(as_of=checkpoint["at"])
                        self.assertEqual(findings(view), checkpoint["findings"])
                        self.assertEqual(view["batch"]["input_version"], checkpoint["input_version"])
                        checked_times += 1
                    resume_trial(h.root, h.trial_id, h.case_id)
                    checked_cases += 1
        self.assertEqual((checked_cases, checked_times), (98, 107))


if __name__ == "__main__":
    unittest.main()
