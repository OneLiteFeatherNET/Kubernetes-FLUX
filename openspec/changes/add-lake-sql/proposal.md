## Why

The Apache Iceberg lake (catalog `corvus`, namespace `capture`, see the merged change `add-data-lake`) is the place where Corvus stores the Minecraft network packets it captures. Once Corvus writes to it, the data can only be reached through the Polaris REST API or from Spark and Airflow jobs. Staff who need to look at the data (to check that captures arrive, to inspect a suspected cheater's packets, or to verify a retention run) have no way to run SQL against it. Today they would have to write a Spark job or a Python script for every question.

This change adds an interactive SQL path on top of the lake: Trino as the query engine, Superset as the browser interface for staff. Both are read-only on catalog `corvus`, reachable only by assigned staff, and do not change the packet path.

## What Changes

Code ships as `feat(data)`. Both components belong to one capability (interactive SQL on the lake), so the change has one type. It adds no change to the lake platform itself beyond one principal role.

- **Trino** (official chart `trino` from `https://trinodb.github.io/charts`, pinned to an exact chart and application version during implementation; latest at proposal time: chart 1.45.0, application 484), in namespace `data`, in layer `foundation-data`:
  - one coordinator and one worker, with modest memory requests and limits on every container;
  - an Iceberg connector catalog `corvus` that uses the Polaris REST catalog `http://polaris.data.svc.cluster.local:8181/api/catalog`, warehouse `corvus`, and OAuth2 client credentials against Entra ID as client app "Polaris Trino" (appId `8832de30-7197-40ed-a2bc-68c88bc770cb`, scope `api://9e2548f4-5e10-440d-9444-cad813a20c87/.default`, Entra app role `lake-sql`);
  - S3 access with the keys of a dedicated read-only RGW user `trino-lake` (CephObjectStoreUser in `rook-fr01`), not with the keys of the Polaris user. A bucket policy on `polaris-corvus` allows that user only `s3:GetObject` and `s3:ListBucket`. The mechanism is the bucket-policy pattern of the parallel change `feat(corvus): grant corvus access to the iceberg lake` (Flux PR in progress). Path-style, against the internal RGW endpoint. Vended credentials are off, because RGW has no STS;
  - read-only in four places: Polaris grants only read rights on catalog `corvus`, Trino's access control denies writes to it, and RGW refuses object writes made with the `trino-lake` keys;
  - `ClusterIP` only, with a NetworkPolicy that admits connections from Superset and from its own coordinator and worker pods.
- **Polaris** (existing release from `add-data-lake`): a new catalog principal role `lake_sql` with read-only grants on catalog `corvus` (`TABLE_LIST`, `NAMESPACE_LIST`, `TABLE_READ_PROPERTIES`, `TABLE_READ_DATA`), a principal named by the client app id of "Polaris Trino", and the OIDC role mapping `lake-sql` to `PRINCIPAL_ROLE:lake_sql`. Both are added to the existing setup Job and to the OIDC mapping in the Polaris release. No other principal, grant or catalog changes.
- **Superset** (official chart `superset` from `https://apache.github.io/superset`, pinned to an exact version during implementation; latest at proposal time: chart 0.22.8, application 6.1.0), in namespace `data`, in layer `foundation-data`:
  - metadata database on the existing CloudNativePG cluster `feather-core-cluster-pg`: a managed role `superset` and a Database `superset`, credentials in SOPS-encrypted Secrets, following the `airflow` and `polaris` pattern. The chart's bundled PostgreSQL is disabled;
  - no shared cache: Superset runs without Redis (decision in design, the chart's bundled Redis is disabled as well);
  - login through Entra ID (`AUTH_OAUTH`, provider `azure`) with the app "Superset" (appId `b592b27d-d127-4de6-9e4d-d3e252b46852`, redirect `https://superset.apps.onelite.feather/oauth-authorized/azure`). Only users assigned to the Entra enterprise app can sign in. The Entra app role `superset-admin` maps to Superset's `Admin` role; every other assigned user gets `Gamma`, which has no data access until one is granted;
  - a preconfigured database connection to Trino catalog `corvus`;
  - `SECRET_KEY` in a SOPS-encrypted Secret;
  - reachable as `https://superset.apps.onelite.feather` through the internal Gateway and an HTTPRoute, the same pattern as `corvus.apps.onelite.feather`. No public route.
- Entra ID registrations are done: the app role `lake-sql` is assigned to "Polaris Trino", and the Superset app has the app role `superset-admin` (assigned to the maintainer) and `appRoleAssignmentRequired` set. The client secrets for Trino and Superset are created during implementation and stored as SOPS Secrets.

**Commit type and scope:** `feat(data)` for the code. The proposal's commit is `docs(openspec): propose add-lake-sql`.

## Out of scope

- Public access to Trino or Superset, and any Ingress or public hostname.
- Write access through SQL: no `INSERT`, `UPDATE`, `DELETE`, `CREATE` or `DROP` on catalog `corvus`, for any user.
- Dashboard content, saved queries and charts. Superset starts empty except for the database connection.
- Login to Airflow through Entra ID (already a follow-up of `add-data-lake`).
- Per-user identity inside Trino (impersonation of the logged-in Superset user). All Superset queries run as one Trino user (design, decision 5).
- Changing the packet path: the plugin, NATS, the Corvus ingest writer and the per-player encryption stay as they are. Trino and Superset never receive the key material for the encryption, so they only ever see the encrypted raw payloads.

## Impact

- New capability `lake-sql` (see `specs/lake-sql/spec.md`).
- New Helm releases `trino` and `superset` in namespace `data` under `foundation/data/`, plus their secrets, NetworkPolicies and HTTPRoute. The layer `foundation-data` gains these entries and one new dependency, `foundation-networking`, because the HTTPRoute attaches to the internal Gateway in that layer.
- Existing objects touched, all additive or in the same files as the merged change: the CloudNativePG cluster `feather-core-cluster-pg` gets one more managed role and one more Database (`superset`); the RGW store `rook-fr01` gets one more user (`trino-lake`), and the bucket `polaris-corvus` gets a bucket policy that grants that user read access only; the Polaris setup Job gets one more principal and grant set and its name is bumped (Jobs are immutable); the Polaris OIDC mapping gets one more role. No existing role, bucket, grant or principal changes.
- Runtime impact: Trino coordinator and worker, and the Superset web server, each with memory requests and limits. The Superset init Job also runs once per upgrade. The CNPG role `superset` gets a connection limit against `max_connections: 200` (design, decision 6).
- Layer `foundation-data` must reconcile Ready at the same revision as its dependencies, as with every layer.
