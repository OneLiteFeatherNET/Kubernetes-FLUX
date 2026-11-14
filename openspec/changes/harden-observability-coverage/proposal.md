## Why

Three gaps make the cluster's own health hard to see before it fails:

1. The log collector `alloy-logs` runs on only 4 of the 10 nodes, because its DaemonSet lacks the tolerations for the control-plane and storage taints. Logs from the other six nodes are not collected.
2. Talos can send its own system logs (`machine.logging`, `json_lines`) to an endpoint, but there is no intake for them. Node-level failures appear in no dashboard.
3. Certificate expiry and etcd health are not alerted. The API server client certificate, kubelet serving certificates and cert-manager certificates can expire without a warning, and an etcd leader loss would only show up as failing requests.

Each gap is closed without touching a running workload. The collector change adds pods; the new intake is a separate Alloy instance; the alerts only read metrics.

## What Changes

- **(a) Collector coverage.** `alloy-logs` (namespace `grafana`, overlay `services/observability/clusters/feather-core/alloy-logs/release.yaml`) gets tolerations for `node-role.kubernetes.io/control-plane:NoSchedule` and `node-role.feather/storage:NoSchedule`, so the DaemonSet runs on all 10 nodes.
- **(b) Talos log intake.** A separate Alloy HelmRelease `alloy-talos-logs` in namespace `grafana`, with its own Service on a fixed Cilium LB-IPAM address from pool `lb-fr01`, accepts Talos `machine.logging` `json_lines` and forwards them to Loki with `cluster` and `node` labels. The Talos side (the `machine.logging` destination) is a separate change in the Talos repository.
- **(c) Alerts.** Grafana rules in folder `Core Services` for:
  - Kubernetes API server client certificate expiry under 14 days.
  - Kubelet serving certificate expiry under 14 days.
  - cert-manager `Certificate` expiry under 14 days.
  - etcd: no leader, or more than three leader changes in 15 minutes.

**Commit type and scope:** the implementation ships as `feat(observability)`. The collector tolerations and the new intake are new capability; the alerts are part of the same observability capability.

**Dependency (outside this repository):** the Talos `machine.logging` destination pointing at the reserved LB-IPAM address is a Talos repository change. Until it is live the intake receives nothing, and no other part of the system is affected.

## Out of scope

- Changing the existing OTLP receiver `alloy-receiver`. Its Deployment is not restarted by this change (decision 2 in design).
- Dashboards for the new signals (follow-up).
- Scraping the Talos etcd metrics endpoint if it needs a Talos-side change (open question).

## Impact

- New capability `observability-coverage` (see `specs/observability-coverage/spec.md`).
- Files: `services/observability/clusters/feather-core/alloy-logs/release.yaml` (tolerations), a new base `services/observability/base/alloy-talos-logs/` and overlay `services/observability/clusters/feather-core/alloy-talos-logs/` (HelmRelease, Service with `lbipam.cilium.io/ips` annotation), the alert rules in `services/observability/clusters/feather-core/grafana/alerting/rules.yaml`.
- Layer `services-observability` (depends on `foundation-networking`, `foundation-storage`, `foundation-databases`, `wait: false`). The collector change is a DaemonSet spec change: a rolling update of that DaemonSet replaces log-collector pods node by node; there is no API or service impact.
- Runtime impact: six new `alloy-logs` pods on nodes that had none. The new Alloy instance is a new Deployment with its own Service and does not change the OTLP receiver.
