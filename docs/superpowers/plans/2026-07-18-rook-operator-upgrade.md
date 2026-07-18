# Rook Operator Upgrade 1.18 → 1.20 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the `rook-ceph` Helm chart in `infrastructure/clusters/feather-core/rook/release.yaml` from `>=1.18.0 <1.19.0` to `1.20.2`, in two separately-merged PRs, including the CSI-driver migration that Rook 1.20 requires.

**Architecture:** PR 1 is a pure version-constraint bump (1.18→1.19) with zero value changes. PR 2 bumps 1.19→1.20, removes the now-obsolete `csi:` block from the `rook-ceph` HelmRelease, and introduces a new `HelmRepository` + `HelmRelease` pair for the `ceph-csi-drivers` chart, wired with `dependsOn` so it never reconciles before the upgraded operator is ready.

**Tech Stack:** FluxCD (`HelmRelease`/`HelmRepository` CRs), Kustomize, Helm charts `rook-ceph` and `ceph-csi-drivers`, Rook/Ceph on the `feather-core` cluster.

## Global Constraints

- Conventional Commits enforced by CI (`commitlint.config.mjs`): types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`; subject starts lowercase; header ≤100 chars.
- `./scripts/validate.sh` must pass locally before every commit (it's what `flux-validate` CI runs).
- Never hammer `flux reconcile` in a loop — one reconcile per stage, then verify health before moving on.
- Rook does not support skipping minor versions — the cluster must actually reconcile at 1.19 before PR 2 is merged, not just have the commit exist in history.
- Ceph itself (the `CephCluster` image, `CephCluster` CR, `rook-fr01` layer) is out of scope — do not touch it.
- Both PRs are merged separately into `main`, with a cluster-health verification gate between them.

---

### Task 1: Bump `rook-ceph` chart to 1.19.x (PR 1)

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook/release.yaml:11`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `rook-ceph` HelmRelease resolving to chart `1.19.x` for Task 2's health-check gate to verify against.

- [ ] **Step 1: Create the PR 1 branch from `main`**

```bash
git checkout main
git pull origin main
git checkout -b fix/rook-operator-1-19
```

- [ ] **Step 2: Bump the version constraint**

In `infrastructure/clusters/feather-core/rook/release.yaml`, change line 11 from:

```yaml
      version: ">=1.18.0 <1.19.0"
```

to:

```yaml
      version: ">=1.19.5 <1.20.0"
```

(The `1.19.5` floor matches Rook's own documented prerequisite for the later 1.20 jump. No other line in this file changes for this task.)

- [ ] **Step 3: Render and inspect the change**

Run: `kubectl kustomize infrastructure/clusters/feather-core/rook | grep -A2 "chart: rook-ceph"`

Expected output includes:

```yaml
      chart: rook-ceph
      version: '>=1.19.5 <1.20.0'
```

- [ ] **Step 4: Run full validation**

Run: `./scripts/validate.sh`

Expected: script exits `0`; the `rook` group reports `Invalid: 0, Errors: 0`.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/clusters/feather-core/rook/release.yaml
git commit -m "fix(rook): bump operator chart to 1.19.x"
```

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin fix/rook-operator-1-19
gh pr create --title "fix(rook): bump operator chart to 1.19.x" --body "$(cat <<'EOF'
## Summary
- Stage 1 of the Rook 1.18 -> 1.20 upgrade (docs/superpowers/specs/2026-07-18-rook-operator-upgrade-design.md)
- Bumps the rook-ceph HelmRelease chart constraint to >=1.19.5 <1.20.0, no CSI value changes

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] Merge, reconcile once, confirm `rook` Kustomization Ready and `ceph status` HEALTH_OK before opening the 1.20 PR
EOF
)"
```

This step requires human/operator judgment on when to actually merge — do not merge automatically as part of this task.

---

### Task 2: Merge PR 1 and verify cluster health (gate before PR 2)

**Files:** none (operational verification only)

**Interfaces:**
- Consumes: merged PR 1 from Task 1
- Produces: a confirmed-healthy cluster at Rook 1.19.x, which Task 3 through 6 assume as their starting state

- [ ] **Step 1: Merge PR 1 into `main`** (via `gh pr merge --merge fix/rook-operator-1-19` or the GitHub UI — confirm with the repo owner before merging)

- [ ] **Step 2: Reconcile once**

```bash
flux reconcile kustomization rook --with-source
```

Do not repeat this command in a loop — it only needs to run once to kick reconciliation off the polling interval.

- [ ] **Step 3: Confirm the Kustomization is Ready**

Run: `flux get kustomizations -A`

Expected: the `rook` row shows `READY=True` at the new revision (no `Reconciling` or error status).

- [ ] **Step 4: Confirm the operator image actually rolled**

```bash
kubectl -n rook-ceph get deploy rook-ceph-operator -o jsonpath='{.spec.template.spec.containers[0].image}'
```

Expected: image tag corresponds to Rook `1.19.x` (check the running pod, not just the Deployment spec, has restarted onto it: `kubectl -n rook-ceph get pods -l app=rook-ceph-operator`).

- [ ] **Step 5: Confirm Ceph cluster health**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph status
```

Expected: `health: HEALTH_OK` (or `HEALTH_WARN` only for pre-existing, unrelated warnings — no new errors attributable to the upgrade).

**Gate:** do not start Task 3 until all four checks above pass. If any fails, stop and investigate before proceeding — do not roll forward into the 1.20 bump on a degraded 1.19 state. Rollback if needed: revert the PR 1 merge commit on `main` — the version constraint is declarative, so Flux/Helm rolls the release back to 1.18.x on the next reconcile; no data-plane state is involved.

---

### Task 3: Research the `ceph-csi-drivers` chart and resolve the open schema question

**Files:**
- Create: `/tmp/claude-1000/-mnt-projects-oss-onelitefeather-Kubernetes-FLUX/6c578aeb-9556-4bf9-bae0-646f3f6bd6b8/scratchpad/ceph-csi-drivers-research.md` (scratch findings file, not committed to the repo — Task 5 reads it)

**Interfaces:**
- Consumes: nothing new (independent research task, can run in parallel with Task 1/2 if desired, but Task 5 needs its output)
- Produces: the scratch findings file above, containing: (a) the latest stable `ceph-csi-drivers` chart version, (b) confirmation of whether `resources` fields are per-pod-side or per-container, (c) confirmation of whether `cephConnections`/`clientProfiles` need manual mon endpoints or are auto-wired by Rook against `rook-ceph-fr01`, (d) whether `enableCSIHostNetwork`'s equivalent belongs under `controllerPlugin.hostNetwork`, `nodePlugin.hostNetwork`, or both, (e) whether an equivalent to `enableGrpcMetrics` exists at all in this chart.

- [ ] **Step 1: Add the Helm repo and inspect available versions**

```bash
helm repo add ceph-csi-operator https://ceph.github.io/ceph-csi-operator
helm repo update ceph-csi-operator
helm search repo ceph-csi-operator/ceph-csi-drivers --versions | head -20
```

Record the newest non-prerelease version in the findings file under a `## Chart version` heading.

- [ ] **Step 2: Pull and inspect the chart's actual templates and schema**

```bash
helm pull ceph-csi-operator/ceph-csi-drivers --untar \
  --destination /tmp/claude-1000/-mnt-projects-oss-onelitefeather-Kubernetes-FLUX/6c578aeb-9556-4bf9-bae0-646f3f6bd6b8/scratchpad/ceph-csi-drivers-chart
helm show values ceph-csi-operator/ceph-csi-drivers \
  > /tmp/claude-1000/-mnt-projects-oss-onelitefeather-Kubernetes-FLUX/6c578aeb-9556-4bf9-bae0-646f3f6bd6b8/scratchpad/ceph-csi-drivers-default-values.yaml
grep -rn "resources\|hostNetwork\|tolerations\|cephConnections\|clientProfiles\|grpcMetrics\|metrics" \
  /tmp/claude-1000/-mnt-projects-oss-onelitefeather-Kubernetes-FLUX/6c578aeb-9556-4bf9-bae0-646f3f6bd6b8/scratchpad/ceph-csi-drivers-chart/ceph-csi-drivers/templates/
```

- [ ] **Step 3: Confirm whether `cephConnections`/`clientProfiles` are auto-managed by Rook**

Check the Rook 1.20 release notes and the `ceph-csi-operator` CRDs shipped by the `rook-ceph` chart itself (already installed from Task 1/2):

```bash
kubectl get crd | grep -i csi
kubectl -n rook-ceph get cephconnections.csi.ceph.io,clientprofiles.csi.ceph.io 2>&1
```

If these CRs already exist and are populated (created automatically by the Rook operator against `rook-ceph-fr01`), record that `cephConnections`/`clientProfiles` should be **omitted** from the new HelmRelease's values (Rook manages them). If they don't exist and nothing else creates them, record the exact mon endpoints needed (from `kubectl -n rook-ceph-fr01 get cephcluster rook-ceph-fr01 -o yaml` and the mon `Service` objects) in the findings file.

- [ ] **Step 4: Write the decision record**

In the findings file, write a final `## Resolved values` section containing the exact YAML block Task 5 should use for the `drivers:` key (and `cephConnections`/`clientProfiles` if Step 3 determined they're required), based on what Steps 1–3 actually showed — not the draft in Task 5, which is only a starting point to be corrected here.

---

### Task 4: Add the `ceph-csi-operator` HelmRepository source (PR 2, commit 1)

**Files:**
- Create: `infrastructure/clusters/feather-core/base-sources/ceph-csi-operator.yaml`
- Modify: `infrastructure/clusters/feather-core/base-sources/kustomization.yaml`

**Interfaces:**
- Consumes: chart version identified in Task 3's findings file (informational only — the `HelmRepository` itself is version-agnostic)
- Produces: `HelmRepository` named `ceph-csi-operator` in namespace `flux-system`, which Task 5's `HelmRelease` references via `sourceRef`

- [ ] **Step 1: Create the PR 2 branch from `main`** (must be done after Task 2's merge, so it starts from the 1.19 state)

```bash
git checkout main
git pull origin main
git checkout -b fix/rook-operator-1-20-csi-migration
```

- [ ] **Step 2: Create the new HelmRepository file**

Create `infrastructure/clusters/feather-core/base-sources/ceph-csi-operator.yaml`:

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: ceph-csi-operator
  namespace: flux-system
spec:
  interval: 5m
  url: https://ceph.github.io/ceph-csi-operator
```

- [ ] **Step 3: Register it in the base-sources kustomization**

In `infrastructure/clusters/feather-core/base-sources/kustomization.yaml`, add `ceph-csi-operator.yaml` right after `rook.yaml`:

```yaml
  - rook.yaml
  - ceph-csi-operator.yaml
  - n8n.yml
```

(only the two new/reordered lines change; everything else in the `resources:` list stays as-is)

- [ ] **Step 4: Render and verify**

Run: `kubectl kustomize infrastructure/clusters/feather-core/base-sources | grep -A5 "name: ceph-csi-operator"`

Expected output:

```yaml
kind: HelmRepository
metadata:
  name: ceph-csi-operator
  namespace: flux-system
spec:
  interval: 5m0s
  url: https://ceph.github.io/ceph-csi-operator
```

- [ ] **Step 5: Run full validation**

Run: `./scripts/validate.sh`

Expected: exits `0`, no new `Invalid`/`Errors` in the `base-sources` group.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/clusters/feather-core/base-sources/ceph-csi-operator.yaml \
        infrastructure/clusters/feather-core/base-sources/kustomization.yaml
git commit -m "feat(rook): add ceph-csi-operator helm repository source"
```

---

### Task 5: Add the `ceph-csi-drivers` HelmRelease, migrate CSI values, bump to 1.20 (PR 2, commit 2)

**Files:**
- Create: `infrastructure/clusters/feather-core/rook/csi-drivers-release.yaml`
- Modify: `infrastructure/clusters/feather-core/rook/kustomization.yaml`
- Modify: `infrastructure/clusters/feather-core/rook/release.yaml` (bump version, remove the `csi:` block)

**Interfaces:**
- Consumes: `HelmRepository` `ceph-csi-operator` from Task 4; the confirmed values below (from Task 3 + Task 3b research, both independently reviewed against the live cluster — see `.superpowers/sdd/task-3-report.md` and `.superpowers/sdd/task-3b-report.md` for full citations)
- Produces: `HelmRelease` `ceph-csi-drivers` in namespace `rook-ceph`, `dependsOn` the `rook-ceph` `HelmRelease`, for Task 6's health-check gate to verify

**Confirmed by research (do not deviate without re-checking the two reports above):**
- Chart version `1.0.4` — pinned to exactly what `rook-ceph`@1.20.2 itself vendors as its `ceph-csi-operator` subchart dependency, keeping Driver CRDs and the controller in lockstep.
- Target namespace is `rook-ceph`, not `rook-ceph-fr01` — that's where the operator, the existing (Rook-managed) `CephConnection`/`ClientProfile`/`OperatorConfig` objects, and the provisioner secrets referenced by them already live.
- `cephConnections:`/`clientProfiles:` are deliberately **omitted** — confirmed live-populated and owned by Rook's `CephCluster` controller against `rook-ceph-fr01`, independent of whichever chart renders `Driver` CRs. Declaring them here would create redundant, conflicting objects.
- `drivers.rbd.name`/`drivers.cephfs.name` **must** be set explicitly to the `rook-ceph.`-prefixed provisioner names already in use by every live StorageClass/CSIDriver on this cluster — the chart's own defaults (`rbd.csi.ceph.com`/`cephfs.csi.ceph.com`, no prefix) would mint differently-named objects and break every existing PVC/PV.
- `resources` are per-container-role maps (`controllerPlugin.resources.{provisioner,resizer,attacher,snapshotter,plugin}`, `nodePlugin.resources.{registrar,plugin}`), not a single pod-level block — confirmed against the chart's actual `Driver` CRD Go types and against the live, already-rendered `Driver` CR's numbers (which match the old per-container `csiRBDProvisionerResource`/`csiRBDPluginResource` figures 1:1).
- `nodePlugin` tolerations **must** be set explicitly — the live `Driver` CR today has none at all (Rook's internal 1.19.7 in-process rendering silently drops `rbdPluginTolerations`/`cephFSPluginTolerations`), so there is no working default to inherit.
- `nodePlugin` host networking is hardcoded `true` unconditionally in this chart/operator version (no field exists to set it); `controllerPlugin.hostNetwork` should stay unset/`false` — this matches both `enableCSIHostNetwork`'s original documented scope (nodeplugins only) and the cluster's actual current state.
- No equivalent of `enableGrpcMetrics` exists in this chart, and the key was already dead/no-op in Rook's own chart at every version checked back to 1.16 — drop it, nothing to migrate.
- `nfs.enabled`/`nvmeof.enabled` must be explicitly `false` — the chart defaults both to `true`, but neither driver is in use on this cluster (no CRs, no pods for either).
- **No additional value (e.g. `rookUseCsiOperator: false`) is needed on the `rook-ceph` HelmRelease itself** — Task 3b confirmed the Go code path that did Rook's in-process CR rendering was deleted outright between v1.19.7 and v1.20.2, not flag-gated, so there is no ownership conflict to defend against once `rook-ceph` is actually on 1.20.x. The one real residual risk is a short availability gap (Rook's own upgrade docs: *"This `ceph-csi-drivers` chart must be installed, otherwise the CSI driver will be in a failed state due to missing service accounts"*) if this chart doesn't reconcile in the same change/window as the `rook-ceph` 1.20.x bump — which is exactly what the `dependsOn` below, and keeping both changes in one PR, is for.

- [ ] **Step 1: Create the new HelmRelease**

Create `infrastructure/clusters/feather-core/rook/csi-drivers-release.yaml`:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: ceph-csi-drivers
  namespace: rook-ceph
spec:
  releaseName: ceph-csi-drivers
  chart:
    spec:
      chart: ceph-csi-drivers
      version: ">=1.0.4 <2.0.0"
      sourceRef:
        kind: HelmRepository
        name: ceph-csi-operator
        namespace: flux-system
  dependsOn:
    - name: rook-ceph
      namespace: rook-ceph
  install:
    remediation:
      retries: 3
  upgrade:
    remediation:
      retries: 3
  interval: 1m0s
  timeout: 10m0s
  values:
    drivers:
      rbd:
        enabled: true
        name: rook-ceph.rbd.csi.ceph.com
        nodePlugin:
          tolerations:
            - key: node-role.feather/storage
              operator: Equal
              value: "true"
              effect: NoSchedule
          resources:
            registrar:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            plugin:
              requests:
                cpu: 50m
                memory: 512Mi
              limits:
                cpu: 500m
                memory: 1Gi
        controllerPlugin:
          replicas: 2
          resources:
            provisioner:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            resizer:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            attacher:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            snapshotter:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            plugin:
              requests:
                cpu: 50m
                memory: 512Mi
              limits:
                cpu: 500m
                memory: 1Gi
      cephfs:
        enabled: true
        name: rook-ceph.cephfs.csi.ceph.com
        nodePlugin:
          tolerations:
            - key: node-role.feather/storage
              operator: Equal
              value: "true"
              effect: NoSchedule
          resources:
            registrar:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            plugin:
              requests:
                cpu: 50m
                memory: 512Mi
              limits:
                cpu: 500m
                memory: 1Gi
        controllerPlugin:
          replicas: 2
          resources:
            provisioner:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            resizer:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            attacher:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            snapshotter:
              requests:
                cpu: 25m
                memory: 128Mi
              limits:
                cpu: 100m
                memory: 256Mi
            plugin:
              requests:
                cpu: 50m
                memory: 512Mi
              limits:
                cpu: 500m
                memory: 1Gi
      nfs:
        enabled: false
      nvmeof:
        enabled: false
```

(Every field above is transcribed verbatim from the confirmed values in `.superpowers/sdd/task-3-report.md`'s `## Resolved values` block and cross-checked by an independent reviewer against the live cluster — this is not a draft, apply it as-is.)

- [ ] **Step 2: Register the new HelmRelease**

In `infrastructure/clusters/feather-core/rook/kustomization.yaml`, add it after `release.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - secrets.sops.yaml
  - release.yaml
  - csi-drivers-release.yaml
  - storageclass.yaml
```

- [ ] **Step 3: Bump the `rook-ceph` chart version to 1.20**

In `infrastructure/clusters/feather-core/rook/release.yaml`, change line 11 from:

```yaml
      version: ">=1.19.5 <1.20.0"
```

to:

```yaml
      version: ">=1.20.0 <1.21.0"
```

- [ ] **Step 4: Remove the obsolete `csi:` block**

In the same file, delete the entire `csi:` key and everything nested under it — every line from `csi:` through the end of `csiCephFSPluginResource`'s content (the full block currently spanning lines 52–214, immediately after the `affinity:` block closes). The file should end right after the `affinity.nodeAffinity.preferredDuringSchedulingIgnoredDuringExecution` list, i.e. after:

```yaml
    affinity:
      nodeAffinity:
        preferredDuringSchedulingIgnoredDuringExecution:
          - weight: 100
            preference:
              matchExpressions:
                - key: topology.kubernetes.io/region
                  operator: In
                  values:
                    - fi-helsinki
```

with nothing after it.

- [ ] **Step 5: Render and verify both releases**

```bash
kubectl kustomize infrastructure/clusters/feather-core/rook | grep -A2 "chart: rook-ceph"
kubectl kustomize infrastructure/clusters/feather-core/rook | grep -A2 "chart: ceph-csi-drivers"
kubectl kustomize infrastructure/clusters/feather-core/rook | grep -c "csi:"
```

Expected: the `rook-ceph` chart shows version `'>=1.20.0 <1.21.0'`; the `ceph-csi-drivers` chart is present with `sourceRef.name: ceph-csi-operator`; the last command returns `0` (no leftover `csi:` key anywhere in the rendered output).

- [ ] **Step 6: Run full validation**

Run: `./scripts/validate.sh`

Expected: exits `0`, `rook` group reports `Invalid: 0, Errors: 0`.

- [ ] **Step 7: Commit**

```bash
git add infrastructure/clusters/feather-core/rook/csi-drivers-release.yaml \
        infrastructure/clusters/feather-core/rook/kustomization.yaml \
        infrastructure/clusters/feather-core/rook/release.yaml
git commit -m "feat(rook): migrate csi drivers to ceph-csi-operator and bump to 1.20.x"
```

---

### Task 6: Open, merge PR 2, and final verification

**Files:** none (operational)

**Interfaces:**
- Consumes: commits from Task 4 and Task 5 on branch `fix/rook-operator-1-20-csi-migration`
- Produces: cluster fully upgraded to Rook 1.20.x with CSI managed by `ceph-csi-drivers`

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin fix/rook-operator-1-20-csi-migration
gh pr create --title "feat(rook): bump operator to 1.20.x and migrate csi drivers" --body "$(cat <<'EOF'
## Summary
- Stage 2 of the Rook 1.18 -> 1.20 upgrade (docs/superpowers/specs/2026-07-18-rook-operator-upgrade-design.md)
- Bumps rook-ceph to >=1.20.0 <1.21.0
- Adds the ceph-csi-operator HelmRepository + ceph-csi-drivers HelmRelease, migrating the old csi.* values

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] Merge, reconcile once, confirm rook-ceph AND ceph-csi-drivers HelmReleases Ready, CSI plugin pods running on fr01-str-01..03, ceph status HEALTH_OK, sample PVC mount still works
EOF
)"
```

This step requires human/operator judgment on when to actually merge — do not merge automatically as part of this task.

- [ ] **Step 2: Merge and reconcile once**

```bash
flux reconcile kustomization rook --with-source
```

- [ ] **Step 3: Confirm both HelmReleases are Ready**

```bash
flux get helmreleases -n rook-ceph
```

Expected: both `rook-ceph` and `ceph-csi-drivers` show `READY=True` at their respective new chart versions.

- [ ] **Step 4: Confirm CSI plugin pods are running on all storage nodes**

```bash
kubectl -n rook-ceph get pods -l app=csi-rbdplugin -o wide
kubectl -n rook-ceph get pods -l app=csi-cephfsplugin -o wide
```

Expected: one Running pod of each per node on `fr01-str-01`, `fr01-str-02`, `fr01-str-03` (confirming the migrated tolerations actually worked).

- [ ] **Step 5: Confirm Ceph cluster health**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph status
```

Expected: `health: HEALTH_OK`.

- [ ] **Step 6: Confirm an existing PVC still mounts correctly**

Pick any workload already using a Rook-backed PVC (e.g. `kubectl get pvc -A | grep rook-ceph`), restart one of its pods, and confirm it comes back `Running` with the volume mounted — this is the concrete signal that the CSI migration didn't silently break provisioning/attachment.

**Gate:** if any of Steps 3–6 fail, do not consider the upgrade complete — investigate before closing out this plan. Rollback if needed: revert the PR 2 merge commit on `main` — this restores the 1.19.x `rook-ceph` constraint and removes the `ceph-csi-drivers` HelmRelease/HelmRepository on the next reconcile; no data-plane state is involved.
