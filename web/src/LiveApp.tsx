import React, { useEffect, useRef, useState } from 'react';
import {
  Activity, ArrowRight, BarChart3, ChevronRight, Clock, Database,
  FileText, Layers, Lock, Play, RefreshCw, RotateCcw, Shield, Terminal, UserCheck,
} from 'lucide-react';
import './live.css';

type Action = 'start' | 'run' | 'approve' | 'interrupt' | 'resume' | 'advance';
type Scenario = { id: string; title: string; description: string; case_id: string };
type Transaction = { transaction_id: string; amount_krw: number; status: string; external_id?: string };
type Audit = {
  call_id: string; tool_name: string; session_id: string;
  status: string; elapsed_ms: number | null; code: string | null;
};
type Operation = { operation_id: string; input_version: number; state: string; result_ref: string };
type Decision = { outcome: string; reason: string; decision_id: string; result_ref: string | null; draft_id: string | null };
type Trial = {
  trial_id: string; batch_id: string; run_id: string; as_of: string;
  input_version: number; input_digest: string; processing_state: string;
  summary: {
    ingestion_complete: boolean; matched_count: number;
    mismatched_ids: string[]; missing_external_ids: string[]; unexpected_external_ids: string[];
    duplicate_transaction_ids: string[]; unsettled_transaction_ids: string[];
  };
  internal_total_krw: number; external_total_krw: number; internal: Transaction[]; external: Transaction[];
  decision: Decision | null; evaluation: { passed: boolean; checks: Record<string, boolean> } | null;
  operations: Operation[]; approvals: { approval_id: string; status: string; expires_at: string }[];
  result_count: number; decision_count: number; audit: Audit[]; budget: { used: number; max_attempts: number };
  model_advice: string | null;
};
type CaseState = Scenario & {
  stage: string; available: Action[]; revision: string; model: string | null;
} & (Trial | { trial_id: null });
type ActionResult = { status: string; exit_code?: number; recovery?: string; reason?: string };

const labels: Record<string, string> = {
  NEW: '실행 전', READY: '조사 준비', PAUSED_FOR_APPROVAL: '승인 대기', APPROVED: '승인 완료',
  INTERRUPTED: '작업 중단', FINAL: '판정 기록 완료',
  WAITING_FOR_INPUT: '자료 도착 대기', ESCALATED: '담당자 검토 요청', REPROCESSED: '재처리 완료',
  INPUT_NOT_COMPLETE: '정산 자료가 모두 도착하지 않았습니다.',
  TRANSACTION_MISMATCH: '거래별 대사 결과에 불일치가 있습니다.',
  CUTOFF_PASSED: '재처리 마감 시각이 지났습니다.',
  VERIFIED_COMMITTED_EFFECT: 'DB에 반영된 거래별 결과와 승인 사용을 확인했습니다.',
  RECOVERED_COMMITTED_EFFECT: '재처리 호출 없이 기존 작업과 거래별 결과를 확인했습니다.',
  VERIFIED_EXISTING_EFFECT: '이미 반영된 거래별 결과를 확인했습니다.',
  ADVISOR_DISAGREEMENT: '모델 제안과 코드 판정이 달라 검토가 필요합니다.',
  BATCH_NOT_ELIGIBLE: '재처리 사전 조건을 다시 확인해야 합니다.',
};
const buttonLabels: Record<Action, string> = {
  start: '사례 조사 시작', run: '재처리 실행', approve: '재처리 승인',
  interrupt: '실행 중 프로세스 종료', resume: '새 세션에서 복구', advance: '지연 자료 반영 및 재조사',
};
const errors: Record<string, string> = {
  STATE_CHANGED: '다른 요청에서 상태가 바뀌었습니다. 새로 읽어 온 내용을 확인하세요.',
  RUN_BUSY: '이 사례의 작업이 진행 중입니다. 잠시 후 새로고침하세요.',
  ACTION_UNAVAILABLE: '현재 단계에서 실행할 수 없는 작업입니다.',
  MODEL_UNAVAILABLE: '설치된 Ollama 모델과 로컬 서버를 확인하세요.',
  WORKER_TIMEOUT: '작업 시간이 초과되었습니다. 반영 상태를 확인한 뒤 다음 단계를 선택하세요.',
  EFFECT_UNKNOWN: '현재 작업의 반영 여부를 확인하지 못했습니다.',
  NOT_CONNECTED: 'Python 서버에 연결하지 못했습니다. 실행 가이드를 확인하세요.',
};
const checkLabels: Record<string, string> = {
  durable_decision: '저장된 최종 결정', decision_snapshot: '입력 시점·버전 일치',
  expected_outcome: '독립 업무 판정 일치', single_bound_effect: '대상 작업 1건',
  consumed_bound_approval: '승인 사용과 작업 연결', exact_result_rows: '거래별 결과 일치',
  no_effect_for_decision_snapshot: '이 입력에 대한 재처리 0건',
};
const fmt = new Intl.NumberFormat('ko-KR');
const time = (at: string) => `${new Date(at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false })} KST`;

async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error('NOT_CONNECTED');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'LOCAL_RUNTIME_ERROR');
  return data;
}

export default function LiveApp() {
  const initial = new URLSearchParams(location.search).get('case') || 'after_commit';
  const [selected, select] = useState(initial);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [state, setState] = useState<CaseState | null>(null);
  const [busy, setBusy] = useState<Action | 'load' | null>('load');
  const [error, setError] = useState('');
  const [last, setLast] = useState<ActionResult | null>(null);
  const [confirming, setConfirming] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const sequence = useRef(0);
  const pending = useRef(false);

  const fetchCase = async (id: string) => {
    const ticket = ++sequence.current;
    setBusy('load');
    setError('');
    try {
      const fresh = await getJSON<CaseState>(`/api/cases/${id}`);
      if (ticket === sequence.current) setState(fresh);
    } catch (err) {
      if (ticket === sequence.current) setError((err as Error).message);
    } finally {
      if (ticket === sequence.current) setBusy(null);
    }
  };

  useEffect(() => {
    getJSON<{ scenarios: Scenario[] }>('/api/workspace').then(result => {
      setScenarios(result.scenarios);
      const id = result.scenarios.some(item => item.id === initial) ? initial : 'after_commit';
      select(id);
      fetchCase(id);
    }).catch(() => {
      setError('NOT_CONNECTED');
      setBusy(null);
    });
  }, []);

  useEffect(() => {
    if (confirming) dialog.current?.showModal();
    else dialog.current?.close();
  }, [confirming]);

  const pick = (id: string) => {
    if (pending.current || id === selected) return;
    select(id);
    setState(null);
    setLast(null);
    setConfirming(false);
    const url = new URL(location.href);
    url.searchParams.set('case', id);
    history.replaceState(null, '', url);
    fetchCase(id);
  };

  async function act(action: Action) {
    if (pending.current || !state || !state.available.includes(action)) return;
    pending.current = true;
    setConfirming(false);
    setBusy(action);
    setLast(null);
    setError('');
    try {
      const result = await getJSON<{ case: CaseState; last_action: ActionResult }>(
        `/api/cases/${state.id}/actions`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workspace': 'local-mvp' },
          body: JSON.stringify({ action, revision: state.revision }),
        });
      setState(result.case);
      setLast(result.last_action);
    } catch (err) {
      const code = (err as Error).message;
      try {
        setState(await getJSON<CaseState>(`/api/cases/${state.id}`));
      } catch {
        // Keep the last observed case visible if the server is unavailable.
      }
      setError(code);
    } finally {
      setBusy(null);
      pending.current = false;
    }
  }

  const trial = state?.trial_id ? state as CaseState & Trial : null;
  const firstAction = state?.available[0];
  const primary = firstAction === 'start' && trial ? undefined : firstAction;
  const descriptor = scenarios.find(item => item.id === selected);
  const title = state?.title || descriptor?.title || '금융 대사 Agent';
  const discrepancyIds = new Set(trial ? [
    ...trial.summary.mismatched_ids, ...trial.summary.missing_external_ids,
    ...trial.summary.unexpected_external_ids, ...trial.summary.duplicate_transaction_ids,
    ...trial.summary.unsettled_transaction_ids,
  ] : []);
  const operation = trial?.operations.at(-1);
  const approval = trial?.approvals.at(-1);
  const interrupted = state?.stage === 'INTERRUPTED';
  const recoveryVerified = trial?.decision?.reason === 'RECOVERED_COMMITTED_EFFECT';
  const retriedAfterRollback = trial?.id === 'before_commit' && trial.decision?.outcome === 'REPROCESSED'
    && trial.audit.some(call => call.tool_name === 'execute_reprocess' && call.status === 'RESERVED');

  return (
    <div className="live-shell">
      <aside className="live-sidebar">
        <header className="live-brand">
          <span className="live-brand-icon"><Shield size={21} /></span>
          <div><strong>Financial Reconciliation</strong><small>Agent Harness</small></div>
        </header>
        <div className="live-sidebar-section">합성 데이터 시나리오</div>
        <nav className="live-nav" aria-label="실험 사례">
          {scenarios.map(item => (
            <button key={item.id} className={selected === item.id ? 'is-selected' : ''}
              aria-current={selected === item.id ? 'page' : undefined} onClick={() => pick(item.id)}
              disabled={busy !== null && busy !== 'load'}>
              <span>{item.title}<code>{item.case_id}</code></span><ChevronRight size={15} />
            </button>
          ))}
        </nav>
        <div className="live-sidebar-foot"><span className="live-dot" />{scenarios.length ? '로컬 Python · SQLite' : '서버 확인 중'}</div>
      </aside>
      <main className="live-main">
        <header className="live-bar">
          <span><Layers size={16} /> 대사 운영 워크벤치</span>
          <div>
            {trial && <a href="#audit"><Terminal size={15} /> 호출 기록</a>}
            <button aria-label="현재 상태 새로고침" title="현재 상태 새로고침"
              onClick={() => fetchCase(selected)} disabled={!!busy || !scenarios.length}><RefreshCw size={17} /></button>
          </div>
        </header>
        <div className="live-content">
          <div className="live-hero">
            <div>
              <div className="live-kicker"><Activity size={15} /> {state?.model ? `모델 제안 · ${state.model}` : '규칙 기반 실행 · R0'}</div>
              <h1>{title}</h1>
              <p>{state?.description || descriptor?.description}</p>
            </div>
            {state && <div className="live-actions">
              {trial && <button className="live-secondary" disabled={!!busy} onClick={() => act('start')}>
                <RotateCcw size={16} /> 새 실험
              </button>}
              {primary && <button className="live-primary" disabled={!!busy}
                onClick={() => primary === 'approve' ? setConfirming(true) : act(primary)}>
                {primary === 'approve' ? <UserCheck size={17} /> : <Play size={16} />}
                {busy === primary ? '처리 중…' : primary === 'run' && state.stage === 'READY' ? '사례 조사 실행' : buttonLabels[primary]}
              </button>}
            </div>}
          </div>

          {error && <div className="live-banner live-error" role="alert">{errors[error] || '실행 중 오류가 발생했습니다.'} <code>{error}</code>
            {error === 'NOT_CONNECTED' && <p>저장소에서 <code>python3.11 scripts/web.py</code>를 실행하고 표시되는 주소를 여세요.</p>}
          </div>}
          {busy && <p className="live-busy" role="status">{busy === 'load' ? '상태를 읽는 중…' : 'Python 작업이 진행 중입니다…'}</p>}

          {state && !trial && <div className="live-card live-empty">
            <span className="live-empty-icon"><Database size={22} /></span>
            <h2>새 실험을 시작할 수 있습니다</h2>
            <p>합성 사건 <code>{state.case_id}</code>를 별도 업무 DB에 적재합니다.</p>
            {state.id.includes('commit') && <div className="live-step-row">
              <span>사례 조사</span><ArrowRight size={14} /><span>운영자 승인</span><ArrowRight size={14} />
              <span>프로세스 종료</span><ArrowRight size={14} /><span>상태 조회 후 복구</span>
            </div>}
          </div>}

          {trial && <>
            <div className="live-context"><code>{trial.case_id} / {trial.batch_id} / {trial.run_id}</code>
              <span>{time(trial.as_of)}</span><span className={`live-chip ${interrupted ? 'amber' : trial.decision ? 'green' : 'gray'}`}>
                {labels[trial.stage] || trial.stage}</span></div>
            {(interrupted || recoveryVerified || retriedAfterRollback || last?.recovery) &&
              <div className={`live-banner ${interrupted ? 'live-wait' : 'live-ok'}`} role="status">
                {interrupted ? <><strong>작업 프로세스가 응답 전에 종료됐습니다.</strong>{' '}
                  DB 작업 {trial.operations.length}건 · 최종 결정 {trial.decision_count}건
                  {last?.exit_code && <code>exit {last.exit_code}</code>}</> :
                  retriedAfterRollback ? <><strong>미반영을 확인하고 같은 의도로 실행했습니다.</strong> 재처리 결과는 1건입니다.</> :
                  <><strong>기존 결과를 회수했습니다.</strong> 새 세션이 작업 상태를 먼저 확인했습니다.</>}
              </div>}
            {last?.status === 'UNRESOLVED' && <div className="live-banner live-wait" role="status">
              {errors[last.reason || ''] || '현재 처리 결과를 확정할 수 없습니다.'} <code>{last.reason}</code>
            </div>}

            <section className="live-stats" aria-label="SQLite 관측 요약">
              <Metric icon={<Layers size={18} />} label="입력 스냅샷" value={`v${trial.input_version}`}
                detail={trial.summary.ingestion_complete ? '입력 수신 완료' : '일부 입력 미도착'} />
              <Metric icon={<Database size={18} />} label="저장된 작업" value={`${trial.operations.length}건`}
                detail={`거래별 결과 ${trial.result_count}행`} />
              <Metric icon={<Lock size={17} />} label="승인" value={approval?.status === 'CONSUMED' ? '사용 완료' : approval ? '발급 완료' : '미발급'}
                detail={approval ? approval.approval_id : '운영자 승인 기록 없음'} />
              <Metric icon={<Activity size={17} />} label="도구 호출 예산" value={`${trial.budget.used} / ${trial.budget.max_attempts}`}
                detail="세션이 바뀌어도 누적" />
            </section>

            <div className="live-grid">
              <section className="live-card live-records">
                <PanelTitle icon={<BarChart3 size={17} />} title="내부 거래 · 외부 정산" />
                <div className="live-totals"><span>내부 <strong>{fmt.format(trial.internal_total_krw)}원</strong></span>
                  <span>외부 <strong>{fmt.format(trial.external_total_krw)}원</strong></span></div>
                <div className="live-scroll"><table><thead><tr><th>거래 ID</th><th>내부 금액</th><th>외부 금액</th><th>대조</th></tr></thead>
                  <tbody>{[...new Set([...trial.internal, ...trial.external].map(row => row.transaction_id))].sort().map(id => {
                    const left = trial.internal.find(row => row.transaction_id === id);
                    const right = trial.external.filter(row => row.transaction_id === id);
                    return <tr key={id}><td><code>{id}</code></td><td>{left ? fmt.format(left.amount_krw) : '—'}</td>
                      <td>{right.length ? right.map(row => fmt.format(row.amount_krw)).join(' · ') : '—'}</td>
                      <td><span className={`live-chip ${discrepancyIds.has(id) ? 'amber' : 'green'}`}>
                        {discrepancyIds.has(id) ? right.length ? '불일치' : '미도착' : '일치'}</span></td></tr>;
                  })}</tbody></table></div>
              </section>
              <section className="live-card live-result">
                <PanelTitle icon={<FileText size={17} />} title="판정과 독립 검증" />
                {trial.decision ? <><h3>{labels[trial.decision.outcome] || trial.decision.outcome}</h3>
                  <p>{labels[trial.decision.reason] || trial.decision.reason}</p></> :
                  <p>{labels[trial.stage] || trial.stage} · 최종 결정이 기록되면 독립 SQL 검증이 표시됩니다.</p>}
                {trial.model_advice && <p>로컬 모델 제안 <code>{trial.model_advice}</code></p>}
                {trial.evaluation && <ul className="live-checks">
                  {Object.entries(trial.evaluation.checks).map(([name, ok]) => <li key={name}>
                    <span>{checkLabels[name] || name}</span><strong className={ok ? 'live-pass' : 'live-fail'}>{ok ? '통과' : '실패'}</strong>
                  </li>)}
                </ul>}
                {operation && <div className="live-ref"><span>작업 참조</span><code>{operation.operation_id}</code></div>}
                {(trial.decision?.result_ref || operation?.result_ref) &&
                  <div className="live-ref"><span>결과 참조</span><code>{trial.decision?.result_ref || operation?.result_ref}</code></div>}
                {trial.decision?.draft_id &&
                  <div className="live-ref"><span>로컬 이관 초안</span><code>{trial.decision.draft_id}</code></div>}
              </section>
            </div>

            <section className="live-card" id="audit">
              <PanelTitle icon={<Terminal size={17} />} title="실제 도구 호출 기록" />
              <div className="live-scroll"><table><thead><tr><th>도구</th><th>세션</th><th>상태</th><th>소요</th></tr></thead>
                <tbody>{trial.audit.map(call => <tr key={call.call_id}>
                  <td><code>{call.tool_name}</code></td><td><code title={call.session_id}>{call.session_id.slice(0, 16)}…</code></td>
                  <td><span className={`live-chip ${call.status === 'success' ? 'green' : 'amber'}`}>
                    {call.status === 'RESERVED' ? '응답 기록 없음' : call.code || call.status}</span></td>
                  <td>{call.elapsed_ms === null ? '—' : `${call.elapsed_ms} ms`}</td>
                </tr>)}</tbody></table></div>
              {!trial.audit.length && <p className="live-muted">이 실험에는 저장된 호출이 없습니다.</p>}
            </section>

            <footer className="live-footer"><Clock size={14} /><code>{trial.input_digest}</code></footer>
          </>}
        </div>
      </main>
      <dialog ref={dialog} className="live-dialog" aria-labelledby="operator-approval"
        onCancel={() => setConfirming(false)} onClose={() => setConfirming(false)}>
        <h2 id="operator-approval">운영자 승인 기록</h2>
        {trial && <p><code>{trial.case_id}</code> · 입력 <strong>v{trial.input_version}</strong>의 재처리를 승인합니다.</p>}
        <p>승인 후 실행 버튼으로 재처리를 시작합니다.</p>
        <div className="live-actions"><button className="live-secondary" onClick={() => setConfirming(false)}>취소</button>
          <button className="live-primary" onClick={() => act('approve')}><UserCheck size={16} /> 승인 기록</button>
        </div>
      </dialog>
    </div>
  );
}

function Metric({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail: string }) {
  return <div className="live-card live-metric">
    <div>{icon}<span>{label}</span></div><strong>{value}</strong><small title={detail}>{detail}</small>
  </div>;
}

function PanelTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return <h2 className="live-panel-title">{icon}{title}</h2>;
}
