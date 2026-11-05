import ast
import unittest
from pathlib import Path

from _support import Fixtures, case_path, reseal
from reconcile_agent.synthetic import validate as validator
from reconcile_agent.synthetic.files import EXTERNAL_FIELDS, read_csv, read_json, write_csv, write_json


class SemanticValidationTests(Fixtures, unittest.TestCase):
    def assert_rejected(self, mutate, benchmark=False, pattern=None):
        with self.changed(benchmark) as root:
            mutate(root)
            reseal(root)
            if pattern:
                with self.assertRaisesRegex(ValueError, pattern):
                    validator.validate_dataset(root)
            else:
                with self.assertRaises(ValueError):
                    validator.validate_dataset(root)

    def edit_expected(self, root, recipe, change):
        path = case_path(root, recipe, "evaluator_only") / "expected.json"
        value = read_json(path)
        change(value)
        write_json(path, value)

    def edit_rows(self, root, recipe, change):
        path = case_path(root, recipe) / "external.csv"
        rows = read_csv(path, EXTERNAL_FIELDS)
        change(rows)
        write_csv(path, rows, EXTERNAL_FIELDS)

    def edit_events(self, root, recipe, change):
        path = case_path(root, recipe, "controller_only") / "scheduled_events.json"
        value = read_json(path)
        change(value)
        write_json(path, value)

    def test_wrong_gold_rejected_even_with_valid_hashes(self):
        self.assert_rejected(lambda root: self.edit_expected(root, "amount_mismatch", lambda value:
                             value["checkpoints"][0].update(expected_action="REPROCESS")), pattern="정답 불일치")

    def test_changed_transaction_rejected_even_with_valid_hashes(self):
        self.assert_rejected(lambda root: self.edit_rows(root, "amount_mismatch", lambda rows:
                             rows[0].update(amount_krw=rows[0]["amount_krw"]+1)), pattern="정답 불일치")

    def test_future_information_rejected(self):
        self.assert_rejected(lambda root: self.edit_rows(root, "amount_mismatch", lambda rows:
                             rows[0].update(known_at="2026-11-05T10:00:01+09:00")), pattern="미래 행")

    def test_duplicate_primary_key_rejected(self):
        self.assert_rejected(lambda root: self.edit_rows(root, "amount_mismatch", lambda rows:
                             rows.append(dict(rows[0]))), pattern="기본키 중복")

    def test_naive_time_rejected(self):
        self.assert_rejected(lambda root: self.edit_rows(root, "amount_mismatch", lambda rows:
                             rows[0].update(known_at="2026-11-05T09:59:00")), pattern="timezone")

    def test_skipped_input_version_rejected(self):
        self.assert_rejected(lambda root: self.edit_events(root, "delayed_arrival", lambda groups:
                             groups[0].update(new_input_version=3)), pattern="버전 연결")

    def test_wrong_row_revision_rejected(self):
        self.assert_rejected(lambda root: self.edit_events(root, "late_correction", lambda groups:
                             groups[0]["events"][0].update(expected_revision=2)), benchmark=True, pattern="revision 불일치")

    def test_same_time_event_groups_rejected(self):
        self.assert_rejected(lambda root: self.edit_events(root, "staggered_arrivals", lambda groups:
                             groups[1].update(at=groups[0]["at"])), benchmark=True, pattern="엄격히 증가")

    def test_wrong_recovery_truth_rejected(self):
        self.assert_rejected(lambda root: self.edit_expected(root, "after_commit", lambda value:
                             value.update(recovery_effect_truth="NOT_APPLIED")), pattern="효과 정답")

    def test_wrong_expiry_recovery_rejected(self):
        self.assert_rejected(lambda root: self.edit_expected(root, "precommit_approval_expiry", lambda value:
                             value.update(recovery_obligation="CONFIRM_NOT_APPLIED_THEN_RETRY_SAME_OPERATION")),
                             benchmark=True, pattern="복구 정답")

    def test_wrong_canary_exposure_rejected(self):
        def mutate(root):
            canary = read_json(case_path(root, "amount_mismatch", "evaluator_only") / "protected_records.json")["records"][0]["canary"]
            (case_path(root, "amount_mismatch") / "request.txt").write_text(canary)
        self.assert_rejected(mutate, pattern="정답·보호 값 노출")

    def test_catalog_cross_split_change_rejected(self):
        def mutate(root):
            path = root / "catalog.json"
            catalog = read_json(path)
            next(e for e in catalog["cases"] if e["suite"] == "security")["split"] = "held_out"
            write_json(path, catalog)
        self.assert_rejected(mutate, pattern="held-out")

    def test_normal_attack_pair_business_change_rejected(self):
        def mutate(root):
            entry = next(e for e in read_json(root / "catalog.json")["cases"] if e["suite"] == "security")
            path = root / "cases" / entry["case_id"] / "agent_view" / "external.csv"
            rows = read_csv(path, EXTERNAL_FIELDS)
            rows[0]["amount_krw"] += 100
            write_csv(path, rows, EXTERNAL_FIELDS)
        self.assert_rejected(mutate, pattern="대조 쌍")

    def test_wrong_attack_label_rejected(self):
        def mutate(root):
            entry = next(e for e in read_json(root / "catalog.json")["cases"] if e["condition"] == "attack")
            path = root / "cases" / entry["case_id"] / "evaluator_only" / "attack_label.json"
            value = read_json(path)
            value["is_attack"] = False
            write_json(path, value)
        self.assert_rejected(mutate, pattern="label·pair")

    def test_unsealed_change_rejected_by_manifest(self):
        with self.changed() as root:
            (root / "data_card.md").write_text("changed")
            with self.assertRaisesRegex(ValueError, "해시 불일치"):
                validator.validate_dataset(root)

    def test_symlinks_rejected(self):
        with self.changed() as root:
            (root / "linked").symlink_to(root / "config.json")
            with self.assertRaisesRegex(ValueError, "심볼릭 링크"):
                validator.validate_dataset(root)

    def test_validator_does_not_import_generation_or_replay_logic(self):
        tree = ast.parse(Path(validator.__file__).read_text())
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertNotIn("recipes", imported)
        self.assertNotIn("generator", imported)


if __name__ == "__main__":
    unittest.main()
