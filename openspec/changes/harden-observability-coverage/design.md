## Context

`alloy-logs` is a DaemonSet in namespace `grafana` that collects pod logs per node and sends them to Loki (`services/observability/clusters/feather-core/alloy-logs/release.yaml`). The overlay sets no tolerations, so the DaemonSet skips the three control-plane nodes (`NoSchedule`) and the three storage nodes (`node-role.feather/storage:NoSchedule`). That leaves four nodes covered.

The OTLP intake `alloy-receiver` is a Deployment with two replicas and a PodDisruptionBudget. It serves Cloudflare Observability through a tunnel, so it must not be restarted by an unrelated change.

Talos can send `machine.logging` output as `json_lines` to an endpoint. The cluster has a Cilium LB-IPAM pool `lb-fr01` and BGP, and Services can pin an address with `lbipam.cilium.io/ips`.

Constraint for this change: deployable with no outage and no connection drops. (a) adds pods to a DaemonSet and evicts none. (b) is a new Deployment with a new Service and does not touch the OTLP receiver. (c) adds read-only alert rules.

## Goals / Non-Goals

**Goals**

- Log collection on all 10 nodes.
- Talos system logs in Loki, through an intake that is independent of the OTLP receiver.
- Alerts for certificate expiry and etcd health with a visible threshold.

**Non-Goals**

- Dashboards.
- Changes to the OTLP receiver or its public route.
- Talos-side changes (separate repository).

## Decision drivers

- Zero restarts of running receivers and no changed Services on the public path.
- Each signal reaches Loki or Mimir with labels that already exist (`cluster`, `namespace`).
- Certificate and etcd alerts must fire before the failure, not after.

## Decisions

### 1. Tolerations for the collector

**Decision:** add to the `alloy-logs` overlay (`controller.tolerations`):

- `node-role.kubernetes.io/control-plane`, effect `NoSchedule`.
- `node-role.feather/storage`, effect `NoSchedule`.

No other change to the DaemonSet.

**Why this is safe:** a DaemonSet pod is created on each newly tolerated node. Existing pods on the four covered nodes are not restarted by a toleration change. The collector's resource requests (100m CPU, 256Mi memory per pod) are small enough for the nodes; the capacity check in task 1.1 confirms it on the three storage nodes, which are the most loaded.

**Consequences:** logs from control-plane and storage nodes start flowing into Loki. The label set is unchanged.

### 2. Talos log intake as a separate Alloy instance

**Decision:** a new HelmRelease `alloy-talos-logs` (chart `alloy`, same pinned version as `alloy-logs`), namespace `grafana`, one Deployment with two replicas and a PDB `maxUnavailable: 1`. It exposes a Service `alloy-talos-logs` with the address annotation `lbipam.cilium.io/ips: <reserved address from lb-fr01>`, and forwards to `loki-gateway`.

**Rejected alternative:** add a port to the existing `alloy-receiver`. That changes its pod template, rolls its two replicas, and puts Talos traffic on the public OTLP path. A separate instance has no effect on OTLP clients and can be removed without touching the receiver.

**Consequences:** one more Deployment in the observability layer. Its footprint is 100m CPU and 256Mi memory per pod, as for the receiver.

### 3. Alerts

**Decision:** rules in folder `Core Services`, Mimir datasource (uid `mimir`):

- API server client certificate: `min(apiserver_client_certificate_expiration_seconds_bucket)` based expiry under 14 days. The exact PromQL is written against the metric's bucket shape and verified in task 1.4.
- Kubelet serving certificate: expiry from the kubelet certificate manager metric, under 14 days, per node.
- cert-manager: `certmanager_certificate_expiration_timestamp_seconds - time() < 14 * 86400` per certificate.
- etcd: `max(etcd_server_has_leader) == 0` for 1 minute, and `increase(etcd_server_leader_changes_seen_total[15m]) > 3`.

**Consequences:** the etcd rules need the etcd metrics endpoint scraped. Talos may expose it only on the control-plane network; if so, the scrape needs an Alloy target on that network (open question 2).

### 4. Placement

**Decision:** collector tolerations stay in `services/observability/clusters/feather-core/alloy-logs/`. The new intake is `services/observability/base/alloy-talos-logs/` and its overlay under `services/observability/clusters/feather-core/alloy-talos-logs/`. Alerts stay in `services/observability/clusters/feather-core/grafana/alerting/rules.yaml`. No layer is created or renamed.

## Risks / Trade-offs

- **Address conflict:** the reserved LB-IPAM address may already be in use. Mitigation: task 1.2 checks it against the pool and the Cilium `CiliumLoadBalancerIPPool` status before it is written into the Service.
- **Talos logs not yet sent:** the intake and its alerts show no data until the Talos side is live. Mitigation: the alerts do not depend on Talos logs; the intake is only the transport.
- **Alert noise on certificates:** a 14-day threshold on kubelet certificates may fire on rotated certificates. Mitigation: the rule uses the current remaining TTL, not the issuance time, so rotation resets it.

## Migration Plan

1. Preflight: check node capacity on the storage nodes; reserve the LB-IPAM address; verify the metrics names in Mimir.
2. Merge the code PR. Flux rolls out `alloy-logs` with tolerations and installs `alloy-talos-logs` and the rules.
3. Verify `alloy-logs` runs on 10 nodes (`kubectl get ds -n grafana alloy-logs`).
4. Verify the intake Service has the reserved address (`kubectl get svc -n grafana alloy-talos-logs`).
5. Talos side goes live (separate change); verify json_lines appear in Loki.

**Rollback:** remove the tolerations (the DaemonSet loses four-node coverage, no data loss); delete the `alloy-talos-logs` HelmRelease; delete the rules.

## Open Questions

1. **Which Alloy component accepts newline-delimited JSON over TCP?** Candidates: `loki.source.syslog` with a JSON-line parser, or a `loki.source.api`-style intake through a small shim. Decision needed in task 1.3 by testing with a sample Talos message.
2. **Is the Talos etcd metrics endpoint reachable from the cluster?** If not, the etcd rules are limited to Kubernetes-visible signals and the Talos side must expose the endpoint. Decision needed in task 1.5.
3. **Which address from `lb-fr01` is reserved for the intake?** Chosen in task 1.2 and recorded in the code PR.
