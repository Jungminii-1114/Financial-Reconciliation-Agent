import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from _support import Fixtures, ROOT, entry_for
from reconcile_agent.evaluation.mvp import evaluate
from reconcile_agent.harness.journal import locked_runtime
from reconcile_agent.harness.workflow import ReconciliationWorkflow
from reconcile_agent.synthetic.files import read_json
from reconcile_agent.tools.errors import ToolError
from reconcile_agent.trials.events import advance_trial
from reconcile_agent.trials.lifecycle import prepare_trial


class MvpTests(Fixtures, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def prepare(self, recipe, *, benchmark=False):
        dataset = self.benchmark if benchmark else self.pilot
        return self.prepare_case(dataset, entry_for(dataset, recipe)["case_id"])

    def prepare_case(self, dataset, case):
        return prepare_trial(dataset, case, "mvp-test", self.root / "trial")

    def test_existence_tracks_arrival_and_past_snapshot(self):
        handle = self.prepare("delayed_arrival")
        with locked_runtime(handle) as journal:
            flow = ReconciliationWorkflow(handle, journal, "run-1", "session-1")
            case = flow.call("get_case", "B-01", case_id=handle.case_id)
            args = {**flow.target, "profile": "EXISTENCE", "transaction_ids": ["TX000003"],
                    "as_of": case.decision_at, "expected_input_version": 1}
            absent = flow.call("query_records", "B-02", **args)
            self.assertEqual(absent.structured_facts.missing_ids, ("TX000003",))
            self.assertTrue(absent.coverage.complete)
            self.assertFalse(case.ingestion_complete)

            when = (datetime.fromisoformat(case.decision_at) + timedelta(minutes=5)).isoformat()
            advance_trial(handle, when)
            arrived = flow.call("query_records", "B-02", **(args | {"as_of": when, "expected_input_version": 2}))
            self.assertEqual(arrived.structured_facts.matched_ids, ("TX000003",))
            self.assertNotEqual(arrived.input_digest, absent.input_digest)
            self.assertEqual(flow.call("query_records", "B-02", **args), absent)
            for changes, code in (({"expected_input_version": 2}, "VERSION_CONFLICT"),
                                  ({"as_of": "2030-01-01T00:00:00Z"}, "ASOF_OUT_OF_RANGE"),
                                  ({"batch_id": "NOT-THE-BATCH"}, "SCOPE_DENIED")):
                with self.subTest(changes=changes), self.assertRaises(ToolError) as exc:
                    flow.call("query_records", "B-02", **(args | changes))
                self.assertEqual(exc.exception.code, code)

    def test_batch_swap_and_cursor_do_not_change_summary(self):
        handle = self.prepare("amount_swap")
        at = handle.business_repository().snapshot()["as_of"]
        with locked_runtime(handle) as journal:
            flow = ReconciliationWorkflow(handle, journal, "run-1", "session-1")
            args = {**flow.target, "profile": "BATCH_COMPARISON", "as_of": at,
                    "expected_input_version": 1, "limit": 2}
            page = flow.call("query_records", "B-02", **args)
            self.assertEqual(page.structured_facts.mismatched_ids, ("TX000001", "TX000002"))
            self.assertTrue(page.coverage.summary_complete)
            self.assertFalse(page.coverage.details_complete)
            refs = list(page.row_refs)
            token = page.coverage.next_cursor
            while token:
                next_page = flow.call("query_records", "B-02", **args, cursor=token)
                self.assertEqual(page.result_digest, next_page.result_digest)
                self.assertEqual(page.structured_facts, next_page.structured_facts)
                refs.extend(next_page.row_refs)
                token = next_page.coverage.next_cursor
            self.assertEqual(len(refs), 6)
            self.assertEqual(len(set(refs)), 6)
            with self.assertRaises(ToolError) as changed:
                flow.call("query_records", "B-02", **(args | {"limit": 3}), cursor=page.coverage.next_cursor)
            self.assertEqual(changed.exception.code, "INVALID_CURSOR")

    def test_expired_approval_requires_new_operator_approval_for_same_intent(self):
        handle = self.prepare("clean")
        with locked_runtime(handle) as journal:
            flow = ReconciliationWorkflow(handle, journal, "run-1", "session-1")
            self.assertEqual(flow.run()["status"], "PAUSED_FOR_APPROVAL")
            approval = flow.approve("operator")["approval"]
            advance_trial(handle, approval["expires_at"])
            rejected = flow.run()
            self.assertEqual(rejected["reason"], "APPROVAL_STALE")
            intent_before = journal.load("run-1")["intent"]
            renewed = flow.approve("operator")["approval"]
            self.assertNotEqual(renewed["approval_id"], approval["approval_id"])
            final = flow.run()["decision"]
            self.assertEqual(final["operation_id"], intent_before["operation_id"])
            self.assertEqual(final["outcome"], "REPROCESSED")
        self.assertTrue(evaluate(handle, "run-1")["passed"])

    def test_after_commit_input_change_keeps_old_effect_and_requires_new_approval(self):
        handle = self.prepare("postcommit_input_change", benchmark=True)

        def lose_response():
            raise ToolError("TOOL_RESPONSE_LOST", "controlled loss of response")

        with locked_runtime(handle) as journal:
            flow = ReconciliationWorkflow(handle, journal, "run-1", "session-1", after_commit=lose_response)
            self.assertEqual(flow.run()["status"], "PAUSED_FOR_APPROVAL")
            flow.approve("operator")
            self.assertEqual(flow.run()["status"], "UNRESOLVED")
            old = journal.load("run-1")["intent"]
            at = handle.business_repository().snapshot()["as_of"]
            advance_trial(handle, (datetime.fromisoformat(at) + timedelta(minutes=5)).isoformat())
            resumed = ReconciliationWorkflow(handle, journal, "run-1", "session-2")
            pending = resumed.run()
            self.assertEqual(pending["status"], "PAUSED_FOR_APPROVAL")
            self.assertEqual(pending["proposal"]["expected_input_version"], 2)
            self.assertEqual(journal.load("run-1")["previous_effect"]["operation_id"], old["operation_id"])
            renewed = resumed.approve("operator")
            self.assertEqual(renewed["approval"]["expected_input_version"], 2)
            final = resumed.run()["decision"]
            self.assertNotEqual(final["operation_id"], old["operation_id"])
        with sqlite3.connect(handle.business_db) as db:
            self.assertEqual(db.execute("SELECT input_version FROM operations ORDER BY input_version").fetchall(), [(1,), (2,)])
        self.assertTrue(evaluate(handle, "run-1")["passed"])

    def test_no_write_without_durable_intent_or_approval_and_model_sees_only_facts(self):
        candidate = next(item for item in read_json(self.pilot / "catalog.json")["cases"]
                         if item["group_id"] == "security:approval_authority" and item["condition"] == "attack")
        handle = self.prepare_case(self.pilot, candidate["case_id"])
        with locked_runtime(handle) as journal:
            flow = ReconciliationWorkflow(handle, journal, "run-1", "session-1")
            with patch("reconcile_agent.harness.workflow.ollama_advice", return_value="REPROCESS") as advise:
                pending = flow.run(model="test-advisor")
            report = pending["proposal"]
            self.assertEqual(pending["status"], "PAUSED_FOR_APPROVAL")
            facts = advise.call_args.args[1]
            self.assertTrue(all(type(item) in (int, bool) for item in facts.values()))
            self.assertNotIn("content", json.dumps(pending))
            args = {key: report[key] for key in ("case_id", "batch_id", "expected_input_version",
                                                "expected_input_digest", "action_digest")}
            with self.assertRaises(ToolError) as denied:
                flow.call("execute_reprocess", "B-04", **args, approval_id="fake",
                          operation_id="op-fake", idempotency_key="fake")
            self.assertEqual(denied.exception.code, "INTENT_REQUIRED")
        with sqlite3.connect(handle.business_db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 0)

    def test_independent_evaluator_rejects_wrong_result_with_valid_decision(self):
        handle = self.prepare("clean")
        with locked_runtime(handle) as journal:
            flow = ReconciliationWorkflow(handle, journal, "run-1", "session-1")
            flow.run()
            flow.approve("operator")
            result = flow.run()["decision"]
            self.assertTrue(evaluate(handle, "run-1")["passed"])
            args = {**flow.target, "profile": "DUPLICATE", "as_of": result["decision_at"],
                    "expected_input_version": 1, "action_digest": "sha256:" + "f" * 64}
            duplicate = flow.call("query_records", "B-05", **args)
            self.assertEqual(duplicate.structured_facts.operation_refs[0].operation_id, result["operation_id"])
            self.assertEqual(duplicate.structured_facts.same_action_operation_ids, ())
            self.assertTrue(flow.call("query_records", "B-05", **(args | {
                "profile": "BATCH_COMPARISON", "action_digest": None, "result_ref": result["result_ref"],
            })).structured_facts.result_verified)
            last_ledger = self.root / "trial.runtime" / "calls.sqlite"
            with sqlite3.connect(last_ledger) as db:
                before = db.execute("SELECT COUNT(*) FROM calls").fetchone()
                self.assertTrue(flow.run()["redelivered"])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM calls").fetchone(), before)
        with sqlite3.connect(handle.business_db) as db:
            db.execute("UPDATE reconciliation_results SET amount_krw=amount_krw+1 WHERE transaction_id='TX000001'")
        checked = evaluate(handle, "run-1")
        self.assertFalse(checked["passed"])
        self.assertFalse(checked["checks"]["exact_result_rows"])

    def test_demo_exits_real_subprocess_and_recovers_without_duplicate_writes(self):
        work = self.root / "demo"
        demo = subprocess.run([sys.executable, str(ROOT / "scripts/mvp.py"), "demo", "--out", str(work)],
                              text=True, capture_output=True, timeout=80)
        self.assertEqual(demo.returncode, 0, demo.stdout + demo.stderr)
        report = read_json(work / "report.json")["scenarios"]
        self.assertEqual(report["before_commit"]["worker_exit"], 71)
        self.assertEqual(report["before_commit"]["after_process_exit"]["operations"], 0)
        self.assertEqual(report["after_commit"]["worker_exit"], 72)
        self.assertEqual(report["after_commit"]["after_process_exit"]["operations"], 1)
        self.assertEqual(report["after_commit"]["after_process_exit"]["completion_decisions"], 0)
        resumed = [event["tool_name"] for event in report["after_commit"]["audit"] if event["session_id"] == "session-3"]
        self.assertEqual(resumed, ["get_operation", "get_case"])
        self.assertEqual(report["after_commit"]["counts"]["operations"], 1)
        self.assertTrue(all(scenario["evaluation"]["passed"] for scenario in report.values()))
