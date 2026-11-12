# Financial-Reconciliation-Agent

**Recover Interrupted Work. Verify Real Outcomes. Keep Tool Use Within Scope.**

〖English | [한국어](readme/README.ko.md)〗

〖[Overview](#overview) | [Why This Project?](#why-this-project) | [Goals and Technology](#goals-and-technology) | [Core Design](#core-design) | [Data and QA](#data-and-qa-scenarios) | [Evaluation](#evaluation-method) | [Decision Principles](#decision-principles) | [Key Metrics](#key-metrics)〗

> A harness engineering project for financial reconciliation agents that recover from interrupted execution, verify business outcomes, and handle malicious external instructions within explicit permission boundaries.

**Project status:** In development. Synthetic data generation and inspection, a versioned business-DB import adapter, explicit trial resume, scoped read-only storage, and atomic arrival/correction events are implemented. TOOL-01 get_case and a local dispatcher with durable call budgets/audit are implemented. TOOL-02–06, agent, approval/reprocessing services, process-recovery harness, injection defenses, and outcome evaluation remain planned. No agent performance results have been established.

**Available now:** [Synthetic data v1: one-command setup and usage guide →](docs/data-generation/README.en.md) · [Six-case teaching starter](examples/synthetic_data_starter/README.en.md) · [Business-DB adapter: prepare, inspect, resume (한국어)](docs/data-adapter/README.md)

**Engineering docs (한국어):** [Overall plan](docs/README.md) · [Big Nodes](docs/big-nodes/README.md) · [Small Nodes](docs/small-nodes/README.md) · [Tool contracts](docs/tools/README.md)

## Overview

Financial reconciliation checks whether records held by different systems agree. This project models a financial IT operations task: compare internal transactions with external settlement records, investigate exceptions, and propose **reprocessing, waiting, or escalation**.

The agent interprets Korean requests and investigates evidence. Its **harness** manages context, persistent state, tool use, permissions, approval, recovery, execution limits, and completion checks. A separate evaluation harness runs controlled scenarios and checks the actual outcome.

This is a one-person, eight-week portfolio project using synthetic data and a local business environment. Reprocessing regenerates reconciliation results. Real transfers, real customer data, source transaction amount changes, and live bank integrations are outside the initial scope.

## Why This Project?

### A committed operation can outlive the agent's response

A reprocessing result may be committed before its response is recorded by the agent. If execution stops there, a resumed agent may repeat a completed operation or report completion without verification. A timeout, lost response, and terminated process are distinct experimental conditions.

### Saved progress can become stale

External records may arrive or be corrected while the agent is stopped. A previous query result, including an empty result, may no longer justify a decision. Approval and completion evidence must be checked against the relevant input version.

### External content can redirect tool use

A transaction memo or tool response may instruct the agent to bypass approval, inspect unrelated records, or trust fabricated success evidence. The design separates external content from authority and tests whether legitimate work can continue safely.

### A successful-looking answer can hide an incorrect outcome

A completion message does not establish that the intended batch was processed correctly. Matching totals can hide transaction-level errors. Evaluation must inspect affected records, approvals, operation history, and the actual final state.

## Goals and Technology

| Goal | Planned behavior |
|---|---|
| Reliable recovery | Query unresolved operation IDs, retrieve committed results, retry only when allowed, and escalate unresolved uncertainty. |
| Valid evidence and approval | Connect decisions and approvals to input versions; recheck affected evidence when inputs change. |
| Safe tool use | Enforce typed arguments, batch-level access, approval, version checks, and idempotency in the business service. |
| Measurable contribution | Compare rule-based and agent baselines under the same permissions, tools, and budgets. |

| Component | Technology or approach | Status |
|---|---|---|
| Synthetic data | Python standard library, fixed seeds, temporal events, paired security fixtures | Implemented |
| Data validation and inspection | Independent SQLite matching, HTML preview, operator-only DB export | Implemented |
| Trial storage and input events | Python sqlite3, versioned import, as-of history, role-separated stores | Implemented; [guide (한국어)](docs/data-adapter/README.md) |
| Business service | TOOL-01 with Python dispatcher, durable call ledger; remaining tools and approval/reprocessing | TOOL-01 implemented; [guide (한국어)](docs/tools/get-case-usage.md) |
| Persistent execution | LangGraph with persistent checkpoints | Planned |
| Inference | Colab GPU; Qwen3-4B-Instruct-2507 as an initial candidate | Pilot required |
| Outcome evaluation | Python, SQL, pytest, structured execution logs | Planned |
| Optional extensions | MCP tool exposure, additional models, Jev-assisted review | After core implementation |

The initial workflow uses one agent and approximately six business tools: case lookup, record lookup, reprocessing precheck, reprocessing execution, operation-status lookup, and escalation-summary preparation. Model fine-tuning is outside the initial eight-week scope.

## Core Design

### Figure 1. Normal reprocessing in execution order

**Top → bottom follows execution time; left → right within each row follows the Small Nodes.** After the business DB is prepared, this is one successful path: a new case, evidence supporting reprocessing, valid external approval, and a received result. `N-xx` identifies a responsibility boundary, not a call count. Click a node for its contract. Purple marks the default LLM call sites; orange marks human input.

**Implementation boundary:** Data generation/validation, business-DB import/resume, and input events are implemented. **All N-01–N-19 business/evaluation nodes below are target designs.** Current DB resume does not implement N-02/N-12 agent recovery.

```mermaid
flowchart TB
    U["User · investigate a new case"]:::input
    subgraph S1["B-01 · Intake and restore"]
        direction LR
        N01["N-01<br/>Admit case and check scope<br/>TOOL-01"]
        N02["N-02<br/>Restore state and used budget"]
        N03["N-03<br/>Build current case context<br/>TOOL-01"]
        N01 --> N02 --> N03
    end
    subgraph S2["B-02 · Investigate evidence"]
        direction LR
        N04["N-04<br/>Plan investigation · LLM"]:::model
        N05["N-05<br/>Collect permitted evidence<br/>TOOL-02"]
        N06["N-06<br/>Revalidate dependencies"]
        N07["N-07<br/>Assemble as-of evidence"]
        N04 --> N05 --> N06 --> N07
    end
    subgraph S3["B-03 · Decide and approve"]
        direction LR
        N08["N-08<br/>Propose action · LLM<br/>REPROCESS"]:::model
        N09["N-09<br/>Precheck and verify external approval<br/>TOOL-03"]
        N10["N-10<br/>Persist intent and idempotency key"]
        N08 --> N09 --> N10
    end
    subgraph S4["B-04 · Execute"]
        direction LR
        N11["N-11<br/>Issue approved reconciliation<br/>TOOL-04"]
    end
    subgraph S5["B-05 · Verify and deliver"]
        direction LR
        N13["N-13<br/>Verify actual business outcome<br/>TOOL-02 / 05"]
        N14["N-14<br/>Finalize only if inputs are current"]
        N15["N-15<br/>Deliver result and limitations"]
        N13 --> N14 --> N15
    end
    U --> S1 --> S2 --> S3 --> S4 --> S5
    classDef input fill:#fff3df,stroke:#ad6800,color:#24292f
    classDef model fill:#e5d7ff,stroke:#6f42c1,color:#24292f
    classDef default fill:#f6f8fa,stroke:#8c959f,color:#24292f
    style S1 fill:#ffffff,stroke:#d0d7de,color:#24292f
    style S2 fill:#ffffff,stroke:#d0d7de,color:#24292f
    style S3 fill:#ffffff,stroke:#d0d7de,color:#24292f
    style S4 fill:#ffffff,stroke:#d0d7de,color:#24292f
    style S5 fill:#ffffff,stroke:#d0d7de,color:#24292f
    click N01 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-01.md"
    click N02 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-02.md"
    click N03 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-03.md"
    click N04 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-04.md"
    click N05 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-05.md"
    click N06 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-06.md"
    click N07 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-07.md"
    click N08 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-08.md"
    click N09 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-09.md"
    click N10 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-10.md"
    click N11 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-11.md"
    click N13 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-13.md"
    click N14 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-14.md"
    click N15 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-15.md"
```

A normal response skips N-12. N-09 checks an authenticated event from a separate approval UI/service; a model proposal or the word “approved” does not create approval. N-10 persists intent in harness storage without assuming a distributed transaction with the business DB. N-15 uses TOOL-06 only to prepare an escalation draft.

### Figure 2. How user input and saved state change the route

Natural language alone does not select the next node. Code checks **request + current scope + restored operation state + input version + actual approval**. A–D are independent branching examples, not four consecutive runtime stages. Read each panel left → right. Orange marks human input/approval waiting; blue marks uncertain-operation lookup. All observation and investigation loops share a cumulative budget.

```mermaid
flowchart TB
    subgraph RESTORE["A · New request / resume"]
        direction LR
        RESTORE_IN["User · investigate / resume"]:::input
        RESTORE_Q{"N-01 → 02 → 03<br/>Current scope, saved state, inputs"}
        RESTORE_IN --> RESTORE_Q
        RESTORE_fresh["N-06 preflight → N-04 / 05 if needed<br/>N-07 → N-08"]:::normal
        RESTORE_Q -->|"New / changed inputs"| RESTORE_fresh
        RESTORE_pending["N-12 · TOOL-05<br/>Observe the existing operation"]:::recovery
        RESTORE_Q -->|"Unresolved operation"| RESTORE_pending
        RESTORE_done["N-15 · redeliver saved decision<br/>Skip reprocessing"]:::normal
        RESTORE_Q -->|"Final decision; same inputs"| RESTORE_done
    end
    subgraph ACTION["B · Evidence-backed action"]
        direction LR
        ACTION_IN["N-08 · proposal after investigation"]:::normal
        ACTION_Q{"Validate proposal against business rules"}
        ACTION_IN --> ACTION_Q
        ACTION_observe["N-06 preflight → required queries<br/>Within cumulative budget"]:::normal
        ACTION_Q -->|"OBSERVE"| ACTION_observe
        ACTION_wait["N-13 → N-14 → N-15<br/>Verify and report; no reprocessing"]:::normal
        ACTION_Q -->|"WAIT / ESCALATE"| ACTION_wait
        ACTION_reprocess["N-09 · precheck and external approval<br/>No execution without approval"]:::normal
        ACTION_Q -->|"REPROCESS"| ACTION_reprocess
    end
    subgraph APPROVAL["C · Approver input"]
        direction LR
        APPROVAL_IN["Separate approval UI<br/>Authenticated approval / denial"]:::input
        APPROVAL_Q{"N-09 · check current plan, inputs, approval"}
        APPROVAL_IN --> APPROVAL_Q
        APPROVAL_approved["N-10 → N-11 · TOOL-04<br/>Persist intent, then issue operation"]:::normal
        APPROVAL_Q -->|"Valid approval; same inputs"| APPROVAL_approved
        APPROVAL_paused["PAUSED_FOR_APPROVAL<br/>Wait without a business write"]:::input
        APPROVAL_Q -->|"No approval yet"| APPROVAL_paused
        APPROVAL_denied["Block write<br/>Report verified escalation / wait reason"]:::normal
        APPROVAL_Q -->|"Denied / expired"| APPROVAL_denied
        APPROVAL_stale["Reinvestigate from N-06 preflight<br/>New plan and approval required"]:::normal
        APPROVAL_Q -->|"Changed inputs"| APPROVAL_stale
    end
    subgraph RECOVERY["D · After interruption / response loss"]
        direction LR
        RECOVERY_IN["N-12 · TOOL-05<br/>Query existing ID; do not reissue yet"]:::recovery
        RECOVERY_Q{"Check past effect and input changes separately"}
        RECOVERY_IN --> RECOVERY_Q
        RECOVERY_applied["N-13 → N-14 → N-15<br/>Verify and retrieve existing result"]:::normal
        RECOVERY_Q -->|"Applied; unchanged inputs"| RECOVERY_applied
        RECOVERY_retry["Reinvestigate from N-06; check approval<br/>Retry only with same intent and key"]:::normal
        RECOVERY_Q -->|"Not-applied proven; same inputs; retry allowed"| RECOVERY_retry
        RECOVERY_unknown["Bounded additional observation<br/>Then verify escalation grounds"]:::normal
        RECOVERY_Q -->|"Unknown"| RECOVERY_unknown
        RECOVERY_changed["N-06 preflight → new investigation<br/>Independent of past effect"]:::normal
        RECOVERY_Q -->|"Changed inputs; retain past effect"| RECOVERY_changed
    end
    RESTORE ~~~ ACTION ~~~ APPROVAL ~~~ RECOVERY
    classDef default fill:#f6f8fa,stroke:#8c959f,color:#24292f
    classDef normal fill:#f6f8fa,stroke:#8c959f,color:#24292f
    classDef input fill:#fff3df,stroke:#ad6800,color:#24292f
    classDef recovery fill:#e6f4ff,stroke:#0969da,color:#24292f
    style RESTORE fill:#ffffff,stroke:#d0d7de,color:#24292f
    click RESTORE_fresh "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/big-nodes/B-02.md"
    click RESTORE_pending "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-12.md"
    click RESTORE_done "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-15.md"
    style ACTION fill:#ffffff,stroke:#d0d7de,color:#24292f
    click ACTION_observe "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/big-nodes/B-02.md"
    click ACTION_wait "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/big-nodes/B-05.md"
    click ACTION_reprocess "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-09.md"
    style APPROVAL fill:#ffffff,stroke:#d0d7de,color:#24292f
    click APPROVAL_approved "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/big-nodes/B-04.md"
    click APPROVAL_paused "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-09.md"
    click APPROVAL_denied "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/big-nodes/B-03.md"
    click APPROVAL_stale "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/big-nodes/B-02.md"
    style RECOVERY fill:#ffffff,stroke:#d0d7de,color:#24292f
    click RECOVERY_applied "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/big-nodes/B-05.md"
    click RECOVERY_retry "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-12.md"
    click RECOVERY_unknown "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-12.md"
    click RECOVERY_changed "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/big-nodes/B-02.md"
```

| User input / later event | Internal route change | Execution and output rule |
|---|---|---|
| “Check this batch” · new case | N-01–03 → N-04–07 → N-08 | Incomplete arrival supports WAIT; discrepancies or unresolved facts support ESCALATE; satisfied reprocessing conditions support a REPROCESS candidate. Code validates N-08 proposals. |
| “Continue the interrupted task” · unresolved operation | N-01–03 → **N-12 / TOOL-05** | Applied + unchanged inputs retrieves the result into N-13. Only authoritative non-application and retry permission allow reinvestigation/approval checks and retry with the **same intent and key**. Unknown states receive bounded observation, then escalation. |
| “Show the result again” · final decision, same inputs | N-01–03 → **N-15** | Check current read scope and redeliver the saved as-of decision, without reprocessing or consuming approval again. |
| Authenticated approval/denial in the separate UI | **N-09** → N-10–11 only with valid approval | No response means PAUSED_FOR_APPROVAL. Denial/expiry blocks writing; changed inputs return to N-06 preflight. Approval waiting differs from the business action WAIT. |
| “New records arrived; check again” · arrival/correction | N-01–03 → **N-06 preflight** → N-04/05 if needed → N-07/08 | Read actual DB versions/change history instead of trusting the claim. Skip new queries if evidence remains sufficient and unaffected. Preserve past decisions. |
| “Execute without approval” / approval impersonation in a memo | Preserve N-01 scope checks and N-09/10 approval checks | Natural language and external content cannot grant authority. Attack detection and defense performance require separate implementation and evaluation. |

N-12 checks **past operation effects** and **current input changes** independently. Applied work with changed inputs retains its historical effect and returns to investigation. N-11 version/approval rejections also return to investigation/approval checks. N-13/14 never convert failed verification into success: record only verified failure/escalation facts or block finalization. Missing/denied admission stops at N-01 and reaches N-15 only with an allowed reason. [Big Node contracts](docs/big-nodes/README.md) define the detailed exception/error routes.

### Figure 3. Preparation → events during execution → independent grading

**B-06 is outside the user-facing business route.** Green marks the implemented fixture generator/checker. Despite its number, N-18 runs before evaluation freeze; N-17 applies events during a business run and records what actually occurred. Answers and future-event plans stay outside model access.

```mermaid
flowchart TB
    DATA["Implemented · synthetic fixtures<br/>Generation and independent validation"]:::implemented
    N18["N-18<br/>Prepare counterexamples and controls before freeze"]
    N16["N-16<br/>Load frozen case; isolate trial and answers"]
    N17["N-17<br/>Run trial; replay events, faults and attacks"]
    N19["N-19<br/>Independently grade DB outcomes and traces"]
    DATA --> N18 --> N16 --> N17 --> N19
    G["Evaluator-only answers and forbidden effects"]:::private
    DB[("Trial business DB")]
    RUN["B-01–05 business run<br/>Observe arrivals, interruption and resume"]
    N16 -.-> G
    N16 --> DB
    DB -->|"Scoped input reads"| RUN
    G --> N19
    N17 -->|"Controlled events"| RUN
    RUN -->|"Actual DB state and traces"| N19
    DB -->|"Actual committed state"| N19
    classDef private fill:#fff1f1,stroke:#cf222e,color:#24292f
    classDef implemented fill:#dafbe1,stroke:#1a7f37,color:#24292f
    classDef default fill:#f6f8fa,stroke:#8c959f,color:#24292f
    click N18 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-18.md"
    click N16 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-16.md"
    click N17 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-17.md"
    click N19 "https://github.com/Jungminii-1114/Financial-Reconciliation-Agent/blob/master/docs/small-nodes/N-19.md"
```

Only the trial preparation foundation for N-16 and financial-input events/virtual time for N-17 are implemented. Agent interruption/restart, attack injection/defense, the dedicated N-18 counterexample node, and N-19 outcome grading remain planned.

### Pipeline components and detailed nodes

The figures separate the business timeline and conditional routes from the test/evaluation environment. Trial import/resume, business input storage, scoped reads, and temporal input events are implemented; TOOL-01 is implemented; agent nodes, remaining Tool handlers, fault injection, and outcome grading remain design specifications. Detailed engineering documents are currently in Korean.

| Diagram component | Role | Detailed nodes and contracts |
|---|---|---|
| Korean request (`U`) | Accept a case, check the caller's scope, and build the investigation context. | [Case admission · N-01](docs/small-nodes/N-01.md) · [Context · N-03](docs/small-nodes/N-03.md) |
| Agent harness (`H`) | Manage persistent state, evidence, execution budgets, and recovery across sessions. | [State restoration · N-02](docs/small-nodes/N-02.md) · [Recovery · N-12](docs/small-nodes/N-12.md) · [Persistence contract](docs/contracts/persistence-and-recovery.md) |
| Model (`M`) | Propose a bounded investigation plan and an evidence-backed action: reprocess, wait, or escalate. | [Investigation planning · N-04](docs/small-nodes/N-04.md) · [Action proposal · N-08](docs/small-nodes/N-08.md) |
| Business tools (`T`) | Provide six business functions with argument, scope, approval, and input-version checks. | [TOOL-01–06](docs/tools/README.md) · [Dispatch and approval services](docs/tools/internal-services.md) |
| Business database (`D`) | Persist input history and prepare approval/operation/result tables; business execution services are planned. | [Business data contract](docs/contracts/business-data.md) · [Implemented adapter](docs/data-adapter/README.md) |
| Scenario controller (`C`) | Run timed arrivals and corrections, inject faults and attacks, restart the agent, and record what actually occurred. | [Trial setup · N-16](docs/small-nodes/N-16.md) · [Scenario execution · N-17](docs/small-nodes/N-17.md) |
| Independent evaluator (`E`) | Compare execution traces and actual database effects against the expected specification; aggregate outcomes, safety violations, and costs. | [Grading · N-19](docs/small-nodes/N-19.md) · [Metrics](docs/evaluation/grading-and-metrics.md) |
| Evaluator-only specification (`G`) | Define expected actions at each checkpoint, recovery obligations, and forbidden effects; keep them outside agent access. | [QA scenario specification](docs/evaluation/scenario-catalog.md) · [Expected-answer files](docs/data-generation/schemas.md) |

**Business stages:** [B-01 · Intake and restore](docs/big-nodes/B-01.md) → [B-02 · Investigation](docs/big-nodes/B-02.md) → [B-03 · Decision and approval](docs/big-nodes/B-03.md) → [B-04 · Execution and recovery](docs/big-nodes/B-04.md) → [B-05 · Verification and delivery](docs/big-nodes/B-05.md). Waiting and escalation skip B-04; recovery or changed inputs can return execution to an earlier stage. [B-06 · Scenario replay and evaluation](docs/big-nodes/B-06.md) manages the separate evaluation workflow.

### Harness mechanisms

| Mechanism | Design | Question to test |
|---|---|---|
| Selective revalidation, including absence evidence | Track query predicates, result sets, and decision dependencies, including empty results. | Can change detection remain accurate while reducing unnecessary reinspection? |
| Uncertain-state recovery | Use additional observations to distinguish possible states, including uncommitted work and committed work with a lost response. | Can recovery reduce incorrect certainty and unnecessary escalation? |
| Time-aware judgment | Separate event time, arrival time, and decision time; expose only records known at that point. | Are historical decisions and responses to later arrivals evaluated appropriately? |
| Counterexample-based completion checks | Test swapped amounts, incorrect mappings, unrelated success evidence, stale approval, and valid transformations. | Which errors are missed by totals-only or incomplete checks? |
| Source-aware security during recovery | Preserve external-content provenance across sessions and separate it from permissions and approval. | Can malicious instructions be resisted while preserving legitimate task success? |

The contribution is domain-specific integration and controlled evaluation. Checkpoints, idempotency, and version checks are established techniques; the project does not claim to invent them.

## Data and QA Scenarios

### Synthetic data v1

The current generator builds related internal/external records, controlled discrepancies, timed arrivals/corrections, private expected answers, and normal/attack pairs. One command generates and independently validates the bundle, then creates HTML and SQLite views; no SQL knowledge is required.

**[Open the v1 usage guide: commands, configuration, features, scenarios, and validation →](docs/data-generation/README.en.md)**

The default pilot contains 6 core development cases and 2 security pairs, totaling 10 case folders. The benchmark profile contains 18 core cases and 40 security pairs across development and held-out candidate splits, totaling 98 folders. These are evaluation candidates, not a frozen blind benchmark; pair variants are not independent attack families. Fault and attack plans do not execute faults or defenses.

### Available synthetic data starter

The starter creates matched mock records, introduces controlled discrepancies or arrival plans, and stores expected behavior separately from agent-visible inputs. It assumes integer KRW amounts, one-to-one transaction IDs, and no fees or currency conversion.

**[Open the synthetic data guide: setup, commands, structure, six cases, and limitations →](examples/synthetic_data_starter/README.en.md)**

| Resource | Link |
|---|---|
| Korean usage guide | [README](examples/synthetic_data_starter/README.md) |
| Generator / file validator | [generate.py](examples/synthetic_data_starter/generate.py) / [validate.py](examples/synthetic_data_starter/validate.py) |
| Sample data | [sample_data](examples/synthetic_data_starter/sample_data) |
| Data card / verification record | [data_card.md](examples/synthetic_data_starter/sample_data/data_card.md) / [verification_report.json](examples/synthetic_data_starter/verification_report.json) |

The six teaching cases cover complete matching input, delayed arrival, a single amount mismatch, pre-commit and post-commit interruption plans, and swapped amounts with unchanged totals. They are not the finalized development or evaluation set. A fault-plan file does not execute a fault.

### Executable QA cases

Each case will define **initial database, access and approval → observable input → timed events and faults → expected actions and forbidden effects → expected outcome → grading checks**.

- Separate development and held-out evaluation by scenario family, evidence combination, and attack template. Changing IDs, amounts, seeds, or wording alone does not create independent cases.
- Pair normal and attack conditions for the same business scenario. Include benign security quotations to measure false positives.
- Keep expected answers and future events outside agent access. Directory separation alone is not access control.
- SAML-D is an optional source of transaction variation. Its AML labels are not reconciliation-error labels; mock counterpart records and operational ground truth would still need to be constructed.

## Evaluation Method

### Controlled comparisons

| Setting | Composition | Purpose |
|---|---|---|
| R0 | Fixed rules and the common business API | Establish how much of the task can be handled without a model. |
| B0 | Business instructions and a basic agent tool loop | Identify basic agent failure modes. |
| B1 | B0 with persistent state, checkpoints, and bounded retries | Measure what persistence alone resolves. |
| H | B1 with state reconciliation, evidence dependencies, time-aware judgment, and completion verification | Measure the additional harness contribution. |

Compare security modules separately within H. Keep backend access, approval, version checks, transaction protection, and idempotency in every setting. Fix model settings, information access, tools, and execution budgets for each comparison.

### Proposed evaluation budget

- **Core evaluation:** 12 held-out scenarios × baseline/perturbed conditions × B0/B1/H × 3 trials = 216 model runs; R0 uses the same 24 scenario conditions separately.
- **Security evaluation:** 20 held-out normal/attack pairs × 2 conditions × module off/on × 3 trials = 240 runs. Another 20 pairs are proposed for development.
- Final counts, thresholds, and budgets will be frozen after the pilot. Development, component-removal, and additional-model experiments are counted separately. Repeated runs are not independent new cases.

Failures, exhausted budgets, and runs that never reach the planned fault boundary remain in the results. Recovery rates use runs with an actual injected disturbance and a recoverable scenario; appropriate escalation under irreducible uncertainty is reported separately.

The methodology follows the distinction between execution traces and actual outcomes in [Anthropic's agent evaluation guide](https://www.anthropic.com/research/demystifying-evals-for-ai-agents).

## Decision Principles

1. **Grade actual state with code.** Check amounts, timestamps, permissions, approval, affected records, and committed effects using independent code and logs. Do not certify success from a completion message.
2. **Separate attempts, blocks, and effects.** A forbidden call blocked by the service differs from an actual unauthorized read or write. A retry request differs from a duplicate business effect.
3. **Apply case-level safety requirements.** Leakage or an unauthorized write fails that case; explanation quality cannot offset the violation. Waiting or escalation can be the correct outcome.
4. **Keep uncertainty and time explicit.** Use evidence available at the decision time. Recheck current approval and input versions before a new write. Count observation and retry costs across restarts.
5. **Use model review as assistance.** Cross-model review can find issues, but agreement is not ground truth. Use Jev only if a human-reviewed Korean pilot supports its value for narrow semantic checks. It does not replace arithmetic, authorization, or database validation; see [Jev's documented limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
6. **Trace requirements to evidence.** Connect source document and version → requirement ID → QA case → grading code → result log. Use the [financial AI guideline](https://www.fsc.go.kr/no010101/87142), [financial AI evaluation framework](https://www.fsec.or.kr/bbs/detail?bbsNo=12031&menuNo=69), [Personal Information Protection Act Article 29](https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=900079357), and [OWASP guidance](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) for selected technical QA requirements. These tests do not establish complete regulatory compliance or certification.

## Key Metrics

| Area | Metrics and observations |
|---|---|
| Input handling | Exact field agreement, malformed-input handling, future-information exposure |
| Business decisions | Correct final outcome, action-level precision/recall/macro-F1, unnecessary escalation |
| Recovery | Recovery success, premature certainty, unresolved-state handling, unnecessary reprocessing |
| Evidence and completion | Missed changes, unnecessary invalidation, false completion requests, blocked requests, actual incorrect completion records |
| Attack detection | Precision, recall, F1, false positive rate; attack input is the positive class |
| Actual safety | Unauthorized reads, unapproved writes, source-data modification, defined prohibited exposure, duplicate business effects |
| Task preservation | Legitimate task success under normal and attack conditions, benign-input blocking |
| Cost | Tool calls, status queries, retries, tokens, latency, memory, including failed and resumed execution |

For attack detection, TP means an attack detected, FN an attack missed, FP a benign input flagged, and TN a benign input passed. Detection metrics and actual safety outcomes are reported separately.

Synthetic canary strings will test defined exposure paths in tool results, model input, responses, and retained logs. This is a bounded leakage test, not comprehensive detection of every disclosure.

The proposed target is **zero observed actual unauthorized reads, unapproved writes, source-data modifications, and defined prohibited disclosures on the frozen evaluation set**. Other thresholds will be set using development data before final evaluation. Report denominators, scenario-level results, and uncertainty; zero observed violations in a small set does not establish universal safety.
