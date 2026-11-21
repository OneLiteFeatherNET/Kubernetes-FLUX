## Why

The lake layers in `lake-dags` (`add-lake-layers`, merged) cannot run in production yet. They need four things that live in this repository: namespaces in catalog `corvus` to write into, Polaris principals that are allowed to read the Corvus tables and write those namespaces, a separate principal for the erasure DAG, and an Airflow pool that limits concurrent Spark jobs. Without them the nightly `refined` build fails at its first catalog call, and the erasure path that GDPR requests depend on has no identity of its own.

Corvus now writes three more tables next to `capture.raw` and `capture.events`: `capture.sessions`, `capture.segments` and `capture.session_ends`. The pipeline needs read access to those too. Corvus also needs one new write right: appending erasure requests to `quality.erasure_requests`, the table through which the lake learns about an erasure.

This change specifies that platform work. It does not implement the layers and does not change the packet path.

## What Changes

The code ships as `feat(data)`, PR title `feat(data): add lake layer platform`. All changes are in `foundation/data/clusters/feather-core/` and in the Entra ID tenant. The lake code stays in `lake-dags`.

- **Namespaces** in catalog `corvus`: `refined`, `features`, `labels`, `ref` and `quality`. Created by the Polaris setup Job, next to the existing `capture`.
- **Principal role `lake_pipeline`** with a new catalog role `pipeline`: read on `capture.raw`, `capture.events`, `capture.sessions`, `capture.segments` and `capture.session_ends` (table level); create, read and write on `refined`, `features`, `labels`, `ref` and `quality` (namespace level). No delete right on any table in `capture`.
- **Principal role `lake_erasure`**, separate from the pipeline, with a new catalog role `erasure`: read on the five `capture` tables (the erasure guards read them); read and write on `refined`, `features`, `labels` and `quality` for the deletes and the status write; expire and orphan cleanup on those namespaces. No write right on `capture`.
- **Corvus ingest** (principal `corvus_ingest`, the existing principal Corvus uses): table-level write on `quality.erasure_requests` only, added to the existing `ingest` catalog role.
- **`lake_maintenance` and `lake_sql`**: no grant change is needed, because both are catalog-wide grants that cover every namespace in `corvus`. This change verifies that instead of adding redundant namespace grants. `lake_sql` stays read-only. Its name is an open question (design, question 4).
- **Entra ID**: app roles `lake-pipeline` and `lake-erasure` on the existing API app "Polaris", one client app per role with that role assigned, both mapped in the Polaris role mapper. Client secrets are SOPS Secrets in namespace `data`. The Entra objects are created by the maintainer with `az` before the code PR (manual step, recorded in tasks). No IDs or secrets appear in this proposal.
- **Airflow pool `lake_spark`** with 2 slots, created by an idempotent Job from a version-controlled pool file. Design, decision 5, records the options.
- **Prerequisite**: the lake jobs read and write the bucket `polaris-corvus` with the owner keys of RGW user `polaris`. The bucket policy must allow those keys the lake paths. That is the separate PR `fix(data): grant polaris access to corvus lake objects`, which must be merged before the first lake run. This change does not depend on its content, only on its merge.

## Current state

- Catalog `corvus` on bucket `polaris-corvus` exists with namespace `capture`. Principal roles `corvus_ingest`, `corvus_ml`, `lake_maintenance` and `lake_sql` are created by `polaris/setup-catalog.py` (Job `polaris-setup-6`). Maintenance and SQL read grants are catalog-wide.
- Entra app roles on the API app "Polaris" are `corvus-ingest`, `corvus-ml`, `lake-maintenance`, and `lake-sql` (added by the DuckDB change). The role mapper in `polaris/release.yaml` filters on that list.
- Airflow runs in namespace `data` with KubernetesExecutor. `airflow/release.yaml` sets `airflowVersion: "3.4.0"`. The lake proposal `add-data-lake` pins chart 1.22.0 (Airflow 3.2.2). The two do not match (design, question 1).
- No pool is declared anywhere in the repository.

## Out of scope

- The layers, DAGs, Spark job and label rules (`lake-dags`, `add-lake-layers`).
- Corvus erasure and the Corvus ingest change that stops writing an erased key (Corvus repository, `feat(services): erase and retain capture data in iceberg tables`). This is task 2.7 of `add-lake-layers`. It is a related change, not part of this one.
- Removing the `trino` HelmRelease. The DuckDB change (`feat(data): query the lake from superset with duckdb instead of trino`) is merged; the Trino removal is its own follow-up.
- Blackhole's access path to the lake (`add-lake-layers`, open question 11).
- The privacy review of permanent `capture.raw` (`add-lake-layers`, task 1.4).

## Impact

- Files: `foundation/data/clusters/feather-core/polaris/` (`setup-catalog.py`, `setup-job.yaml` renamed to `polaris-setup-7`, `release.yaml` role mapper), `foundation/data/clusters/feather-core/airflow/` (`kustomization.yaml`, two SOPS env files, pool Job and its ConfigMap). Tests for the grant table in the repository's existing validation.
- Runtime: the setup Job re-runs once, creates five namespaces and one table, and adds grants. Existing grants and principals do not change. The Airflow release restarts only if its values change; this change does not change them.
- No new layer, no new HelmRelease, no new storage or database. Lake DAGs stay unscheduled in production until the lake code merges and the first run is recorded.
- Risk: a grant typo in the setup script can silently remove access for a principal that already works, because the Job treats 409 as success. The post-merge verification (tasks, group 7) checks every principal, including the negative cases.
