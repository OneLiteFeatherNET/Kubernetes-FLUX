# Ceph Capacity Reclamation and Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take `rook-ceph-fr01` off a measured 0.419 TiB/day fill curve — 71.26% raw used, 3.4 TiB free, fullest OSD at 77.75% — and back under a documented ~55–60% raw operating ceiling, then install the quota, lifecycle and alerting guardrails that stop it recurring.

**Architecture:** Six separately-mergeable PRs (PR 1, PR 2, PR 3a, PR 3b, PR 4, PR 5) plus operational (non-git) stages, sequenced strictly by risk. Non-destructive measurement first, then dead-data deletion that no live object references, then the one high-value git change (MariaDB backup retention + compression), then CNPG retention, then PV/RBD reaping, then quotas and lifecycle, then docs and the new alert. Every destructive step is gated on a verification command whose expected output is written out here.

**Tech Stack:** FluxCD (`Kustomization`/`HelmRelease`), Kustomize, Rook-Ceph 1.20.3 / Ceph Squid 19.2.5, RGW (realm `feather-s3`), ceph-csi RBD + CephFS, mariadb-operator 26.6.0 (`PhysicalBackup`), CNPG barman-cloud plugin (`ObjectStore`), Grafana unified alerting.

---

## Baseline as measured 2026-08-03 (re-measure in Task 1 before acting)

| Fact | Value | Source |
|---|---|---|
| Raw usage | 8.6 TiB used / 12 TiB, **71.26 %RAW USED**, 3.4 TiB AVAIL | `ceph df` |
| MAX AVAIL per pool | 706 GiB | `ceph df` |
| Fullest OSD | osd.5 @ **77.75%** (fr01-str-02) | `ceph osd df tree` |
| Ceph thresholds | nearfull 0.85 / backfillfull 0.90 / full 0.95 | `ceph osd dump` |
| Growth | **+0.419 TiB/day** raw | Mimir `ceph_cluster_total_used_bytes` |
| Health | HEALTH_OK | `ceph health detail` |
| Projected host % after losing one OSD | **95.03%** | PromQL in Task 16 |
| S3 total | 2555.17 GiB logical across 18 buckets | `radosgw-admin bucket stats` |
| `mariadb-galera-backup` | 1921.29 GiB / 266 obj = 75% of all S3 | same |
| — `feather-core-backups/` | 71 obj / 1790.46 GiB, 2026-07-13 → 2026-08-03 | `radosgw-admin bucket list` |
| — `backups/` (retired prefix) | 65 obj / 128.81 GiB, all mtime 2026-06-09 | same |
| — `_multipart_*` orphans | 149 obj / 2.02 GiB, 2026-06-09 + 2026-07-23…26 | same |
| Objects >7d old under the live prefix | **42 obj / 788.1 GiB logical** | same |
| Released PVs | **42** (37 Bound), RBD used 128.71 GiB = 386.13 GiB raw | `kubectl get pv` + `rbd du` |
| CephFS orphans | 6 subvolumes, 0 Bound CephFS PVCs, 76.1 GiB = ~228 GiB raw | `ceph fs subvolume ls/info` |
| `kafka-data-mimir-kafka-0` | Bound, 7.91 GiB used = 23.7 GiB raw, `kafka.enabled: false` | `rbd du` |
| Bucket quotas | **all 18 buckets**: `enabled=false, max_size=-1` | `radosgw-admin bucket stats` |
| Lifecycle rules | `radosgw-admin lc list` → `[]` (none anywhere) | same |

**Total reclaimable by this plan: ~3.3 TiB raw** — 2.31 TiB from MariaDB retention, 386 GiB from the retired `backups/` prefix, 386 GiB from Released PVs, ~228 GiB from CephFS orphans, ~24 GiB from the kafka PVC, ~6 GiB from multipart debris. That moves the cluster from 71.3% to roughly 44% raw.

---

## Prerequisites

- `kubectl` context `admin@feather-core`, write access. Several steps run destructive `radosgw-admin`/`aws s3`/`kubectl delete pv` commands.
- Push access to `main` and `gh` CLI.
- Ceph must be `HEALTH_OK` (or only pre-existing warnings) before starting. Verify: `kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph health detail` → `HEALTH_OK`.
- All Flux layers Ready at the same revision. Verify: `flux get kustomizations -A` → every row `READY=True`. At the time of writing `base-configs` was transiently `False` ("dependency 'flux-system/base-controllers' is not ready") while `base-controllers` was `True` — if you see that, wait for it to settle rather than reconciling in a loop.

## Cross-theme dependencies

- **Blocks `offsite-backups-and-disaster-recovery`:** that theme's `VolumeSnapshotClass` + RBD snapshot work must not begin until **Task 6's gate** passes. RBD snapshots are copy-on-write inside `feather-rbd` and add to the same pool this plan is draining.
- **File conflict with `alert-coverage-and-escalation`:** Task 16 edits `apps/clusters/feathre-core/base-apps/grafana/release.yaml`, a 28k-line file that theme also edits. Land whichever is ready first, then rebase; re-anchor the insertion point by searching for `uid: ceph-osd-usage-critical` rather than trusting the line number in this plan.
- **File conflict with `flux-release-control-and-convergence`:** Task 12 edits `infrastructure/clusters/feather-core/rook/release.yaml` line 31 only; that theme edits line 11 (chart version). Non-overlapping, but rebase before pushing.
- **Defers to `alert-coverage-and-escalation`:** the `mimir-alertmanager` disable decision (Task 17) — see Decision Gate 5.

## Decision gates

These need a human answer before the referenced task runs. Do not silently pick.

| # | Task | Question | Options | Recommendation |
|---|---|---|---|---|
| 1 | Task 2 | Keep the pre-MariaDB-12.3 restore point? `feather-core-backups/physicalbackup-20260718060000.xb.bz2` (5.6 GiB, MariaDB 11.8.8, per `docs/incidents/2026-07-18-…md:53`) is 16 days old and **will be deleted** by a 168h retention. | (a) copy it to a `restore-points/` prefix first (+5.6 GiB logical / 17 GiB raw, permanent); (b) accept the loss | **(a)** — 17 GiB raw is noise against 3.3 TiB reclaimed, and it is the only artefact that could roll the data back across a two-major upgrade. |
| 2 | Task 5 | MariaDB backup retention window. | 168h (7d) / 336h (14d) / 240h (10d) | **168h.** At 4 backups/day × ~34.5 GiB uncompressed that is still 966 GiB logical = 2.83 TiB raw until gzip takes effect; 336h would be 5.7 TiB raw and does not solve the problem. |
| 3 | Task 5 | Backup cadence. `cron: "0 */6 * * *"` = 4/day. | keep 6-hourly / go daily | **Keep 6-hourly.** With gzip (measured 4.2× on this cluster) 7 days × 4/day ≈ 230 GiB logical ≈ 0.67 TiB raw, which is affordable. Going daily would cost 6h of RPO for no capacity need. |
| 4 | Task 7 | CNPG barman retention. Bucket holds 140.34 GiB / 41,463 objects with backups back to 2026-06-10 (54 days) and **no** retention policy. | 14d / 30d / 60d | **30d.** This is the only Postgres backup store and no restore has ever been rehearsed; 30d exceeds a plausible silent-corruption detection window. It is 1.6% of cluster usage, so there is no capacity argument for going shorter. |
| 5 | Task 17 | Disable `mimir-alertmanager` (2 replicas, 2×10Gi PVCs) and/or cut Mimir `compactor_blocks_retention_period` from 365d? | see Task 17 | **Defer the alertmanager to `alert-coverage-and-escalation`** — measured actual usage is 0.06 GiB per PV, so there is no capacity case, and that theme may need it. **Do** put the 365d Mimir retention in front of the owner: `mimir-blocks` is 187 GiB after ~2 months and 365d extrapolates past 1 TiB logical / 3 TiB raw. |

## Global constraints

- Conventional Commits enforced by CI (`commitlint.config.mjs`): types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`; subject starts lowercase; header ≤100 chars. PR titles are linted too.
- `./scripts/validate.sh` must pass locally before every commit.
- A change takes effect only once pushed to `main`. One `flux reconcile` per stage, then verify — never in a loop.
- Renovate moves `main`; `git pull --rebase` before every push.
- No secret changes in this plan, so no `rollout restart` for `disableNameSuffixHash` reasons. The one restart that *is* required is the Rook operator in Task 12.
- **`mariadb-operator` prunes on the backup Job, not on reconcile.** Retention is enforced by the `mariadb-operator backup` command inside each backup Job, *before* it uploads the new backup, and it only lists objects under `spec.storage.s3.prefix`. Changing `maxRetention` in git does nothing until the next Job runs (00:00/06:00/12:00/18:00) or you force one.
- **RGW lifecycle runs once a day, in a window.** `ceph config get client.rgw rgw_lifecycle_work_time` → `00:00-06:00`, `rgw_lc_debug_interval` → `-1`. A lifecycle rule added in Task 14 will not clean anything until that window; it is prevention, not cleanup. The existing debris is removed by hand in Task 3.

## What this plan deliberately does NOT do

- **Does not add capacity.** No 4th storage host, no 5th device per node. Task 15 writes the headroom arithmetic down so the decision can be made with numbers; buying hardware is not this plan's job.
- **Does not touch `full_ratio`/`nearfull_ratio`/`backfillfull_ratio`.** Raising them to buy time is exactly the trap that turns a capacity warning into an unrecoverable cluster.
- **Does not blanket-flip any StorageClass `reclaimPolicy` from `Retain` to `Delete`.** `Retain` is a deliberate, correct default here. Tasks 8–11 flip the policy on *individual, named* PVs immediately before deleting them.
- **Does not change Loki (`retention_period: 168h`) or Tempo (`block_retention: 168h`).** Both are already short and together account for ~10 GiB of 2.5 TiB.
- **Does not change Mimir's `compactor_blocks_retention_period: 365d`** without the owner's answer to Decision Gate 5 — it is a real future cliff but not a current one, and it is a data-retention policy choice, not a bug.
- **Does not adopt the undeclared `olf` bucket** (54.38 GiB, owner `olf`). The `CephObjectStoreUser` is in git (`infrastructure/clusters/feather-core/rook-fr01/users/olf.yaml`) but there is no OBC for the bucket, so it gets the lifecycle rule imperatively in Task 3 and is otherwise flagged to `flux-release-control-and-convergence`.
- **Does not rehearse a restore.** Task 7 adds a retention policy that will start deleting Postgres base backups; the "is this backup worth anything" question belongs to `offsite-backups-and-disaster-recovery`.
- **Does not export the uptime-kuma monitor definitions to git** (part of `observability-dr/unused-and-undeclared-observability-components`). They live only in the Galera DB (`volume.enabled: false`, `UPTIME_KUMA_DB_*` → maxscale-galera). That is a reproducibility gap, not a capacity one — it belongs to `offsite-backups-and-disaster-recovery`.
- **Does not re-provision the `feather-core-cluster-pg-backup` OBC.** See the loud warning in Task 14 Step 5: its bucket owner is an OBC-generated RGW user whose access key is what the git-committed SOPS secret `cnpg-backup` contains, so deleting that OBC would break CNPG WAL archiving. Its quota and lifecycle are applied imperatively instead (Task 14 Step 5b).
- **Does not delete `apps/base/pyroscope` / `apps/base/pushgateway`** or the other unreferenced bases (`backstage`, `minio`, `autocert`, `action-runner-controller`). They consume zero cluster resources — dead-code removal belongs to `flux-release-control-and-convergence`.

## Re-verified against the live cluster on review (2026-08-03, later the same day)

These were checked again after the plan was written and still hold: `%RAW USED 71.28`; `37 Bound / 42 Released`
PVs, and the 21+21 PV UUIDs in Tasks 8 and 9 all exist, are all `Released`, and split exactly as the plan claims
(Task 8 = 21 dead claimRefs, Task 9 = 21 with a live PVC on a *different* PV); 17 OBCs; `radosgw-admin lc list`
→ `[]`; `ROOK_OBC_ALLOW_ADDITIONAL_CONFIG_FIELDS` = `maxObjects,maxSize,bucketOwner`; the rook operator
Deployment has `envFrom: null`; `ceph-bucket-fr01` `reclaimPolicy: Retain`; `PhysicalBackup.spec.schedule.onDemand`
exists in the CRD; `kafka-data-mimir-kafka-0` → `pvc-45fdaa3a-a0d4-4a8d-8d6c-04e7e084145d`, `Bound`; ConfigMap
`grafana` exists in ns `grafana`; the `ceph-osd-usage-critical` rule ends at `release.yaml:28182` with the
`databases` group starting at `:28183`; `mimir/release.yaml` `kafka.enabled` at `:27-28`,
`compactor_blocks_retention_period` at `:44`, `alertmanager:` at `:143`; `rook/release.yaml:31` is the
allow-list line; `phsysical-backup.yaml` lines 13–20 are the retention/compression block; `object-store.yaml`
is 17 lines with `wal.compression` at `:16-17`; the `mariadb-galera-backup` Secret's key
(`46AMW9X2JZAB7NMNIWEG`) does match RGW user `mariadb`, and that user does own the bucket.

## Things this plan could not verify

- **Whether Rook 1.20.3 applies `additionalConfig` changes to an already-`Bound` OBC in place.** Rook's own docs for v1.20.3 describe `bucketMaxSize` and `bucketLifecycle` but say nothing about the update path, and `docs/incidents/2026-07-18-mariadb-upgrade-and-rgw-access-denied.md:137` states plainly that `bucketOwner` is applied on `Provision()` only — so **assume path B**. Task 13 is a dedicated canary that answers this empirically before any high-value bucket is touched. Both outcomes have a written-out path.
- **Whether `aws s3 cp` will complete a 5.6 GiB server-side copy against this RGW** (it needs `UploadPartCopy`). Task 2 has a download-then-upload fallback.
- **Exact post-gzip backup size.** The 4.2× ratio in the comment at `phsysical-backup.yaml:14-19` was measured on a 28.6 GiB dump; the dump is now 34.6 GiB. Expect ~8 GiB and ~22 min, still inside `timeout: 2h`, but Task 6 measures it rather than assuming.
- **Hardware spare-slot availability.** `deviceFilter: "^sd[b-e]$"` claims four slots per node; whether a fifth physical slot exists was not checked (no `talosctl` access from here).

---

### Task 1: Capture the baseline (no changes)

**Files:**
- Create: `/tmp/claude-1000/-mnt-projects-oss-onelitefeather-Kubernetes-FLUX/6a7fbd35-4a60-4531-91e4-cf130f8aa8e8/scratchpad/ceph-baseline-before.txt` (scratch, not committed)

**Interfaces:**
- Consumes: nothing
- Produces: the before-numbers every later task's verification compares against

- [ ] **Step 1: Dump the baseline**

```bash
OUT=/tmp/claude-1000/-mnt-projects-oss-onelitefeather-Kubernetes-FLUX/6a7fbd35-4a60-4531-91e4-cf130f8aa8e8/scratchpad/ceph-baseline-before.txt
T="kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools --"
{
  date -u
  $T ceph df
  $T ceph osd df tree
  $T ceph health detail
  $T radosgw-admin bucket stats --rgw-realm=feather-s3
  $T rbd du -p feather-rbd
  $T ceph fs subvolume ls feather-cephfs csi
  kubectl get pv -o wide
  kubectl get pvc -A
} > "$OUT" 2>&1
grep -E "%RAW USED|RAW USED" "$OUT" | head -3
```

Expected: `%RAW USED` around **71–73%**. If it is already ≥80%, the cluster has moved faster than the audit projected — skip straight to Task 5 (the single largest reclamation), then come back and do Tasks 2–4.

- [ ] **Step 2: Confirm no backup Job is running**

```bash
kubectl -n mariadb-galera get jobs --sort-by=.metadata.creationTimestamp | tail -3
```

Expected: the newest Job shows `Complete   1/1`, not `0/1`. Backups run at 00/06/12/18 and take ~5 min. Do not start Task 3 while one is active — you would abort its in-flight multipart upload.

---

### Task 2: Preserve the pre-12.3 restore point (Decision Gate 1)

> **Requires credentials.** Uses the `mariadb-galera-backup` Secret in `rook-ceph-fr01`, which carries the `mariadb` RGW user's `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` (verified: its access key matches `rook-ceph-object-user-feather-s3-mariadb`).

**Files:** none (S3 operation)

**Interfaces:**
- Consumes: Decision Gate 1's answer
- Produces: `s3://mariadb-galera-backup/restore-points/…` outside the retention-managed prefix, unblocking Task 5

**Skip this task entirely if Decision Gate 1 was answered (b).** Record that decision in the PR body for Task 5.

- [ ] **Step 1: Confirm the object still exists**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket list --bucket=mariadb-galera-backup --rgw-realm=feather-s3 --max-entries=5000 \
  | grep -A3 'physicalbackup-20260718060000'
```

Expected: one entry, `"size": 6015…` (≈5.6 GiB), `"mtime": "2026-07-18T07:03:05…"`. If absent, stop — the restore point is already gone and Decision Gate 1 is moot.

- [ ] **Step 2: Server-side copy it out of the pruned prefix**

```bash
kubectl run s3-preserve --rm -i --restart=Never -n rook-ceph-fr01 \
  --image=amazon/aws-cli:2.17.60 \
  --overrides='{"spec":{"containers":[{"name":"s3","image":"amazon/aws-cli:2.17.60","command":["/bin/sh","-c","aws --endpoint-url=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80 --region=us-east-1 s3 cp s3://mariadb-galera-backup/feather-core-backups/physicalbackup-20260718060000.xb.bz2 s3://mariadb-galera-backup/restore-points/physicalbackup-20260718060000-mariadb-11.8.8.xb.bz2"],"envFrom":[{"secretRef":{"name":"mariadb-galera-backup"}}]}]}}'
```

Expected: `copy: s3://…/physicalbackup-20260718060000.xb.bz2 to s3://…/restore-points/physicalbackup-20260718060000-mariadb-11.8.8.xb.bz2` and pod exit code 0.

Fallback if the server-side copy fails (RGW `UploadPartCopy` issues on a >5 GiB object): replace the `command` with a download-then-upload through the pod's ephemeral storage — `aws … s3 cp s3://…/physicalbackup-20260718060000.xb.bz2 /tmp/rp.xb.bz2 && aws … s3 cp /tmp/rp.xb.bz2 s3://mariadb-galera-backup/restore-points/physicalbackup-20260718060000-mariadb-11.8.8.xb.bz2` — and schedule it onto a node with ≥10 GiB free ephemeral storage.

- [ ] **Step 3: Verify the copy**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket list --bucket=mariadb-galera-backup --rgw-realm=feather-s3 --max-entries=5000 \
  | grep -c 'restore-points/'
```

Expected: `1`.

**Rollback:** none needed — this only adds an object. If the copy is corrupt, it is deleted with `aws … s3 rm s3://mariadb-galera-backup/restore-points/…`.

**Gate:** do not start Task 5 until this task has either completed successfully or been explicitly waived.

---

### Task 3: Abort the 149 orphaned multipart uploads

> **Destructive.** Aborting a multipart upload discards its uploaded parts permanently. The parts targeted here are debris from uploads that already failed (`_multipart_feather-core-backups`, mtime 2026-07-23…26 — the InsufficientCapacity window; `_multipart_backups`, mtime 2026-06-09). The JMESPath filter below only touches uploads initiated more than 3 days ago, so a running backup can never be caught.

**Files:** none (S3 operation)

**Interfaces:**
- Consumes: Task 1 Step 2's confirmation that no backup Job is running
- Produces: ~2.02 GiB logical / ~6 GiB raw reclaimed; a clean multipart state for the lifecycle rule in Task 14 to maintain

- [ ] **Step 1: List what would be aborted (dry run)**

```bash
CUTOFF=$(date -u -d '3 days ago' +%Y-%m-%dT%H:%M:%SZ)
kubectl run s3-mpu-list --rm -i --restart=Never -n rook-ceph-fr01 \
  --image=amazon/aws-cli:2.17.60 \
  --overrides="{\"spec\":{\"containers\":[{\"name\":\"s3\",\"image\":\"amazon/aws-cli:2.17.60\",\"command\":[\"/bin/sh\",\"-c\",\"aws --endpoint-url=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80 --region=us-east-1 s3api list-multipart-uploads --bucket mariadb-galera-backup --query \\\"Uploads[?Initiated<='${CUTOFF}'].[Key,UploadId,Initiated]\\\" --output text | wc -l\"],\"envFrom\":[{\"secretRef\":{\"name\":\"mariadb-galera-backup\"}}]}]}}"
```

Expected: a count in the range **20–60** (149 *objects* in `radosgw-admin bucket list` are the individual parts; `list-multipart-uploads` counts *uploads*). Any non-zero number under ~100 is consistent. If it returns `0`, RGW has already reaped them — skip to Step 3 and confirm.

- [ ] **Step 2: Abort them**

```bash
CUTOFF=$(date -u -d '3 days ago' +%Y-%m-%dT%H:%M:%SZ)
EP=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80
SCRIPT="aws --endpoint-url=${EP} --region=us-east-1 s3api list-multipart-uploads --bucket mariadb-galera-backup --query \\\"Uploads[?Initiated<='${CUTOFF}'].[Key,UploadId]\\\" --output text | while read -r k u; do [ -n \\\"\\\$u\\\" ] && aws --endpoint-url=${EP} --region=us-east-1 s3api abort-multipart-upload --bucket mariadb-galera-backup --key \\\"\\\$k\\\" --upload-id \\\"\\\$u\\\" && echo aborted \\\"\\\$k\\\"; done"
kubectl run s3-mpu-abort --rm -i --restart=Never -n rook-ceph-fr01 \
  --image=amazon/aws-cli:2.17.60 \
  --overrides="{\"spec\":{\"containers\":[{\"name\":\"s3\",\"image\":\"amazon/aws-cli:2.17.60\",\"command\":[\"/bin/sh\",\"-c\",\"${SCRIPT}\"],\"envFrom\":[{\"secretRef\":{\"name\":\"mariadb-galera-backup\"}}]}]}}"
```

Expected: one `aborted <key>` line per upload, pod exit code 0.

- [ ] **Step 3: Verify the parts are gone**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket list --bucket=mariadb-galera-backup --rgw-realm=feather-s3 --max-entries=5000 \
  | grep -c '_multipart_'
```

Expected: `0` (was 149).

**Rollback:** none — aborted uploads cannot be resumed. This is acceptable because every one of them predates the last successful backup (`mariadb-galera-backup-20260803120000`, Complete 1/1) by more than a week.

---

### Task 4: Delete the retired `backups/` prefix and the canary object

> **Destructive and irreversible.** Deletes 65 objects / 128.81 GiB logical (386 GiB raw): `physicalbackup-20251010000000.xb.bz2` … `physicalbackup-20251108180000.xb.bz2`, all re-stamped mtime 2026-06-09, taken on **MariaDB 11.x before the 12.3.2 upgrade** and carried over during a cluster rebuild. `mariadb-operator` only lists objects under `spec.storage.s3.prefix` (`feather-core-backups`), so nothing will ever prune these. Also deletes `canary-owner-put-test.txt`, left over from the 2026-07-18 RGW remediation.

**Files:** none (S3 operation)

**Interfaces:**
- Consumes: Task 2 (the restore point you chose to keep is under `feather-core-backups/`, not `backups/` — this task does not touch it)
- Produces: 386 GiB raw reclaimed

- [ ] **Step 1: Confirm nothing references the old prefix**

```bash
grep -rn "backups/" infrastructure/clusters/feather-core/configs/mariadb-galera/ ; \
grep -n "prefix" infrastructure/clusters/feather-core/configs/mariadb-galera/phsysical-backup.yaml ; \
kubectl get physicalbackup -A -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.storage.s3.prefix}{"\n"}{end}'
```

Expected (verified 2026-08-03): the first `grep -rn "backups/"` returns exactly **one** hit and it is a comment —
`phsysical-backup.yaml:19:  # than gzip (~3.4TiB vs ~816GiB at 4 backups/day * 30-day maxRetention).` — i.e. no
manifest writes to the bare `backups/` prefix. The second grep returns `24:      prefix: feather-core-backups`.
The `kubectl get physicalbackup` line returns `mariadb-galera-backup<TAB>feather-core-backups`.
If any manifest or live CR references the bare `backups` prefix, **stop** and re-open Decision Gate 1.

- [ ] **Step 2: Dry-run the delete**

```bash
EP=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80
kubectl run s3-rm-dry --rm -i --restart=Never -n rook-ceph-fr01 \
  --image=amazon/aws-cli:2.17.60 \
  --overrides="{\"spec\":{\"containers\":[{\"name\":\"s3\",\"image\":\"amazon/aws-cli:2.17.60\",\"command\":[\"/bin/sh\",\"-c\",\"aws --endpoint-url=${EP} --region=us-east-1 s3 rm s3://mariadb-galera-backup/backups/ --recursive --dryrun | tee /dev/stderr | wc -l\"],\"envFrom\":[{\"secretRef\":{\"name\":\"mariadb-galera-backup\"}}]}]}}"
```

Expected: exactly **65** `(dryrun) delete: s3://mariadb-galera-backup/backups/physicalbackup-2025…` lines, and **zero** lines containing `feather-core-backups` or `restore-points`. If any line mentions those, **stop** — the prefix match is wrong.

- [ ] **Step 3: Delete for real**

```bash
EP=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80
kubectl run s3-rm --rm -i --restart=Never -n rook-ceph-fr01 \
  --image=amazon/aws-cli:2.17.60 \
  --overrides="{\"spec\":{\"containers\":[{\"name\":\"s3\",\"image\":\"amazon/aws-cli:2.17.60\",\"command\":[\"/bin/sh\",\"-c\",\"aws --endpoint-url=${EP} --region=us-east-1 s3 rm s3://mariadb-galera-backup/backups/ --recursive && aws --endpoint-url=${EP} --region=us-east-1 s3 rm s3://mariadb-galera-backup/canary-owner-put-test.txt\"],\"envFrom\":[{\"secretRef\":{\"name\":\"mariadb-galera-backup\"}}]}]}}"
```

- [ ] **Step 4: Verify**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket stats --bucket=mariadb-galera-backup --rgw-realm=feather-s3 \
  | grep -E '"num_objects"|"size_actual"'
```

Expected: `num_objects` around **72** (71 live backups + 1 restore-point copy; was 266 before Tasks 3–4), `size_actual` around **1,925,000,000,000** bytes (≈1793 GiB — the 128.81 GiB `backups/` prefix gone, the 5.6 GiB restore point added).

- [ ] **Step 5: Confirm the space actually returns to the pool**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | grep -E "TOTAL|buckets.data"
```

Expected: `%RAW USED` down by roughly **3 points** (386 GiB of 12 TiB). RGW garbage collection is asynchronous — if the number has not moved after 10 minutes, force it: `kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin gc process --include-all --rgw-realm=feather-s3`.

**Rollback:** none. The objects are gone. This is why Step 1 and the dry-run in Step 2 are mandatory.

---

### Task 5: Shorten MariaDB backup retention and enable gzip (PR 1)

**Files:**
- Modify: `infrastructure/clusters/feather-core/configs/mariadb-galera/phsysical-backup.yaml` (lines 13–20)

**Interfaces:**
- Consumes: Decision Gates 1, 2, 3; Task 2's preserved restore point
- Produces: ~2.31 TiB raw reclaimed on the next backup Job, and a steady state of ~0.67 TiB raw instead of ~5.2 TiB

This is the single highest-value change in the plan. `maxRetention: 720h` cannot delete anything until the oldest object under `feather-core-backups/` turns 30 days old on ~2026-08-12 — after the cluster fills.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b fix/mariadb-backup-retention-and-compression
```

- [ ] **Step 2: Change retention and compression**

In `infrastructure/clusters/feather-core/configs/mariadb-galera/phsysical-backup.yaml`, replace lines 13–20:

```yaml
  maxRetention: 720h # 30 days
  # No compression, deliberately: the operator's pipeline is sequential
  # (dump -> compress -> upload, not streamed), so compression algorithm was
  # the entire bottleneck. Measured on this cluster: bzip2 ~60-67min/run,
  # gzip 22m12s/6.8GiB, none 4m18s/28.6GiB. "none" was chosen to hit a
  # 5-15min backup window; trade-off is ~4.2x more S3 storage per backup
  # than gzip (~3.4TiB vs ~816GiB at 4 backups/day * 30-day maxRetention).
  compression: none
```

with:

```yaml
  maxRetention: 168h # 7 days
  # gzip, not none: at 4 backups/day of a ~34.6GiB dump, uncompressed backups
  # were 75% of all S3 data and drove the cluster to 71% raw. Measured here:
  # bzip2 ~60-67min/run, gzip 22m12s/6.8GiB, none 4m18s/28.6GiB. gzip's ~22min
  # is still well inside the 2h timeout below.
  compression: gzip
```

Do **not** change `cron: "0 */6 * * *"` at line 10 (Decision Gate 3 answer: keep 6-hourly). Do not change `prefix: feather-core-backups` at line 24 — changing the prefix is what stranded 128 GiB last time.

- [ ] **Step 3: Render and verify**

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs/mariadb-galera | grep -E "maxRetention|compression|cron|prefix"
```

Expected:

```yaml
  compression: gzip
  maxRetention: 168h
    cron: 0 */6 * * *
      prefix: feather-core-backups
```

(That is the real order kustomize emits — verified against the current file, which renders
`compression: none` / `maxRetention: 720h` / `cron: 0 */6 * * *` / `prefix: feather-core-backups` in exactly
these positions. If the order differs, you edited the wrong document in the overlay.)

- [ ] **Step 4: Validate**

Run: `./scripts/validate.sh`

Expected: exits `0`; the `configs` group reports `Invalid: 0, Errors: 0`.

- [ ] **Step 5: Commit and open the PR**

```bash
git add infrastructure/clusters/feather-core/configs/mariadb-galera/phsysical-backup.yaml
git commit -m "fix(mariadb): cut physical backup retention to 7d and enable gzip"
git push -u origin fix/mariadb-backup-retention-and-compression
gh pr create --title "fix(mariadb): cut physical backup retention to 7d and enable gzip" --body "$(cat <<'EOF'
## Summary
- maxRetention 720h -> 168h and compression none -> gzip in phsysical-backup.yaml
- mariadb-galera-backup is 1921 GiB = 75% of all S3 data; 720h retention could not prune
  anything before ~2026-08-12, while the cluster was projected to hit full_ratio ~2026-08-10
- Frees ~2.31 TiB raw on the next backup Job; steady state drops from ~5.2 TiB raw to ~0.67 TiB raw

## Deliberate data loss
Backups older than 7 days are deleted permanently. The pre-MariaDB-12.3 restore point
(physicalbackup-20260718060000, MariaDB 11.8.8) was copied to the restore-points/ prefix first,
which maxRetention does not scan.

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] Merge, reconcile configs once, force one backup Job, confirm 42 old objects pruned and ceph df drops
EOF
)"
```

Merging is a human decision — do not merge automatically.

**Rollback:** revert the merge commit. Retention returns to 720h. Note this does **not** restore deleted backups — the prune is irreversible the moment the first Job runs after merge.

---

### Task 6: Merge PR 1, force one backup, verify the prune (health gate)

**Files:** none (operational)

**Interfaces:**
- Consumes: merged PR 1
- Produces: the confirmed-drained cluster that Tasks 8–17 and the `offsite-backups-and-disaster-recovery` theme both assume

- [ ] **Step 1: Merge PR 1, then reconcile once**

```bash
flux reconcile kustomization configs --with-source
```

- [ ] **Step 2: Confirm the live CR picked up the change**

```bash
kubectl -n mariadb-galera get physicalbackup mariadb-galera-backup \
  -o jsonpath='{.spec.maxRetention}{"  "}{.spec.compression}{"\n"}'
```

Expected: `168h  gzip`. (The CRD stores `maxRetention` as the literal string you wrote — verified live today: it
currently prints `720h`, not `720h0m0s`. If you see `168h0m0s`, that is fine too, but `168h` is what to expect.)
If it still says `720h`, the `configs` Kustomization has not applied — check `flux get kustomizations -A` and wait; do not re-reconcile in a loop.

- [ ] **Step 3: Force one backup Job instead of waiting for the cron**

```bash
kubectl patch physicalbackup mariadb-galera-backup -n mariadb-galera --type merge \
  -p "{\"spec\":{\"schedule\":{\"onDemand\":\"$(date -u +%Y%m%d%H%M%S)\"}}}"
```

- [ ] **Step 4: Watch the Job to completion**

```bash
kubectl -n mariadb-galera get jobs --sort-by=.metadata.creationTimestamp | tail -2
```

Expected: a new Job reaching `Complete   1/1`. **Expect ~22 minutes, not ~5** — that is gzip working. If it is still running at 2h it will be killed by `timeout: 2h`; if that happens, revert PR 1's `compression: gzip` (keep the `168h`) and re-open Decision Gate 3.

Note: `kubectl get physicalbackup` has a known stale-status bug (`docs/incidents/2026-07-18-…md`) — trust the Job, not the CR's `status.conditions`.

- [ ] **Step 5: Confirm the prune happened**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket list --bucket=mariadb-galera-backup --rgw-realm=feather-s3 --max-entries=5000 \
  | grep -c 'feather-core-backups/'
```

Expected: around **29** objects (was 71; 42 were older than 7 days). The newest object's name should end `.xb.gz`, confirming gzip: re-run with `| grep '.xb.gz' | head -1`.

- [ ] **Step 6: Confirm the capacity actually came back**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | grep -E "TOTAL|buckets.data"
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph osd df tree | tail -3
```

Expected: `%RAW USED` down to roughly **48–52%** (from 71.26%, having reclaimed ~2.31 TiB from this task plus ~0.39 TiB from Tasks 3–4). RGW GC is asynchronous; if it has not moved within 30 minutes, run `radosgw-admin gc process --include-all --rgw-realm=feather-s3`.

**Rollback:** the git change reverts with `git revert` of PR 1's merge commit (retention returns to 720h,
compression to `none`). The *data* deleted by the first post-merge Job does not come back — that is the
deliberate, gated data loss described in Task 5. If the Job fails or is killed by `timeout: 2h`, revert only
the `compression: gzip` half and keep `maxRetention: 168h`: the retention prune runs *before* the dump, so
7-day retention still takes effect on the next uncompressed run.

**Gate:** do not start Task 8, and tell the `offsite-backups-and-disaster-recovery` theme it may not start creating VolumeSnapshots, until Step 6 shows `%RAW USED` below **60%** and `ceph health detail` is `HEALTH_OK`.

---

### Task 7: Add CNPG barman retention and base-backup compression (PR 2)

**Files:**
- Modify: `infrastructure/clusters/feather-core/configs/postgresql/object-store.yaml` (currently 17 lines)

**Interfaces:**
- Consumes: Decision Gate 4's answer
- Produces: a bounded Postgres backup store (currently 140.34 GiB / 41,463 objects growing since 2026-06-10 with nothing to prune it)

> Only base backups are uncompressed — `wal.compression: gzip` at `:16-17` already compresses WAL. The audit's phrasing "every WAL segment, uncompressed" is wrong; do not act on it.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b fix/cnpg-objectstore-retention
```

- [ ] **Step 2: Rewrite the file**

Replace the whole of `infrastructure/clusters/feather-core/configs/postgresql/object-store.yaml` with:

```yaml
apiVersion: barmancloud.cnpg.io/v1
kind: ObjectStore
metadata:
  name: s3-store
spec:
  # Unbounded before 2026-08-03: 140 GiB / 41k objects back to 2026-06-10.
  retentionPolicy: "30d"
  configuration:
    destinationPath: s3://feather-core-cluster-pg-backup/backups-fr01
    endpointURL: http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80
    s3Credentials:
      accessKeyId:
        name: cnpg-backup
        key: access-key-id
      secretAccessKey:
        name: cnpg-backup
        key: secret-access-key
    data:
      compression: gzip
    wal:
      compression: gzip
```

(`retentionPolicy` is `XXu` with `u` in `[dwm]` per `kubectl explain objectstore.spec.retentionPolicy`; `data.compression` accepts `bzip2|gzip|snappy` per `kubectl explain objectstore.spec.configuration.data`. Substitute Decision Gate 4's answer if it was not `30d`.)

- [ ] **Step 3: Render and validate**

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs/postgresql | grep -A2 -E "retentionPolicy|data:|wal:"
./scripts/validate.sh
```

Expected: `retentionPolicy: 30d`, a `data:` block with `compression: gzip`, a `wal:` block with `compression: gzip`; `validate.sh` exits `0`.

- [ ] **Step 4: Commit and open the PR**

```bash
git add infrastructure/clusters/feather-core/configs/postgresql/object-store.yaml
git commit -m "fix(postgresql): add barman retention policy and base-backup compression"
git push -u origin fix/cnpg-objectstore-retention
gh pr create --title "fix(postgresql): add barman retention policy and base-backup compression" --body "$(cat <<'EOF'
## Summary
- Adds spec.retentionPolicy: "30d" (there was none) and spec.configuration.data.compression: gzip
- feather-core-cluster-pg-backup is 140 GiB / 41,463 objects with no retention; WAL was already gzip,
  base backups were not

## Deliberate data loss
Base backups and WALs older than 30 days are deleted on the next barman maintenance pass.
CNPG Backup objects older than that will remain "completed" in the API but no longer be restorable.

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] Merge, reconcile configs, confirm the live ObjectStore shows retentionPolicy 30d
- [ ] After the next daily backup (02:00), confirm bucket object count drops
EOF
)"
```

- [ ] **Step 5: After merge, reconcile once and verify**

```bash
flux reconcile kustomization configs --with-source
kubectl -n cnpg-system get objectstore s3-store -o jsonpath='{.spec.retentionPolicy}{"  "}{.spec.configuration.data.compression}{"\n"}'
```

Expected: `30d  gzip`.

- [ ] **Step 6: Verify after the next scheduled backup (02:00, ScheduledBackup `feather-core-cluster-pg-daily`)**

```bash
kubectl -n cnpg-system get cluster feather-core-cluster-pg -o jsonpath='{range .status.conditions[*]}{.type}={.status} {end}{"\n"}'
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket stats --bucket=feather-core-cluster-pg-backup --rgw-realm=feather-s3 | grep num_objects
```

Expected: `ContinuousArchiving=True LastBackupSucceeded=True`, and `num_objects` materially below 41,463.

**Rollback:** revert the merge commit — `retentionPolicy` disappears and pruning stops. Already-deleted backups do not come back. If `ContinuousArchiving` flips to `False` after this change, revert immediately: a broken WAL archive is a worse problem than an unbounded bucket.

---

### Task 8: Reap the unambiguously dead Released PVs (tier 1)

> **Destructive and irreversible.** Flipping `persistentVolumeReclaimPolicy` to `Delete` and then deleting the PV makes ceph-csi remove the underlying RBD image / CephFS subvolume. Every PV in this list has a `claimRef` to a PVC that does not exist — three of them point at namespaces (`cnpg-demo`, `redis`) that have themselves been deleted.

**Files:** none (cluster operation)

**Interfaces:**
- Consumes: Task 6's gate
- Produces: ~18 GiB RBD + ~76 GiB CephFS reclaimed (~283 GiB raw)

- [ ] **Step 1: Re-confirm every claimRef is dead**

```bash
for pv in pvc-15818fc7-e37d-446b-ab39-8bb98bfb76f2 pvc-190c574e-c5a6-4c8c-833d-3613ef1333fe \
          pvc-7e3556be-aecd-4bd8-be56-cb8c937dbdc0 pvc-a7d22273-04eb-432d-8ce1-3654a42a40be \
          pvc-e0668d65-d45b-4e5a-81c1-894445ad9ed1 pvc-ae9225c3-295a-481b-ab4c-6be470c6b245 \
          pvc-8f4f07e0-3e70-4a15-a59f-af690c62494c pvc-be52cec6-6b9a-487c-8303-405ef44f871d \
          pvc-febe034c-85de-42f2-a423-0c4f2c321d65 pvc-9a19fb0a-951b-4aa3-8eb5-46f46bb225b9 \
          pvc-5d678462-87a0-45ba-8747-2ac9c0d81720 pvc-5af558cb-626e-418b-b6a0-ff8b1c437622 \
          pvc-7e188116-fbee-4d84-ad58-8e2a2c3d397c pvc-5fd30151-f15d-430c-8479-604063ff2006 \
          pvc-51b06713-fb2f-4d47-b583-4bc393cbe9ae pvc-97574624-faad-4b8b-bb94-edd35e4570b5 \
          pvc-3e02b69b-db31-4e8e-b89e-fce2e2f605ad pvc-b606f52f-8a64-43f2-9683-72c64263f76c \
          pvc-cb2b6f5a-4a94-402b-946b-316e2c9b688b pvc-94709e73-31ae-4ffb-a912-34a3dac102ef \
          pvc-9a850563-e411-4bcd-8eb3-d40d1d27a51d ; do
  ns=$(kubectl get pv "$pv" -o jsonpath='{.spec.claimRef.namespace}')
  n=$(kubectl get pv "$pv" -o jsonpath='{.spec.claimRef.name}')
  ph=$(kubectl get pv "$pv" -o jsonpath='{.status.phase}')
  live=$(kubectl -n "$ns" get pvc "$n" --no-headers 2>/dev/null | wc -l)
  echo "$pv $ph $ns/$n live_pvc=$live"
done
```

Expected: every line ends `Released … live_pvc=0`. **If any line shows `live_pvc=1` or a phase other than `Released`, remove that PV from the list and investigate — do not proceed with it.**

The 21 PVs above are, by claim: `default/csi-migration-verify-rbd`, `default/test-rbd-pvc`, `default/ceph-speedtest-pvc`, `default/test-cephfs-pvc` (×2), `default/csi-migration-verify-cephfs`, `cnpg-demo/pg-demo-{1,2,3}` (namespace deleted), `redis/redis-data-redis-node-{0,1,2}` + `redis/sentinel-data-redis-node-{0,1,2}` (namespace deleted, superseded by Dragonfly), `n8n/redis-data-n8n-redis-master-0`, `grafana/storage-loki-0` (×2, superseded by `data-loki-ingester-{0,1,2}`), `cnpg-system/feather-core-cluster-pg-3` (live cluster runs pg-1/2/4), `mariadb-galera/mariadb-galera-backup-staging` (×2 CephFS 75Gi, superseded by the `emptyDir` staging at `phsysical-backup.yaml:40-43`).

- [ ] **Step 2: Record what will be freed**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- rbd du -p feather-rbd | tail -1
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  ceph fs subvolume info feather-cephfs csi-vol-ffa3bc09-8ec2-4f27-95b6-454f57b8cfca csi | grep bytes_used
```

Expected: the CephFS subvolume backing `pvc-9a850563` reports `"bytes_used": 80530…` (≈75.00 GiB) — the single largest item in this task.

- [ ] **Step 3: Flip the reclaim policy, then delete, one PV at a time**

Reuse the exact same `for pv in …` list from Step 1 (all 21 UUIDs; re-verified live on review — every one is
`Released` with a claimRef to a PVC that does not exist). **Drop any UUID whose Step 1 line did not end
`live_pvc=0`.** The loop below re-checks that inline so a stale list cannot delete a live volume:

```bash
for pv in pvc-15818fc7-e37d-446b-ab39-8bb98bfb76f2 pvc-190c574e-c5a6-4c8c-833d-3613ef1333fe \
          pvc-7e3556be-aecd-4bd8-be56-cb8c937dbdc0 pvc-a7d22273-04eb-432d-8ce1-3654a42a40be \
          pvc-e0668d65-d45b-4e5a-81c1-894445ad9ed1 pvc-ae9225c3-295a-481b-ab4c-6be470c6b245 \
          pvc-8f4f07e0-3e70-4a15-a59f-af690c62494c pvc-be52cec6-6b9a-487c-8303-405ef44f871d \
          pvc-febe034c-85de-42f2-a423-0c4f2c321d65 pvc-9a19fb0a-951b-4aa3-8eb5-46f46bb225b9 \
          pvc-5d678462-87a0-45ba-8747-2ac9c0d81720 pvc-5af558cb-626e-418b-b6a0-ff8b1c437622 \
          pvc-7e188116-fbee-4d84-ad58-8e2a2c3d397c pvc-5fd30151-f15d-430c-8479-604063ff2006 \
          pvc-51b06713-fb2f-4d47-b583-4bc393cbe9ae pvc-97574624-faad-4b8b-bb94-edd35e4570b5 \
          pvc-3e02b69b-db31-4e8e-b89e-fce2e2f605ad pvc-b606f52f-8a64-43f2-9683-72c64263f76c \
          pvc-cb2b6f5a-4a94-402b-946b-316e2c9b688b pvc-94709e73-31ae-4ffb-a912-34a3dac102ef \
          pvc-9a850563-e411-4bcd-8eb3-d40d1d27a51d ; do
  ns=$(kubectl get pv "$pv" -o jsonpath='{.spec.claimRef.namespace}')
  n=$(kubectl get pv "$pv" -o jsonpath='{.spec.claimRef.name}')
  ph=$(kubectl get pv "$pv" -o jsonpath='{.status.phase}')
  if [ "$ph" != "Released" ] || kubectl -n "$ns" get pvc "$n" >/dev/null 2>&1 ; then
    echo "SKIP $pv ($ph, $ns/$n) -- not safe, investigate"; continue
  fi
  kubectl patch pv "$pv" -p '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}'
  kubectl delete pv "$pv" --wait=true --timeout=120s
done
```

Expected: 21 `persistentvolume/… patched` + `persistentvolume "…" deleted` pairs and **zero** `SKIP` lines. Any
`SKIP` line means the cluster has changed since Step 1 — stop and investigate that PV before continuing.

- [ ] **Step 4: Verify no PV is stuck**

```bash
kubectl get pv --no-headers | awk '{print $5}' | sort | uniq -c
kubectl get pv --no-headers | grep -c Terminating
```

Expected: `Released` count down from 42 to **21**; `Terminating` count `0`. If a PV hangs in `Terminating`, its CSI `DeleteVolume` failed — check `kubectl -n rook-ceph logs -l app=csi-rbdplugin-provisioner -c csi-rbdplugin --tail=100`, then reclaim manually (`rbd rm -p feather-rbd <imageName>` or `ceph fs subvolume rm feather-cephfs <subvolumeName> csi`) and remove the finalizer.

- [ ] **Step 5: Verify the images actually went away**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- sh -c 'rbd ls -p feather-rbd | wc -l'
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph fs subvolume ls feather-cephfs csi
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | grep -E "feather-rbd|cephfs-data0"
```

Expected: RBD image count down from 74 to **58**; the CephFS subvolume list down from 6 entries to **1** (`csi-vol-a5529085-…`, handled in Task 10); `feather-cephfs-data0` STORED down from ~76 GiB to ~**0.6 GiB** (the one subvolume Task 10 removes).

(Arithmetic, verified live 2026-08-03: of the 21 PVs in this task, **16** are `rook-ceph.rbd.csi.ceph.com` and **5** are `rook-ceph.cephfs.csi.ceph.com`. 74 − 16 = 58. All 37 Bound PVs are RBD, so after Task 9 removes its 21 RBD PVs the count lands on 37.)

**Rollback:** none. Step 1's `live_pvc=0` check is the safety mechanism.

---

### Task 9: Reap superseded Galera / CNPG PV generations (tier 2)

> **Destructive and irreversible.** These 21 PVs have `claimRef`s whose names *do* match live PVCs — they are older generations left behind by StatefulSet re-provisions. Deleting them destroys point-in-time copies of Galera and Postgres data that could otherwise be mounted read-only in an emergency.

**Files:** none (cluster operation)

**Interfaces:**
- Consumes: Task 8 complete; a successful post-gzip backup from Task 6
- Produces: ~110 GiB RBD reclaimed (~330 GiB raw)

- [ ] **Step 1: Gate — a current backup must exist**

```bash
kubectl -n mariadb-galera get jobs --sort-by=.metadata.creationTimestamp | tail -1
kubectl -n mariadb-galera get mariadb mariadb-galera -o jsonpath='{.status.conditions}{"\n"}'
```

Expected: the most recent Job is `Complete   1/1` (this is the gzip backup from Task 6), and the MariaDB CR reports `Ready=True`. **If the newest Job is not Complete, stop.** These PVs are the only other copy of Galera's data on this cluster.

- [ ] **Step 2: Verify each PV is a superseded generation, not the live one**

```bash
kubectl get pv -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
live={}; rel=[]
for i in d["items"]:
    cr=i["spec"].get("claimRef",{}); key=(cr.get("namespace"),cr.get("name"))
    if i["status"]["phase"]=="Bound": live[key]=i["metadata"]["name"]
    elif i["status"]["phase"]=="Released": rel.append((key,i["metadata"]["name"],i["metadata"]["creationTimestamp"]))
for key,name,ts in sorted(rel,key=lambda r:r[2]):
    print(name, ts, "%s/%s"%key, "LIVE_PV=%s"%live.get(key,"NONE"))
'
```

Expected: 21 rows, all with claims under `mariadb-galera/` or `cnpg-system/feather-core-cluster-pg-1`, each showing a `LIVE_PV=pvc-…` that is a **different** PV name from the row's own. Live Galera PVs were created 2026-07-12T11:49–23:21; every row here is older. Any row showing `LIVE_PV=NONE` belonged in Task 8 — handle it there instead.

- [ ] **Step 3: Delete, largest first, checking `ceph df` as you go**

```bash
for pv in pvc-89e3d24a-40f2-4ab1-9b51-bcb007ca605a pvc-94a4e401-73fd-407e-87ef-2080af7d0534 \
          pvc-a0f577f1-ed60-4f5f-9c66-85f79798b6a5 pvc-c6da4f37-22fb-4a80-aeb5-db3b566d3f94 \
          pvc-5cc57391-51e6-497f-aaff-538966e8f82f pvc-dacd0891-bd47-4f5b-9da0-bcf1ed59395b ; do
  kubectl patch pv "$pv" -p '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}'
  kubectl delete pv "$pv" --wait=true --timeout=120s
done
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | grep feather-rbd
```

Those six are 25.35, 25.13, 24.65, 18.69, 7.27 and 6.89 GiB — 108 of the 110 GiB. Expected after: `feather-rbd` STORED down from 278 GiB to roughly 170 GiB.

- [ ] **Step 4: Delete the remaining 15 small generations**

```bash
for pv in pvc-db16e7d8-01c5-4b39-95da-0961d8904596 pvc-93580c5d-7b66-4e4e-9d3f-448e95a75313 \
          pvc-2ab6b9c5-3432-4476-b545-eb90171f293a pvc-63473b64-ed53-43f8-b3f1-0828c9666d68 \
          pvc-ff3afe0b-e4e7-4415-8337-a5010c50ff9e pvc-3fa2d434-2bcc-4f8d-9424-499df9b27534 \
          pvc-01fdafcf-7fd8-452c-9478-406b138fce4a pvc-4e182022-1270-4304-8f08-679bca98a4b5 \
          pvc-7febe2aa-9902-4143-afbe-81c87d89d07f pvc-31822010-239a-4e90-bd14-77605753ee50 \
          pvc-e4867bcf-9388-413f-a6cc-ad6c282294dd pvc-fc22090e-6847-4c53-b439-0697fc8c5753 \
          pvc-850d2f5d-e574-453a-af7d-1a58082589cc pvc-d2bec614-0476-4564-ae71-f30a831eca65 \
          pvc-f8156ad8-94a0-4be2-bf5b-be6b53180ef7 ; do
  kubectl patch pv "$pv" -p '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}'
  kubectl delete pv "$pv" --wait=true --timeout=120s
done
```

- [ ] **Step 5: Verify**

```bash
kubectl get pv --no-headers | awk '{print $5}' | sort | uniq -c
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- sh -c 'rbd ls -p feather-rbd | wc -l'
kubectl -n mariadb-galera get mariadb mariadb-galera -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}'
kubectl -n cnpg-system get cluster feather-core-cluster-pg -o jsonpath='{.status.readyInstances}{"\n"}'
```

Expected: `Released` count `0`, `Bound` `37`; RBD image count **37** (one per Bound PVC, down from 74); MariaDB `Ready=True`; CNPG `readyInstances` matches its configured instance count.

**Rollback:** none. Steps 1 and 2 are the safety mechanism.

---

### Task 10: Remove the fully-orphaned CephFS subvolume

> **Destructive.** `csi-vol-a5529085-97b8-4280-901d-fb496f26bf33` (created 2026-06-10 10:30, 75Gi quota, 0.64 GiB used) has **no** PersistentVolume, no PVC and no owner — a leaked subvolume from an earlier `mariadb-galera-backup-staging` generation. ceph-csi will never reclaim it because nothing in Kubernetes references it.

**Files:** none (cluster operation)

- [ ] **Step 1: Prove nothing references it**

```bash
kubectl get pv  -o json | { grep -c 'csi-vol-a5529085-97b8-4280-901d-fb496f26bf33' || true; }
kubectl get pvc -A -o json | { grep -c 'csi-vol-a5529085-97b8-4280-901d-fb496f26bf33' || true; }
```

Expected: `0` and `0`. **If either is non-zero, stop.** (`grep -c` exits 1 on zero matches, which is the *good*
outcome here — the `|| true` keeps that from looking like a command failure under `set -e`.)

- [ ] **Step 2: Remove it**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  ceph fs subvolume rm feather-cephfs csi-vol-a5529085-97b8-4280-901d-fb496f26bf33 csi
```

- [ ] **Step 3: Verify**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph fs subvolume ls feather-cephfs csi
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | grep cephfs-data0
```

Expected: `[]` (empty list), and `feather-cephfs-data0` STORED near `0 B`.

Note the accounting: the pool was 76 GiB / 228 GiB raw *before Task 8*. Task 8 already removed the 75 GiB
`mariadb-galera-backup-staging` subvolume, so entering this task the pool should read ~0.6 GiB, and this task
takes it to ~0. Do not expect a 228 GiB drop here — that credit was booked in Task 8.

**Rollback:** none.

---

### Task 11: Delete the orphaned `kafka-data-mimir-kafka-0` PVC

> **Destructive.** 10Gi PVC in `grafana`, Bound since 2026-06-10, holding **7.91 GiB** of real RBD data (23.7 GiB raw). Mimir has `kafka.enabled: false` at `apps/clusters/feathre-core/monitoring/mimir/release.yaml:27-28` and there is no `mimir-kafka` pod or StatefulSet — this is a leftover `volumeClaimTemplate` volume from before Kafka was disabled.

**Files:** none (cluster operation)

- [ ] **Step 1: Confirm nothing consumes it**

```bash
kubectl -n grafana get sts,pod | grep -i kafka
kubectl -n grafana get pvc kafka-data-mimir-kafka-0 -o jsonpath='{.status.phase}{"\n"}'
grep -n -A1 "^    kafka:" apps/clusters/feathre-core/monitoring/mimir/release.yaml
```

Expected: no kafka StatefulSet or pod; PVC `Bound`; the manifest shows `kafka:` / `enabled: false`.

- [ ] **Step 2: Delete the PVC, then reclaim the PV**

```bash
kubectl -n grafana delete pvc kafka-data-mimir-kafka-0
kubectl patch pv pvc-45fdaa3a-a0d4-4a8d-8d6c-04e7e084145d -p '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}'
kubectl delete pv pvc-45fdaa3a-a0d4-4a8d-8d6c-04e7e084145d --wait=true --timeout=120s
```

(Re-derive the PV name if it differs: `kubectl -n grafana get pvc kafka-data-mimir-kafka-0 -o jsonpath='{.spec.volumeName}'` **before** deleting the PVC.)

- [ ] **Step 3: Verify**

```bash
kubectl -n grafana get pvc | { grep -c kafka || true; }
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- sh -c 'rbd ls -p feather-rbd | grep -c csi-vol-e58127ba-41ec-4432-9489-1f3ff1411b68 || true'
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- sh -c 'rbd ls -p feather-rbd | wc -l'
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | grep -E "TOTAL"
```

Expected: `0`, `0`, RBD image count **36** (37 after Task 9, minus this one), and `%RAW USED` at roughly **44%**.

(The `|| true` matters: `grep -c` exits 1 on zero matches, and inside `kubectl exec` that surfaces as a
non-zero exit which reads like a failure when it is actually the success case.)

**Rollback:** none.

---

### Task 12: Allow `bucketMaxSize` and `bucketLifecycle` on OBCs (PR 3a, commit 1)

> **PR structure — read before branching.** The quota/lifecycle work is **two** PRs, not one, because Task 13's
> canary result is only observable on a *merged* revision and Task 14 branches on that result:
> - **PR 3a** = Task 12 (allow-list) + Task 13 Step 1–2 (canary OBC). Branch `feat/rook-obc-quotas-and-lifecycle`.
>   Merge it, then run Task 12 Step 4–5 and Task 13 Step 3–4.
> - **PR 3b** = Task 14 (rollout to the other 16 OBCs). Branch `feat/rook-obc-quota-rollout`, cut from `main`
>   *after* PR 3a is merged and the path A/B answer is recorded.
>
> Do not put Task 14's commit on the PR 3a branch — that branch is already merged by the time you know which
> path to take.

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook/release.yaml:31`

**Interfaces:**
- Consumes: Task 6's gate (quotas must be set *after* the prune, or they would immediately block a bucket that is over its new quota)
- Produces: the operator allow-list that Tasks 13–14 depend on

Rook v1.20.3 supports seven `additionalConfig` keys: `maxObjects`, `maxSize` (both are quotas on the *user* account), `bucketMaxObjects`, `bucketMaxSize` (quotas on the *individual bucket*), `bucketPolicy`, `bucketLifecycle`, `bucketOwner`. Only the first two are enabled by default. The repo currently allows `maxObjects,maxSize,bucketOwner`.

**Use `bucketMaxSize`, not `maxSize`.** `maxSize` quotas the RGW user, and three users own multiple buckets (`mimir` owns `mimir-blocks`/`mimir-ruler`/`mimir-alertmanager`; `reposilite` owns three; `reposilite-public` owns two), so a user quota would not do what the audit intended.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/rook-obc-quotas-and-lifecycle
```

- [ ] **Step 2: Extend the allow-list**

In `infrastructure/clusters/feather-core/rook/release.yaml`, change line 31 from:

```yaml
    obcAllowAdditionalConfigFields: "maxObjects,maxSize,bucketOwner"
```

to:

```yaml
    obcAllowAdditionalConfigFields: "maxObjects,maxSize,bucketOwner,bucketMaxObjects,bucketMaxSize,bucketLifecycle"
```

No other line in this file changes. Do **not** add `bucketPolicy` — nothing in this plan needs it and it is the riskiest of the set.

- [ ] **Step 3: Render, validate, commit**

```bash
kubectl kustomize infrastructure/clusters/feather-core/rook | grep obcAllowAdditionalConfigFields
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook/release.yaml
git commit -m "feat(rook): allow bucketMaxSize and bucketLifecycle on object bucket claims"
```

Expected: the grep prints the new six-value string; `validate.sh` exits `0`.

**Do not push or open the PR yet** — Task 13 Step 2 adds the second commit to this same branch and pushes both.

- [ ] **Step 4: After PR 3a is merged, reconcile once and restart the operator**

```bash
flux reconcile kustomization rook --with-source
flux reconcile helmrelease rook-ceph -n rook-ceph
kubectl -n rook-ceph get cm rook-ceph-operator-config -o jsonpath='{.data.ROOK_OBC_ALLOW_ADDITIONAL_CONFIG_FIELDS}{"\n"}'
kubectl -n rook-ceph rollout restart deploy/rook-ceph-operator
kubectl -n rook-ceph rollout status deploy/rook-ceph-operator --timeout=180s
```

Expected: the ConfigMap prints `maxObjects,maxSize,bucketOwner,bucketMaxObjects,bucketMaxSize,bucketLifecycle` (verified live today it currently prints `maxObjects,maxSize,bucketOwner`), and the rollout completes.

The explicit `flux reconcile helmrelease` is needed because the value lives in `spec.values` of the HelmRelease: reconciling the Kustomization only updates the HelmRelease object, and the Helm upgrade that rewrites the ConfigMap happens on the HelmRelease's own interval. **Check the ConfigMap before restarting the operator** — restarting it before the Helm upgrade lands just reloads the old allow-list.

The restart is required: the operator Deployment has no `envFrom` for this ConfigMap (verified live today — `envFrom: null`, and none of its 9 explicit `env` entries is `ROOK_OBC_ALLOW_ADDITIONAL_CONFIG_FIELDS`), so the value is read at operator start.

> **`rook` is a `wait: true` layer with dependents (`rook-fr01`, `configs`).** A Helm upgrade of the rook-ceph chart briefly flips the layer to `Reconciling`, and its dependents will report "dependency not ready" while that happens. That is expected — let it settle; do not reconcile in a loop.

- [ ] **Step 5: Confirm no OBC regressed**

```bash
kubectl -n rook-ceph-fr01 get obc
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket stats --rgw-realm=feather-s3 | grep -c '"owner"'
```

Expected: all 17 OBCs still `Bound`; owner count 18. Wait ~6 minutes before concluding anything is wrong — `docs/incidents/2026-07-18-…md` documents an RGW convergence lag of ~5–6 minutes after OBC changes.

**Rollback:** revert the commit and restart the operator again. The allow-list is purely permissive; nothing that already works depends on it.

---

### Task 13: Canary the OBC lifecycle field on `loki-ruler`

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/loki-ruler.yaml`

**Interfaces:**
- Consumes: Task 12 merged and the operator restarted
- Produces: a definitive answer to "does Rook 1.20.3 apply `additionalConfig` in place, or does the OBC need deleting and re-provisioning?" — which Task 14 branches on

`loki-ruler` is the right canary: **0 objects**, owner `loki`, and Loki's ruler config bucket has no live traffic, so a failed re-provision costs nothing.

- [ ] **Step 1: Add the lifecycle to the canary OBC**

Replace `infrastructure/clusters/feather-core/rook-fr01/buckets/loki-ruler.yaml` with:

```yaml
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: loki-ruler
  namespace: rook-ceph-fr01
spec:
  bucketName: loki-ruler
  storageClassName: ceph-bucket-fr01
  additionalConfig:
    bucketOwner: loki
    # Rules MUST stay sorted by ID -- Rook compares the parsed config against
    # the live one and re-PUTs on any difference; unsorted rules never converge.
    bucketLifecycle: |
      {
        "Rules": [
          {
            "ID": "AbortIncompleteMultipartUploads",
            "Status": "Enabled",
            "Prefix": "",
            "AbortIncompleteMultipartUpload": {
              "DaysAfterInitiation": 3
            }
          }
        ]
      }
```

- [ ] **Step 2: Validate and ship it as PR 3a (this commit + Task 12's)**

```bash
kubectl kustomize infrastructure/clusters/feather-core/rook-fr01/buckets | grep -A14 "name: loki-ruler"
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/buckets/loki-ruler.yaml
git commit -m "feat(rook): canary multipart-abort lifecycle on the loki-ruler bucket"
git pull --rebase origin main
git push -u origin feat/rook-obc-quotas-and-lifecycle
gh pr create --title "feat(rook): allow bucket quotas and lifecycle on obcs, canary on loki-ruler" --body "Extends obcAllowAdditionalConfigFields with bucketMaxObjects/bucketMaxSize/bucketLifecycle (release.yaml:31) and adds an AbortIncompleteMultipartUpload lifecycle to the empty loki-ruler bucket as a canary. Rollout to the other 16 OBCs is a follow-up PR, gated on whether Rook applies additionalConfig in place or only on Provision()."
```

Merging is a human decision. **PR 3a must be merged before Task 12 Step 4 or Task 13 Step 3 can run.**

- [ ] **Step 3: After PR 3a is merged, reconcile once and check whether it applied in place**

```bash
flux reconcile kustomization rook-fr01 --with-source
sleep 360
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin lc list --rgw-realm=feather-s3
```

Expected (path A — in-place update works): a JSON array containing an entry with `"bucket": ":loki-ruler:…"`. Record "in-place works" and go to Task 14 path A.

Expected (path B — nothing appears): `[]`. Rook only applied it on `Provision()`. Record "re-provision required" and go to Task 14 path B.

**Path B is the more likely outcome — plan your evening around it.** `docs/incidents/2026-07-18-mariadb-upgrade-and-rgw-access-denied.md:137` states flatly: "Rook only applies `bucketOwner` on its `Provision()` (creation) path, not on an in-place patch." `bucketLifecycle` goes through the same `additionalConfig` path, so assume the 16-bucket serialised re-provision loop in Task 14 Step 5 (≈6 min each, ≈1.5 h wall clock) until Step 3 proves otherwise. Baseline confirmed live today: `radosgw-admin lc list --rgw-realm=feather-s3` → `[]`, and `loki-ruler` has no `usage` block, i.e. 0 objects.

- [ ] **Step 4 (path B only): confirm re-provisioning applies it**

```bash
kubectl -n rook-ceph-fr01 delete obc loki-ruler
flux reconcile kustomization rook-fr01
sleep 360
kubectl -n rook-ceph-fr01 get obc loki-ruler
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin lc list --rgw-realm=feather-s3
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=loki-ruler --rgw-realm=feather-s3 | grep '"owner"'
```

Expected: OBC back to `Bound`, the bucket appears in `lc list`, and `"owner": "loki"` unchanged. Deleting an OBC is safe **only** because `ceph-bucket-fr01` has `reclaimPolicy: Retain` (verify first: `kubectl get sc ceph-bucket-fr01 -o jsonpath='{.reclaimPolicy}'` → `Retain`).

**Rollback:** revert the commit; Rook removes the lifecycle configuration when `bucketLifecycle` disappears from `additionalConfig` (path A) or on the next re-provision (path B).

---

### Task 14: Roll out lifecycle to all buckets and quotas to the four large ones (PR 3b)

> **Separate PR, branched from `main` after PR 3a is merged.** Do not append this to the PR 3a branch.
>
> ```bash
> git checkout main && git pull --rebase origin main
> git checkout -b feat/rook-obc-quota-rollout
> ```

**Files:**
- Modify: 16 of the 17 OBC files in `infrastructure/clusters/feather-core/rook-fr01/buckets/` (all except `loki-ruler.yaml`, done in Task 13, and `kustomization.yaml`, which is not an OBC)

**Interfaces:**
- Consumes: Task 13's recorded path A/B answer; Task 6's post-prune sizes
- Produces: per-bucket hard caps and automatic multipart-debris expiry

**Quota sizing.** 12 TiB raw at `size 3` is 4 TiB logical. A 55–60% raw ceiling (Task 15) is 2.2–2.4 TiB logical for everything — RBD, CephFS and S3 combined. After Tasks 8–11, RBD+CephFS is ~170 GiB logical, leaving ~2.0 TiB for S3. The four caps below total 1350 GiB and the remaining 14 buckets total ~90 GiB, so the declared ceiling is ~1.44 TiB logical = 4.3 TiB raw = **~36% raw**, leaving genuine headroom rather than a cap that is already breached.

| Bucket | Now (logical) | Post-Task-6 expectation | `bucketMaxSize` |
|---|---|---|---|
| `mariadb-galera-backup` | 1921 GiB | ~250 GiB (7d × 4/day × ~8 GiB gzip + 5.6 GiB restore point) | `400Gi` |
| `bluemap0` | 212 GiB | 212 GiB | `300Gi` |
| `mimir-blocks` | 187 GiB | 187 GiB, growing (365d retention — Decision Gate 5) | `400Gi` |
| `feather-core-cluster-pg-backup` | 140 GiB | falling once Task 7's 30d retention runs | `250Gi` |

- [ ] **Step 1: Verify the actual post-prune sizes before writing the quotas**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket stats --rgw-realm=feather-s3 \
  | python3 -c 'import json,sys
for b in json.load(sys.stdin):
    u=b.get("usage",{}).get("rgw.main",{})
    print("%9.2f GiB  %s"%(u.get("size_actual",0)/1024**3, b["bucket"]))' | sort -rn | head -6
```

**Every quota must be at least 1.5× the number printed here.** If `mariadb-galera-backup` is still above 400 GiB, Task 6's prune has not finished — wait, do not lower the quota to fit.

- [ ] **Step 2: Add the lifecycle block to every OBC**

For each of the 16 remaining files in `infrastructure/clusters/feather-core/rook-fr01/buckets/`, add the identical `bucketLifecycle` block used in Task 13 under `spec.additionalConfig`. For `feather-core-cluster-pg-backup.yaml` — which intentionally has **no** `bucketOwner` because CNPG authenticates as the OBC's own generated owner — the file becomes:

```yaml
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: feather-core-cluster-pg-backup
  namespace: rook-ceph-fr01
spec:
  bucketName: feather-core-cluster-pg-backup
  storageClassName: ceph-bucket-fr01
  additionalConfig:
    # No bucketOwner, deliberately: CNPG authenticates as the OBC's own
    # generated owner. See docs/incidents/2026-07-18-...md, out-of-scope note.
    bucketMaxSize: 250Gi
    bucketLifecycle: |
      {
        "Rules": [
          {
            "ID": "AbortIncompleteMultipartUploads",
            "Status": "Enabled",
            "Prefix": "",
            "AbortIncompleteMultipartUpload": {
              "DaysAfterInitiation": 3
            }
          }
        ]
      }
```

- [ ] **Step 3: Add `bucketMaxSize` to the three remaining large buckets**

`mariadb-galera-backup.yaml`:

```yaml
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: mariadb-galera-backup
  namespace: rook-ceph-fr01
spec:
  bucketName: mariadb-galera-backup
  storageClassName: ceph-bucket-fr01
  additionalConfig:
    bucketOwner: mariadb
    # Hard cap so a backup-retention regression can never take the cluster
    # down again (2026-07-26 and 2026-08-03). Steady state is ~250Gi.
    bucketMaxSize: 400Gi
    bucketLifecycle: |
      {
        "Rules": [
          {
            "ID": "AbortIncompleteMultipartUploads",
            "Status": "Enabled",
            "Prefix": "",
            "AbortIncompleteMultipartUpload": {
              "DaysAfterInitiation": 3
            }
          }
        ]
      }
```

Same shape for `bluemap0.yaml` (`bucketOwner: bluemap`, `bucketMaxSize: 300Gi`) and `mimir-blocks.yaml` (`bucketOwner: mimir`, `bucketMaxSize: 400Gi`).

- [ ] **Step 4: Validate and commit**

```bash
kubectl kustomize infrastructure/clusters/feather-core/rook-fr01/buckets | grep -c bucketLifecycle
kubectl kustomize infrastructure/clusters/feather-core/rook-fr01/buckets | grep bucketMaxSize
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/buckets/
git commit -m "feat(rook): add per-bucket quotas and multipart-abort lifecycle to obcs"
git pull --rebase origin main
git push -u origin feat/rook-obc-quota-rollout
gh pr create --title "feat(rook): add per-bucket quotas and multipart-abort lifecycle to obcs" --body "Adds AbortIncompleteMultipartUpload (3d) to all 17 OBCs and bucketMaxSize to the four largest buckets. Requires the allow-list from PR 3a to be merged and the rook operator restarted first."
```

Expected: the lifecycle count is `17` (16 changed here + loki-ruler from Task 13); four `bucketMaxSize` lines (`400Gi`, `300Gi`, `400Gi`, `250Gi`); `validate.sh` exits `0`.

- [ ] **Step 5: Merge PR 3b, reconcile once, and apply**

```bash
flux reconcile kustomization rook-fr01 --with-source
sleep 360
```

**Path A** (Task 13 showed in-place updates work): nothing further to do.

**Path B** (re-provision required): delete and let Flux re-provision the OBCs **one at a time, largest last**, waiting for `Bound` and ~6 minutes of RGW convergence between each.

> ### ⛔ DO NOT re-provision `feather-core-cluster-pg-backup`. It will break Postgres backups.
>
> Every other bucket carries `additionalConfig.bucketOwner: <named CephObjectStoreUser>`, so a re-provision
> re-links the bucket to a **pre-existing, stable** user whose keys never change — that is exactly what the
> 2026-07-18 remediation did to 16 buckets safely.
>
> `feather-core-cluster-pg-backup` is the one exception, and it is not a cosmetic one. Verified live 2026-08-03:
>
> - Bucket owner is `obc-rook-ceph-fr01-feather-core-cluster-pg-backup-c5bc11a0-0966-443d-affe-3e312a3d2474` —
>   a user Rook **generated at provision time**, not a `CephObjectStoreUser` in git.
> - That user's RGW access key is `VMZMKMH7Y4HU7A0ZT5Z3`.
> - `kubectl -n cnpg-system get secret cnpg-backup -o jsonpath='{.data.access-key-id}' | base64 -d` returns
>   **the same string** — i.e. the SOPS file `infrastructure/clusters/feather-core/configs/postgresql/s3-backup.env`
>   hard-codes that generated user's credentials.
>
> Deleting the OBC therefore risks removing that RGW user, and a re-provision mints a **new** generated user with
> **new** keys and a new UID. The git-committed `cnpg-backup` secret would still hold the dead key, so CNPG's WAL
> archiving and base backups start failing with `403 AccessDenied`. Recovering from that requires editing a SOPS
> file, pushing, reconciling `configs`, and — because `generatorOptions.disableNameSuffixHash: true` means a
> changed Secret does **not** roll consumers — an explicit
> `kubectl -n cnpg-system rollout restart cluster/feather-core-cluster-pg` equivalent (`kubectl cnpg restart`).
> Until then WAL segments accumulate on the primary's PVC. This is a worse outage than the one this plan is fixing.
>
> Its quota and lifecycle are applied imperatively in **Step 5b** instead. Giving it a declarative OBC field is a
> follow-up that belongs with `flux-release-control-and-convergence` (it needs a named `CephObjectStoreUser` plus
> a credential rotation, which is its own change).

```bash
for obc in loki-chunks mimir-ruler mimir-alertmanager outline plane tempo-traces \
           reposilite-releases reposilite-snapshots reposilite-onelitefeather-proxy \
           reposilite-onelitefeather-snapshots reposilite-onelitefeather-releases \
           harbor mimir-blocks bluemap0 mariadb-galera-backup ; do
  echo "=== $obc"
  kubectl -n rook-ceph-fr01 delete obc "$obc"
  flux reconcile kustomization rook-fr01
  kubectl -n rook-ceph-fr01 wait --for=jsonpath='{.status.phase}'=Bound obc/"$obc" --timeout=300s
  sleep 360
  kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
    radosgw-admin bucket stats --bucket="$obc" --rgw-realm=feather-s3 | grep -E '"owner"|"num_objects"|"max_size"'
done
```

After each bucket, its `"owner"` and `"num_objects"` **must** be unchanged from the Step 1 listing. If either changes, stop immediately and consult `docs/superpowers/plans/2026-07-18-rgw-bucket-owner-fix.md`.

That loop is 15 buckets, ≈6 min each — budget ~1.5 h. `loki-ruler` was already done in Task 13;
`feather-core-cluster-pg-backup` is handled in Step 5b.

- [ ] **Step 5b (path B only): apply pg-backup's quota and lifecycle imperatively**

The declarative `bucketMaxSize`/`bucketLifecycle` you committed for `feather-core-cluster-pg-backup.yaml` is
inert under path B (Rook applies `additionalConfig` on `Provision()` only), and the warning above forbids
re-provisioning it. Apply the same settings directly instead — this touches only quota/lifecycle metadata, never
ownership or objects, so CNPG's credentials keep working:

```bash
# Quota (admin path, no app credentials involved)
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin quota set --bucket=feather-core-cluster-pg-backup --quota-scope=bucket \
  --max-size=268435456000 --rgw-realm=feather-s3
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin quota enable --bucket=feather-core-cluster-pg-backup --quota-scope=bucket --rgw-realm=feather-s3

# Lifecycle (S3 API, using the OBC's own generated-owner credentials — the same ones CNPG uses)
EP=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80
LC='{"Rules":[{"ID":"AbortIncompleteMultipartUploads","Status":"Enabled","Prefix":"","AbortIncompleteMultipartUpload":{"DaysAfterInitiation":3}}]}'
kubectl run s3-lc-pgbackup --rm -i --restart=Never -n rook-ceph-fr01 \
  --image=amazon/aws-cli:2.17.60 \
  --overrides="{\"spec\":{\"containers\":[{\"name\":\"s3\",\"image\":\"amazon/aws-cli:2.17.60\",\"command\":[\"/bin/sh\",\"-c\",\"aws --endpoint-url=${EP} --region=us-east-1 s3api put-bucket-lifecycle-configuration --bucket feather-core-cluster-pg-backup --lifecycle-configuration '${LC}'\"],\"envFrom\":[{\"secretRef\":{\"name\":\"feather-core-cluster-pg-backup\"}}]}]}}"
```

Verify, then confirm Postgres is still archiving:

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket stats --bucket=feather-core-cluster-pg-backup --rgw-realm=feather-s3 \
  | python3 -c 'import json,sys;q=json.load(sys.stdin)["bucket_quota"];print(q["enabled"], q["max_size"])'
kubectl -n cnpg-system get cluster feather-core-cluster-pg \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} {end}{"\n"}'
```

Expected: `True 268435456000`, and `ContinuousArchiving=True LastBackupSucceeded=True`.

**Rollback for Step 5b:** `radosgw-admin quota disable --bucket=feather-core-cluster-pg-backup --quota-scope=bucket --rgw-realm=feather-s3`, and `aws … s3api delete-bucket-lifecycle --bucket feather-core-cluster-pg-backup`. Neither touches data. **If `ContinuousArchiving` flips to `False`, disable the quota first** — a quota that a WAL push hits returns `QuotaExceeded`, which stalls archiving.

Note the imperative state drifts from git: the OBC file says `bucketMaxSize: 250Gi` but Rook is not the thing enforcing it. Record that in the PR body alongside the `olf` note in Step 7.

- [ ] **Step 6: Verify quotas and lifecycle are live**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin lc list --rgw-realm=feather-s3 | grep -c bucket
for b in mariadb-galera-backup bluemap0 mimir-blocks feather-core-cluster-pg-backup ; do
  echo -n "$b "
  kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- \
    radosgw-admin bucket stats --bucket=$b --rgw-realm=feather-s3 \
    | python3 -c 'import json,sys;q=json.load(sys.stdin)["bucket_quota"];print(q["enabled"], q["max_size"])'
done
```

Expected: `lc list` shows **17** buckets (path A: all 17 from git; path B: 15 from the Step 5 loop + `loki-ruler` from Task 13 + `feather-core-cluster-pg-backup` from Step 5b); each of the four prints `True <bytes>` with `max_size` = 429496729600 / 322122547200 / 429496729600 / 268435456000. **If any prints `False -1`, Rook silently dropped `bucketMaxSize`** — re-check the operator ConfigMap from Task 12 Step 4 and that the operator actually restarted. If `max_size` is a decimal-not-binary number, Rook parsed `Gi` differently than expected; adjust the manifest to the documented decimal form (`430G`, `320G`, `430G`, `270G`) and repeat.

- [ ] **Step 7: Apply the lifecycle to the undeclared `olf` bucket**

`olf` (54.38 GiB, owner `olf`) has a `CephObjectStoreUser` in git but no OBC, so it cannot be covered declaratively. Apply the same rule imperatively and flag it:

```bash
EP=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80
LC='{"Rules":[{"ID":"AbortIncompleteMultipartUploads","Status":"Enabled","Prefix":"","AbortIncompleteMultipartUpload":{"DaysAfterInitiation":3}}]}'
kubectl run s3-lc-olf --rm -i --restart=Never -n rook-ceph-fr01 \
  --image=amazon/aws-cli:2.17.60 \
  --overrides="{\"spec\":{\"containers\":[{\"name\":\"s3\",\"image\":\"amazon/aws-cli:2.17.60\",\"command\":[\"/bin/sh\",\"-c\",\"aws --endpoint-url=${EP} --region=us-east-1 s3api put-bucket-lifecycle-configuration --bucket olf --lifecycle-configuration '${LC}'\"],\"env\":[{\"name\":\"AWS_ACCESS_KEY_ID\",\"valueFrom\":{\"secretKeyRef\":{\"name\":\"rook-ceph-object-user-feather-s3-olf\",\"key\":\"AccessKey\"}}},{\"name\":\"AWS_SECRET_ACCESS_KEY\",\"valueFrom\":{\"secretKeyRef\":{\"name\":\"rook-ceph-object-user-feather-s3-olf\",\"key\":\"SecretKey\"}}}]}]}}"
```

Expected: exit code 0; `radosgw-admin lc list` now shows **18** buckets. Note in the PR body that `olf` has no OBC in git — that belongs to `flux-release-control-and-convergence`.

**Rollback:** revert PR 3b. Quotas and lifecycle configurations are removed on the next reconcile (path A) or re-provision (path B). No object data is affected either way. Anything applied imperatively (Step 5b's pg-backup quota/lifecycle, Step 7's `olf` lifecycle) is **not** reverted by the git revert — undo those with `radosgw-admin quota disable` and `aws … s3api delete-bucket-lifecycle` as listed under Step 5b.

---

### Task 15: Rewrite `docs/buckets.md` and record the capacity ceiling (PR 4)

**Files:**
- Rewrite: `docs/buckets.md` (currently 60 lines, last touched `0dd2241` on 2026-07-13 — five days *before* the incident that invalidated it)
- Create: `docs/ceph-capacity.md`

**Interfaces:**
- Consumes: everything Tasks 12–14 established
- Produces: a runbook that no longer prescribes the exact manual step the 2026-07-18 incident fix replaced

`docs/buckets.md` currently claims at line 3 that the OBCs "don't actually provision anything" and at line 10 that "**none of them have ever reached `Bound`**". All 17 are `Bound` and 16 carry an explicit `bucketOwner`. Worse, lines 54–55 tell operators to run `radosgw-admin bucket link` by hand — which `docs/incidents/2026-07-18-mariadb-upgrade-and-rgw-access-denied.md:139` explicitly rejects.

- [ ] **Step 1: Branch and replace `docs/buckets.md` in full**

```bash
git checkout main && git pull --rebase origin main
git checkout -b docs/ceph-buckets-and-capacity
```

New content for `docs/buckets.md`:

````markdown
# Ceph RGW buckets

Every bucket in `feather-s3` is declared as an `ObjectBucketClaim` (OBC) under
`infrastructure/clusters/feather-core/rook-fr01/buckets/`, applied by the
`rook-fr01` Flux Kustomization. All of them are `Bound`. This file replaced an
earlier version (pre-2026-07-18) that claimed OBCs never provision and told you
to run `radosgw-admin bucket link` by hand — do not do that; see the incident
note at the bottom.

## Adding a bucket

1. Make sure the owning app has a `CephObjectStoreUser` under
   `infrastructure/clusters/feather-core/rook-fr01/users/`.
2. Add an OBC file:

   ```yaml
   apiVersion: objectbucket.io/v1alpha1
   kind: ObjectBucketClaim
   metadata:
     name: <bucket>
     namespace: rook-ceph-fr01
   spec:
     bucketName: <bucket>
     storageClassName: ceph-bucket-fr01
     additionalConfig:
       bucketOwner: <cephobjectstoreuser>
       bucketMaxSize: <e.g. 100Gi>          # optional but strongly encouraged
       bucketLifecycle: |                    # rules MUST be sorted by ID
         {
           "Rules": [
             {
               "ID": "AbortIncompleteMultipartUploads",
               "Status": "Enabled",
               "Prefix": "",
               "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 3 }
             }
           ]
         }
   ```

3. Register it in `buckets/kustomization.yaml`, push, and
   `flux reconcile kustomization rook-fr01 --with-source` once.

`ceph-bucket-fr01` is `provisioner: rook-ceph-fr01.ceph.rook.io/bucket` with
`reclaimPolicy: Retain` — deleting an OBC never deletes the bucket or its
objects.

## `additionalConfig` fields

Rook ignores any `additionalConfig` key not listed in
`obcAllowAdditionalConfigFields` at
`infrastructure/clusters/feather-core/rook/release.yaml:31` — silently, with no
error. The current allow-list is
`maxObjects,maxSize,bucketOwner,bucketMaxObjects,bucketMaxSize,bucketLifecycle`.

- `bucketOwner` — makes a named `CephObjectStoreUser` the real bucket owner.
  This is what fixed the 2026-07-18 cluster-wide `403 AccessDenied`.
- `bucketMaxSize` / `bucketMaxObjects` — quota on **this bucket**.
- `maxSize` / `maxObjects` — quota on the **user account**, which is shared:
  `mimir` owns three buckets, `reposilite` three, `reposilite-public` two. Use
  the `bucket*` variants unless you specifically want a per-user cap.
- `bucketLifecycle` — raw JSON S3 lifecycle configuration. RGW runs lifecycle
  once a day inside `rgw_lifecycle_work_time` (`00:00-06:00` here); force a pass
  with `radosgw-admin lc process --rgw-realm=feather-s3`.

Changing `additionalConfig` on an existing OBC: if the change does not take
effect within ~6 minutes, delete the live OBC object and let Flux re-provision
it (`kubectl -n rook-ceph-fr01 delete obc <name>` then
`flux reconcile kustomization rook-fr01`). That is safe **only** because the
StorageClass is `Retain` *and* the OBC has a `bucketOwner` pointing at a stable
`CephObjectStoreUser`; confirm the first with
`kubectl get sc ceph-bucket-fr01 -o jsonpath='{.reclaimPolicy}'`. It is **not**
safe for `feather-core-cluster-pg-backup` — see "Known exceptions" below.

**RGW convergence lag:** after an OBC re-provision the three RGW daemons do not
pick up new metadata atomically. A residual ~3% `403` rate immediately after a
change is normal and clears within ~5–6 minutes. Re-check before concluding a
change failed.

## Credentials

Each OBC generates a Secret of the same name in `rook-ceph-fr01` with
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`. When `bucketOwner` is set, those
are the **owner user's** keys, so the Secret can be used directly with the AWS
CLI for admin work:

```bash
kubectl run s3-admin --rm -i --restart=Never -n rook-ceph-fr01 \
  --image=amazon/aws-cli:2.17.60 \
  --overrides='{"spec":{"containers":[{"name":"s3","image":"amazon/aws-cli:2.17.60","command":["/bin/sh","-c","aws --endpoint-url=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80 --region=us-east-1 s3 ls s3://<bucket>/"],"envFrom":[{"secretRef":{"name":"<obc-name>"}}]}]}}'
```

(`--region=us-east-1` is required — RGW's zonegroup rejects any other explicit
`LocationConstraint`.)

Apps do **not** consume that Secret: there is no cross-namespace secret sync in
this repo, so each app's S3 credentials live in its own SOPS file
(`apps/.../<app>/*.sops.env`), copied from the `CephObjectStoreUser`'s Secret
`rook-ceph-object-user-feather-s3-<user>` (keys `AccessKey`/`SecretKey`).

## Known exceptions

- `feather-core-cluster-pg-backup.yaml` has **no** `bucketOwner` on purpose:
  CNPG authenticates as the OBC's own generated owner and was never affected by
  the non-owner grant breakage.

  **Never delete this OBC.** Its bucket owner is the Rook-generated user
  `obc-rook-ceph-fr01-feather-core-cluster-pg-backup-<uuid>`, and that user's
  access key is hard-coded into the SOPS file
  `infrastructure/clusters/feather-core/configs/postgresql/s3-backup.env`
  (Secret `cnpg-system/cnpg-backup`). A re-provision mints a new user with new
  keys, and Postgres WAL archiving fails with `403 AccessDenied` until the SOPS
  file is updated, pushed, reconciled and the CNPG cluster restarted (the Secret
  name is stable under `disableNameSuffixHash: true`, so nothing rolls on its
  own). Change its quota/lifecycle imperatively instead
  (`radosgw-admin quota set/enable`, `aws s3api put-bucket-lifecycle-configuration`).
  Every *other* OBC is safe to re-provision because `bucketOwner` points at a
  stable `CephObjectStoreUser` whose keys do not change.
- The `olf` bucket exists in RGW (54 GiB, owner `olf`) but has **no OBC in this
  repo** — only the `CephObjectStoreUser` (`users/olf.yaml`). Its lifecycle rule
  was applied imperatively on 2026-08-03. It should get an OBC.

## Do not

Do not run `radosgw-admin bucket link --bucket=… --uid=… --rgw-realm=feather-s3`.
`docs/incidents/2026-07-18-mariadb-upgrade-and-rgw-access-denied.md:139` rejects
it explicitly: ownership is a declared OBC field in this repo, so it survives a
namespace rebuild or a DR restore. A hand-linked bucket does not.
````

- [ ] **Step 2: Create `docs/ceph-capacity.md`**

```markdown
# Ceph capacity policy (rook-ceph-fr01)

## The operating ceiling is ~55-60% raw

The cluster is 3 hosts x 4 OSDs x ~1 TiB = 12 TiB raw. Every pool is
`failureDomain: host` with `size 3` / `min_size 2`
(`rook-fr01/cluster/blockpool.yaml:7`, `objectstore.yaml:9,14`), so with exactly
three hosts CRUSH must place one replica of every object on each host. That has
a hard consequence:

**Losing one OSD shrinks its host from 4 TiB to 3 TiB while that host still has
to hold every object it held before.**

At 71.3% raw (2026-08-03) each host held 2.9 TiB, so a single SSD failure would
have pushed the surviving 3 OSDs on that host to ~95-97% -- past
`backfillfull_ratio 0.9` and `full_ratio 0.95`. Backfill would stall with
`backfill_toofull`, PGs would stay degraded indefinitely, and writes to every
pool would block. There is no fourth host to fail over to and
`deviceFilter: "^sd[b-e]$"` (`cluster.yaml:56`) already claims all four slots
per node, so there is no N+1 device either.

For a single OSD loss to stay under 0.90, a host must sit below
`0.90 x 3/4 = 67.5%`. Allowing margin for rebalance skew (observed MIN/MAX VAR
0.90/1.09, so the fullest OSD runs ~9% above the host average) gives a working
ceiling of:

**55-60% raw cluster-wide. Treat 65% as the point where you stop adding data
and start deleting it.**

## Sizing consequences

- 60% of 12 TiB raw = 7.2 TiB raw = **2.4 TiB logical** for everything
  (RBD + CephFS + S3 combined).
- Declared S3 bucket quotas (`bucketMaxSize`) must sum to less than that minus
  RBD and CephFS usage. See `docs/buckets.md`.
- Backup retention is a capacity decision, not just an RPO decision:
  `infrastructure/clusters/feather-core/configs/mariadb-galera/phsysical-backup.yaml`
  (`maxRetention` x `cron` x `compression`) and
  `infrastructure/clusters/feather-core/configs/postgresql/object-store.yaml`
  (`retentionPolicy`) both write directly into this budget.

## Raising the ceiling

Only two things move it:

1. **A fourth storage host.** Lets a whole host be lost and re-replicated.
2. **A fifth device per node.** Lets a single OSD loss redistribute inside the
   host without crossing 0.90 -- ceiling rises to `0.90 x 4/5 = 72%` minus skew.

Either triggers a large backfill; with `osd_max_backfills: "1"` and
`osd_recovery_op_priority: "1"` (`cluster.yaml:134-136`) that is slow but
client-safe.

## Alerts

- `ceph-osd-usage-high` (80%) / `ceph-osd-usage-critical` (90%) -- current
  per-OSD usage, added 2026-07-26 after the first InsufficientCapacity incident.
- `ceph-host-projected-usage-after-osd-loss` (90%) -- the *projected* usage of
  the fullest host if it lost its largest OSD. This is the one that reflects the
  policy above; the other two can both be green while a single disk failure
  would still wedge the cluster.
```

- [ ] **Step 3: Validate and commit**

```bash
./scripts/validate.sh
git add docs/buckets.md docs/ceph-capacity.md
git commit -m "docs(ceph): rewrite bucket runbook and record the raw capacity ceiling"
git push -u origin docs/ceph-buckets-and-capacity
gh pr create --title "docs(ceph): rewrite bucket runbook and record the raw capacity ceiling"  --body "Rewrites docs/buckets.md (claimed OBCs never Bind; prescribed the radosgw-admin bucket link recipe the 2026-07-18 incident fix explicitly replaced) and adds docs/ceph-capacity.md with the 3-host/4-OSD headroom arithmetic."
```

Expected: `validate.sh` exits `0` (it does not read `docs/`, but run it anyway so the habit holds).

**Rollback:** revert the commit. Documentation only.

---

### Task 16: Alert on projected host usage after a single-OSD loss (PR 5)

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml` (insert into the `storage` alert group, immediately after the `ceph-osd-usage-critical` rule — at the time of writing that rule ends at line 28182, with `- orgId: 1` for the `databases` group at 28183)

**Interfaces:**
- Consumes: Task 6 and Tasks 8–11 complete (otherwise this alert fires immediately and stays firing)
- Produces: the leading indicator that `ceph-osd-usage-high` cannot give — at 71% raw, per-OSD usage read 77.75% (green against its own 80% threshold) while projected post-OSD-loss usage was **95.03%**

- [ ] **Step 1: Verify the query against live Mimir before writing it**

```bash
# via the Grafana Explore UI or the Mimir query API, datasource uid "mimir":
max(
  sum by (hostname) (ceph_osd_stat_bytes_used * on (ceph_daemon) group_left(hostname) ceph_osd_metadata)
  /
  (
    sum by (hostname) (ceph_osd_stat_bytes * on (ceph_daemon) group_left(hostname) ceph_osd_metadata)
    -
    max by (hostname) (ceph_osd_stat_bytes * on (ceph_daemon) group_left(hostname) ceph_osd_metadata)
  )
) * 100
```

Expected: a single scalar. It returned `95.03` on 2026-08-03 at 71.26% raw; after Tasks 6–11 it should return roughly **58–62**. If it returns no data, `ceph_osd_metadata` is not being scraped — check `kubectl -n rook-ceph-fr01 get servicemonitor`.

- [ ] **Step 2: Insert the rule**

In `apps/clusters/feathre-core/base-apps/grafana/release.yaml`, find `uid: ceph-osd-usage-critical` and insert the following immediately after that rule's final `severity: critical` line, at the same indentation as the other `- uid:` entries in the `storage` group:

```yaml
              - uid: ceph-host-projected-usage-after-osd-loss
                title: Ceph host projected usage after single-OSD loss
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # failureDomain: host + size 3 on exactly 3 hosts means one replica of
                      # every object must sit on each host. Losing one OSD drops that host
                      # from 4 TiB to 3 TiB while it still has to hold the same data, so the
                      # real leading indicator is host_used / (host_size - largest_osd_size),
                      # not current per-OSD %. On 2026-08-03 the per-OSD alerts were green at
                      # 77.75% while this read 95.03% -- past backfillfull, i.e. a single SSD
                      # failure would have stalled recovery and blocked writes.
                      expr: |
                        max(
                          sum by (hostname) (ceph_osd_stat_bytes_used * on (ceph_daemon) group_left(hostname) ceph_osd_metadata)
                          /
                          (
                            sum by (hostname) (ceph_osd_stat_bytes * on (ceph_daemon) group_left(hostname) ceph_osd_metadata)
                            -
                            max by (hostname) (ceph_osd_stat_bytes * on (ceph_daemon) group_left(hostname) ceph_osd_metadata)
                          )
                        ) * 100
                      instant: true
                      intervalMs: 1000
                      maxDataPoints: 43200
                      range: false
                      refId: A
                  - refId: B
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: '-100'
                    model:
                      conditions:
                        - evaluator:
                            params:
                              - 90
                            type: gt
                          operator:
                            type: and
                          query:
                            params:
                              - B
                          reducer:
                            params: []
                            type: last
                          type: query
                      datasource:
                        type: __expr__
                        uid: '-100'
                      expression: A
                      intervalMs: 1000
                      maxDataPoints: 43200
                      refId: B
                      type: threshold
                noDataState: Alerting
                execErrState: Alerting
                for: 30m
                annotations:
                  summary: "If the fullest Ceph host lost a single OSD it would exceed 90% used -- past backfillfull_ratio, so recovery would stall with backfill_toofull and writes to every pool would block. This is not a current-usage alert; per-OSD usage can be green while this is red. Operating ceiling and remediation: docs/ceph-capacity.md. Check: kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph osd df tree"
                  dashboard_url: "https://grafana.apps.onelite.feather/d/tbO9LAiZK/ceph-cluster"
                labels:
                  severity: warning
```

- [ ] **Step 3: Validate**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana | grep -c "ceph-host-projected-usage-after-osd-loss"
python3 -c "import yaml,sys; yaml.safe_load(open('apps/clusters/feathre-core/base-apps/grafana/release.yaml'))" && echo "yaml ok"
./scripts/validate.sh
```

Expected: `1`, `yaml ok`, and `validate.sh` exits `0`. The YAML parse check matters — a mis-indented block in a 28k-line file will render but silently land the rule in the wrong group.

- [ ] **Step 4: Commit, PR, merge, verify**

```bash
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "feat(grafana): alert on projected ceph host usage after single-osd loss"
git push -u origin feat/grafana-ceph-osd-loss-headroom-alert
gh pr create --title "feat(grafana): alert on projected ceph host usage after single-osd loss" --body "Adds ceph-host-projected-usage-after-osd-loss to the storage alert group. On 2026-08-03 per-OSD usage read 77.75% (below its own 80% threshold) while projected post-single-OSD-loss host usage was 95.03% -- past backfillfull. Land this AFTER the capacity reclamation, or it fires on merge."
```

After merge:

```bash
flux reconcile kustomization base-apps --with-source
kubectl -n grafana get cm grafana -o yaml | grep -c ceph-host-projected-usage-after-osd-loss
kubectl -n grafana rollout status deploy/grafana --timeout=300s
```

Expected: the ConfigMap contains the rule (`1`), and the Deployment rolls (the chart's `checksum/config` annotation changes, so this happens automatically — verify the ConfigMap *before* concluding a restart is needed).

- [ ] **Step 5: Confirm the rule is loaded and not firing**

In Grafana → Alerting → Alert rules → folder `Storage`, confirm `Ceph host projected usage after single-OSD loss` exists and is `Normal`. If it is `Alerting`, the reclamation in Tasks 6–11 did not free as much as expected — go back and check `ceph df` rather than raising the threshold.

**Rollback:** revert the commit; the rule disappears on the next reconcile.

---

### Task 17 (optional, Decision Gate 5): Mimir retention and alertmanager

**Files:**
- Modify (only if approved): `apps/clusters/feathre-core/monitoring/mimir/release.yaml:44` and/or `:143-164`

**Interfaces:**
- Consumes: Decision Gate 5's answer
- Produces: bounded Mimir block growth

Two separate items, both requiring an explicit human decision. Do not do either silently.

**(a) `compactor_blocks_retention_period: 365d` at `release.yaml:44`.** `mimir-blocks` is 187 GiB after ~2 months; 365 days extrapolates past 1 TiB logical / 3 TiB raw — a future repeat of the cliff this plan just cleared, on a cluster whose whole logical budget is 2.4 TiB. If approved, change to `90d`, commit as `fix(mimir): cut block retention from 365d to 90d`, reconcile `monitoring`, and verify with `radosgw-admin bucket stats --bucket=mimir-blocks` after the compactor's next run. **This deletes metrics history irreversibly.**

**(b) The `alertmanager:` block at `release.yaml:143-164`.** Note there is currently **no** `enabled` key under it (verified: 143 `alertmanager:`, 144 `replicas: 2`, … 161-164 `persistentVolume`), so disabling means *adding* `enabled: false`, not flipping an existing value. The audit recommends `false`: Grafana uses its own unified alerting (`unified_alerting.enabled: true`, `alerting.enabled: false` at `base-apps/grafana/release.yaml:84-91`), the notification policy is Grafana-managed (`"provenance":"file"`), the Mimir ruler holds zero rule groups, and nothing routes to Mimir's alertmanager.

**Recommendation: do not do (b) in this plan.** The two alertmanager PVCs are provisioned at 10Gi but hold **0.06 GiB each** — there is no capacity case, and the `alert-coverage-and-escalation` theme may deliberately move rule evaluation to the Mimir ruler, in which case the alertmanager becomes required infrastructure rather than waste. Hand this decision to that theme.

---

## Completion criteria

All of the following must hold before this plan is closed:

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | grep TOTAL
#   %RAW USED < 50

kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph health detail | head -1
#   HEALTH_OK

kubectl get pv --no-headers | awk '{print $5}' | sort | uniq -c
#   36 Bound, 0 Released
#   (37 Bound today; Task 11 deletes kafka-data-mimir-kafka-0, which is one of them)

kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- sh -c 'rbd ls -p feather-rbd | wc -l'
#   36  (74 today: 74 - 16 in Task 8 - 21 in Task 9 - 1 in Task 11)

kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph fs subvolume ls feather-cephfs csi
#   []  (6 subvolumes today: 5 reaped in Task 8, 1 in Task 10)

kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin lc list --rgw-realm=feather-s3 | grep -c bucket
#   18  (17 OBC buckets + the undeclared olf bucket from Task 14 Step 7)

kubectl -n mariadb-galera get physicalbackup mariadb-galera-backup -o jsonpath='{.spec.maxRetention} {.spec.compression}{"\n"}'
#   168h gzip

kubectl -n cnpg-system get cluster feather-core-cluster-pg -o jsonpath='{range .status.conditions[*]}{.type}={.status} {end}{"\n"}'
#   ContinuousArchiving=True LastBackupSucceeded=True
#   (this must still hold at the end — Tasks 7 and 14 both touch the Postgres backup path)

kubectl -n cnpg-system get objectstore s3-store -o jsonpath='{.spec.retentionPolicy} {.spec.configuration.data.compression}{"\n"}'
#   30d gzip

flux get kustomizations -A
#   every row READY=True at the same revision
```

Then tell the `offsite-backups-and-disaster-recovery` theme that its snapshot work is unblocked.
