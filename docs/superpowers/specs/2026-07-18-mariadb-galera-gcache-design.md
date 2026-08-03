# MariaDB Galera `gcache.size` Increase — Design

**Status:** Approved, ready for implementation plan.

## Goal

Proactively increase the Galera replication cache (`gcache`) on `mariadb-galera` from its current 128M default to 2G, extending the safe window during which a node that goes offline can rejoin via a fast Incremental State Transfer (IST) instead of falling back to a slow full State Snapshot Transfer (SST, a complete ~30GB data copy).

## Why

Not a response to an incident — proactive tuning, part of a broader MariaDB cluster performance review (2026-07-18). Measured live on the cluster: average Galera replication throughput is ~49.5 KB/s (`wsrep_replicated_bytes` / `Uptime` sampled on `mariadb-galera-1`). At the current 128M `gcache.size`, that throughput fills the cache — and therefore exhausts the IST-eligible history window — in **~44 minutes**. Any node offline longer than that (a longer maintenance drain, a stuck pod, a longer network partition) would force a full SST on rejoin, which is dramatically slower and more I/O-intensive than the ~100-second ISTs observed repeatedly during today's rolling restarts.

Storage headroom supports this comfortably: after today's separate `slow_query_log` fix, all 3 nodes sit at 61% disk usage (~20GB free each) on their 50Gi PVCs. A 2G `gcache` costs ~1.9GB of that per node.

## Approach

Set `spec.galera.providerOptions: {gcache.size: "2G"}` on the `mariadb-galera` `MariaDB` custom resource (`infrastructure/clusters/feather-core/configs/mariadb-galera/mariadb.yaml`). This is a dedicated `map[string]string` field on the CRD (confirmed via `kubectl explain mariadb.spec.galera.providerOptions`) that `mariadb-operator` merges into the full `wsrep_provider_options` string it already generates and manages (which includes TLS, replication protocol, and other settings not otherwise exposed on the CRD). Setting `gcache.size` via `myCnf` instead was considered and rejected: a `wsrep_provider_options=...` line in `myCnf` would *replace* the operator's generated string wholesale rather than merge into it, silently dropping unrelated settings (e.g. `socket.ssl = YES`).

**Sizing chosen: 2G**, selected from three options presented (1G ≈ 6h safety window, 2G ≈ 12h, 4G ≈ 24h) as the balance between comfortably covering a normal maintenance window and not over-committing disk headroom needed for actual data growth.

## Rollout & Verification

Same pattern already exercised multiple times today on this cluster:
1. Edit `mariadb.yaml`, validate (`kubectl kustomize ... | grep`, `./scripts/validate.sh`), commit, push to `main`.
2. `flux reconcile source git flux-system` + `flux reconcile kustomization configs --with-source` (once, not looped).
3. `mariadb-operator`'s default `ReplicasFirstPrimaryLast` update strategy rolls the 3 pods one at a time, primary last — no extra tooling needed, this is the built-in canary behavior already observed to complete in ~100s/pod via IST.
4. Post-rollout verification on all 3 pods:
   - `SHOW VARIABLES LIKE 'wsrep_provider_options'` contains `gcache.size = 2G`.
   - `wsrep_cluster_size = 3`, `wsrep_local_state_comment = Synced` on all 3.
   - `MariaDB` CR conditions all `True` (`Ready`, `GaleraReady`, etc.).
   - Disk usage (`df -h /var/lib/mysql`) increased by roughly the expected ~1.9GB per node, still comfortably under capacity.

## Out of Scope

- `replicaThreads`/`wsrep_slave_threads` tuning — surfaced during the same review as a lower-priority, not-currently-evidenced optimization (no sign of apply-thread contention at current write volume). Not part of this change; a separate decision if/when write volume grows.
- The other three optimization areas identified in today's broader review (resource request/limit rightsizing, backup retention strategy, monitoring/alerting) — explicitly deferred to their own separate design/plan cycles per the decomposition agreed with the user.
