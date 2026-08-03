# RGW Bucket Ownership Fix (declarative `bucketOwner`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permanently fix the cluster-wide RGW `403 AccessDenied` incident (see `docs/incidents/2026-07-18-mariadb-upgrade-and-rgw-access-denied.md`) by making each named `CephObjectStoreUser` the actual, declared owner of its bucket — via Rook's own `ObjectBucketClaim.spec.additionalConfig.bucketOwner` field — instead of a one-off manual `radosgw-admin bucket link` command. Once this is in place, no future bucket ever needs manual re-linking: creating a fresh OBC with `bucketOwner` set is Rook's own supported, git-tracked pattern.

**Architecture:** Two-phase rollout per bucket, done once per app-group: (1) declare `additionalConfig.bucketOwner: <user>` on the `ObjectBucketClaim` YAML in this repo and push it, (2) delete the existing (already-`Bound`) OBC object so Rook re-provisions it — Rook's `Provision()` path is the ONLY one that applies `bucketOwner`, and its own documented behavior is "if the bucket already exists and is owned by a different user, the bucket will be re-linked to the specified user." This never touches object data: the `ceph-bucket-fr01` StorageClass has `reclaimPolicy: Retain`, confirmed live, so deleting the `ObjectBucketClaim` never deletes the underlying Ceph bucket. Every `ObjectBucketClaim` lives in the `rook-ceph-fr01` namespace — deleting/recreating one never touches any consuming app's own namespace, Deployments, or Secrets.

**Tech Stack:** FluxCD (Kustomize, HelmRelease), Rook v1.20.2, Ceph v19.2.5 (Squid), `ObjectBucketClaim`/`CephObjectStoreUser` CRDs.

## Global Constraints

- `./scripts/validate.sh` must pass before every commit.
- Conventional Commits: types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`, subject lowercase, header ≤100 chars.
- Never hammer `flux reconcile` in a loop — one reconcile per stage, then watch.
- **Confirmed live, do not re-derive:** `ceph-bucket-fr01` StorageClass has `reclaimPolicy: Retain`. `rook-ceph-operator-config` ConfigMap currently has `ROOK_OBC_ALLOW_ADDITIONAL_CONFIG_FIELDS: "maxObjects,maxSize"` (rendered from the `rook-ceph` HelmRelease's `obcAllowAdditionalConfigFields` value, which is unset in this repo today, so the chart default applies). Existing `ObjectBucketClaim` status uses `status.phase` with capitalized values (`Bound`, confirmed via `kubectl get objectbucketclaim ... -o jsonpath`).
- **Locked-in bucket → named-user mapping** (do not re-derive, verified in the parent investigation — 10 via direct repo config citation, 5 reposilite-\* via live RGW access-log traffic since repo config for Reposilite's per-repository storage is not GitOps-tracked):
  - `mariadb-galera-backup` → `mariadb`
  - `loki-chunks` → `loki`, `loki-ruler` → `loki`
  - `mimir-alertmanager` → `mimir`, `mimir-blocks` → `mimir`, `mimir-ruler` → `mimir`
  - `tempo-traces` → `tempo`
  - `bluemap0` → `bluemap`
  - `harbor` → `harbor`
  - `outline` → `outline`
  - `plane` → `plane`
  - `reposilite-onelitefeather-proxy` → `reposilite`, `reposilite-onelitefeather-releases` → `reposilite`, `reposilite-onelitefeather-snapshots` → `reposilite`, `reposilite-releases` → `reposilite`, `reposilite-snapshots` → `reposilite`
- **Explicitly OUT of scope:** `feather-core-cluster-pg-backup` (CNPG Postgres WAL/base backups). It already authenticates as its own bucket owner directly (confirmed live: `cnpg-backup` secret's access key matches the bucket's current OBC-owner key exactly) and has already recovered (`pg_stat_archiver` showing continuous successful archiving). Do not touch this bucket or its OBC.
- **Hard gate:** do not start Task 3 (bulk rollout) until Task 2's canary (`mariadb-galera-backup`) passes every verification step, including a real end-to-end `PhysicalBackup` run — not just a synthetic S3 test.
- Every "delete the OBC" step is scoped to `namespace: rook-ceph-fr01` only — never delete anything in an app's own namespace.

---

### Task 1: Allow-list `bucketOwner` on the rook-ceph operator

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook/release.yaml`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `rook-ceph-operator-config` ConfigMap with `ROOK_OBC_ALLOW_ADDITIONAL_CONFIG_FIELDS` including `bucketOwner` — every later task's `additionalConfig.bucketOwner` field is silently ignored by Rook without this.

- [ ] **Step 1: Add the Helm value**

In `infrastructure/clusters/feather-core/rook/release.yaml`, add a new top-level key under `spec.values` (alongside the existing `crds:`, `monitoring:`, `resources:`, etc. — insert after `monitoring:` block, before `resources:`):

```yaml
    obcAllowAdditionalConfigFields: "maxObjects,maxSize,bucketOwner"
```

- [ ] **Step 2: Validate**

Run: `kubectl kustomize infrastructure/clusters/feather-core/rook | grep -A2 "obcAllowAdditionalConfigFields\|chart: rook-ceph"`

Expected: the rendered `HelmRelease` includes `obcAllowAdditionalConfigFields: maxObjects,maxSize,bucketOwner` under `values`.

Then run: `./scripts/validate.sh` — expect exit 0, `rook` group `Invalid: 0, Errors: 0`.

- [ ] **Step 3: Commit and push**

```bash
git add infrastructure/clusters/feather-core/rook/release.yaml
git commit -m "fix(rook): allow bucketOwner in OBC additionalConfig"
git push origin HEAD:main
```

(If `git push` rejects as non-fast-forward, `git fetch origin main && git rebase origin/main` first — `main` moves under you in this repo, per `CLAUDE.md`.)

- [ ] **Step 4: Reconcile once and verify the ConfigMap picked it up**

```bash
flux reconcile source git flux-system
flux reconcile kustomization rook --with-source
kubectl get cm rook-ceph-operator-config -n rook-ceph -o jsonpath='{.data.ROOK_OBC_ALLOW_ADDITIONAL_CONFIG_FIELDS}'
```

Expected output: `maxObjects,maxSize,bucketOwner`. Do not proceed to Task 2 until this exact string is confirmed live.

---

### Task 2: Canary — `mariadb-galera-backup`

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/mariadb-galera-backup.yaml`

**Interfaces:**
- Consumes: `obcAllowAdditionalConfigFields` including `bucketOwner`, confirmed live by Task 1.
- Produces: a proven, repeatable 6-step per-bucket procedure (declare → push → reconcile → delete OBC → verify ownership/data/access) that Task 3 repeats verbatim for the remaining 15 buckets.

- [ ] **Step 1: Capture the PRE-fix state**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=mariadb-galera-backup --rgw-realm=feather-s3 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('owner:', d['owner']); print('num_objects:', d['usage']['rgw.main']['num_objects']); print('size:', d['usage']['rgw.main']['size'])"
```

Write down the three values — Step 6 must match `num_objects` and `size` exactly, and `owner` must have changed to `mariadb`.

- [ ] **Step 2: Add `additionalConfig.bucketOwner` to the OBC**

`infrastructure/clusters/feather-core/rook-fr01/buckets/mariadb-galera-backup.yaml` currently reads:

```yaml
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: mariadb-galera-backup
  namespace: rook-ceph-fr01
spec:
  bucketName: mariadb-galera-backup
  storageClassName: ceph-bucket-fr01
```

Change it to:

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
```

- [ ] **Step 3: Validate, commit, push**

```bash
kubectl kustomize infrastructure/clusters/feather-core/rook-fr01/buckets | grep -A3 "name: mariadb-galera-backup"
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/buckets/mariadb-galera-backup.yaml
git commit -m "fix(rook-fr01): set bucketOwner=mariadb on mariadb-galera-backup OBC"
git push origin HEAD:main
```

(Rebase on `origin/main` first if the push is rejected, same as Task 1 Step 3.)

- [ ] **Step 4: Reconcile the source, then force re-provisioning by deleting the existing OBC**

```bash
flux reconcile source git flux-system
kubectl delete objectbucketclaim mariadb-galera-backup -n rook-ceph-fr01
flux reconcile kustomization rook-fr01 --with-source
```

(Deleting first and then reconciling is deliberate — Flux/Kustomize would otherwise just patch the live object in place, since the name is unchanged (`generatorOptions.disableNameSuffixHash: true` convention throughout this repo) and `additionalConfig` is only read by Rook's creation path. Deleting forces a real recreation.)

- [ ] **Step 5: Wait for it to re-provision and reach `Bound`**

```bash
kubectl wait --for=jsonpath='{.status.phase}'=Bound objectbucketclaim/mariadb-galera-backup -n rook-ceph-fr01 --timeout=5m
```

Expected: `objectbucketclaim.objectbucket.io/mariadb-galera-backup condition met` within well under 5 minutes (this is a metadata-only re-link, not a data copy).

- [ ] **Step 6: Verify ownership changed and data is untouched**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=mariadb-galera-backup --rgw-realm=feather-s3 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('owner:', d['owner']); print('num_objects:', d['usage']['rgw.main']['num_objects']); print('size:', d['usage']['rgw.main']['size'])"
```

Expected: `owner: mariadb` (no longer the `obc-rook-ceph-fr01-mariadb-galera-backup-...` UUID), and `num_objects`/`size` **identical** to Step 1's recorded values.

- [ ] **Step 7: Verify live S3 access as the `mariadb` user (not just synthetic — a real backup)**

```bash
kubectl patch physicalbackup mariadb-galera-backup -n mariadb-galera \
  --type merge -p '{"spec":{"schedule":{"onDemand":"bucketowner-fix-'"$(date +%s)"'"}}}'
kubectl wait -n mariadb-galera physicalbackup/mariadb-galera-backup --for=condition=complete --timeout=90m
kubectl get physicalbackup mariadb-galera-backup -n mariadb-galera
```

Expected: `COMPLETE=True STATUS=Success`. This is the real gate — a synthetic SigV4 test proves auth works, but a full `PhysicalBackup` run proves the whole pipeline (mariadb-backup → compress → S3 push) works end to end on the new ownership.

**Gate:** if Step 6 or Step 7 fails, stop — do not proceed to Task 3. Investigate (check `kubectl get objectbucketclaim mariadb-galera-backup -n rook-ceph-fr01 -o yaml` for a `Failed`/error phase, check `rook-ceph-operator` logs for `mariadb-galera-backup`) before touching any other bucket.

---

### Task 3: Roll out to the observability stack (loki, mimir, tempo — 6 buckets)

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/loki-chunks.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/loki-ruler.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/mimir-alertmanager.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/mimir-blocks.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/mimir-ruler.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/tempo-traces.yaml`

**Interfaces:**
- Consumes: Task 2's proven procedure and the Task 2 gate having passed.
- Produces: loki, mimir, tempo fully recovered (these three are the ones still 100%/majority failing live, per the parent incident's last live check — highest-priority group to unblock).

- [ ] **Step 1: Add `additionalConfig.bucketOwner` to all 6 OBC files**

Each file follows the exact same pattern as Task 2 Step 2 — add this block to `spec` (indentation matches the existing `bucketName`/`storageClassName` keys in each file):

```yaml
  additionalConfig:
    bucketOwner: loki
```
for `loki-chunks.yaml` and `loki-ruler.yaml` (both get `bucketOwner: loki`);

```yaml
  additionalConfig:
    bucketOwner: mimir
```
for `mimir-alertmanager.yaml`, `mimir-blocks.yaml`, `mimir-ruler.yaml` (all three get `bucketOwner: mimir`);

```yaml
  additionalConfig:
    bucketOwner: tempo
```
for `tempo-traces.yaml`.

- [ ] **Step 2: Validate, commit, push (one commit for all 6 — same app-family, same change)**

```bash
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/buckets/loki-chunks.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/loki-ruler.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/mimir-alertmanager.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/mimir-blocks.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/mimir-ruler.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/tempo-traces.yaml
git commit -m "fix(rook-fr01): set bucketOwner on loki/mimir/tempo OBCs"
git push origin HEAD:main
```

- [ ] **Step 3: Reconcile source, delete all 6 OBCs, re-reconcile**

```bash
flux reconcile source git flux-system
kubectl delete objectbucketclaim loki-chunks loki-ruler mimir-alertmanager mimir-blocks mimir-ruler tempo-traces -n rook-ceph-fr01
flux reconcile kustomization rook-fr01 --with-source
```

- [ ] **Step 4: Wait for all 6 to reach `Bound`**

```bash
for b in loki-chunks loki-ruler mimir-alertmanager mimir-blocks mimir-ruler tempo-traces; do
  kubectl wait --for=jsonpath='{.status.phase}'=Bound objectbucketclaim/$b -n rook-ceph-fr01 --timeout=5m
done
```

- [ ] **Step 5: Verify ownership + data integrity for all 6**

```bash
for b in loki-chunks loki-ruler mimir-alertmanager mimir-blocks mimir-ruler tempo-traces; do
  echo "== $b =="
  kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=$b --rgw-realm=feather-s3 \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('owner:', d['owner']); print('num_objects:', d['usage']['rgw.main']['num_objects'])"
done
```

Expected: `owner` is `loki`/`mimir`/`tempo` respectively (matching the mapping above) for each bucket — never an `obc-rook-ceph-fr01-...` UUID.

- [ ] **Step 6: Verify live app traffic recovers (real requests, not synthetic)**

```bash
sleep 60
for user in loki mimir tempo; do
  echo "== $user, last 2 min =="
  for p in $(kubectl get pods -n rook-ceph-fr01 -l app=rook-ceph-rgw -o jsonpath='{.items[*].metadata.name}'); do
    kubectl logs -n rook-ceph-fr01 "$p" -c rgw --since=2m 2>&1
  done | grep -E " - $user " | grep -oE '" (200|206|403) ' | sort | uniq -c
done
```

Expected: `200`/`206` entries present, `403` count no longer dominant (ideally zero new `403`s for these three users in the sampled window). If `403`s persist for a specific user after this fix, stop and treat it as a new, separate investigation — do not proceed to Task 4 assuming it'll self-resolve.

---

### Task 4: Roll out to bluemap (1 bucket)

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/bluemap0.yaml`

**Interfaces:**
- Consumes: Task 2's procedure.
- Produces: bluemap fully recovered.

- [ ] **Step 1: Add `additionalConfig.bucketOwner: bluemap`**

```yaml
  additionalConfig:
    bucketOwner: bluemap
```

- [ ] **Step 2: Validate, commit, push**

```bash
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/buckets/bluemap0.yaml
git commit -m "fix(rook-fr01): set bucketOwner=bluemap on bluemap0 OBC"
git push origin HEAD:main
```

- [ ] **Step 3: Reconcile, delete, re-reconcile, wait**

```bash
flux reconcile source git flux-system
kubectl delete objectbucketclaim bluemap0 -n rook-ceph-fr01
flux reconcile kustomization rook-fr01 --with-source
kubectl wait --for=jsonpath='{.status.phase}'=Bound objectbucketclaim/bluemap0 -n rook-ceph-fr01 --timeout=5m
```

- [ ] **Step 4: Verify**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=bluemap0 --rgw-realm=feather-s3 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('owner:', d['owner']); print('num_objects:', d['usage']['rgw.main']['num_objects'])"
sleep 60
for p in $(kubectl get pods -n rook-ceph-fr01 -l app=rook-ceph-rgw -o jsonpath='{.items[*].metadata.name}'); do
  kubectl logs -n rook-ceph-fr01 "$p" -c rgw --since=2m 2>&1
done | grep -E " - bluemap " | grep -oE '" (200|206|403) ' | sort | uniq -c
```

Expected: `owner: bluemap`, no new `403`s for the `bluemap` user.

---

### Task 5: Roll out to reposilite (5 buckets)

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-onelitefeather-proxy.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-onelitefeather-releases.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-onelitefeather-snapshots.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-releases.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-snapshots.yaml`

**Interfaces:**
- Consumes: Task 2's procedure.
- Produces: all 5 reposilite buckets owned by `reposilite`. Note: only `reposilite-onelitefeather-proxy` and `reposilite-onelitefeather-releases` had confirmed live traffic during the parent investigation — the other 3 are lower-confidence mappings by convention only (Reposilite's per-repo storage config lives in its own runtime DB, not this repo). Verify each individually in Step 4; if any of the 3 low-traffic buckets shows a DIFFERENT actual consumer once traffic is observed, stop and re-map that one bucket rather than assuming the batch mapping was right.

- [ ] **Step 1: Add `additionalConfig.bucketOwner: reposilite` to all 5 files**

```yaml
  additionalConfig:
    bucketOwner: reposilite
```

- [ ] **Step 2: Validate, commit, push**

```bash
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-onelitefeather-proxy.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-onelitefeather-releases.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-onelitefeather-snapshots.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-releases.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/reposilite-snapshots.yaml
git commit -m "fix(rook-fr01): set bucketOwner=reposilite on all reposilite OBCs"
git push origin HEAD:main
```

- [ ] **Step 3: Reconcile, delete all 5, re-reconcile, wait**

```bash
flux reconcile source git flux-system
kubectl delete objectbucketclaim reposilite-onelitefeather-proxy reposilite-onelitefeather-releases \
  reposilite-onelitefeather-snapshots reposilite-releases reposilite-snapshots -n rook-ceph-fr01
flux reconcile kustomization rook-fr01 --with-source
for b in reposilite-onelitefeather-proxy reposilite-onelitefeather-releases reposilite-onelitefeather-snapshots reposilite-releases reposilite-snapshots; do
  kubectl wait --for=jsonpath='{.status.phase}'=Bound objectbucketclaim/$b -n rook-ceph-fr01 --timeout=5m
done
```

- [ ] **Step 4: Verify each bucket individually**

```bash
for b in reposilite-onelitefeather-proxy reposilite-onelitefeather-releases reposilite-onelitefeather-snapshots reposilite-releases reposilite-snapshots; do
  echo "== $b =="
  kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=$b --rgw-realm=feather-s3 \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('owner:', d['owner']); print('num_objects:', d['usage']['rgw.main']['num_objects'])"
done
sleep 60
for p in $(kubectl get pods -n rook-ceph-fr01 -l app=rook-ceph-rgw -o jsonpath='{.items[*].metadata.name}'); do
  kubectl logs -n rook-ceph-fr01 "$p" -c rgw --since=2m 2>&1
done | grep -E " - reposilite " | grep -oE '" (200|206|403) ' | sort | uniq -c
```

Expected: `owner: reposilite` on all 5, no new `403`s for the `reposilite` user across any bucket path.

---

### Task 6: Roll out to harbor (1 bucket, highest external impact — container registry)

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/harbor.yaml`

**Interfaces:**
- Consumes: Task 2's procedure.
- Produces: harbor registry storage recovered.

- [ ] **Step 1: Add `additionalConfig.bucketOwner: harbor`**

```yaml
  additionalConfig:
    bucketOwner: harbor
```

- [ ] **Step 2: Validate, commit, push**

```bash
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/buckets/harbor.yaml
git commit -m "fix(rook-fr01): set bucketOwner=harbor on harbor OBC"
git push origin HEAD:main
```

- [ ] **Step 3: Reconcile, delete, re-reconcile, wait**

```bash
flux reconcile source git flux-system
kubectl delete objectbucketclaim harbor -n rook-ceph-fr01
flux reconcile kustomization rook-fr01 --with-source
kubectl wait --for=jsonpath='{.status.phase}'=Bound objectbucketclaim/harbor -n rook-ceph-fr01 --timeout=5m
```

- [ ] **Step 4: Verify ownership/data, then verify a real image pull/push path works**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=harbor --rgw-realm=feather-s3 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('owner:', d['owner']); print('num_objects:', d['usage']['rgw.main']['num_objects'])"
kubectl get pods -n harbor
kubectl logs -n harbor -l component=registry --since=2m 2>&1 | grep -iE "error|denied" || echo "no registry errors in last 2m"
```

Expected: `owner: harbor`, `num_objects` unchanged, no new registry-side S3 errors. If you have `docker`/`crane` access to the registry, pulling one known image is the strongest real-world confirmation — do that if convenient, but the log check is the minimum bar.

---

### Task 7: Roll out to outline (1 bucket)

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/outline.yaml`

**Interfaces:**
- Consumes: Task 2's procedure.
- Produces: outline document-upload storage recovered.

- [ ] **Step 1: Add `additionalConfig.bucketOwner: outline`**

```yaml
  additionalConfig:
    bucketOwner: outline
```

- [ ] **Step 2: Validate, commit, push**

```bash
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/buckets/outline.yaml
git commit -m "fix(rook-fr01): set bucketOwner=outline on outline OBC"
git push origin HEAD:main
```

- [ ] **Step 3: Reconcile, delete, re-reconcile, wait**

```bash
flux reconcile source git flux-system
kubectl delete objectbucketclaim outline -n rook-ceph-fr01
flux reconcile kustomization rook-fr01 --with-source
kubectl wait --for=jsonpath='{.status.phase}'=Bound objectbucketclaim/outline -n rook-ceph-fr01 --timeout=5m
```

- [ ] **Step 4: Verify**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=outline --rgw-realm=feather-s3 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('owner:', d['owner']); print('num_objects:', d['usage']['rgw.main']['num_objects'])"
kubectl logs -n outline -l app.kubernetes.io/name=outline --since=2m 2>&1 | grep -iE "S3|AccessDenied|error" | tail -10
```

Expected: `owner: outline`, `num_objects` unchanged, no new S3 errors in Outline's logs.

---

### Task 8: Roll out to plane (1 bucket)

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/plane.yaml`

**Interfaces:**
- Consumes: Task 2's procedure.
- Produces: plane document-store bucket recovered. Note: `plane` was created AFTER the operator upgrade already broke this pattern, so it may never have worked correctly at all (not a regression from a previously-good state) — same fix applies regardless.

- [ ] **Step 1: Add `additionalConfig.bucketOwner: plane`**

```yaml
  additionalConfig:
    bucketOwner: plane
```

- [ ] **Step 2: Validate, commit, push**

```bash
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook-fr01/buckets/plane.yaml
git commit -m "fix(rook-fr01): set bucketOwner=plane on plane OBC"
git push origin HEAD:main
```

- [ ] **Step 3: Reconcile, delete, re-reconcile, wait**

```bash
flux reconcile source git flux-system
kubectl delete objectbucketclaim plane -n rook-ceph-fr01
flux reconcile kustomization rook-fr01 --with-source
kubectl wait --for=jsonpath='{.status.phase}'=Bound objectbucketclaim/plane -n rook-ceph-fr01 --timeout=5m
```

- [ ] **Step 4: Verify**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=plane --rgw-realm=feather-s3 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('owner:', d['owner']); print('num_objects:', d['usage']['rgw.main']['num_objects'])"
kubectl logs -n plane -l app.kubernetes.io/component=api --since=2m 2>&1 | grep -iE "S3|AccessDenied|error" | tail -10
```

Expected: `owner: plane`, no new S3 errors.

---

### Task 9: Update the incident doc to reflect the permanent fix

**Files:**
- Modify: `docs/incidents/2026-07-18-mariadb-upgrade-and-rgw-access-denied.md`

**Interfaces:**
- Consumes: the completed, verified rollout from Tasks 1-8.
- Produces: an accurate, closed-out incident record — the "Recommended next steps" section currently describes this as unresolved; it needs to reflect what was actually done.

- [ ] **Step 1: Update the doc's status line and Part 2 "Recommended next steps" section**

Change the top status line from:

```markdown
- Rook Ceph RGW (S3) AccessDenied incident — **ROOT-CAUSED, NOT YET FIXED.** Blocks MariaDB backups and several unrelated services. Awaiting a decision on remediation.
```

to:

```markdown
- Rook Ceph RGW (S3) AccessDenied incident — **FIXED.** All 16 affected buckets now declare `additionalConfig.bucketOwner` on their `ObjectBucketClaim` (see `docs/superpowers/plans/2026-07-18-rgw-bucket-owner-fix.md`), making the named app user the true bucket owner instead of relying on admin-cap access that the Rook 1.18→1.20 upgrade broke. No manual `radosgw-admin` step is needed for these buckets ever again — any future re-creation re-applies the same ownership automatically via Rook's own `Provision()` path.
```

Add a short "Resolution" subsection at the end of Part 2 summarizing: the fix mechanism, that it's fully declarative/git-tracked now, and a link to the new plan doc.

- [ ] **Step 2: Commit and push**

```bash
git add docs/incidents/2026-07-18-mariadb-upgrade-and-rgw-access-denied.md
git commit -m "docs(rook): close out RGW incident with the bucketOwner fix"
git push origin HEAD:main
```

---

## Self-Review

**Spec coverage:** allow-listing `bucketOwner` (Task 1); canary with full data-integrity + real-backup verification (Task 2) as the required gate; all 15 remaining buckets grouped by app family (Tasks 3-8); incident doc closed out (Task 9). Postgres explicitly excluded per the Global Constraints. Every task uses the same verified, data-safe mechanism (`Retain` reclaim policy confirmed live) — no task relies on the manual `radosgw-admin bucket link` command the user explicitly wants to avoid going forward.

**Placeholder scan:** every step has the literal YAML/bash to run; no "add appropriate config" language; the reposilite low-confidence mapping is flagged explicitly rather than asserted as certain, with a concrete stop-and-remap instruction if verification disagrees.

**Type/name consistency:** bucket names, OBC file paths, and `bucketOwner` values match the Global Constraints mapping table exactly across every task; the verification command shape (owner + num_objects via the same `radosgw-admin bucket stats ... | python3 -c ...` one-liner) is identical from Task 2 through Task 8, matching the "proven procedure" Task 2 promises to produce.
