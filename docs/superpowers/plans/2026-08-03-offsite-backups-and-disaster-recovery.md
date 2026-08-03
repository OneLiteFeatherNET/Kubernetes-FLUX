# Offsite Backups and Disaster Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get exactly one copy of every irreplaceable thing on `feather-core` *off* the Ceph cluster that currently holds both the primary data and every backup of it; add a scheduled, encrypted etcd snapshot where none exists; and write down the three recovery procedures that today exist only in the maintainer's head.

**Architecture:** Nine separately-mergeable PRs across **two repos**, ordered so that the zero-risk, zero-cost artefacts (a hand-taken etcd snapshot, two runbooks, a `VolumeSnapshotClass`) land first, and anything that spends money, adds load, or depends on a human decision lands last. The offsite Postgres path is proven by an actual restore rehearsal *before* it is committed as a schedule — because a backup that has never been restored is not a backup.

**Tech Stack:** FluxCD + Kustomize (GitOps repo), CloudNativePG 1.28.1 + `plugin-barman-cloud` v0.11.0, mariadb-operator 26.6.0 `PhysicalBackup`, Rook-Ceph RBD CSI + `snapshot-controller` v8.5.0, Talos v1.13.4 (`talosctl` v1.13.7) + `age` + `rclone`, SOPS/PGP (GitOps repo) and SOPS/age (Talos repo).

---

## Global Constraints

- **Two repos.** Every task below is labelled **[GITOPS]** (`.../Kubernetes-FLUX`, remote `OneLiteFeatherNET/Kubernetes-FLUX`, **public**) or **[TALOS]** (`/mnt/projects/lab/talos-cluster`, remote `TheMeinerLP/FeatherCore`). Never mix a change across both in one commit.
- A change takes effect **only when committed and pushed to `main`**; Flux then applies it (GitRepository polls 1m, root Kustomization 10m).
- Conventional Commits enforced by CI (`commitlint.config.mjs`) in the GitOps repo: types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`; subject starts lowercase; header ≤100 chars. The PR title is linted too.
- `./scripts/validate.sh` must pass locally before every GitOps commit.
- Never hammer `flux reconcile` in a loop — one reconcile per stage, then verify.
- Renovate moves `main` under you; `git pull --rebase origin main` before every push.
- SOPS recipients live in **both** `.sops.yaml` and `clusters/feather-core/.sops.yaml`; this plan adds no recipients, so neither file changes — but every new `*.env` **must** be `sops --encrypt --in-place`'d *before* `git add`.
- `generatorOptions.disableNameSuffixHash: true` on both the `postgresql` and `mariadb-galera` overlays: changing a Secret's *contents* does **not** roll consuming pods. Every task here that touches a Secret states its rollout explicitly.
- **The GitOps repo is public** (`gh repo view --json isPrivate` → `false`, re-verified 2026-08-03). Nothing in this plan may commit a hostname list, a bucket name tied to a credential, or an etcd snapshot in plaintext.
- ⚠️ **Tasks 5, 8, 9 all add resources to the `configs` Flux layer, which is `wait: true` with `timeout: 10m0s` (`clusters/feather-core/configs.yaml:13,19`) and is a `dependsOn` of `base-apps` → `apps` and of `monitoring`.** If a new CR in that layer never becomes Ready, `configs` goes `False`, and **every application layer stops reconciling** — not just Postgres/MariaDB. After each merge into `configs`, the first verification is always `flux get kustomizations -A`; if `configs` is not `True` within ~12 minutes, revert the merge commit immediately rather than debugging in place. (Measured on 2026-08-03: the existing `PhysicalBackup` and `ObjectStore` CRs report status conditions kstatus treats as Current, so this is a guard, not an expectation.)
- ⚠️ **`ceph-rbd-fr01` is `reclaimPolicy: Retain` (`rook-fr01/storageclasses/rbd.yaml:20`) and the `VolumeSnapshotClass` this plan adds is `deletionPolicy: Retain`.** Deleting a PV or a `VolumeSnapshotContent` under a Retain policy **does not free the underlying RBD image/snapshot** — it only removes the Kubernetes object and leaks the Ceph object forever, on a pool that is at 71.29 % raw. Every teardown step in this plan flips the policy to `Delete` *before* deleting. Do not shortcut them.

---

## Prerequisites

1. `kubectl` with context `admin@feather-core` (read-write for Task 6, Task 7, Task 9 Step 5 and Task 10 Step 4; read-only everywhere else).
2. The Talos repo checkout at `/mnt/projects/lab/talos-cluster` with a working `.age/key.txt`, and `clusters/feather-core/generated/talosconfig` present (it is git-ignored; regenerate with `./talos.sh gen-base` if missing).
3. `talosctl` ≥ v1.13.x, `age`, `rclone`, `openssl`, `python3` on the machine that will run the snapshots. All five were confirmed present on the maintainer's workstation on 2026-08-03.
4. LAN reachability to `192.168.15.10`, `.11`, `.12` (the three control planes — verified against `clusters/feather-core/talos/nodes/fr01/controlplane/*.yaml` and `kubectl get nodes -o wide`, 2026-08-03) from that machine.
5. A GPG key that is a recipient of `.sops.yaml` in the GitOps repo (fingerprint `0231831CB40B8E587B7353CBA3AF727721205A62` is the only recipient today) — needed for the offsite S3 credentials.
6. **An `rclone` remote literally named `offsite`, configured on every machine that runs a verification command in this plan.** Tasks 4, 6, 8 and 9 all verify with `rclone lsl offsite:…`; none of them creates the remote. DG-1 (Task 5 Step 0) provisions *prefix-scoped* keys that deliberately cannot list each other's prefixes, so you need **two** rclone profiles:
   - `offsite:` on the **snapshot host** — the `feather-core-etcd-offsite` key, which can only see `etcd/`.
   - `offsite:` on the **operator workstation** — a fourth, read-only key with list access to the whole bucket, used only for the verification greps in Tasks 6/8/9. Create it at DG-1 time alongside the other three; never put it in the cluster or in git.

   Create both with `rclone config` (`n` → name `offsite` → `s3` → provider per DG-1 → paste key/secret → set `endpoint`), then prove it before you rely on it:

   ```bash
   rclone lsd offsite:
   ```

   Expected: the bucket chosen in DG-1 is listed, exit `0`. If this errors, **stop** — every "verify the bytes landed" gate in this plan is inoperative until it works.

## Cross-theme dependencies

| This plan… | …depends on |
|---|---|
| **Task 13** (scheduled RBD snapshots) | **`ceph-capacity-reclamation-and-retention` must have landed and `ceph df` must show the `feather-rbd` pool with headroom.** RBD snapshots are copy-on-write inside `feather-rbd`; on 2026-08-03 the cluster was at `71.29 %RAW USED`, `feather-rbd MAX AVAIL 705 GiB`. Do **not** start Task 13 before that theme's capacity gate is green. |
| Task 9 (offsite MariaDB `PhysicalBackup`) | Touches only a **new** file plus one line of `configs/mariadb-galera/kustomization.yaml`. The capacity theme edits `phsysical-backup.yaml` itself (`maxRetention`, `compression`) — no textual conflict, but rebase before pushing. |
| Everything else | Nothing. Tasks 1–12 can start today; only Task 13 is blocked. |
| `crown-jewel-rotation-leaked-pki-and-credentials` | Is **downstream** of Task 2/3: an etcd snapshot is a plaintext dump of every Kubernetes Secret. Having snapshots *before* the PKI rotation is fine (they age out); just make sure the pre-rotation snapshots are expired or destroyed after the rotation completes. Note this in that theme, not here. |

## Decision gates (do not proceed past these without the repo owner)

- **DG-1 — Offsite object-storage provider** (blocks Task 5). See Task 5 Step 0.
- **DG-2 — Which host runs the etcd snapshot timer** (blocks Task 4). See Task 4 Step 0.
- **DG-3 — Offsite retention for Postgres and MariaDB** (blocks Task 5 / Task 9). See Task 5 Step 0b.
- **DG-4 — Whether the uptime-kuma monitor inventory may be committed at all** (blocks **Task 12**). See Task 12 Step 0.

## What this plan deliberately does NOT do

- **No Velero.** There is an unused `velero` `CephObjectStoreUser` at `infrastructure/clusters/feather-core/rook-fr01/users/velero.yaml`, and deploying Velero would be the "proper" answer for PVC-level offsite backup. It is a whole new operator, a new CRD surface, a new S3 credential and a new failure mode, and it does not protect the two things that actually matter (Postgres, MariaDB) any better than the barman/PhysicalBackup paths already do. Out of scope; revisit once this plan's offsite destination exists and is proven.
- **No Loki / Mimir / Tempo bucket mirroring.** `feather-s3.rgw.buckets.data` holds 2.5 TiB (`ceph df`, 2026-08-03), the overwhelming majority of it observability blocks. Mirroring that offsite is the single biggest cost item in the audit and buys back only metric/log *history*, not service. Explicitly accepted as lost on total cluster loss. Revisit as its own decision after the capacity theme.
- **No MariaDB restore rehearsal.** The Postgres rehearsal (Task 10) is cheap: 2584 MB of data (`select pg_size_pretty(sum(pg_database_size(datname)))`). MariaDB is 31.8 GiB and restoring it into a throwaway instance would allocate another ~40 GiB PVC on a pool at 71 % raw. Deferred until after `ceph-capacity-reclamation-and-retention`. The plan says so in Task 9 rather than pretending it is covered.
- **No in-cluster CronJob for etcd snapshots.** That would require enabling `machine.features.kubernetesTalosAPIAccess` on all three control planes — a machine-config change with a rolling apply, in the Talos repo, to add a capability whose whole purpose is to work when the cluster is broken. An external timer is both safer and more correct.
- **No change to the existing in-cluster backups.** `s3-store`, `feather-core-cluster-pg-daily` and `mariadb-galera-backup` are untouched. Everything here is additive and runs *alongside* them.
- **No secret rotation.** The leaked-PKI problem is a separate theme.

---

### Task 1: Take one etcd snapshot by hand, today — [TALOS], no PR

⚠️ **The output of this task is a plaintext dump of every Kubernetes Secret in the cluster** (etcd is encrypted at rest by Talos' disk encryption, not at the API layer). It must never touch a non-encrypted path, a shared drive, or git. This task encrypts it before it is written anywhere persistent.

**Files:** none committed. Produces one `.db.age` file outside both repos.

**Interfaces:**
- Consumes: nothing.
- Produces: the cluster's first-ever point-in-time recovery artefact, and the working `talosctl` invocation that Task 3 automates.

- [ ] **Step 1: Confirm the control plane is healthy enough to snapshot**

```bash
cd /mnt/projects/lab/talos-cluster
talosctl --talosconfig clusters/feather-core/generated/talosconfig \
  -n 192.168.15.10 etcd status
```

Expected: three rows (`192.168.15.10/.11/.12`), all with a non-empty `DB SIZE`, `RAFT INDEX` values within a few of each other, and no `LEARNER` column set to `true`. If a member is missing, stop — snapshotting a degraded etcd is still worth doing, but investigate first.

- [ ] **Step 2: Snapshot to tmpfs, encrypt, and remove the plaintext**

`/dev/shm` is tmpfs, so the plaintext never reaches a disk. The three `-r` recipients are the exact age public keys in `/mnt/projects/lab/talos-cluster/.sops.yaml`.

```bash
cd /mnt/projects/lab/talos-cluster
TS="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="/dev/shm/feather-core-etcd-${TS}.db"
OUT_DIR="${HOME}/feather-core-snapshots"
mkdir -p "$OUT_DIR" && chmod 700 "$OUT_DIR"

talosctl --talosconfig clusters/feather-core/generated/talosconfig \
  -n 192.168.15.10 etcd snapshot "$TMP"
PLAIN_BYTES="$(stat -c %s "$TMP")"; echo "plaintext bytes: $PLAIN_BYTES"

age -r age1gw9xxukmnxtvum24tvxjncpacjfaahwvcdtxnhdlj0v37dphcchsa875sa \
    -r age1rnmne8x2wp2gdffk9mhtuqepay9yg7tnzgrhphc5s3aepyll5czqvevmxl \
    -r age1k8wvl6e2pecggl5crv4zwye760t52em5emjm5p559ehf4xvl0arsjuzm0k \
    -o "${OUT_DIR}/feather-core-etcd-${TS}.db.age" "$TMP"

rm -f "$TMP"
chmod 600 "${OUT_DIR}/feather-core-etcd-${TS}.db.age"
ls -lh "${OUT_DIR}"
```

Expected: `talosctl` prints `snapshot written to /dev/shm/...` with a byte count; `ls -lh` shows one `*.db.age` file of roughly the same order of magnitude as the `DB SIZE` from Step 1 (tens to low hundreds of MiB). `/dev/shm` is empty again.

- [ ] **Step 3: Prove the encryption round-trips before you trust it**

```bash
age -d -i /mnt/projects/lab/talos-cluster/.age/key.txt \
  "${OUT_DIR}/feather-core-etcd-${TS}.db.age" > /dev/shm/verify.db
echo "decrypt exit: $?  bytes: $(stat -c %s /dev/shm/verify.db)  (plaintext was ${PLAIN_BYTES})"
# A Talos etcd snapshot is a bbolt/etcd file; the first page is not a stable
# magic string, so size equality is the check that matters. `etcdutl` is the
# only tool that can validate it properly, and it is not installed here.
command -v etcdutl >/dev/null && etcdutl snapshot status /dev/shm/verify.db --write-out=table
rm -f /dev/shm/verify.db
```

Expected: `decrypt exit: 0` and `bytes` **exactly equal** to the `plaintext bytes` printed in Step 2. (`talosctl` has no `etcd snapshot-status` subcommand — do not go looking for one.) **If the decrypt fails or the sizes differ, stop** — you have an unrecoverable file and Task 3 must not be built on this recipient list.

- [ ] **Step 4: Copy it somewhere that is not this cluster and not this laptop**

Any destination the operator already controls (external drive, a machine in another building, a personal cloud account). This is a stop-gap until Task 4 automates it — record where you put it, because **Task 11**'s `docs/dr-rebuild.md` has to name it.

**Rollback:** delete the file. Nothing on the cluster changed — `talosctl etcd snapshot` is read-only.

---

### Task 2: Write the two runbooks that already exist as tribal knowledge (PR 1) — [GITOPS]

**Files:**
- Create: `docs/runbook-node-failure.md`
- Create: `docs/runbook-osd-replacement.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the two documents `docs/dr-rebuild.md` (Task 11) links to.

Both documents are written from procedures already proven on this cluster: the out-of-service taint is standard Kubernetes (GA since 1.28; this cluster is v1.36.1), and the OSD procedure is the one used during the 2026-06-15 disk swap.

- [ ] **Step 1: Branch**

```bash
cd /mnt/projects/oss/onelitefeather/Kubernetes-FLUX
git checkout main && git pull --rebase origin main
git checkout -b docs/dr-runbooks
```

- [ ] **Step 2: Create `docs/runbook-node-failure.md`**

````markdown
# Runbook: node failure

Applies to `feather-core`. Node inventory (2026-08-03): 3 control planes
`fr01-cp-01..03` (192.168.15.10-.12), 3 storage nodes `fr01-str-01..03`
(.7-.9), 4 general workers `fr01-wrk-xl-01..04` (.5, .6, .15, .16).

## 0. Triage — which failure is this?

```bash
kubectl get nodes -o wide
kubectl get pods -A --field-selector spec.nodeName=<node> -o wide
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph status
```

| Symptom | Go to |
|---|---|
| Node `NotReady`, will come back (reboot, maintenance) | §1 |
| Node gone for good / hardware dead, has RWO PVCs | §2 |
| A **storage** node is down | §3 |
| Two or more control planes are down | `docs/dr-rebuild.md` |

## 1. Node is coming back — just wait

Do nothing. Kubernetes evicts after `tolerationSeconds` (default 300s) and
reschedules Deployments. StatefulSets with RWO volumes will **not** move;
they wait for the node. That is correct behaviour — do not force it.

## 2. Node is gone — the out-of-service taint

This is the one step a hard node failure needs and the one nobody remembers
at 03:00. Without it, RWO PVCs stay attached to the dead node forever and
pods sit in `Terminating` / `ContainerCreating` indefinitely. On this cluster
that is 15 `ceph-rbd-fr01` RWO PVCs in the `grafana` namespace alone
(`loki-ingester-*`, `mimir-ingester-*`, `tempo-ingester-*`), plus every other
StatefulSet.

⚠️ **Only apply this taint when you are certain the kubelet is dead and the
node is not writing to its volumes.** If the node is actually alive and just
partitioned from the API server, the taint tells Kubernetes to force-detach
volumes that are still mounted and being written to — that is a
split-brain filesystem corruption, not a recovery.

Confirm it is really dead first:

```bash
ping -c3 <node-ip>
talosctl --talosconfig <talos-repo>/clusters/feather-core/generated/talosconfig \
  -n <node-ip> version     # must fail/time out
```

Then:

```bash
kubectl cordon <node>
kubectl taint node <node> node.kubernetes.io/out-of-service=nodeshutdown:NoExecute
```

Watch the stuck pods drain and reschedule:

```bash
kubectl get pods -A --field-selector spec.nodeName=<node> -w
kubectl get volumeattachments | grep <node>
```

Expected within ~1-2 minutes: the `Terminating` pods disappear, the
`VolumeAttachment` rows for that node disappear, and replacement pods reach
`Running` on other nodes.

### Untaint — mandatory when the node returns

```bash
kubectl taint node <node> node.kubernetes.io/out-of-service=nodeshutdown:NoExecute-
kubectl uncordon <node>
```

**Leaving the taint on a returned node means every volume on it gets
force-detached again.** If the node is being replaced rather than repaired,
delete it instead: `kubectl delete node <node>`, then remove its file under
`clusters/feather-core/talos/nodes/fr01/<role>/` in the Talos repo and
re-render (`./talos.sh render-all && ./talos.sh gen-talosconfig`).

## 3. A storage node is down

Ceph survives: `feather-rbd` is `replicated size: 3`, `failureDomain: host`
over exactly three hosts (`rook-fr01/cluster/blockpool.yaml:7-10`). With one
host down the pool is `undersized+degraded` but `min_size: 2` keeps I/O
serving. **It cannot re-replicate**, because there is no fourth host to
place the third copy on. It will sit degraded until the node returns.

- Do **not** `ceph osd out` the down OSDs hoping recovery starts. It cannot,
  and it makes the eventual return slower.
- Do **not** apply the out-of-service taint to a storage node to "unstick"
  Ceph — the OSD pods are the storage.
- Expect `pods-stuck-terminating` and Ceph health alerts to fire. Acknowledge,
  do not act.
- If the node is permanently gone, you have a real capacity/topology change:
  either add a replacement host **before** anything else fails, or accept
  `size 3 / 2 hosts` (one more failure = data loss) as a temporary state.

Disk-level failure inside a surviving node: see `docs/runbook-osd-replacement.md`.
````

- [ ] **Step 3: Create `docs/runbook-osd-replacement.md`**

````markdown
# Runbook: replacing or resizing an OSD disk (rook-ceph-fr01)

Cluster: `rook-ceph-fr01`, Ceph **v19.2.5 Squid**, raw-device OSDs,
`deviceFilter: "^sd[b-e]$"`, `failureDomain: host`, `size 3`, three hosts
`fr01-str-01..03`, 4 OSDs each (osd.0-11). Toolbox:
`kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph ...`.

Note the namespace split: the **operator** HelmRelease lives in `rook-ceph`,
the **cluster** in `rook-ceph-fr01`.

## The trap (cost 30 % degraded and a stuck cluster on 2026-06-15)

Squid writes BlueStore bdev labels at **multiple offsets**: 0, 1 GiB, 10 GiB,
100 GiB (and 1 TiB). A `dd` over the first ~200 MB clears only offset 0.
`blkid` and `lsblk FSTYPE` also read only offset 0, so the disk *looks*
blank — but `ceph-volume raw list` reads the backup labels and resurrects the
old OSD identity (osd_uuid/ID). Three disks then claimed `ID:0` with three
different FSIDs, the new osd.0 crashed in `expand-bluefs` init, and the
cluster hung.

Address disks by **`/dev/disk/by-id` serial** (Proxmox serial = `drive-scsiN`),
never `/dev/sdX` — those renumber across reboots.

## One OSD at a time. One host at a time. `HEALTH_OK` between each.

### 1. Pre-flight

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph status
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph osd tree
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df
```

Required: `HEALTH_OK`, all 12 OSDs `up`, and enough `MAX AVAIL` to hold the
displaced PGs. **Identify which physical disk holds the OSD you are removing
and write the serial down.** Never touch the live data copy.

### 2. Remove the OSD

```bash
kubectl -n rook-ceph-fr01 scale deploy rook-ceph-osd-<id> --replicas=0
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph osd purge <id> --yes-i-really-mean-it
kubectl -n rook-ceph-fr01 delete deploy rook-ceph-osd-<id>
```

### 3. Zap the disk at every label offset

⚠️ **Destructive. Triple-check the device serial.** Run from a privileged pod
on that node with `/dev` as hostPath, image `quay.io/ceph/ceph:v19.2.5`:

```bash
dev=/dev/disk/by-id/scsi-<serial>
for seekmb in 0 1024 10240 102400; do
  dd if=/dev/zero of="$dev" bs=1M count=16 seek=$seekmb conv=notrunc oflag=direct
done
sgdisk --zap-all "$dev"
wipefs -a "$dev"
```

Verify — every one of these must **fail**:

```bash
for off in 0 1073741824 10737418240 107374182400; do
  ceph-bluestore-tool show-label --dev "$dev" --offset $off && echo "STILL LABELLED AT $off"
done
ceph-volume raw list
```

`ceph-volume raw zap --destroy "$dev"` is an acceptable alternative, but still
run the verification loop afterwards.

### 4. Let the operator re-provision

```bash
kubectl -n rook-ceph rollout restart deploy rook-ceph-operator
kubectl -n rook-ceph-fr01 get pods -l app=rook-ceph-osd -w
```

Expected: a new `rook-ceph-osd-<newid>` prepare job runs and a new OSD pod
reaches `Running`; `ceph osd tree` shows 12 OSDs `up` again.

### 5. Wait for `HEALTH_OK` before the next disk

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph status
```

Backfill speed: Squid defaults to the **mClock** scheduler, and the
`cluster.yaml` spec does not override it. To speed recovery:

```bash
ceph config set osd osd_mclock_profile high_recovery_ops
```

The classic knobs need `ceph config set osd osd_mclock_override_recovery_settings true`
first, then `osd_max_backfills` / `osd_recovery_max_active`. Measured ceiling on
this cluster is ~500 MiB/s (bandwidth-bound); `max_backfills` 8 vs 16 makes
almost no difference. **Revert to `balanced` when done.**

## Capacity note

`failureDomain: host` + `size 3` over three hosts means a larger disk on one
host yields **no usable capacity** until the other two match. As of 2026-06,
fr01-str-01 was 2×512G + 2×256G while str-02/03 were still 4×256G — the extra
capacity on str-01 is stranded.
````

- [ ] **Step 4: Validate and commit**

```bash
./scripts/validate.sh
```

Expected: exits `0`. (`docs/` is not a Flux path, so nothing new is built — this is a regression check that you did not break anything else.)

```bash
git add docs/runbook-node-failure.md docs/runbook-osd-replacement.md
git commit -m "docs: add node-failure and osd-replacement runbooks"
git push -u origin docs/dr-runbooks
gh pr create --title "docs: add node-failure and osd-replacement runbooks" --body "$(cat <<'EOF'
## Summary
- docs/runbook-node-failure.md: the `node.kubernetes.io/out-of-service` taint sequence for RWO PVCs stuck Terminating, when it is safe, and the mandatory untaint
- docs/runbook-osd-replacement.md: the Squid multi-offset bdev-label wipe procedure, written up from the 2026-06-15 disk swap

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] Docs-only; no cluster change. Reviewer sanity-checks the taint and zap commands against their own memory of the 2026-06-15 incident.
EOF
)"
```

**Rollback:** revert the merge commit. Docs-only, no cluster effect.

---

### Task 3: Add `snapshot` / `snapshot-config` verbs to `talos.sh` (PR 2) — [TALOS]

**Files:**
- Modify: `/mnt/projects/lab/talos-cluster/talos.sh` (436 lines as of `26e0186`; help text at `cmd_help`, line 68; new functions inserted after `cmd_build()`, which spans **lines 294-297**; `COMMANDS` at lines 303-305; `command_keys()` at lines 311-319; the dispatch `case` alternation at **lines 429-431**)
- Create: `/mnt/projects/lab/talos-cluster/scripts/systemd/talos-etcd-snapshot.service`
- Create: `/mnt/projects/lab/talos-cluster/scripts/systemd/talos-etcd-snapshot.timer`
- Modify: `/mnt/projects/lab/talos-cluster/README.md` (add a "Backups" section)

**Interfaces:**
- Consumes: the working invocation proven in Task 1.
- Produces: `./talos.sh snapshot`, which Task 4 schedules.

**Note on the Talos repo's own conventions:** it uses `.sops.yaml` with **age** (3 recipients), unlike the GitOps repo's PGP. `clusters/*/generated/` is git-ignored, so the scoped talosconfig this task creates is never committed. Confirm this repo's commit-message convention before Step 6 — `git log --oneline -20` shows Conventional-Commit-style subjects (`chore: …`, `feat: …`) but there is no commitlint config, so match the existing style rather than assuming CI enforces it.

- [ ] **Step 1: Branch**

```bash
cd /mnt/projects/lab/talos-cluster
git checkout main && git pull --rebase origin main
git checkout -b feat/etcd-snapshot
```

- [ ] **Step 2: Add the two helpers and two commands to `talos.sh`**

Insert immediately **after** `cmd_build()` — it ends at **line 297** (`}`), and line 299 opens the `# ---` / `# Shell completion` comment block. Insert between the two. (Verified against `26e0186`; if `git log --oneline -1` shows a newer commit, re-locate with `grep -n '^cmd_build()' talos.sh` rather than trusting these numbers.)

```bash
# ---------------------------------------------------------------------------
# etcd snapshots
# ---------------------------------------------------------------------------

# Echo the age recipients from .sops.yaml, space-separated.
age_recipients() {
  [ -f .sops.yaml ] || return 0
  python3 - <<'PY'
import yaml
recs = []
for rule in (yaml.safe_load(open('.sops.yaml')) or {}).get('creation_rules', []):
    for r in (rule.get('age') or '').split(','):
        r = r.strip()
        if r and r not in recs:
            recs.append(r)
print(' '.join(recs))
PY
}

# Echo the first control-plane endpoint from a talosconfig.
first_endpoint() {
  python3 -c "
import yaml, sys
cfg = yaml.safe_load(open(sys.argv[1]))
eps = list(cfg['contexts'].values())[0].get('endpoints') or []
print(eps[0] if eps else '')
" "$1"
}

# Warn (never fail) when a talosconfig client cert is close to expiry.
warn_if_cert_expiring() {
  local tc="$1" end days
  command -v openssl >/dev/null 2>&1 || return 0
  end="$(python3 -c "
import yaml, base64, sys
cfg = yaml.safe_load(open(sys.argv[1]))
sys.stdout.write(base64.b64decode(list(cfg['contexts'].values())[0]['crt']).decode())
" "$tc" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)" || return 0
  [ -n "$end" ] || return 0
  days=$(( ( $(date -d "$end" +%s) - $(date -u +%s) ) / 86400 ))
  if [ "$days" -lt 30 ]; then
    info "WARNING: ${tc} client cert expires in ${days}d — run: ./talos.sh snapshot-config"
  fi
}

# Mint a talosconfig scoped to os:etcd:backup (EtcdSnapshot + etcd member/alarm
# list only — cannot read Secrets, cannot reboot, cannot apply config).
cmd_snapshot-config() {
  need talosctl; need python3
  local admin="${CLUSTER_DIR}/generated/talosconfig"
  [ -f "$admin" ] || die "Missing $admin (run: ./talos.sh gen-base)"
  local out="${CLUSTER_DIR}/generated/talosconfig-etcd-backup"
  local node="${NODE:-$(first_endpoint "$admin")}"
  [ -n "$node" ] || die "No endpoint in $admin and no NODE= given"
  talosctl --talosconfig "$admin" -n "$node" config new "$out" \
    --roles os:etcd:backup --crt-ttl "${CRT_TTL:-8760h}"
  # `config new` does not always carry endpoints over; make sure they are set.
  python3 -c "
import yaml, sys
admin, out = sys.argv[1], sys.argv[2]
a = yaml.safe_load(open(admin)); o = yaml.safe_load(open(out))
eps = list(a['contexts'].values())[0].get('endpoints') or []
for ctx in o['contexts'].values():
    ctx['endpoints'] = eps
yaml.dump(o, open(out, 'w'), default_flow_style=False)
" "$admin" "$out"
  chmod 600 "$out"
  info "Wrote $out (git-ignored, role os:etcd:backup, ttl ${CRT_TTL:-8760h})"
}

# Take an etcd snapshot, encrypt it to the .sops.yaml age recipients, and
# optionally push it off-host. The plaintext .db is a dump of every Kubernetes
# Secret, so it is written to tmpfs and removed immediately.
cmd_snapshot() {
  need talosctl; need age; need python3
  local tc="${TALOSCONFIG_FILE:-${CLUSTER_DIR}/generated/talosconfig-etcd-backup}"
  [ -f "$tc" ] || die "Missing $tc (run: ./talos.sh snapshot-config)"
  warn_if_cert_expiring "$tc"

  local node="${NODE:-$(first_endpoint "$tc")}"
  [ -n "$node" ] || die "No endpoint in $tc and no NODE= given"

  local recipients; recipients="$(age_recipients)"
  [ -n "$recipients" ] || die "No age recipients in .sops.yaml — refusing to write a plaintext snapshot"

  local out_dir="${OUT_DIR:-${HOME}/${CLUSTER}-snapshots}"
  mkdir -p "$out_dir"; chmod 700 "$out_dir"

  local ts; ts="$(date -u +%Y%m%dT%H%M%SZ)"
  local tmpdir="${SNAPSHOT_TMPDIR:-/dev/shm}"
  local tmp="${tmpdir}/${CLUSTER}-etcd-${ts}.db"
  local enc="${out_dir}/${CLUSTER}-etcd-${ts}.db.age"
  # shellcheck disable=SC2064
  trap "rm -f '$tmp'" EXIT

  talosctl --talosconfig "$tc" -n "$node" etcd snapshot "$tmp"
  # shellcheck disable=SC2086
  age $(printf -- '-r %s ' $recipients) -o "$enc" "$tmp"
  rm -f "$tmp"; trap - EXIT
  chmod 600 "$enc"
  info "snapshot: $enc ($(du -h "$enc" | cut -f1))"

  if [ -n "${RCLONE_REMOTE:-}" ]; then
    need rclone
    rclone copy "$enc" "${RCLONE_REMOTE}" --checksum
    info "uploaded to ${RCLONE_REMOTE}"
  fi

  local keep="${KEEP_DAYS:-30}"
  find "$out_dir" -maxdepth 1 -name "${CLUSTER}-etcd-*.db.age" -mtime "+${keep}" -print -delete
}
```

- [ ] **Step 3: Register the commands**

Three edits, all mechanical.

`COMMANDS` (lines 303-305) — append `snapshot snapshot-config` before `completion`:

```bash
COMMANDS="help age-keygen sops-config sops-updatekeys sops-encrypt sops-decrypt \
sops-edit flux-key gen-base gen-talosconfig new-node render-node render-all \
validate build snapshot snapshot-config completion"
```

`command_keys()` — add two cases above the `*)` default:

```bash
    snapshot)               echo "NODE OUT_DIR KEEP_DAYS RCLONE_REMOTE TALOSCONFIG_FILE SNAPSHOT_TMPDIR" ;;
    snapshot-config)        echo "NODE CRT_TTL" ;;
```

Dispatch `case` (lines 429-431 — the `age-keygen|sops-config|…|build)` alternation inside `main()`, **not** the earlier `__complete|completion` case at 411-414) — add the two names:

```bash
    age-keygen|sops-config|sops-updatekeys|sops-encrypt|sops-decrypt|sops-edit|\
    flux-key|gen-base|gen-talosconfig|new-node|render-node|render-all|validate|build|\
    snapshot|snapshot-config)
      "cmd_${command}" ;;
```

`cmd_help()` — add a `Backup:` block between the `Build:` and `Shell:` sections:

```
Backup:
  snapshot-config [NODE=] [CRT_TTL=8760h]   Mint a talosconfig scoped to os:etcd:backup
  snapshot [NODE=] [OUT_DIR=] [KEEP_DAYS=30] [RCLONE_REMOTE=]
                             Take an etcd snapshot, age-encrypt it, prune old ones
                             NOTE: the plaintext snapshot contains every Kubernetes
                             Secret. It is written to tmpfs and deleted immediately.
```

- [ ] **Step 4: Create the systemd units**

`scripts/systemd/talos-etcd-snapshot.service`:

```ini
[Unit]
Description=Take an age-encrypted etcd snapshot of the feather-core Talos cluster
Documentation=https://github.com/TheMeinerLP/FeatherCore
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# EDIT: absolute path to this repo checkout on the snapshot host.
WorkingDirectory=/opt/talos-cluster
# EDIT: rclone remote:path for the offsite copy, or comment out to keep local only.
Environment=RCLONE_REMOTE=offsite:feather-core-offsite/etcd
Environment=KEEP_DAYS=30
ExecStart=/opt/talos-cluster/talos.sh snapshot
# Hardening: this unit handles a plaintext dump of every cluster Secret.
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=%h /dev/shm
UMask=0077
```

⚠️ **Two things about this unit that will bite you in Task 4 if you skip them:**
- It has no `User=`, so it runs as **root**. `OUT_DIR` therefore defaults to `/root/feather-core-snapshots`, the `age` recipients come from the repo's `.sops.yaml` (fine — public keys), but **`rclone` reads `/root/.config/rclone/rclone.conf`**, not the operator's. Configure the `offsite` remote as root on that host (`sudo rclone config`) or the upload silently never happens.
- `ProtectSystem=strict` makes the entire filesystem read-only except `ReadWritePaths`. `%h` is `/root` for a system unit; `/dev/shm` covers the tmpfs staging. If you move `OUT_DIR` or the repo checkout somewhere else, add that path to `ReadWritePaths` or the unit fails with `Read-only file system`.

`scripts/systemd/talos-etcd-snapshot.timer`:

```ini
[Unit]
Description=Daily etcd snapshot of feather-core

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=15m
Persistent=true
Unit=talos-etcd-snapshot.service

[Install]
WantedBy=timers.target
```

03:30 UTC deliberately avoids 02:00 (CNPG `feather-core-cluster-pg-daily`) and the MariaDB `PhysicalBackup` slots. The MariaDB cron is `0 */6 * * *` (`phsysical-backup.yaml:10`), i.e. **00:00, 06:00, 12:00 and 18:00** — each run takes ~4-5 min with `compression: none`, so 03:30 is clear of all four.

- [ ] **Step 5: Add a Backups section to `README.md`**

`README.md` is 223 lines. Insert this **between the end of `## Deploy` (line 210) and `## Documentation` (line 211)** — `## Deploy` is where first-boot `apply-config` and `flux bootstrap` are documented, so backups belong right after it, not at the end of the file behind the licence:

````markdown
## Backups (etcd)

The cluster's only point-in-time recovery artefact is a `talosctl etcd snapshot`.
Nothing takes one automatically — set up the timer.

```bash
./talos.sh snapshot-config          # once: mint an os:etcd:backup-scoped talosconfig
./talos.sh snapshot                 # take one now
RCLONE_REMOTE=offsite:bucket/etcd ./talos.sh snapshot   # …and push it off-host
```

Schedule it with the units in `scripts/systemd/` (edit `WorkingDirectory` and
`RCLONE_REMOTE` first):

```bash
sudo cp scripts/systemd/talos-etcd-snapshot.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now talos-etcd-snapshot.timer
systemctl list-timers talos-etcd-snapshot.timer
```

⚠️ **A Talos etcd snapshot is a plaintext dump of every Kubernetes Secret.**
`./talos.sh snapshot` never leaves the plaintext on disk (tmpfs + immediate
delete) and encrypts the result to the `age` recipients in `.sops.yaml`.
Do not bypass it with a raw `talosctl etcd snapshot` into a synced folder.

Restore: `talosctl -n <cp-ip> bootstrap --recover-from=./db.snapshot`, on
control planes whose ephemeral partitions have been wiped and whose etcd
service is in `Preparing`. Full order of operations lives in the GitOps repo
at `docs/dr-rebuild.md`.
````

- [ ] **Step 6: Verify and commit**

```bash
cd /mnt/projects/lab/talos-cluster
bash -n talos.sh                      # syntax
./talos.sh help 2>&1 | grep -A4 '^Backup:'
./talos.sh __complete '' '' | grep -c '^snapshot'
```

Expected: `bash -n` silent; `help` shows the new `Backup:` block; `__complete` prints `2`.

```bash
./talos.sh snapshot-config
ls -l clusters/feather-core/generated/talosconfig-etcd-backup
```

Expected: file exists, mode `600`.

Prove the scoping actually took — this is the whole point of using `os:etcd:backup` instead of admin:

```bash
talosctl --talosconfig clusters/feather-core/generated/talosconfig-etcd-backup \
  -n 192.168.15.10 read /etc/hostname
```

Expected: **failure** with a permission/RBAC error (`PermissionDenied` / "not authorized"). If this succeeds, the config is not scoped — do not proceed.

```bash
./talos.sh snapshot KEEP_DAYS=30
ls -lh ~/feather-core-snapshots/
```

Expected: a new `feather-core-etcd-<ts>.db.age`, mode `600`, and `/dev/shm` clean (`ls /dev/shm | grep etcd` → nothing).

```bash
git add talos.sh scripts/systemd README.md
git commit -m "feat: add etcd snapshot command with age encryption and systemd timer"
git push -u origin feat/etcd-snapshot
gh pr create --repo TheMeinerLP/FeatherCore \
  --title "feat: add etcd snapshot command with age encryption and systemd timer" \
  --body "Adds ./talos.sh snapshot + snapshot-config (os:etcd:backup-scoped talosconfig, age-encrypted output, optional rclone push) and the systemd service/timer. Additive: no existing verb changes behaviour."
```

**Merge it before starting Task 4** — Task 4 installs `scripts/systemd/*` from a checkout of this repo on another host, and that host should be tracking `main`, not a feature branch.

**Rollback:** revert the commit. The two new verbs are additive; nothing existing changes behaviour. The generated `talosconfig-etcd-backup` is git-ignored; revoke it if needed by rotating the Talos PKI (out of scope here) or simply letting the 1-year cert lapse.

---

### Task 4: Enable the timer and prove two consecutive snapshots land offsite (gate)

**Files:** none (operational). Host configuration only.

**Interfaces:**
- Consumes: merged PR 2, and DG-1's chosen provider (Task 5 Step 0) if the offsite copy is enabled now rather than later.
- Produces: a running daily snapshot, which `docs/dr-rebuild.md` (Task 11) documents as the recovery source.

- [ ] **Step 0 — DECISION GATE DG-2: which host runs the timer**

The snapshot host must (a) be on the LAN with reach to `192.168.15.10-12`, (b) be always-on, (c) **not be a node of this cluster**, and (d) hold a copy of the Talos repo plus an `age` private key that is one of the three `.sops.yaml` recipients.

| Option | Pro | Con | |
|---|---|---|---|
| A. Maintainer's workstation | zero new infrastructure, key already there | not always-on; snapshots silently stop when it is off | |
| B. A small always-on box on the LAN (NAS, Pi, hypervisor host) | actually daily | one more machine to hold a decryption-capable key | **recommended** |
| C. A Proxmox host outside the k8s cluster | always-on, already exists | key custody on a hypervisor | acceptable |

**I could not verify whether such a host exists** — no inventory of non-cluster machines is in either repo. Ask the owner. If the answer is "only the workstation", take option A *and* accept a gap, rather than shipping nothing.

The gap matters: a missed snapshot is silent. Whichever host wins, add a Grafana/uptime-kuma dead-man check on the offsite object's `LastModified` (out of scope for this plan — belongs to `alert-coverage-and-escalation`; raise it there).

- [ ] **Step 0b: Prepare the chosen host — clone, key, rclone-as-root**

The unit runs as root (no `User=`), so all three of these are root-scoped. Do them before Step 1 or Step 2 will half-succeed (snapshot written locally, nothing uploaded, exit 0).

```bash
# 1. Repo checkout at the path the unit's WorkingDirectory names.
sudo git clone https://github.com/TheMeinerLP/FeatherCore.git /opt/talos-cluster

# 2. The age private key (one of the 3 recipients) and the admin talosconfig.
#    talosconfig-etcd-backup is git-ignored, so copy the one minted in Task 3
#    Step 6 rather than regenerating it here.
sudo install -m 700 -d /opt/talos-cluster/.age
sudo install -m 600 <your>/.age/key.txt /opt/talos-cluster/.age/key.txt
sudo install -m 600 <your>/clusters/feather-core/generated/talosconfig-etcd-backup \
  /opt/talos-cluster/clusters/feather-core/generated/talosconfig-etcd-backup

# 3. The `offsite` rclone remote, as root, with the etcd-prefix-scoped key from DG-1.
sudo rclone config      # n -> name: offsite -> s3 -> provider per DG-1 -> keys -> endpoint
sudo rclone lsd offsite:
```

Expected from `rclone lsd offsite:`: the DG-1 bucket, exit `0`.

⚠️ `age -d` is **not** used by `./talos.sh snapshot` (it only encrypts), so the private key is not strictly needed on this host — but without it nobody on that machine can verify a snapshot decrypts. Decide deliberately: a host that can only encrypt is the safer custody choice (DG-2 option C).

- [ ] **Step 1: Install and start the timer on the chosen host**

```bash
cd /opt/talos-cluster
sudo cp scripts/systemd/talos-etcd-snapshot.service /etc/systemd/system/
sudo cp scripts/systemd/talos-etcd-snapshot.timer   /etc/systemd/system/
sudo systemctl edit --full talos-etcd-snapshot.service   # fix WorkingDirectory + RCLONE_REMOTE
sudo systemctl daemon-reload
sudo systemctl enable --now talos-etcd-snapshot.timer
```

- [ ] **Step 2: Fire it once manually**

```bash
sudo systemctl start talos-etcd-snapshot.service
systemctl status talos-etcd-snapshot.service --no-pager
```

Expected: `Active: inactive (dead)` with `status=0/SUCCESS`, and the journal showing **both** a `snapshot: …db.age` line and an `uploaded to offsite:…` line:

```bash
journalctl -u talos-etcd-snapshot.service -n 30 --no-pager | grep -E 'snapshot:|uploaded to'
```

Expected: two lines. If only `snapshot:` appears, `RCLONE_REMOTE` was not set in the unit — the snapshot exists only on this host and the task is **not** done.

- [ ] **Step 3: Verify the offsite copy exists**

```bash
rclone lsl offsite:feather-core-offsite/etcd
```

Expected: at least one `feather-core-etcd-*.db.age` with a plausible size.

- [ ] **Step 4: Wait one full day and verify the timer fired on its own**

```bash
systemctl list-timers talos-etcd-snapshot.timer --no-pager
rclone lsl offsite:feather-core-offsite/etcd
```

Expected: `NEXT`/`LAST` populated, and **two** objects offsite.

**Gate:** do not mark this theme's etcd work done on one manual run. Two consecutive automatic runs is the signal.

**Rollback:** `sudo systemctl disable --now talos-etcd-snapshot.timer`. No cluster impact.

---

### Task 5: Add the offsite object store credentials and CNPG `ObjectStore` (PR 3) — [GITOPS]

⚠️ **This task requires creating credentials at a third-party provider and puts them into SOPS.** It does not schedule anything yet — the `ObjectStore` is inert until a `Backup` references it.

**Files:**
- Create: `infrastructure/clusters/feather-core/configs/postgresql/s3-backup-offsite.env` (SOPS-encrypted)
- Create: `infrastructure/clusters/feather-core/configs/postgresql/object-store-offsite.yaml`
- Modify: `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`

**Interfaces:**
- Consumes: DG-1, DG-3.
- Produces: `ObjectStore/s3-store-offsite` in `cnpg-system`, which Task 7's probe `Backup` and Task 8's `ScheduledBackup` reference.

- [ ] **Step 0 — DECISION GATE DG-1: offsite provider**

Sizing, measured 2026-08-03: Postgres total `2584 MB` (all databases), MariaDB `31.8 GiB` (`information_schema.tables`), etcd snapshot tens of MiB. With the retentions recommended in DG-3, expect **~50-80 GiB offsite**, growing slowly.

| Option | Cost at ~80 GiB | Notes | |
|---|---|---|---|
| Backblaze B2 | ~$0.50/mo storage; egress free up to 3× stored | S3-compatible; supports **per-prefix application keys** (so the Postgres key cannot delete the MariaDB or etcd prefix) and Object Lock | **recommended** |
| Cloudflare R2 | ~$1.20/mo; zero egress fees | S3-compatible; simplest billing; no per-prefix key scoping as clean as B2's | good alternative |
| Hetzner Object Storage | €5.99/mo flat (1 TB incl.) | EU, closest to fr01; flat fee is poor value at 80 GiB but fine if you later mirror observability buckets | pick if EU-only data residency is required |

Whatever is chosen, create **three separate application keys**, each scoped to one prefix of one bucket:

| Prefix | Used by | Key |
|---|---|---|
| `postgresql/` | CNPG barman (Task 5) | `feather-core-pg-offsite` |
| `mariadb-galera/` | mariadb-operator (Task 9) | `feather-core-mariadb-offsite` |
| `etcd/` | `talos.sh snapshot` (Task 4) | `feather-core-etcd-offsite` |

Plus **a fourth key** — bucket-wide, **read/list only**, no write, no delete — used solely by the operator's `rclone lsl offsite:…` verification commands in Tasks 6, 8 and 9. Without it those greps fail with 403 against a prefix-scoped key and every "did the bytes really land" gate becomes unverifiable. Keep it on the workstation only; it never goes into the cluster or into git.

Rationale: a compromise of the in-cluster Postgres backup credential must not be able to delete the MariaDB copy. This is the only real protection this design has against "attacker with cluster access deletes all backups".

- [ ] **Step 0b — DECISION GATE DG-3: offsite retention**

The offsite copy is a *disaster* copy, not the operational one — the in-cluster `s3-store` (daily) and `mariadb-galera-backup` (6-hourly) remain the day-to-day restore path.

| | Recommended | Why | Alternative |
|---|---|---|---|
| Postgres offsite | `retentionPolicy: 8w`, weekly schedule | 8 base backups × ~1 GiB gzipped = negligible | `12w` if you want a quarter of history |
| MariaDB offsite | `maxRetention: 720h` (30d), weekly schedule | 4-5 copies × ~7 GiB gzipped ≈ 35 GiB | `1344h` (56d) doubles it to ~56 GiB |
| etcd | `KEEP_DAYS=30`, daily | 30 × tens of MiB | `KEEP_DAYS=7` if space-constrained |

Note the Postgres offsite store receives **base backups only, no WAL** — see the caveat in Step 3. Its retention therefore expresses "how far back can we roll to a weekly boundary", not PITR granularity.

- [ ] **Step 1: Branch**

```bash
cd /mnt/projects/oss/onelitefeather/Kubernetes-FLUX
git checkout main && git pull --rebase origin main
git checkout -b feat/offsite-postgres-backup
```

- [ ] **Step 2: Create and immediately encrypt the credentials**

The key names match the existing `cnpg-backup` Secret exactly (verified live: `access-key-id`, `secret-access-key`); `region` is new because the offsite `ObjectStore` needs it (`s3Credentials.region` is a `SecretKeySelector`, not a plain string).

```bash
cat > infrastructure/clusters/feather-core/configs/postgresql/s3-backup-offsite.env <<'EOF'
access-key-id=REPLACE_ME
secret-access-key=REPLACE_ME
region=REPLACE_ME
EOF
$EDITOR infrastructure/clusters/feather-core/configs/postgresql/s3-backup-offsite.env   # paste real values
sops --encrypt --in-place infrastructure/clusters/feather-core/configs/postgresql/s3-backup-offsite.env
head -3 infrastructure/clusters/feather-core/configs/postgresql/s3-backup-offsite.env
```

⚠️ **Do not `git add` before the `sops --encrypt --in-place` succeeds.** The root `.sops.yaml` rule `.*\.(env|sops\.env|…)$` covers plain `.env`, so the encryption is whole-file PGP, same as the existing `s3-backup.env`.

Expected from `head -3`: lines of the form `access-key-id=ENC[AES256_GCM,data:…]` — **not** the plaintext key.

- [ ] **Step 3: Create the offsite `ObjectStore`**

Create `infrastructure/clusters/feather-core/configs/postgresql/object-store-offsite.yaml`. `metadata.namespace` is omitted deliberately — the overlay's `kustomization.yaml` sets `namespace: cnpg-system`, matching the existing `object-store.yaml`.

```yaml
# Second, OFF-CLUSTER barman destination. The primary `s3-store` writes to
# rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc -- i.e. into the same Ceph that
# holds the Postgres PVCs it is backing up. This one exists so that losing that
# Ceph does not lose the backups with it.
#
# Base backups only: only one plugin entry on the Cluster may set
# isWALArchiver, and that is `s3-store`. This store therefore restores to the
# consistency point of the newest weekly base backup (RPO <= 7 days), not to an
# arbitrary point in time. Proven by the restore rehearsal recorded in
# docs/dr-rebuild.md.
apiVersion: barmancloud.cnpg.io/v1
kind: ObjectStore
metadata:
  name: s3-store-offsite
spec:
  retentionPolicy: 8w
  configuration:
    destinationPath: s3://REPLACE_BUCKET/postgresql
    endpointURL: https://REPLACE_ENDPOINT
    s3Credentials:
      accessKeyId:
        name: cnpg-backup-offsite
        key: access-key-id
      secretAccessKey:
        name: cnpg-backup-offsite
        key: secret-access-key
      region:
        name: cnpg-backup-offsite
        key: region
    data:
      compression: gzip
```

Replace `REPLACE_BUCKET` and `REPLACE_ENDPOINT` with DG-1's answers (e.g. `olf-feather-core-offsite` and `https://s3.eu-central-003.backblazeb2.com`). The bucket name is not a secret; the keys are, and they are in the SOPS file.

- [ ] **Step 4: Register both in the overlay**

In `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`, add the resource right after `object-store.yaml`:

```yaml
  - object-store.yaml
  - object-store-offsite.yaml
  - backup.yaml
```

and add the secret generator right after the existing `cnpg-backup` entry:

```yaml
secretGenerator:
  - name: cnpg-backup
    envs:
      - s3-backup.env
  - name: cnpg-backup-offsite
    envs:
      - s3-backup-offsite.env
```

- [ ] **Step 5: Render and validate**

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs/postgresql | grep -A3 "name: s3-store-offsite"
kubectl kustomize infrastructure/clusters/feather-core/configs/postgresql | grep -c "cnpg-backup-offsite"
./scripts/validate.sh
```

Expected: the `ObjectStore` renders with `namespace: cnpg-system`; the grep count is ≥ 4 (one Secret + three `secretKeyRef`s); `validate.sh` exits `0` with no new `Invalid`/`Errors` in the `configs` group. (`ObjectStore` is a CRD — kubeconform skips it under `-ignore-missing-schemas`; that is expected, not a pass.)

- [ ] **Step 6: Commit and open the PR**

```bash
git add infrastructure/clusters/feather-core/configs/postgresql/s3-backup-offsite.env \
        infrastructure/clusters/feather-core/configs/postgresql/object-store-offsite.yaml \
        infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml
git commit -m "feat(postgresql): add offsite barman objectstore"
git push -u origin feat/offsite-postgres-backup
gh pr create --title "feat(postgresql): add offsite barman objectstore" --body "$(cat <<'EOF'
## Summary
- Adds ObjectStore/s3-store-offsite in cnpg-system, pointing at an external S3 provider
- Adds the cnpg-backup-offsite Secret (SOPS)
- No schedule yet: the store is inert until a Backup references it. A one-off Backup and a full restore rehearsal are the gate before the ScheduledBackup PR.

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] Merge, reconcile configs once, confirm the ObjectStore exists and the Secret has 3 keys
- [ ] Run a one-off Backup against it (next task) and restore from it before scheduling
EOF
)"
```

- [ ] **Step 7: After merge, reconcile once and verify**

```bash
flux reconcile kustomization configs --with-source
flux get kustomizations -A | grep -E 'NAME|configs'
kubectl get objectstore -n cnpg-system
kubectl get secret -n cnpg-system cnpg-backup-offsite -o jsonpath='{.data}' | python3 -c "import json,sys;print(sorted(json.load(sys.stdin).keys()))"
```

Expected: `configs` `READY=True` at the new revision; `s3-store-offsite` listed; the Secret prints `['access-key-id', 'region', 'secret-access-key']`.

**Rollback:** revert the merge commit; Flux prunes the `ObjectStore` and Secret on the next reconcile. Nothing consumed them yet, so there is no dangling reference. Also delete the provider-side keys.

---

### Task 6: Probe — one manual `Backup` to the offsite store (gate)

⚠️ **This is the first time Postgres data leaves the cluster.** It is also the step most likely to fail, because it is the step this plan could not verify in advance.

**Files:** one scratch YAML **outside the repo**. Nothing is committed.

**Interfaces:**
- Consumes: merged PR 3.
- Produces: a completed offsite `Backup` object, which Task 7 restores from.

**Unverified going in:** whether the barman instance sidecar can resolve the `cnpg-backup-offsite` Secret and the `s3-store-offsite` `ObjectStore` at backup time when that store is **not** listed in `Cluster.spec.plugins`. Upstream documentation says yes; that was not testable without running it. Step 3 is the fallback if it is no.

- [ ] **Step 1: Create the probe backup**

```bash
cat > /tmp/probe-offsite-backup.yaml <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: probe-offsite-20260803
  namespace: cnpg-system
spec:
  cluster:
    name: feather-core-cluster-pg
  method: plugin
  target: prefer-standby
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
    parameters:
      barmanObjectName: s3-store-offsite
EOF
kubectl apply -f /tmp/probe-offsite-backup.yaml
```

`target: prefer-standby` mirrors the existing `feather-core-cluster-pg-daily` so the primary takes no extra I/O.

- [ ] **Step 2: Watch it**

```bash
kubectl get backups.postgresql.cnpg.io -n cnpg-system probe-offsite-20260803 -w
```

Expected within a few minutes (2584 MB gzipped over the WAN): `PHASE` reaches `completed`.

If it does not, get the reason:

```bash
kubectl describe backups.postgresql.cnpg.io -n cnpg-system probe-offsite-20260803
kubectl logs -n cnpg-system feather-core-cluster-pg-2 -c plugin-barman-cloud --tail=100
```

- [ ] **Step 3: If it failed with a missing-Secret / unknown-ObjectStore error — the documented fallback**

Add the offsite store as a **second, non-archiving** plugin entry on the Cluster, in `infrastructure/clusters/feather-core/configs/postgresql/cluster.yaml` (currently lines 103-107):

```yaml
  plugins:
    - name: barman-cloud.cloudnative-pg.io
      isWALArchiver: true
      parameters:
        barmanObjectName: s3-store
    - name: barman-cloud.cloudnative-pg.io
      parameters:
        barmanObjectName: s3-store-offsite
```

⚠️ **This rolls all three Postgres instances** (`feather-core-cluster-pg-1/-2/-4`; the `plugin-barman-cloud` native sidecar spec changes). CNPG does a rolling restart with a switchover; expect a few seconds of write unavailability on `feather-core-cluster-pg-rw` — which is **every** application database on this cluster (harbor, outline, otis, vulpes, backstage, dependency-track, grafana, n8n, plane). Do it in a maintenance window, as its own commit `fix(postgresql): mount the offsite objectstore on the instance sidecar`, and re-run Step 1 afterwards.

⚠️ **Unverified: whether CNPG accepts two `spec.plugins` entries with the same `name`.** The CRD stores `plugins` as a plain array with no list-map key, but the operator may key its internal plugin map by name and reject or silently collapse the duplicate. Before merging, apply the change and immediately check:

```bash
kubectl -n cnpg-system logs deploy/cnpg-cloudnative-pg --tail=50 | grep -iE 'duplicate|plugin'
kubectl -n cnpg-system get cluster feather-core-cluster-pg -o jsonpath='{.status.conditions}' | python3 -m json.tool
```

Expected: no `duplicate`/`already registered` error and the Cluster still reporting `Ready=True`. **If the operator rejects it, do not force it** — revert the commit (`git revert`, push, `flux reconcile kustomization configs --with-source`, confirm the pods stop rolling) and go straight to Task 7 Step 6 option 1 (the `rclone sync` mirror), which needs no CNPG change at all.

If the failure is instead a network error (DNS, TLS, 403), it is a credential/egress problem, not this — fix the key or the provider bucket policy and retry Step 1. There are no egress `NetworkPolicy` objects in `cnpg-system` (`kubectl get networkpolicy -A`, 2026-08-03), so nothing in-cluster should be blocking it.

- [ ] **Step 4: Confirm objects actually landed at the provider**

```bash
rclone lsl offsite:REPLACE_BUCKET/postgresql/
```

Expected: a `base/` directory tree with a backup ID, non-zero sizes. **An `ObjectStore` that reports `completed` but has written nothing is the exact failure mode this step exists to catch.**

**Gate:** do not proceed to Task 7 until Step 4 shows real bytes at the provider.

**Rollback:** `kubectl delete backups.postgresql.cnpg.io -n cnpg-system probe-offsite-20260803` and delete the objects at the provider. No production state was touched.

---

### Task 7: Restore rehearsal — bring the offsite backup up as a throwaway cluster (gate)

⚠️ **Applied with `kubectl`, never committed to git.** Flux `prune: true` only removes resources it owns, so a hand-applied `Cluster` will not be reaped — you must delete it yourself in Step 5, or it sits there consuming a PVC forever.

**Files:** one scratch YAML outside the repo, plus the result recorded in Task 11's `docs/dr-rebuild.md`.

**Interfaces:**
- Consumes: the completed probe backup from Task 6.
- Produces: the single fact that makes this whole theme worth anything — evidence the offsite copy is restorable — and the exact recovery manifest that goes into `docs/dr-rebuild.md`.

- [ ] **Step 1: Check there is room**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | head -6
```

Expected: `feather-rbd` `MAX AVAIL` comfortably above 10 GiB (it was 706 GiB on 2026-08-03). If the capacity theme has not landed and this is tight, use `size: 5Gi` — the whole database is 2.6 GB.

- [ ] **Step 2: Create the rehearsal cluster**

Note it has **no `spec.plugins`** — it must not archive WAL anywhere, least of all into the offsite store it is restoring from.

```bash
cat > /tmp/dr-rehearsal-pg.yaml <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: dr-rehearsal-pg
  namespace: cnpg-system
spec:
  instances: 1
  imageName: ghcr.io/cloudnative-pg/postgresql:17
  storage:
    size: 10Gi
    storageClass: ceph-rbd-fr01
  resources:
    requests:
      cpu: 100m
      memory: 512Mi
    limits:
      memory: 512Mi
  affinity:
    nodeSelector:
      topology.kubernetes.io/zone: fr01
  bootstrap:
    recovery:
      source: offsite
  externalClusters:
    - name: offsite
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters:
          barmanObjectName: s3-store-offsite
          serverName: feather-core-cluster-pg
EOF
kubectl apply -f /tmp/dr-rehearsal-pg.yaml
kubectl get cluster -n cnpg-system dr-rehearsal-pg -w
```

Expected: a `dr-rehearsal-pg-1-full-recovery-*` Job runs, then `dr-rehearsal-pg-1` reaches `Running`, and the Cluster reports `Cluster in healthy state` with `1/1` instances.

If recovery fails complaining about missing WAL segments, that is the base-backup-without-WAL-archive risk this plan flagged. **Do not paper over it.** Record it and escalate to the fallback in Step 6.

- [ ] **Step 3: Prove the data is actually there**

```bash
kubectl exec -n cnpg-system dr-rehearsal-pg-1 -c postgres -- \
  psql -U postgres -Atc "select datname, pg_size_pretty(pg_database_size(datname)) from pg_database order by 1;"
```

Expected: the real application databases (`harbor`, `outline`, `otis`, `vulpes`, `backstage`, `dependency-track`, `grafana`, `n8n`, `plane`, …) with non-trivial sizes — not just `postgres`/`template*`.

Spot-check one table for real rows:

```bash
kubectl exec -n cnpg-system dr-rehearsal-pg-1 -c postgres -- \
  psql -U postgres -d outline -Atc "select count(*) from pg_stat_user_tables;"
```

Expected: a non-zero table count.

- [ ] **Step 4: Record the numbers**

Write down, for `docs/dr-rebuild.md`: recovery start → `Running` wall-clock, the backup ID restored, the newest transaction timestamp recovered, and any warning in the recovery Job's log. These become the RTO/RPO the runbook quotes instead of a guess.

- [ ] **Step 5: Tear it down — do not skip this, and do not skip the reclaim-policy flip**

First record the baseline you will compare against (do this **before** Step 2 if you can; if you forgot, take it now and accept a weaker check):

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- rbd ls feather-rbd | wc -l
```

⚠️ **`ceph-rbd-fr01` is `reclaimPolicy: Retain` (`rook-fr01/storageclasses/rbd.yaml:20`). Deleting the PVC leaves a `Released` PV, and deleting *that* PV does NOT delete the RBD image — it just drops the Kubernetes object and orphans ~10 GiB in `feather-rbd` permanently.** The reclaim policy must be flipped to `Delete` **while the PV still exists**, so ceph-csi runs `DeleteVolume`. In this order:

```bash
# 1. Delete the Cluster. CNPG-owned PVCs go with it; if any survive, delete them.
kubectl delete cluster -n cnpg-system dr-rehearsal-pg
kubectl get pvc -n cnpg-system | grep dr-rehearsal || echo "no pvcs left"
kubectl delete pvc -n cnpg-system -l cnpg.io/cluster=dr-rehearsal-pg --ignore-not-found

# 2. Find the now-Released PVs and flip them to Delete BEFORE deleting them.
PVS=$(kubectl get pv -o json | python3 -c "
import json,sys
for p in json.load(sys.stdin)['items']:
    cr = p['spec'].get('claimRef') or {}
    if cr.get('namespace')=='cnpg-system' and 'dr-rehearsal' in (cr.get('name') or ''):
        print(p['metadata']['name'])
")
echo "PVs to reclaim: $PVS"     # sanity-check this list before the next line
for pv in $PVS; do
  kubectl patch pv "$pv" -p '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}'
done
# A Released PV with policy Delete is reclaimed by the CSI controller on its own;
# only delete it by hand if it is still there after a couple of minutes.
kubectl get pv | grep dr-rehearsal || echo "all reclaimed"
```

⚠️ **`kubectl patch pv` is a blunt instrument — the python filter above is scoped to `claimRef.namespace == cnpg-system` and a name containing `dr-rehearsal`. Read the `PVs to reclaim:` line before running the loop. Flipping the wrong PV to `Delete` arms a production volume for destruction the moment its PVC goes away.**

Then confirm the images actually went:

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- rbd ls feather-rbd | wc -l
```

Expected: back to the baseline count from the top of this step. Leaving orphaned images behind is exactly the problem the capacity theme is cleaning up — do not add to it.

- [ ] **Step 6: If the rehearsal failed — fallback decision gate**

If Step 2 could not recover for lack of WALs, the "second `ObjectStore`" design does not give a restorable offsite copy on its own. Options, in order of preference:

1. **Mirror the whole in-cluster backup bucket instead.** An `rclone sync offsite` of `s3://feather-core-cluster-pg-backup/backups-fr01` (base backups *and* the WAL archive) from the snapshot host of Task 4, on the same daily timer. Provider-agnostic, needs no CNPG change, gives real PITR offsite. Costs a second S3 credential (the `postgresql-cluster-backup` `CephObjectStoreUser` already exists) and reads over the RGW tunnel.
2. Keep the base-backup-only store and accept RPO = the weekly boundary, documented as such.

Recommendation: option 1. Raise it with the owner rather than picking silently; it changes what Task 8 is.

**Gate:** Task 8 does not start until either the rehearsal succeeded or option 1 above is agreed and re-scoped.

**Rollback:** Step 5 *is* the rollback — nothing in this task touches production. If you abort mid-rehearsal, still run Step 5 in full (including the reclaim-policy flip), otherwise you have left a 10 GiB RBD image and a running Postgres pod behind on a pool at 71 % raw. Verify with `kubectl get cluster,pvc,pv -A | grep dr-rehearsal` → no output.

---

### Task 8: Schedule the weekly offsite Postgres backup (PR 4) — [GITOPS]

**Files:**
- Create: `infrastructure/clusters/feather-core/configs/postgresql/scheduled-backup-offsite.yaml`
- Modify: `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`

**Interfaces:**
- Consumes: the proven-restorable `s3-store-offsite` from Task 7.
- Produces: an automatic weekly offsite copy.

- [ ] **Step 0: Branch**

```bash
cd /mnt/projects/oss/onelitefeather/Kubernetes-FLUX
git checkout main && git pull --rebase origin main
git checkout -b feat/offsite-postgres-schedule
```

- [ ] **Step 1: Create the ScheduledBackup**

Note the CNPG cron is **6-field, seconds first** — the existing `scheduled-backup.yaml:8` (`"0 0 2 * * *"`) documents this. Sunday 04:00 UTC is chosen to clear 02:00 (the in-cluster daily), 03:30 (etcd), and the MariaDB `0 */6 * * *` slots at 00:00/06:00/12:00/18:00.

```yaml
# Weekly OFF-CLUSTER base backup. The daily in-cluster backup
# (feather-core-cluster-pg-daily -> s3-store) stays the operational restore
# path; this one exists only for the case where the Ceph holding that daily
# backup is gone.
#
# Sunday 04:00 UTC: clear of the 02:00 in-cluster backup, the 03:30 etcd
# snapshot and the MariaDB PhysicalBackup slots (00/06/12/18), so no two
# backup jobs contend for the same disks or uplink.
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: feather-core-cluster-pg-offsite-weekly
spec:
  schedule: "0 0 4 * * 0"
  cluster:
    name: feather-core-cluster-pg
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
    parameters:
      barmanObjectName: s3-store-offsite
  backupOwnerReference: self
  # Same reasoning as the daily schedule: keep backup I/O off the primary.
  target: prefer-standby
  # The one-off probe in this theme's plan already proved the path; no need to
  # fire another backup the moment this lands.
  immediate: false
```

- [ ] **Step 2: Register it**

In `kustomization.yaml`, after `scheduled-backup.yaml`:

```yaml
  - scheduled-backup.yaml
  - scheduled-backup-offsite.yaml
```

- [ ] **Step 3: Render, validate, commit**

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs/postgresql | grep -B2 -A6 "feather-core-cluster-pg-offsite-weekly"
./scripts/validate.sh
git add infrastructure/clusters/feather-core/configs/postgresql/scheduled-backup-offsite.yaml \
        infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml
git commit -m "feat(postgresql): schedule weekly offsite base backup"
git push -u origin feat/offsite-postgres-schedule
gh pr create --title "feat(postgresql): schedule weekly offsite base backup" \
  --body "Weekly Sunday 04:00 UTC base backup to s3-store-offsite, target prefer-standby, immediate:false. The offsite path was proven by a one-off Backup and a full restore rehearsal before this PR."
```

Expected: the render shows `namespace: cnpg-system` and `schedule: 0 0 4 * * 0`; `validate.sh` exits `0`.

- [ ] **Step 4: After merge, reconcile once and verify**

```bash
flux reconcile kustomization configs --with-source
flux get kustomizations -A | grep -E 'NAME|configs'
kubectl get scheduledbackup -A
```

Expected: `configs` `READY=True` at the new revision, and two `ScheduledBackup` rows — `feather-core-cluster-pg-daily` and `feather-core-cluster-pg-offsite-weekly`. If `configs` is not `True` within ~12 minutes, revert the merge commit (it blocks `base-apps`/`apps`/`monitoring`).

- [ ] **Step 5: Verify after the first Sunday**

```bash
kubectl get backups.postgresql.cnpg.io -n cnpg-system | grep offsite
rclone lsl offsite:REPLACE_BUCKET/postgresql/ | tail
```

Expected: one `completed` backup named `feather-core-cluster-pg-offsite-weekly-<ts>`, and matching objects at the provider.

**Rollback:** revert the merge commit; Flux prunes the `ScheduledBackup`. Existing offsite backups are untouched (`backupOwnerReference: self` only owns future ones).

---

### Task 9: Add the offsite MariaDB `PhysicalBackup` (PR 5) — [GITOPS]

⚠️ **This uploads ~7 GiB (gzipped from 31.8 GiB) over the WAN weekly, and stages the uncompressed dump (~28.6 GiB measured) in an `emptyDir` on the target node.** Confirm the node has the ephemeral space **before merging**:

```bash
kubectl get node fr01-wrk-xl-01 -o jsonpath='{.status.allocatable.ephemeral-storage}{"\n"}'
kubectl describe node fr01-wrk-xl-01 | grep -A4 'Allocated resources' | grep -i ephemeral
```

Required: at least ~60 GiB free — enough for the in-cluster backup's staging **and** this one, because they can overlap (see Step 2). If it is tighter than that, do not merge; reduce `stagingStorage.volume.emptyDir.sizeLimit` and move the cron instead.

**Files:**
- Create: `infrastructure/clusters/feather-core/configs/mariadb-galera/mariadb-backup-offsite.env` (SOPS)
- Create: `infrastructure/clusters/feather-core/configs/mariadb-galera/phsysical-backup-offsite.yaml`
- Modify: `infrastructure/clusters/feather-core/configs/mariadb-galera/kustomization.yaml`

**Interfaces:**
- Consumes: DG-1's `feather-core-mariadb-offsite` key.
- Produces: an offsite copy of the Galera data — which, because uptime-kuma runs with `volume.enabled: false` and stores everything in the `uptime-kuma` database, is also the offsite copy of the monitor configuration (see Task 12).

**Cross-theme note:** `ceph-capacity-reclamation-and-retention` edits `phsysical-backup.yaml` (`maxRetention:13`, `compression:20`). This task creates a *new* file and adds one line to `kustomization.yaml` — no textual conflict, but rebase before pushing.

- [ ] **Step 1: Branch and create the credentials**

New Secret rather than extending the existing `mariadb` Secret, because that one is generated from `mariadb-root.env` and also carries the root `password`; there is no reason for the offsite S3 key to share a blast radius with it.

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/offsite-mariadb-backup
cat > infrastructure/clusters/feather-core/configs/mariadb-galera/mariadb-backup-offsite.env <<'EOF'
access-key-id=REPLACE_ME
secret-access-key=REPLACE_ME
EOF
$EDITOR infrastructure/clusters/feather-core/configs/mariadb-galera/mariadb-backup-offsite.env
sops --encrypt --in-place infrastructure/clusters/feather-core/configs/mariadb-galera/mariadb-backup-offsite.env
head -2 infrastructure/clusters/feather-core/configs/mariadb-galera/mariadb-backup-offsite.env
```

Expected: `access-key-id=ENC[AES256_GCM,…]`.

- [ ] **Step 2: Create the offsite PhysicalBackup**

Differences from the in-cluster `phsysical-backup.yaml`, all deliberate:

| Field | In-cluster | Offsite | Why |
|---|---|---|---|
| `schedule.cron` | `0 */6 * * *` (00/06/12/18) | `30 6 * * 0` | Weekly, starting **after** the 06:00 in-cluster run finishes (~4-5 min with `compression: none`), leaving a 5.5 h window before the 12:00 one |
| `compression` | `none` | `gzip` | In-cluster optimises for a short backup window; offsite optimises for uplink bytes: 31.8 GiB → ~7 GiB |
| `timeout` | `2h` | `5h` | gzip (~22 min measured) plus a WAN upload — capped at 5 h so a stuck run **cannot** still be holding the staging `emptyDir` when the 12:00 in-cluster job starts |
| `storage.s3.tls.enabled` | `false` | `true` | External provider is HTTPS |
| `region` | *(unset)* | set | Required by most external S3 providers; the field exists on the CRD (`storage.s3.region`, verified against the live `physicalbackups.k8s.mariadb.com` CRD) |
| `maxRetention` | `720h` | `720h` | DG-3 |

⚠️ **Do not use `0 5 * * 0`.** With a 5-6 h timeout an offsite run starting at 05:00 can still be uploading when the 06:00 in-cluster `PhysicalBackup` fires. `podAffinity: true` pins both onto the same node, both stage into an `emptyDir` on that node's ephemeral storage, and two ~28.6 GiB staging directories plus the gzip output is how you fill a worker's root disk and evict everything on it. mariadb-operator does **not** serialise runs across two different `PhysicalBackup` CRs.

```yaml
# Second, OFF-CLUSTER destination for the Galera physical backup. The primary
# PhysicalBackup writes to rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc -- the
# same Ceph that holds /var/lib/mysql. This one survives losing that Ceph.
#
# Weekly rather than 6-hourly, and gzip rather than none: this copy optimises
# for uplink bytes (31.8 GiB raw -> ~7 GiB), not for a short backup window.
apiVersion: k8s.mariadb.com/v1alpha1
kind: PhysicalBackup
metadata:
  name: mariadb-galera-backup-offsite
  namespace: mariadb-galera
spec:
  mariaDbRef:
    name: mariadb-galera
  schedule:
    # Sunday 06:30 UTC -- deliberately AFTER the 06:00 in-cluster PhysicalBackup
    # (0 */6 * * *, ~4-5 min per run) rather than before it: podAffinity pins
    # both jobs to the same node and both stage ~28.6 GiB into an emptyDir
    # there, so they must never overlap. 5.5 h of clear runway to the 12:00 run.
    cron: "30 6 * * 0"
    suspend: false
    immediate: false
  maxRetention: 720h # 30 days -> 4-5 weekly copies
  compression: gzip
  storage:
    s3:
      bucket: REPLACE_BUCKET
      prefix: mariadb-galera
      endpoint: REPLACE_ENDPOINT_HOST_NO_SCHEME
      region: REPLACE_REGION
      tls:
        enabled: true
      accessKeyIdSecretKeyRef:
        name: mariadb-backup-offsite
        key: access-key-id
      secretAccessKeySecretKeyRef:
        name: mariadb-backup-offsite
        key: secret-access-key
  stagingStorage:
    volume:
      emptyDir:
        sizeLimit: 100Gi
  podAffinity: true
  timeout: 5h
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: 2
      memory: 4Gi
```

`endpoint` takes a **host without scheme** (the in-cluster one is `rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80`), so use e.g. `s3.eu-central-003.backblazeb2.com` with `tls.enabled: true`, not `https://…`.

- [ ] **Step 3: Register both**

In `infrastructure/clusters/feather-core/configs/mariadb-galera/kustomization.yaml`:

```yaml
resources:
  - namespace.yaml
  - mariadb.yaml
  - users
  - phsysical-backup.yaml
  - phsysical-backup-offsite.yaml
  - databases
  - grants
  - passwords
  - maxscale.yaml

secretGenerator:
  - name: mariadb
    envs:
      - mariadb-root.env
  - name: mariadb-backup-offsite
    envs:
      - mariadb-backup-offsite.env
```

- [ ] **Step 4: Validate and commit**

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs/mariadb-galera | grep -A4 "name: mariadb-galera-backup-offsite"
./scripts/validate.sh
git add infrastructure/clusters/feather-core/configs/mariadb-galera/mariadb-backup-offsite.env \
        infrastructure/clusters/feather-core/configs/mariadb-galera/phsysical-backup-offsite.yaml \
        infrastructure/clusters/feather-core/configs/mariadb-galera/kustomization.yaml
git commit -m "feat(mariadb): add weekly offsite physical backup"
git push -u origin feat/offsite-mariadb-backup
gh pr create --title "feat(mariadb): add weekly offsite physical backup" \
  --body "Second PhysicalBackup for mariadb-galera writing to an external S3 provider (gzip, weekly Sun 06:30 UTC, 30d retention). New SOPS secret mariadb-backup-offsite. The in-cluster 6-hourly backup is untouched."
```

Expected from the `kubectl kustomize` grep: the `PhysicalBackup` renders with `namespace: mariadb-galera` and `cron: "30 6 * * 0"`; `validate.sh` exits `0`.

- [ ] **Step 5: After merge, reconcile once and force one run**

The Secret is new, so nothing needs a rollout restart — the backup Job reads it at Job creation. (`disableNameSuffixHash: true` means *edits* to an existing Secret would not roll anything; this is a create.)

```bash
flux reconcile kustomization configs --with-source
flux get kustomizations -A | grep -E 'NAME|configs'
kubectl get physicalbackup -n mariadb-galera
```

Expected: `configs` `READY=True` at the new revision, and two `PhysicalBackup` rows. If `configs` is not `True` within ~12 minutes, revert the merge commit — it blocks `base-apps`/`apps`/`monitoring`.

Do not wait a week for the first proof. Trigger one manually with the operator's **`schedule.onDemand`** trigger — a unique identifier that differs from `status.lastScheduleOnDemand` starts exactly one run. (Do **not** patch `schedule.immediate: true`: that field is only evaluated relative to the CR's creation, so patching it after the fact is unreliable. `onDemand` is the mechanism already used on this cluster — the live `mariadb-galera-backup` shows `status.lastScheduleOnDemand: manual-storage-fix-1785095257`.)

⚠️ Pick a moment at least an hour clear of 00:00/06:00/12:00/18:00 UTC so this run cannot collide with the in-cluster backup on the same node.

```bash
kubectl patch physicalbackup -n mariadb-galera mariadb-galera-backup-offsite \
  --type=merge -p "{\"spec\":{\"schedule\":{\"onDemand\":\"offsite-proof-$(date -u +%s)\"}}}"
kubectl get jobs -n mariadb-galera -w
```

The next `flux reconcile` drops the `onDemand` field again (git does not set it); that does **not** re-trigger anything, because a run is only started when the value *differs* from `status.lastScheduleOnDemand`.

- [ ] **Step 6: Verify**

```bash
kubectl get physicalbackup -n mariadb-galera mariadb-galera-backup-offsite
rclone lsl offsite:REPLACE_BUCKET/mariadb-galera/ | tail
JOB=$(kubectl get jobs -n mariadb-galera -o name --sort-by=.metadata.creationTimestamp | grep offsite | tail -1)
kubectl -n mariadb-galera logs "$JOB" --tail=30
```

Expected: `COMPLETE=True`, `STATUS=Success`; a `*.xb.gz` object of roughly 7 GiB at the provider.

⚠️ **A completed MariaDB backup that has never been restored is still unproven.** The restore rehearsal for MariaDB is explicitly deferred (see "What this plan deliberately does NOT do") because it needs ~40 GiB of Ceph the cluster does not currently have to spare. Schedule it as the first item after `ceph-capacity-reclamation-and-retention` lands, using a `Restore` CR against a throwaway `MariaDB`.

**Rollback:** revert the merge commit; Flux prunes the `PhysicalBackup` and Secret. Objects already at the provider stay.

---

### Task 10: Add the `VolumeSnapshotClass` (PR 6) — [GITOPS]

**Files:**
- Create: `infrastructure/clusters/feather-core/rook-fr01/storageclasses/volumesnapshotclass.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/storageclasses/kustomization.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: `VolumeSnapshotClass/ceph-rbd-fr01-snapshot`, the prerequisite for Task 13 and for any ad-hoc "snapshot this before I upgrade it" during an incident.

Everything needed is already deployed and idle: `kube-system/snapshot-controller` `2/2` (v8.5.0), the `volumesnapshotclasses|contents|volumesnapshots` CRDs at `v1`, and the RBD/CephFS snapshotter sidecars with resources allocated in `rook/csi-drivers-release.yaml`. `kubectl get volumesnapshotclass` returns `No resources found` — the class is the one missing piece.

Parameters are copied from the live `ceph-rbd-fr01` StorageClass (`storageclasses/rbd.yaml:9,13-14`): `clusterID: rook-ceph-fr01`, provisioner Secret `rook-csi-rbd-provisioner` in `rook-ceph-fr01`.

- [ ] **Step 1: Branch and create the class**

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/rbd-volumesnapshotclass
```

Create `infrastructure/clusters/feather-core/rook-fr01/storageclasses/volumesnapshotclass.yaml`:

```yaml
# deletionPolicy: Retain matches the ceph-rbd-fr01 StorageClass convention --
# deleting a VolumeSnapshot leaves the VolumeSnapshotContent and the underlying
# Ceph snapshot in place, so a fat-fingered delete cannot destroy the copy.
# Consequence: snapshots taken through this class must be reclaimed by hand
# (delete the VolumeSnapshotContent too). A rotating class with
# deletionPolicy: Delete belongs with the scheduled-snapshot work, not here.
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata:
  name: ceph-rbd-fr01-snapshot
driver: rook-ceph.rbd.csi.ceph.com
parameters:
  clusterID: rook-ceph-fr01
  csi.storage.k8s.io/snapshotter-secret-name: rook-csi-rbd-provisioner
  csi.storage.k8s.io/snapshotter-secret-namespace: rook-ceph-fr01
deletionPolicy: Retain
```

- [ ] **Step 2: Register it**

In `infrastructure/clusters/feather-core/rook-fr01/storageclasses/kustomization.yaml`:

```yaml
resources:
  - rbd.yaml
  - cephfs.yaml
  - bucket.yaml
  - volumesnapshotclass.yaml
```

- [ ] **Step 3: Validate and commit**

```bash
kubectl kustomize infrastructure/clusters/feather-core/rook-fr01 | grep -A9 "kind: VolumeSnapshotClass"
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/storageclasses/volumesnapshotclass.yaml \
        infrastructure/clusters/feather-core/rook-fr01/storageclasses/kustomization.yaml
git commit -m "feat(rook): add rbd volumesnapshotclass"
git push -u origin feat/rbd-volumesnapshotclass
gh pr create --title "feat(rook): add rbd volumesnapshotclass" \
  --body "Adds VolumeSnapshotClass/ceph-rbd-fr01-snapshot (driver rook-ceph.rbd.csi.ceph.com, clusterID rook-ceph-fr01, deletionPolicy Retain). The snapshot-controller and both CSI snapshotter sidecars are already deployed and idle; this is the missing piece. No workload change."
```

Expected: the `kubectl kustomize` grep prints the class; `validate.sh` exits `0` (`VolumeSnapshotClass` is a CRD, so kubeconform skips it under `-ignore-missing-schemas` — that is expected, not a pass).

- [ ] **Step 4: After merge, reconcile once and smoke-test one snapshot**

```bash
flux reconcile kustomization rook-fr01 --with-source
kubectl get volumesnapshotclass
```

Expected: one row, `ceph-rbd-fr01-snapshot`, driver `rook-ceph.rbd.csi.ceph.com`, `Retain`.

Prove it actually works — a class that renders but cannot snapshot is worthless:

```bash
kubectl apply -f - <<'EOF'
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: smoketest-node-red
  namespace: node-red
spec:
  volumeSnapshotClassName: ceph-rbd-fr01-snapshot
  source:
    persistentVolumeClaimName: node-red
EOF
kubectl get volumesnapshot -n node-red smoketest-node-red -w
```

Expected: `READYTOUSE=true` within seconds; `RESTORESIZE=10Gi` (verified live 2026-08-03: `node-red/node-red` is `10Gi`, `RWO`, `ceph-rbd-fr01`).

`node-red` is chosen deliberately: 10Gi, single RWO PVC, low-traffic, and *not* the step-ca or Postgres volume.

⚠️ **Clean up — and note that `deletionPolicy: Retain` makes the obvious cleanup wrong.** Deleting the `VolumeSnapshotContent` object under a `Retain` class **does not** call `DeleteSnapshot` on the CSI driver: the Kubernetes object goes away and the Ceph snapshot is orphaned in `feather-rbd` with nothing left pointing at it. The policy must be flipped to `Delete` on the *content object* first, exactly like the PV reclaim-policy flip in Task 7 Step 5:

```bash
CONTENT=$(kubectl get volumesnapshot -n node-red smoketest-node-red \
  -o jsonpath='{.status.boundVolumeSnapshotContentName}')
echo "content: $CONTENT"        # must be non-empty before you continue

# Flip the content to Delete FIRST, then remove the VolumeSnapshot; the
# controller then garbage-collects the content AND the Ceph snapshot.
kubectl patch volumesnapshotcontent "$CONTENT" --type=merge \
  -p '{"spec":{"deletionPolicy":"Delete"}}'
kubectl delete volumesnapshot -n node-red smoketest-node-red
kubectl get volumesnapshotcontent
```

Expected: `No resources found` (the content disappears on its own once the snapshot is gone). Then prove the Ceph snapshot really went, which is the check that actually matters:

```bash
IMG=$(kubectl get pv "$(kubectl get pvc -n node-red node-red -o jsonpath='{.spec.volumeName}')" \
  -o jsonpath='{.spec.csi.volumeAttributes.imageName}')
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- rbd snap ls "feather-rbd/$IMG"
```

Expected: no snapshot rows. **If a snapshot is still listed, remove it explicitly** with `rbd snap rm feather-rbd/$IMG@<snapname>` from the toolbox — otherwise it silently accrues copy-on-write space in a pool that is already at 71 % raw.

**Rollback:** revert the merge commit. Deleting the class does not delete existing snapshots.

---

### Task 11: Write `docs/dr-rebuild.md` and record the rehearsal result (PR 7) — [GITOPS]

**Files:**
- Create: `docs/dr-rebuild.md`

**Interfaces:**
- Consumes: the measured numbers from Task 7 Step 4, the offsite destinations from Tasks 5/9, and the etcd snapshot location from Task 4.
- Produces: the ordered bring-up procedure. This is the artefact that turns everything above from "files exist" into "someone can recover".

- [ ] **Step 0: Branch**

```bash
cd /mnt/projects/oss/onelitefeather/Kubernetes-FLUX
git checkout main && git pull --rebase origin main
git checkout -b docs/dr-rebuild
```

- [ ] **Step 1: Create `docs/dr-rebuild.md`**

Fill every **ALL-CAPS** placeholder (`<DATE>`, `<PROVIDER>`, `<BUCKET>`, `<PASS/FAIL>`, `<MM:SS>`, `<ID>`, `<N>`, `<TS>`, `<ENDPOINT_HOST>`, `<REGION>`) from what actually exists after Tasks 4-9. Lowercase placeholders (`<node>`, `<ip>`, `<talos-repo>`, `<snapshot>`, `<cluster>`) are **intentional parameters** the reader substitutes at recovery time — leave those in.

````markdown
# Disaster recovery: rebuilding feather-core from nothing

Scope: total loss of the cluster (all Talos nodes gone, or etcd quorum lost
beyond repair, or the Ceph pool unrecoverable). For a single node see
`docs/runbook-node-failure.md`; for a single disk see
`docs/runbook-osd-replacement.md`.

Last rehearsed: **<DATE>** — Postgres only, see §7.

## 0. What you need before you can do anything

Recovery is impossible without all four of these. **Verify quarterly that you
still have them**; that check is the single highest-value thing in this file.

| # | Artefact | Where it lives | Without it |
|---|---|---|---|
| 1 | GPG private key `0231831CB40B8E587B7353CBA3AF727721205A62` | operator's keyring / offline escrow | Flux cannot decrypt **anything** in this repo — no secrets, no databases, no TLS |
| 2 | `age` private key for the Talos repo (one of the 3 recipients in `/mnt/projects/lab/talos-cluster/.sops.yaml`) | operator's `.age/key.txt` + the `SOPS_AGE_CI_KEY` CI secret | Talos machine configs cannot be rendered; cluster PKI is unreachable |
| 3 | The Talos repo (`TheMeinerLP/FeatherCore`) | GitHub | no machine configs; `clusters/*/talos/base/` and `clusters/*/generated/` are git-ignored and must be regenerated with `./talos.sh gen-base` |
| 4 | The GitOps repo (`OneLiteFeatherNET/Kubernetes-FLUX`) | GitHub | no workloads |

Plus, for data: the **offsite** copies at `<PROVIDER>`, bucket `<BUCKET>`:
`etcd/`, `postgresql/`, `mariadb-galera/`. In-cluster copies
(`s3://feather-core-cluster-pg-backup`, `s3://mariadb-galera-backup`) are
assumed gone in this scenario — that is the entire premise.

## 1. Decide: recover etcd, or rebuild clean?

| | Recover from etcd snapshot | Rebuild clean and re-reconcile |
|---|---|---|
| Keeps | in-cluster-generated state: PV↔PVC bindings, cert-manager Certificates, Rook mon map, in-cluster-created Secrets, CNPG cluster state | nothing — everything comes from git |
| Needs | a recent, decryptable `*.db.age` **and** the Ceph OSDs to still hold the data those PVs point at | only the two repos and the offsite backups |
| Use when | control planes died but storage nodes are intact | storage is gone too, or the snapshot is stale/unusable |

If Ceph is gone, an etcd snapshot restores PV objects pointing at RBD images
that no longer exist. **Rebuild clean.** Do not spend the outage trying to
recover etcd into a world where its references are dangling.

## 2. Talos: install and bring up the control plane

```bash
cd <talos-repo>
export SOPS_AGE_KEY_FILE=.age/key.txt
./talos.sh gen-base          # regenerates talos/base/ + generated/talosconfig
./talos.sh render-all
./talos.sh validate
```

Boot the nodes from the Image Factory schematic pinned in the repo at
`clusters/feather-core/talos/patches/common/installer-secureboot.yaml` — today
`factory.talos.dev/metal-installer-secureboot/2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896:v1.13.4`.
It is a **SecureBoot** installer, so the boot media must match; re-generate the
same schematic ID at <https://factory.talos.dev> if you need different media for
the same extension set. Then per node:

```bash
talosctl apply-config --insecure -n <ip> -f clusters/feather-core/generated/machineconfigs/<node>.yaml
```

Then **either**:

```bash
# 2a. Recover etcd (path chosen in §1)
age -d -i .age/key.txt <snapshot>.db.age > /dev/shm/db.snapshot
talosctl --talosconfig clusters/feather-core/generated/talosconfig \
  -n 192.168.15.10 bootstrap --recover-from=/dev/shm/db.snapshot
rm -f /dev/shm/db.snapshot
```

All control planes must have wiped ephemeral partitions and their etcd service
in `Preparing` first. Add `--recover-skip-hash-check` **only** if the snapshot
came from a raw `talosctl cp /var/lib/etcd/member/snap/db` rather than
`etcd snapshot`.

```bash
# 2b. Clean bootstrap
talosctl --talosconfig clusters/feather-core/generated/talosconfig \
  -n 192.168.15.10 bootstrap
```

Verify: `kubectl get nodes` shows the three control planes `Ready`.

## 3. Flux: the two secrets that must exist before anything reconciles

**Nothing in this repo decrypts without `sops-gpg`.** Create it before
bootstrapping, or every layer fails with a decryption error.

```bash
gpg --export-secret-keys --armor 0231831CB40B8E587B7353CBA3AF727721205A62 > /dev/shm/sops.asc
kubectl create ns flux-system
kubectl create secret generic sops-gpg -n flux-system --from-file=sops.asc=/dev/shm/sops.asc
rm -f /dev/shm/sops.asc
```

Then bootstrap (the deploy key comes from the Talos repo's SOPS store):

```bash
cd <talos-repo> && ./talos.sh flux-key      # -> clusters/feather-core/generated/flux-deploy-key
flux bootstrap git \
  --url=ssh://git@github.com/OneLiteFeatherNET/Kubernetes-FLUX.git \
  --branch=main --path=clusters/feather-core \
  --private-key-file=<talos-repo>/clusters/feather-core/generated/flux-deploy-key
```

The `flux-system` Secret (`identity`, `identity.pub`, `known_hosts`) is created
by `flux bootstrap` itself. `sops-gpg` is **not** — that is the one you must
place by hand.

## 4. Let the layers come up in dependency order

Flux enforces the order itself; do not reconcile ahead of it. Expected
sequence (`flux get kustomizations -A -w`):

```
base-sources -> base-controllers -> controllers -> base-configs
             -> rook -> rook-fr01 -> configs -> base-apps -> apps
                                            \-> monitoring
rbac and internal-certs run alongside.
```

`rook-fr01` is the long pole: it creates the `CephCluster`, mons, OSDs and the
`feather-rbd` pool from bare disks (`deviceFilter: "^sd[b-e]$"` on
`fr01-str-01..03`). Wait for
`kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph status` →
`HEALTH_OK` with 12 OSDs `up` before expecting any PVC to bind.

`configs` will create an **empty** Postgres cluster and an **empty** Galera
cluster. That is expected — §5 and §6 put the data back.

## 5. Postgres: restore from the offsite ObjectStore

Suspend the `configs` layer so Flux does not fight you, then replace the
`Cluster` bootstrap.

```bash
flux suspend kustomization configs
flux get kustomizations -A | grep configs      # SUSPENDED must be True
```

⚠️ While `configs` is suspended, `base-apps` → `apps` and `monitoring` will not
reconcile either (they `dependsOn` it). That is fine during a rebuild — but you
**must** resume it at the end of this section or the cluster stays half-built:

```bash
flux resume kustomization configs
flux get kustomizations -A                     # every layer back to READY=True
```

Recovery manifest, verbatim from the rehearsal in §7:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: feather-core-cluster-pg
  namespace: cnpg-system
spec:
  instances: 3
  imageName: ghcr.io/cloudnative-pg/postgresql:17
  storage:
    size: 75Gi
    storageClass: ceph-rbd-fr01
  bootstrap:
    recovery:
      source: offsite
  externalClusters:
    - name: offsite
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters:
          barmanObjectName: s3-store-offsite
          serverName: feather-core-cluster-pg
  plugins:
    - name: barman-cloud.cloudnative-pg.io
      isWALArchiver: true
      parameters:
        barmanObjectName: s3-store
```

The `ObjectStore` CRs and the `cnpg-backup` / `cnpg-backup-offsite` Secrets
come from git and already exist at this point.

**RPO: up to 7 days.** The offsite store holds weekly base backups and no WAL
archive; recovery lands at the consistency point of the newest one. The daily
in-cluster backup gives a much better RPO but is gone in this scenario.

Once recovered and healthy, remove `bootstrap.recovery` / `externalClusters`
from git so the committed `cluster.yaml` matches reality again.

## 6. MariaDB: restore from the offsite PhysicalBackup

```yaml
apiVersion: k8s.mariadb.com/v1alpha1
kind: Restore
metadata:
  name: restore-offsite
  namespace: mariadb-galera
spec:
  mariaDbRef:
    name: mariadb-galera
  s3:
    bucket: <BUCKET>
    prefix: mariadb-galera
    endpoint: <ENDPOINT_HOST>
    region: <REGION>
    tls:
      enabled: true
    accessKeyIdSecretKeyRef:
      name: mariadb-backup-offsite
      key: access-key-id
    secretAccessKeySecretKeyRef:
      name: mariadb-backup-offsite
      key: secret-access-key
```

**RPO: up to 7 days.** ⚠️ **This path has never been rehearsed** (deferred for
Ceph capacity reasons — see the theme plan). Treat the first real run as
exploratory and expect to iterate.

Everything in Galera comes back with it, including the **uptime-kuma monitor
configuration** — uptime-kuma runs with `volume.enabled: false` and keeps all
state in the `uptime-kuma` database. A human-readable inventory also lives at
`docs/uptime-kuma-monitors.sops.yaml`.

## 7. Rehearsal record

| Date | Scope | Result |
|---|---|---|
| **<DATE>** | Postgres, offsite `s3-store-offsite` → throwaway 1-instance `dr-rehearsal-pg` | **<PASS/FAIL>**. Recovery wall-clock **<MM:SS>**. Backup ID `<ID>`. Databases restored: `<N>`. Newest transaction recovered: `<TS>`. Notes: `<…>` |
| — | MariaDB | not yet rehearsed |
| — | Full cluster rebuild | not yet rehearsed |

**Re-run the Postgres rehearsal at least every 6 months**, and after any CNPG
or barman-plugin major upgrade. The procedure is Task 7 of
`docs/superpowers/plans/2026-08-03-offsite-backups-and-disaster-recovery.md`.

## 8. What is knowingly not recoverable

- **Loki / Mimir / Tempo history.** ~2.5 TiB in `feather-s3.rgw.buckets.data`,
  not mirrored offsite. Accepted loss. Dashboards and rules come from git;
  only the data behind them is gone.
- **Harbor image layers, Reposilite artifacts, BlueMap tiles.** Same bucket,
  same decision. Rebuildable from upstream/CI.
- **PVCs with no backup path** (31 of 37 as of 2026-08-03): dragonfly (cache,
  by design), ollama models (re-pullable), node-red flows, Plane's
  OpenSearch/RabbitMQ, dependency-track and harbor-trivy scratch data.
- **step-ca's issued-certificate database** (`step-ca/database-step-ca-step-certificates-0`,
  15Gi). **The CA keys themselves are safe** — `root_ca_key`,
  `intermediate_ca_key` and `ca_password` are SOPS-encrypted in
  `infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml`
  and come back with the repo. What is lost is issuance/revocation history;
  cert-manager + step-issuer re-mint the short-lived leaf certs automatically.
````

- [ ] **Step 2: Validate and commit**

```bash
grep -nE '<[A-Z][A-Z_/ .]*>' docs/dr-rebuild.md
```

Expected: **no output**. Any hit is an unfilled ALL-CAPS placeholder and the doc is not finished. (This deliberately ignores the lowercase `<node>` / `<ip>` / `<cluster>` parameters, which must stay.)

```bash
./scripts/validate.sh
git add docs/dr-rebuild.md
git commit -m "docs: add disaster-recovery rebuild runbook with rehearsal record"
git push -u origin docs/dr-rebuild
gh pr create --title "docs: add disaster-recovery rebuild runbook with rehearsal record" \
  --body "$(cat <<'EOF'
## Summary
- docs/dr-rebuild.md: ordered total-loss recovery (Talos install -> sops-gpg + flux bootstrap -> layer order -> Postgres/MariaDB restore), the four artefacts without which recovery is impossible, and the recorded result of the Postgres restore rehearsal.

## Test plan
- [x] ./scripts/validate.sh passes
- [x] No unfilled ALL-CAPS placeholders (`grep -nE '<[A-Z][A-Z_/ .]*>'` is empty)
- [ ] Docs-only; no cluster change.
EOF
)"
```

Expected: `validate.sh` exits `0`.

**Rollback:** revert. Docs-only.

---

### Task 12: Export the uptime-kuma monitor inventory (PR 8) — [GITOPS]

**Files:**
- Create: `docs/uptime-kuma-monitors.sops.yaml`

**Interfaces:**
- Consumes: read access to the `uptime-kuma` database in Galera.
- Produces: a human-readable inventory that survives a restore, independent of the DB.

- [ ] **Step 0 — DECISION GATE DG-4: this repo is public**

uptime-kuma has 25 monitors (`select count(*) from monitor`) of types `group`, `http`, `port`, `postgres`, `mysql`. The `monitor` table also contains `basic_auth_pass`, `mqtt_password`, `radius_password`, `oauth_client_secret`, `database_connection_string` and `push_token` — real secrets — and the URLs/hostnames are an internal topology map.

| Option | | |
|---|---|---|
| A. Commit plaintext to `docs/` | ❌ leaks internal topology on a public repo | reject |
| B. Commit column-scoped and **SOPS-encrypted** as `docs/uptime-kuma-monitors.sops.yaml` | root `.sops.yaml` rule `.*\.sops\.ya?ml$` covers it; `docs/` is not a Flux path so nothing tries to apply it | **recommended** |
| C. Do not commit; rely on the offsite MariaDB backup from Task 9 | already true and already sufficient for recovery | acceptable if the owner prefers less secret sprawl |

The audit's premise ("only in the Galera DB") is **partly out of date once Task 9 lands** — the Galera DB is then offsite too. This task's value is redundancy and human-readability during an outage, not the only copy. Say so to the owner and let them choose B or C.

If C is chosen, skip this task and note the decision in `docs/dr-rebuild.md` §6.

- [ ] **Step 1: Export, scoped to non-secret columns only**

Note the deliberately narrow column list. Do **not** use `SELECT *`. The export
goes through `JSON_ARRAYAGG` rather than `--xml`/TSV: MariaDB emits well-formed
JSON regardless of tabs, newlines or quotes in `name`/`description`, and no XML
parser is involved (Python's stdlib XML parsers are XXE/billion-laughs prone and
there is no reason to introduce one here).

Write the query to a file first — quoting it through `kubectl exec … sh -c` is
otherwise a guaranteed source of shell-escaping bugs.

```bash
cd /mnt/projects/oss/onelitefeather/Kubernetes-FLUX
git checkout main && git pull --rebase origin main
git checkout -b docs/uptime-kuma-inventory

cat > /tmp/uk-export.sql <<'SQL'
SELECT JSON_ARRAYAGG(JSON_OBJECT(
  'id', id, 'parent', parent, 'name', name, 'type', type, 'active', active,
  'url', url, 'hostname', hostname, 'port', port, 'interval', `interval`,
  'retry_interval', retry_interval, 'maxretries', maxretries,
  'keyword', keyword, 'upside_down', upside_down,
  'accepted_statuscodes_json', accepted_statuscodes_json,
  'description', description))
FROM monitor;
SQL
# -N: no column header. --raw: do NOT escape \, \t, \n in output -- without it
# the client mangles the escape sequences inside the JSON strings and the
# `description` field comes back with doubled backslashes. -D: pick the schema
# by flag; `uptime-kuma` contains a hyphen and is not a valid bare argument.
kubectl exec -i -n mariadb-galera mariadb-galera-0 -c mariadb -- sh -c \
  'mariadb -u root -p"$MARIADB_ROOT_PASSWORD" -N --raw -D "uptime-kuma"' \
  < /tmp/uk-export.sql > /tmp/uptime-kuma-monitors.json

python3 -c "import json;d=json.load(open('/tmp/uptime-kuma-monitors.json'));print('rows:',len(d))"

python3 - <<'PY' > docs/uptime-kuma-monitors.sops.yaml
import json, yaml, datetime
rows = json.load(open('/tmp/uptime-kuma-monitors.json'))
rows.sort(key=lambda r: r['id'])
print(yaml.safe_dump({
    'exported_at': datetime.datetime.now(datetime.UTC).isoformat(),
    'source': 'mariadb-galera / uptime-kuma / table monitor (non-secret columns only)',
    'monitors': rows,
}, sort_keys=False, allow_unicode=True), end='')
PY
rm -f /tmp/uk-export.sql /tmp/uptime-kuma-monitors.json
```

Expected: `rows: 25` (live count on 2026-08-03). If `json.load` raises, the client escaped the output — check that `--raw` is present. If it prints `rows: 0` or the file is the literal string `NULL`, `JSON_ARRAYAGG` found no rows and you are pointed at the wrong schema.

- [ ] **Step 2: Confirm no secret leaked into the export, then encrypt**

```bash
grep -icE 'password|secret|token|basic_auth|connection_string' docs/uptime-kuma-monitors.sops.yaml
grep -c 'name:' docs/uptime-kuma-monitors.sops.yaml
```

Expected: the first command prints `0`; the second prints ≥ 25. **If the first is non-zero, stop and fix the column list.**

```bash
sops --encrypt --in-place docs/uptime-kuma-monitors.sops.yaml
head -5 docs/uptime-kuma-monitors.sops.yaml
```

Expected: `ENC[AES256_GCM,…]` values. ⚠️ Verify this *before* `git add` — a plaintext commit to a public repo cannot be un-published by a later force-push.

- [ ] **Step 3: Validate and commit**

```bash
./scripts/validate.sh
git add docs/uptime-kuma-monitors.sops.yaml
git commit -m "docs: export uptime-kuma monitor inventory (sops-encrypted)"
git push -u origin docs/uptime-kuma-inventory
gh pr create --title "docs: export uptime-kuma monitor inventory (sops-encrypted)" \
  --body "SOPS-encrypted, column-scoped export of the 25 uptime-kuma monitors so the inventory survives a restore independently of the Galera DB. No secret-bearing columns are exported (verified: grep for password/secret/token/basic_auth/connection_string returns 0). docs/ is not a Flux path, so nothing applies this."
```

Expected: `validate.sh` exits `0`.

Add a line to `docs/dr-rebuild.md` §6 pointing at it (already drafted in Task 11 — if Task 11's PR has already merged, that line is present and no edit is needed; if this task runs first, add it there).

**Rollback:** revert. Note that a plaintext leak is *not* rollback-able — hence Step 2.

**Staleness:** this snapshot rots. Re-export whenever monitors change materially; it is a convenience copy, not the source of truth (the DB is).

---

### Task 13: Schedule RBD snapshots for the irreplaceable PVCs (PR 9) — [GITOPS] — **BLOCKED on the capacity theme**

🚧 **Do not start this task until `ceph-capacity-reclamation-and-retention` has landed and this passes:**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | grep -E 'RAW USED|feather-rbd'
```

Required: `%RAW USED` meaningfully below 71.26 % (the 2026-08-03 reading) and `feather-rbd` `MAX AVAIL` above 1 TiB. RBD snapshots are copy-on-write **inside `feather-rbd`** — they grow as the source volume changes, and scheduling them on a pool near backfillfull is how you cause the incident you were trying to prevent.

**Files:**
- Create: `infrastructure/clusters/feather-core/rook-fr01/storageclasses/volumesnapshotclass-rotating.yaml`
- Create: `infrastructure/base/configs/volume-snapshot-scheduler/` (namespace, ServiceAccount, ClusterRole, ClusterRoleBinding, CronJob)
- Create: `infrastructure/clusters/feather-core/configs/volume-snapshot-scheduler/kustomization.yaml`
- Modify: `infrastructure/clusters/feather-core/configs/kustomization.yaml`

**Interfaces:**
- Consumes: `VolumeSnapshotClass` from Task 10.
- Produces: rotating daily snapshots of `step-ca/database-step-ca-step-certificates-0` (15Gi), `node-red/node-red` (10Gi), `plane/pvc-plane-opensearch-vol-plane-opensearch-wl-0` (5Gi).

**Scope note, and an honest correction to the audit.** The audit frames step-ca as "the internal PKI database, currently zero backup" and implies losing it means re-minting every certificate. That overstates it: `root_ca_key`, `intermediate_ca_key` and `ca_password` are SOPS-encrypted in `infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml` and survive total cluster loss with the repo. The PVC holds issuance/revocation history and provisioner state, which cert-manager + step-issuer rebuild automatically. Snapshotting it is still worth doing — corruption or a bad upgrade is a real scenario and `reclaimPolicy: Retain` does nothing for either — but it is *not* the crown jewel the finding describes, and it does not justify jumping the capacity gate.

Also note: **an RBD snapshot lives in the same Ceph pool as its source.** It protects against corruption, a bad app upgrade and an accidental delete. It does **not** protect against cluster loss. Do not let this task create the impression that these three PVCs are now "backed up offsite" — they are not, and `docs/dr-rebuild.md` §8 says so.

- [ ] **Step 1: Add a rotating class**

`ceph-rbd-fr01-snapshot` from Task 10 is `deletionPolicy: Retain`, which means a scheduler using it would never actually reclaim space. Add a sibling for rotating snapshots:

```yaml
# Companion to ceph-rbd-fr01-snapshot for SCHEDULED snapshots only.
# deletionPolicy: Delete so retention actually frees space in feather-rbd --
# with Retain the pruner would delete VolumeSnapshot objects while leaving
# every Ceph snapshot behind, which is worse than not pruning at all.
# Use ceph-rbd-fr01-snapshot (Retain) for anything taken by hand.
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata:
  name: ceph-rbd-fr01-snapshot-rotating
driver: rook-ceph.rbd.csi.ceph.com
parameters:
  clusterID: rook-ceph-fr01
  csi.storage.k8s.io/snapshotter-secret-name: rook-csi-rbd-provisioner
  csi.storage.k8s.io/snapshotter-secret-namespace: rook-ceph-fr01
deletionPolicy: Delete
```

- [ ] **Step 2: Build the scheduler CronJob**

⚠️ **This step is a specification, not a transcription — unlike every other task in this plan it does not hand you finished YAML.** Budget real implementation time: a namespace, a ServiceAccount, three namespaced `Role`s + `RoleBinding`s, the `CronJob` with its snapshot/prune script, and a `kustomization.yaml` per directory. Do not start it at 22:00 expecting a copy-paste. If the capacity gate is green but you have no appetite for writing a scheduler, land Task 10's `VolumeSnapshotClass` (already merged by then) and take snapshots by hand before risky operations — that captures most of the value at none of the cost.

There is no built-in VolumeSnapshot scheduler in Kubernetes and none is deployed here (`kubectl get cronjob -A` → only `descheduler/descheduler`, verified 2026-08-03). The minimum viable version is a `CronJob` running `kubectl`, with a `Role` scoped to `volumesnapshots` in each of the three target namespaces.

Design constraints for the implementer:
- Image: use one already present on the cluster if possible; otherwise `registry.k8s.io/kubectl:v1.36.1`, pinned.
- The Job creates `VolumeSnapshot` named `<pvc>-<UTC-date>` with `volumeSnapshotClassName: ceph-rbd-fr01-snapshot-rotating`, then deletes any snapshot for that PVC older than `KEEP_DAYS` (recommend 7).
- RBAC: `Role` per namespace (`step-ca`, `node-red`, `plane`) granting `get,list,create,delete` on `snapshot.storage.k8s.io/volumesnapshots` — **not** a cluster-wide `ClusterRole`.
- Schedule: `30 1 * * *` (01:30 UTC) — before the 02:00 Postgres backup and the 03:30 etcd snapshot.
- `concurrencyPolicy: Forbid`, `successfulJobsHistoryLimit: 3`, `failedJobsHistoryLimit: 3`.
- Namespace `volume-snapshot-scheduler`, wired into the `configs` layer.

⚠️ **An RBD snapshot of a running database is crash-consistent, not application-consistent.** For step-ca (an embedded badger DB) that is normally recoverable but not guaranteed. Say so in a comment in the CronJob manifest so nobody later mistakes it for a clean backup.

- [ ] **Step 3: Verify after merge**

```bash
flux reconcile kustomization configs --with-source
kubectl get cronjob -n volume-snapshot-scheduler
kubectl create job -n volume-snapshot-scheduler manual-test --from=cronjob/volume-snapshot-scheduler
kubectl get volumesnapshot -A
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- rbd snap ls feather-rbd/<image> 2>/dev/null | head
```

Expected: three `READYTOUSE=true` VolumeSnapshots; a matching Ceph snapshot per image.

Then, **8 days later**, confirm pruning works:

```bash
kubectl get volumesnapshot -A
kubectl get volumesnapshotcontent | wc -l
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph df | grep feather-rbd
```

Expected: at most 7 snapshots per PVC; `volumesnapshotcontent` count tracking it; `feather-rbd` `STORED` growing slowly and predictably, not linearly forever. **If `STORED` climbs without bound, suspend the CronJob immediately** (`kubectl patch cronjob -n volume-snapshot-scheduler volume-snapshot-scheduler -p '{"spec":{"suspend":true}}'`) and investigate before it eats the headroom the capacity theme just reclaimed.

**Rollback:** revert the merge commit, then delete the leftover snapshots by hand (`Delete` policy means deleting the `VolumeSnapshot` objects also removes the Ceph snapshots).

---

## Summary of PRs and gates

| # | Task | PR title / branch | Repo | Gate before the next one |
|---|---|---|---|---|
| — | 1 | hand-taken etcd snapshot (no PR) | — | decrypt round-trips byte-for-byte (Task 1 Step 3) |
| 1 | 2 | `docs: add node-failure and osd-replacement runbooks` — `docs/dr-runbooks` | GITOPS | none |
| 2 | 3 | `feat: add etcd snapshot command with age encryption and systemd timer` — `feat/etcd-snapshot` | TALOS | scoped talosconfig is provably *not* admin (Task 3 Step 6); **merge before Task 4** |
| — | 4 | enable the timer (host config) | host | **two consecutive automatic snapshots offsite** |
| 3 | 5 | `feat(postgresql): add offsite barman objectstore` — `feat/offsite-postgres-backup` | GITOPS | DG-1, DG-3 answered; `rclone lsd offsite:` works |
| — | 6 | probe `Backup` | — | **real bytes at the provider** |
| — | 7 | restore rehearsal | — | **a throwaway cluster comes up with the real databases in it**, then torn down with the reclaim-policy flip |
| 4 | 8 | `feat(postgresql): schedule weekly offsite base backup` — `feat/offsite-postgres-schedule` | GITOPS | first Sunday backup completes |
| 5 | 9 | `feat(mariadb): add weekly offsite physical backup` — `feat/offsite-mariadb-backup` | GITOPS | one `onDemand` run completes and lands offsite |
| 6 | 10 | `feat(rook): add rbd volumesnapshotclass` — `feat/rbd-volumesnapshotclass` | GITOPS | smoke-test snapshot `READYTOUSE=true`, **and the Ceph snapshot verifiably gone** |
| 7 | 11 | `docs: add disaster-recovery rebuild runbook with rehearsal record` — `docs/dr-rebuild` | GITOPS | no ALL-CAPS placeholders left |
| 8 | 12 | `docs: export uptime-kuma monitor inventory (sops-encrypted)` — `docs/uptime-kuma-inventory` | GITOPS | DG-4; zero secret-column hits; encrypted *before* `git add` |
| 9 | 13 | scheduled RBD snapshots | GITOPS | 🚧 **capacity theme landed first** |

After every GitOps merge that touches the `configs` layer (PRs 3, 4, 5): `flux get kustomizations -A` must show `configs` `READY=True` at the new revision within ~12 minutes, or revert — `base-apps`, `apps` and `monitoring` all `dependsOn` it.

## Things this plan could not verify

1. **Whether an always-on non-cluster host exists on the LAN** to run the etcd snapshot timer. Neither repo has a machine inventory. DG-2.
2. **Whether the barman instance sidecar can use an `ObjectStore` that is not listed in `Cluster.spec.plugins`.** Upstream docs say yes; it was not testable read-only. Task 6 Step 3 carries the fallback (a rolling restart of the Postgres instances).
3. **Whether a base backup with no WAL archive in the same destination is restorable at all.** This is the load-bearing assumption of the offsite Postgres design, which is exactly why Task 7 is a hard gate and Task 7 Step 6 carries an alternative design (`rclone sync` of the whole in-cluster bucket).
4. **Cluster egress to the chosen provider.** There are no `NetworkPolicy` objects restricting `cnpg-system` or `mariadb-galera` (`kubectl get networkpolicy -A`, 2026-08-03) and Flux pulls charts from the internet, so egress almost certainly works — but it was not proven from those two namespaces specifically.
5. **`talosctl config new` endpoint propagation.** The `cmd_snapshot-config` helper re-writes endpoints defensively because it was not confirmed whether `config new` copies them from the source context.
6. **There is no `talosctl etcd snapshot-status`.** Task 1 Step 3 therefore validates the encrypted copy by byte-count equality against the plaintext, and uses `etcdutl snapshot status` only if that binary happens to be installed. A byte-identical decrypt proves the age round-trip, not that the snapshot itself is internally consistent — the only real proof of that is a `bootstrap --recover-from`, which is not rehearsable without spare hardware.
7. **The MariaDB restore path has never been exercised**, offsite or in-cluster. Stated plainly in `docs/dr-rebuild.md` §6 rather than glossed over.
8. **Whether CNPG accepts two `spec.plugins` entries sharing the same plugin `name`.** The CRD stores `plugins` as an untyped array, but the operator's internal handling was not inspectable read-only. Only relevant if Task 6 Step 3's fallback is needed; that step now carries the check and the abort path.
9. **Whether `mariadb-operator` serialises two `PhysicalBackup` CRs against the same `MariaDB`.** Assumed *not* — hence the Sunday 06:30 slot and the 5 h timeout in Task 9, chosen so the offsite run cannot still hold the shared staging `emptyDir` when the next in-cluster run fires. If it turns out the operator does serialise, the scheduling constraint is harmless anyway.
10. **Actual uplink throughput to the chosen provider.** The ~7 GiB weekly MariaDB upload is sized from the measured gzip ratio, not from a measured transfer. Task 9 Step 5's manual run is the first real data point; if it exceeds the 5 h timeout, raise the timeout *and* move the cron rather than only raising the timeout.
