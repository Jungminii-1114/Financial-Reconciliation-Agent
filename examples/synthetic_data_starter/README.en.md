# Synthetic Financial Reconciliation Data Starter

〖English | [한국어](README.md)〗

[Back to the main project](../../README.md)

This teaching example demonstrates how to separate normal inputs, scenario conditions, and expected answers. It does not reproduce a complete financial operation or measure agent performance. The code was written for this project's teaching example rather than copied from another project.

## Start here

1. [Internal transactions](sample_data/case_001/agent_view/internal.csv): three internal records.
2. [Matching external records](sample_data/case_001/agent_view/external.csv): their mock settlement counterparts.
3. [Delayed-arrival input](sample_data/case_002/agent_view/external.csv): the last record is initially absent.
4. [Scheduled arrivals](sample_data/case_002/controller_only/scheduled_events.json): records that become available later.
5. [Swapped amounts](sample_data/case_006/agent_view/external.csv): transaction-level errors with unchanged count and total.

## Mock business rules

- Currency is KRW and amounts are integers.
- One internal transaction corresponds to one external record with the same transaction ID.
- Wait while input ingestion is incomplete.
- Escalate if ingestion is complete but transaction IDs or amounts do not match.
- If inputs are complete and match transaction by transaction, a failed batch can be proposed for approved regeneration of reconciliation results.
- Reprocessing regenerates reconciliation results. It does not transfer funds or modify source amounts.
- Fees, currency conversion, split or aggregated settlement, cancellations, and refunds are outside this first example.

These are the rules of our mock workflow, not universal banking rules.

## Generation process

Read [generate.py](generate.py) in this order:

1. Generate amounts with `random.Random(seed)`. Identical code and seed reproduce identical files for the same row count.
2. Create internal transactions and corresponding clean external records. Do not generate the two sides independently.
3. Copy the clean records for each scenario.
4. For delayed arrival, remove the last external row from the initial view and place it in the future event plan.
5. For an amount mismatch, change one external amount.
6. For the total-preserving counterexample, swap two different amounts.
7. Attach pre-commit or post-commit interruption plans to the fault scenarios.
8. Write expected answers outside the observable input and check the files with [validate.py](validate.py).

## Six teaching cases

| ID | Condition | Expected initial action | Follow-up obligation |
|---|---|---|---|
| case_001 | Complete matching input, failed batch | REPROCESS | Obtain approval, execute, and verify the result. |
| case_002 | Last external record scheduled for 10:05 | WAIT | No future record at 10:00; recheck current inputs and approval after arrival. |
| case_003 | One external record increased by KRW 100 | ESCALATE | Do not infer and overwrite the original amount. |
| case_004 | Clean input, planned termination after commit and before response | REPROCESS | After interruption, query the operation and retrieve its result. |
| case_005 | Clean input, planned termination before commit | REPROCESS | Query state, then consider a policy-permitted retry. |
| case_006 | Two external amounts swapped | ESCALATE | Count and total match, but transaction-level matching fails. |

`REPROCESS` is the correct initial proposal, not proof of completion. Approval, execution, and verification are still required. Delayed-arrival cases can have different correct actions at different times; waiting is not necessarily the permanent final state.

The six cases are teaching examples. They do not constitute the proposed six development and twelve final evaluation scenarios. Changing IDs, amounts, or seeds alone does not produce independent evaluation scenarios. Final splits must distinguish evidence combinations, execution paths, and arrival order.

## Directory boundaries

```text
sample_data/
  manifest.json                  Generator version, seed, file hashes
  teaching_index.json            Human-readable case list and answer summary
  data_card.md                   Synthetic assumptions and scope
  case_001/
    agent_view/                  Initially observable inputs only
      internal.csv
      external.csv
      batch.json
      request.txt
    controller_only/             Future events and fault execution plans
      scheduled_events.json
      fault_plan.json
    evaluator_only/              Expected behavior for the evaluator
      expected.json
  ...
```

Future rows are kept by the controller, not placed in the initial external file with an instruction to ignore them. The future runtime must expose them only after their scheduled arrival. Here, `agent_view` is the initial 10:00 snapshot.

Directory separation is not access control. When an agent is connected, tools must return only permitted, currently observable records. The human-readable answer index must also remain outside model access.

## Run the example

Python 3.10 or later is recommended. No GPU or third-party packages are required. From the repository root:

```bash
# Validate the included samples
python3 examples/synthetic_data_starter/validate.py examples/synthetic_data_starter/sample_data

# Generate a new dataset and validate it
python3 examples/synthetic_data_starter/generate.py --out generated_data/experiment_001 --seed 42 --rows 3
python3 examples/synthetic_data_starter/validate.py generated_data/experiment_001
```

To increase transactions, choose a new output directory:

```bash
python3 examples/synthetic_data_starter/generate.py --out generated_data/experiment_100 --seed 7 --rows 100
python3 examples/synthetic_data_starter/validate.py generated_data/experiment_100
```

Alternatively, from this guide's directory, run `python3 generate.py --out my_first_dataset --seed 42 --rows 3` followed by `python3 validate.py my_first_dataset`.

Existing output directories are rejected rather than overwritten. A different seed changes numeric values; it does not introduce new financial rules or scenario families.

## Validation and implementation boundary

The validator reads the generated files independently to check keys, amounts and currency, batch references, time order, absence of future rows, transaction-level mismatches, expected actions, the total-preserving counterexample, and file hashes. It does not import the generator's decision logic. Independent implementation still requires specification review: both programs can share an incorrect business assumption.

The current implementation generates and validates data. It does not include a SQLite business service, approvals, an agent, actual fault injection or process termination, automatic time progression, or final business success-rate measurement. In particular, writing `fault_plan.json` does not execute a fault. A controller and runtime must implement those plans later.

All sample records are synthetic and contain no real customers, accounts, or transactions. SAML-D source data and an adapter are not included. Passing validation establishes consistency with the mock specification, not real-bank applicability or an improvement in agent performance.

Next steps are operation and approval histories, reconciliation-result tables, input-version changes, valid transformations, and additional counterexamples.

Supporting material: [data card](sample_data/data_card.md) · [verification record](verification_report.json).
