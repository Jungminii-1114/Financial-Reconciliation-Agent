import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import Fixtures, ROOT, entry_for


class TrialCLITests(Fixtures, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "trial"

    def call(self, *args):
        completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "trial.py"), *map(str, args)],
                                   cwd=ROOT, text=True, capture_output=True)
        self.assertFalse(completed.stderr, completed.stderr)
        return completed.returncode, json.loads(completed.stdout)

    def test_prepare_inspect_resume_and_overwrite_rejection(self):
        code, created = self.call("prepare", "--dataset", self.pilot, "--case", "first", "--trial-id", "cli-1", "--out", self.out)
        self.assertEqual(code, 0)
        self.assertEqual(created["status"], "READY")
        self.assertEqual(created["row_counts"], {"internal": 3, "external": 3})
        code, inspected = self.call("inspect", "--trial", self.out)
        self.assertEqual(code, 0)
        self.assertEqual(inspected["input_version"], 1)
        code, _ = self.call("resume", "--trial", self.out, "--trial-id", "cli-1", "--case", created["case_id"])
        self.assertEqual(code, 0)
        code, error = self.call("prepare", "--dataset", self.pilot, "--case", "first", "--trial-id", "cli-1", "--out", self.out)
        self.assertEqual(code, 2)
        self.assertEqual(error["error"]["code"], "TRIAL_ALREADY_EXISTS")

    def test_advance_command_reports_effect_once(self):
        case_id = entry_for(self.pilot, "delayed_arrival")["case_id"]
        self.call("prepare", "--dataset", self.pilot, "--case", case_id, "--trial-id", "delay", "--out", self.out)
        args = ("advance", "--trial", self.out, "--trial-id", "delay", "--case", case_id, "--to", "2026-11-05T10:05:00+09:00")
        code, result = self.call(*args)
        self.assertEqual(code, 0)
        self.assertEqual(result["applied_group_ids"], ["G001"])
        self.assertEqual(result["input_version"], 2)
        self.assertFalse(result["faults_executed"])
        self.assertEqual(self.call(*args)[1]["applied_group_ids"], [])

    def test_batch_prepares_all_pilot_cases_and_publishes_index(self):
        code, result = self.call("prepare-all", "--dataset", self.pilot, "--out", self.out, "--prefix", "pilot")
        self.assertEqual(code, 0)
        self.assertEqual(result["trials"], 10)
        self.assertEqual(len(result["index"]), 10)
        self.assertTrue((self.out / "trial_index.json").is_file())
        self.assertTrue(all((self.out / e["trial_dir"] / "operator" / "READY").is_file() for e in result["index"]))
        self.assertEqual(self.call("prepare-all", "--dataset", self.pilot, "--out", self.out)[1]["error"]["code"], "TRIAL_ALREADY_EXISTS")

    def test_safe_error_contains_no_payload_or_traceback(self):
        code, result = self.call("resume", "--trial", self.out, "--trial-id", "none", "--case", "c_000000000000")
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "TRIAL_MISSING")
        self.assertEqual(set(result["error"]), {"code", "message"})

    def test_runner_setting_is_bound_across_commands(self):
        code, result = self.call("prepare", "--dataset", self.pilot, "--case", "first", "--trial-id", "H-1", "--out", self.out, "--setting", "H")
        self.assertEqual(code, 0)
        self.assertEqual(self.call("inspect", "--trial", self.out)[1]["error"]["code"], "SCOPE_DENIED")
        self.assertEqual(self.call("inspect", "--trial", self.out, "--setting", "H")[0], 0)


if __name__ == "__main__":
    unittest.main()
