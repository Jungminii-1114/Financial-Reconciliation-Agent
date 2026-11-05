import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import ROOT


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "dataset.py"), *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)

    def test_documented_build_validate_inspect_export_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "pilot"
            result = self.run_cli("build", "--config", ROOT / "configs" / "synthetic-pilot.json", "--rows", 3, "--out", root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("10사례", result.stdout)
            self.assertTrue((root / "operator_view" / "index.html").is_file())
            validated = self.run_cli("validate", root)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            listed = self.run_cli("list", root)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("delayed_arrival", listed.stdout)
            inspected = self.run_cli("inspect", root, "--case", "first", "--show-rows", 1)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            value = json.loads(inspected.stdout)
            self.assertEqual(value["specification_action"], "REPROCESS")
            self.assertEqual(len(value["internal_sample"]), 1)
            exported = self.run_cli("export-sqlite", root, "--case", "first", "--out", Path(temporary) / "one.sqlite")
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertEqual(json.loads(exported.stdout)["cases"], 1)
            before = (root / "manifest.json").read_bytes()
            again = self.run_cli("build", "--out", root)
            self.assertEqual(again.returncode, 2)
            self.assertNotIn("Traceback", again.stderr)
            self.assertEqual(before, (root / "manifest.json").read_bytes())

    def test_bad_path_and_invalid_config_return_readable_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = self.run_cli("validate", Path(temporary) / "missing")
            self.assertEqual(missing.returncode, 2)
            self.assertNotIn("Traceback", missing.stderr)
            bad = self.run_cli("build", "--rows", 2, "--out", Path(temporary) / "bad")
            self.assertEqual(bad.returncode, 2)
            self.assertIn("3~5000", bad.stderr)
            self.assertFalse((Path(temporary) / "bad").exists())

    def test_generate_alone_and_benchmark_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "candidate"
            generated = self.run_cli("generate", "--profile", "benchmark", "--rows", 3, "--out", root)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertFalse((root / "operator_view").exists())
            validated = self.run_cli("validate", root)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertIn("98사례", validated.stdout)


if __name__ == "__main__":
    unittest.main()
