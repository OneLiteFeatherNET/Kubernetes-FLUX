## Context

The shared CNPG cluster has three ways in: the direct `-rw` service, the session pooler `pooler-rw` (`pool_mode: session`, one backend per client connection) and the transaction pooler `pooler-rw-tx` (`pool_mode: transaction`, `max_prepared_statements: 200`, `default_pool_size: 20` per user and database and pod). Only the transaction pooler multiplexes idle clients onto few backends, so only it reduces the count. Moving a client to the session pooler changes nothing.

Transaction pooling breaks three things that applications rely on: session-level state (advisory locks, `SET` that must persist, `LISTEN`), startup parameters that PgBouncer rejects, and long-lived prepared statements beyond the `max_prepared_statements` limit. Each application is checked against these before it moves.

## Decision drivers

- Move only what is safe; a failed move must not break an application's migrations or runtime.
- Each wave is independently revertible.
- Secret changes are slower to verify and to roll back than values changes, so they come second.
- The cluster must stay under 80% without raising `max_connections`.

## Decisions

### 1. Per-application routing criteria

**Decision:** an application connects through `feather-core-cluster-pg-pooler-rw-tx` only when all of these hold:

- its migrations and runtime take no session-level advisory locks (`pg_advisory_lock`, not `pg_advisory_xact_lock`);
- it sends no startup parameter that PgBouncer rejects (for example `statement_timeout` in the startup packet);
- it uses no `LISTEN`/`NOTIFY`.

Otherwise it stays on `-rw` or the session pooler and gets a bounded pool.

**Options considered:**

- **Move everything to the transaction pooler.** Breaks Grafana, Harbor, Dependency-Track, n8n and Corvus, which take session advisory locks or send rejected startup parameters.
- **Keep everything where it is and only cap pools.** Safe, but leaves about 30 backends on the table where the move is safe.
- **Criteria per application, wave by wave (chosen).** Moves what is verified and caps the rest.

**Consequences:** each application needs a short note in its release file explaining why it is or is not on the transaction pooler. This is kept to one or two lines, with the full reasoning in the pull request description.

### 2. Grafana: pool cap instead of transaction pooler

**Decision:** Grafana stays on the session pooler and gets `GF_DATABASE_MAX_OPEN_CONN: "5"` and `GF_DATABASE_MAX_IDLE_CONN: "2"`.

**Rationale:** the Grafana migrator takes a session advisory lock (`migration_locking`, `pkg/services/sqlstore/migrator/migrator.go`), which transaction pooling does not support. With two replicas the cap bounds Grafana at 10 backends, from about 20 today.

**Rejected alternative:** a separate direct connection for migrations only. Grafana does not expose a separate migration connection, so this would need a patch, and the cap alone already gives most of the saving.

**Consequences:** Grafana's peak falls from about 20 to 10 backends. Under a burst of dashboard queries, a request may wait for a pooled connection. The pool is sized for the current dashboard load.

### 3. Airflow: migration split vs. bounded pools

**Decision:** Airflow moves its runtime connection (scheduler, api-server, dag-processor, triggerer) to the transaction pooler, and `airflow db migrate` keeps the direct `-rw` connection. This is possible only if the Airflow chart (1.22.0) can give the migrate job a different connection string (for example `migrateDatabaseJob.env` overriding `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN`, or a second secret). Airflow documents transaction-mode PgBouncer support, and its production guide uses `pool_mode = transaction`.

**Fallback:** if the chart has no such mechanism, Airflow is not moved. It gets `AIRFLOW__DATABASE__SQL_ALCHEMY_POOL_SIZE` and `AIRFLOW__DATABASE__SQL_ALCHEMY_MAX_OVERFLOW` instead, sized so the role cap of 20 in `cluster.yaml` is not reached.

**Rationale:** `airflow db migrate` takes a global lock through `pg_advisory_lock` (`airflow.utils.db.create_global_lock`), a session lock. The migrate job is the only part that needs it.

**Consequences:** the split adds a second connection secret, or a chart override that must be re-checked on every chart bump (see the Renovate note in the Kubernetes-FLUX CLAUDE.md).

### 4. Two waves vs. one

**Decision:** two waves. Wave 1 changes no secrets: Grafana, Penpot and Vikunja. Wave 2 changes host and connection secrets: Metabase, Outline and Airflow, starting after wave 1 has run for 24 hours.

**Rejected alternative:** one pull request. A failing application would block the others and rollback would be a larger revert.

**Consequences:** the cluster stays at the higher count for longer. Each wave is one revert.

### 5. No PgBouncer `ignore_startup_parameters`

**Decision:** do not add `ignore_startup_parameters` to the transaction pooler.

**Rationale:** the pooler is shared. Ignoring `statement_timeout` would silently drop the timeout for every client, including sentry, which does not need it. Fixing the client is safer than changing the shared pooler.

**Consequences:** n8n stays off the transaction pooler while it sends `statement_timeout` as a startup parameter.

## Risks / Trade-offs

- **An application relies on session state it does not declare.** Mitigation: the PgBouncer log check and the health endpoint check after each wave; revert on the first error.
- **Secret-sourced hosts do not roll pods.** Mitigation: `kubectl rollout restart` after each secret change, documented in the rollback steps.
- **Airflow chart has no migrate-only connection.** Mitigation: the bounded-pool fallback in decision 3.
- **Grafana cap too small under load.** Mitigation: raise `GF_DATABASE_MAX_OPEN_CONN` in a values commit; the session pooler still bounds the total.

## Migration Plan

1. Merge this proposal pull request.
2. Wave 1 code pull request, title `fix(databases): route penpot and vikunja through the transaction pooler`. The Grafana pool cap ships in the same pull request. Verify for 24 hours.
3. Wave 2 code pull request, title `fix(databases): route metabase, outline and airflow through the transaction pooler`. Outline moved from wave 1 to wave 2 because its host is in an encrypted secret, not in plain values. Re-measure the backends.

**Rollback:** revert the wave's pull request. For secret-sourced hosts, restart the affected Deployments.

## Open Questions

- **Does the Airflow chart 1.22.0 accept a separate connection for `migrateDatabaseJob`?** Confirm in the chart before wave 2. Fallback in decision 3.
- **Is Outline's `outline.sops.env` host the only place the database host is set?** Confirm when editing; Outline is moved in wave 2 because its host is in a secret.
