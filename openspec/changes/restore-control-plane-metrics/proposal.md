## Why

The three control-plane nodes of `feather-core` are under memory and API pressure. kube-apiserver was OOMKilled on all three, and Flux reports `etcdserver: request timed out`. Our dashboards and alerts cannot explain this yet, because three signals are missing from Mimir:

1. **etcd is not scraped at all.** The chart's `kube-etcd` Service selects pods labelled `component=etcd`. On Talos, etcd is a system service, not a pod, so the Service has no endpoints. Mimir holds zero `etcd_disk_*` and `etcd_server_*` series, and the existing etcd alert rules (`etcd-no-leader`, `etcd-leader-changes-high`) query series that do not exist. They run with `noDataState: OK`, so they can never fire.
2. **apiserver latency is dropped.** A `metricRelabelings` drop (added in PR #582 to save series) removes every `apiserver_request*_duration_seconds_bucket`, `etcd_request_*` and `rest_client_request_*` histogram. About 1800 label combinations for non-WATCH/CONNECT verbs across the three apiservers are invisible. Only `_sum` and `_count` survive.
3. **No general PVC fill alert.** `kubelet_volume_stats_*` is in Mimir (708 series), but only the MariaDB Galera and MongoDB PVCs have alert rules.

This change restores these three signals within the series budget. The Mimir tenant runs at about 475k active series against a 600k limit, so every addition is budgeted.

## What Changes

- **etcd scrape:** `kubeEtcd` scrapes the three etcd members directly by node IP (`192.168.15.10`, `.11`, `.12`) on port 2381 over plain HTTP, without bearer token. A `metricRelabelings` keep-list stores only the etcd families needed for disk latency, leader health, and DB size. Estimate: fewer than 1.5k series.
- **apiserver latency:** the blanket drop of `apiserver_request_sli_duration_seconds_bucket` is replaced by a drop rule that keeps only the `le` values `0.1`, `0.5`, `1`, `5` and `+Inf`. The other histogram drops stay as they are. Estimate: about 9k series (1800 combinations x 5 buckets).
- **PVC alert:** one Grafana rule, "PVC almost full", fires when any PVC is above 85 % used for 15 minutes. It follows the structure of the existing rules in the `storage` group.
- **Dependency:** the etcd endpoint change depends on the companion change `expose-etcd-metrics` in the talos-cluster repository, which opens the plain-HTTP metrics listener on :2381 on the three control-plane nodes. This change merges and applies only after that listener is live on all three nodes.

**Commit type and scope:** the implementation ships as `feat(observability)`.

## Out of scope

- A Grafana dashboard for etcd or apiserver latency. It is a follow-up.
- Recording rules for apiserver histograms. Histograms cannot be aggregated before ingest in Alloy, so the bucket filter is the only lever here.
- Changes to the Talos configuration itself, which belong to `expose-etcd-metrics`.
- Changes to the `control-plane-scrape` token or RBAC.
- Retuning the existing etcd alert thresholds. Their behaviour is verified after the metrics arrive, not changed here.

## Impact

- New capability `control-plane-metrics` (see `specs/control-plane-metrics/spec.md`).
- Files changed: `foundation/observability/clusters/feather-core/kube-prometheus-stack/release.yaml` (kubeApiServer and kubeEtcd values) and `services/observability/clusters/feather-core/grafana/alerting/rules.yaml` (one rule in the `storage` group).
- Estimated series: about +10.5k in total, against roughly 125k of headroom to the 600k limit.
- Activated alerts: `etcd-no-leader` (critical) and `etcd-leader-changes-high` start evaluating real data on merge. They can fire for the first time, which is intended but must be watched during rollout.
- Failure visibility: a missing etcd target shows as `up{job="kube-etcd"} == 0` only after this change. Today the failure is silent.
