# Synthetic reconciliation data v1 — operator guide

〖[한국어](README.md) | English〗 · [Project](../../README.md)

**Implemented and locally checked, 2026-11-05.** The dependency-free Python generator creates related transaction records, controlled discrepancies, temporal event plans, security pairs, and private reference answers. A separate SQLite validator checks the fixtures. `build` also produces a static HTML preview and an operator-only SQLite snapshot.

## Quick start

Run from the repository root with Python 3.10+, preferably 3.11. No package installation, GPU, API key, or database server is required.

```bash
python3.11 scripts/dataset.py build --config configs/synthetic-pilot.json --out generated_data/pilot_v1
python3.11 scripts/dataset.py validate generated_data/pilot_v1
python3.11 scripts/dataset.py inspect generated_data/pilot_v1 --case first
```

Open `generated_data/pilot_v1/operator_view/index.html` in a browser. Expand a case to inspect sample rows, its external note, and expected decisions. Full rows are in each case's CSV files. No SQL knowledge is required. `first` selects the clean core case.

The SQLite file is created automatically at `operator_view/initial_snapshot.sqlite`. It stores input snapshots for human inspection, **not** the complete business-service database. It contains no approvals, operations, generated reconciliation results, private labels, or protected canary values.

| Profile | Core cases | Security pairs | Case folders |
|---|---:|---:|---:|
| `pilot` | 6 development | 2 development | 10 |
| `benchmark` | 6 development + 12 held-out candidates | 20 development + 20 held-out candidates | 98 |

Each case initially has 100 internal transactions by default; external and later counts vary with the scenario. Use `--rows 3` for a tiny fixture or `--seed 43` for another realization. The generator supports 3–5000 initial internal rows per case.

```bash
python3.11 scripts/dataset.py build --config configs/synthetic-benchmark.json --out generated_data/benchmark_candidate_v1
python3.11 scripts/dataset.py list generated_data/pilot_v1
```

Use a new output path each time. Existing files and directories are never overwritten. `/generated_data/` is ignored by Git; checked-in code and configuration reproduce it.

## Observe a later state

Find the `delayed_arrival` case ID using `list`. Replace `<CASE_ID>` below with its actual ID, without angle brackets.

```bash
python3.11 scripts/dataset.py inspect generated_data/pilot_v1 --case <CASE_ID> --at 2026-11-05T10:05:00+09:00
python3.11 scripts/dataset.py export-sqlite generated_data/pilot_v1 --case <CASE_ID> --at 2026-11-05T10:05:00+09:00 --out generated_data/later_snapshot.sqlite
```

The initial time is `2026-11-05T10:00:00+09:00`. The missing row arrives at +300 seconds; its event group changes input version once from 1 to 2. Source files remain unchanged. Times must include an offset; states before the initial snapshot are unsupported. Export destinations must be outside the dataset directory.

## Configuration and CLI

Configuration keys: `seed`, `rows_per_batch`, `profile`, `decision_at`, `security_pairs_per_split`. Unknown keys are rejected. CLI overrides: `--seed`, `--rows`, `--profile`, `--decision-at`, `--security-pairs`. An explicitly configured pair count survives a profile override; prefer the provided separate configuration files.

Commands: `build` (generate, validate, HTML, SQLite), `generate` (core files only), `validate`, `list`, `inspect`, `export-sqlite`. Use `python3.11 scripts/dataset.py --help` or a command's `--help` for arguments. Python's standard `sqlite3` module handles the DB automatically.

## Interpretation and boundaries

- Same code, seed, and normalized configuration reproduce core files and their manifest. Source hashes are recorded; HTML/SQLite/report artifacts have separate hashes. Hashes are integrity/provenance aids, not authenticity signatures.
- Internal and external tables derive from one base ledger. Integer KRW, one-to-one matching, POSTED/SETTLED compatibility, no fees or foreign exchange are explicit toy assumptions. Amount distributions are authored, not fitted to bank data. SAML-D is not included.
- Security cases pair identical financial data with benign vs adversarial external notes, including benign security quotations. Forty pairs are variations from six attack families, not forty independent attack types.
- Opaque IDs avoid putting scenario names in public paths. `catalog`, `controller_only`, `evaluator_only`, and `operator_view` must stay outside model access. Directory separation itself is not authorization.
- `benchmark` contains evaluation candidates, not a frozen blind benchmark. Core mechanisms overlap across splits; semantic combinations and security families are grouped explicitly. Numeric/wording variants and repeated trials are not independent scenarios.
- Temporal snapshot reconstruction does not execute faults, approvals, agent actions, or defenses. A reference `REPROCESS` decision is not a completed operation. All runtime evaluation statuses remain `NOT_EXECUTED`.

## Next: import into business storage

The [business-DB adapter (한국어)](../data-adapter/README.md) now supports isolated trial preparation, explicit resume without reimport, scoped as-of reads, and atomic arrival/correction events. Six business Tool handlers, approval/reprocessing services, agent recovery, security controls, and runtime outcome grading are still planned.

## Detailed specifications

The following engineering references are currently in Korean, with stable field/API names in English:

- [Generation design and reproducibility](design.md)
- [Features, role-separated files, and SQLite schema](schemas.md)
- [Core and security scenario catalog](scenarios.md)
- [Independent validation, tests, and limitations](validation.md)

The [older six-case teaching starter](../../examples/synthetic_data_starter/README.en.md) remains available and uses a different schema. Do not mix the two validators.
