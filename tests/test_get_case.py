import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch
from time import monotonic
from _support import Fixtures, entry_for
from reconcile_agent.trials.lifecycle import prepare_trial, prepare_from_dataset
from reconcile_agent.trials.source import ValidatedDataset
from reconcile_agent.trials.events import advance_trial
from reconcile_agent.business.errors import AdapterError
from reconcile_agent.business.repository import BusinessScope
from reconcile_agent.tools.contracts import TargetScope, ToolInvocation
from reconcile_agent.tools.dispatcher import RuntimeServices, dispatch_tool
from reconcile_agent.tools.errors import ToolError
from reconcile_agent.tools.get_case import (GetCaseServices, GetCaseLimits, GetCaseRequest,
    parse_get_case_request, resolve_case_scope, get_case, serialize_get_case_result)
from reconcile_agent.tools.ledger import ToolCallLedger
from reconcile_agent.tools.runner import context_for_trial


class GetCaseTests(Fixtures, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.h = prepare_trial(self.pilot,entry_for(self.pilot,'clean')['case_id'],'tool-test',self.root/'trial')
        self.repo = self.h.business_repository()
        self.svc = RuntimeServices(GetCaseServices(self.repo,GetCaseLimits()),ToolCallLedger(self.root/'runtime.sqlite'))

    def ctx(self, **changes):
        c=context_for_trial(self.h,logical_run_id='run-1',session_id='session-1')
        return replace(c,**changes)

    def call(self, payload=None, ctx=None, services=None, name='get_case', version='tool-call-1'):
        ctx = ctx or self.ctx()
        case_id = self.h.case_id if ctx.trial_id == self.h.trial_id else next(iter(ctx.allowed_targets)).case_id
        return dispatch_tool(ToolInvocation(version,name,payload if payload is not None else {'case_id':case_id}),
                             ctx,services or self.svc)

    def code(self,r):
        return next(d.error_code for d in r.diagnostics.values() if d.status=='failed')

    def test_round_trip_read_only_and_public_fields(self):
        before=self.h.business_db.read_bytes()
        r=self.call()
        self.assertEqual(r.status,'success')
        self.assertEqual(set(asdict(r)),{'status','data','diagnostics','outcome'})
        self.assertEqual(set(asdict(r.data)),{'case_id','batch_id','input_version','input_digest','decision_at',
                         'ingestion_complete','processing_state','operation_refs','external_notes'})
        self.assertEqual(r.data.processing_state,'FAILED')
        self.assertTrue(r.data.ingestion_complete)
        self.assertEqual(r.data.operation_refs,())
        self.assertEqual(before,self.h.business_db.read_bytes())
        encoded=serialize_get_case_result(r,limits=GetCaseLimits())
        self.assertIsInstance(json.loads(encoded)['data']['external_notes'],list)

    def test_parser_rejects_extra_fields_missing_and_wrong_types(self):
        for p in ({},{'case_id':3},{'case_id':True},{'case_id':'c_2e8b2kdpo10c'},
                  {'case_id':self.h.case_id,'principal_id':'CANARY_SECRET'},[]):
            with self.subTest(payload=p):
                r=self.call(p)
                self.assertEqual(self.code(r),'INVALID_ARGUMENT')
                self.assertIsNone(r.data)
                self.assertNotIn('CANARY_SECRET',repr(r))

    def test_unknown_tool_and_contract_are_counted(self):
        self.assertEqual(self.code(self.call(name='arbitrary_sql')),'UNKNOWN_TOOL')
        self.assertEqual(self.code(self.call(version='bad')),'UNSUPPORTED_CONTRACT_VERSION')
        with sqlite3.connect(self.svc.ledger.path) as db:
            self.assertEqual(db.execute('SELECT used FROM budgets').fetchone()[0],2)

    def test_scope_denied_before_handler(self):
        for ctx in (self.ctx(allowed_tools=frozenset()),self.ctx(allowed_targets=frozenset()),
                    self.ctx(principal_id='other'),self.ctx(trial_id='other')):
            with self.subTest(ctx=ctx):
                r=self.call(ctx=ctx)
                self.assertEqual(self.code(r),'SCOPE_DENIED')
                self.assertIsNone(r.data)

    def test_cross_pair_denied(self):
        ctx=self.ctx(allowed_targets=frozenset({TargetScope(self.h.case_id,'OTHER'),TargetScope('c_000000000000',self.h.batch_id)}))
        self.assertEqual(self.code(self.call(ctx=ctx)),'SCOPE_DENIED')

    def test_ambiguous_case_denied(self):
        ctx=self.ctx(allowed_targets=frozenset({TargetScope(self.h.case_id,'OTHER'),TargetScope(self.h.case_id,self.h.batch_id)}))
        self.assertEqual(self.code(self.call(ctx=ctx)),'CONTRACT_VIOLATION')

    def test_config_and_policy_rechecked(self):
        for kw in ({'config_hash':'sha256:'+'0'*64},{'policy_version':'other'}):
            self.assertEqual(self.code(self.call(ctx=self.ctx(**kw))),'SCOPE_DENIED')

    def test_context_cannot_use_mutable_permissions(self):
        self.assertEqual(self.code(self.call(ctx=self.ctx(allowed_tools={'get_case'}))),'SCOPE_DENIED')

    def test_response_type_validation_rejects_bool_version(self):
        real=self.repo.get_case_view
        with patch.object(self.repo,'get_case_view',side_effect=lambda **kw: replace(real(**kw),input_version=True)):
            self.assertEqual(self.code(self.call()),'CONTRACT_VIOLATION')

    def test_input_tampering_is_not_trusted_digest(self):
        with sqlite3.connect(self.h.business_db) as db:
            db.execute('UPDATE settlement_history SET amount_krw=amount_krw+1')
        self.assertEqual(self.code(self.call()),'IMPORT_INVARIANT_ERROR')

    def test_missing_runtime_is_error_not_default(self):
        with sqlite3.connect(self.h.business_db) as db: db.execute('DELETE FROM batch_runtime')
        self.assertEqual(self.code(self.call()),'IMPORT_INVARIANT_ERROR')

    def test_missing_case_only_after_scope_admission(self):
        ctx=self.ctx()
        with sqlite3.connect(self.h.business_db) as db: db.execute('DELETE FROM cases')
        self.assertEqual(self.code(self.call(ctx=ctx)),'CASE_UNAVAILABLE')

    def test_future_note_hidden(self):
        with sqlite3.connect(self.h.business_db) as db:
            db.execute("INSERT INTO external_notes VALUES('future',?,'r','missing','hash',1,'x','2099-01-01T00:00:00.000000+00:00')",(self.h.case_id,))
        self.assertNotIn('future',[n.note_id for n in self.call().data.external_notes])

    def test_real_arrival_changes_version_and_preserves_old_reference(self):
        h=prepare_trial(self.pilot,entry_for(self.pilot,'delayed_arrival')['case_id'],'delay',self.root/'delay')
        services=RuntimeServices(GetCaseServices(h.business_repository(),GetCaseLimits()),self.svc.ledger)
        def run():return self.call(ctx=context_for_trial(h,logical_run_id='r',session_id='s'),services=services)
        first=run();self.assertFalse(first.data.ingestion_complete)
        # Seeded storage state is not an approval/reprocessing service execution.
        with sqlite3.connect(h.business_db) as db:
            db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       ('a',h.case_id,h.batch_id,1,first.data.input_digest,'sha256:'+'2'*64,'mock-policy-v1',None,None,'REQUIRED',None))
            db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       ('op','idem',h.case_id,h.batch_id,1,'REPROCESS','sha256:'+'2'*64,'a','UNKNOWN',None,None))
        advance_trial(h,'2026-11-05T10:05:00+09:00')
        second=run();self.assertEqual(second.status,'success')
        self.assertEqual(second.data.input_version,2)
        self.assertTrue(second.data.ingestion_complete)
        self.assertEqual(second.data.operation_refs[0].input_version,1)

    def test_security_notes_remain_untrusted_and_hash_checked(self):
        with ValidatedDataset(self.pilot) as source:
            entry=next(e for e in source.entries if e['suite']=='security')
            h=prepare_from_dataset(source,entry['case_id'],'notes',self.root/'notes')
        services=RuntimeServices(GetCaseServices(h.business_repository(),GetCaseLimits()),self.svc.ledger)
        ctx=lambda:context_for_trial(h,logical_run_id='r',session_id='s')
        r=self.call(ctx=ctx(),services=services)
        self.assertEqual(r.status,'success')
        self.assertEqual(r.data.external_notes[0].origin,'external_settlement_note')
        self.assertEqual(r.data.external_notes[0].content,h.business_repository().snapshot()['external_notes'][0]['content'])
        with sqlite3.connect(h.business_db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM approvals').fetchone()[0],0)
            db.execute("UPDATE content_objects SET content='changed' WHERE origin='external_settlement_note'")
        self.assertEqual(self.code(self.call(ctx=ctx(),services=services)),'CONTENT_INTEGRITY_ERROR')

    def test_output_limit_blocks_whole_response(self):
        # The complete successful envelope exceeds this intentionally small allowance.
        services=RuntimeServices(GetCaseServices(self.repo,GetCaseLimits(512)),self.svc.ledger)
        r=self.call(services=services)
        self.assertEqual(self.code(r),'OUTPUT_LIMIT_EXCEEDED')
        self.assertIsNone(r.data)

    def test_audit_failure_prevents_handler(self):
        with patch.object(self.svc.ledger,'reserve',side_effect=ToolError('AUDIT_UNAVAILABLE','기록 실패')),patch.object(self.repo,'get_case_view') as read:
            self.assertEqual(self.code(self.call()),'AUDIT_UNAVAILABLE')
            read.assert_not_called()

    def test_post_handler_audit_failure_does_not_expose_result(self):
        with patch.object(self.svc.ledger,'finish',side_effect=OSError('CANARY_SECRET')):
            r=self.call();self.assertEqual(r.status,'error');self.assertIsNone(r.data)
            self.assertNotIn('CANARY_SECRET',repr(r))

    def test_budget_persists_across_sessions_and_reopen(self):
        ledger=ToolCallLedger(self.root/'small.sqlite',max_attempts=1)
        services=RuntimeServices(self.svc.get_case_services,ledger)
        self.assertEqual(self.call(services=services).status,'success')
        services=RuntimeServices(self.svc.get_case_services,ToolCallLedger(ledger.path,max_attempts=1))
        self.assertEqual(self.code(self.call(ctx=self.ctx(session_id='new'),services=services)),'BUDGET_EXHAUSTED')

    def test_concurrent_budget_reservation(self):
        services=RuntimeServices(self.svc.get_case_services,ToolCallLedger(self.root/'parallel.sqlite',max_attempts=1))
        with ThreadPoolExecutor(max_workers=3) as pool:
            rs=list(pool.map(lambda _:self.call(services=services),range(3)))
        self.assertEqual(sum(r.status=='success' for r in rs),1)
        self.assertEqual(sum(r.status=='error' and self.code(r)=='BUDGET_EXHAUSTED' for r in rs),2)

    def test_call_id_reuse_is_not_another_attempt(self):
        ctx=self.ctx();self.assertEqual(self.call(ctx=ctx).status,'success')
        self.assertEqual(self.code(self.call(ctx=ctx)),'CALL_ID_CONFLICT')
        with sqlite3.connect(self.svc.ledger.path) as db:
            self.assertEqual(db.execute('SELECT used FROM budgets').fetchone()[0],1)

    def test_unknown_exception_is_payload_free(self):
        with patch.object(self.repo,'get_case_view',side_effect=RuntimeError('CANARY_SECRET /private/path')):
            r=self.call();self.assertEqual(self.code(r),'INTERNAL_ERROR')
            self.assertNotIn('CANARY_SECRET',repr(r))
        self.assertNotIn(b'CANARY_SECRET',self.svc.ledger.path.read_bytes())

    def test_deadline_expired_before_sql(self):
        with self.assertRaises(AdapterError) as e:
            self.repo.get_case_view(requested_case_id=self.h.case_id,
                                   expected_scope=self.repo._scope,deadline=monotonic()-1)
        self.assertEqual(e.exception.code,'TOOL_TIMEOUT')

    def test_phase_gate_optional(self):
        self.assertEqual(self.call().status,'success')
        r=dispatch_tool(ToolInvocation('tool-call-1','get_case',{'case_id':self.h.case_id}),self.ctx(),self.svc,phase='B-03')
        self.assertEqual(self.code(r),'PHASE_DENIED')

    def test_entire_pilot_can_be_observed(self):
        with ValidatedDataset(self.pilot) as source:
            for i,e in enumerate(source.entries):
                h=prepare_from_dataset(source,e['case_id'],f'pilot-{i}',self.root/f'pilot-{i}')
                services=RuntimeServices(GetCaseServices(h.business_repository(),GetCaseLimits()),self.svc.ledger)
                r=self.call(ctx=context_for_trial(h,logical_run_id='r',session_id='s'),services=services)
                self.assertEqual(r.status,'success',(e['case_id'],r))

    def test_ledger_hash_matches_actual_success_and_error_response(self):
        from reconcile_agent.business.input_digest import sha256
        for payload in ({'case_id': self.h.case_id}, {'case_id': self.h.case_id, 'bad': True}):
            ctx=self.ctx()
            r=self.call(payload,ctx=ctx)
            actual=sha256(serialize_get_case_result(r,limits=GetCaseLimits()))
            with sqlite3.connect(self.svc.ledger.path) as db:
                self.assertEqual(db.execute('SELECT response_digest FROM calls WHERE call_id=?',(ctx.tool_call_id,)).fetchone()[0],actual)

    def test_cli_uses_existing_trial_and_external_runtime_ledger(self):
        script=Path(__file__).resolve().parents[1]/'scripts/get_case.py'
        cmd=[sys.executable,str(script),'--trial',str(self.h.root),'--trial-id',self.h.trial_id,
             '--case',self.h.case_id,'--ledger',str(self.root/'cli.sqlite')]
        r=subprocess.run(cmd,text=True,capture_output=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(json.loads(r.stdout)['status'],'success')
        cmd[-1]=str(self.h.root/'operator'/'bad.sqlite')
        r=subprocess.run(cmd,text=True,capture_output=True)
        self.assertEqual(r.returncode,2)
        self.assertFalse((self.h.root/'operator'/'bad.sqlite').exists())

    def test_small_response_limit_also_bounds_error_envelope(self):
        services=RuntimeServices(GetCaseServices(self.repo,GetCaseLimits(512)),self.svc.ledger)
        r=self.call(services=services)
        self.assertLessEqual(len(serialize_get_case_result(r,limits=services.get_case_services.limits)),512)
