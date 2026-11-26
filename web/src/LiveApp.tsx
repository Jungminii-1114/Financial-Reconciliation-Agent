import React, { useEffect, useRef, useState } from 'react';
import {
  Activity, ArrowRight, Check, CircleAlert, CircleCheck, CircleDashed, CircleMinus, Database,
  FileText, GitBranch, LoaderCircle, Minus, Play, RefreshCw, RotateCcw, Scale, ShieldCheck,
  Terminal, TriangleAlert, Undo2, UserCheck, Zap,
} from 'lucide-react';
import './live.css';

/* ───────────────────────── API types (server contract, unchanged) ───────────────────────── */

type Action = 'start' | 'run' | 'approve' | 'interrupt' | 'resume' | 'advance';
type Scenario = { id: string; title: string; description: string; case_id: string };
type Transaction = { transaction_id: string; amount_krw: number; status: string; external_id?: string };
type Audit = {
  call_id: string; run_id: string; tool_name: string; session_id: string; started_at: string;
  status: string; elapsed_ms: number | null; code: string | null;
};
type Operation = { operation_id: string; input_version: number; state: string; result_ref: string; committed_at?: string };
type Approval = { approval_id: string; input_version: number; granted_by: string; status: string; expires_at: string };
type Decision = {
  outcome: string; reason: string; decision_id: string; result_ref: string | null; draft_id: string | null;
  operation_id?: string | null; evidence_ref?: string; input_version?: number;
};
type Evaluation = { passed: boolean; expected_outcome?: string; observed_outcome?: string; checks: Record<string, boolean> };
type Trial = {
  trial_id: string; batch_id: string; run_id: string; as_of: string;
  input_version: number; input_digest: string; processing_state: string;
  summary: {
    internal_count: number; external_count: number;
    ingestion_complete: boolean; matched_count: number;
    mismatched_ids: string[]; missing_external_ids: string[]; unexpected_external_ids: string[];
    duplicate_transaction_ids: string[]; unsettled_transaction_ids: string[];
  };
  internal_total_krw: number; external_total_krw: number; internal: Transaction[]; external: Transaction[];
  decision: Decision | null; evaluation: Evaluation | null;
  operations: Operation[]; approvals: Approval[];
  result_count: number; decision_count: number; audit: Audit[]; budget: { used: number; max_attempts: number };
  model_advice: string | null;
};
type CaseState = Scenario & {
  stage: string; available: Action[]; revision: string; model: string | null;
} & (Trial | { trial_id: null });
type ActionResult = { status: string; exit_code?: number; recovery?: string; reason?: string };
type LiveTrial = CaseState & Trial;
type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral';

/* ───────────────────────── Copy ───────────────────────── */

const ORDER = ['after_commit', 'before_commit', 'delayed_arrival', 'amount_swap', 'clean'];
const FAULT_CASES = new Set(['after_commit', 'before_commit']);
const WRITE_TOOLS = new Set(['execute_reprocess', 'prepare_escalation']);

const stageLabel: Record<string, string> = {
  NEW: '실행 전', READY: '조사 준비', PAUSED_FOR_APPROVAL: '승인 대기', APPROVED: '실행 대기',
  INTERRUPTED: '실행 중단', FINAL: '판정 기록 완료',
};
const outcomeLabel: Record<string, string> = {
  REPROCESSED: '재처리 완료', WAITING_FOR_INPUT: '자료 도착 대기', ESCALATED: '담당자 이관',
};
const outcomeAction: Record<string, string> = { REPROCESSED: 'REPROCESS', WAITING_FOR_INPUT: 'WAIT', ESCALATED: 'ESCALATE' };
const outcomeMeaning: Record<string, string> = {
  REPROCESSED: '승인된 재처리가 DB에 반영됐고, 그 결과로 최종 결정을 기록했습니다.',
  WAITING_FOR_INPUT: '정산 자료가 모두 도착할 때까지 기다리기로 판정했습니다. 업무 쓰기는 없습니다.',
  ESCALATED: '거래별 불일치를 담당자가 검토하도록 로컬 이관 초안을 남겼습니다. 재처리는 하지 않습니다.',
};
const reasonLabel: Record<string, string> = {
  INPUT_NOT_COMPLETE: '정산 자료가 모두 도착하지 않았습니다.',
  TRANSACTION_MISMATCH: '거래별 대사 결과에 불일치가 있습니다.',
  CUTOFF_PASSED: '재처리 마감 시각이 지났습니다.',
  VERIFIED_COMMITTED_EFFECT: 'DB에 반영된 거래별 결과와 승인 사용을 확인했습니다.',
  RECOVERED_COMMITTED_EFFECT: '재처리 호출 없이 기존 작업과 거래별 결과를 확인했습니다.',
  VERIFIED_EXISTING_EFFECT: '이미 반영된 거래별 결과를 확인했습니다.',
  ADVISOR_DISAGREEMENT: '모델 제안과 코드 판정이 달라 검토가 필요합니다.',
  BATCH_NOT_ELIGIBLE: '재처리 사전 조건을 다시 확인해야 합니다.',
};
const actionLabel: Record<Action, string> = {
  start: '사례 조사 시작', run: '재처리 실행', approve: '재처리 승인',
  interrupt: '실행 중 프로세스 종료', resume: '새 세션에서 복구', advance: '지연 자료 반영 및 재조사',
};
const errors: Record<string, string> = {
  STATE_CHANGED: '다른 요청에서 상태가 바뀌었습니다. 최신 관측을 다시 읽은 뒤 조작하세요.',
  RUN_BUSY: '이 사례의 작업이 아직 진행 중입니다. 잠시 후 최신 상태를 다시 읽으세요.',
  ACTION_UNAVAILABLE: '현재 단계에서 허용되지 않는 조작입니다.',
  MODEL_UNAVAILABLE: '설치된 Ollama 모델과 로컬 서버를 확인하세요.',
  WORKER_TIMEOUT: '작업 시간이 초과되었습니다. 반영 상태를 확인한 뒤 다음 단계를 선택하세요.',
  EFFECT_UNKNOWN: '현재 작업의 반영 여부를 확인하지 못했습니다.',
  FAULT_NOT_OBSERVED: '예상한 프로세스 종료가 관측되지 않았습니다. 저장된 상태를 확인하세요.',
  NOT_CONNECTED: 'Python 서버에 연결하지 못했습니다.',
};
const checkLabels: Record<string, string> = {
  durable_decision: '저장된 최종 결정', decision_snapshot: '입력 시점·버전 일치',
  expected_outcome: '독립 업무 판정 일치', single_bound_effect: '대상 작업 1건',
  consumed_bound_approval: '승인 사용과 작업 연결', exact_result_rows: '거래별 결과 행 일치',
  no_effect_for_decision_snapshot: '이 입력에 대한 재처리 0건',
};

const won = new Intl.NumberFormat('ko-KR');
function kst(at: string) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(new Date(at));
  const p = (t: string) => parts.find(x => x.type === t)?.value ?? '';
  return `${p('year')}-${p('month')}-${p('day')} ${p('hour')}:${p('minute')} KST`;
}
const short = (value: string, head = 10, tail = 6) => value.length > head + tail + 3 ? `${value.slice(0, head)}…${value.slice(-tail)}` : value;

async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error('NOT_CONNECTED');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'LOCAL_RUNTIME_ERROR');
  return data;
}

/* ───────────────────────── Derivations from the observed case ───────────────────────── */

type SessionGroup = {
  id: string; run_id: string; calls: Audit[]; index: number;
  role: 'investigate' | 'execute' | 'interrupted' | 'recovery' | 'escalate';
  reads: number; writes: number; reserved: number;
};
const roleLabel: Record<SessionGroup['role'], string> = {
  investigate: '조사 세션', execute: '실행 세션', interrupted: '중단된 실행 세션',
  recovery: '복구 세션', escalate: '이관 세션',
};

function sessions(audit: Audit[]): SessionGroup[] {
  const groups: SessionGroup[] = [];
  for (const call of audit) {
    let group = groups.find(item => item.id === call.session_id);
    if (!group) {
      group = { id: call.session_id, run_id: call.run_id, calls: [], index: groups.length + 1, role: 'investigate', reads: 0, writes: 0, reserved: 0 };
      groups.push(group);
    }
    group.calls.push(call);
  }
  for (const group of groups) {
    group.writes = group.calls.filter(call => WRITE_TOOLS.has(call.tool_name)).length;
    group.reads = group.calls.length - group.writes;
    group.reserved = group.calls.filter(call => call.status === 'RESERVED').length;
    if (group.calls[0]?.tool_name === 'get_operation') group.role = 'recovery';
    else if (group.reserved) group.role = 'interrupted';
    else if (group.calls.some(call => call.tool_name === 'execute_reprocess')) group.role = 'execute';
    else if (group.calls.some(call => call.tool_name === 'prepare_escalation')) group.role = 'escalate';
  }
  return groups;
}

function proposedAction(trial: LiveTrial) {
  if (trial.decision) return outcomeAction[trial.decision.outcome] ?? trial.decision.outcome;
  if (['PAUSED_FOR_APPROVAL', 'APPROVED', 'INTERRUPTED'].includes(trial.stage)) return 'REPROCESS';
  return null;
}

type Step = { key: string; label: string; status: 'done' | 'current' | 'todo' | 'skip' | 'alert'; note: string };
function steps(trial: LiveTrial | null, stage: string): Step[] {
  if (!trial) {
    return ['사례 조사', '조치 제안', '승인 기록', '업무 반영', '최종 결정', '독립 검증'].map((label, i) => ({
      key: label, label, status: i === 0 ? 'current' : 'todo', note: i === 0 ? '대기' : '',
    }));
  }
  const action = proposedAction(trial);
  const needsApproval = action === 'REPROCESS';
  const approval = trial.approvals.filter(a => a.input_version === trial.input_version).at(-1);
  const ops = trial.operations.filter(o => o.input_version === trial.input_version);
  const interrupted = stage === 'INTERRUPTED';
  return [
    { key: 'investigate', label: '사례 조사', status: 'done', note: `입력 v${trial.input_version}` },
    { key: 'propose', label: '조치 제안', status: action ? 'done' : 'current', note: action ?? '조사 대기' },
    {
      key: 'approve', label: '승인 기록',
      status: !needsApproval ? 'skip' : approval ? 'done' : stage === 'PAUSED_FOR_APPROVAL' ? 'current' : 'todo',
      note: !needsApproval ? '해당 없음' : approval ? (approval.status === 'CONSUMED' ? '사용됨' : '기록됨') : '대기',
    },
    {
      key: 'effect', label: '업무 반영',
      status: !needsApproval ? 'skip' : ops.length ? 'done' : interrupted ? 'alert' : stage === 'APPROVED' ? 'current' : 'todo',
      note: !needsApproval ? (action === 'ESCALATE' ? '이관 초안만' : '쓰기 없음') : ops.length ? `작업 ${ops.length}건` : interrupted ? '미반영' : '대기',
    },
    {
      key: 'decide', label: '최종 결정',
      status: trial.decision ? 'done' : interrupted ? 'alert' : 'todo',
      note: trial.decision ? (outcomeLabel[trial.decision.outcome] ?? trial.decision.outcome) : interrupted ? '복구 필요' : '',
    },
    {
      key: 'verify', label: '독립 검증',
      status: trial.evaluation ? (trial.evaluation.passed ? 'done' : 'alert') : 'todo',
      note: trial.evaluation ? (trial.evaluation.passed ? '통과' : '실패') : '결과 없음',
    },
  ];
}

function nextHint(action: Action | undefined, state: CaseState): string {
  switch (action) {
    case 'start': return '합성 사건을 별도 업무 DB에 적재하고 조사 세션을 실행합니다.';
    case 'run': return state.stage === 'READY'
      ? '조사 세션을 실행해 거래를 대조하고 조치를 제안합니다.'
      : '새 세션이 사전 조건을 다시 확인한 뒤 재처리를 실행합니다.';
    case 'approve': return '승인 대상과 입력 버전을 확인하고 승인 레코드를 남깁니다. 업무 쓰기는 일어나지 않습니다.';
    case 'interrupt': return state.id === 'after_commit'
      ? '재처리 작업이 DB 커밋 직후 응답 없이 종료됩니다. 기대 종료 코드 exit 72.'
      : '재처리 작업이 DB 커밋 직전에 종료됩니다. 기대 종료 코드 exit 71.';
    case 'resume': return '새 세션이 get_operation으로 반영 여부를 먼저 확인하고, 필요한 경우에만 실행합니다.';
    case 'advance': return '업무 시각을 5분 진행해 지연 자료를 반영하고, 입력 v2로 새 조사를 실행합니다.';
    default: return '현재 입력에 대한 결정이 기록됐습니다. 다른 조건을 보려면 새 실험을 시작하세요.';
  }
}

/* ───────────────────────── App ───────────────────────── */

export default function LiveApp() {
  const initial = new URLSearchParams(location.search).get('case') || 'after_commit';
  const [selected, select] = useState(initial);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [overview, setOverview] = useState<Record<string, { stage: string; outcome?: string }>>({});
  const [state, setState] = useState<CaseState | null>(null);
  const [busy, setBusy] = useState<Action | 'load' | null>('load');
  const [error, setError] = useState('');
  const [last, setLast] = useState<(ActionResult & { action: Action; at: number }) | null>(null);
  const [confirming, setConfirming] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const sequence = useRef(0);
  const pending = useRef(false);

  const remember = (fresh: CaseState) => {
    setState(fresh);
    setOverview(prev => ({ ...prev, [fresh.id]: { stage: fresh.stage, outcome: fresh.trial_id ? (fresh as LiveTrial).decision?.outcome : undefined } }));
  };

  const fetchCase = async (id: string) => {
    const ticket = ++sequence.current;
    setBusy('load');
    setError('');
    try {
      const fresh = await getJSON<CaseState>(`/api/cases/${id}`);
      if (ticket === sequence.current) remember(fresh);
    } catch (err) {
      if (ticket === sequence.current) setError((err as Error).message);
    } finally {
      if (ticket === sequence.current) setBusy(null);
    }
  };

  useEffect(() => {
    getJSON<{ scenarios: Scenario[] }>('/api/workspace').then(result => {
      const sorted = [...result.scenarios].sort((a, b) => ORDER.indexOf(a.id) - ORDER.indexOf(b.id));
      setScenarios(sorted);
      const id = sorted.some(item => item.id === initial) ? initial : 'after_commit';
      select(id);
      fetchCase(id);
      for (const item of sorted) {
        if (item.id === id) continue;
        getJSON<CaseState>(`/api/cases/${item.id}`).then(other => setOverview(prev => ({
          ...prev, [other.id]: prev[other.id] ?? { stage: other.stage, outcome: other.trial_id ? (other as LiveTrial).decision?.outcome : undefined },
        }))).catch(() => undefined);
      }
    }).catch(() => {
      setError('NOT_CONNECTED');
      setBusy(null);
    });
  }, []);

  useEffect(() => {
    if (confirming && !dialog.current?.open) {
      dialog.current?.showModal();
      cancelButton.current?.focus();
    }
    else if (!confirming && dialog.current?.open) dialog.current.close();
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
      remember(result.case);
      setLast({ ...result.last_action, action, at: Date.now() });
    } catch (err) {
      // Never resend the write: report the failure and re-read what the server actually stored.
      const code = (err as Error).message;
      try {
        remember(await getJSON<CaseState>(`/api/cases/${state.id}`));
      } catch {
        // Keep the last observed case visible if the server is unavailable.
      }
      setError(code);
    } finally {
      setBusy(null);
      pending.current = false;
    }
  }

  const trial = state?.trial_id ? state as LiveTrial : null;
  const working = busy !== null && busy !== 'load';
  const primary: Action | undefined = state
    ? (trial ? state.available.find(a => a !== 'start') : state.available.includes('start') ? 'start' : undefined)
    : undefined;
  const canRestart = !!trial && !!state?.available.includes('start');
  const descriptor = scenarios.find(item => item.id === selected);
  const flow = state ? steps(trial, state.stage) : [];

  return (
    <div className="wb">
      <aside className="wb-rail" aria-label="사례 탐색">
        <header className="wb-brand">
          <span className="wb-brand-mark"><ShieldCheck size={18} /></span>
          <div><strong>대사 운영 워크벤치</strong><small>Reconciliation Agent Harness</small></div>
        </header>
        <nav className="wb-cases">
          {scenarios.length > 0 && <div className="wb-rail-label">P0 시연</div>}
          {scenarios.map((item, i) => {
            const info = overview[item.id];
            return <React.Fragment key={item.id}>
              {i === 1 && <div className="wb-rail-label">비교 경로</div>}
              <button className={`wb-case ${selected === item.id ? 'is-on' : ''}`} onClick={() => pick(item.id)}
                aria-current={selected === item.id ? 'page' : undefined} disabled={working}>
                <span className="wb-case-title">{item.title}</span>
                <span className="wb-case-meta">
                  <StageDot stage={info?.stage} outcome={info?.outcome} />
                  {info ? (info.outcome ? outcomeLabel[info.outcome] : stageLabel[info.stage]) : '…'}
                  <code>{item.id}</code>
                </span>
              </button>
            </React.Fragment>;
          })}
        </nav>
        <footer className="wb-rail-foot">
          <span className={`wb-live ${scenarios.length ? 'on' : ''}`} />
          {scenarios.length ? '로컬 Python · SQLite 연결' : '서버 확인 중'}
        </footer>
      </aside>

      <main className="wb-main" aria-busy={busy !== null}>
        <header className="wb-head">
          <div className="wb-head-text">
            <div className="wb-kicker">
              <Activity size={14} /> {state?.model ? `모델 제안 · ${state.model}` : '결정적 규칙 R0'}
              {state && <code className="wb-kicker-id" title={state.case_id}>{state.case_id}</code>}
            </div>
            <h1>{state?.title || descriptor?.title || '금융 대사 Agent'}</h1>
            <p>{state?.description || descriptor?.description}</p>
            {trial && <dl className="wb-context">
              <div><dt>업무 시각</dt><dd>{kst(trial.as_of)}</dd></div>
              <div><dt>입력</dt><dd><strong>v{trial.input_version}</strong> <Ref value={trial.input_digest} /></dd></div>
              <div><dt>배치 · 실행</dt><dd><code>{trial.batch_id}</code> · <code>{trial.run_id}</code></dd></div>
            </dl>}
          </div>
          <button className="wb-icon" aria-label="최신 상태 다시 읽기" title="최신 상태 다시 읽기"
            onClick={() => fetchCase(selected)} disabled={busy !== null || !scenarios.length}>
            <RefreshCw size={16} className={busy === 'load' ? 'wb-spin' : ''} />
          </button>
        </header>

        {error && <div className={`wb-banner ${error === 'NOT_CONNECTED' ? 'danger' : 'warn'}`} role="alert">
          <CircleAlert size={17} />
          <div>
            <strong>{errors[error] || '실행 중 오류가 발생했습니다.'}</strong> <code>{error}</code>
            {error === 'NOT_CONNECTED' && <p>저장소에서 <code>python3.11 scripts/web.py</code>를 실행하고 표시되는 주소를 여세요.</p>}
            {error !== 'NOT_CONNECTED' && <p>요청은 자동으로 다시 보내지 않습니다. 화면에는 서버에서 다시 읽은 최신 관측을 표시합니다.</p>}
          </div>
          {['STATE_CHANGED', 'RUN_BUSY', 'WORKER_TIMEOUT'].includes(error) &&
            <button className="wb-btn ghost" onClick={() => fetchCase(selected)} disabled={busy !== null}>다시 읽기</button>}
        </div>}

        {state && <section className="wb-ops" aria-label="운영 상황">
          <div className="wb-next">
            <div className="wb-next-state">
              <span className="wb-eyebrow">현재 단계</span>
              <StagePill stage={state.stage} outcome={trial?.decision?.outcome} />
              <code className="wb-code-sm">{state.stage}</code>
            </div>
            <div className="wb-next-act">
              <span className="wb-eyebrow">다음 조작</span>
              <p>{nextHint(primary, state)}</p>
              <div className="wb-next-buttons">
                {primary && <ActionButton action={primary} busy={busy === primary} disabled={busy !== null}
                  onClick={() => primary === 'approve' ? setConfirming(true) : act(primary)} />}
                {canRestart && <button className="wb-btn ghost" disabled={busy !== null} onClick={() => act('start')}
                  title="새 trial을 적재합니다. 이전 실험 기록은 보존됩니다.">
                  {busy === 'start' ? <LoaderCircle size={15} className="wb-spin" /> : <RotateCcw size={15} />} 새 실험
                </button>}
              </div>
            </div>
          </div>
          <ol className="wb-flow" aria-label="처리 단계">
            {flow.map(step => <li key={step.key} className={`wb-step ${step.status}`}
              aria-current={step.status === 'current' ? 'step' : undefined}>
              <span className="wb-step-mark"><StepIcon status={step.status} /></span>
              <span className="wb-step-label">{step.label}</span>
              <span className="wb-step-note">{step.note}</span>
            </li>)}
          </ol>
        </section>}

        {working && <div className="wb-banner info" role="status">
          <LoaderCircle size={17} className="wb-spin" />
          <div><strong>{actionLabel[busy as Action]} 요청을 처리하는 중입니다.</strong>
            <p>Python 작업 프로세스의 응답을 기다립니다. 다른 조작은 잠시 잠깁니다.</p></div>
        </div>}

        {last && trial && <LastAction last={last} trial={trial} />}

        {state && !trial && <section className="wb-card wb-empty">
          <span className="wb-empty-mark"><Database size={22} /></span>
          <h2>이 사례는 아직 실행 전입니다</h2>
          <p>합성 사건 <code>{state.case_id}</code>를 별도 업무 DB에 적재한 뒤 조사 세션을 실행합니다.</p>
          <ScenarioScript id={state.id} />
        </section>}

        {trial && <>
          <StatusBoard trial={trial} last={last} />
          {trial.stage === 'INTERRUPTED' && <InterruptNote trial={trial} last={last} />}
          <RecoverySummary trial={trial} />
          <VersionNote trial={trial} />
          <div className="wb-evidence">
            <Reconciliation trial={trial} />
            <DecisionPanel trial={trial} />
          </div>
          <Ledger trial={trial} />
        </>}
      </main>

      <dialog ref={dialog} className="wb-dialog" aria-labelledby="approval-title" aria-describedby="approval-desc"
        onCancel={() => setConfirming(false)} onClose={() => setConfirming(false)}>
        {trial && <form method="dialog" onSubmit={event => { event.preventDefault(); act('approve'); }}>
          <header>
            <span className="wb-dialog-mark"><UserCheck size={19} /></span>
            <div>
              <h2 id="approval-title">재처리 승인 기록</h2>
              <p id="approval-desc">승인은 실행이 아닙니다. 승인 레코드만 생성되고 업무 DB 작업은 0건으로 유지됩니다.</p>
            </div>
          </header>
          <dl className="wb-dialog-facts">
            <div><dt>승인 대상</dt><dd><code>{trial.case_id}</code> · <code>{trial.batch_id}</code></dd></div>
            <div><dt>입력 버전</dt><dd><strong>v{trial.input_version}</strong> <Ref value={trial.input_digest} /></dd></div>
            <div><dt>업무 시각</dt><dd>{kst(trial.as_of)}</dd></div>
            <div><dt>제안 조치</dt><dd><span className="wb-tag info">REPROCESS</span> 거래 {trial.summary.matched_count}/{trial.summary.internal_count}건 일치 · {trial.summary.ingestion_complete ? '자료 수신 완료' : '자료 미완'}</dd></div>
            <div><dt>기록자</dt><dd>로컬 합성 실험 운영자 <small>(서버 지정 ID)</small></dd></div>
            <div><dt>승인 후 다음 조작</dt><dd><ArrowRight size={13} /> {FAULT_CASES.has(trial.id) ? actionLabel.interrupt : actionLabel.run}</dd></div>
          </dl>
          <footer>
            <button type="button" ref={cancelButton} className="wb-btn ghost" onClick={() => setConfirming(false)}>취소</button>
            <button type="submit" className="wb-btn approve" disabled={busy !== null}><UserCheck size={15} /> 승인 기록</button>
          </footer>
        </form>}
      </dialog>
    </div>
  );
}

/* ───────────────────────── Components ───────────────────────── */

function stageTone(stage?: string, outcome?: string): Tone {
  if (outcome === 'REPROCESSED') return 'ok';
  if (outcome === 'WAITING_FOR_INPUT' || outcome === 'ESCALATED') return 'ok';
  if (stage === 'INTERRUPTED') return 'warn';
  if (stage === 'PAUSED_FOR_APPROVAL' || stage === 'APPROVED') return 'info';
  return 'neutral';
}

function StageDot({ stage, outcome }: { stage?: string; outcome?: string }) {
  return <span className={`wb-dot ${stageTone(stage, outcome)}`} aria-hidden="true" />;
}

function StagePill({ stage, outcome }: { stage: string; outcome?: string }) {
  const text = stage === 'FINAL' && outcome ? `${stageLabel.FINAL} · ${outcomeLabel[outcome] ?? outcome}` : stageLabel[stage] ?? stage;
  return <span className={`wb-pill ${stageTone(stage, outcome)}`}>{text}</span>;
}

function StepIcon({ status }: { status: Step['status'] }) {
  if (status === 'done') return <Check size={13} strokeWidth={3} />;
  if (status === 'alert') return <TriangleAlert size={13} />;
  if (status === 'skip') return <Minus size={13} />;
  if (status === 'current') return <span className="wb-step-pulse" />;
  return null;
}

function ActionButton({ action, busy, disabled, onClick }: { action: Action; busy: boolean; disabled: boolean; onClick: () => void }) {
  const kind = action === 'approve' ? 'approve' : action === 'interrupt' ? 'fault' : action === 'resume' ? 'recover' : 'primary';
  const Icon = action === 'approve' ? UserCheck : action === 'interrupt' ? Zap : action === 'resume' ? Undo2 : Play;
  return <button className={`wb-btn ${kind}`} disabled={disabled} onClick={onClick}>
    {busy ? <LoaderCircle size={16} className="wb-spin" /> : <Icon size={16} />}
    {busy ? (action === 'interrupt' ? '프로세스 종료 대기 중…' : '처리 중…') : actionLabel[action]}
  </button>;
}

function Ref({ value, label }: { value: string; label?: string }) {
  const [open, setOpen] = useState(false);
  return <button type="button" className={`wb-ref ${open ? 'open' : ''}`} onClick={() => setOpen(!open)}
    title={value} aria-label={`${label ?? '참조'} ${open ? '접기' : '전체 값 보기'}: ${value}`} aria-expanded={open}>
    <code>{open ? value : short(value)}</code>
  </button>;
}

function LastAction({ last, trial }: { last: ActionResult & { action: Action }; trial: LiveTrial }) {
  let tone: Tone = 'neutral';
  let text = '';
  const approval = trial.approvals.at(-1);
  if (last.status === 'WORKER_INTERRUPTED') {
    tone = 'warn'; text = `작업 프로세스 종료를 관측했습니다 · exit ${last.exit_code}`;
  } else if (last.action === 'approve' && approval) {
    tone = 'info'; text = `승인 요청 처리 → SQLite 승인 레코드 ${short(approval.approval_id, 8, 4)} (${approval.status}) 관측`;
  } else if (last.recovery === 'RETRIEVED_EXISTING_RESULT') {
    tone = 'ok'; text = '새 세션이 기존 작업을 확인하고 이전 결과를 회수했습니다.';
  } else if (last.status === 'UNRESOLVED') {
    tone = 'warn'; text = `${errors[last.reason || ''] || '현재 처리 결과를 확정할 수 없습니다.'} (${last.reason})`;
  } else if (last.status === 'PAUSED_FOR_APPROVAL') {
    tone = 'info'; text = '조사 세션이 REPROCESS를 제안하고 승인 대기에서 멈췄습니다. 업무 쓰기 없음.';
  } else if (last.status === 'FINAL' && trial.decision) {
    tone = 'ok'; text = `최종 결정 기록 · ${outcomeLabel[trial.decision.outcome] ?? trial.decision.outcome}`;
  } else {
    text = `서버 응답 ${last.status}${last.reason ? ` · ${last.reason}` : ''}`;
  }
  return <div className={`wb-last ${tone}`} role="status">
    <span className="wb-eyebrow">직전 조작</span>
    <strong>{actionLabel[last.action]}</strong><ArrowRight size={13} /><span>{text}</span>
  </div>;
}

function Lens({ title, icon, tone, value, sub, children }: {
  title: string; icon: React.ReactNode; tone: Tone; value: string; sub: string; children?: React.ReactNode;
}) {
  return <section className={`wb-lens ${tone}`} aria-label={title}>
    <header>{icon}<span>{title}</span></header>
    <strong className="wb-lens-value">{value}</strong>
    <p className="wb-lens-sub">{sub}</p>
    {children && <ul className="wb-lens-facts">{children}</ul>}
  </section>;
}

function Fact({ k, children }: { k: string; children: React.ReactNode }) {
  return <li><span>{k}</span><span>{children}</span></li>;
}

function StatusBoard({ trial, last }: { trial: LiveTrial; last: (ActionResult & { action: Action }) | null }) {
  const reserved = trial.audit.filter(c => c.status === 'RESERVED' && c.run_id === trial.run_id).at(-1);
  const approval = trial.approvals.filter(a => a.input_version === trial.input_version).at(-1);
  const op = trial.operations.at(-1);
  const stage = trial.stage;

  const run = {
    NEW: ['실행 전', 'neutral', '조사 세션이 아직 없습니다.'],
    READY: ['조사 준비', 'neutral', '조사 세션을 실행할 수 있습니다.'],
    PAUSED_FOR_APPROVAL: ['승인 대기', 'info', '조사 세션이 제안 후 멈췄습니다. 쓰기 호출은 없습니다.'],
    APPROVED: ['실행 대기', 'info', '승인이 기록됐고 실행 세션은 아직 시작되지 않았습니다.'],
    INTERRUPTED: ['중단', 'warn', last?.exit_code ? `작업 프로세스가 응답 전에 종료됐습니다 (exit ${last.exit_code}).` : '완료 응답이 원장에 남지 않은 호출이 있습니다.'],
    FINAL: ['판정 기록 완료', 'ok', '현재 입력에 대한 최종 결정을 저장했습니다.'],
  }[stage] as [string, Tone, string] ?? [stage, 'neutral', ''];

  const effectTone: Tone = trial.operations.length ? (stage === 'INTERRUPTED' ? 'info' : 'ok') : 'neutral';
  const effectSub = trial.operations.length
    ? (stage === 'INTERRUPTED' ? '실행은 중단됐지만 DB에는 재처리 작업이 커밋돼 있습니다.' : '재처리 작업이 커밋됐습니다.')
    : stage === 'INTERRUPTED' ? '커밋 전에 중단돼 DB에 남은 작업이 없습니다.'
    : trial.decision?.outcome === 'ESCALATED' ? '재처리 작업 없음 · 로컬 이관 초안만 기록했습니다.'
    : '업무 DB 쓰기 없음';

  const ev = trial.evaluation;
  const checks = ev ? Object.values(ev.checks) : [];
  const evalLens: [string, Tone, string] = !ev
    ? ['결과 없음', 'neutral', trial.decision ? '독립 검증을 아직 읽지 못했습니다.' : '최종 결정이 없어 독립 SQL 검증을 실행하지 않았습니다.']
    : ev.passed ? ['통과', 'ok', `${checks.length}개 검사 모두 통과 · 기대 ${ev.expected_outcome ?? '—'} = 관측 ${ev.observed_outcome ?? '—'}`]
    : ['실패', 'danger', `${checks.filter(x => !x).length}개 검사 실패 · 기대 ${ev.expected_outcome ?? '—'} / 관측 ${ev.observed_outcome ?? '—'}`];

  return <section className="wb-board" aria-label="상태 보드">
    <Lens title="실행 상태 · 하네스" icon={<Activity size={15} />} tone={run[1]} value={run[0]} sub={run[2]}>
      <Fact k="stage"><code>{stage}</code></Fact>
      {reserved && stage === 'INTERRUPTED' && <Fact k="응답 기록 없음"><code>{reserved.tool_name}</code></Fact>}
      <Fact k="호출 예산">{trial.budget.used} / {trial.budget.max_attempts}</Fact>
    </Lens>
    <Lens title="업무 효과 · SQLite" icon={<Database size={15} />} tone={effectTone}
      value={trial.operations.length ? `작업 ${trial.operations.length}건` : '작업 0건'} sub={effectSub}>
      <Fact k="거래별 결과">{trial.result_count}행</Fact>
      <Fact k="승인">{approval ? `${approval.status === 'CONSUMED' ? '사용됨' : '기록됨'} · v${approval.input_version}` : '기록 없음'}</Fact>
      {op && <Fact k="결과 참조"><Ref value={op.result_ref} label="결과 참조" /></Fact>}
      <Fact k="배치 상태"><code>{trial.processing_state}</code></Fact>
    </Lens>
    <Lens title="최종 결정" icon={<FileText size={15} />} tone={trial.decision ? 'ok' : stage === 'INTERRUPTED' ? 'warn' : 'neutral'}
      value={trial.decision ? (outcomeLabel[trial.decision.outcome] ?? trial.decision.outcome) : trial.decision_count ? '현재 결정 없음' : '결정 0건'}
      sub={trial.decision ? (reasonLabel[trial.decision.reason] ?? trial.decision.reason)
        : stage === 'INTERRUPTED' ? '완료 결정이 기록되기 전에 중단됐습니다.' : '아직 최종 결정이 없습니다.'}>
      {trial.decision && <Fact k="outcome"><code>{trial.decision.outcome}</code></Fact>}
      <Fact k="trial 저장 결정">{trial.decision_count}건</Fact>
    </Lens>
    <Lens title="독립 검증 · SQL" icon={<Scale size={15} />} tone={evalLens[1]} value={evalLens[0]} sub={evalLens[2]} />
  </section>;
}

function InterruptNote({ trial, last }: { trial: LiveTrial; last: (ActionResult & { action: Action }) | null }) {
  const committed = trial.operations.length > 0;
  return <div className="wb-banner warn wb-interrupt" role="status">
    <TriangleAlert size={18} />
    <div>
      <strong>실행은 중단됐고, 업무 효과는 {committed ? 'DB에 반영돼 있습니다' : 'DB에 남지 않았습니다'}.</strong>
      <p>
        {committed
          ? '응답은 유실됐지만 커밋된 작업은 남아 있습니다. 다시 실행하지 말고, 새 세션이 저장된 작업부터 확인해야 합니다.'
          : '커밋 전에 종료돼 작업 0건입니다. 새 세션이 미반영을 확인한 뒤 같은 실행 의도로 한 번 실행합니다.'}
        {last?.exit_code ? <> 종료 코드 <code>exit {last.exit_code}</code>.</> : <> 새로고침 후에는 저장된 작업·결정 건수와 원장 상태로 표시합니다.</>}
      </p>
    </div>
  </div>;
}

function RecoverySummary({ trial }: { trial: LiveTrial }) {
  const groups = sessions(trial.audit);
  const recovery = groups.find(g => g.role === 'recovery');
  const broken = groups.find(g => g.role === 'interrupted');
  if (!recovery || !broken) return null;
  const sameRef = trial.decision?.result_ref && trial.operations.length === 1 && trial.decision.result_ref === trial.operations[0].result_ref;
  const reexecuted = recovery.calls.some(c => c.tool_name === 'execute_reprocess' && c.status === 'success');
  return <section className="wb-card wb-recovery" aria-label="복구 요약">
    <div className="wb-recovery-side">
      <span className="wb-eyebrow">세션 {broken.index} · {roleLabel[broken.role]}</span>
      <strong className="warn-text">쓰기 시도 {broken.writes}회</strong>
      <p><code>execute_reprocess</code> 응답 기록 없음 (RESERVED)</p>
    </div>
    <ArrowRight className="wb-recovery-arrow" size={20} />
    <div className="wb-recovery-side">
      <span className="wb-eyebrow">세션 {recovery.index} · {roleLabel[recovery.role]}</span>
      <strong className={reexecuted ? 'info-text' : 'ok-text'}>
        {reexecuted ? `확인 후 실행 · 쓰기 ${recovery.writes}회` : `읽기 ${recovery.reads}회 · 쓰기 0회`}
      </strong>
      <p>{recovery.calls.map(c => c.tool_name).join(' → ')}</p>
    </div>
    <div className="wb-recovery-result">
      <span className="wb-eyebrow">결과</span>
      <strong>최종 작업 {trial.operations.length}건</strong>
      <p>{reexecuted ? '미반영을 확인했기 때문에 같은 의도로 한 번 실행했습니다.'
        : sameRef ? '중단 전에 커밋된 작업과 같은 결과 참조를 회수했습니다.' : '저장된 작업을 확인했습니다.'}</p>
    </div>
  </section>;
}

function VersionNote({ trial }: { trial: LiveTrial }) {
  if (trial.id !== 'delayed_arrival' && trial.input_version === 1) return null;
  if (trial.input_version === 1) {
    if (trial.decision?.outcome !== 'WAITING_FOR_INPUT') return null;
    return <div className="wb-version">
      <GitBranch size={16} />
      <div><strong>v1 결정: 자료 도착 대기</strong>
        <p>이 판정은 v1 입력에 대한 정상 결론입니다. 지연 자료가 도착하면 v2로 새 실행(<code>web-v2</code>)을 시작하고 v1 결정은 보존합니다.</p></div>
    </div>;
  }
  const prior = trial.input_version - 1;
  const priorRun = `web-v${prior}`;
  const priorDecisions = trial.decision_count - (trial.decision ? 1 : 0);
  const priorApprovals = trial.approvals.filter(a => a.input_version === prior).length;
  const priorOps = trial.operations.filter(o => o.input_version === prior).length;
  const priorCalls = trial.audit.filter(c => c.run_id === priorRun);
  const priorWrites = priorCalls.filter(c => WRITE_TOOLS.has(c.tool_name)).length;
  return <div className="wb-version">
    <GitBranch size={16} />
    <div>
      <strong>현재 화면은 입력 v{trial.input_version}입니다. v{prior} 기록은 보존됩니다.</strong>
      <ul className="wb-version-facts">
        <li><span>v{prior} 저장 결정</span><b>{priorDecisions}건</b></li>
        <li><span>v{prior} 승인</span><b>{priorApprovals}회</b></li>
        <li><span>v{prior} 업무 작업</span><b>{priorOps}건</b></li>
        <li><span>v{prior} 호출 · 쓰기</span><b>{priorCalls.length}회 · {priorWrites}회</b></li>
      </ul>
      <p className="wb-muted">v{prior} 결정 상세를 함께 보려면 별도 이력 조회 계약이 필요합니다. 여기에는 저장된 건수와 실제 호출만 표시합니다.</p>
    </div>
  </div>;
}

function Reconciliation({ trial }: { trial: LiveTrial }) {
  const s = trial.summary;
  const flag = new Map<string, string>();
  for (const id of s.unsettled_transaction_ids) flag.set(id, '미정산');
  for (const id of s.duplicate_transaction_ids) flag.set(id, '중복');
  for (const id of s.unexpected_external_ids) flag.set(id, '예상 외');
  for (const id of s.missing_external_ids) flag.set(id, '미도착');
  for (const id of s.mismatched_ids) flag.set(id, '금액 불일치');
  const ids = [...new Set([...trial.internal, ...trial.external].map(r => r.transaction_id))].sort();
  const diffTotal = trial.external_total_krw - trial.internal_total_krw;
  const totalsMatch = diffTotal === 0;
  const flagged = ids.filter(id => flag.has(id)).length;

  return <section className="wb-card wb-recon" aria-label="대사 근거">
    <h2 className="wb-card-title"><Scale size={16} /> 내부 거래 · 외부 정산 대조</h2>
    <div className="wb-totals">
      <div><span>내부 합계</span><strong>{won.format(trial.internal_total_krw)}</strong><small>원 · {s.internal_count}건</small></div>
      <div><span>외부 합계</span><strong>{won.format(trial.external_total_krw)}</strong><small>원 · {s.external_count}건</small></div>
      <div className={totalsMatch ? '' : 'warn'}><span>합계 차이</span><strong>{diffTotal > 0 ? '+' : ''}{won.format(diffTotal)}</strong><small>원</small></div>
      <div className={flagged ? 'warn' : 'ok'}><span>거래별 대조</span><strong>{s.matched_count}/{ids.length}</strong><small>일치 · 이상 {flagged}건</small></div>
    </div>
    {totalsMatch && flagged > 0 && <p className="wb-callout warn"><TriangleAlert size={14} />
      합계는 일치하지만 거래 {flagged}건이 어긋납니다. 합계만으로는 이상을 찾을 수 없습니다.</p>}
    {!s.ingestion_complete && <p className="wb-callout info"><CircleDashed size={14} />
      외부 정산 자료가 모두 도착하지 않았습니다. 빈 칸은 미수신 상태입니다.</p>}
    <div className="wb-table-scroll" tabIndex={0} aria-label="거래별 대조 표 (가로 스크롤)">
      <table className="wb-table">
        <thead><tr><th scope="col">거래 ID</th><th scope="col" className="num">내부 금액</th><th scope="col" className="num">외부 금액</th>
          <th scope="col" className="num">차이</th><th scope="col">외부 ID</th><th scope="col">대조</th></tr></thead>
        <tbody>{ids.map(id => {
          const left = trial.internal.find(r => r.transaction_id === id);
          const right = trial.external.filter(r => r.transaction_id === id);
          const rightSum = right.reduce((sum, r) => sum + r.amount_krw, 0);
          const diff = left && right.length ? rightSum - left.amount_krw : null;
          const issue = flag.get(id);
          return <tr key={id} className={issue ? 'is-flag' : ''}>
            <th scope="row"><code>{id}</code></th>
            <td className="num">{left ? won.format(left.amount_krw) : <span className="wb-empty-cell">—</span>}</td>
            <td className="num">{right.length ? right.map(r => won.format(r.amount_krw)).join(' · ') : <span className="wb-empty-cell">—</span>}</td>
            <td className={`num ${diff ? 'warn-text' : ''}`}>{diff === null ? <span className="wb-empty-cell">—</span> : `${diff > 0 ? '+' : ''}${won.format(diff)}`}</td>
            <td>{right.length ? right.map(r => r.external_id).join(' · ') : <span className="wb-empty-cell">—</span>}</td>
            <td><span className={`wb-tag ${issue ? 'warn' : 'ok'}`}>{issue ?? '일치'}</span></td>
          </tr>;
        })}</tbody>
        <tfoot><tr><th scope="row">합계</th><td className="num">{won.format(trial.internal_total_krw)}</td>
          <td className="num">{won.format(trial.external_total_krw)}</td>
          <td className={`num ${diffTotal ? 'warn-text' : ''}`}>{diffTotal > 0 ? '+' : ''}{won.format(diffTotal)}</td><td colSpan={2} className="wb-muted">단위 원(KRW) · 빈 값 —</td></tr></tfoot>
      </table>
    </div>
    {trial.decision?.draft_id && flagged > 0 && <p className="wb-link-note">
      <ArrowRight size={13} /> 표시된 {flagged}건은 로컬 이관 초안 <Ref value={trial.decision.draft_id} label="이관 초안" />에 연결됩니다.
    </p>}
  </section>;
}

function DecisionPanel({ trial }: { trial: LiveTrial }) {
  const d = trial.decision;
  const ev = trial.evaluation;
  const op = trial.operations.at(-1);
  const sameRef = d?.result_ref && op && d.result_ref === op.result_ref;
  return <section className="wb-card wb-decision" aria-label="판정과 독립 검증">
    <h2 className="wb-card-title"><FileText size={16} /> 판정 · 결과 · 독립 검증</h2>
    <div className="wb-decision-head">
      {d ? <>
        <span className={`wb-tag ${d.outcome === 'REPROCESSED' ? 'ok' : 'info'}`}>{outcomeAction[d.outcome] ?? d.outcome}</span>
        <h3>{outcomeLabel[d.outcome] ?? d.outcome}</h3>
        <p>{outcomeMeaning[d.outcome] ?? ''}</p>
        <p className="wb-muted">{reasonLabel[d.reason] ?? d.reason} <code>{d.reason}</code></p>
      </> : <>
        <h3 className="wb-muted-title">최종 결정 없음</h3>
        <p>{stageLabel[trial.stage] ?? trial.stage} 단계입니다. 결정이 기록되면 독립 SQL 검증 결과가 이곳에 표시됩니다.</p>
      </>}
      {trial.model_advice && <p className="wb-model">로컬 모델 제안 <code>{trial.model_advice}</code> · 코드 판정과 별도 기록</p>}
    </div>
    <dl className="wb-refs">
      {op && <div><dt>작업 참조</dt><dd><Ref value={op.operation_id} label="작업 참조" /></dd></div>}
      {(d?.result_ref || op?.result_ref) && <div><dt>결과 참조</dt><dd>
        <Ref value={(d?.result_ref || op?.result_ref)!} label="결과 참조" />
        {sameRef && <span className="wb-tag ok"><Check size={11} strokeWidth={3} /> DB 작업과 동일</span>}
      </dd></div>}
      {d?.draft_id && <div><dt>로컬 이관 초안</dt><dd><Ref value={d.draft_id} label="이관 초안" /></dd></div>}
      {d && <div><dt>결정 ID</dt><dd><Ref value={d.decision_id} label="결정 ID" /></dd></div>}
      {d?.evidence_ref && <div><dt>근거 다이제스트</dt><dd><Ref value={d.evidence_ref} label="근거 다이제스트" /></dd></div>}
    </dl>
    <div className="wb-checks">
      <div className="wb-checks-head">
        <span>독립 검증</span>
        <span className={`wb-tag ${!ev ? 'neutral' : ev.passed ? 'ok' : 'danger'}`}>{!ev ? '미실행' : ev.passed ? '통과' : '실패'}</span>
      </div>
      {ev ? <ul>{Object.entries(ev.checks).map(([name, ok]) => <li key={name}>
        {ok ? <CircleCheck size={15} className="ok-text" /> : <CircleAlert size={15} className="danger-text" />}
        <span>{checkLabels[name] || name}</span><code>{name}</code>
      </li>)}</ul> : <p className="wb-muted"><CircleMinus size={14} /> 검증 결과 없음 (<code>evaluation = null</code>)</p>}
    </div>
  </section>;
}

function Ledger({ trial }: { trial: LiveTrial }) {
  const groups = sessions(trial.audit);
  const max = Math.max(trial.budget.max_attempts, trial.budget.used, 1);
  return <section className="wb-card wb-ledger" id="audit" aria-label="세션별 도구 호출 기록">
    <h2 className="wb-card-title"><Terminal size={16} /> 세션별 도구 호출 원장</h2>
    <div className="wb-budget">
      <div className="wb-budget-text">
        <span>호출 예산</span><strong>{trial.budget.used} / {trial.budget.max_attempts}</strong>
        <small>trial 단위로 누적 · 세션이 바뀌어도 초기화되지 않습니다</small>
      </div>
      <div className="wb-budget-bar" role="img" aria-label={`누적 시도 ${trial.budget.used}회, 한도 ${trial.budget.max_attempts}회`}>
        {groups.map(g => <span key={g.id} className={`seg ${g.role}`} style={{ width: `${(g.calls.length / max) * 100}%` }}
          title={`세션 ${g.index} · ${roleLabel[g.role]} · ${g.calls.length}회`}>{g.calls.length}</span>)}
      </div>
    </div>
    {!groups.length && <p className="wb-muted wb-pad">이 실험에는 저장된 호출이 없습니다.</p>}
    <div className="wb-sessions">
      {groups.map(g => <article key={g.id} className={`wb-session ${g.role}`}>
        <header>
          <span className="wb-session-no">{g.index}</span>
          <div>
            <strong>{roleLabel[g.role]}</strong>
            <small><code>{g.run_id}</code> · <code title={g.id}>{short(g.id, 12, 4)}</code></small>
          </div>
          <span className="wb-session-counts">읽기 {g.reads} · 쓰기 {g.writes}{g.reserved ? ` · 응답 없음 ${g.reserved}` : ''}</span>
        </header>
        <div className="wb-table-scroll" tabIndex={0} aria-label={`세션 ${g.index} 호출 표`}>
          <table className="wb-table compact">
            <thead><tr><th scope="col">도구</th><th scope="col">구분</th><th scope="col">상태</th><th scope="col" className="num">소요</th></tr></thead>
            <tbody>{g.calls.map(call => {
              const write = WRITE_TOOLS.has(call.tool_name);
              const reserved = call.status === 'RESERVED';
              return <tr key={call.call_id} className={reserved ? 'is-flag' : ''}>
                <th scope="row"><code>{call.tool_name}</code></th>
                <td><span className={`wb-kind ${write ? 'write' : 'read'}`}>{write ? '쓰기' : '읽기'}</span></td>
                <td><span className={`wb-tag ${call.status === 'success' ? 'ok' : reserved ? 'warn' : 'danger'}`}>
                  {reserved ? '응답 기록 없음' : call.status === 'success' ? '성공' : call.status}</span>
                  {call.code && <code className="wb-err">{call.code}</code>}</td>
                <td className="num">{call.elapsed_ms === null ? <span className="wb-empty-cell">—</span> : `${call.elapsed_ms} ms`}</td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      </article>)}
    </div>
  </section>;
}

function ScenarioScript({ id }: { id: string }) {
  const scripts: Record<string, string[]> = {
    after_commit: ['사례 조사', '운영자 승인', '커밋 직후 종료', '새 세션에서 회수'],
    before_commit: ['사례 조사', '운영자 승인', '커밋 직전 종료', '확인 후 같은 의도로 실행'],
    delayed_arrival: ['v1 조사', '자료 대기 결정', '지연 자료 도착', 'v2 재조사'],
    amount_swap: ['사례 조사', '거래별 불일치 발견', '로컬 이관 초안'],
    clean: ['사례 조사', '운영자 승인', '재처리 실행', '독립 검증'],
  };
  const list = scripts[id];
  if (!list) return null;
  return <ol className="wb-script">{list.map((item, i) => <li key={item}><span>{i + 1}</span>{item}</li>)}</ol>;
}
