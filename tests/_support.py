import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reconcile_agent.synthetic.config import DatasetConfig
from reconcile_agent.synthetic.files import core_hashes, digest, read_json, write_json
from reconcile_agent.synthetic.generator import generate_dataset


def reseal(root):
    """Model a correctly hashed but semantically wrong dataset, not mere bit rot."""
    manifest = read_json(root / "manifest.json")
    manifest["file_sha256"] = core_hashes(root)
    manifest["dataset_fingerprint"] = digest({k: v for k, v in manifest.items() if k != "dataset_fingerprint"})
    write_json(root / "manifest.json", manifest)


def entry_for(root, recipe, suite="core"):
    return next(e for e in read_json(root / "catalog.json")["cases"]
                if e["recipe"] == recipe and e["suite"] == suite)


def case_path(root, recipe, folder="agent_view"):
    return root / "cases" / entry_for(root, recipe)["case_id"] / folder


class Fixtures:
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.pilot = Path(cls.directory.name) / "pilot"
        cls.benchmark = Path(cls.directory.name) / "benchmark"
        generate_dataset(cls.pilot, DatasetConfig(rows_per_batch=3))
        generate_dataset(cls.benchmark, DatasetConfig(rows_per_batch=3, profile="benchmark"))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    @contextmanager
    def changed(self, benchmark=False):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "data"
            shutil.copytree(self.benchmark if benchmark else self.pilot, target)
            yield target
