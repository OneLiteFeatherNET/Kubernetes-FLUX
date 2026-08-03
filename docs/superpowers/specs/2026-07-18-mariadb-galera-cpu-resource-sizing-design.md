# MariaDB Galera CPU Resource Sizing — Design

**Status:** Approved, ready for implementation plan.

## Goal

Raise `mariadb-galera`'s CPU request from `2` to `4` cores (per pod, ×3 replicas) to better reflect measured real peak load, correcting an earlier idle-state-only assumption. Memory (`8Gi` request/limit) and the absence of a CPU limit are both left unchanged — the data doesn't support touching either.

## Why

This is the last of a series of MariaDB performance investigations on 2026-07-18. An earlier pass (idle-state only: ~3-6% CPU usage, ~31-65 millicores) suggested the `2`-core CPU request was over-provisioned. A later 32-thread `sysbench` benchmark, measured live via Mimir/Prometheus (not estimated), disproved that framing:

- **Primary pod CPU under real concurrent write load: ~5 full cores** — 2.5× the current `2`-core request.
- Galera-level metrics stayed clean throughout (`wsrep_flow_control_paused` ≈ 0, `wsrep_local_cert_failures`/`wsrep_local_bf_aborts` = 0 on all 3 pods) — confirming CPU, not Galera certification or flow control, is the resource that actually moves under load.
- Memory during the same window: primary peaked at ~2.79-3.0GiB, barely above idle (~2.5GiB) and at ~37% of the existing `8Gi` request/limit — no case for change.
- Node capacity: each of the 3 worker nodes hosting these pods (`fr01-wrk-xl-01/02/04`) has 32 allocatable cores. Even the measured 5-core peak was only ~15.6% of one node's capacity, which is why bursting worked cleanly with no throttling despite no CPU limit being set today.

**Why `4`, not `5` (the measured peak) or leaving it at `2`:** a resource *request* is a scheduling guarantee/floor, not a hard cap — bursting above the request into a node's spare capacity is normal and already worked without issue in the benchmark. `4` cores covers ~80% of the observed peak as a guaranteed reservation while still leaving burst room for anything above it, without over-reserving on 32-core nodes where even `4` cores/pod is a modest 12.5% of one node's capacity.

**Why no CPU limit:** explicitly considered and rejected (a separate option in the underlying decision) — bursting without a limit already worked cleanly in the benchmark, and `priorityClassName: system-node-critical` already gives these pods scheduling priority against less-critical workloads on the same nodes, mitigating the main risk (noisy-neighbor CPU starvation of other pods) that a limit would otherwise guard against.

## Approach

Change `spec.resources.requests.cpu` from `"2"` to `"4"` in `infrastructure/clusters/feather-core/configs/mariadb-galera/mariadb.yaml`. No other field in the `resources` block changes — `requests.memory: 8Gi`, `limits.memory: 8Gi` stay as-is, and no `limits.cpu` is added.

## Rollout & Verification

Same pattern used repeatedly today on this exact resource:
1. Edit, validate (`kubectl kustomize ... | grep`, `./scripts/validate.sh`), commit, push to `main`.
2. `flux reconcile source git flux-system` + `flux reconcile kustomization configs --with-source` (once).
3. `mariadb-operator`'s `ReplicasFirstPrimaryLast` strategy rolls the 3 pods one at a time, primary last (~100s/pod via Galera IST).
4. Verify on all 3 pods: `kubectl describe pod` shows `cpu: 4` under Requests; `MariaDB` CR conditions all `True`; `wsrep_cluster_size=3` / `Synced` on all 3.
5. Confirm no scheduling problem resulted from the higher reservation: all 3 pods land back on their expected nodes (`fr01-wrk-xl-01/02/04`) without going `Pending`.

## Out of Scope

- Memory sizing — explicitly not changed, no evidence supports it.
- Adding a CPU limit — explicitly considered and rejected, see "Why no CPU limit" above.
- MaxScale resource sizing — already ruled out in an earlier investigation today (not CPU-bound during benchmarking).
- The other two still-open broader optimization areas from earlier today (storage capacity, backup retention) and monitoring/alerting — untouched, separate items.
