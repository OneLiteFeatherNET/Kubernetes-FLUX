## 1. Preflight

- [ ] 1.1 Confirm with the Talos repository owner that the role `os:etcd:backup` for namespace `etcd-backup` is live on all three control-plane nodes; record the date
- [ ] 1.2 Create a test Talos client from the `ServiceAccount` and run `talosctl etcd snapshot` once by hand; record the snapshot size
- [ ] 1.3 Check the Talos config for the snapshot node list and choose the rotation (open question in design)
- [ ] 1.4 Record the measured snapshot size and derive the retention numbers (design, decision 5)
- [ ] 1.5 Confirm that kube-state-metrics in this cluster exports `kube_cronjob_status_last_successful_time`
- [ ] 1.6 Maintainer decides: external S3 provider, client-side encryption yes or no (design, open questions)

## 2. Storage

- [ ] 2.1 Add the ObjectBucketClaim `etcd-snapshots` in `foundation/storage/clusters/feather-core/rook-fr01/buckets/` (owner: user `etcd-backup`)
- [ ] 2.2 Add the `CephObjectStoreUser` `etcd-backup` in `foundation/storage/clusters/feather-core/rook-fr01/users/`, limited to bucket `etcd-snapshots`
- [ ] 2.3 Register both files in the existing `kustomization.yaml` of `buckets/` and `users/`
- [ ] 2.4 Add the RGW lifecycle rule for prefix `etcd/` with 14-day expiration (safety net, decision 5)

## 3. Base component

- [ ] 3.1 Add `foundation/storage/base/etcd-backup/` with namespace `etcd-backup` (PSA labels from design, decision 3)
- [ ] 3.2 Add the Talos `ServiceAccount` (`talos.dev/v1alpha1`, role `os:etcd:backup`) with the Talos client secret it issues
- [ ] 3.3 Add the `CronJob` `etcd-snapshot`: `talosctl` image pinned by digest, `concurrencyPolicy: Forbid`, `startingDeadlineSeconds` set, non-root, all capabilities dropped, read-only root filesystem, `emptyDir` for scratch
- [ ] 3.4 Add the upload step for target 1 and the target-2 switch (skipped when the external Secret is absent)
- [ ] 3.5 Add the NetworkPolicy: egress only to the Talos API port on control-plane node addresses and to the RGW endpoint, plus DNS
- [ ] 3.6 Add `suspend: true` to the CronJob for the first merge (migration step 2)

## 4. Cluster overlay and alerting

- [ ] 4.1 Add `foundation/storage/clusters/feather-core/etcd-backup/` with a `kustomization.yaml` that references the base and the RGW objects
- [ ] 4.2 Add the Grafana rule `etcd-snapshot-missing` to `services/observability/clusters/feather-core/grafana/alerting/rules.yaml` (folder `Core Services`, 26 h threshold)
- [ ] 4.3 Validate: `./.github/scripts/validate.sh` and `kubectl kustomize` on the new overlay

## 5. Rollout

- [ ] 5.1 Merge the code PR; confirm `foundation-storage` is Ready
- [ ] 5.2 Run one manual Job from the CronJob, verify the object in bucket `etcd-snapshots`
- [ ] 5.3 Remove `suspend: true`, confirm the next scheduled run succeeds
- [ ] 5.4 Confirm the alert rule evaluates to `Normal`

## 6. Restore test in a throwaway cluster

- [ ] 6.1 Stand up a throwaway Talos cluster (not `feather-core`) with the same Talos version
- [ ] 6.2 Restore the snapshot from bucket `etcd-snapshots` with the documented Talos procedure
- [ ] 6.3 Verify on the restored cluster that the expected namespaces and Secrets are present; record the result in the rollout notes
- [ ] 6.4 Tear the throwaway cluster down

## 7. Dependencies outside this repository

- [ ] 7.1 Talos repository change: allow `os:etcd:backup` for `etcd-backup` (must be live before 3.2 is applied)
- [ ] 7.2 Follow-up change when the maintainer delivers external S3 credentials: activate target 2 (not a task of this change)

## 8. Code pull request

- [ ] 8.1 Open the code pull request under the title `feat(backup): add scheduled etcd snapshots to in-cluster and external storage`; no files under `openspec/` in it
