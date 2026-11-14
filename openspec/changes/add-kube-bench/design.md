## Context

`feather-core` runs Talos v1.13.9 on 10 nodes. Talos hardens the nodes by default and does not expose the kube-apiserver, kubelet and kube-scheduler configuration as files on disk in the way `kube-bench` expects. Some CIS checks therefore fail on Talos for reasons that are not real findings. The Talos hardening page lists these checks and the reasons they do not apply.

Constraint for this change: deployable with no outage and no connection drops. The CronJob is a new, separate workload that only reads the node configuration. It creates no webhook, changes no node and restarts no pod.

## Goals / Non-Goals

**Goals**

- A weekly CIS 1.10 result for control-plane and worker nodes, with Talos false positives skipped and each skip cited.
- Results available in Loki.
- A failed or missing run alerted.

**Non-Goals**

- Remediation.
- Changing Talos configuration in this repository.

## Decision drivers

- The check must not change the cluster.
- Results must be readable without extra tooling.
- Talos false positives must not drown real findings.

## Decisions

### 1. Two Jobs, one per node role

**Decision:** one CronJob with two Jobs per run: `kube-bench run --targets master` pinned to control-plane nodes (`node-role.kubernetes.io/control-plane` label selector) and `kube-bench run --targets node` pinned to worker nodes. Targets map to the node roles of the Talos cluster.

**Consequences:** the control-plane Job runs on the three control-plane nodes, which are tainted `NoSchedule`. The Job's pod template carries the toleration for that taint. The worker Job runs on workers and must tolerate the storage taint `node-role.feather/storage:NoSchedule` for the three storage nodes. Both tolerations add pods; nothing is evicted.

### 2. Privileged namespace, scoped to this job

**Decision:** namespace `kube-bench` is labelled `pod-security.kubernetes.io/enforce: privileged`. kube-bench needs `hostPID` and read access to host configuration paths. No other workload is placed in this namespace.

**Rejected alternative:** a privileged exemption for one pod in a shared namespace. Harder to audit, and the namespace label is visible in review.

**Consequences:** the namespace is the only privileged namespace added by this change. Kyverno audit (`add-kyverno-audit`) excludes `kube-bench`.

### 3. Talos skip list

**Decision:** a ConfigMap `kube-bench-skips` lists each skipped check ID with a one-line reason and the Talos documentation link. The list is generated from the Talos hardening page during preflight (task 1.2), not written from memory. The kube-bench configuration file is mounted from this ConfigMap.

**Consequences:** the skip list is reviewed in the code PR. A skip without a cited reason fails the review.

### 4. Results to Loki

**Decision:** the Job writes the JSON result to stdout. Alloy's log collector already ships pod logs to Loki with namespace and pod labels (`alloy-logs`). Results therefore need no new collector. The result has a line `kube-bench-summary` with the totals of pass, fail, warn and info, which a LogQL query can count.

**Consequences:** the result depends on the Alloy DaemonSet running on the nodes where the Job runs. The tolerations change in `harden-observability-coverage` makes that true for all 10 nodes. Until that change lands, results from nodes without an Alloy pod are missing; the alert in decision 5 reports this as a missing run.

**Alternative considered:** a PersistentVolume with the result files. Rejected: adds storage for a weekly text file that Loki already keeps.

### 5. Alerting

**Decision:** a Grafana rule `kube-bench-stale` in folder `Core Services`, using the Mimir datasource, fires when the CronJob has no successful run for 8 days (`time() - kube_cronjob_status_last_successful_time{cronjob="kube-bench"} > 8 * 86400`). A second rule fires when the summary line in Loki reports any fail count above the baseline recorded at rollout.

**Consequences:** the second rule needs a baseline value set after the first run; until then only the staleness rule is active.

### 6. Schedule and resources

**Decision:** weekly, Sunday 04:00, `concurrencyPolicy: Forbid`, `ttlSecondsAfterFinished` set. Requests 50m CPU and 128Mi memory, limits 500m and 256Mi.

## Risks / Trade-offs

- **Privileged pods on every node:** the scope is one short Job per node per week. Mitigation: privileged namespace contains only this job; no image other than the pinned kube-bench image.
- **Skip list hides a real finding:** mitigation: each skip cites the Talos reason and the list is reviewed in the code PR.
- **Image trust:** the kube-bench image is pinned by digest and comes from the upstream Aqua project image. Mitigation: digest pinning, and trivy-operator already scans the image.
- **Alloy gap:** results missing from Loki on nodes without an Alloy pod. Mitigation: `harden-observability-coverage` closes the gap.

## Migration Plan

1. Preflight: generate the skip list from the Talos page (task 1.2); pin the kube-bench image by digest.
2. Merge the code PR with the CronJob `suspend: true`.
3. Run one manual Job per role, read the summary line in Loki, set the rule baseline.
4. Resume the CronJob.

**Rollback:** `suspend: true` on the CronJob; delete the namespace to remove all results pods.

## Open Questions

- **Does kube-bench read Talos's control-plane configuration?** Talos does not keep the apiserver and scheduler configuration as files on disk. The control-plane Job may report checks as "not found" rather than failing. Decision needed after the first manual run: keep those checks as skipped with a Talos reason, or drop the control-plane target.
- **CIS benchmark version:** 1.10 is the requested profile. Confirm that the pinned kube-bench version supports it against the Kubernetes version of the cluster.
- **Result surfacing beyond Loki:** a Grafana dashboard panel, or a Discord notification on a new fail. Follow-up, not part of this change.
