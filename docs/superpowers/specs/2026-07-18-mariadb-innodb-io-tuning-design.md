# MariaDB Galera InnoDB I/O Tuning (Ceph RBD) — Design

**Status:** Approved, ready for implementation plan.

## Goal

Correct two InnoDB I/O settings on `mariadb-galera` that are still at their conservative, spinning-disk-era defaults despite the cluster running on Ceph RBD (network block storage). Surfaced during a benchmark comparison session (2026-07-18) that established baseline throughput of ~390 TPS direct-to-service / ~244 TPS via MaxScale (the real production path) with `sysbench oltp_read_write`, 4 threads, 100k rows — both already explained as expected Galera + proxy overhead relative to a ~860 TPS standalone-MySQL reference point, not a red flag. This change targets an incremental improvement on top of that baseline, not a fix for a problem.

## Why

Checked live on the cluster (`mariadb-galera-2`):

- `innodb_flush_neighbors = 1` — batches flushing of physically-adjacent dirty pages to reduce disk-seek cost. This is a spinning-disk optimization with no benefit on Ceph RBD (a network block device with no seek penalty in the traditional sense) and can waste I/O by flushing pages that didn't need writing yet. Universally recommended as `0` for SSD/network-block storage.
- `innodb_io_capacity = 200` — MariaDB's compiled-in default, calibrated for a single spinning disk's sustained IOPS. It throttles how aggressively InnoDB's background threads flush dirty pages and run purge, which can become a self-inflicted bottleneck under write load on faster storage. `innodb_io_capacity_max = 2000` is already MariaDB's own default (not currently overridden anywhere in this repo's `myCnf`) and needs no change — the common "half of max" starting point for SSD-class backends lands at `1000`.

Ruled out during this same investigation: `sync_binlog` (irrelevant — `log_bin=OFF`, no binary log is written) and bumping MaxScale's CPU/memory (MaxScale used only 7-11m of its 100m CPU request during the benchmark — not resource-starved, so more resources wouldn't change throughput).

**Basis for the exact `innodb_io_capacity` value:** established general SSD/RBD guidance, not a storage-specific `fio` IOPS measurement — a deliberate choice (confirmed with the user) to keep this an incremental, low-effort tuning pass rather than a deeper storage-benchmarking exercise.

## Approach

Add two lines to the existing `myCnf` block in `infrastructure/clusters/feather-core/configs/mariadb-galera/mariadb.yaml` (same file, same block already holding the InnoDB buffer-pool and flush settings from the "Constraint: I/O path" section):

```
innodb_flush_neighbors=0
innodb_io_capacity=1000
```

No other file changes. `innodb_io_capacity_max` is left at its existing default (2000) — not added explicitly, since 1000 already sits comfortably under it.

## Rollout & Verification

Same pattern used repeatedly today on this exact resource:
1. Edit `myCnf`, validate (`kubectl kustomize ... | grep`, `./scripts/validate.sh`), commit, push to `main`.
2. `flux reconcile source git flux-system` + `flux reconcile kustomization configs --with-source` (once).
3. `mariadb-operator`'s `ReplicasFirstPrimaryLast` strategy rolls the 3 pods one at a time, primary last (~100s/pod via Galera IST, already observed multiple times today).
4. Verify on all 3 pods: `SHOW VARIABLES LIKE 'innodb_flush_neighbors'` = `0`, `SHOW VARIABLES LIKE 'innodb_io_capacity'` = `1000`; `MariaDB` CR conditions all `True`; `wsrep_cluster_size=3` / `Synced` on all 3.
5. Optional, informative but not a pass/fail gate: re-run the same `sysbench oltp_read_write` benchmark (4 threads, 90s, 100k rows, via MaxScale with a fresh temporary `benchmark` user, same cleanup discipline as the earlier run — dedicated `sbtest` DB and user created and dropped within the task) to see the actual before/after TPS/latency delta. Framed as informative because the expected effect here is incremental and may not be clearly visible in a single 90s run at light concurrency — absence of a dramatic jump would not mean the change was wrong.

## Out of Scope

- `fio`-based storage IOPS measurement to precisely calibrate `innodb_io_capacity` — deliberately deferred, see "Basis for the exact value" above.
- `innodb_buffer_pool_instances`, `innodb_read_io_threads`/`innodb_write_io_threads`/`innodb_purge_threads` tuning — checked during the same investigation (currently 4/4/4, unset buffer_pool_instances), no evidence found suggesting these are currently limiting; not part of this change.
- MaxScale resource sizing — explicitly ruled out, not resource-starved.
- The three other broader optimization areas from today's earlier review (resource request/limit rightsizing, backup retention, monitoring/alerting) — still deferred to their own cycles.
