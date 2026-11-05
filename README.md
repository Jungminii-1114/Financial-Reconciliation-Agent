# Financial-Reconciliation-Agent

**Recover Interrupted Work. Verify Real Outcomes. Keep Tool Use Within Scope.**

〖English | [한국어](readme/README.ko.md)〗

〖[Overview](#overview) | [Why This Project?](#why-this-project) | [Goals and Technology](#goals-and-technology) | [Core Design](#core-design) | [Data and QA](#data-and-qa-scenarios) | [Evaluation](#evaluation-method) | [Decision Principles](#decision-principles) | [Key Metrics](#key-metrics)〗

> A harness engineering project for financial reconciliation agents that recover from interrupted execution, verify business outcomes, and handle malicious external instructions within explicit permission boundaries.

**Project status:** In development. The v1 synthetic generator, independent SQL validator, temporal snapshots, normal/attack pairs, HTML preview, and operator-only SQLite export are implemented. The agent, business service, recovery runtime, security controls, and end-to-end evaluation are planned. No agent performance results have been established.

**Available now:** [Synthetic data v1: one-command setup and usage guide →](docs/data-generation/README.en.md) · [Six-case teaching starter](examples/synthetic_data_starter/README.en.md)

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
| Business service | Python, Pydantic, FastAPI, SQLite | Planned |
| Persistent execution | LangGraph with persistent checkpoints | Planned |
| Inference | Colab GPU; Qwen3-4B-Instruct-2507 as an initial candidate | Pilot required |
| Outcome evaluation | Python, SQL, pytest, structured execution logs | Planned |
| Optional extensions | MCP tool exposure, additional models, Jev-assisted review | After core implementation |

The initial workflow uses one agent and approximately six business tools: case lookup, record lookup, reprocessing precheck, reprocessing execution, operation-status lookup, and escalation-summary preparation. Model fine-tuning is outside the initial eight-week scope.

## Core Design

The following diagram describes the intended system.

```mermaid
flowchart LR
    U["Korean request"] --> H["Agent harness: state, evidence, budget, recovery"]
    H <--> M["Model"]
    H <--> T["Business tools: arguments, access, approval, version"]
    T <--> D[("Business database")]
    C["Scenario controller"] -->|"Arrivals and time"| D
    C -->|"Fault and attack injection"| H
    H -->|"Execution trace"| E["Independent evaluator"]
    D -->|"Actual outcome"| E
    G["Evaluator-only specification"] --> E
```

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
