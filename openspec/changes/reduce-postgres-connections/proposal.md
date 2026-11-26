## Why

The shared CloudNativePG cluster `feather-core-cluster-pg` has `max_connections` 200 and was at 151 client backends on 2026-11-26. The Grafana alert "CNPG Postgres connection saturation high" fires at 80% (160 backends) and resolves below 70%. At that level a restart storm or one more application pool can exhaust the last slots, and then new connections are refused for every application on the cluster.

Most of the load comes from clients that do not use the transaction pooler `feather-core-cluster-pg-pooler-rw-tx`, which was added on 2026-11-08 to multiplex idle pools onto few backends:

- The session pooler `feather-core-cluster-pg-pooler-rw` holds about 49 backends. Session mode maps every client connection to one backend, so moving a client there saves nothing. Grafana (20) and Dependency-Track (11) are the largest users.
- The direct `-rw` service holds about 72 backends. Airflow (14), cygnus_stats (12), titan (8), corvus (8) and metabase (7) are the largest users.
- The transaction pooler holds about 30 backends for sentry, otis, vulpes, sturnus, blackhole, coloeus and the `_dev` variants.

Many applications stayed off the transaction pooler for reasons that are fixable per application. Some migrations and runtimes take session-level advisory locks, which do not survive transaction pooling. Some send startup parameters that PgBouncer rejects. Others were simply never moved. This change moves the applications where that is safe and caps the pools of the ones that must stay.

**Success measure:** with both waves live, the cluster uses at most 125 client backends under normal load (from 151), so the 80% alert stops being the normal state.

**Commit type and scope:** the implementation ships as `fix(databases)`. All changes tune how applications connect to the shared PostgreSQL cluster, one capability.

## What Changes

Each application moves to the transaction pooler only when its runtime and migrations are safe in transaction mode. Applications that are not safe get a bounded pool instead. The per-application rules are in `design.md`.

**Wave 1 (no secret changes), code pull request title `fix(databases): route penpot and vikunja through the transaction pooler`:**

- **Grafana** (`services/observability/clusters/feather-core/grafana/release.yaml`): stays on the session pooler, because its migrator takes a session advisory lock. The pool is capped instead with `GF_DATABASE_MAX_OPEN_CONN: "5"` and `GF_DATABASE_MAX_IDLE_CONN: "2"`. With two replicas this bounds Grafana at 10 backends (from about 20). Saves about 10.
- **Penpot** (`services/development/clusters/feather-core/penpot/release.yaml`, `postgresql.host`): from `feather-core-cluster-pg-rw` to `feather-core-cluster-pg-pooler-rw-tx`. Saves about 4.
- **Vikunja** (`services/collaboration/clusters/feather-core/vikunja/release.yaml`, `database.host`): from `feather-core-cluster-pg-rw` to `feather-core-cluster-pg-pooler-rw-tx`. Saves about 3.

**Wave 2 (host and connection secrets, after wave 1 has run 24 hours), code pull request title `fix(databases): route metabase, outline and airflow through the transaction pooler`:**

- **Metabase** (`services/collaboration/clusters/feather-core/metabase/metabase-app-db.sops.env`): the application database host moves to the transaction pooler. The host comes from the secret, so the change is an encrypted edit. Saves about 6.
- **Outline** (`services/collaboration/clusters/feather-core/outline/outline.sops.env`): the database host moves to the transaction pooler. The host also comes from a secret, which is why Outline is in wave 2 and not wave 1. Saves about 3.
- **Airflow** (`foundation/data/clusters/feather-core/database-secrets/airflow-db.sops.env`): the runtime connection (scheduler, api-server, dag-processor, triggerer) moves to the transaction pooler. `airflow db migrate` keeps the direct `-rw` connection, because it takes a global `pg_advisory_lock`. This needs a chart mechanism that gives the migration job a different connection. If the chart has none, Airflow is not moved and gets bounded pools (`AIRFLOW__DATABASE__SQL_ALCHEMY_POOL_SIZE` and `MAX_OVERFLOW`). Saves about 6 to 10.

**Not changed, on purpose:**

- n8n: scaled to zero since 2026-11-21. Its `statement_timeout` startup parameter is rejected by PgBouncer.
- Harbor and Dependency-Track: session advisory locks in their migrations. They stay on the session pooler.
- Corvus: session advisory locks in ingest and scoring. It stays on `-rw` and keeps its role cap.
- Polaris and Superset: at most 3 connections each, not worth the risk.
- cygnus_stats, titan and skygridng: their connections are configured in CloudNet on mc01, outside this repository. cygnus_stats holds 12 idle connections, which is a separate follow-up.
- PgBouncer `ignore_startup_parameters` is not used. The pooler is shared, so it would silently drop `statement_timeout` for every client, including sentry.

**Expected result:** about 30 fewer backends (151 to about 120), if all planned moves land.

## Rollout and rollback

Each wave is its own code pull request under the same type and scope, so a failing application is isolated and rollback is one revert. Verification after each wave:

- PgBouncer logs of the transaction pooler show no `unsupported startup parameter` and no protocol errors.
- Each application's pods are Ready and its UI or health endpoint works.
- The backend count per user and path is re-measured.

Rollback is a revert of the wave's pull request. For a secret-sourced host the change also needs `kubectl rollout restart`, because secret names are stable (`disableNameSuffixHash: true`) and a content change does not roll the pods.

## Rule for new products

New products, for example the upcoming Pulsar, follow the same rule. The runtime connects through `feather-core-cluster-pg-pooler-rw-tx`. Migrations connect to `-rw` and use transaction-scoped locks (`pg_advisory_xact_lock`) so that they can run through the pooler later.

## Out of scope

- Raising `max_connections`. It costs memory per backend and hides the problem.
- The mc01 CloudNet clients (cygnus_stats, titan, skygridng). They need a change in CloudNet config.
- Changing the alert threshold or the Grafana rule. The alert is correct at 80% once the cluster is below it.

## Impact

- New capability `postgres-connection-budget` (see `specs/postgres-connection-budget/spec.md`).
- Files: the release and overlay files named above, and three secret files, edited with `sops` in place. No secret content is printed in review.
- Layers: `services-observability`, `services-development`, `services-collaboration`, `foundation-data`. No layer is renamed or split. Secret-only edits take effect on the next reconcile, plus the rollout restart described above.
- Runtime impact: connection counts drop, latency may rise slightly for transaction-pooled clients, and each wave can be reverted on its own.

## Relation to other changes

- `reduce-alert-noise` changes the "CNPG Postgres connection saturation high" rule. Its hysteresis is independent of this change and the two can ship in either order.
- `right-size-core-operators` reduces restarts of core operators, which in turn reduces connection churn. It does not depend on this change.
