## Context

`feather-core` is a Talos v1.13.9 cluster with three control-plane nodes, each running an etcd member. Etcd holds all Kubernetes state and Flux's view of the desired state. Talos can produce a consistent snapshot of etcd from any control-plane node through its API, using `talosctl etcd snapshot`. Access is granted with a Talos `ServiceAccount` resource whose role `os:etcd:backup` may only read snapshots.

Current backup coverage: CNPG (PostgreSQL) and Rook-Ceph write to the in-cluster RGW. That RGW is on the same cluster, so a cluster loss loses both the data and the backup.

Constraint for this change: deployable with no outage and no connection drops. The CronJob is a new, separate workload. It only reads etcd through the Talos API. It creates no new API server watch and no webhook, and it does not restart or change any existing pod.

## Goals / Non-Goals

**Goals**

- A snapshot of etcd at least once per 24 hours, stored off the cluster as well as in-cluster.
- A failed or missing snapshot is alerted within 26 hours.
- A restore is proven in a throwaway cluster before the change counts as done.

**Non-Goals**

- Restoring into `feather-core` as part of this change.
- Backing up objects or volumes (Velero, separate change).
- Choosing the external S3 provider (pending maintainer decision).

## Decision drivers

- Snapshots contain Secrets, so the credential that can read them must be as narrow as possible.
- The cluster must not be made less available by the backup.
- The external target must be a real second location, not a second bucket on the same Ceph.

## Decisions

### 1. Take the snapshot with `talosctl` in a CronJob

**Decision:** A CronJob runs a `talosctl` image, uses the Talos client configuration issued for the `ServiceAccount`, and calls `talosctl etcd snapshot` against one control-plane node. Each run takes a fresh snapshot.

**Rejected alternative:** a Talos-native scheduled snapshot. Talos does not provide one in v1.13.x; the CronJob keeps scheduling in Kubernetes, where it is visible to Flux and to Grafana.

**Consequences:** the CronJob depends on the Talos API being reachable from the `etcd-backup` namespace. The Talos API port is reached through Cilium; the NetworkPolicy must allow it to the control-plane node addresses only.

### 2. Placement: base and cluster overlay

**Decision:** `services/development/...` is not the right domain. The component lives in `foundation/storage/base/etcd-backup/` with the cluster overlay `foundation/storage/clusters/feather-core/etcd-backup/`, next to the existing RGW buckets and users it depends on. It joins the existing `foundation-storage` layer, which already owns the RGW. No new layer.

**Consequences:** the job is part of `foundation-storage`, which is `wait: true`. A broken `CronJob` spec does not block the layer's readiness, because a CronJob has no readiness of its own. The `ServiceAccount` and the Talos client secret are created before the CronJob by the same overlay.

### 3. Pod Security and privileges

**Decision:** the namespace `etcd-backup` is labelled `pod-security.kubernetes.io/enforce: baseline` and `audit`/`warn: restricted`. The snapshot pod needs no host access; it runs non-root, drops all capabilities and has a read-only root filesystem. If `talosctl` needs a writable path, an `emptyDir` is mounted.

**Consequences:** no privileged pod is introduced. Existing workloads are unaffected because the namespace is new.

### 4. Two S3 targets, second one switched on by configuration

**Decision:**

- Target 1 (active): bucket `etcd-snapshots` on the in-cluster RGW, owned by a new `CephObjectStoreUser` `etcd-backup` with access to that bucket only.
- Target 2 (designed, inactive): an external S3 bucket. The provider and the credentials are pending from the maintainer. The upload step reads the target from a Secret; while the Secret does not exist the second upload is skipped and the job logs `external target not configured`. Adding the Secret plus a `values` switch activates it. No code change is needed.

**Rejected alternative:** a second RGW bucket. It is the same failure domain as the cluster and does not satisfy the requirement.

**Consequences:** until the external target is active, the backup is only as safe as the RGW. The open question below tracks this, and the alert in decision 6 covers only the in-cluster upload until the second target exists.

### 5. Retention

**Decision (proposed, to confirm):** on target 1, 14 daily snapshots (an RGW lifecycle rule on prefix `etcd/` with `Expiration: 14 days`). The job prunes older objects itself on each run, so the lifecycle rule is a safety net. On target 2, 30 daily snapshots once active.

**Consequences:** 14 days is the window in which an operator can notice corruption and restore. Snapshot size is measured in the rollout (task 1.4) and the figure is recorded before retention is confirmed.

### 6. Alerting

**Decision:** a Grafana alert rule `etcd-snapshot-missing` in folder `Core Services` fires when `time() - kube_cronjob_status_last_successful_time{cronjob="etcd-snapshot"} > 26 * 3600`, using the Mimir datasource (uid `mimir`). The 26 h threshold is a 24 h schedule plus a 2 h margin.

**Consequences:** the alert depends on kube-state-metrics exporting the CronJob's last successful time; task 1.5 confirms the metric exists in this cluster before the rule is merged.

## Risks / Trade-offs

- **Talos role missing:** the CronJob fails at authentication. Mitigation: the Talos change is a stated dependency and the job is harmless when it fails. No other workload is affected.
- **Snapshot holds Secrets:** an attacker who gets the bucket credentials gets all Secrets. Mitigation: a dedicated user per bucket, no shared credentials, and client-side encryption is an open question.
- **External target not yet active:** a single-location backup remains. Mitigation: the external target is the first follow-up once the maintainer delivers credentials; the alert wording states which targets succeeded.
- **Snapshot load on etcd:** a snapshot is a read of the database. On a 3-node control plane this is a short I/O burst. Mitigation: schedule at a quiet hour and keep the job's `concurrencyPolicy: Forbid`.

## Migration Plan

1. The maintainer (or the Talos repository change) makes the Talos role `os:etcd:backup` live on all control-plane nodes. Verify with `talosctl` that a test client can run `etcd snapshot`.
2. Merge the code PR. Flux reconciles `foundation-storage`: the bucket, the user, the namespace and the `ServiceAccount`. The CronJob is suspended in the first commit (`suspend: true`) and resumed after step 3.
3. Run one manual Job from the CronJob (`kubectl create job --from=cronjob/etcd-snapshot`) and verify the object in the RGW bucket.
4. Remove `suspend: true`; the next scheduled run is the first automatic one.
5. Restore-test in a throwaway cluster (task group 6).
6. Activate target 2 when the external credentials exist (follow-up change).

**Rollback:** set `suspend: true` on the CronJob. Nothing else depends on it.

## Open Questions

- **Which external S3 provider, and who holds its credentials?** Pending from the maintainer. Blocks activation of target 2, not this change.
- **Client-side encryption of snapshots before upload?** Snapshots contain Secrets. Recommendation: encrypt with an age public key so the external provider never sees plaintext. Decision needed from the maintainer.
- **Retention numbers** (14 and 30 days) are a proposal, to be confirmed after the size measurement.
- **Which control-plane node is used:** a fixed node or a rotation? Recommendation: rotate with a short `talosctl` `--nodes` list, so a single node outage does not stop backups.
