## Why

Four core operators restart far more often than they should, and they are the largest real source of the Discord alert noise. Over the last 14 days, the operators in `foundation-certificates-operators` and `foundation-databases-operators` restarted 1,020 times in total:

- `cert-manager-cainjector` (2 replicas): 653 restarts. 72% of its CPU periods were throttled. It mostly exits with "leader election lost", because the lease renewal misses its deadline while the CPU limit throttles it.
- `cert-manager-controller` (2 replicas): 43 restarts, same leader-election pattern, 12.5% throttled periods.
- `step-issuer` manager (1 replica): 160 restarts. Its memory limit is hit (127.4 MiB against a 128Mi limit), and the last termination is `OOMKilled`.
- `mariadb-operator` controller (3 replicas): 164 restarts. Memory is capped at 256Mi, and two pods were `OOMKilled`.

Each restart shows up as "Core container OOM-killed" (150 alert transitions in 14 days, the largest Grafana source) or "Core container crash-looping" (20). The noise is real, but the cause is resource sizing, not the alert rules.

This change right-sizes the four controllers. It removes the CPU limits on the leader-elected controllers, so throttling stops breaking lease renewal, and it sets memory requests and limits from the measured 14-day working sets. Nothing else in the operators changes.

**Commit type and scope:** the implementation ships as `fix(operators)`. All four are the same defect (resource values that do not fit the workload), one capability.

## What Changes

- **cert-manager-cainjector:** remove the CPU limit (keep the CPU request of 30m), memory request 224Mi, memory limit 384Mi (was 128Mi / 256Mi, CPU limit 50m).
- **cert-manager-controller:** remove the CPU limit (keep the CPU request of 20m), memory request 96Mi, memory limit 160Mi (was 40Mi / 80Mi, CPU limit 40m).
- **step-issuer manager:** memory request 160Mi, memory limit 256Mi (was 64Mi / 128Mi). Its CPU limit is already absent.
- **mariadb-operator controller:** memory request 256Mi, memory limit 512Mi (was 128Mi / 256Mi). The CPU request stays at 50m.
- Webhooks are unchanged. Their measured usage is 28% and 23% of their limits.

## Out of scope

- Root-cause analysis of apiserver and etcd latency, which is what the leader-election timeouts also point at. That is a separate investigation.
- Leader-election lease durations and renew deadlines.
- Webhook resources.
- Re-sizing the non-core operators.

## Impact

- New capability `core-operator-resources` (see `specs/core-operator-resources/spec.md`).
- Files: `foundation/certificates/clusters/feather-core/cert-manager/release.yaml`, `foundation/certificates/base/step-issuer/release.yaml` (via the cluster overlay values), `foundation/databases/base/mariadb-operator/release.yaml`.
- Layers: `foundation-certificates-operators` (cert-manager, step-issuer) and `foundation-databases-operators` (mariadb-operator). No layer is renamed or split.
- Runtime impact: the HelmRelease values change, so Helm rolls each Deployment once. The cert-manager Deployments use `maxSurge: 0` and `maxUnavailable: 1` with a PDB of `minAvailable: 1`, so one replica at a time is replaced. mariadb-operator keeps a PDB of `maxUnavailable: 1` across 3 replicas. step-issuer is a single replica, so issuance pauses briefly during its restart. Certificate data is not affected.
