## Why

Today there is no backup of the cluster state that lives in etcd. Every backup the cluster makes (CloudNativePG WAL and base backups, Rook-Ceph pools) lands in the in-cluster Rook-Ceph RGW. If `feather-core` is lost, the RGW goes with it, so the etcd state (all Kubernetes objects, Secrets and Flux state) cannot be restored from anywhere. A Talos etcd snapshot taken on a schedule and stored off the cluster closes this gap.

The snapshot is taken with `talosctl etcd snapshot` through the Talos API, so no SSH and no host access are needed. The cluster keeps running while the snapshot is taken, so this change adds backups without changing any running workload.

## What Changes

- Namespace `etcd-backup` with a Pod Security Admission label that fits the snapshot job (see design, decision 3).
- A Talos `ServiceAccount` resource (`apiVersion: talos.dev/v1alpha1`, role `os:etcd:backup`). It makes Talos issue a client certificate that may read etcd snapshots and nothing else.
- A `CronJob` `etcd-snapshot` that runs `talosctl etcd snapshot` against one control-plane node, then uploads the file to two S3 targets.
- Target 1, active: a new bucket `etcd-snapshots` on the in-cluster Rook-Ceph RGW (ObjectBucketClaim plus a dedicated user).
- Target 2, designed but inactive: an external S3 bucket. Its provider and credentials are still pending from the maintainer. The second target is switched on by a single overlay change once the credentials exist.
- Retention on each target (see design, decision 5).
- A Grafana alert in `Core Services` that fires when no successful snapshot has completed in 26 hours.
- Restore-test runbook task: a snapshot is restored into a throwaway cluster before the change is considered done.

**Commit type and scope:** the implementation ships as `feat(backup)`. One type per commit: the change adds backup capability and nothing else.

**Dependency (outside this repository):** the Talos side that allows the role `os:etcd:backup` for the namespace `etcd-backup` is a separate change in the Talos repository. It must be live on all control-plane nodes before the `ServiceAccount` here is applied. Without it the CronJob fails at authentication and no other part of the cluster is affected.

## Out of scope

- Velero backups of Kubernetes objects and persistent volumes. Those are `add-velero-backups`.
- Automatic etcd restore. Restore is a documented manual procedure, tested once in a throwaway cluster (task group 6).
- Encryption of the snapshot at rest beyond the storage provider's own mechanisms. Snapshots contain Secrets; see the open question on client-side encryption in design.

## Impact

- New capability `etcd-backup` (see `specs/etcd-backup/spec.md`).
- New files: `foundation/storage/clusters/feather-core/rook-fr01/buckets/etcd-snapshots.yaml`, `.../rook-fr01/users/etcd-backup.yaml`, and the base `foundation/storage/base/etcd-backup/` with the cluster overlay `foundation/storage/clusters/feather-core/etcd-backup/` (design, decision 2).
- Alert rule added to `services/observability/clusters/feather-core/grafana/alerting/rules.yaml`.
- Layer placement: the component joins the existing `foundation-storage` layer; no new layer is created and no layer is renamed.
- Runtime impact: one short Talos API call per schedule run. The snapshot is read-only for etcd; it adds no write load to etcd beyond the read.
