import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from _support import Fixtures, ROOT, entry_for, reseal
from reconcile_agent.business.connection import connect
from reconcile_agent.business.errors import AdapterError
from reconcile_agent.business.input_digest import utc_time
from reconcile_agent.business.repository import BusinessRepository, BusinessScope
from reconcile_agent.synthetic.files import core_hashes, read_json, write_json
from reconcile_agent.trials import lifecycle
from reconcile_agent.trials.contracts import RunnerConfig
from reconcile_agent.trials.lifecycle import prepare_from_dataset, prepare_trial, resume_trial
from reconcile_agent.trials.source import ValidatedDataset


class ImportTests(Fixtures, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name) / "trial"
        self.addCleanup(self.temp.cleanup)

    def prepare(self, recipe="clean", *, root=None, out=None, trial_id="trial-1", config=RunnerConfig()):
        root = root or self.pilot
        case_id = entry_for(root, recipe)["case_id"]
        return prepare_trial(root, case_id, trial_id, out or self.out, config)

    def assertCode(self, code, call):
        with self.assertRaises(AdapterError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)

    def test_clean_round_trip_and_empty_execution_tables(self):
        before = core_hashes(self.pilot)
        h = self.prepare()
        view = h.business_repository().snapshot()
        self.assertEqual((len(view["internal"]), len(view["external"])), (3, 3))
        self.assertEqual(view["batch"]["input_version"], 1)
        self.assertIs(view["batch"]["ingestion_complete"], True)
        self.assertEqual(view["processing_state_current"], "FAILED")
        self.assertIs(view["policy"]["approval_required"], True)
        self.assertEqual(view["internal"][0]["known_at"], utc_time(view["internal"][0]["known_at"]))
        with connect(h.business_db) as db:
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            for name in ("approvals", "operations", "reconciliation_results", "completion_decisions"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0], 0)
        self.assertEqual(core_hashes(self.pilot), before)
        self.assertTrue((self.out / "operator" / "READY").is_file())
        self.assertEqual(set(h.agent_admission()), {"trial_id", "case_id", "batch_id"})

    def test_delayed_input_remains_incomplete(self):
        h = self.prepare("delayed_arrival")
        view = h.business_repository().snapshot()
        self.assertEqual((len(view["internal"]), len(view["external"])), (3, 2))
        self.assertIs(view["batch"]["ingestion_complete"], False)
        self.assertNotIn("TX000003", [r["transaction_id"] for r in view["external"]])

    def test_discrepancies_are_preserved_not_repaired(self):
        for recipe in ("amount_mismatch", "amount_swap", "duplicate_then_remove", "unknown_external", "unsettled_status", "missing_complete"):
            with self.subTest(recipe=recipe):
                h = self.prepare(recipe, root=self.benchmark, out=Path(self.temp.name) / recipe, trial_id=recipe)
                view = h.business_repository().snapshot()
                inside = {r["transaction_id"]: r["amount_krw"] for r in view["internal"]}
                errors = [r for r in view["external"] if r["transaction_id"] in inside and r["amount_krw"] != inside[r["transaction_id"]]]
                if recipe == "amount_mismatch":
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0]["amount_krw"] - inside[errors[0]["transaction_id"]], 100)
                elif recipe == "amount_swap":
                    self.assertEqual(len(errors), 2)
                    self.assertEqual(sum(inside.values()), sum(r["amount_krw"] for r in view["external"]))
                elif recipe == "duplicate_then_remove":
                    self.assertEqual(len(view["external"]), 4)
                    self.assertEqual(len({r["transaction_id"] for r in view["external"]}), 3)
                elif recipe == "unknown_external":
                    self.assertIn("TX_UNKNOWN", [r["transaction_id"] for r in view["external"]])
                elif recipe == "unsettled_status":
                    self.assertIn("PENDING", [r["status"] for r in view["external"]])
                else:
                    self.assertEqual(len(view["external"]), 2)
                    self.assertIs(view["batch"]["ingestion_complete"], True)

    def test_pre_and_postcommit_plans_do_not_prepopulate_effects(self):
        views = []
        for recipe in ("before_commit", "after_commit"):
            h = self.prepare(recipe, out=Path(self.temp.name) / recipe, trial_id=recipe)
            views.append(h.business_repository().snapshot())
            with connect(h.business_db) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 0)
                dump = "\n".join(db.iterdump())
                for marker in ("after_commit_before_response", "recovery_effect_truth", "CANARY_", '"is_attack"'):
                    self.assertNotIn(marker, dump)
        self.assertEqual(views[0]["internal"], views[1]["internal"])
        self.assertEqual(views[0]["external"], views[1]["external"])

    def test_security_pair_keeps_finance_and_exact_untrusted_note(self):
        with ValidatedDataset(self.pilot) as dataset:
            pair = [e for e in dataset.entries if e["suite"] == "security"][:1][0]["pair_id"]
            snapshots = []
            for i, e in enumerate(e for e in dataset.entries if e["pair_id"] == pair):
                h = prepare_from_dataset(dataset, e["case_id"], f"pair-{i}", Path(self.temp.name) / f"pair-{i}")
                view = h.business_repository().snapshot()
                raw = read_json(self.pilot / "cases" / e["case_id"] / "agent_view" / "external_notes.json")
                self.assertEqual(view["external_notes"][0]["content"], raw[0]["content"])
                self.assertEqual(view["external_notes"][0]["origin"], "external_settlement_note")
                self.assertIs(view["policy"]["approval_required"], True)
                snapshots.append(view)
        self.assertEqual(snapshots[0]["internal"], snapshots[1]["internal"])
        self.assertEqual(snapshots[0]["external"], snapshots[1]["external"])
        self.assertNotEqual(snapshots[0]["external_notes"][0]["content"], snapshots[1]["external_notes"][0]["content"])

    def test_future_and_private_material_stay_out_of_business(self):
        h = self.prepare("negative_evidence_growth", root=self.benchmark)
        with connect(h.business_db) as db:
            dump = "\n".join(db.iterdump())
        for marker in ("TX_FUTURE", "CANARY_", "expected_initial_action", "expected_action", "attack_family", "scheduled_events", "PLAN_ONLY"):
            self.assertNotIn(marker, dump)
        for role, path in (("controller", h.controller_db), ("evaluator", h.evaluator_db)):
            with connect(path) as db:
                self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='role'").fetchone()[0], role)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 3)

    def test_duplicate_prepare_never_overwrites_existing_trial(self):
        h = self.prepare()
        before = h.business_db.read_bytes()
        self.assertCode("TRIAL_ALREADY_EXISTS", lambda: self.prepare())
        self.assertEqual(h.business_db.read_bytes(), before)

    def test_symlink_output_and_source_are_rejected(self):
        target = Path(self.temp.name) / "target"
        target.mkdir()
        self.out.symlink_to(target, target_is_directory=True)
        self.assertCode("TRIAL_ALREADY_EXISTS", lambda: self.prepare())
        with self.changed() as root:
            path = root / "data_card.md"
            path.unlink()
            path.symlink_to(self.pilot / "data_card.md")
            self.assertCode("FIXTURE_INTEGRITY_ERROR", lambda: self.prepare(root=root, out=Path(self.temp.name) / "source-link"))

    def test_output_cannot_pollute_source(self):
        self.assertCode("UNSAFE_PATH", lambda: self.prepare(out=self.pilot / "trial-output"))
        self.assertFalse((self.pilot / "trial-output").exists())

    def test_unknown_case_and_path_traversal_are_rejected(self):
        self.assertCode("CASE_NOT_FOUND", lambda: prepare_trial(self.pilot, "c_000000000000", "trial-1", self.out))
        self.assertCode("INVALID_ARGUMENT", lambda: prepare_trial(self.pilot, "../../catalog.json", "trial-1", self.out))

    def test_unsupported_version_and_semantically_invalid_source_fail_before_publication(self):
        with self.changed() as root:
            manifest = read_json(root / "manifest.json")
            manifest["schema_version"] = "future-999"
            write_json(root / "manifest.json", manifest)
            self.assertCode("UNSUPPORTED_FIXTURE_VERSION", lambda: self.prepare(root=root))
            self.assertFalse(self.out.exists())
        with self.changed() as root:
            path = root / "cases" / entry_for(root, "clean")["case_id"] / "evaluator_only" / "expected.json"
            value = read_json(path)
            value["expected_initial_action"] = "WAIT"
            write_json(path, value)
            reseal(root)
            self.assertCode("FIXTURE_INTEGRITY_ERROR", lambda: self.prepare(root=root))
            self.assertFalse(self.out.exists())

    def test_verified_snapshot_is_used_if_original_changes(self):
        with self.changed() as root, ValidatedDataset(root) as dataset:
            case_id = entry_for(root, "clean")["case_id"]
            original = (root / "cases" / case_id / "agent_view" / "internal.csv")
            original.write_bytes(b"changed after snapshot")
            h = prepare_from_dataset(dataset, case_id, "snapshot", self.out)
            self.assertEqual(len(h.business_repository().snapshot()["internal"]), 3)

    def test_snapshot_mutation_after_validation_is_rejected(self):
        with ValidatedDataset(self.pilot) as dataset:
            case_id = entry_for(self.pilot, "clean")["case_id"]
            (dataset.root / "cases" / case_id / "agent_view" / "request.txt").write_text("changed")
            self.assertCode("FIXTURE_INTEGRITY_ERROR", lambda: prepare_from_dataset(dataset, case_id, "changed", self.out))
            self.assertFalse(self.out.exists())

    def test_independent_readback_catches_mapping_error_and_rolls_back(self):
        original = lifecycle.insert_financial_row
        def wrong_mapping(db, case_id, row, *, external):
            original(db, case_id, {**row, "amount_krw": row["amount_krw"]+1} if external else row, external=external)
        with patch.object(lifecycle, "insert_financial_row", side_effect=wrong_mapping):
            self.assertCode("IMPORT_INVARIANT_ERROR", lambda: self.prepare())
        self.assertFalse(self.out.exists())
        self.assertEqual(list(Path(self.temp.name).glob(".trial-stage-*")), [])

    def test_scope_and_runner_config_mismatch_return_no_data(self):
        h = self.prepare()
        bad = BusinessScope(h.principal_id, h.trial_id, "c_000000000000", h.batch_id)
        self.assertCode("SCOPE_DENIED", lambda: BusinessRepository(h.business_db, bad).snapshot())
        self.assertCode("SCOPE_DENIED", lambda: resume_trial(h.root, "wrong-id", h.case_id))
        self.assertCode("SCOPE_DENIED", lambda: resume_trial(h.root, h.trial_id, h.case_id, RunnerConfig(setting="H")))

    def test_normal_resume_is_read_only_and_missing_db_is_not_recreated(self):
        h = self.prepare()
        before = h.business_db.read_bytes()
        restored = resume_trial(h.root, h.trial_id, h.case_id)
        self.assertEqual(restored.business_db.read_bytes(), before)
        h.business_db.unlink()
        self.assertCode("TRIAL_MISSING", lambda: resume_trial(h.root, h.trial_id, h.case_id))
        self.assertFalse(h.business_db.exists())

    def test_resume_preserves_committed_operation_and_approval_state(self):
        h = self.prepare()
        view = h.business_repository().snapshot()
        with sqlite3.connect(h.business_db) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       ("a-1", h.case_id, h.batch_id, 1, view["batch"]["input_digest"], "action-1", "mock-policy-v1", "human", "2026-11-05T02:00:00.000000+00:00", "APPROVED", None))
            db.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       ("op-1", "idem-1", h.case_id, h.batch_id, 1, "REPROCESS", "action-1", "a-1", "SUCCEEDED", "result-1", "2026-11-05T01:00:00.000000+00:00"))
            db.execute("UPDATE approvals SET status='CONSUMED',consumed_by='op-1'")
            db.execute("UPDATE batch_runtime SET run_status='SUCCEEDED',last_operation_id='op-1'")
        before = h.business_db.read_bytes()
        resume_trial(h.root, h.trial_id, h.case_id)
        self.assertEqual(h.business_db.read_bytes(), before)
        with connect(h.business_db) as db:
            self.assertEqual(tuple(db.execute("SELECT status,consumed_by FROM approvals").fetchone()), ("CONSUMED", "op-1"))
        # This is preservation of seeded storage state, not execution of a business service.

    def test_schema_changes_and_swapped_private_store_are_rejected(self):
        h = self.prepare()
        original = h.business_db.read_bytes()
        with sqlite3.connect(h.business_db) as db:
            db.execute("PRAGMA user_version=99")
        self.assertCode("BUSINESS_SCHEMA_MISMATCH", lambda: resume_trial(h.root, h.trial_id, h.case_id))
        with sqlite3.connect(h.business_db) as db:
            db.execute("PRAGMA user_version=1")
            db.execute("DROP INDEX current_external")
        self.assertCode("BUSINESS_SCHEMA_MISMATCH", lambda: resume_trial(h.root, h.trial_id, h.case_id))
        h.business_db.write_bytes(original)
        shutil.copyfile(h.evaluator_db, h.controller_db)
        self.assertCode("TRIAL_IDENTITY_MISMATCH", lambda: lifecycle._validate_bundle(h.root, read_json(h.root / "operator" / "trial_manifest.json")))

    def test_tampered_immutable_artifact_blocks_resume(self):
        h = self.prepare()
        path = h.root / "operator" / "source" / "agent_view" / "request.txt"
        path.write_text("changed")
        self.assertCode("TRIAL_NOT_READY", lambda: resume_trial(h.root, h.trial_id, h.case_id))

    def test_modified_import_receipt_is_rejected(self):
        h = self.prepare()
        with sqlite3.connect(h.business_db) as db:
            db.execute("UPDATE import_receipts SET row_counts='{}'")
        self.assertCode("IMPORT_INVARIANT_ERROR", lambda: resume_trial(h.root, h.trial_id, h.case_id))

    def test_modified_business_content_and_unversioned_policy_are_rejected(self):
        h = self.prepare()
        with sqlite3.connect(h.business_db) as db:
            db.execute("UPDATE content_objects SET content='changed' WHERE origin='external_settlement_note'")
        self.assertCode("CONTENT_INTEGRITY_ERROR", lambda: h.business_repository().snapshot())
        self.assertCode("CONTENT_INTEGRITY_ERROR", lambda: resume_trial(h.root, h.trial_id, h.case_id))
        h2 = self.prepare(out=Path(self.temp.name) / "policy", trial_id="policy")
        with sqlite3.connect(h2.business_db) as db:
            db.execute("UPDATE case_policies SET approval_ttl_seconds=123")
        self.assertCode("IMPORT_INVARIANT_ERROR", lambda: resume_trial(h2.root, h2.trial_id, h2.case_id))

    def test_database_rejects_invalid_amount_active_revision_and_case_fk(self):
        h = self.prepare()
        with sqlite3.connect(h.business_db) as db:
            db.execute("PRAGMA foreign_keys=ON")
            for sql in ("UPDATE transaction_history SET amount_krw=-1", "UPDATE transaction_history SET amount_krw=1.5",
                        "UPDATE transaction_history SET case_id='c_000000000000'"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("""INSERT INTO transaction_history SELECT case_id,batch_id,transaction_id,row_revision+1,
                    amount_krw,currency,status,event_time,known_at,NULL FROM transaction_history LIMIT 1""")

    def test_same_source_new_trial_has_same_projection_and_independent_history(self):
        first = self.prepare()
        second = self.prepare(out=Path(self.temp.name) / "second", trial_id="second")
        one = read_json(first.root / "operator" / "import_report.json")
        two = read_json(second.root / "operator" / "import_report.json")
        self.assertEqual(one["initial_input_digest"], two["initial_input_digest"])
        self.assertEqual(one["financial_projection_digest"], two["financial_projection_digest"])
        self.assertNotEqual(first.business_db, second.business_db)
        if os.name == "posix":
            self.assertEqual(first.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(first.business_db.stat().st_mode & 0o777, 0o600)

    def test_concurrent_prepare_reserves_destination_once(self):
        with ValidatedDataset(self.pilot) as dataset:
            case_id = entry_for(self.pilot, "clean")["case_id"]
            def create(index):
                try:
                    return prepare_from_dataset(dataset, case_id, f"race-{index}", self.out).trial_id
                except AdapterError as exc:
                    return exc.code
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(create, range(2)))
        self.assertEqual(results.count("TRIAL_ALREADY_EXISTS"), 1)
        winner = next(x for x in results if x != "TRIAL_ALREADY_EXISTS")
        resume_trial(self.out, winner, case_id)

    def test_actual_process_exit_before_ready_leaves_nonresumable_bundle(self):
        case_id = entry_for(self.pilot, "clean")["case_id"]
        code = f'''
import os,sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'src')!r})
from reconcile_agent.trials import lifecycle
old = lifecycle._write_json
def interrupted(path, value):
    if path.name == 'READY':
        os._exit(79)
    old(path,value)
lifecycle._write_json = interrupted
lifecycle.prepare_trial(Path({str(self.pilot)!r}), {case_id!r}, 'interrupted', Path({str(self.out)!r}))
'''
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 79, completed.stderr)
        self.assertTrue(self.out.exists())
        self.assertFalse((self.out / "operator" / "READY").exists())
        self.assertCode("TRIAL_NOT_READY", lambda: resume_trial(self.out, "interrupted", case_id))
        self.assertCode("TRIAL_ALREADY_EXISTS", lambda: self.prepare())

    def test_sqlite_crash_rollback_preserves_last_committed_input(self):
        h = self.prepare()
        original = h.business_repository().snapshot()["request"]
        code = f'''
import os,sqlite3
db=sqlite3.connect({str(h.business_db)!r}, isolation_level=None)
db.execute('PRAGMA cache_size=1')
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE batch_runtime SET run_status='RUNNING'")
db.execute("UPDATE content_objects SET content=?", ('uncommitted'*10000,))
os._exit(78)
'''
        self.assertEqual(subprocess.run([sys.executable, "-c", code]).returncode, 78)
        restored = resume_trial(h.root, h.trial_id, h.case_id)
        view = restored.business_repository().snapshot()
        self.assertEqual(view["request"], original)
        self.assertEqual(view["processing_state_current"], "FAILED")


if __name__ == "__main__":
    unittest.main()
