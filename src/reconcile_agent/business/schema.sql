-- business schema 1. Canonical ten tables plus explicitly documented support tables.
-- Known times are normalized UTC ISO-8601 with six fractional digits.
CREATE TABLE trial_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE content_objects (
    content_ref TEXT PRIMARY KEY, case_id TEXT NOT NULL, content TEXT NOT NULL,
    content_hash TEXT NOT NULL, origin TEXT NOT NULL, known_at TEXT NOT NULL,
    UNIQUE(content_ref,case_id), FOREIGN KEY(case_id) REFERENCES cases(case_id));
CREATE TABLE cases (
    case_id TEXT PRIMARY KEY, target_batch_id TEXT NOT NULL,
    business_rule_version TEXT NOT NULL, policy_version TEXT NOT NULL,
    request_ref TEXT NOT NULL, UNIQUE(case_id,target_batch_id),
    FOREIGN KEY(request_ref,case_id) REFERENCES content_objects(content_ref,case_id)
        DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE case_policies (
    case_id TEXT NOT NULL, policy_version TEXT NOT NULL, business_rule_version TEXT NOT NULL,
    approval_required INTEGER NOT NULL CHECK(approval_required=1),
    approval_ttl_seconds INTEGER NOT NULL CHECK(typeof(approval_ttl_seconds)='integer' AND approval_ttl_seconds>0),
    reprocess_cutoff_at TEXT, PRIMARY KEY(case_id,policy_version),
    FOREIGN KEY(case_id) REFERENCES cases(case_id));
CREATE TABLE batch_versions (
    case_id TEXT NOT NULL, batch_id TEXT NOT NULL,
    input_version INTEGER NOT NULL CHECK(typeof(input_version)='integer' AND input_version>0),
    input_digest TEXT NOT NULL, ingestion_complete INTEGER NOT NULL CHECK(ingestion_complete IN (0,1)),
    known_at TEXT NOT NULL, PRIMARY KEY(case_id,batch_id,input_version),
    UNIQUE(case_id,batch_id,known_at), FOREIGN KEY(case_id,batch_id) REFERENCES cases(case_id,target_batch_id));
CREATE TABLE batch_runtime (
    case_id TEXT NOT NULL, batch_id TEXT NOT NULL,
    run_status TEXT NOT NULL CHECK(run_status IN ('FAILED','RUNNING','SUCCEEDED')),
    last_operation_id TEXT, PRIMARY KEY(case_id,batch_id),
    FOREIGN KEY(case_id,batch_id) REFERENCES cases(case_id,target_batch_id),
    FOREIGN KEY(last_operation_id) REFERENCES operations(operation_id));
CREATE TABLE transaction_history (
    case_id TEXT NOT NULL, batch_id TEXT NOT NULL, transaction_id TEXT NOT NULL,
    row_revision INTEGER NOT NULL CHECK(typeof(row_revision)='integer' AND row_revision BETWEEN 1 AND 100000),
    amount_krw INTEGER NOT NULL CHECK(typeof(amount_krw)='integer' AND amount_krw BETWEEN 1 AND 1000000000000),
    currency TEXT NOT NULL CHECK(currency='KRW'), status TEXT NOT NULL CHECK(status='POSTED'),
    event_time TEXT NOT NULL, known_at TEXT NOT NULL, superseded_known_at TEXT,
    PRIMARY KEY(case_id,transaction_id,row_revision),
    CHECK(event_time<=known_at), CHECK(superseded_known_at IS NULL OR known_at<superseded_known_at),
    FOREIGN KEY(case_id,batch_id) REFERENCES cases(case_id,target_batch_id));
CREATE UNIQUE INDEX current_internal ON transaction_history(case_id,transaction_id) WHERE superseded_known_at IS NULL;
CREATE INDEX internal_asof ON transaction_history(case_id,batch_id,known_at,superseded_known_at);
CREATE TABLE settlement_history (
    case_id TEXT NOT NULL, batch_id TEXT NOT NULL, external_id TEXT NOT NULL, transaction_id TEXT NOT NULL,
    row_revision INTEGER NOT NULL CHECK(typeof(row_revision)='integer' AND row_revision BETWEEN 1 AND 100000),
    amount_krw INTEGER NOT NULL CHECK(typeof(amount_krw)='integer' AND amount_krw BETWEEN 1 AND 1000000000000),
    currency TEXT NOT NULL CHECK(currency='KRW'), status TEXT NOT NULL CHECK(status IN ('SETTLED','PENDING')),
    event_time TEXT NOT NULL, known_at TEXT NOT NULL, superseded_known_at TEXT,
    PRIMARY KEY(case_id,external_id,row_revision),
    CHECK(event_time<=known_at), CHECK(superseded_known_at IS NULL OR known_at<superseded_known_at),
    FOREIGN KEY(case_id,batch_id) REFERENCES cases(case_id,target_batch_id));
-- Deliberately no transaction_id uniqueness or FK to internal: duplicates/unknown IDs are fixtures.
CREATE UNIQUE INDEX current_external ON settlement_history(case_id,external_id) WHERE superseded_known_at IS NULL;
CREATE INDEX external_asof ON settlement_history(case_id,batch_id,known_at,superseded_known_at);
CREATE TABLE input_mutations (
    mutation_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, batch_id TEXT NOT NULL,
    old_version INTEGER NOT NULL, new_version INTEGER NOT NULL CHECK(new_version=old_version+1),
    table_name TEXT NOT NULL, affected_ids TEXT NOT NULL, changed_fields TEXT NOT NULL, known_at TEXT NOT NULL,
    FOREIGN KEY(case_id,batch_id,old_version) REFERENCES batch_versions(case_id,batch_id,input_version),
    FOREIGN KEY(case_id,batch_id,new_version) REFERENCES batch_versions(case_id,batch_id,input_version));
CREATE TABLE external_notes (
    note_id TEXT NOT NULL, case_id TEXT NOT NULL, record_ref TEXT NOT NULL,
    content_ref TEXT NOT NULL, content_hash TEXT NOT NULL,
    context_revision INTEGER NOT NULL CHECK(context_revision>0), origin TEXT NOT NULL, known_at TEXT NOT NULL,
    PRIMARY KEY(case_id,note_id,context_revision),
    FOREIGN KEY(content_ref,case_id) REFERENCES content_objects(content_ref,case_id));
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, batch_id TEXT NOT NULL, input_version INTEGER NOT NULL,
    input_digest TEXT NOT NULL, action_digest TEXT NOT NULL, policy_version TEXT NOT NULL,
    granted_by TEXT, expires_at TEXT, status TEXT NOT NULL CHECK(status IN ('REQUIRED','APPROVED','DENIED','EXPIRED','STALE','CONSUMED')),
    consumed_by TEXT, FOREIGN KEY(case_id,batch_id,input_version) REFERENCES batch_versions(case_id,batch_id,input_version),
    FOREIGN KEY(case_id,policy_version) REFERENCES case_policies(case_id,policy_version),
    FOREIGN KEY(consumed_by) REFERENCES operations(operation_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE operations (
    operation_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
    case_id TEXT NOT NULL, batch_id TEXT NOT NULL, input_version INTEGER NOT NULL,
    action_kind TEXT NOT NULL CHECK(action_kind='REPROCESS'), action_digest TEXT NOT NULL,
    approval_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('RUNNING','SUCCEEDED','FAILED_RETRYABLE','FAILED_FINAL','REJECTED','UNKNOWN')),
    result_ref TEXT, committed_at TEXT, UNIQUE(case_id,batch_id,input_version,action_kind),
    FOREIGN KEY(case_id,batch_id,input_version) REFERENCES batch_versions(case_id,batch_id,input_version),
    FOREIGN KEY(approval_id) REFERENCES approvals(approval_id));
CREATE TABLE reconciliation_results (
    result_id TEXT NOT NULL, operation_id TEXT NOT NULL, transaction_id TEXT NOT NULL,
    internal_ref TEXT NOT NULL, external_ref TEXT NOT NULL,
    amount_krw INTEGER NOT NULL CHECK(typeof(amount_krw)='integer' AND amount_krw BETWEEN 1 AND 1000000000000),
    match_status TEXT NOT NULL CHECK(match_status='MATCHED'), PRIMARY KEY(result_id,transaction_id),
    FOREIGN KEY(operation_id) REFERENCES operations(operation_id));
CREATE TABLE completion_decisions (
    decision_id TEXT PRIMARY KEY, logical_run_id TEXT NOT NULL UNIQUE, case_id TEXT NOT NULL, batch_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('REPROCESSED','WAITING_FOR_INPUT','ESCALATED','CANCELLED')),
    decision_at TEXT NOT NULL, input_version INTEGER NOT NULL, operation_id TEXT,
    verification_digest TEXT NOT NULL, report_facts TEXT NOT NULL,
    FOREIGN KEY(case_id,batch_id,input_version) REFERENCES batch_versions(case_id,batch_id,input_version),
    FOREIGN KEY(operation_id) REFERENCES operations(operation_id));
CREATE TABLE import_receipts (
    import_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL UNIQUE, case_id TEXT NOT NULL UNIQUE,
    dataset_fingerprint TEXT NOT NULL, fixture_schema_version TEXT NOT NULL,
    importer_version TEXT NOT NULL, business_schema_version INTEGER NOT NULL,
    initial_input_digest TEXT NOT NULL, financial_projection_digest TEXT NOT NULL,
    row_counts TEXT NOT NULL, initial_observed_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES cases(case_id));
CREATE TABLE applied_event_groups (
    case_id TEXT NOT NULL, group_id TEXT NOT NULL, group_payload_digest TEXT NOT NULL,
    old_version INTEGER NOT NULL, new_version INTEGER NOT NULL CHECK(new_version=old_version+1),
    applied_at TEXT NOT NULL, PRIMARY KEY(case_id,group_id),
    FOREIGN KEY(case_id) REFERENCES cases(case_id));
