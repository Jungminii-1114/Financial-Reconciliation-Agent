"""Context factory for trusted local experiments, not an authentication server."""
from datetime import datetime, timezone
from uuid import uuid4
from .contracts import TargetScope, TrustedCallContext
from ..business.connection import connect
from ..trials.contracts import TrialHandle


def context_for_trial(handle: TrialHandle, *, logical_run_id: str, session_id: str,
                      budget_group_id: str | None = None) -> TrustedCallContext:
    with connect(handle.business_db) as db:
        policy = db.execute('SELECT policy_version FROM cases WHERE case_id=?',(handle.case_id,)).fetchone()
        if policy is None: raise ValueError('허용된 사건이 없습니다.')
    return TrustedCallContext(handle.principal_id,handle.trial_id,logical_run_id,session_id,
                              'call-'+uuid4().hex,budget_group_id or handle.trial_id,
                              frozenset({TargetScope(handle.case_id,handle.batch_id)}),frozenset({'get_case'}),
                              'local-scope-1',policy[0],datetime.now(timezone.utc),handle.config.config_hash)
