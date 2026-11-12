#!/usr/bin/env python3
"""Run TOOL-01 against a prepared trial without an LLM, HTTP, or SQL input."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from reconcile_agent.trials.lifecycle import resume_trial
from reconcile_agent.trials.contracts import RunnerConfig
from reconcile_agent.tools.contracts import ToolInvocation
from reconcile_agent.tools.dispatcher import RuntimeServices, dispatch_tool
from reconcile_agent.tools.get_case import GetCaseServices, GetCaseLimits
from reconcile_agent.tools.ledger import ToolCallLedger
from reconcile_agent.tools.runner import context_for_trial


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trial',type=Path,required=True)
    p.add_argument('--trial-id',required=True)
    p.add_argument('--case',required=True)
    p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--run-id',default='tool1-manual')
    p.add_argument('--session-id',default='session-1')
    p.add_argument('--principal',default='local-runner')
    p.add_argument('--setting',default='development')
    p.add_argument('--repeat',type=int,default=0)
    args=p.parse_args()
    try:
        h=resume_trial(args.trial,args.trial_id,args.case,
                       RunnerConfig(args.principal,args.setting,args.repeat))
        # Keep runtime state physically outside all trial role stores.
        trial=args.trial.resolve()
        dest=args.ledger.resolve()
        if dest == trial or trial in dest.parents:
            raise ValueError('ledger must be outside the trial directory')
        services=RuntimeServices(GetCaseServices(h.business_repository(),GetCaseLimits()),ToolCallLedger(args.ledger))
        result=dispatch_tool(ToolInvocation('tool-call-1','get_case',{'case_id':args.case}),
                             context_for_trial(h,logical_run_id=args.run_id,session_id=args.session_id),services)
        print(json.dumps(asdict(result),ensure_ascii=False,allow_nan=False))
        return 0 if result.status=='success' else 2
    except Exception:
        print(json.dumps({'status':'error','data':None,'diagnostics':{'setup':{
            'status':'failed','error_code':'SETUP_ERROR','reason':'trial 및 runtime 설정을 확인하세요.'}},'outcome':None},ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
