## Context

`feather-core` has a `CephObjectStoreUser` `velero` with bucket and user capabilities `*` on the store `feather-s3` (`foundation/storage/clusters/feather-core/rook-fr01/users/velero.yaml`). No bucket for Velero exists yet. The Rook-Ceph RBD CSI driver is `rook-ceph.rbd.csi.ceph.com` with the storage classes `rbd` and `rbd-ephemeral`. The snapshot-controller is installed (`foundation/storage/base/snapshot-controller`). No `VolumeSnapshotClass` exists in the repository, so CSI snapshots do not work yet even though the controller runs.

Constraint for this change: deployable with no outage and no connection drops. Velero is a new Deployment. It adds no webhook, so no API request can be blocked by it. Its CRDs are new; none of the existing objects change. The `VolumeSnapshotClass` only applies to new snapshots.

## Goals / Non-Goals

**Goals**

- Kubernetes objects and RBD volumes are backed up on a schedule to the in-cluster RGW and to an external S3 location.
- A backup failure is alerted.
- A restore is tested once in a throwaway cluster.

**Non-Goals**

- Backing up CNPG and Rook-Ceph metadata through Velero.
- Backing up volumes that have no CSI snapshot class (no such volume is known today; see decision 3).

## Decision drivers

- Keep the blast radius small: no webhook, no node-level change, no pod restart.
- Second location outside the cluster, as for etcd.
- Use existing components (`velero` user, snapshot-controller) instead of new ones.

## Decisions

### 1. Install the official chart through Flux

**Decision:** `vmware-tanzu/velero` chart, pinned to an exact version, `HelmRepository` in `foundation/sources/clusters/feather-core/sources/`, HelmRelease in namespace `velero` under the base in decision 2. The exact chart version is chosen in task 1.2 and recorded in the code PR.

**Rejected alternative:** raw manifests. Third-party software reaches the cluster only through the official chart (repository rule).

### 2. Placement: foundation storage

**Decision:** Velero's base lives in `foundation/storage/base/velero/` with overlay `foundation/storage/clusters/feather-core/velero/`. Velero backs up storage, and its only dependency is the RGW and the snapshot-controller, both in `foundation-storage`. The layer is not renamed or split.

**Consequences:** the layer's `wait: true` covers the Velero HelmRelease. A Velero install failure would block `foundation-storage` readiness and therefore its dependents (`foundation-databases`, `services-*`). To avoid that, the HelmRelease sets `install.remediation.retries: 0` and `upgrade` remediation with `remediateLastFailure: true`, the same pattern as `alloy-logs`. Alternative considered: a separate layer. Rejected, because a new layer changes the dependency graph and needs the rename procedure; the constraint is met by the remediation settings instead.

### 3. CSI snapshots first, node agent off

**Decision:** Rook-Ceph RBD volumes are backed up with CSI snapshots (`VolumeSnapshotClass` `rbd-snapshot` with driver `rook-ceph.rbd.csi.ceph.com`, deletion policy `Retain` so that a deleted Velero backup does not delete the Ceph image in use by a restore). The node agent (file-system backup DaemonSet) stays off in the first rollout.

**When to enable it:** only if an inventory of PVCs finds a volume without a CSI snapshot class (task 1.3). In that case the node agent is enabled and must tolerate the storage taint `node-role.feather/storage:NoSchedule` only if the volume lives on a storage node. Tolerations are not added by default.

**Consequences:** the DaemonSet is a new set of pods, so enabling it later only adds pods and evicts nothing (global rule). No pod restart is needed to enable CSI snapshots.

### 4. Schedules and retention

**Decision (proposed, to confirm):**

- `daily`: cron `0 2 * * *`, TTL 14 days, includes all namespaces except `kube-system`, `velero`, and namespaces without volumes that are rebuilt from Git (Flux restores these).
- `weekly`: cron `0 3 * * 0`, TTL 56 days.

**Consequences:** namespaces that Flux rebuilds (all of them, from Git) need only Kubernetes objects for fast restore. Volume data is what must be protected. The exclusion list is recorded in the Schedule manifest and reviewed when a namespace is added.

### 5. Two backup storage locations

**Decision:**

- `default` location: bucket `velero`, the in-cluster RGW, owner `velero` user (existing `CephObjectStoreUser`). A new `ObjectBucketClaim` `velero` sets `bucketOwner: velero`.
- `external` location: external S3, `default: false`, `accessMode: ReadOnly` until the credentials exist. Switched to `ReadWrite` by a follow-up change. Until then, Velero does not write to it.

**Rejected alternative:** two `default` locations or a single location with replication. Velero supports one default location per schedule; schedules are written against `default` and `external` explicitly once the external one is active.

**Consequences:** until the external location is active the backup is only in the in-cluster RGW. The alert in decision 6 names the location.

### 6. Alerting

**Decision:** Velero exports `velero_backup_last_successful_timestamp`. A Grafana rule `velero-backup-missing` in folder `Core Services` fires when the daily schedule has no successful backup for 26 hours, using the Mimir datasource (uid `mimir`).

### 7. Restore test

**Decision:** one restore into a throwaway cluster before the change is complete. The test uses the RBD snapshot of a non-critical PVC and an object restore of one namespace.

## Risks / Trade-offs

- **Velero install breaks `foundation-storage` readiness:** mitigated by remediation settings (decision 2) and by installing in a commit that can be reverted.
- **Retain policy keeps snapshot objects:** `Retain` means snapshots of deleted PVCs remain until removed by hand. Mitigation: an occasional manual cleanup step, recorded in the runbook.
- **Restore into a live cluster:** Velero restores can overwrite objects. Mitigation: restores are only documented, not automated, and the restore test is in a throwaway cluster.

## Migration Plan

1. Preflight: inventory PVCs and their storage classes (task 1.3); decide whether the node agent is needed.
2. Create the bucket `velero` and the `VolumeSnapshotClass` first (both are inert without Velero).
3. Merge the code PR. Flux installs Velero with `default` active, `external` `ReadOnly`, schedules created with `paused: true`.
4. Run one manual backup (`velero backup create`) and verify it in bucket `velero`.
5. Unpause the schedules.
6. Restore test in a throwaway cluster.
7. Follow-up: activate the external location when credentials exist.

**Rollback:** set the schedules to `paused: true`; delete the HelmRelease. Existing backups remain in the RGW bucket.

## Open Questions

- **External S3 provider and credentials:** pending from the maintainer. Blocks activation of the external location only.
- **Is there any volume without a CSI snapshot class?** Answered in task 1.3; determines whether the node agent is enabled.
- **Retention:** 14 days daily and 56 days weekly are proposals to confirm after the first month of storage measurement.
- **Velero chart version:** chosen at rollout; must be recorded in the code PR.
