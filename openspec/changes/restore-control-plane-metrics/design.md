## Context

The HelmRelease `kube-prometheus-stack` (chart 93.0.0, pinned in `foundation/sources/clusters/feather-core/sources/prometheus-stack.yaml`) runs with `prometheus.enabled: false`. Scraping is done by `alloy-metrics`, which reads the ServiceMonitors that the chart renders. The control-plane ServiceMonitors authenticate with the `control-plane-scrape-token` Secret.

Verified against the chart (`helm template` of 93.0.0 with the planned values):

- Default `kubeEtcd.service` renders `clusterIP: None`, port and targetPort 2381, port name `http-metrics`, and `selector: component: etcd`. Without `endpoints` this selector matches no pods on Talos.
- Setting `kubeEtcd.endpoints` removes the selector and renders a manual `Endpoints` object in `kube-system` with the listed IPs.
- `kubeEtcd.serviceMonitor.scheme` (`http` by default) and `kubeEtcd.serviceMonitor.metricRelabelings` are the keys used for scheme and keep-list.
- `kubeEtcd.serviceMonitor.authorization: null` renders a ServiceMonitor without an authorization block. The chart fails to render if an enabled control-plane ServiceMonitor has no `authorization` key at all while `prometheus.enabled` is false, so the explicit `null` is required, not optional.

Constraints:

- Series budget: tenant at about 475k of 600k. Every addition needs a number.
- The cluster reconciles from `main`. Scraping a target that does not yet listen on :2381 would only produce `up == 0`, but enabling it before the Talos change is live gives no benefit and creates noise, so the merge is gated.
- Alloy ≥1.20 drops ServiceMonitors that use `bearerTokenFile` (see the memory note on alloy-servicemonitor-file-access). The etcd listener needs no token, so the Bearer block is removed as well.

## Decisions

### 1. How etcd is scraped (MADR)

**Context and problem:** etcd runs as a Talos system service on three control-plane nodes. It has no pods, so the chart's default pod-selector Service matches nothing.

**Decision drivers:**

- Scrape etcd without changing how Talos runs etcd.
- Keep the scrape inside the existing ServiceMonitor path that Alloy already reads.
- No credentials for a metrics endpoint that is only reachable on the node network.

**Considered options:**

1. **Node IP endpoints in `kubeEtcd.endpoints` (chosen).** The chart builds the Service and Endpoints; the ServiceMonitor stays as the chart renders it. Works with the Talos listener on :2381.
2. **PodMonitor for etcd.** Rejected: there are no etcd pods on Talos, so a PodMonitor has nothing to select.
3. **Static scrape config in Alloy.** Rejected: it moves a chart-managed target out of the chart and out of Flux review, and it bypasses the keep-list that the chart already supports.

**Consequences:** the three IPs are hard-coded in the release values. A control-plane node change requires a values change in the same PR as the Talos change.

### 2. apiserver histogram buckets (MADR)

**Context and problem:** `apiserver_request_sli_duration_seconds_bucket` (and the other request histograms) were dropped entirely to save about 127k series. The SLI histogram is what the apiserver latency analysis needs. Its full bucket set, however, is about 66k series.

**Decision drivers:**

- Keep a latency signal that can show p90 and p99 approximately.
- Stay within the series budget of about 10k added series.
- Do not change the apiserver itself.

**Considered options:**

1. **Keep full buckets.** Rejected: about 66k series, roughly 11 % of the tenant limit, which is the same failure that caused the drop in PR #582.
2. **Keep a subset of `le` values (chosen).** Keep `le` in {0.1, 0.5, 1, 5, +Inf} for the SLI histogram only. About 1800 x 5 = 9k series. Gives coarse but usable quantiles for the 0.1 s, 0.5 s, 1 s and 5 s objectives.
3. **Recording rules in Alloy.** Rejected: histograms cannot be aggregated before ingest in Alloy's ServiceMonitor path, so the cardinality is already paid when the samples arrive.

**Consequences:** quantiles are approximate and bounded by the chosen bucket edges. `apiserver_request_duration_seconds_bucket` and `apiserver_request_slo_duration_seconds_bucket` stay dropped; the SLI histogram carries the same signal for the verbs we track.

The drop rule matches on `__name__;le`, so it removes exactly the buckets outside the keep set and leaves `_sum` and `_count` untouched.

### 3. PVC alert scope (open question)

**Context:** the new rule covers all PVCs. The MariaDB Galera PVCs already have dedicated rules at 80 % (warning) and 90 % (critical), and the MongoDB PVCs at 80 % for 15 minutes. Those PVCs would produce a second notification at 85 %.

**Current state (2026-11-27):** `count(kubelet_volume_stats_capacity_bytes{job="kubelet"})` = 69 PVC series, and none is above 85 %, so the rule would not fire on merge.

**Decision for this change:** the expression stays as specified, without a namespace exclusion. Whether to exclude `mariadb-galera` and `mongodb` is a follow-up decision, to be taken once the first notification volume is observed.

## Expressions

- etcd keep-list: `etcd_disk_wal_fsync_duration_seconds_(bucket|sum|count)`, `etcd_disk_backend_commit_duration_seconds_(bucket|sum|count)`, `etcd_server_(leader_changes_seen_total|has_leader|is_leader|proposals_.*|slow_.*)`, `etcd_mvcc_db_total_size(_in_use)?_in_bytes`, `etcd_network_peer_round_trip_time_seconds_(bucket|sum|count)`, `etcd_server_heartbeat_send_failures_total`, `process_resident_memory_bytes`, `up`. Estimated fewer than 1.5k series.
- apiserver SLI drop: `__name__=apiserver_request_sli_duration_seconds_bucket` and `le` not in {0.1, 0.5, 1, 5, +Inf}.
- PVC rule: `max by (namespace, persistentvolumeclaim) (kubelet_volume_stats_used_bytes{job="kubelet"} / kubelet_volume_stats_capacity_bytes{job="kubelet"}) > 0.85`, `for: 15m`, `severity: warning`.
