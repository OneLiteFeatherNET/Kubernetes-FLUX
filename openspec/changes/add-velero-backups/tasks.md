## 1. Preflight

- [ ] 1.1 Confirm the RGW user `velero` (existing) can create bucket `velero` with the ObjectBucketClaim
- [ ] 1.2 Choose the Velero chart version (`helm show chart vmware-tanzu/velero`) and the matching Velero plugin for CSI; record both in the code PR
- [ ] 1.3 Inventory PVCs: list storage class per PVC; record any volume without a CSI snapshot class (decides the node agent)
- [ ] 1.4 Confirm the snapshot-controller CRDs (`VolumeSnapshot`, `VolumeSnapshotContent`, `VolumeSnapshotClass`) are installed
- [ ] 1.5 Maintainer decides: external S3 provider and credentials (blocks activation only)
- [ ] 1.6 Confirm that `velero_backup_last_successful_timestamp` is exported to Mimir

## 2. Storage objects

- [ ] 2.1 Add the ObjectBucketClaim `velero` in `foundation/storage/clusters/feather-core/rook-fr01/buckets/` with `bucketOwner: velero`
- [ ] 2.2 Register it in the buckets `kustomization.yaml`
- [ ] 2.3 Add the `VolumeSnapshotClass` `rbd-snapshot` (driver `rook-ceph.rbd.csi.ceph.com`, deletion policy `Retain`) in the storage overlay, with the Rook RBD provisioner secret parameters

## 3. Base component

- [ ] 3.1 Add `HelmRepository` for Velero in `foundation/sources/clusters/feather-core/sources/` and list it in that `kustomization.yaml`
- [ ] 3.2 Add the base `foundation/storage/base/velero/` with namespace `velero`, HelmRelease with pinned version, `install` and `upgrade` remediation as in design, decision 2
- [ ] 3.3 Configure the `default` BackupStorageLocation (in-cluster RGW) and the `external` location with `accessMode: ReadOnly` and `default: false`
- [ ] 3.4 Set `deployNodeAgent: false`; set the CSI plugin feature flag
- [ ] 3.5 Add the `Schedule` objects `daily` (02:00, TTL 336h) and `weekly` (Sunday 03:00, TTL 1344h), both created with `paused: true`
- [ ] 3.6 Add the Schedule exclusions for `kube-system` and `velero`

## 4. Cluster overlay and alerting

- [ ] 4.1 Add `foundation/storage/clusters/feather-core/velero/` with `kustomization.yaml` referencing the base
- [ ] 4.2 Add the Grafana rule `velero-backup-missing` to `services/observability/clusters/feather-core/grafana/alerting/rules.yaml` (folder `Core Services`, 26 h)
- [ ] 4.3 Validate with `./.github/scripts/validate.sh` and `kubectl kustomize` on the overlay

## 5. Rollout

- [ ] 5.1 Merge the code PR; confirm `foundation-storage` is Ready
- [ ] 5.2 Run one manual backup (`velero backup create`) and verify objects in bucket `velero`
- [ ] 5.3 Unpause `daily` and `weekly`
- [ ] 5.4 Confirm the alert rule evaluates to `Normal`

## 6. Restore test in a throwaway cluster

- [ ] 6.1 Restore one namespace and one RBD PVC from the snapshot into a throwaway cluster; record the result
- [ ] 6.2 Tear the throwaway cluster down

## 7. Follow-ups outside this change

- [ ] 7.1 Activate the external location (`accessMode: ReadWrite`, credentials Secret) when the maintainer delivers credentials
- [ ] 7.2 Enable the node agent only if task 1.3 finds a volume without CSI snapshot class (its own change)

## 8. Code pull request

- [ ] 8.1 Open the code pull request under the title `feat(backup): add velero backups with rbd csi snapshots`; no files under `openspec/` in it
