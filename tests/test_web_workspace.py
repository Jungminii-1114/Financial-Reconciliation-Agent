from pathlib import Path
import tempfile
import unittest

from _support import ROOT
from reconcile_agent.web_workspace import WebWorkspace, WorkspaceError


class WebWorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.worker = ROOT / "scripts" / "mvp.py"
        self.workspace = WebWorkspace(self.root, self.worker)

    def act(self, state, action):
        result = self.workspace.act(state["id"], action, state["revision"])
        return result["case"]

    def test_interruption_survives_reload_and_recovery_uses_existing_effect(self):
        clean = self.workspace.case("after_commit")
        pending = self.act(clean, "start")
        self.assertEqual(pending["stage"], "PAUSED_FOR_APPROVAL")
        self.assertEqual(pending["operations"], [])
        self.assertEqual(pending["approvals"], [])
        with self.assertRaises(WorkspaceError) as stale:
            self.act(clean, "start")
        self.assertEqual(stale.exception.code, "STATE_CHANGED")
        with self.assertRaises(WorkspaceError) as unapproved:
            self.act(pending, "interrupt")
        self.assertEqual(unapproved.exception.code, "ACTION_UNAVAILABLE")
        approved = self.act(pending, "approve")
        self.assertEqual(approved["approvals"][0]["status"], "APPROVED")
        stopped = self.workspace.act(approved["id"], "interrupt", approved["revision"])
        self.assertEqual(stopped["last_action"]["exit_code"], 72)
        self.workspace = WebWorkspace(self.root, self.worker)
        interrupted = self.workspace.case("after_commit")
        self.assertEqual(interrupted["stage"], "INTERRUPTED")
        self.assertEqual(len(interrupted["operations"]), 1)
        self.assertIsNone(interrupted["decision"])
        recovered = self.act(interrupted, "resume")
        self.assertEqual(recovered["decision"]["result_ref"], interrupted["operations"][0]["result_ref"])
        self.assertEqual(len(recovered["operations"]), 1)
        self.assertTrue(recovered["evaluation"]["passed"])
        session = recovered["audit"][-1]["session_id"]
        self.assertEqual([call["tool_name"] for call in recovered["audit"] if call["session_id"] == session],
                         ["get_operation", "get_case"])

    def test_input_arrival_creates_a_new_run_and_preserves_prior_decision(self):
        waiting = self.act(self.workspace.case("delayed_arrival"), "start")
        self.assertEqual(waiting["decision"]["outcome"], "WAITING_FOR_INPUT")
        updated = self.act(waiting, "advance")
        self.assertNotEqual(waiting["run_id"], updated["run_id"])
        self.assertEqual(updated["input_version"], 2)
        self.assertEqual(updated["decision_count"], 1)
        self.assertTrue(updated["summary"]["ingestion_complete"])
        self.assertEqual(updated["stage"], "PAUSED_FOR_APPROVAL")
        self.assertEqual(len(updated["external"]), len(waiting["external"]) + 1)

    def test_start_another_trial_preserves_the_prior_workspace(self):
        first = self.act(self.workspace.case("clean"), "start")
        second = self.act(first, "start")
        self.assertNotEqual(first["trial_id"], second["trial_id"])
        self.assertTrue((self.root / "trials" / "clean" / first["trial_id"] / "business" / "business.sqlite").is_file())
        self.assertEqual(self.workspace.case("clean")["trial_id"], second["trial_id"])
