#!/usr/bin/env python3
"""Record full regression, per-method Tool 1 results and a 100-row CLI replay.

No model calls. Writes only to the explicitly selected, new report directory
and temporary fixture directories. Does not edit product code.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();root=args.repo.resolve();out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    def git(*parts):return subprocess.check_output(['git',*parts],cwd=root,text=True).strip()
    def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    started=datetime.now(timezone.utc).isoformat()
    tracked=git('ls-files').splitlines()
    source={f:digest(root/f) for f in tracked if f.startswith(('src/','tests/','configs/','scripts/'))}
    env=dict(__import__('os').environ,PYTHONDONTWRITEBYTECODE='1')
    begin=time.monotonic()
    full=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=root,
                        env=env,text=True,capture_output=True)
    full_elapsed=time.monotonic()-begin
    log=full.stdout+full.stderr
    log=log.replace(str(root),'<repo>')
    (out/'unittest-full.txt').write_text(log)
    import re
    count=re.search(r'Ran (\d+) tests?',log)
    full_summary={'command':'python3.11 -m unittest discover -s tests -v',
                  'returncode':full.returncode,'tests_run':int(count[1]) if count else None,
                  'elapsed_seconds':round(full_elapsed,6)}
    sys.path[:0]=[str(root/'tests'),str(root/'src')]
    locations={node.name:node.lineno for node in ast.walk(ast.parse((root/'tests/test_get_case.py').read_text()))
               if isinstance(node,ast.FunctionDef) and node.name.startswith('test_')}
    records=[]
    class Result(unittest.TextTestResult):
        def startTest(self,test):
            super().startTest(test)
            self.current={'test_id':test.id(),'method':test._testMethodName,'status':'running',
                          'source':f'tests/test_get_case.py#L{locations[test._testMethodName]}',
                          'subtests':0,'failed_subtests':0}
            self.start=time.monotonic()
        def addSuccess(self,test):super().addSuccess(test);self.current['status']='passed'
        def addError(self,test,err):super().addError(test,err);self.current['status']='error'
        def addFailure(self,test,err):super().addFailure(test,err);self.current['status']='failed'
        def addSkip(self,test,reason):super().addSkip(test,reason);self.current['status']='skipped'
        def addSubTest(self,test,subtest,err):
            super().addSubTest(test,subtest,err)
            self.current['subtests']+=1
            if err is not None:
                self.current['failed_subtests']+=1;self.current['status']='failed'
        def stopTest(self,test):
            self.current['elapsed_ms']=round((time.monotonic()-self.start)*1000,3)
            records.append(self.current.copy());super().stopTest(test)
    stream=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_get_case.py')
    begin=time.monotonic()
    result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
    tool_elapsed=time.monotonic()-begin
    (out/'unittest-tool01.txt').write_text(stream.getvalue().replace(str(root),'<repo>'))
    transcript=[]
    with tempfile.TemporaryDirectory(prefix='tool01-verification-') as tmp:
        temp=Path(tmp)
        def execute(parts):
            cmd=[sys.executable,*[str(x) for x in parts]]
            r=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True)
            item={'command':'python3.11 '+' '.join(str(x).replace(str(temp),'<tmp>') for x in parts),
                  'returncode':r.returncode}
            transcript.append(item)
            if r.returncode:raise RuntimeError('CLI validation failed; see returncode in transcript')
            return json.loads(r.stdout) if r.stdout.strip().startswith('{') else None
        execute(['scripts/dataset.py','build','--config','configs/synthetic-pilot.json','--out',temp/'pilot'])
        case='c_2e8b6b7c4702'
        execute(['scripts/trial.py','prepare','--dataset',temp/'pilot','--case',case,
                 '--trial-id','tool1-delay','--out',temp/'trial'])
        call=['scripts/get_case.py','--trial',temp/'trial','--case',case,'--trial-id','tool1-delay',
              '--ledger',temp/'runtime/calls.sqlite']
        def observe(r):
            return {'status':r['status'],**{k:r['data'][k] for k in
                ('case_id','batch_id','input_version','input_digest','decision_at','ingestion_complete','processing_state')},
                'operation_ref_count':len(r['data']['operation_refs']),
                'external_note_count':len(r['data']['external_notes'])}
        before_db=digest(temp/'trial/business/business.sqlite')
        before=observe(execute(call))
        before_read_only=before_db==digest(temp/'trial/business/business.sqlite')
        execute(['scripts/trial.py','advance','--trial',temp/'trial','--case',case,
                 '--trial-id','tool1-delay','--to','2026-11-05T10:05:00+09:00'])
        after_db=digest(temp/'trial/business/business.sqlite')
        after=observe(execute(call+['--session-id','session-2']))
        after_read_only=after_db==digest(temp/'trial/business/business.sqlite')
        with sqlite3.connect(temp/'trial/business/business.sqlite') as db:
            external_rows=db.execute('SELECT COUNT(*) FROM settlement_history WHERE superseded_known_at IS NULL').fetchone()[0]
        with sqlite3.connect(temp/'runtime/calls.sqlite') as db:
            used=db.execute('SELECT used FROM budgets').fetchone()[0]
            sessions=[r[0] for r in db.execute('SELECT session_id FROM calls ORDER BY rowid')]
            statuses=[r[0] for r in db.execute('SELECT status FROM calls ORDER BY rowid')]
        assert (before['status'],before['input_version'],before['ingestion_complete'])==('success',1,False)
        assert (after['status'],after['input_version'],after['ingestion_complete'])==('success',2,True)
        assert external_rows==100 and used==2 and before_read_only and after_read_only
        manual={'config':'configs/synthetic-pilot.json','rows_per_batch':100,'seed':42,
                'before':before,'after':after,'external_rows_after':external_rows,
                'business_db_unchanged_by_reads':[before_read_only,after_read_only],
                'ledger_used':used,'ledger_sessions':sessions,'ledger_statuses':statuses,'status':'passed'}
    (out/'cli-replay.json').write_text(json.dumps({'commands':transcript,'observations':manual},ensure_ascii=False,indent=2)+'\n')
    assert source=={f:digest(root/f) for f in source},'Tracked source changed during validation'
    summary={'schema':'tool01-verification-1','started_at_utc':started,
        'finished_at_utc':datetime.now(timezone.utc).isoformat(),'source_commit':git('rev-parse','HEAD'),
        'tracked_changes':bool(git('diff','HEAD','--name-only')),'untracked_files_excluded':True,
        'runner_sha256':digest(Path(__file__)),
        'environment':{'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,
                       'system':platform.system(),'release':platform.release(),'machine':platform.machine()},
        'fixture':{'unit_rows_per_batch':3,'seed':42,'unit_profiles':['pilot','benchmark'],
                   'note':'Tool tests use pilot; benchmark is generated by the shared Fixtures base.'},
        'full_suite':full_summary,'tool_suite':{'tests_run':result.testsRun,'passed':sum(r['status']=='passed' for r in records),
           'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
           'elapsed_seconds':round(tool_elapsed,6),'tests':records},
        'manual_cli':manual,'source_sha256':source,'model_calls':0,
        'artifact_sha256':{f.name:digest(f) for f in out.iterdir() if f.is_file()}}
    (out/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'out':str(out),'full':full_summary,'tool_tests':result.testsRun,
                      'tool_passed':sum(r['status']=='passed' for r in records),'cli':manual['status']}))
    return 0 if full.returncode==0 and result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
