import unittest
from dataclasses import replace

from reconcile_agent.tools.errors import ToolError
from reconcile_agent.tools.query_records import (
    QueryRecordsLimits,
    parse_query_records_request,
    validate_query_records_request,
)


class QueryRecordsRequestTests(unittest.TestCase):
    def setUp(self):
        self.limits = QueryRecordsLimits()
        self.payload = {
            "case_id": "c_2e8b6b7c4702",
            "batch_id": "BATCH001",
            "profile": "EXISTENCE",
            "transaction_ids": ["TX_FUTURE", "TX000001", "TX_UNKNOWN"],
            "as_of": "2026-11-05T10:00:00+09:00",
            "expected_input_version": 1,
        }

    def parse(self, **changes):
        return parse_query_records_request(self.payload | changes, limits=self.limits)

    def assert_rejected(self, payload, code="INVALID_ARGUMENT"):
        with self.assertRaises(ToolError) as caught:
            parse_query_records_request(payload, limits=self.limits)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_equivalent_requests_normalize_to_same_value_without_mutating_input(self):
        request = self.parse()
        self.assertEqual(request.as_of, "2026-11-05T01:00:00.000000+00:00")
        self.assertEqual(request.transaction_ids, ("TX000001", "TX_FUTURE", "TX_UNKNOWN"))
        self.assertEqual(request.limit, 50)
        self.assertIsNone(request.cursor)
        reordered = self.parse(
            as_of="2026-11-05T01:00:00Z",
            transaction_ids=["TX_UNKNOWN", "TX000001", "TX_FUTURE"],
            cursor=None,
            result_ref=None,
            action_digest=None,
        )
        self.assertEqual(reordered, request)
        self.payload["transaction_ids"].clear()
        self.assertEqual(len(request.transaction_ids), 3)

    def test_request_shape_and_unknown_fields(self):
        self.assert_rejected([])
        for field in self.payload:
            with self.subTest(missing=field):
                self.assert_rejected({key: value for key, value in self.payload.items() if key != field})
        for field in ("ctx", "sql", "db_path", "principal_id", "CANARY_INPUT"):
            with self.subTest(unknown=field):
                exc = self.assert_rejected(self.payload | {field: "CANARY_VALUE"})
                self.assertNotIn("CANARY_", str(exc))

    def test_unsupported_or_mistyped_profiles(self):
        for profile in ("CUSTOM", "existence", [], None):
            with self.subTest(profile=profile):
                self.assert_rejected(self.payload | {"profile": profile}, "INVALID_QUERY_PROFILE")

    def test_invalid_common_and_profile_specific_fields(self):
        for changes in (
            {"case_id": "case_002"}, {"case_id": 7}, {"batch_id": ""},
            {"batch_id": " BATCH001"}, {"batch_id": "B" * 129},
            {"expected_input_version": True}, {"expected_input_version": 1.0},
            {"expected_input_version": 0}, {"limit": False}, {"limit": None},
            {"cursor": 42}, {"cursor": ""}, {"cursor": "\n"},
            {"result_ref": "result-1"}, {"action_digest": ""},
        ):
            with self.subTest(changes=changes):
                self.assert_rejected(self.payload | changes)

    def test_transaction_ids_require_json_array_of_valid_unique_strings(self):
        for ids in (
            None, "TX000001", ("TX000001",), [], [""], [" TX000001"],
            ["TX000001\x00"], [7], [[]], ["TX000001", "TX000001"],
        ):
            with self.subTest(ids=ids):
                self.assert_rejected(self.payload | {"transaction_ids": ids})

    def test_timestamp_errors_are_tool_errors(self):
        for value in (
            False, "2026-11-05T10:00:00", "2026-11-05x10:00:00Z", "2026-02-30T10:00:00Z",
            "2026-11-05T10:00:00+09:60", "2026-11-05T10:00:00.1234567Z", "0001-01-01T00:00:00+09:00",
        ):
            with self.subTest(value=value):
                self.assert_rejected(self.payload | {"as_of": value})

    def test_snapshot_time_version_and_cursor_contents_are_deferred_to_query_layer(self):
        request = self.parse(
            as_of="2099-01-01T00:00:00.1Z", expected_input_version=999, cursor="opaque-token"
        )
        self.assertEqual(request.as_of, "2099-01-01T00:00:00.100000+00:00")
        self.assertEqual(request.expected_input_version, 999)
        self.assertEqual(request.cursor, "opaque-token")

    def test_configurable_limits_and_hard_caps(self):
        ids = [f"TX{index:06d}" for index in range(50)]
        self.assertEqual(len(self.parse(transaction_ids=ids, limit=50).transaction_ids), 50)
        self.assert_rejected(self.payload | {"transaction_ids": ids + ["TX_FUTURE"]})
        self.assert_rejected(self.payload | {"limit": 51})

        self.limits = QueryRecordsLimits(max_transaction_ids=2, max_page_size=5)
        self.assertEqual(self.parse(transaction_ids=ids[:2]).limit, 5)
        self.assert_rejected(self.payload | {"transaction_ids": ids[:3]})
        self.assert_rejected(self.payload | {"transaction_ids": ids[:2], "limit": 6})
        for values in ({"max_transaction_ids": True}, {"max_page_size": 51}, {"max_page_size": 0}):
            with self.subTest(values=values):
                with self.assertRaises(ToolError) as caught:
                    QueryRecordsLimits(**values)
                self.assertEqual(caught.exception.code, "CONTRACT_VIOLATION")

    def test_direct_construction_does_not_bypass_request_validation(self):
        request = self.parse()
        self.assertIsNone(validate_query_records_request(request, limits=self.limits))
        for changes, code in (
            ({"transaction_ids": ["TX000001"]}, "INVALID_ARGUMENT"),
            ({"transaction_ids": ("TX000001", "TX000001")}, "INVALID_ARGUMENT"),
            ({"expected_input_version": True}, "INVALID_ARGUMENT"),
            ({"as_of": "2026-11-05T10:00:00"}, "INVALID_ARGUMENT"),
            ({"profile": "CUSTOM"}, "INVALID_QUERY_PROFILE"),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ToolError) as caught:
                    validate_query_records_request(replace(request, **changes), limits=self.limits)
                self.assertEqual(caught.exception.code, code)
