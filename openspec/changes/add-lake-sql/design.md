## Context

The lake from `add-data-lake` is merged: catalog `corvus` with namespace `capture` on Polaris, bucket `polaris-corvus` on Ceph RGW (`feather-s3`), and the Polaris principal roles `corvus_ingest`, `corvus_ml` and `lake_maintenance`. Corvus will write the packet data into tables `capture.events` and `capture.raw`. Those tables do not exist yet, so the first query paths are tested on smoke tables.

Staff need an interactive SQL path. The query engine must speak the Iceberg REST catalog with OAuth2, and the interface must be usable by people who do not write code. The path must be read-only, reachable only by assigned staff, and must not change the packet path.

## Verified facts

Repository and cluster (2026-11-18):

- Polaris runs as HelmRelease `polaris` 1.9.0 in namespace `data`, with two replicas, `persistence: relational-jdbc` on CNPG database `polaris`, and internal `ClusterIP` only. Its authentication is currently `mixed` in `foundation/data/clusters/feather-core/polaris/release.yaml`, not `external` as the `add-data-lake` spec says. Entra ID tokens are accepted in both modes, so this change works with either. Switching to `external` stays with the platform change.
- The OIDC principal mapping uses `nameClaimPath: azp`, so each client app is a Polaris principal named by its client app id. The filter regex `^(corvus-ingest|corvus-ml|lake-maintenance)$` and its three mappings must gain the `lake-sql` entry.
- The setup Job is `polaris-setup-3` in `foundation/data/clusters/feather-core/polaris/setup-job.yaml`. Jobs are immutable, so a changed `setup-catalog.py` needs `polaris-setup-4`.
- The bucket `polaris-corvus` and its ObjectBucketClaim write the Secret `polaris-corvus` into namespace `data`. That Secret belongs to the Polaris user and is not used by Trino (decision 4).
- The internal Gateway is `eg` in namespace `envoy`, section `https`. The pattern for an internal route is `products/corvus/clusters/feather-core/corvus/httproute.yaml`.
- CNPG cluster `feather-core-cluster-pg` has `max_connections: 200` and is close to saturation. Existing managed roles use `connectionLimit: 20`.
- Dragonfly (`foundation/databases/clusters/feather-core/dragonfly`) is a shared data store, not a cache: `cache_mode` is off, so keys are not evicted, and `maxmemory` is 1 GB. Its NetworkPolicy admits only a fixed list of namespaces.
- Metabase is already in the cluster (`services/collaboration`). It uses CNPG PostgreSQL.
- Superset chart 0.22.8 enables two bundled subcharts by default: `postgresql` (`postgresql.enabled: true`) and `redis` (`redis.enabled: true`, Bitnami, marked deprecated by the chart). Both must be disabled.
- Trino chart 1.45.0 (application 484) exposes `accessControl` (rules file) and `additionalCatalogs` (one properties file per catalog) as values.
- Kyverno policy `require-memory-resources` is in audit mode in this repository (`failurePolicy: Ignore`, results land in PolicyReports, requests are never denied). The requirement is met by explicit requests and limits on every container, and checked in the PolicyReports.

Entra ID (done by the maintainer, 2026-11-18):

- App role `lake-sql` exists on the API app "Polaris" (`9e2548f4-5e10-440d-9444-cad813a20c87`) and is assigned to client "Polaris Trino" (`8832de30-7197-40ed-a2bc-68c88bc770cb`).
- The Superset app (`b592b27d-d127-4de6-9e4d-d3e252b46852`) has the app role `superset-admin` (value `superset-admin`, user-assignable), `appRoleAssignmentRequired` is `true`, and the maintainer is assigned `superset-admin`.
- The client secrets for "Polaris Trino" and for the Superset app are not yet created. They are created during implementation and stored as SOPS Secrets.

## Decision drivers

- Read-only access for staff, enforced in several independent layers, including the object store.
- Each staff member is authenticated with Entra ID, and only assigned staff can sign in.
- Trino must not hold credentials that can write to the bucket.
- Reuse what the platform already runs (Polaris, CNPG, Entra ID, Gateway). No new data store and no new cache unless it is isolated.
- Memory is tight on the cluster, so the sizing must be modest, and the Superset cache must not touch the shared Dragonfly.
- Trino and Superset must never hold the key material that protects the raw packet payloads.
- Official charts from the projects' own repositories, with pinned versions.

## Options and decisions

### 1. Query engine: Trino

**Options**

1. Trino (official chart `trino`). Chosen. Fast interactive SQL, mature Iceberg REST catalog support with OAuth2 credentials, and a Superset dialect.
2. Apache Kyuubi with Spark. Rejected for interactive use: it would keep the stack entirely Apache, but each user session runs a Spark session, which costs more memory per session and starts more slowly. Kyuubi remains an option if the platform later needs Spark-based SQL.
3. Spark Thrift Server directly. Rejected: no session isolation and no OIDC.

**Decision:** Trino.

**Consequences:** Trino is a second long-running query engine next to Spark. Spark stays for batch maintenance (`add-data-lake`), Trino for interactive SQL. Both read the same Iceberg tables through the same catalog.

### 2. Interface: Apache Superset

**Options**

1. Apache Superset (official chart `superset`). Chosen. Apache top-level project, native Trino and Iceberg support, SQL Lab for staff who write SQL and charts for staff who do not, and OIDC login through Flask-AppBuilder.
2. Metabase (already running in `services/collaboration`). Closest alternative: it runs on CNPG PostgreSQL and provides a browser UI with OIDC. Rejected for this path because its open-source edition has no Trino driver that was verified in this proposal, and Superset serves the lake dialect and Iceberg metadata better. Metabase stays in place for its current users.
3. Raw SQL clients (DBeaver, the Trino CLI) only. Rejected as the sole path: staff without a local client would have no access, and every client would need its own credentials.

**Decision:** Superset.

**Consequences:** Superset needs its own metadata database and cache (decision 6). Its web server holds a connection to Trino, and its users are staff with Entra accounts.

### 3. Trino access: read-only, enforced in four layers

**Decision:** read-only is enforced by four independent layers, so that a mistake in one does not open writes:

1. Polaris: the catalog role `lake_sql` holds only the read grants on catalog `corvus`, so a commit from the principal "Polaris Trino" is refused with HTTP 403.
2. Trino: file-based access control with the catalog rule `allow: read-only` for `corvus`, so a write statement fails before it reaches the connector. The `system` catalog stays closed to write statements.
3. RGW: the Trino S3 user `trino-lake` can only read the bucket (decision 4), so an object write made with its keys is refused by RGW, even if the layers above were bypassed.
4. Network: only Superset can open a connection to Trino, so no other pod can submit queries.

**Consequences:** a write has to pass the Polaris commit, the Trino access control and the RGW bucket policy. Each layer is tested separately in tasks 5.3 to 5.5.

### 4. S3 access: dedicated read-only RGW user

**Options**

1. The keys of the existing Secret `polaris-corvus` (the Polaris user, which can write). Rejected: Trino would then hold write access to the whole bucket, and a Trino process could write objects that Polaris does not know about.
2. A dedicated RGW user `trino-lake` (CephObjectStoreUser in `rook-fr01/users/`) with a bucket policy on `polaris-corvus` that allows only `s3:GetObject` and `s3:ListBucket` for that user. Chosen. The bucket policy is applied with the same mechanism as the parallel change `feat(corvus): grant corvus access to the iceberg lake` (Flux PR in progress), which grants the Corvus namespace access to the same bucket. This change uses that pattern for `trino-lake`.
3. Enable STS on `feather-s3` and vend table-scoped credentials. Rejected, as in `add-data-lake`: a change to a shared object store, excluded by the additive constraint.

**Decision:** option 2. Vended credentials are off in the Iceberg REST client (`vended-credentials-enabled=false`), so Trino never asks Polaris for credentials and uses the `trino-lake` keys from its own Secret.

**Consequences:**

- Rook generates the keys of `trino-lake` in its own namespace. They are copied into a SOPS-encrypted Secret in namespace `data` for the Trino coordinator and worker; the keys are not in git in plain text.
- The bucket policy document contains no secret and is in git.
- An object write made with the `trino-lake` keys (PutObject, DeleteObject) is refused by RGW with AccessDenied. This is tested in task 5.4.
- Trino may need `s3:GetBucketLocation` for path-style access. If so, that one action is added to the policy and the policy stays read-only. The smoke test confirms whether it is needed (open question 2).
- Keys are rotated by hand, as for all static keys in this platform.

### 5. Authentication and identity

**Decision:**

- Trino authenticates to Polaris with the OAuth2 client credentials flow of Entra ID for client "Polaris Trino" (client id `8832de30-7197-40ed-a2bc-68c88bc770cb`), scope `api://9e2548f4-5e10-440d-9444-cad813a20c87/.default`. The token carries the app role `lake-sql`, which is assigned to that client (Entra facts above).
- The Iceberg OAuth2 token endpoint is the Entra endpoint `https://login.microsoftonline.com/1a14dfb5-0eac-41bf-94cb-195c2e387520/oauth2/v2.0/token`, not the Polaris endpoint, so the Trino `oauth2` server URI is set explicitly.
- Staff authenticate to Superset with Entra ID (`AUTH_OAUTH`, provider `azure`). The Superset Entra app requires user assignment (`appRoleAssignmentRequired` is `true`), so an unassigned user is refused by Entra before Superset sees the request.
- The Superset role comes from the app role claim at each login (`AUTH_ROLES_SYNC_AT_LOGIN`): the Entra app role `superset-admin` maps to Superset's `Admin` role; every other assigned user gets `Gamma`. Gamma has no permission on any database until the maintainer grants one, so a new assigned user sees no data by default.
- Superset connects to Trino as one fixed Trino user, `superset`. Trino therefore records every query as `superset`. Per-user identity in Trino (impersonation of the logged-in user) is out of scope. Query history per person is kept in Superset's own metadata database.

**Consequences:** only assigned staff can open the UI, and only the maintainer has admin rights. Adding a person is an Entra assignment, and granting data access is a Superset permission the maintainer sets.

### 6. Superset metadata database and cache

**Metadata database.** Superset uses PostgreSQL. Decision: a managed role `superset` (`connectionLimit: 10`, sized against `max_connections: 200`) and a Database `superset` (owner `superset`, `databaseReclaimPolicy: retain`) on `feather-core-cluster-pg`, with SOPS-encrypted Secrets for the role and the connection, following the `airflow` and `polaris` pattern. The chart's bundled PostgreSQL is disabled.

**Cache options**

1. Shared Dragonfly (`dragonfly.dragonfly.svc.cluster.local:6379`). Rejected: it is a data store without eviction (`cache_mode` off) and has 1 GB `maxmemory`, shared with shlink, outline, harbor, n8n and others. A Superset cache that fills the store would make writes fail for every consumer. Adding `data` to its NetworkPolicy would also widen the shared store's trust boundary.
2. The chart's bundled Redis (Bitnami subchart, deprecated by the chart). Rejected: a new Redis in the namespace for a cache that is optional, and a dependency on a subchart the chart marks as deprecated.
3. No Redis, file-system cache inside the Superset pod. Chosen. Superset works without Redis if the cache and the asynchronous query features are off. The cache is a convenience, not a data store; losing it on pod restart costs only query re-execution. Asynchronous queries and alerting workers are not enabled in this change, so no Celery broker is needed.

**Consequences:** Superset runs as one web replica with no Celery worker and no Redis. If asynchronous queries are needed later, a separate change decides between a dedicated Redis and a Dragonfly database with its own number and eviction policy.

### 7. Network exposure

**Decision:** Trino is `ClusterIP`. The NetworkPolicy of Trino admits ingress only from Superset pods (port 8080) and from its own coordinator and worker pods. Superset is reachable only through the internal Gateway as `superset.apps.onelite.feather`, with no public route.

**Consequences:** access to Trino depends on network policy and Trino access control, not on user authentication (Trino runs without user authentication in this design). This is acceptable only because the Trino service is unreachable except from Superset.

### 8. Resource sizing

Starting values, to be checked with a smoke-test query load in task 5.2:

| Container | Memory request | Memory limit | JVM heap |
|---|---|---|---|
| Trino coordinator | 2 Gi | 2 Gi | 1.5 Gi |
| Trino worker | 3 Gi | 3 Gi | 2.4 Gi |
| Superset web | 1 Gi | 1.5 Gi | n/a |
| Superset init (once per upgrade) | 1 Gi | 1.5 Gi | n/a |

Every container, including init containers, has an explicit memory request and limit. CPU requests are small (250m for Trino and Superset web), with no CPU limits, to avoid throttling a query engine.

**Consequences:** about 7.5 Gi of memory requests across the release. Trino's query memory limits (`query.max-memory-per-node`) are set below the JVM heap so that a single query cannot take a worker down.

### 9. GDPR and query data

- Raw payloads in `capture.raw` stay encrypted per player (crypto-shredding in the Corvus writer). Trino and Superset never receive the key material, so queries return encrypted bytes for raw payloads. The plaintext view of a packet is outside this platform.
- Pseudonymised data only: queries against `capture.events` run on the pseudonymised columns that the Corvus change defines. This change adds no decryption path.
- Query logs: Superset keeps the SQL text and the user name of each query in its metadata database. Trino keeps query text in its coordinator logs. Retention of both is an operator decision. The proposed default is 30 days for Superset query history and the coordinator log rotation of the chart.
- Access: the people allowed to sign in are the assigned users of the Superset Entra app. Removing an assignment removes access at once. Trino has no per-user access, because all queries run as `superset`; its access is limited by the network policy and access control.

## Open questions

1. **Trino Iceberg OAuth2 with Entra v2 tokens.** The Iceberg REST client must send the scope parameter to the Entra token endpoint, and Entra must accept the `.default` scope in the client credentials flow. Verify in task 2.3. If the client cannot send the scope, the fallback is a token refresh Job, which is a separate design.
2. **`s3:GetBucketLocation` for `trino-lake`.** Confirm in the smoke test whether Trino needs it for path-style access. If it does, add that one read action to the bucket policy and record the change in the code pull request.
3. **Superset role mapping for analysts.** Gamma grants no data by default. The maintainer defines the database permissions for Gamma, or a custom role with datasource access, when the first analyst needs data.
4. **Exact chart versions.** Trino chart 1.45.0 (application 484) and Superset chart 0.22.8 (application 6.1.0) are the latest at proposal time. Pin the exact versions in the code pull request, record them in Outline, and check again whether newer releases exist.
