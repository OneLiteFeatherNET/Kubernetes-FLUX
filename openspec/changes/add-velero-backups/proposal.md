## Why

The cluster has no backup of Kubernetes objects and of persistent volume data. Rook-Ceph and CloudNativePG write their backups into the in-cluster RGW, which is lost with the cluster. A cluster-level Velero backup stores Kubernetes objects and CSI volume snapshots in an S3 location, first in the in-cluster RGW and then in an external S3 location, so a restore is possible after the loss of `feather-core`.

Velero is installed as an additive component. It reads objects and creates snapshots; it does not change running pods, and it adds no admission webhook. Installing it cannot stop a running workload.

## What Changes

- Velero HelmRelease from the official `vmware-tanzu/velero` chart, pinned, in namespace `velero`, through a `HelmRepository` source in the existing source set.
- A `BackupStorageLocation` `default` on the in-cluster RGW, bucket `velero`, using the existing `CephObjectStoreUser` `velero` (already in `rook-fr01/users/velero.yaml`) plus a new `ObjectBucketClaim` `velero` that that user owns.
- A second `BackupStorageLocation` `external` on an external S3 target. Provider and credentials are pending from the maintainer; the location is declared but `default: false` and not used until the credentials exist.
- CSI snapshots for Rook-Ceph RBD via the existing snapshot-controller: a `VolumeSnapshotClass` for `rook-ceph.rbd.csi.ceph.com`, which the repository does not have yet.
- `Schedule` objects with retention (see design, decision 4).
- Node agent: a DaemonSet for file-system backup. It is disabled in the first rollout because CSI snapshots cover the RBD volumes. It is enabled only if a volume type without a CSI snapshot class needs backup (design, decision 3).

**Commit type and scope:** the implementation ships as `feat(backup)`. The change adds backup capability; the snapshot class is part of that capability and not a separate fix.

## Out of scope

- etcd snapshots (`add-etcd-snapshot-backups`).
- Restoring into `feather-core`. A restore is a documented procedure, tested in a throwaway cluster.
- Backing up CNPG and Rook-Ceph data through Velero. Those keep their own mechanisms (CNPG barman into RGW); Velero covers Kubernetes objects and RBD volumes.

## Impact

- New capability `velero-backups` (see `specs/velero-backups/spec.md`).
- New files: the base `foundation/storage/base/velero/` and overlay `foundation/storage/clusters/feather-core/velero/` (design, decision 2), `foundation/storage/clusters/feather-core/rook-fr01/buckets/velero.yaml`, and a `VolumeSnapshotClass` manifest in the storage overlay.
- Layer: `foundation-storage` (depends on `foundation-storage-operators`, `foundation-networking`); no new layer, no rename.
- Runtime impact: Velero's controller is a new Deployment; the node agent is off in the first rollout. The `VolumeSnapshotClass` is a cluster-scoped object that does not change existing `PersistentVolumeClaim`s. Creating a snapshot reads an RBD image; it does not pause the volume.
