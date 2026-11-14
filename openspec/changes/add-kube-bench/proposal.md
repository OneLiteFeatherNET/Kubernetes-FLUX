## Why

The cluster has no periodic check against the CIS Kubernetes benchmark. Talos is hardened by default, but the kube-apiserver, kubelet and worker settings are still a moving target across upgrades (the cluster is on Talos v1.13.9 and upgrades are planned). A scheduled `kube-bench` run turns the CIS benchmark into a recurring signal instead of a one-off audit.

The check is read-only. It inspects the configuration of each node and writes results to the logs, which Alloy already ships to Loki. It changes no node, no pod and no admission path, so it cannot cause an outage.

## What Changes

- A `CronJob` `kube-bench` that runs `kube-bench` against CIS Kubernetes benchmark 1.10 on control-plane nodes and worker nodes.
- Two Jobs per run: one profile for control-plane nodes, one for worker nodes, each pinned to its node role.
- Namespace `kube-bench` labelled with Pod Security `privileged`. kube-bench needs host PID and host path access to read the node configuration. This is the one namespace in this change with elevated rights.
- A skip list for Talos false positives, derived from the Talos hardening documentation (https://docs.siderolabs.com/talos/v1.14/security/talos-default-hardening-and-cis-compliance). The list is kept in a ConfigMap in the namespace and cited per entry.
- Results are written to stdout as JSON and collected by Alloy into Loki, under the label `job="kube-bench/..."`. Loki is the minimum surface; a Grafana panel is a follow-up.
- A Grafana alert when the job has not completed successfully for 8 days (weekly schedule plus margin).

**Commit type and scope:** the implementation ships as `feat(security)`.

## Out of scope

- Remediation. Failing checks are read and tracked; nothing is changed on the nodes automatically.
- Running kube-bench against Talos's own API server configuration (not on disk; see the open question).
- Enforcement of CIS controls through admission policy.

## Impact

- New capability `cis-benchmark-scan` (see `specs/cis-benchmark-scan/spec.md`).
- New files: `foundation/security/base/kube-bench/` and `foundation/security/clusters/feather-core/kube-bench/`, listed in `foundation/layers/feather-core/security/kustomization.yaml`.
- New alert rule in `services/observability/clusters/feather-core/grafana/alerting/rules.yaml`.
- Layer `foundation-security`; no new layer.
- Runtime impact: a privileged pod runs on each node once a week, for a short time. A privileged pod is not a restart of anything existing. No webhook, no service and no network policy changes.
