## Context

Trino and Superset are live since #672 (`add-lake-sql`). Trino runs as chart `trino` 1.45.0 (application 484) with one coordinator and two workers in namespace `data`. Its `corvus` catalog is an Iceberg REST catalog at `polaris.data.svc.cluster.local:8181/api/catalog`, with OAuth2 client credentials of the Entra client "Polaris Trino" and S3 keys of the read-only RGW user `trino-lake`. The `trino-lake` user has no bucket of its own. Its read rights come from the `TrinoLakeRead` statement in the bucket policy of `polaris-corvus`.

Superset is not the official chart. It is an instance of the Apache Superset Kubernetes Operator (chart `superset-operator` 0.3.0, namespace `superset-system`, watches namespace `data`). The `Superset` CR `superset` pins image `apache/superset:6.1.0`, which ships without database drivers. An init container installs `trino`, `psycopg2-binary` and `authlib` with `uv` into `/opt/pydeps` on every pod start. Container resources: request 1Gi and limit 2Gi (web server and workers). Lifecycle init: limit 1Gi. The bundle dashboards use database UUID `2601a04b-...`, whose URI is `trino://superset@trino.data.svc.cluster.local:8080/corvus`.

Polaris has `stsUnavailable: true` for `polaris-corvus`, so no vended S3 credentials exist. Every reader needs its own S3 keys. Polaris maps client app ids to principals in `setup-catalog.py` (`PRINCIPALS`) and roles from Entra app roles in `release.yaml` (`lake-sql` to `PRINCIPAL_ROLE:lake_sql`). The catalog role `sql` already has read-only grants: `TABLE_LIST`, `VIEW_LIST`, `NAMESPACE_LIST`, `TABLE_READ_PROPERTIES`, `TABLE_READ_DATA`.

Data volume: the bucket is small. The exact size is measured in task 1.4. Constraint: no outage of the dashboards during the switch and no change to the Corvus ingest path.

## Goals / Non-Goals

**Goals**

- Dashboards load in under 30 seconds.
- Superset is not OOM-killed under dashboard load.
- Reads go only through the Iceberg catalog, with read-only credentials.
- Three JVMs and their limits are removed.

**Non-Goals**

- A general SQL lab for many users. Few users are expected.
- Write access from SQL. Denied at Polaris, at RGW and in the DuckDB session.

## Decision drivers

- Memory: the current engine caused the outages. Less memory in total is a goal.
- Read-only by construction, with the same credential boundaries as today.
- Reproducible images. No package or extension download at pod start.
- Small blast radius: Superset is the only consumer, and it is internal.

## Decisions

### 1. Query engine: DuckDB inside Superset

**Decision:** DuckDB runs in the Superset processes through `duckdb-engine`. It reads Iceberg through the `iceberg` extension and Parquet through `httpfs`.

**Considered options**

- **Single-node Trino (coordinator also acts as worker).** Keeps the SQL dialect and the connector. Still one JVM with the same heap and GC behaviour that failed today. Kept as the fallback if task 1 finds a dialect blocker that DuckDB cannot handle.
- **ClickHouse or StarRocks.** Rejected. Both need their own stateful cluster and their own copy or connector work for Iceberg REST. Their maturity against Polaris REST is lower than the DuckDB path, and the operations cost does not match a few users.

**Consequences:** the query runs in the Superset process, so Superset's memory limit must cover it (decision 4). SQL dialect differences must be inventoried (risks).

### 2. Catalog access: Iceberg REST through Polaris, read-only principal

**Decision:** DuckDB attaches the catalog with `ATTACH 'corvus' AS corvus (TYPE iceberg, ENDPOINT ..., ACCESS_DELEGATION_MODE 'none', SECRET ...)`. The secret is `CREATE TEMPORARY SECRET ... TYPE iceberg` with `CLIENT_ID`, `CLIENT_SECRET` and `OAUTH2_SERVER_URI` of the Entra token endpoint. S3 keys come from a `TYPE S3` secret. The Polaris identity is a new Entra client "Polaris Superset" with app role `lake-sql`, mapped to principal `lake_sql`, which has only the read grants of the catalog role `sql`.

**Considered options**

- **Read the Parquet files under `s3://polaris-corvus/...` directly.** Rejected. It bypasses Iceberg snapshots, delete files and the catalog's access rules, and it would show files that a snapshot has expired.
- **Share the "Polaris Trino" principal.** Rejected for step 1. Removing Trino would then break Superset, and the audit trail would not separate the two clients.

**Consequences:**

- The client secret is a new SOPS value in `superset-secrets`. The operator must create the Entra client and the principal in the setup Job before the switch.
- Temporary secrets are not written to disk. Persistent DuckDB secrets are not used, because their storage location is not controlled here.
- The RGW keys of `trino-lake` are used in step 1 from the existing Secret `trino-lake-s3`, in the same namespace. Task 5 moves them into a Superset-owned Secret, because `trino-lake-s3` is removed with Trino.

### 3. Packaging: custom image, extensions baked in

**Decision:** build `superset-duckdb` from `apache/superset:6.1.0` in CI, pinned to:

- `duckdb` and `duckdb-engine` at exact versions (the DuckDB 2.x line that the official docs describe; the exact patch is chosen in task 2.3),
- `trino`, `psycopg2-binary` and `authlib` as today (`trino` is dropped in task 5),
- the extensions `iceberg` and `httpfs`, and `avro` if the iceberg extension needs it (`avro` is a community extension, see facts).

The extensions are installed during the image build into a directory named by the DuckDB extension directory setting. At runtime the connection sets `autoinstall_known_extensions` to `false` and `LOAD`s from that directory. The image is pushed to Harbor with a tag that changes per build. The `Superset` CR `spec.image.tag` changes with it, which also triggers the operator's migration.

**Considered options**

- **Keep the start-up `uv pip install` and add `duckdb` to it, with `INSTALL` at first use.** Rejected. Pods would depend on PyPI and on the DuckDB extension repository at every start, the versions would drift, and a rolling restart during an outage would fail.
- **Copy the extensions from another image or a ConfigMap at start.** Possible, but more moving parts than a build-time install. Not chosen.

**Consequences:** the repository has no image build pipeline today (`.github/workflows/` has none). Task 2.3 decides where the Dockerfile lives and which workflow pushes it to Harbor. The init container `install-drivers` loses its DuckDB parts once the image has them.

### 4. Resources and DuckDB settings

**Decision (to measure in task 4):**

- Superset container: request 2Gi memory, limit 6Gi (from 1Gi and 2Gi). Request CPU stays at 500m.
- Gunicorn worker count set explicitly to 2 (confirm how the operator sets it, task 2.1).
- Per connection, through `DB_CONNECTION_MUTATOR` (verified in Superset's `config.py`, see facts): `memory_limit` 2GB, `threads` 2, `temp_directory` on an `emptyDir` with a size limit of 10Gi.

The arithmetic: 2 workers times (about 0.5 GiB Python and Superset baseline plus 2 GB DuckDB limit) is about 5 GiB. The 1 GiB above that is headroom for allocations DuckDB's buffer manager does not count, and for the operator's init container.

**Considered options:** one worker with a larger memory limit. Rejected: a long query blocks all dashboards of that pod.

**Consequences:** the net memory limit change is minus 8 GiB (Trino 12 GiB out, Superset 4 GiB in). `threads` is set explicitly, because DuckDB defaults to the number of CPU cores it sees.

### 5. Migration in two steps

**Decision:**

- **Step 1 (code PR, `feat(data)`):** DuckDB image, Polaris principal, NetworkPolicy egress, resources, `corvus` switched to DuckDB, `corvus-trino` added for comparison. Trino keeps running. The dashboards are verified on DuckDB (task 4) before anything is removed.
- **Step 2 (removal PR):** remove Trino and its secrets, NetworkPolicy, source, layer entry and the `corvus-trino` connection (task group 5). The title carries `!` only if the inventory finds a consumer outside Superset. Otherwise it is `feat(data): remove trino after superset moved to duckdb`.

**Considered options:**

- **One PR with a verification gate.** Rejected. A failed verification would then leave the cluster with neither engine working, and the gate would be a manual step inside a merge.
- **Two changes.** Kept as the fallback if the maintainer wants one code PR per change. The removal then becomes `remove-trino` with its own proposal.

**Consequences:** Trino stays with its memory limits for the length of the verification. Trino's Polaris principal stays until step 2, so both principals can read.

### 6. Dialect and dataset SQL

**Decision:** dataset SQL is kept as is where DuckDB accepts it. Changes are made per dataset and reviewed in the code PR. The inventory is task 1.2. Functions seen in the bundle: `CAST`, `LAG`, `to_hex`, `substr`, `count`, `floor`, `COALESCE`, `min`, `max`, `power`, `abs`, `sum`, `sqrt`, `approx_percentile` (in the movement metrics), `VALUES` with a column list, `INTERVAL '7' DAY`, `current_date`. `to_hex` on a blob and `approx_percentile` are the two known risks (see facts).

## Verified DuckDB facts and open points

Sources: DuckDB documentation and blog posts, read through Context7 (`/duckdb/duckdb-web`) and by WebFetch. The docs site redirected to `duckdb.org/docs/2.0/...`, which is the current line. Status: **verified** means the text was read; **unverified** means it must be checked in the spike of task 2.2.

| Fact | Status | Source |
|---|---|---|
| `ATTACH` with `TYPE iceberg`, `ENDPOINT`, `SECRET`; `ACCESS_DELEGATION_MODE` values `vended_credentials` (default) and `none` | verified, introduced in 1.4.1 LTS | duckdb.org blog 2025-10-07 (DuckDB 1.4.1), Context7 `/duckdb/duckdb-web` |
| Attaching a catalog that does not vend credentials was broken before 1.4.1 | verified (blog text) | same |
| `CREATE SECRET (TYPE iceberg, CLIENT_ID, CLIENT_SECRET, OAUTH2_SERVER_URI)` | verified | duckdb.org/docs/2.0/core_extensions/iceberg/catalogs.html |
| `OAUTH2_SCOPE` as a secret option | verified only in a third-party (Lakekeeper) example on the same page | same |
| Entra token endpoint and scope `api://.../.default` work with the secret | unverified | spike |
| `TYPE S3` secret with `KEY_ID`, `SECRET`, `ENDPOINT`, `URL_STYLE 'path'`, `USE_SSL false`, `REGION` | verified in examples | duckdb.org blog 2025-09-16 (DuckDB 1.4.0) |
| The iceberg extension reads S3 data with that S3 secret when no vended credentials exist | stated in the 1.4.1 blog; end-to-end unverified | blog 2025-10-07 |
| ENDPOINT form for RGW (`host`, with or without port, scheme) and the REST path prefix `/api/catalog` | unverified | spike |
| Minimum DuckDB version | the 2.0 page states none; the attach mode needs 1.4.1 | docs 2.0, blog |
| `autoinstall_known_extensions` and `autoload_known_extensions` default `true`; can be set to `false` | verified | duckdb.org/docs/2.0/configuration/overview.html |
| `enable_external_access` default `true`; `allow_unsigned_extensions` default `false` | verified | same |
| `extension_directory` is deprecated; use `extension_directories` | verified | same |
| Directory layout of pre-installed extensions and offline `LOAD` | unverified | spike |
| `avro` is a community extension (`INSTALL avro FROM community`); needed by iceberg or not | community status verified (2024-12 blog); need unverified | duckdb.org blog 2024-12-09 |
| `memory_limit` (default 80% RAM), `threads` (default CPU cores), `temp_directory` (default `.tmp`) | verified | docs 2.0 configuration |
| `duckdb-engine` accepts DuckDB options in `connect_args` (`config`) and experimental `preload_extensions` | verified | github.com/Mause/duckdb_engine README (fetched) |
| `duckdb-engine` has no general init-SQL hook | verified as "not documented" | same |
| Per-connection `ATTACH` and secrets in Superset (via `connect` event, or a hook in `superset_config.py`) | unverified | spike |
| `DB_CONNECTION_MUTATOR(uri, params, username, security_manager, source)` returns `(uri, params)` passed to `create_engine(url, **params)` | verified | apache/superset `superset/config.py` (Context7 `/apache/superset`) |
| `superset_config` path through the operator `config:` block | verified in repo (`superset.yaml` comment) | repository |
| `extra.engine_params` in a Superset database is passed to `create_engine` | unverified in the docs fetched (the Superset docs page returned 404) | spike |
| A dataset reference `corvus.capture.events` resolves when the attached catalog is `corvus` | expected, unverified | spike |
| `to_hex` on a BLOB, `approx_percentile`, `INTERVAL '7' DAY` | unverified | spike |

**Open points for the maintainer:** whether removal is a second PR inside this change or a separate change (decision 5); the Entra client id of "Polaris Superset" (created in task 2.1).

## Risks / Trade-offs

- **SQL dialect differences:** some datasets may not run on DuckDB unchanged. Mitigation: task 1.2 inventories every dataset and metric; fallback is decision 1's single-node Trino, which is then kept for those datasets only.
- **Concurrency:** DuckDB runs once per Superset worker process. Each connection is in-memory. Few users are expected, so this is acceptable. A busy dashboard blocks one worker, which is why there are two.
- **Extension compatibility with Polaris OAuth:** the token endpoint and scope are unverified (see facts). Mitigation: the spike in task 2.2 before any manifest change.
- **S3 addressing to RGW:** path-style with HTTP on port 80 is assumed to work for DuckDB as it does for Trino. Mitigation: spike.
- **Path reads are not blocked technically:** the RGW user `trino-lake` can read the whole bucket, so a dataset could read Parquet by path. Mitigation: dataset review and the scenario in the spec. A narrower RGW policy or a per-prefix user is a later option, not part of this change.
- **Memory limit is not a hard cap:** DuckDB's `memory_limit` covers its buffer manager, not every allocation. Mitigation: the 1 GiB headroom, and task 4 measures the peak.
- **Secret exposure:** the client secret and the RGW keys reach the SQL session. Mitigation: temporary secrets only, no SQL logging of secret statements in Superset's query log (verify in spike).

## Migration Plan

1. Inventory (task group 1) and the spike (task 2.2) in a scratch Superset pod, not in the cluster manifests.
2. Polaris: create the Entra client, the principal, and the setup Job change. Merge the code PR. Confirm the principal exists in Polaris.
3. Build and push the image. Switch the Superset CR to it. Switch `corvus` to DuckDB and keep `corvus-trino`.
4. Verify the dashboards on DuckDB, against `corvus-trino` for parity (task group 4).
5. Removal PR (task group 5). Delete the Polaris principal "Polaris Trino" by hand afterwards.

**Rollback (before step 5):** point the `corvus` database back to `corvus-trino`'s URI, or revert the code PR. Trino is still running, so no data path is lost.

## Open Questions

- Exact DuckDB version and the matching `duckdb-engine` version (task 2.3).
- Whether `avro` is needed by the iceberg extension (task 2.2).
- The gunicorn worker count the operator sets, and whether it can be fixed explicitly (task 2.1).
- Whether `add-lake-sql` should be archived with this change or separately.
