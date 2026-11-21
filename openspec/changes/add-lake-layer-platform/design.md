## Context

The lake (`add-data-lake`, merged) is Apache Iceberg tables in catalog `corvus` on bucket `polaris-corvus`, served by Polaris 1.9 with Entra ID tokens. Today the catalog holds one namespace, `capture`, owned by Corvus. The layers in `lake-dags` (`add-lake-layers`) need five more namespaces, a pipeline identity that reads Corvus data and writes the layers, an erasure identity that deletes from the layers but never from `capture`, one new write right for Corvus, and a pool in Airflow. The lake-dags design defines the contracts (`quality.erasure_requests` columns, table names, the erasure procedure). This document decides how the platform provides them.

## Decision drivers

- Least privilege by principal: the pipeline must not delete, the erasure role must not write `capture`, and Corvus must write one table only.
- Reuse the existing mechanism. Polaris state is created by one idempotent script (`setup-catalog.py`). A second mechanism for the same catalog would drift.
- Reproducible from git. Everything that can be a file in the repository is a file, and the only manual steps are the Entra ID objects, which `az` creates once.
- No secret, ID or key in this repository beyond SOPS-encrypted values.
- Keep the Polaris Job immutable-safe: a changed Job needs a new name.

## Decisions

### 1. Airflow version: confirm before go-live, do not decide here

**Context:** `add-lake-layers` (question 10) and `add-data-lake` (task 7.2) assume chart 1.22.0 with Airflow 3.2.2. `foundation/data/clusters/feather-core/airflow/release.yaml` on `main` sets `airflowVersion: "3.4.0"`. Pool handling depends on the version (decision 5).

**Decision:** the pool mechanism in decision 5 is written for Airflow 3.x CLI and REST API, which both versions have. Task 1.1 records the deployed version. If the pool CLI differs on the deployed version, decision 5 is revisited.

**Consequences:** `add-lake-layers` question 10 is answered by task 1.1 of this change. The Airflow version is not changed here.

### 2. Provisioning: extend `setup-catalog.py`, bump the Job

**Options**

1. Extend `setup-catalog.py` with the new namespaces, principal roles, catalog roles, grants, the principal entries and one table. Bump the Job from `polaris-setup-6` to `polaris-setup-7`. **Chosen.**
2. A second Job with its own script for the lake roles. Rejected: two scripts would write one catalog, and the order of catalog-level and namespace-level grants (the script comment explains why it matters) would be split across files.
3. Manual steps with `polaris-admin-tool` or the REST API, recorded in Outline. Rejected: the grants would not be reviewable or reproducible from git.

**Decision:** option 1. The script already creates namespaces before namespace grants and treats 409 as success, so it can add the new state on a re-run. The new tables are created by the same script (decision 3).

**Consequences:** `setup-job.yaml` is renamed to `polaris-setup-7`, and the old Job is pruned by Flux (it is part of the same layer). Grant changes made by the Job are additive; the script never revokes. Removing a grant is a manual step, as it is today for principals.

### 3. The table `quality.erasure_requests` is created by the setup script

**Context:** Corvus must append to `quality.erasure_requests` (table-level write grant), and a table-level grant on a table that does not exist fails. `add-lake-layers` creates its tables in Spark on the first run, which is after this change. Corvus must not depend on the first lake run.

**Options**

1. The setup script creates the table with the contract schema from `add-lake-layers` decision 14 through the Iceberg REST API, before the grants run. **Chosen.**
2. Corvus creates the table with its own writer. Rejected: Corvus has `TABLE_CREATE` only on `capture` and would need a new namespace-level right.
3. The first lake run creates it. Rejected: the Corvus erasure change would fail until then.

**Decision:** option 1. The schema is copied into the script once. A later schema change needs a new table or an Iceberg schema evolution, and both go through lake-dags review.

**Consequences:** the schema exists in two places (lake-dags and the setup script). The lake-dags contract is the source, the script copy is checked by a test in this change (tasks group 3). Open question 5 asks whether the lake should own the schema.

### 4. Grants: new catalog roles, existing catalog-wide grants kept

**Context:** the brief asks to extend the grants of `lake_maintenance` and `lake_sql` to the new namespaces. Both existing catalog roles are catalog-level (`type: catalog`), so they already cover namespaces created later.

**Options**

1. Keep `maintenance` and `sql` unchanged and rely on catalog-level inheritance. Verify it in the post-merge checks. **Chosen.**
2. Add namespace-level grants for the five namespaces to both roles. Rejected: duplicate grants that must be kept in sync with the namespace list, with no gain in access.
3. Reuse `maintenance` for the erasure maintenance grants. Rejected: `maintenance` has `TABLE_WRITE_DATA` on the whole catalog, which includes `capture`. The erasure role must not have that.

**Decision:** option 1 for `maintenance` and `sql`. A new catalog role `pipeline` (for `lake_pipeline`) and a new catalog role `erasure` (for `lake_erasure`), with the grants in the table below.

| Catalog role | Scope | Privileges |
| --- | --- | --- |
| `pipeline` | table: `capture.raw`, `capture.events`, `capture.sessions`, `capture.segments`, `capture.session_ends` | `TABLE_READ_DATA`, `TABLE_READ_PROPERTIES` |
| `pipeline` | namespace `capture` | `NAMESPACE_READ_PROPERTIES`, `TABLE_LIST` |
| `pipeline` | namespaces `refined`, `features`, `labels`, `ref`, `quality` | `TABLE_CREATE`, `TABLE_READ_DATA`, `TABLE_WRITE_DATA`, `TABLE_WRITE_PROPERTIES`, `TABLE_LIST`, `NAMESPACE_LIST`, `NAMESPACE_READ_PROPERTIES` |
| `erasure` | table: `capture.raw`, `capture.events` (guards) | `TABLE_READ_DATA`, `TABLE_READ_PROPERTIES` |
| `erasure` | namespace `capture` | `NAMESPACE_READ_PROPERTIES`, `TABLE_LIST` |
| `erasure` | namespaces `refined`, `features`, `labels`, `quality` | `TABLE_READ_DATA`, `TABLE_WRITE_DATA`, `TABLE_WRITE_PROPERTIES`, `TABLE_LIST`, `NAMESPACE_LIST`, `NAMESPACE_READ_PROPERTIES` |
| `ingest` (existing) | table: `quality.erasure_requests` | `TABLE_WRITE_DATA`, `TABLE_READ_PROPERTIES` |

Points that need a check on the cluster (task 1.3):

- Polaris has no separate delete privilege. Row-level deletes (Iceberg position or equality deletes) are writes, so `TABLE_WRITE_DATA` covers the erasure deletes. That matches how the maintenance role is already defined.
- `TABLE_WRITE_PROPERTIES` covers snapshot and branch metadata changes. The write-audit-publish branches of `add-lake-layers` (decision 3) need it.
- The pipeline does not get `TABLE_DROP`. Dropping a stale audit branch is a metadata change, not a table drop.

**Consequences:** `erasure` has no write privilege on `capture`. Polaris gives table-level and namespace-level grants on the same catalog role, both are additive, and the verification (tasks group 7) tests the negative cases.

### 5. Airflow pool `lake_spark`: a Job runs `airflow pools import`

**Context:** nothing in the repository declares a pool. The `airflow` HelmRelease sets `config`, `dags` and component values, and this repository does not vendor the chart, so whether the chart has a pool value is not verified.

**Options**

1. Chart value, if the pinned chart exposes pools. Not verified. Would be the simplest, if available.
2. A Job in namespace `data` that runs `airflow pools import` on a pool file from a ConfigMap, after the Airflow release is Ready. Idempotent; the import sets the listed pools. **Chosen, unless option 1 is verified.**
3. Airflow REST API `POST /api/v2/pools` from a Job, with an API token. Rejected: needs a token that is not in the repository today.
4. Creating the pool from DAG code. Rejected: a pool must exist before the task that uses it runs, and pool creation from DAG code is not a supported pattern.

**Decision:** option 2 unless task 1.2 verifies option 1. The Job uses the Airflow image and the metadata secret `airflow-db` (the same way the scheduler does). The pool file is `pools.json` with `lake_spark`, 2 slots, description text.

**Consequences:** the Job reads the metadata database, so it must run after the database is Ready, which the layer ordering covers (`foundation-data` depends on `foundation-databases`). It runs again on every change of the pool file (new Job name, per Job immutability).

### 6. Entra ID: two app roles, two client apps, mapped in the Polaris role mapper

**Decision**

- App roles `lake-pipeline` and `lake-erasure` on the API app "Polaris" (appId `9e2548f4-5e10-440d-9444-cad813a20c87`), with the same value and role name pattern as the existing `lake-sql`.
- Client apps "Polaris Lake Pipeline" and "Polaris Lake Erasure", each with its own role assigned. One client per role, as for the existing clients. A shared client would mean one secret for two privilege sets.
- The Polaris role mapper in `polaris/release.yaml` gets both names in `filter` and two `mappings` entries: `lake-pipeline` to `PRINCIPAL_ROLE:lake_pipeline`, `lake-erasure` to `PRINCIPAL_ROLE:lake_erasure`.
- The principal entries in `setup-catalog.py` (`PRINCIPALS`) are keyed by the client app ids. The maintainer records the ids in the code PR. The ids are not secrets, but this proposal does not include them, because the objects do not exist yet.
- Client secrets are SOPS Secrets, `lake-pipeline-oauth.sops.env` and `lake-erasure-oauth.sops.env`, in namespace `data`, generated like `lake-maintenance-oauth`. Until the maintainer sets a real secret with `sops`, the value is a placeholder, as it is today for the maintenance client.

**Consequences:** `az` creates the objects, not Flux. The steps are in tasks group 2. A role that is not assigned to a client gives an empty `roles` claim, and Polaris then has no principal role; the verification catches that.

### 7. Lake S3 credentials: owner keys of RGW user `polaris` (accepted, with an alternative)

**Context:** Polaris issues no vended credentials (`stsUnavailable: true`), so the Spark jobs authenticate to RGW with static keys. The lake jobs read and write `polaris-corvus`. The bucket policy must allow the owner keys of user `polaris` the lake paths; PR `fix(data): grant polaris access to corvus lake objects` adds that statement (prerequisite).

**Options**

1. Owner keys of `polaris`, as the existing Polaris writer already uses. **Accepted for this change.**
2. A dedicated RGW user for the lake jobs with a bucket policy limited to the lake paths and the operations used, like the read-only `trino-lake` user of the DuckDB change. Better least privilege, one more user and secret. Recorded as the follow-up.

**Consequences:** Airflow pods (Spark drivers and executors) receive a key that can do anything the `polaris` owner can do on `polaris-corvus`. The narrower option is open question 3.

### 8. `lake_sql` keeps its name

**Decision:** the principal role stays `lake_sql`. The DuckDB change (merged in #704) maps the client "Polaris Superset" to it through the role mapper and `PRINCIPALS`, and the name appears in the Entra app role. A rename would change a live mapping. Question 4 asks whether it should be renamed in a later change, after the Trino removal.

## Open questions

1. **Airflow version.** `main` pins `airflowVersion: 3.4.0`, and `add-data-lake` and `add-lake-layers` assume chart 1.22.0 (Airflow 3.2.2). Task 1.1 records the deployed version and the chart source before the Job is written.
2. **Pool option.** Whether the pinned chart exposes pools. If it does, decision 5 option 1 replaces the Job (task 1.2).
3. **Narrower S3 identity.** Whether the lake gets its own RGW user (decision 7 option 2). Needs the maintainer's call, and the bucket policy PR would change.
4. **Name of `lake_sql`.** Whether the principal role is renamed after the Trino removal. Not part of this change.
5. **Schema owner of `quality.erasure_requests`.** Whether lake-dags owns the schema and the setup script reads it, instead of a copy (decision 3).
6. **Pipeline and erasure on `quality`.** Polaris cannot exclude one table from a namespace grant. The pipeline can therefore write `quality.erasure_requests`, and erasure can write `quality.check_results`. Narrowing both to table-level grants requires the tables to exist before the Job runs. Recommendation: keep namespace grants now, narrow them in a follow-up once the tables exist.
7. **Erasure role and Corvus ingest.** Corvus must stop writing an erased key (`add-lake-layers` question 15). That is the Corvus change, not this one.
8. **Verification of Polaris privilege names** in 1.9 (for example `TABLE_READ_PROPERTIES` on table grants, and whether a table grant needs namespace read). Task 1.3 checks them on the cluster in a scratch namespace. Anything that differs is changed in the table above before the code PR.

## Consequences

- The lake jobs can run in production once the code PR of `lake-dags` merges, the Entra objects exist, and the bucket policy PR is merged. Until then the DAGs stay unscheduled in production.
- Corvus gets one new table-level write right, and no new namespace right.
- The setup Job grows; if a grant fails the Job retries (`backoffLimit: 30`) and the layer shows the failure, rather than the change being silently partial.
- Trade-off accepted: decision 7 gives Airflow pods owner-level S3 keys on the warehouse bucket.
