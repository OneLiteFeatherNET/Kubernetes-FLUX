# Workload Resilience & Pod Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the HA choke points, PodDisruptionBudget traps, missing probes/requests, missing priority classes and missing container hardening found by the 2026-08-03 best-practice audit, in ten separately-mergeable PRs plus three operational (non-PR) tasks, ordered so the reversible/verifiable work lands first and the rollout-heavy work lands last.

**Architecture:** Every change is either (a) a Helm *values* edit in a cluster overlay, (b) a Kustomize post-render patch in the same overlay, (c) an in-repo chart template/values edit under `helm/` (always with a `Chart.yaml` version bump), or (d) a plain manifest added to an overlay. Nothing here changes application images, storage, or network topology.

**Tech Stack:** FluxCD (`HelmRelease`, `Kustomization`), Kustomize (strategic-merge + JSON6902 post-renderers), in-repo Helm charts under `helm/`, Rook/Ceph CRs, Pod Security Admission labels.

---

## Global Constraints

- A change takes effect **only** when committed and pushed to `main`; Flux then applies it. Nothing in this plan is done by `kubectl apply`.
- Conventional Commits enforced by CI (`commitlint.config.mjs`): types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`; subject starts **lowercase**; header ≤100 chars. The PR title is linted too.
- `./scripts/validate.sh` must pass locally before every commit.
- Two-tier Kustomize: `infrastructure/base` + `infrastructure/clusters/feather-core/<layer>/`, `apps/base` + `apps/clusters/feathre-core/<layer>/`. **The "feathre" misspelling under `apps/` is real and intentional** — do not "fix" it.
- Editing anything under `helm/` **requires** bumping that chart's `Chart.yaml` `version:` in the same commit, or Flux serves the cached chart and the change silently does nothing.
- `generatorOptions.disableNameSuffixHash: true` — no Secret contents change in this plan, so no rollout-restart-for-secrets is needed anywhere. If you deviate and touch a Secret, add the explicit `kubectl rollout restart`.
- Never hammer `flux reconcile` in a loop. One reconcile per stage, then verify.
- Renovate moves `main` under you — `git pull --rebase origin main` before every push.
- `./scripts/validate.sh` does **not** render Helm charts. Every `helm/` edit must additionally be checked with `helm template`/`helm lint` as specified per task.
- `./scripts/validate.sh` also does **not** build `clusters/feather-core/flux-system/` — Task 12 adds an explicit `kubectl kustomize` check for it.

---

## Prerequisites

- `kubectl` with context `admin@feather-core` (read access is enough for verification; Task 1 is the only step that mutates the cluster directly, and it is a delete of two orphaned objects).
- `helm` ≥ 3.14 on PATH (used for `helm template`, `helm lint`, and value-schema checks).
- `flux` CLI on PATH.
- Repo checked out at a revision at or after `ac16018`. Every line number in this plan was verified against `ac16018`; re-grep before editing if `main` has moved.

## Cross-Theme Dependencies

| Dependency | Direction | Why |
|---|---|---|
| **Theme 9 (Talos rolling node drain)** | This plan's **Tasks 1, 18, 19 must land BEFORE** theme 9 starts | Theme 9 drains all 10 nodes. Every PDB at `disruptionsAllowed: 0` will make the eviction API return 429 forever and hang the drain. Task 1 deletes two of them; Task 18 resolves `n8n-main`; Task 19 documents the rest so the operator running the drain knows which ones need a force. |
| **Theme 9 (Talos rolling node drain)** | **PR 8 (Tasks 18-19) may be pulled forward** ahead of PRs 3-7 | Tasks 18 and 19 are the only ones theme 9 actually blocks on, yet they sit 8th in a strictly serialised chain that includes a Ceph MDS restart window and three chart rollouts. Task 18 touches one HelmRelease value in `apps/.../base-apps/n8n/release.yaml` and Task 19 adds one doc file — neither shares a file, layer or chart with Tasks 5-17, so nothing in PRs 2-7 is a real prerequisite (the "Consumes: PR 7" line in Task 18 is sequencing convention, not a dependency). **If theme 9 is scheduled before this plan finishes, run Task 1, then PR 8, then resume at PR 1.** State the reordering in the PR body. |
| **Theme 9 (Talos rolling node drain)** | Theme 9 **should follow** PR 9 (Task 20), or PR 9 should be deferred until after theme 9 | `DoNotSchedule` on the Mimir ingesters/store-gateways makes node maintenance stricter. Verify a single-worker drain still completes (Task 20 Step 9) before theme 9 relies on it. |
| **Telemetry / observability theme** | Blocks Task 21 (leader-election root cause) | Verified live 2026-08-03: `apiserver_request_duration_seconds_bucket` is **not** present in Mimir (only `_sum`/`_count`), and **no** `etcd_*` metrics are scraped at all. The mean lease-PUT latency is 5.6 ms — the 5 s timeouts are a rare tail that is invisible without buckets. Task 21 is therefore an investigation with a telemetry prerequisite, not a fix. |
| **Talos config repo** (`/mnt/projects/lab/talos-cluster`, remote `TheMeinerLP/FeatherCore`) | Referenced by Task 21 only | Enabling etcd metrics scraping is a Talos machine-config change (`cluster.etcd.extraArgs.listen-metrics-urls`) in that **separate** repo. **No task in this plan writes to that repo.** Task 21 only records the finding and the recommendation. |

## Decision Gates

These need a human answer before the corresponding task can be executed. Each is called out again inline.

1. **Task 16 — Leantime probe path.** `/` returns `303 → https://leantime.onelitefeather.net/install` (kubelet counts 3xx as success, but logs `ProbeWarning ... Probe terminated redirects` — 192 169 events in 22 d). Verified live: `/install` → `200`, PHP-rendered; `/robots.txt` and `/favicon.ico` → `200` but served statically by nginx (does not exercise PHP-FPM). **Recommendation: `/install` for readiness, `/` for liveness** — a liveness probe bound to an install-wizard route SIGKILLs the container in a loop if that route ever stops answering. Alternatives: both on `/install` (zero warnings, accepts that risk), or keep both on `/` and accept the event noise. Full options table in Task 16 Step 4.
2. **Task 18 — n8n-main PDB.** Disable the chart PDB (`pdb.enabled: false`) so drains proceed, or keep it and document the force-drain like `apps/clusters/feathre-core/base-apps/harbor/pdb.yaml:20-23` does. **Recommendation: disable** — n8n-main is a single replica whose state lives in external Postgres + Dragonfly, `n8n-worker` ×2 keeps executing queued jobs, and a 1-replica PDB cannot actually protect anything, it can only hang the drain.
3. **Task 14 — `runAsNonRoot` on `helm/micronaut`.** Verified live: **all four** micronaut workloads (`otis`, `otis-dev`, `vulpes-backend`, `vulpes-backend-dev`) run as `uid=0(root)`. Adding `runAsNonRoot: true` would CrashLoopBackOff all four immediately. **Recommendation: do NOT add it in this plan.** Deferred; it needs a non-root `USER` in the application images (separate repos) or a verified `runAsUser`/`fsGroup` for the JVM's writable paths.
4. **Task 8 — Ceph MDS restart window.** The MDS priority-class change restarts the active MDS; the standby-replay MDS takes over and CephFS clients see a short metadata stall. Pick a quiet window.

## Deliberately Out of Scope

- **Hardening third-party charts** (`plane` ×10 containers, `reposilite`, `bluemap`, `uptime-kuma`, `ollama`). The audit's `inrepo-charts-no-container-hardening` finding also recommends `allowPrivilegeEscalation:false` / `drop:[ALL]` / `seccompProfile` on these via overlay patches. That is a bigger, riskier sweep (each needs its own port/capability check — `reposilite` and `bluemap` in particular) and belongs in its own plan. This plan does the five in-repo charts only.
- **`runAsNonRoot` on `helm/micronaut`** — decision gate 3 above (all four consumers run as uid 0 today).
- **`readOnlyRootFilesystem: true` on outline/shlink/leantime.** All three write outside mounted volumes; `outline` and `leantime` overlays already force it to `false`. The charts here default it to `false` deliberately.
- **Retargeting the zone-keyed `topologySpreadConstraints` on tempo/mimir/loki components to `kubernetes.io/hostname`.** Once Tasks 2, 3 and 20 add real hostname-keyed anti-affinity/`DoNotSchedule` where it matters, the remaining zone TSCs are harmless no-ops. Each one is a `tpl`-rendered string in a different chart; the change is pure cosmetics with non-zero rendering risk. Not worth it.
- **`monitoring` namespace PSA labels.** The audit notes `monitoring` is blanket-`privileged` while only the 10 `node-exporter` pods need it. Leaving it is a reasonable trade at this cluster's size and the audit says so; only `cloudflare-tunnel-ingress-controller` (which holds the tunnel token) is changed here.
- **Fixing the Flux `notification-controller` upstream.** Task 12 patches it locally; it does not file anything upstream.
- **Any write to `/mnt/projects/lab/talos-cluster`.** Nothing in this plan touches the Talos repo.
- **`loki-compactor` PDB.** The audit notes it has none; a singleton compactor ("Exactly one compactor must run at a time", `monitoring/loki/release.yaml:231`) arguably should not have one. Left alone.

---

## Task 1: Delete the two orphaned Outline PDBs (operational, no PR)

> ⚠️ **This step deletes live cluster objects.** It is a `kubectl delete` on two Helm-owned `PodDisruptionBudget`s that protect **zero** pods. It is not a data operation and nothing is at risk, but it is the only direct cluster mutation in this plan.

**Files:** none (operational)

**Interfaces:**
- Consumes: nothing
- Produces: a cluster with no permanently-firing `KubePdbNotEnoughHealthyPods` on `outline-worker`/`outline-websockets`; a precondition for theme 9's drain and for Task 21's documentation being accurate.

**Why now:** both PDBs carry `helm.sh/chart: outline-0.4.0` while `helm/outline/Chart.yaml:18` is at `0.5.1` and the live `outline-web`/`outline-collaboration` objects carry `outline-0.5.1` — helm-controller failed to prune them across the upgrade. They match zero pods (`currentHealthy: 0`, `desiredHealthy: 1`), so `KubePdbNotEnoughHealthyPods` (`kube_poddisruptionbudget_status_desired_healthy - kube_poddisruptionbudget_status_current_healthy > 0`, `for: 15m`) has been firing continuously for 46 days.

- [ ] **Step 1: Confirm the orphans still match zero pods and no component defines them**

```bash
kubectl get pdb -n outline -o custom-columns=N:.metadata.name,CHART:.metadata.labels.'helm\.sh/chart',CUR:.status.currentHealthy,EXP:.status.expectedPods
kubectl get deploy -n outline
grep -n "^  worker:\|^  websockets:\|^  web:\|^  collaboration:" helm/outline/values.yaml
```

Expected: `outline-websockets` and `outline-worker` show `CHART=outline-0.4.0`, `CUR=0`, `EXP=0`; `kubectl get deploy -n outline` lists **only** `outline-collaboration` and `outline-web`; `helm/outline/values.yaml` defines only `web` and `collaboration` components.

**Abort if** either PDB shows `EXP > 0`, or a `worker`/`websockets` component exists in `values.yaml` — that would mean they are live objects, not orphans.

- [ ] **Step 2: Delete them**

```bash
kubectl delete pdb outline-worker outline-websockets -n outline
```

Expected: `poddisruptionbudget.policy "outline-worker" deleted` and `... "outline-websockets" deleted`.

- [ ] **Step 3: Verify**

```bash
kubectl get pdb -n outline
kubectl get pdb -A -o json | jq -r '.items[] | select(.status.disruptionsAllowed==0 and .status.expectedPods>=0) | "\(.metadata.namespace)/\(.metadata.name)"'
```

Expected: `kubectl get pdb -n outline` returns `No resources found in outline namespace.`; the second command lists exactly three entries — `cnpg-system/feather-core-cluster-pg-primary`, `harbor/harbor-registry`, `n8n/n8n-main`.

- [ ] **Step 4: Confirm the alert clears**

Wait ≥15 min (the rule's `for:`), then check the Grafana alert `KubePdbNotEnoughHealthyPods` (rule group `kube-prometheus-stack-kubernetes-apps`) no longer has instances with `poddisruptionbudget="outline-worker"` or `"outline-websockets"`.

- [ ] **Step 5: Confirm the outline HelmRelease is still healthy after the delete**

```bash
flux get helmreleases -n outline
```

Expected: `outline`, `REVISION=0.5.1`, `READY=True`.

**Rollback:** none needed — recreating a PDB that protects zero pods has no value. If Helm ever recreates them on a future `outline` chart revision (it will not; `helm/outline/templates/pdb.yaml:2-3` guards on `$component.enabled`), simply repeat this task.

---

## Task 2: Restore `tempo-gateway`'s podAntiAffinity (PR 1, commit 1)

**Files:**
- Modify: `apps/clusters/feathre-core/monitoring/tempo/release.yaml:292-301`

**Interfaces:**
- Consumes: nothing
- Produces: `tempo-gateway` replicas on two distinct nodes, for Task 4's gate to verify.

**Evidence:** the overlay sets `gateway.affinity` to a nodeAffinity-only string. Because `affinity` is a whole-block override, it dropped the chart's default — verified against `tempo-distributed` 1.61.3, whose `gateway.affinity` default is a **required** `podAntiAffinity` on `kubernetes.io/hostname`. The only remaining spread signal is the chart's `topology.kubernetes.io/zone`-keyed TSC with `whenUnsatisfiable: ScheduleAnyway`, and all ten nodes report zone `fr01`, so it is a guaranteed no-op. Both replicas are on `fr01-wrk-xl-02` right now. This is the sole read **and** write path for all traces, in front of the deliberate 3-replica HA work of commit `ca12af1`.

- [ ] **Step 1: Create the PR 1 branch from `main`**

```bash
git checkout main
git pull --rebase origin main
git checkout -b fix/monitoring-gateway-ha
```

- [ ] **Step 2: Confirm the current state before editing**

```bash
sed -n '282,303p' apps/clusters/feathre-core/monitoring/tempo/release.yaml
kubectl get pods -n grafana -o wide | grep tempo-gateway
```

Expected: the `affinity: |` block at line 292 contains `nodeAffinity` only; both `tempo-gateway-*` pods are on the same node.

- [ ] **Step 3: Append the podAntiAffinity to the gateway affinity string**

In `apps/clusters/feathre-core/monitoring/tempo/release.yaml`, replace lines 292-301:

```yaml
      affinity: |
        nodeAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              preference:
                matchExpressions:
                  - key: topology.kubernetes.io/zone
                    operator: In
                    values:
                      - fr01
```

with:

```yaml
      # `affinity` is a whole-block override, so setting nodeAffinity here also
      # dropped the chart's default required podAntiAffinity. Restored below.
      affinity: |
        nodeAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              preference:
                matchExpressions:
                  - key: topology.kubernetes.io/zone
                    operator: In
                    values:
                      - fr01
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchLabels:
                  app.kubernetes.io/name: tempo
                  app.kubernetes.io/instance: tempo
                  app.kubernetes.io/component: gateway
              topologyKey: kubernetes.io/hostname
```

Nothing else in the file changes. The three labels are exactly the live Deployment selector (`kubectl get deploy tempo-gateway -n grafana -o jsonpath='{.spec.selector.matchLabels}'` → `{"app.kubernetes.io/component":"gateway","app.kubernetes.io/instance":"tempo","app.kubernetes.io/name":"tempo"}`).

`required` is safe here: 2 replicas over 4 schedulable workers (`fr01-wrk-xl-01..04`), and the Deployment default strategy gives `maxSurge: 1`, so the surge pod always has a free node. If gateway replicas are ever raised above 4, revisit.

- [ ] **Step 4: Render and verify**

```bash
kubectl kustomize apps/clusters/feathre-core/monitoring/tempo | sed -n '/^    gateway:/,/^    metaMonitoring:/p'
```

Expected: the rendered `affinity: |` string contains both `nodeAffinity:` and `podAntiAffinity:` with `topologyKey: kubernetes.io/hostname`.
(Do **not** use a short `grep -A N` window here — the gateway block is ~10 lines before `affinity: |` and `topologyKey` lands ~18 lines after it, so anything under `-A 30` silently shows nothing and the check passes for the wrong reason.)

- [ ] **Step 5: Validate**

Run: `./scripts/validate.sh`

Expected: exits `0`; the `apps/clusters/feathre-core/monitoring` group reports `Invalid: 0, Errors: 0`.

- [ ] **Step 6: Commit**

```bash
git add apps/clusters/feathre-core/monitoring/tempo/release.yaml
git commit -m "fix(tempo): restore gateway podantiaffinity dropped by the affinity override"
```

**Rollback:** revert this commit — the affinity is declarative, Flux/Helm reverts the Deployment on the next reconcile and the pods reschedule.

---

## Task 3: Give `loki-gateway` two replicas, anti-affinity and a PDB (PR 1, commit 2)

**Files:**
- Modify: `apps/clusters/feathre-core/monitoring/loki/release.yaml:259-279`
- Create: `apps/clusters/feathre-core/monitoring/loki/pdb.yaml`
- Modify: `apps/clusters/feathre-core/monitoring/loki/kustomization.yaml`

**Interfaces:**
- Consumes: nothing (independent of Task 2, same PR for one health gate)
- Produces: `loki-gateway` at 2 replicas on distinct nodes with a PDB, for Task 4's gate.

**Evidence:** `kubectl get deploy -n grafana loki-gateway -o jsonpath='{.spec.replicas}'` → `1`, while every other Loki component in the same file is explicitly 2-3 (and the compactor is a deliberate 1). `kubectl get pdb -n grafana` has no `loki-gateway`. It is the sole read and write path: `alloy-logs` pushes to `http://loki-gateway.grafana.svc.cluster.local/loki/api/v1/push` and the Grafana Loki datasource is the same host.

**Two corrections to the audit's recommendation, both verified against `loki` chart 7.2.0:**
1. The chart has **no** `gateway.podDisruptionBudget` key (unlike `mimir-distributed`, whose `gateway.podDisruptionBudget.maxUnavailable` is already used at `monitoring/mimir/release.yaml:141-142`). The PDB must be authored as a plain manifest in the overlay — the same pattern as `apps/clusters/feathre-core/base-apps/harbor/pdb.yaml`.
2. The chart's `gateway.affinity` default is a **required** `podAntiAffinity` on `kubernetes.io/hostname` — and the overlay's nodeAffinity-only block at lines 268-277 dropped it, exactly like tempo. Going to 2 replicas without restoring it just recreates the tempo bug.

- [ ] **Step 1: Confirm current state**

```bash
sed -n '259,280p' apps/clusters/feathre-core/monitoring/loki/release.yaml
kubectl get deploy loki-gateway -n grafana -o jsonpath='{.spec.replicas}{"\n"}{.spec.selector.matchLabels}{"\n"}'
kubectl get pdb -n grafana | grep loki-gateway || echo "no loki-gateway pdb (expected)"
```

Expected: no `replicas:` key in the `gateway:` block; `1`; selector `{"app.kubernetes.io/component":"gateway","app.kubernetes.io/instance":"loki","app.kubernetes.io/name":"loki"}`; `no loki-gateway pdb (expected)`.

- [ ] **Step 2: Edit the gateway block**

In `apps/clusters/feathre-core/monitoring/loki/release.yaml`, replace lines 259-279:

```yaml
    gateway:
      podLabels:
        logs.onelitefeather.net/env: prod
      resources:
        requests:
          cpu: 10m
          memory: 32Mi
        limits:
          memory: 128Mi
      affinity:
        nodeAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              preference:
                matchExpressions:
                  - key: topology.kubernetes.io/zone
                    operator: In
                    values:
                      - fr01
      ingress:
        enabled: false
```

with:

```yaml
    gateway:
      # Chart default is 1. This nginx proxy is the sole read AND write path for
      # all logs (alloy-logs pushes here, the Grafana datasource reads here), so
      # a node drain took log ingest and query down for the reschedule window.
      replicas: 2
      podLabels:
        logs.onelitefeather.net/env: prod
      resources:
        requests:
          cpu: 10m
          memory: 32Mi
        limits:
          memory: 128Mi
      # `affinity` is a whole-block override, so setting nodeAffinity here also
      # dropped the chart's default required podAntiAffinity. Restored below.
      affinity:
        nodeAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              preference:
                matchExpressions:
                  - key: topology.kubernetes.io/zone
                    operator: In
                    values:
                      - fr01
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchLabels:
                  app.kubernetes.io/name: loki
                  app.kubernetes.io/instance: loki
                  app.kubernetes.io/component: gateway
              topologyKey: kubernetes.io/hostname
      ingress:
        enabled: false
```

- [ ] **Step 3: Create the PDB manifest**

Create `apps/clusters/feathre-core/monitoring/loki/pdb.yaml`:

```yaml
# The loki chart (7.2.0) exposes no gateway.podDisruptionBudget key, unlike
# mimir-distributed (see monitoring/mimir/release.yaml:141). Authored here
# instead, same pattern as apps/.../base-apps/harbor/pdb.yaml.
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: loki-gateway
  namespace: grafana
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: loki
      app.kubernetes.io/instance: loki
      app.kubernetes.io/component: gateway
```

- [ ] **Step 4: Register it**

In `apps/clusters/feathre-core/monitoring/loki/kustomization.yaml`, add `pdb.yaml` to `resources:` after `httproute.yaml`:

```yaml
resources:
  - ../../../../../apps/base/loki
  - httproute.yaml
  - pdb.yaml
```

- [ ] **Step 5: Render and verify**

```bash
kubectl kustomize apps/clusters/feathre-core/monitoring/loki | grep -c "kind: PodDisruptionBudget"
kubectl kustomize apps/clusters/feathre-core/monitoring/loki | grep -A 3 "name: loki-gateway"
kubectl kustomize apps/clusters/feathre-core/monitoring/loki | sed -n '/^    gateway:/,/^    lokiCanary:/p' | grep -E "replicas:|podAntiAffinity:|topologyKey:"
```
(Range-addressed `sed`, not `grep -A N`: with the added comments the new gateway block is ~33 lines long, so a `-A 30` window stops just short of `topologyKey` and the check would pass while showing nothing.)

Expected: `1`; the PDB in namespace `grafana` with `maxUnavailable: 1`; `replicas: 2`, `podAntiAffinity:` and `topologyKey: kubernetes.io/hostname` all present.

- [ ] **Step 6: Validate**

Run: `./scripts/validate.sh`

Expected: exits `0`, `Invalid: 0, Errors: 0` for the monitoring group.

- [ ] **Step 7: Commit**

```bash
git add apps/clusters/feathre-core/monitoring/loki/release.yaml \
        apps/clusters/feathre-core/monitoring/loki/pdb.yaml \
        apps/clusters/feathre-core/monitoring/loki/kustomization.yaml
git commit -m "feat(loki): run gateway with 2 replicas, host anti-affinity and a pdb"
```

**Rollback:** revert this commit. The gateway drops back to 1 replica and the PDB is pruned by Flux (`prune: true` on the root Kustomization).

---

## Task 4: Open, merge PR 1 and verify (gate before PR 2)

**Files:** none (operational)

**Interfaces:**
- Consumes: commits from Tasks 2 and 3
- Produces: a verified-spread tempo/loki gateway pair; the healthy baseline every later task assumes.

- [ ] **Step 1: Push and open the PR**

```bash
git pull --rebase origin main
git push -u origin fix/monitoring-gateway-ha
gh pr create --title "fix(monitoring): spread tempo and loki gateways across nodes" --body "$(cat <<'EOF'
## Summary
- tempo: restores the chart's required podAntiAffinity that the gateway `affinity` override dropped — both replicas are on fr01-wrk-xl-02 today and it is the sole trace read/write path
- loki: gateway to 2 replicas + the same restored anti-affinity + a hand-authored PDB (loki chart 7.2.0 has no gateway.podDisruptionBudget key)

Plan: docs/superpowers/plans/2026-08-03-workload-resilience-and-pod-hardening.md (Tasks 2-4)

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] After merge: tempo-gateway pods on 2 distinct nodes, loki-gateway 2/2 on 2 distinct nodes, loki-gateway PDB ALLOWED DISRUPTIONS=1, log ingest and trace query still working
EOF
)"
```

This step requires human judgement on when to merge — do not merge automatically.

- [ ] **Step 2: Merge, then reconcile once**

```bash
flux reconcile kustomization monitoring --with-source
```

Do not repeat this in a loop.

- [ ] **Step 3: Confirm the layer and both HelmReleases are Ready**

```bash
flux get kustomizations -A | grep monitoring
flux get helmreleases -n grafana | grep -E "^loki|^tempo"
```

Expected: `monitoring` `READY=True` at the new revision; `loki` and `tempo` both `READY=True`.
(Note: `mimir` is expected to still read `READY=False` here — a 19-day-old stalled upgrade, fixed in Task 20. That is pre-existing and unrelated to this PR.)

- [ ] **Step 4: Confirm the pods are actually spread**

```bash
kubectl get pods -n grafana -o wide | grep -E "tempo-gateway|loki-gateway"
```

Expected: exactly 2 `tempo-gateway-*` pods `Running` on **two different** nodes, and exactly 2 `loki-gateway-*` pods `Running` on **two different** nodes. No pod in `Pending`.

- [ ] **Step 5: Confirm the loki-gateway PDB is effective**

```bash
kubectl get pdb loki-gateway -n grafana
```

Expected: `MAX UNAVAILABLE = 1`, `ALLOWED DISRUPTIONS = 1`.

- [ ] **Step 6: Confirm the data paths still work**

```bash
kubectl -n grafana logs deploy/loki-gateway --tail=20
# NOTE: the alloy-logs DaemonSet's name label is `alloy`; the *instance* label is
# `alloy-logs`. Selecting on name=alloy-logs matches zero pods, and the pipeline
# below would then print "no ... errors" while having inspected nothing.
kubectl -n grafana get pods -l app.kubernetes.io/instance=alloy-logs -o name | head -1 \
  | xargs -r -I{} kubectl -n grafana logs {} -c alloy --tail=20 | grep -i "error" || echo "no alloy-logs push errors"
```

Sanity-check the selector first: `kubectl -n grafana get pods -l app.kubernetes.io/instance=alloy-logs -o name | wc -l` must be `10` (one per node), not `0`.

Then, in Grafana, run a Loki query over the last 5 minutes and a Tempo trace search over the last 15 minutes; both must return results.

**Gate:** do not start Task 5 until Steps 3-6 all pass. If a gateway pod is `Pending`, the required anti-affinity could not be satisfied — check `kubectl describe pod` for `didn't match pod anti-affinity rules` and how many workers are schedulable.

**Rollback:** revert the PR 1 merge commit on `main` and reconcile once. Both changes are pure Helm values / one pruned manifest; no state is involved.

---

## Task 5: Drop the `privileged` PSA labels from the cloudflare-tunnel namespace (PR 2, commit 1)

**Files:**
- Modify: `infrastructure/base/controllers/cloudflare-tunnel-ingress-controller/namespace.yaml`

**Interfaces:**
- Consumes: PR 1 merged and healthy (Task 4)
- Produces: the namespace at `enforce: baseline`, verified by Task 9's forced connector restart.

**Evidence:** the namespace pins `enforce|warn|audit: privileged`, justified by the comment *"The cloudflared connector Deployment is generated by the controller and is not PodSecurity 'restricted'-compliant (privileged caps, runAsRoot)"*. Verified live 2026-08-03 — all four pods (`cloudflare-tunnel-ingress-controller` ×2, `controlled-cloudflared-connector` ×2) have `hostNetwork=null`, `hostPID=null`, zero `hostPath` volumes, zero container ports, and no privileged container. Talos enforces `baseline` cluster-wide by default (`/mnt/projects/lab/talos-cluster/clusters/feather-core/talos/base/controlplane.yaml`), which already admits them unchanged — so the exemption buys nothing, in the one namespace whose Secret is the Cloudflare tunnel token fronting every public hostname.

- [ ] **Step 1: Create the PR 2 branch and re-verify the premise**

```bash
git checkout main
git pull --rebase origin main
git checkout -b fix/psa-and-controller-priority-classes

kubectl get pods -n cloudflare-tunnel-ingress-controller -o json | jq -r '.items[] | "\(.metadata.name) hostNet=\(.spec.hostNetwork) hostPID=\(.spec.hostPID) hostPaths=\([.spec.volumes[]?|select(.hostPath)]|length) priv=\([.spec.containers[].securityContext.privileged//false])"'
```

Expected: all four pods `hostNet=null hostPID=null hostPaths=0 priv=[false]`.

**Abort if** any pod reports `hostNet=true`, a hostPath, or `priv=[true]` — the controller has changed what it generates and the exemption is now load-bearing.

- [ ] **Step 2: Replace the labels**

Rewrite `infrastructure/base/controllers/cloudflare-tunnel-ingress-controller/namespace.yaml` in full:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: cloudflare-tunnel-ingress-controller
  labels:
    # Was enforce/warn/audit: privileged, on the premise that the generated
    # cloudflared connector Deployment needs privileged caps + runAsRoot.
    # Verified 2026-08-03: all four pods (controller x2, connector x2) have no
    # hostNetwork, no hostPID, no hostPath volumes and no privileged container,
    # so the Talos cluster default (baseline) already admits them and the
    # exemption bought nothing — in the namespace holding the tunnel token.
    # Pinned explicitly rather than left to inherit, so a future controller
    # release that starts requiring elevation fails admission loudly.
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/warn: restricted
    pod-security.kubernetes.io/audit: restricted
```

> **Why `warn`/`audit` stay at `restricted`, not `baseline`:** the Talos cluster
> default (verified at `/mnt/projects/lab/talos-cluster/clusters/feather-core/talos/base/controlplane.yaml:260-266`)
> is `enforce: baseline, warn: restricted, audit: restricted`. Pinning
> `warn`/`audit` to `baseline` here would be **looser** than the cluster default
> and would silence the restricted-level warnings that tell you when the
> controller starts generating a more privileged connector. Only `enforce` needs
> to be pinned; the other two mirror the cluster default deliberately.
> If you prefer strict parity with the old file's shape, `baseline` for all three
> is still an improvement over `privileged` — but it is a downgrade of the
> warn/audit signal, so say so in the PR body.

- [ ] **Step 3: Render and verify**

```bash
kubectl kustomize infrastructure/clusters/feather-core/base-controllers/cloudflare-tunnel-ingress-controller | grep -A 6 "kind: Namespace"
```

Expected: `pod-security.kubernetes.io/enforce: baseline`, `warn: restricted`, `audit: restricted`, and no `privileged` remains.

- [ ] **Step 4: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/base/controllers/cloudflare-tunnel-ingress-controller/namespace.yaml
git commit -m "fix(cloudflare-tunnel): drop unnecessary privileged psa labels from the namespace"
```

**Rollback:** revert this commit. PSA label changes never evict running pods, so the worst case is that a *new* connector pod is rejected — Task 9 Step 4 forces exactly that scenario before the change is considered done.

---

## Task 6: Add `feather-platform` to MetalLB controller and speaker (PR 2, commit 2)

**Files:**
- Modify: `infrastructure/clusters/feather-core/base-controllers/metallb/kustomization.yml:9-41`

**Interfaces:**
- Consumes: nothing new
- Produces: MetalLB pods at priority 900000000.

**Evidence:** the file's own comment (lines 6-8) states the intent — *"leaving the controller and speakers BestEffort (first to be evicted — would drop BGP announcements)"* — and the patch fixes QoS but never sets `priorityClassName`. Live: controller and all 7 speakers show `priorityClassName: null`, QoS `Burstable`.

**Honest framing (the audit's own correction):** this is *not* about eviction. All these pods run **under** their 64Mi requests (speakers 28-41Mi, controller 63Mi) and the kubelet ranks over-request pods first, so they were never near the front of the eviction queue. The value of this change is **preemption**: a pending `feather-critical`/`feather-platform` pod can currently preempt a MetalLB speaker at schedule time, silently withdrawing that node's L2 announcement for any LoadBalancer service homed there — and MetalLB fronts `envoy-envoy-eg` (10.200.90.1), the CNPG external `-rw` endpoint, Dragonfly, MaxScale and step-ca. Consistency with the analogous data-plane components (`configs/gateway/envoyproxy.yaml:17`, `base-controllers/cloudflare-tunnel-ingress-controller/release.yaml:26`) is the other half.

- [ ] **Step 1: Add `priorityClassName` to both patches**

In `infrastructure/clusters/feather-core/base-controllers/metallb/kustomization.yml`, add one line to each of the two strategic-merge patches, as a sibling of `containers:` under `spec.template.spec`:

```yaml
patches:
  - patch: |
      apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: controller
      spec:
        template:
          spec:
            priorityClassName: feather-platform
            containers:
              - name: controller
                resources:
                  requests:
                    cpu: 25m
                    memory: 64Mi
                  limits:
                    memory: 128Mi
  - patch: |
      apiVersion: apps/v1
      kind: DaemonSet
      metadata:
        name: speaker
      spec:
        template:
          spec:
            priorityClassName: feather-platform
            containers:
              - name: speaker
                resources:
                  requests:
                    cpu: 25m
                    memory: 64Mi
                  limits:
                    memory: 128Mi
```

Also extend the comment at lines 6-8 with a second sentence:

```yaml
# Upstream MetalLB manifests ship no resource requests/limits, leaving the
# controller and speakers BestEffort (first to be evicted — would drop BGP
# announcements). Inject modest requests + memory limits, and feather-platform
# so a pending high-priority pod cannot preempt a speaker off a node and
# silently withdraw that node's LoadBalancer announcement.
```

- [ ] **Step 2: Render and verify**

```bash
kubectl kustomize infrastructure/clusters/feather-core/base-controllers/metallb | grep -c "priorityClassName: feather-platform"
```

Expected: `2`.

- [ ] **Step 3: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 4: Commit**

```bash
git add infrastructure/clusters/feather-core/base-controllers/metallb/kustomization.yml
git commit -m "fix(metallb): set feather-platform priority class on controller and speaker"
```

**Rollback:** revert the commit; the pods roll back to priority 0.

---

## Task 7: Add priority classes to the remaining unprotected control-plane workloads (PR 2, commit 3)

> ⚠️ **This restarts four operators.** All are reconcilers with no request-serving hot path (rook-ceph-operator, mariadb-operator ×3 HA replicas + webhook, envoy-gateway controller, flux notification-controller). Envoy **dataplane** pods are not touched by an envoy-gateway controller restart. No user-facing traffic is affected. Do not batch this with the Ceph MDS/RGW change (Task 8) — that one restarts data-plane daemons and belongs in its own window.

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook/release.yaml` (after line 31)
- Modify: `infrastructure/base/controllers/mariadb-operator/release.yaml` (after line 20 and inside the `webhook:` block)
- Modify: `infrastructure/clusters/feather-core/controllers/envoy/release.yaml:12-16`

**Interfaces:**
- Consumes: nothing new
- Produces: `rook-ceph-operator`, `mariadb-operator`, `mariadb-operator-webhook` and `envoy-gateway` at `feather-platform`.

**Evidence:** `kubectl get deploy -A -o json | jq` confirms `envoy/envoy-gateway`, `mariadb-operator/mariadb-operator`, `mariadb-operator/mariadb-operator-webhook` and `rook-ceph/rook-ceph-operator` all have `priorityClassName: null`. Chart support verified: `rook-ceph` 1.20.2 has a top-level `priorityClassName`; `mariadb-operator` has `priorityClassName` plus `webhook.priorityClassName`; `gateway-helm` 1.7.5 has `deployment.priorityClassName`.

- [ ] **Step 1: rook-ceph operator**

In `infrastructure/clusters/feather-core/rook/release.yaml`, immediately after line 31 (`obcAllowAdditionalConfigFields: "maxObjects,maxSize,bucketOwner"`), add:

```yaml
    # The operator reconciles every Ceph daemon; keep it off priority 0 so a
    # pending high-priority pod cannot preempt it. Matches the tier used for
    # the other stateful-core components (envoy, cloudflare-tunnel).
    priorityClassName: feather-platform
```

- [ ] **Step 2: mariadb-operator (controller + webhook)**

In `infrastructure/base/controllers/mariadb-operator/release.yaml`, add a top-level `priorityClassName` immediately after line 20 (`  values:`), and a `priorityClassName` inside the existing `webhook:` block (which starts at line 29):

```yaml
  values:
    priorityClassName: feather-platform
    resources:
      requests:
        cpu: 50m
        memory: 128Mi
      limits:
        memory: 256Mi
    metrics:
      enabled: false
    webhook:
      priorityClassName: feather-platform
      resources:
        requests:
          cpu: 25m
          memory: 64Mi
        limits:
          memory: 128Mi
      cert:
        certManager:
          enabled: true
```

(the rest of the file — `ha:`, `affinity:`, `pdb:` — is unchanged)

Do **not** add `certController.priorityClassName`: no `mariadb-operator-cert-controller` Deployment exists on this cluster (`cert.certManager.enabled: true` means cert-manager issues the webhook cert instead).

- [ ] **Step 3: envoy-gateway**

In `infrastructure/clusters/feather-core/controllers/envoy/release.yaml`, replace lines 12-16:

```yaml
  values:
    config:
      envoyGateway:
        extensionApis:
          enableBackend: true
```

with:

```yaml
  values:
    # The Envoy *dataplane* pods already carry feather-platform via
    # configs/gateway/envoyproxy.yaml:17; the controller that programs them did
    # not. Restarting the controller does not restart the dataplane.
    deployment:
      priorityClassName: feather-platform
    config:
      envoyGateway:
        extensionApis:
          enableBackend: true
```

- [ ] **Step 4: Render and verify all three**

```bash
kubectl kustomize infrastructure/clusters/feather-core/rook | grep -c "priorityClassName: feather-platform"
kubectl kustomize infrastructure/clusters/feather-core/controllers | grep -c "priorityClassName: feather-platform"
```

Expected: `1` for the rook layer; `3` for the controllers layer (envoy `deployment.priorityClassName`, mariadb-operator top-level, mariadb-operator `webhook.priorityClassName` — `mariadb-operator` is pulled into `controllers` via `infrastructure/clusters/feather-core/controllers/kustomization.yaml`).

- [ ] **Step 5: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/clusters/feather-core/rook/release.yaml \
        infrastructure/base/controllers/mariadb-operator/release.yaml \
        infrastructure/clusters/feather-core/controllers/envoy/release.yaml
git commit -m "fix(controllers): set feather-platform priority class on rook, mariadb and envoy operators"
```

**Rollback:** revert the commit. Each is a Helm value; the operators roll back on the next reconcile.

---

## Task 8: Set `feather-critical` on Ceph MDS and RGW (PR 2, commit 4)

> ⚠️ **Decision gate 4 — this restarts the active CephFS MDS.** The standby-replay MDS takes over, but CephFS clients see a short metadata stall. Pick a quiet window. RGW has 3 instances behind a Service, so its rollout is non-disruptive. **Do not run this commit's reconcile at the same time as Task 7's.**

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook-fr01/cluster/filesystem.yaml` (inside `spec.metadataServer`, after line 18)
- Modify: `infrastructure/clusters/feather-core/rook-fr01/cluster/objectstore.yaml` (inside `spec.gateway`, after line 40)

**Interfaces:**
- Consumes: nothing new
- Produces: MDS and RGW pods at `feather-critical` (1000000000).

**Evidence:** `rook-fr01/cluster/cluster.yaml:49-52` sets `priorityClassNames` for `mon`/`osd`/`mgr` only. Rook's `CephCluster.spec.priorityClassNames` map **does not** cover MDS or RGW — they must be set on their own CRs. Verified the CRDs accept it:
`kubectl get crd cephfilesystems.ceph.rook.io -o json | jq '.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.metadataServer.properties.priorityClassName'` → `{"description":"PriorityClassName sets priority classes on components","type":"string"}`, and the equivalent on `cephobjectstores.ceph.rook.io` `spec.gateway` → `{"description":"PriorityClassName sets priority classes on the rgw pods","type":"string"}`.

**Honest framing (the audit's own correction):** this is a consistency gap, not a live eviction risk. MDS/RGW run well under their requests (mds 31-69Mi against a 512Mi request, rgw 264-453Mi against a 1Gi request), the kubelet ranks over-request pods first, and `kubeshark-worker` on the same storage node sits at 618Mi against a 50Mi request — it would be evicted many rounds earlier. The 161-167% figure on those nodes is memory *limits*; *requests* are 69%, and eviction is driven by usage. Six other priority-0 pods also share those nodes. Fix it because "mon/osd/mgr protected, MDS/RGW not" is an inconsistency waiting to bite, not because it is about to.

- [ ] **Step 1: Confirm the current state**

```bash
kubectl get pods -n rook-ceph-fr01 -o custom-columns=N:.metadata.name,P:.spec.priorityClassName | grep -E "mds|rgw"
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph status
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph fs status feather-cephfs
```

Expected: all `mds`/`rgw` rows show `<none>`; `ceph status` is `HEALTH_OK` (or only pre-existing warnings); `ceph fs status` shows one active MDS and one standby-replay.

**Abort if** there is no standby MDS — without it, restarting the active MDS is a real outage, not a stall.

- [ ] **Step 2: filesystem.yaml**

In `infrastructure/clusters/feather-core/rook-fr01/cluster/filesystem.yaml`, add one line inside `spec.metadataServer`, immediately after line 18 (`    activeStandby: true`):

```yaml
  metadataServer:
    activeCount: 1
    activeStandby: true
    # CephCluster.spec.priorityClassNames (cluster.yaml:49-52) covers mon/osd/mgr
    # only — MDS and RGW must be set on their own CRs.
    priorityClassName: feather-critical
    placement:
```

- [ ] **Step 3: objectstore.yaml**

In `infrastructure/clusters/feather-core/rook-fr01/cluster/objectstore.yaml`, add one line inside `spec.gateway`, immediately after line 40 (`    instances: 3`):

```yaml
  gateway:
    port: 80
    instances: 3
    # See filesystem.yaml — CephCluster.spec.priorityClassNames does not cover RGW.
    priorityClassName: feather-critical
```

Do not touch `rgwCommandFlags` — changing it restarts all RGW pods for a different reason and `port=80` must stay in sync with `spec.gateway.port`.

- [ ] **Step 4: Render and verify**

```bash
kubectl kustomize infrastructure/clusters/feather-core/rook-fr01 | grep -c "priorityClassName: feather-critical"
```

Expected: `2`.

- [ ] **Step 5: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/clusters/feather-core/rook-fr01/cluster/filesystem.yaml \
        infrastructure/clusters/feather-core/rook-fr01/cluster/objectstore.yaml
git commit -m "fix(rook): set feather-critical priority class on ceph mds and rgw"
```

**Rollback:** revert the commit; Rook restarts the daemons back to priority 0. Same MDS stall on the way back — plan the revert for a quiet window too.

---

## Task 9: Open, merge PR 2 and verify (gate before PR 3)

**Files:** none (operational)

**Interfaces:**
- Consumes: commits from Tasks 5-8
- Produces: verified PSA change + priority classes.

- [ ] **Step 1: Push and open**

```bash
git pull --rebase origin main
git push -u origin fix/psa-and-controller-priority-classes
gh pr create --title "fix(cluster): tighten cloudflare-tunnel psa and fill priority-class gaps" --body "$(cat <<'EOF'
## Summary
- cloudflare-tunnel namespace: enforce/warn/audit privileged -> baseline (verified: no pod there uses hostNetwork, hostPath or privileged, so the cluster default already admits them)
- metallb controller + speaker, rook-ceph-operator, mariadb-operator (+webhook), envoy-gateway: priorityClassName feather-platform
- ceph MDS + RGW: priorityClassName feather-critical (CephCluster.spec.priorityClassNames does not cover them)

Plan: docs/superpowers/plans/2026-08-03-workload-resilience-and-pod-hardening.md (Tasks 5-9)

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] After merge: force a cloudflared connector restart and confirm it is admitted under baseline
- [ ] All named workloads report the expected priorityClassName
- [ ] ceph status HEALTH_OK and `ceph fs status` shows an active MDS after the MDS restart
EOF
)"
```

Human judgement on merge timing — the Ceph MDS restart wants a quiet window.

- [ ] **Step 2: Merge, then reconcile the affected layers once each**

```bash
flux reconcile kustomization base-controllers --with-source
flux get kustomizations -A | grep base-controllers      # wait for READY=True at the new revision
flux reconcile kustomization controllers
flux reconcile kustomization rook
flux get kustomizations -A | grep -E "^rook "           # wait for READY=True
flux reconcile kustomization rook-fr01
```

One pass. Do not loop.

> ⚠️ **Wait between these, do not fire all four back to back.** `controllers`
> `dependsOn` `base-controllers`, and `rook-fr01` `dependsOn` `rook` (which itself
> dependsOn base-sources/base-controllers/base-configs/controllers). Flux requires
> a dependency to be `Ready` **at the same git revision** before a dependent
> reconciles, so forcing a dependent while its parent is still `Reconciling` just
> makes it report "dependency not ready" and you have manufactured the churn
> `CLAUDE.md` warns about. If in doubt, reconcile only `base-controllers` and let
> the graph pull the other three on its own 10 m interval.

- [ ] **Step 3: Confirm the layers are Ready**

```bash
flux get kustomizations -A
```

Expected: `base-controllers`, `controllers`, `rook`, `rook-fr01` all `READY=True` at the new revision.

- [ ] **Step 4: Force a cloudflared connector restart and confirm baseline admits it**

> This is the load-bearing check for Task 5. The PSA label change does not evict running pods, so without this you have not tested anything.

```bash
kubectl get ns cloudflare-tunnel-ingress-controller -o jsonpath='{.metadata.labels}' | tr ',' '\n' | grep pod-security
kubectl -n cloudflare-tunnel-ingress-controller delete pod -l app=controlled-cloudflared-connector
sleep 45
kubectl -n cloudflare-tunnel-ingress-controller get pods
kubectl -n cloudflare-tunnel-ingress-controller get events --sort-by=.lastTimestamp | tail -20
```

Expected: `enforce=baseline`, `warn=restricted`, `audit=restricted`; both connector pods come back `1/1 Running`; **no** `FailedCreate` / `violates PodSecurity` events. A `Warning` that mentions `would violate PodSecurity "restricted"` is **expected and harmless** — that is the `warn` level doing its job, not an admission failure. Only `enforce` rejections block a pod.

**If a pod is rejected:** immediately revert the PR 2 merge commit and reconcile `base-controllers` once. Every public hostname routes through these connectors.

- [ ] **Step 5: Confirm the public edge still works**

```bash
curl -sSI https://repo.onelitefeather.dev/ | head -1
curl -sSI https://harbor.onelitefeather.dev/ | head -1
```

Expected: an HTTP status line from each (any 2xx/3xx — not a connection failure or a Cloudflare 502/1033).

- [ ] **Step 6: Confirm every priority class landed**

```bash
kubectl get pods -A -o json | jq -r '.items[] | select(.metadata.namespace=="metallb-system" or (.metadata.name|test("rook-ceph-operator|mariadb-operator|envoy-gateway"))) | "\(.metadata.namespace)/\(.metadata.name) \(.spec.priorityClassName)"'
kubectl get pods -n rook-ceph-fr01 -o custom-columns=N:.metadata.name,P:.spec.priorityClassName | grep -E "mds|rgw"
```

Expected: MetalLB controller + all speakers, `rook-ceph-operator`, `mariadb-operator*`, `envoy-gateway` → `feather-platform`; all `rook-ceph-mds-*` and `rook-ceph-rgw-*` → `feather-critical`.

- [ ] **Step 7: Confirm Ceph is healthy after the MDS/RGW restart**

```bash
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph status
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph fs status feather-cephfs
kubectl get pods -n rook-ceph-fr01 | grep -E "mds|rgw"
```

Expected: `HEALTH_OK`; one MDS `active` and one `standby-replay`; all MDS/RGW pods `Running`, none `CrashLoopBackOff`.

- [ ] **Step 8: Confirm CephFS RWX volumes are still writable**

Pick any pod with a `ceph-cephfs` PVC (`kubectl get pvc -A | grep cephfs`) and confirm it is still `Running` and its mount is live (`kubectl exec ... -- touch /<mountpath>/.mds-restart-check && rm /<mountpath>/.mds-restart-check`).

**Gate:** do not start Task 10 until Steps 3-8 pass.

**Rollback:** revert the PR 2 merge commit. Reverting the MDS priority class restarts the MDS again — same quiet-window caveat.

---

## Task 10: Give Plane realistic requests and RabbitMQ a QoS class (PR 3, commit 1)

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/plane/release.yaml` (the `services:` block, lines 52-85; add a `monitor:` block)
- Modify: `apps/clusters/feathre-core/base-apps/plane/release.yaml` (the `postRenderers:` block, after line 42)

**Interfaces:**
- Consumes: PR 2 merged and healthy (Task 9)
- Produces: Plane pods with requests within ~20% of real usage and `plane-rabbitmq-wl` out of `BestEffort`, for Task 12's gate.

**Evidence (verified live 2026-08-03):** nine of eleven Plane containers request `50m/50Mi` against limits of 1000-2000Mi. `kubectl top pods -n plane`: `plane-worker-wl 483Mi`, `plane-beat-worker-wl 276Mi`, `plane-api-wl 268Mi`, `plane-silo-wl 209Mi`, `plane-live-wl 199Mi`, `plane-space-wl 150Mi`, `plane-rabbitmq-wl 155Mi/453m`. `plane-rabbitmq-wl` has `resources: {}` entirely — **BestEffort**, the first thing evicted under node pressure, and it is the queue backing every worker. `kubectl get limitrange -A` → `No resources found`, so nothing floors any of this.

**Two corrections to the audit:** `plane-opensearch-wl` is correctly sized already (500m/2Gi requests) and is **excluded**. And the scheduler-accounting gap is ~20% (2.4Gi requested vs 2.9Gi actual), not 5-10× — worker nodes sit at 40-42% of allocatable in requests, so there is ample headroom; this is about correctness and eviction ranking, not about reclaiming a crisis.

Chart value keys verified against `plane-enterprise` 3.0.0: every `services.<component>` block exposes `memoryRequest` / `cpuRequest` / `memoryLimit` / `cpuLimit`. **`services.rabbitmq` does not** — it has no resource keys at all, which is why it needs a post-render patch.

- [ ] **Step 1: Create the PR 3 branch and check node headroom**

```bash
git checkout main
git pull --rebase origin main
git checkout -b feat/plane-requests-and-probes

kubectl describe nodes fr01-wrk-xl-01 fr01-wrk-xl-02 fr01-wrk-xl-03 fr01-wrk-xl-04 | grep -A 6 "Allocated resources"
kubectl top pods -n plane
```

Expected: memory *requests* on each worker at ~40-45% of allocatable. This task adds ~+1.9Gi of memory requests cluster-wide (from ~450Mi to ~2.1Gi across the nine Plane containers, plus 256Mi for RabbitMQ). Abort and re-plan the numbers downward if any worker is already above 70% of allocatable in memory **requests**.

- [ ] **Step 2: Set requests on the nine under-sized components**

In `apps/clusters/feathre-core/base-apps/plane/release.yaml`, replace lines 64-85 (from `      web:` through `        enabled: true` of the `silo:` block) with:

```yaml
      # Requests were the chart default 50m/50Mi against 1000-2000Mi limits
      # while `kubectl top` showed 150-483Mi. Floors below are ~real usage
      # rounded up; the cluster has no LimitRange anywhere to correct them.
      # plane-opensearch-wl is deliberately absent — it is already sized
      # correctly by the chart (500m/2Gi).
      web:
        assign_cluster_ip: true
        memoryRequest: 64Mi
        cpuRequest: 25m
      space:
        assign_cluster_ip: true
        memoryRequest: 256Mi
        cpuRequest: 50m
      admin:
        assign_cluster_ip: true
        memoryRequest: 64Mi
        cpuRequest: 25m
      api:
        assign_cluster_ip: true
        memoryRequest: 320Mi
        cpuRequest: 100m
      live:
        assign_cluster_ip: true
        memoryRequest: 256Mi
        cpuRequest: 50m
      monitor:
        memoryRequest: 64Mi
        cpuRequest: 25m

      # Same reasoning as the CE cutover: chart default (1000Mi) OOMKilled the
      # worker repeatedly under real load. Bump both async components.
      worker:
        memoryLimit: 2000Mi
        cpuLimit: "1"
        memoryRequest: 512Mi
        cpuRequest: 100m
      beatworker:
        memoryLimit: 1500Mi
        cpuLimit: 500m
        memoryRequest: 320Mi
        cpuRequest: 100m

      silo:
        enabled: true
        memoryRequest: 256Mi
        cpuRequest: 50m
```

Keys must be merged into the **existing** blocks — duplicate `web:`/`api:` keys in the same mapping are a YAML error and `validate.sh` will not necessarily catch it inside a Helm `values:` blob.

- [ ] **Step 3: Give `plane-rabbitmq-wl` explicit resources via post-render**

In the same file, append to the `postRenderers[0].kustomize.patches` list, after the `plane-worker-wl` patch that ends at line 42:

```yaml
          # services.rabbitmq exposes no resource keys in plane-enterprise 3.0.0,
          # so the StatefulSet ships with `resources: {}` — BestEffort, and the
          # first thing the kubelet evicts, despite being the queue every worker
          # depends on. No CPU limit on purpose: throttling a broker is worse
          # than letting it burst (observed 453m under normal load).
          - target:
              kind: StatefulSet
              name: plane-rabbitmq-wl
            patch: |
              - op: add
                path: /spec/template/spec/containers/0/resources
                value:
                  requests:
                    cpu: 250m
                    memory: 256Mi
                  limits:
                    memory: 1Gi
```

- [ ] **Step 4: Render and verify**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/plane | grep -A 30 "memoryRequest"
kubectl kustomize apps/clusters/feathre-core/base-apps/plane | grep -A 12 "name: plane-rabbitmq-wl"
# PyYAML's safe_load silently accepts duplicate mapping keys (last one wins), so
# a plain `yaml.safe_load_all(...)` is NOT the guard Step 2 warns about. This
# loader raises on them.
python3 - <<'PY'
import yaml
class Dup(yaml.SafeLoader): pass
def m(loader, node, deep=False):
    seen=set()
    for k,_ in node.value:
        key=loader.construct_object(k, deep=True)
        if key in seen:
            raise ValueError(f"duplicate key {key!r} at {k.start_mark}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)
Dup.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, m)
list(yaml.load_all(open('apps/clusters/feathre-core/base-apps/plane/release.yaml'), Dup))
print('yaml ok, no duplicate keys')
PY
```

Expected: the rendered HelmRelease `values.services` contains `memoryRequest` on exactly nine components (`web, space, admin, api, live, monitor, worker, beatworker, silo`) and **not** on `opensearch`; the rabbitmq JSON6902 patch is present; `yaml ok, no duplicate keys`.

**If the duplicate-key check raises,** you left the original `web:`/`api:`/`worker:` blocks in place instead of merging into them. Fix before committing — Helm would silently take only the last block and the requests you expect would not be applied.

- [ ] **Step 5: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 6: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/plane/release.yaml
git commit -m "fix(plane): set realistic resource requests and give rabbitmq a qos class"
```

**Rollback:** revert the commit; requests drop back to the chart defaults on the next Helm upgrade.

---

## Task 11: Add readiness probes to Plane's HTTP components (PR 3, commit 2)

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/plane/release.yaml` (`postRenderers` block)

**Interfaces:**
- Consumes: Task 10's branch
- Produces: readiness probes on five Deployments, so a hung pod leaves its Endpoints and a bad image rollout is gated.

**Evidence:** nine of eleven Plane containers have **no probe at all** (`plane-api-wl` has a readinessProbe, `plane-rabbitmq-wl` has an exec readinessProbe; the other nine have neither). Without a readiness probe, a hung `plane-web`/`plane-live`/`plane-silo` keeps its Endpoint and keeps taking traffic, and there is no rollout gate — a bad image rolls to completion.

**Health endpoints verified live 2026-08-03** by curling from inside the cluster (`kubectl -n plane exec deploy/plane-api-wl -- curl -s -o /dev/null -w '%{http_code}'`):

| Component | Port | Path | Code |
|---|---|---|---|
| `plane-web-wl` | 3000 | `/` | 200 |
| `plane-admin-wl` | 3000 | `/` | 200 |
| `plane-space-wl` | 3000 | `/spaces/` | 200 (`/spaces` → 301, `/` → 404) |
| `plane-live-wl` | 3000 | `/live/health` | 200 (`/` and `/live` → 404) |
| `plane-silo-wl` | 3000 | `/silo/health` | 200 (`/` and `/silo` → 404) |
| `plane-monitor-wl` | 8080 | — | `/` → 404, no endpoint found → **no probe added** |

Ports are numeric because the Plane chart declares **no** `ports:` on any container (`kubectl get deploy -n plane -o json | jq '.spec.template.spec.containers[0].ports'` → `null`), so a named port would not resolve.

Only **readiness** probes are added. A liveness probe on a component whose slow-start behaviour is unmeasured would turn a slow boot into a crashloop; readiness is the safe half and is what buys the Endpoint removal and the rollout gate.

- [ ] **Step 1: Re-verify each endpoint before writing the probes**

```bash
kubectl -n plane exec deploy/plane-api-wl -- sh -c 'for u in http://plane-web:3000/ http://plane-admin:3000/ http://plane-space:3000/spaces/ http://plane-live:3000/live/health http://plane-silo:3000/silo/health; do printf "%s " "$u"; curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 "$u"; done'
```

Expected: five lines, all `200`.

**Abort on any non-2xx/3xx** — a readiness probe against a path that does not return 2xx/3xx removes that component from its Service permanently.

- [ ] **Step 2: Append the five probe patches**

In `apps/clusters/feathre-core/base-apps/plane/release.yaml`, append to the same `postRenderers[0].kustomize.patches` list, after the rabbitmq patch from Task 10:

```yaml
          # Nine of eleven Plane containers shipped with no probe at all, so a
          # hung pod kept its Endpoint and a bad image rolled to completion with
          # no gate. Readiness only — liveness on an unmeasured slow-start path
          # would turn a cold boot into a self-inflicted crashloop. Paths and
          # codes verified live 2026-08-03; ports are numeric because the chart
          # declares no containerPort on any container.
          - target:
              kind: Deployment
              name: plane-web-wl
            patch: |
              - op: add
                path: /spec/template/spec/containers/0/readinessProbe
                value:
                  httpGet:
                    path: /
                    port: 3000
                  initialDelaySeconds: 15
                  periodSeconds: 10
                  timeoutSeconds: 5
                  failureThreshold: 3
          - target:
              kind: Deployment
              name: plane-admin-wl
            patch: |
              - op: add
                path: /spec/template/spec/containers/0/readinessProbe
                value:
                  httpGet:
                    path: /
                    port: 3000
                  initialDelaySeconds: 15
                  periodSeconds: 10
                  timeoutSeconds: 5
                  failureThreshold: 3
          - target:
              kind: Deployment
              name: plane-space-wl
            patch: |
              - op: add
                path: /spec/template/spec/containers/0/readinessProbe
                value:
                  httpGet:
                    path: /spaces/
                    port: 3000
                  initialDelaySeconds: 15
                  periodSeconds: 10
                  timeoutSeconds: 5
                  failureThreshold: 3
          - target:
              kind: Deployment
              name: plane-live-wl
            patch: |
              - op: add
                path: /spec/template/spec/containers/0/readinessProbe
                value:
                  httpGet:
                    path: /live/health
                    port: 3000
                  initialDelaySeconds: 15
                  periodSeconds: 10
                  timeoutSeconds: 5
                  failureThreshold: 3
          - target:
              kind: Deployment
              name: plane-silo-wl
            patch: |
              - op: add
                path: /spec/template/spec/containers/0/readinessProbe
                value:
                  httpGet:
                    path: /silo/health
                    port: 3000
                  initialDelaySeconds: 15
                  periodSeconds: 10
                  timeoutSeconds: 5
                  failureThreshold: 3
```

- [ ] **Step 3: Render and verify**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/plane | grep -c "readinessProbe"
```

Expected: `5`.

- [ ] **Step 4: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 5: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/plane/release.yaml
git commit -m "feat(plane): add readiness probes to the http components"
```

**Rollback:** revert the commit; the probes disappear on the next Helm upgrade.

---

## Task 12: Align `notification-controller` with its Flux siblings (PR 3, commit 3)

**Files:**
- Modify: `clusters/feather-core/flux-system/kustomization.yaml`

**Interfaces:**
- Consumes: Task 11's branch
- Produces: `notification-controller` at `system-cluster-critical`, matching the other three Flux controllers.

**Evidence:** `clusters/feather-core/flux-system/gotk-components.yaml` sets `priorityClassName: system-cluster-critical` on `source-controller` (line 2655), `kustomize-controller` (3576) and `helm-controller` (5172), but **not** on `notification-controller` — an upstream gotk quirk. Live: `flux-system/notification-controller` → `priorityClassName: null`. The `critical-pods-flux-system` ResourceQuota in `flux-system` is scoped to `system-node-critical`/`system-cluster-critical` with `hard.pods: 1k` and only 3 pods used — ample headroom.

`gotk-components.yaml` is generated by `flux bootstrap` ("DO NOT EDIT"), so the patch goes in the sibling `kustomization.yaml`, which this repo already treats as locally-owned (it lists a hand-added `monitoring.yaml`).

**Why this file is actually reconciled** (verify before trusting the task): `clusters/feather-core/` has **no** `kustomization.yaml` of its own, so kustomize-controller generates one by scanning the directory. Its scan adds any sub-directory that contains a kustomization file as a *resource* and does not descend into it — so `clusters/feather-core/flux-system/kustomization.yaml` is genuinely evaluated on every root reconcile, and `patches:` added there take effect. Confirm with `ls clusters/feather-core/kustomization.yaml` → `No such file or directory`.

> ⚠️ **This restarts `notification-controller`.** For the ~30 s of the rolling
> update, Flux `Alert`/`Provider` dispatch is down — Discord alert notifications
> raised in that window are lost (the controller does not replay them). It is a
> notification gap, not a reconciliation gap: source-, kustomize- and
> helm-controller keep working. Do not run this at the same time as a change you
> are relying on alerts to watch (i.e. not together with Task 20).

- [ ] **Step 1: Add the patch**

Rewrite `clusters/feather-core/flux-system/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
- gotk-components.yaml
- gotk-sync.yaml
- monitoring.yaml
# gotk-components.yaml (generated, DO NOT EDIT) sets system-cluster-critical on
# source-, kustomize- and helm-controller but not on notification-controller.
# Patched here so it survives a `flux bootstrap` regenerating gotk-components.
patches:
- patch: |
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: notification-controller
      namespace: flux-system
    spec:
      template:
        spec:
          priorityClassName: system-cluster-critical
```

- [ ] **Step 2: Render and verify** (`scripts/validate.sh` does **not** build this path — this check is the only coverage)

```bash
kubectl kustomize clusters/feather-core/flux-system | grep -B 40 "priorityClassName: system-cluster-critical" | grep -c "name: notification-controller"
kubectl kustomize clusters/feather-core/flux-system | grep -c "priorityClassName: system-cluster-critical"
```

Expected: the second command returns `4` (was `3`). The first returns ≥1.

- [ ] **Step 3: Validate**

Run: `./scripts/validate.sh` — expected exit `0` (it kubeconforms `gotk-components.yaml` and `clusters/feather-core/*.yaml` directly; the patch does not change those).

- [ ] **Step 4: Commit**

```bash
git add clusters/feather-core/flux-system/kustomization.yaml
git commit -m "fix(flux): give notification-controller the same priority class as its siblings"
```

**Rollback:** revert the commit.

---

## Task 13: Open, merge PR 3 and verify (gate before PR 4)

**Files:** none (operational)

**Interfaces:**
- Consumes: commits from Tasks 10-12
- Produces: verified Plane sizing/probes and a corrected Flux priority class.

- [ ] **Step 1: Push and open**

```bash
git pull --rebase origin main
git push -u origin feat/plane-requests-and-probes
gh pr create --title "feat(plane): realistic requests, rabbitmq qos and readiness probes" --body "$(cat <<'EOF'
## Summary
- plane: requests set from `kubectl top` on the nine under-sized components (opensearch excluded, it is already sized correctly)
- plane-rabbitmq-wl: explicit requests/limits via post-render — it was BestEffort and backs every worker
- plane web/admin/space/live/silo: readiness probes on endpoints verified live (200 on /, /, /spaces/, /live/health, /silo/health)
- flux notification-controller: system-cluster-critical, matching its three siblings

Plan: docs/superpowers/plans/2026-08-03-workload-resilience-and-pod-hardening.md (Tasks 10-13)

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] After merge: all plane pods Running and Ready, rabbitmq QoS Burstable, tasks.onelitefeather.net loads
EOF
)"
```

- [ ] **Step 2: Merge, then reconcile once each**

```bash
flux reconcile kustomization flux-system --with-source
```

> ⚠️ **`flux-system` is the ROOT Kustomization**, not a narrow layer:
> `clusters/feather-core/flux-system/gotk-sync.yaml` points it at
> `./clusters/feather-core` with `prune: true`. Reconciling it re-applies the
> **entire** dependency graph, so this single command already covers `base-apps`
> — do not also reconcile `base-apps` separately, and do not run this while any
> other layer is mid-rollout. This is the one place in the plan where a reconcile
> touches everything; expect `flux get kustomizations -A` to show several layers
> briefly `Reconciling`. That is normal. Wait for it to settle; **do not** issue a
> second reconcile.

- [ ] **Step 3: Confirm the layers and HelmRelease are Ready**

```bash
flux get kustomizations -A | grep -E "base-apps|flux-system"
flux get helmreleases -n plane
```

Expected: both `READY=True`; `plane` `READY=True`.

- [ ] **Step 4: Confirm every Plane pod is Running and Ready**

```bash
kubectl get pods -n plane
kubectl get deploy,sts -n plane -o json | jq -r '.items[] | "\(.metadata.name) req=\(.spec.template.spec.containers[0].resources.requests) readiness=\(.spec.template.spec.containers[0].readinessProbe.httpGet.path // .spec.template.spec.containers[0].readinessProbe.exec.command // "none")"'
kubectl get pods -n plane -o json | jq -r '.items[] | "\(.metadata.name) \(.status.qosClass)"'
```

Expected: every pod `Running` with all containers ready (`1/1`); the nine components show the new requests; `plane-web-wl/admin/space/live/silo` show their readiness paths; **no** pod reports `qosClass: BestEffort`.

**If a pod sits at `0/1 Running` with `Readiness probe failed`:** the endpoint changed. Revert the PR 3 merge commit immediately — the Service will have dropped that component's Endpoint.

- [ ] **Step 5: Confirm the app works end to end**

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://tasks.onelitefeather.net/
```

Expected: `200` (or `302` to the login page).

- [ ] **Step 6: Confirm node headroom is still sane**

```bash
kubectl describe nodes fr01-wrk-xl-01 fr01-wrk-xl-02 fr01-wrk-xl-03 fr01-wrk-xl-04 | grep -A 6 "Allocated resources"
```

Expected: memory *requests* still below ~60% of allocatable on every worker, and no pod anywhere in `Pending` with `Insufficient memory`.

- [ ] **Step 7: Confirm notification-controller**

```bash
kubectl get deploy notification-controller -n flux-system -o jsonpath='{.spec.template.spec.priorityClassName}{"\n"}'
kubectl get resourcequota -n flux-system
```

Expected: `system-cluster-critical`; the quota shows `pods: 4/1k` (was 3).

**Gate:** do not start Task 14 until Steps 3-7 pass.

**Rollback:** revert the PR 3 merge commit and reconcile `flux-system` **once**. All three commits are declarative (Helm values, a post-render patch list, a Kustomize patch); nothing in PR 3 touches state, a Secret or a PVC. The Plane pods roll back to the chart-default requests and lose their probes; `notification-controller` rolls back to priority 0. If only the Plane half is bad, prefer reverting the two Plane commits individually (`git revert <sha>` per commit) over reverting the merge — the `notification-controller` change is independent and worth keeping.

---

## Task 14: Delete `helm/metabase` and add `seccompProfile` to `helm/micronaut` (PR 4)

**Files:**
- Delete: `helm/metabase/` (entire directory)
- Modify: `CLAUDE.md:14`
- Modify: `helm/micronaut/values.yaml:252-257`
- Modify: `helm/micronaut/Chart.yaml` (version `0.5.2` → `0.5.3`)

**Interfaces:**
- Consumes: PR 3 merged and healthy (Task 13)
- Produces: one fewer dead chart; `seccompProfile: RuntimeDefault` on the four micronaut workloads.

**Evidence — metabase is dead code:** `grep -rn metabase` across the repo, excluding `helm/metabase/` itself, returns exactly **one** hit: `CLAUDE.md:14`. No `HelmRelease` references it, no Deployment exists. `helm/metabase/Chart.yaml` has `appVersion: "latest"` with `values.yaml` `tag: ""`, so it would render `metabase/metabase:latest` if anyone ever did use it.

**Evidence — micronaut:** `helm/micronaut/values.yaml:252-257` already sets `containerSecurityContext: {enabled: true, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities.drop: [ALL]}` and it is confirmed live on `otis-micronaut`. It is missing `seccompProfile`.

> **Decision gate 3 applies here.** Do **not** add `runAsNonRoot: true`. Verified live 2026-08-03: `kubectl exec` into all four micronaut Deployments (`otis`, `otis-dev`, `vulpes-backend`, `vulpes-backend-dev`) returns `uid=0(root)`. Adding `runAsNonRoot` would CrashLoopBackOff all four with *"container has runAsNonRoot and image will run as root"*. That needs a non-root `USER` in the application images (different repos) and is deliberately out of scope.

- [ ] **Step 1: Create the PR 4 branch and re-confirm metabase is unreferenced**

```bash
git checkout main
git pull --rebase origin main
git checkout -b chore/chart-housekeeping

grep -rn "metabase" --include='*.yaml' --include='*.yml' --include='*.sh' --include='*.md' . | grep -v "^./helm/metabase/"
kubectl get deploy,sts,helmrelease -A 2>/dev/null | grep -i metabase || echo "no metabase workload (expected)"
```

Expected: exactly one grep hit (`CLAUDE.md:14`); `no metabase workload (expected)`.

- [ ] **Step 2: Delete the chart**

```bash
git rm -r helm/metabase
```

- [ ] **Step 3: Update CLAUDE.md**

In `CLAUDE.md`, change line 14 from:

```
- `helm/` — in-repo Helm charts (`shlink`, `outline`, `leantime`, `metabase`, `micronaut`). `micronaut` is the generic chart reused by several Micronaut services (e.g. otis, vulpes).
```

to:

```
- `helm/` — in-repo Helm charts (`shlink`, `outline`, `leantime`, `micronaut`). `micronaut` is the generic chart reused by several Micronaut services (e.g. otis, vulpes).
```

- [ ] **Step 4: Add `seccompProfile` to micronaut**

In `helm/micronaut/values.yaml`, replace lines 252-257:

```yaml
containerSecurityContext:
  enabled: true
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
```

with:

```yaml
containerSecurityContext:
  enabled: true
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
  seccompProfile:
    type: RuntimeDefault
# NOTE: runAsNonRoot is deliberately NOT set. All four consumers (otis,
# otis-dev, vulpes-backend, vulpes-backend-dev) run as uid 0 today; adding it
# would CrashLoopBackOff every one of them. It needs a non-root USER in the
# application images first.
```

- [ ] **Step 5: Bump the chart version**

In `helm/micronaut/Chart.yaml`, change `version: 0.5.2` to `version: 0.5.3`. **Without this, Flux serves the cached chart and the change does nothing.**

- [ ] **Step 6: Render and verify**

```bash
helm lint helm/micronaut
helm template t helm/micronaut | grep -A 10 "securityContext:" | grep -A 2 seccompProfile
test ! -d helm/metabase && echo "metabase gone"
```

Expected: `helm lint` passes with 0 failures; the rendered container `securityContext` contains `seccompProfile: {type: RuntimeDefault}`; `metabase gone`.

- [ ] **Step 7: Validate**

Run: `./scripts/validate.sh` — expected exit `0` (nothing references metabase, so no Kustomize path breaks).

- [ ] **Step 8: Commit**

```bash
# `git rm -r helm/metabase` in Step 2 already staged the deletion and the path no
# longer exists in the worktree — stage the rest by name, do not re-add that path.
git add helm/micronaut CLAUDE.md
git status --short          # expect: D helm/metabase/*, M helm/micronaut/*, M CLAUDE.md
git commit -m "chore(helm): delete the dead metabase chart and add seccompprofile to micronaut"
```

- [ ] **Step 9: Push, open, merge and verify**

```bash
git pull --rebase origin main
git push -u origin chore/chart-housekeeping
gh pr create --title "chore(helm): delete dead metabase chart and harden micronaut seccomp" --body "$(cat <<'EOF'
## Summary
- helm/metabase deleted: referenced nowhere in apps/, infrastructure/ or clusters/, no workload exists, appVersion "latest"
- helm/micronaut: seccompProfile RuntimeDefault added to containerSecurityContext (chart 0.5.2 -> 0.5.3)
- runAsNonRoot deliberately NOT added: all four micronaut consumers run as uid 0 and would crashloop

Plan: docs/superpowers/plans/2026-08-03-workload-resilience-and-pod-hardening.md (Task 14)

## Test plan
- [x] ./scripts/validate.sh and helm lint pass
- [ ] After merge: otis/vulpes pods roll and stay Running with seccompProfile RuntimeDefault
EOF
)"
```

After merge:

```bash
# All four consumers (otis, otis-dev, vulpes-backend, vulpes-backend-dev) live in
# the `apps` layer and reference `chart: ./helm/micronaut` from the `helmcharts`
# GitRepository with NO pinned version — the Chart.yaml bump is what makes Flux
# re-resolve. Verified: apps/base/otis/release.yaml:10, apps/base/vulpes-backend/release.yaml:10.
flux reconcile kustomization apps --with-source
flux get helmreleases -A | grep -E "otis|vulpes"
kubectl get pods -n otis; kubectl get pods -n otis-dev; kubectl get pods -n vulpes; kubectl get pods -n vulpes-dev
kubectl get deploy -n otis otis-micronaut -o jsonpath='{.spec.template.spec.containers[0].securityContext}{"\n"}'
```

Expected: all four HelmReleases show the new chart revision `0.5.3` and `READY=True`; all `otis*`/`vulpes*` pods `Running` and ready; the container securityContext includes `"seccompProfile":{"type":"RuntimeDefault"}`.

**Gate:** do not start Task 15 until all four micronaut workloads are `Running`. **Rollback:** revert the merge commit (which restores `helm/metabase` and `Chart.yaml` `0.5.2`) and reconcile once.

---

## Task 15: Harden `helm/shlink` and drop its post-renderer (PR 5)

> ⚠️ **One chart at a time — watch this rollout complete before starting Task 16.** `drop: [ALL]` and `runAsNonRoot: true` can CrashLoopBackOff an app that binds a privileged port or has no non-root user.

**Files:**
- Modify: `helm/shlink/templates/deployment.yaml`
- Modify: `helm/shlink/values.yaml`
- Modify: `helm/shlink/Chart.yaml` (version `0.5.0` → `0.6.0`)
- Modify: `apps/clusters/feathre-core/base-apps/shlink/release.yaml`

**Interfaces:**
- Consumes: PR 4 merged and healthy (Task 14)
- Produces: a shlink chart that supports `priorityClassName` + `startupProbe` natively, with the JSON6902 post-renderer removed in the same commit.

**Evidence:**
1. `helm/shlink/templates/deployment.yaml` contains **zero** occurrences of `priorityClassName`, `startupProbe`, `topologySpreadConstraints` or `terminationGracePeriodSeconds` — it is an unmodified `helm create` scaffold. The cost is the JSON6902 post-renderer at `apps/clusters/feathre-core/base-apps/shlink/release.yaml:7-16` that exists purely to inject one field.
2. `helm/shlink/values.yaml:37` is `securityContext: {}` with the `helm create` hardening lines commented out. The template consumes `.Values.securityContext` as the **container** securityContext (`deployment.yaml:40-41`).
3. **New finding, verified live:** the overlay's probe timings at `release.yaml:46-63` are nested one level too deep — `initialDelaySeconds`/`timeoutSeconds`/`failureThreshold` sit *under* `httpGet:` instead of beside it, so the API server drops them. The running probe is `{"failureThreshold":3,"httpGet":{"path":"/rest/health","port":"http"},"periodSeconds":10,"successThreshold":1,"timeoutSeconds":1}` — the intended `timeoutSeconds: 5` / `failureThreshold: 5` / `initialDelaySeconds: 10` never took effect. This task fixes that.
4. Safe to harden: `kubectl exec -n shlink deploy/shlink -- id` → `uid=1001 gid=0(root)`, and the container port is `8080` (>1024), so `runAsNonRoot: true` and `drop: [ALL]` are both fine. `readOnlyRootFilesystem` stays `false` — shlink writes its GeoLite database and RoadRunner state to the rootfs.

> ⚠️ **`runAsNonRoot` hazard — read before Step 3.** shlink's live Deployment has
> an **empty** `securityContext` on both pod and container
> (`kubectl get deploy shlink -n shlink -o jsonpath='{.spec.template.spec.containers[0].securityContext}'` → `{}`),
> so uid 1001 comes purely from the image's `USER`. `runAsNonRoot: true` alone is
> only enforceable when the image declares a **numeric** `USER`; if
> `shlinkio/shlink:5.1.4` declares a *named* user, the kubelet refuses to start
> the container with `CreateContainerConfigError: container has runAsNonRoot and
> image has non-numeric user`, and **all three replicas fail to start** — the
> plan's only remedy would be a post-merge revert. Unlike leantime and outline,
> whose overlays already pin `runAsUser`, shlink pins nothing.
> **Therefore Step 3 also sets `runAsUser: 1001`,** which makes `runAsNonRoot`
> verifiable regardless of how the image declares its user. Do not drop that
> line.

- [ ] **Step 1: Create the PR 5 branch and confirm the premise**

```bash
git checkout main
git pull --rebase origin main
git checkout -b feat/shlink-chart-hardening

grep -c "priorityClassName\|startupProbe" helm/shlink/templates/deployment.yaml
kubectl exec -n shlink deploy/shlink -- id
kubectl get deploy shlink -n shlink -o jsonpath='{.spec.template.spec.containers[0].livenessProbe}{"\n"}'
```

Expected: `0`; `uid=1001 gid=0(root) groups=0(root)`; the probe shows `timeoutSeconds:1` and no `initialDelaySeconds`.

- [ ] **Step 2: Add `priorityClassName` + `startupProbe` to the template**

In `helm/shlink/templates/deployment.yaml`, after line 37 (the end of the pod-level `securityContext:` block), insert:

```yaml
      {{- with .Values.priorityClassName }}
      priorityClassName: {{ . }}
      {{- end }}
```

and immediately **before** `          livenessProbe:` (line 48), insert:

```yaml
          {{- if .Values.startupProbe.enabled }}
          startupProbe:
            httpGet:
              path: {{ .Values.startupProbe.path }}
              port: http
            failureThreshold: {{ .Values.startupProbe.failureThreshold }}
            periodSeconds: {{ .Values.startupProbe.periodSeconds }}
          {{- end }}
```

`startupProbe` gets its own explicit `path` rather than reusing `.Values.readinessProbe.httpGet.path` so an operator can switch readiness to a non-HTTP probe without breaking the template.

- [ ] **Step 3: Add the values**

In `helm/shlink/values.yaml`, add `priorityClassName: ""` near the top (immediately after the `replicaCount:` line, line 5), and replace **lines 37-43** — `securityContext: {}` *plus* the six commented-out `helm create` hint lines below it (`# capabilities:` … `# runAsUser: 1000`), so no stale hints are left sitting inside the new mapping — with:

```yaml
securityContext:
  allowPrivilegeEscalation: false
  runAsNonRoot: true
  # Pinned explicitly. runAsNonRoot alone is only enforceable when the image
  # declares a NUMERIC USER; a named USER makes the kubelet refuse the container
  # with "image has non-numeric user". The live pod runs uid=1001 with an empty
  # securityContext, i.e. purely from the image — so pin it here.
  runAsUser: 1001
  # Left false on purpose: shlink writes its GeoLite database and RoadRunner
  # state to the container filesystem, not to a mounted volume.
  readOnlyRootFilesystem: false
  capabilities:
    drop:
      - ALL
  seccompProfile:
    type: RuntimeDefault
```

and add, immediately after the existing `readinessProbe:` block:

```yaml
# Off by default. Enable for slow-booting deployments so the liveness probe is
# suspended until the app first answers, instead of SIGKILLing a cold start.
startupProbe:
  enabled: false
  path: /rest/health
  failureThreshold: 30
  periodSeconds: 10
```

- [ ] **Step 4: Bump the chart version**

`helm/shlink/Chart.yaml`: `version: 0.5.0` → `version: 0.6.0`.

- [ ] **Step 5: Replace the post-renderer with the chart value, and fix the probe indentation**

In `apps/clusters/feathre-core/base-apps/shlink/release.yaml`, delete lines 7-16 (the whole `postRenderers:` block) and replace lines 46-63 (the two malformed probes) so the file starts:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: shlink
  namespace: shlink
spec:
  values:
    # Chart 0.6.0 supports this natively; replaces the former JSON6902
    # post-renderer that existed only to inject this one field.
    priorityClassName: feather-standard
    affinity:
```

and the probe section becomes:

```yaml
    # NOTE: the previous version of this block nested initialDelaySeconds /
    # timeoutSeconds / failureThreshold *under* httpGet, so the API server
    # dropped them and the live probe ran at the Kubernetes defaults
    # (timeoutSeconds 1, failureThreshold 3, no initial delay).
    startupProbe:
      enabled: true
      path: /rest/health
      failureThreshold: 30
      periodSeconds: 10
    livenessProbe:
      httpGet:
        path: /rest/health
        port: http
      periodSeconds: 10
      timeoutSeconds: 5
      successThreshold: 1
      failureThreshold: 5
    readinessProbe:
      httpGet:
        path: /rest/health
        port: http
      periodSeconds: 10
      timeoutSeconds: 5
      successThreshold: 1
      failureThreshold: 5
```

(`initialDelaySeconds` is dropped because the `startupProbe` now covers cold start.) Everything else in the file — `image`, `replicaCount`, `ingress`, `httpRoute`, `service`, `podLabels`, `podAnnotations`, `resources`, `autoscaling`, `env` — is unchanged.

**Both halves must be in the same commit.** Removing the post-renderer without the chart value in the same chart version briefly drops `priorityClassName`.

- [ ] **Step 6: Render and verify**

```bash
helm lint helm/shlink
helm template t helm/shlink --set priorityClassName=feather-standard --set startupProbe.enabled=true \
  | grep -E "priorityClassName|startupProbe|runAsNonRoot|runAsUser|drop|seccompProfile|timeoutSeconds"
kubectl kustomize apps/clusters/feathre-core/base-apps/shlink | grep -c "postRenderers" || echo "0 postRenderers (expected)"
kubectl kustomize apps/clusters/feathre-core/base-apps/shlink | grep -A 3 "priorityClassName"
grep -n "^version:" helm/shlink/Chart.yaml
```

Expected: lint passes; the rendered Deployment has `priorityClassName: feather-standard`, a `startupProbe` on `/rest/health`, and the container securityContext with `runAsNonRoot: true`, `runAsUser: 1001`, `drop: [ALL]`, `seccompProfile`; `0 postRenderers (expected)`; `version: 0.6.0`.

- [ ] **Step 7: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 8: Commit**

```bash
git add helm/shlink apps/clusters/feathre-core/base-apps/shlink/release.yaml
git commit -m "feat(shlink): add priorityclassname, startupprobe and container hardening to the chart"
```

- [ ] **Step 9: Push, open, merge, then watch the rollout**

```bash
git pull --rebase origin main
git push -u origin feat/shlink-chart-hardening
gh pr create --title "feat(shlink): harden the in-repo chart and drop the priorityclass post-renderer" --body "$(cat <<'EOF'
## Summary
- helm/shlink 0.5.0 -> 0.6.0: priorityClassName + startupProbe template support, populated securityContext (allowPrivilegeEscalation false, runAsNonRoot + explicit runAsUser 1001, drop ALL, seccompProfile RuntimeDefault; readOnlyRootFilesystem stays false — shlink writes to its rootfs)
- overlay: JSON6902 priorityClassName post-renderer removed in the same commit
- overlay: probe timings were nested under httpGet and silently dropped by the API server; fixed, and a startupProbe added

Plan: docs/superpowers/plans/2026-08-03-workload-resilience-and-pod-hardening.md (Task 15)

## Test plan
- [x] ./scripts/validate.sh + helm lint pass
- [ ] After merge: 3/3 shlink pods Running with the new securityContext, 1lf.link still redirects
EOF
)"
```

After merge:

```bash
flux reconcile kustomization base-apps --with-source
kubectl -n shlink rollout status deploy/shlink --timeout=5m
kubectl get pods -n shlink -o wide
kubectl get deploy shlink -n shlink -o jsonpath='{.spec.template.spec.priorityClassName}{"\n"}{.spec.template.spec.containers[0].securityContext}{"\n"}{.spec.template.spec.containers[0].livenessProbe}{"\n"}'
```

Expected: rollout completes; 3/3 `Running`; `feather-standard`; the securityContext has all six fields (`allowPrivilegeEscalation`, `runAsNonRoot`, `runAsUser`, `readOnlyRootFilesystem`, `capabilities.drop`, `seccompProfile`); the liveness probe now shows `timeoutSeconds: 5` and `failureThreshold: 5`.

**If the new pod is `CreateContainerConfigError`** — check `kubectl -n shlink describe pod <new-pod>`. `container has runAsNonRoot and image has non-numeric user` means the `runAsUser: 1001` line was dropped; `permission denied` on the GeoLite path means `drop: [ALL]` is the culprit. In both cases: revert the merge commit and reconcile `base-apps` once. The old ReplicaSet's 3 pods stay `Running` throughout (rolling update blocks on the new pod becoming ready), so `1lf.link` does not go down while you do it.

- [ ] **Step 10: Confirm the app still works**

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://1lf.link/
kubectl -n shlink logs deploy/shlink --tail=30 | grep -iE "permission denied|operation not permitted" || echo "no capability errors"
```

Expected: an HTTP status line (2xx/3xx/404 — not a connection error); `no capability errors`.

**Gate:** do not start Task 16 until the shlink rollout is complete and `1lf.link` answers.

**Rollback:** revert the merge commit. This restores `Chart.yaml` `0.5.0`, so Flux resolves the previous chart artifact and the post-renderer comes back with it.

---

## Task 16: Harden `helm/leantime` and drop its post-renderer (PR 6)

> ⚠️ **One chart at a time — Task 15's rollout must be complete first.**
> ⚠️ **Decision gate 1 applies to Step 4 (probe path).**

**Files:**
- Modify: `helm/leantime/templates/deployment.yaml`
- Modify: `helm/leantime/values.yaml`
- Modify: `helm/leantime/Chart.yaml` (version `0.2.0` → `0.3.0`)
- Modify: `apps/clusters/feathre-core/base-apps/leantime/release.yaml`

**Interfaces:**
- Consumes: shlink rolled out cleanly (Task 15)
- Produces: the same chart capabilities for leantime, plus probe timings that are not Kubernetes' defaults.

**Evidence:**
1. `helm/leantime/templates/deployment.yaml` has **zero** occurrences of `priorityClassName`/`startupProbe`/`topologySpreadConstraints`/`terminationGracePeriodSeconds`; the overlay carries a JSON6902 post-renderer at `release.yaml:7-16` for the one field.
2. `helm/leantime/values.yaml:34` is `securityContext: {}`; `helm/leantime/values.yaml:103-110` defines probes with only `httpGet.path`/`port`, so the rendered probes take the Kubernetes defaults — confirmed live: `{"failureThreshold":3,"periodSeconds":10,"timeoutSeconds":1}` with **no** `initialDelaySeconds` (i.e. 0). A liveness probe with 0s delay / 1s timeout / 3 failures SIGKILLs the container 30 s after start if it is not yet serving; leantime is PHP/Laravel and boots + migrates. It survives today because it boots fast enough — that is luck, not policy.
3. `Warning ProbeWarning ... Probe terminated redirects` has fired 192 169 times in 22 days, because `/` returns `303 → https://leantime.onelitefeather.net/install` (verified live; the kubelet counts 3xx as probe success but logs the warning).
4. Safe to harden: `kubectl exec -n leantime deploy/leantime -- id` → `uid=1000(www-data)`, container port `8080`. The overlay already sets `readOnlyRootFilesystem: false` + `runAsUser: 1000`, which deep-merges over the new chart defaults.

**Note on the 10 exit-137 restarts:** the audit could **not** attribute those to the probe. The events expired 22 days ago, the reason is `Error` not `OOMKilled`, and this cluster has a separately documented containerd 2.2.4 shim delete-hang that produces the identical SIGKILL signature. The probe shape is a forward-looking risk, not a demonstrated cause — do not write the causal claim into the commit message.

- [ ] **Step 1: Create the PR 6 branch and confirm the premise**

```bash
git checkout main
git pull --rebase origin main
git checkout -b feat/leantime-chart-hardening

grep -c "priorityClassName\|startupProbe" helm/leantime/templates/deployment.yaml
kubectl exec -n leantime deploy/leantime -- id
kubectl get deploy leantime -n leantime -o jsonpath='{.spec.template.spec.containers[0].livenessProbe}{"\n"}'
```

Expected: `0`; `uid=1000(www-data)`; probe with `timeoutSeconds:1`, no `initialDelaySeconds`.

- [ ] **Step 2: Template changes** — identical to Task 15 Step 2, applied to `helm/leantime/templates/deployment.yaml`:

after line 32 (end of the pod-level `securityContext:` block) insert:

```yaml
      {{- with .Values.priorityClassName }}
      priorityClassName: {{ . }}
      {{- end }}
```

and before `          livenessProbe:` (line 43) insert:

```yaml
          {{- if .Values.startupProbe.enabled }}
          startupProbe:
            httpGet:
              path: {{ .Values.startupProbe.path }}
              port: http
            failureThreshold: {{ .Values.startupProbe.failureThreshold }}
            periodSeconds: {{ .Values.startupProbe.periodSeconds }}
          {{- end }}
```

- [ ] **Step 3: Values — `priorityClassName` and `securityContext`**

In `helm/leantime/values.yaml`, add `priorityClassName: ""` immediately after the `replicaCount:` line (line 5), and replace **lines 34-40** — `securityContext: {}` *plus* the six commented-out `helm create` hint lines below it (`# capabilities:` … `# runAsUser: 1000`) — with:

```yaml
securityContext:
  allowPrivilegeEscalation: false
  runAsNonRoot: true
  # Left false on purpose: Leantime (PHP/Laravel) writes cache, sessions and
  # uploads to the container filesystem. The overlay also pins this to false.
  readOnlyRootFilesystem: false
  capabilities:
    drop:
      - ALL
  seccompProfile:
    type: RuntimeDefault
```

- [ ] **Step 4: Values — probe timings and path** ⚠️ **DECISION GATE 1**

Verified live (`curl` from inside the cluster against `http://leantime.leantime.svc:8080`):

| Path | Code | Notes |
|---|---|---|
| `/` | 303 | → `https://leantime.onelitefeather.net/install`; probe success, but logs `ProbeWarning` |
| `/install` | 200 | HTML rendered by PHP (`X-Powered-By: PHP/8.3.30`) — exercises PHP-FPM + framework boot |
| `/robots.txt`, `/favicon.ico` | 200 | served statically by nginx — does **not** exercise PHP |
| `/api/jsonrpc`, `/auth/login`, `/health` | 303 | all redirect |

**Recommendation: `/install` for the *readiness* probe only; keep `/` for *liveness*.** `/install` is the only 200 that actually runs PHP, but a **liveness** probe pointed at an install-wizard path is a standing risk: the moment that route stops returning 2xx/3xx — a Leantime version bump that removes it, or a setup state change — the kubelet SIGKILLs the container in a loop, and the ProbeWarning noise this is meant to fix is cosmetic by comparison. A readiness failure only removes the pod from its Service; on a single-replica Deployment that is already recoverable and self-announcing.

Options, pick one before writing the value:
- **(a) recommended)** liveness `/`, readiness `/install`. Keeps the crash-safety of a redirect-tolerant liveness path, gets a real PHP check on readiness. `ProbeWarning` volume roughly halves rather than going to zero.
- **(b)** both on `/install`. ProbeWarnings go to zero; accepts the liveness risk above.
- **(c)** keep both on `/` and accept the 192 k `ProbeWarning` events.

Replace `helm/leantime/values.yaml:103-110` (verified at `ac16018`: `livenessProbe:` is line 103 and the `readinessProbe:` block's last line — `    port: http` — is line **110**; replacing only 103-109 leaves that line dangling and produces invalid YAML) with (substituting the chosen paths):

```yaml
livenessProbe:
  httpGet:
    # Option (a): `/` 303-redirects to /install and the kubelet counts 3xx as
    # success. Deliberately NOT /install here — a liveness probe bound to an
    # install-wizard route SIGKILLs the container the day that route stops
    # answering 2xx/3xx. For option (b), change this to /install too.
    path: /
    port: http
  periodSeconds: 10
  # Kubernetes' default is 1s. A PHP-FPM worker under load routinely exceeds
  # that, and three consecutive misses SIGKILL the container.
  timeoutSeconds: 5
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: /install
    port: http
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3

# Suspends liveness until the app first answers, so a cold boot (Laravel
# bootstrap + migrations) cannot be killed by the liveness probe.
startupProbe:
  enabled: false
  path: /install
  failureThreshold: 30
  periodSeconds: 10
```

- [ ] **Step 5: Bump the chart version**

`helm/leantime/Chart.yaml`: `version: 0.2.0` → `version: 0.3.0`.

- [ ] **Step 6: Overlay — drop the post-renderer, add the value, enable the startup probe**

Rewrite `apps/clusters/feathre-core/base-apps/leantime/release.yaml`:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: leantime
  namespace: leantime
spec:
  values:
    # Chart 0.3.0 supports this natively; replaces the former JSON6902
    # post-renderer that existed only to inject this one field.
    priorityClassName: feather-standard
    podLabels:
      logs.onelitefeather.net/env: prod
    resources:
      limits:
        cpu: 2
        memory: 2Gi
      requests:
        cpu: 1
        memory: 1Gi
    securityContext:
      readOnlyRootFilesystem: false
      runAsUser: 1000
    startupProbe:
      enabled: true
    envFrom:
      - secretRef:
          name: leantime-env

    replicaCount: 1
    ingress:
      enabled: false
    # Exposed via Cloudflare Tunnel (see ingress.yaml); chart HTTPRoute off.
    httpRoute:
      enabled: false
```

`securityContext` here deep-merges over the chart defaults from Step 3, so the result is `allowPrivilegeEscalation: false`, `runAsNonRoot: true`, `drop: [ALL]`, `seccompProfile: RuntimeDefault`, `readOnlyRootFilesystem: false`, `runAsUser: 1000`.

- [ ] **Step 7: Render and verify**

```bash
helm lint helm/leantime
helm template t helm/leantime --set priorityClassName=feather-standard --set startupProbe.enabled=true \
  --set securityContext.readOnlyRootFilesystem=false --set securityContext.runAsUser=1000 \
  | grep -E "priorityClassName|startupProbe|runAsNonRoot|runAsUser|drop|seccompProfile|timeoutSeconds|path:"
kubectl kustomize apps/clusters/feathre-core/base-apps/leantime | grep -c "postRenderers" || echo "0 postRenderers (expected)"
grep -n "^version:" helm/leantime/Chart.yaml
```

Expected: lint passes; the rendered container securityContext has all six fields and `runAsNonRoot: true` coexists with `runAsUser: 1000`; `startupProbe` present; `timeoutSeconds: 5` on liveness and readiness; `0 postRenderers (expected)`; `version: 0.3.0`.

- [ ] **Step 8: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 9: Commit, push, open, merge, watch**

```bash
git add helm/leantime apps/clusters/feathre-core/base-apps/leantime/release.yaml
git commit -m "feat(leantime): add priorityclassname, startupprobe and container hardening to the chart"
git pull --rebase origin main
git push -u origin feat/leantime-chart-hardening
gh pr create --title "feat(leantime): harden the in-repo chart and fix the default probe timings" --body "$(cat <<'EOF'
## Summary
- helm/leantime 0.2.0 -> 0.3.0: priorityClassName + startupProbe template support, populated securityContext, probe timeoutSeconds 5 (was the Kubernetes default of 1) and readiness moved to /install (a real PHP-rendered 200); liveness deliberately stays on / so an install-wizard route change cannot SIGKILL the container
- overlay: JSON6902 priorityClassName post-renderer removed in the same commit; startupProbe enabled

Plan: docs/superpowers/plans/2026-08-03-workload-resilience-and-pod-hardening.md (Task 16)

## Test plan
- [x] ./scripts/validate.sh + helm lint pass
- [ ] After merge: leantime pod Running and Ready, the app loads, `Probe terminated redirects` volume down as expected for the chosen option
EOF
)"
```

After merge:

```bash
flux reconcile kustomization base-apps --with-source
kubectl -n leantime rollout status deploy/leantime --timeout=5m
kubectl get deploy leantime -n leantime -o jsonpath='{.spec.template.spec.priorityClassName}{"\n"}{.spec.template.spec.containers[0].securityContext}{"\n"}{.spec.template.spec.containers[0].startupProbe}{"\n"}'
kubectl -n leantime get events --sort-by=.lastTimestamp | grep -i probewarning | tail -5 || echo "no new ProbeWarning"
```

Expected: rollout completes; `feather-standard`; the securityContext has all six fields; a `startupProbe` on the chosen path. `ProbeWarning ... Probe terminated redirects` disappears entirely under option (b), and roughly halves under option (a) (readiness stops redirecting, liveness still does). Under option (c) it is unchanged and that is expected.

- [ ] **Step 10: Confirm the app works**

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://leantime.onelitefeather.net/
kubectl -n leantime logs deploy/leantime --tail=40 | grep -iE "permission denied|operation not permitted" || echo "no capability errors"
```

Expected: 2xx/3xx; `no capability errors`.

**Gate:** do not start Task 17 until leantime is Running and the site answers.

**Rollback:** revert the merge commit — `Chart.yaml` returns to `0.2.0` and Flux resolves the previous artifact together with its post-renderer.

---

## Task 17: Harden `helm/outline` (PR 7)

> ⚠️ **One chart at a time — Task 16's rollout must be complete first.**

**Files:**
- Modify: `helm/outline/values.yaml:230`
- Modify: `helm/outline/Chart.yaml` (version `0.5.1` → `0.5.2`)

**Interfaces:**
- Consumes: leantime rolled out cleanly (Task 16)
- Produces: `outline-web` and `outline-collaboration` containers with a populated securityContext.

**Evidence:** `helm/outline/values.yaml:230` is `securityContext: {}`, consumed by `helm/outline/templates/deployment.yaml:47-49` as the **container** securityContext. The overlay at `apps/clusters/feathre-core/base-apps/outline/release.yaml:33-35` sets only `readOnlyRootFilesystem: false` + `runAsUser: 1000` — no capability drop, no `allowPrivilegeEscalation: false`. Safe: `kubectl exec -n outline deploy/outline-web -- id` → `uid=1000(node)`, container port `3000`.

Outline already supports `priorityClassName` (`helm/outline/templates/deployment.yaml:39-40`) and the overlay uses it (`release.yaml:20`), so there is no post-renderer to remove and no template change needed — values only.

- [ ] **Step 1: Create the PR 7 branch and confirm the premise**

```bash
git checkout main
git pull --rebase origin main
git checkout -b feat/outline-chart-hardening

sed -n '228,237p' helm/outline/values.yaml
kubectl exec -n outline deploy/outline-web -- id
```

Expected: `securityContext: {}` with commented-out `helm create` hints; `uid=1000(node)`.

- [ ] **Step 2: Populate the securityContext**

In `helm/outline/values.yaml`, replace lines 230-236 (`securityContext: {}` plus the commented block) with:

```yaml
securityContext:
  allowPrivilegeEscalation: false
  runAsNonRoot: true
  # Left false on purpose: Outline writes to its rootfs (Node build cache,
  # temp files). The overlay also pins this to false.
  readOnlyRootFilesystem: false
  capabilities:
    drop:
      - ALL
  seccompProfile:
    type: RuntimeDefault
```

The overlay's `securityContext: {readOnlyRootFilesystem: false, runAsUser: 1000}` deep-merges on top; the result carries all six fields.

- [ ] **Step 3: Bump the chart version**

`helm/outline/Chart.yaml`: `version: 0.5.1` → `version: 0.5.2`.

- [ ] **Step 4: Render and verify**

```bash
helm lint helm/outline
helm template t helm/outline --set securityContext.readOnlyRootFilesystem=false --set securityContext.runAsUser=1000 \
  | grep -E "runAsNonRoot|runAsUser|allowPrivilegeEscalation|drop|seccompProfile"
grep -n "^version:" helm/outline/Chart.yaml
```

Expected: lint passes; all five hardening fields plus `runAsUser: 1000` rendered on both component Deployments; `version: 0.5.2`.

- [ ] **Step 5: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 6: Commit, push, open, merge, watch**

```bash
git add helm/outline
git commit -m "feat(outline): populate the chart container securitycontext"
git pull --rebase origin main
git push -u origin feat/outline-chart-hardening
gh pr create --title "feat(outline): populate the chart container securitycontext" --body "$(cat <<'EOF'
## Summary
- helm/outline 0.5.1 -> 0.5.2: securityContext defaults (allowPrivilegeEscalation false, runAsNonRoot, drop ALL, seccompProfile RuntimeDefault). readOnlyRootFilesystem stays false — Outline writes to its rootfs, and the overlay already pins it.

Plan: docs/superpowers/plans/2026-08-03-workload-resilience-and-pod-hardening.md (Task 17)

## Test plan
- [x] ./scripts/validate.sh + helm lint pass
- [ ] After merge: outline-web and outline-collaboration roll cleanly, docs load and realtime editing works
EOF
)"
```

After merge:

```bash
flux reconcile kustomization base-apps --with-source
kubectl -n outline rollout status deploy/outline-web --timeout=5m
kubectl -n outline rollout status deploy/outline-collaboration --timeout=5m
kubectl get deploy -n outline -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].securityContext}{"\n"}{end}'
flux get helmreleases -n outline
```

Expected: both rollouts complete; both containers show all six fields; the HelmRelease shows revision `0.5.2` and `READY=True`.

- [ ] **Step 7: Confirm the app works**

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://docs.onelitefeather.net/ 2>/dev/null || echo "check the actual outline hostname in apps/clusters/feathre-core/base-apps/outline/ingress.yaml"
kubectl -n outline logs deploy/outline-web --tail=40 | grep -iE "permission denied|EACCES|operation not permitted" || echo "no capability errors"
```

Expected: an HTTP status line; `no capability errors`. Also open a document in the browser and confirm live collaborative editing still connects (that is the `outline-collaboration` path, which the smoke curl does not cover).

- [ ] **Step 8: Confirm no PDB orphans reappeared**

```bash
kubectl get pdb -n outline
```

Expected: `No resources found` (Task 1's cleanup held across a chart upgrade — this is the pruning check the audit asked for).

**Rollback:** revert the merge commit; `Chart.yaml` returns to `0.5.1`.

---

## Task 18: Resolve the `n8n-main` PDB (PR 8)

> ⚠️ **Decision gate 2 — get a human answer before executing.**

**Files (option A, recommended):**
- Modify: `apps/clusters/feathre-core/base-apps/n8n/release.yaml`

**Files (option B):**
- Modify: `apps/clusters/feathre-core/base-apps/n8n/release.yaml` (comment only)

**Interfaces:**
- Consumes: PR 7 merged (Task 17) — **sequencing convention only.** This task shares no file, layer or chart with Tasks 5-17, so if theme 9's drain campaign is scheduled first, run this PR immediately after Task 1 and resume the plan at PR 1 afterwards. It must land **before theme 9's rolling drain** either way.
- Produces: either no `n8n-main` PDB, or an explicit in-repo record that its node needs a force-drain.

**Evidence:** `kubectl get pdb n8n-main -n n8n -o json` → `minAvailable: 1`, `currentHealthy: 1`, `desiredHealthy: 1`, `expectedPods: 1`, `disruptionsAllowed: 0`, condition `DisruptionAllowed=False/InsufficientPods` since 2026-07-17. The PDB is chart-generated (`helm.sh/chart: n8n-1.11.0_a0bf4694f6e0`), not repo-authored, and nothing in the repo mentions it. The replica count is at `release.yaml:16` with an existing comment explaining that `multiMain` needs an enterprise licence.

Verified against the chart source (`oci://ghcr.io/n8n-io/n8n-helm-chart/n8n` 1.11.0, `templates/pdb.yaml`): the chart's `pdb` block is top-level (`pdb: {enabled: true, minAvailable: 1}`) and creates a PDB for the **`main` component only** — there is no worker or webhook PDB to lose. `kubectl get pdb -n n8n` confirms `n8n-main` is the only one.

**Honest framing:** `n8n-main` is one of four PDBs sitting at `disruptionsAllowed: 0` (with `harbor-registry`, the CNPG primary and — until Task 1 — the two outline orphans), only one of which is documented. So "some PDBs need a force" is an established operational pattern here, not a novel trap. It is recoverable by cordon + force-delete with no data loss (n8n-main's state is in external Postgres and Dragonfly, `release.yaml:29-53`). The Talos-specific claim that the drain "stalls silently" was **not** verifiable read-only.

**Option A (recommended) — disable the chart PDB:**

- [ ] **Step A1: Create the branch**

```bash
git checkout main
git pull --rebase origin main
git checkout -b fix/n8n-pdb-blocks-drain
```

- [ ] **Step A2: Add the value**

In `apps/clusters/feathre-core/base-apps/n8n/release.yaml`, add at the top of `spec.values` (immediately after `  values:`):

```yaml
    # The chart creates a PDB for the `main` component only, with
    # minAvailable: 1 against replicaCount: 1 below — permanently
    # disruptionsAllowed: 0, so the eviction API 429s forever and a Talos node
    # drain hangs until someone cordons + force-deletes. A 1-replica PDB cannot
    # protect anything anyway; n8n-main's state is in external Postgres +
    # Dragonfly and n8n-worker x2 keeps executing queued jobs across the gap.
    pdb:
      enabled: false
```

- [ ] **Step A3: Render, validate, commit**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/n8n | grep -A 2 "pdb:"
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/n8n/release.yaml
git commit -m "fix(n8n): disable the single-replica main pdb that blocks node drains"
```

Expected: the rendered values contain `pdb: {enabled: false}`; validate exits `0`.

- [ ] **Step A4: Push, open, merge, verify**

```bash
git pull --rebase origin main
git push -u origin fix/n8n-pdb-blocks-drain
gh pr create --title "fix(n8n): disable the single-replica main pdb that blocks node drains" --body "$(cat <<'EOF'
## Summary
- n8n's chart PDB is minAvailable:1 against replicaCount:1, so disruptionsAllowed has been 0 since 2026-07-17 and any eviction-API drain of its node hangs
- The chart only creates a PDB for the `main` component, so nothing else is affected
- n8n-main's state is external (Postgres + Dragonfly); n8n-worker x2 keeps executing queued jobs

Plan: docs/superpowers/plans/2026-08-03-workload-resilience-and-pod-hardening.md (Task 18)

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] After merge: `kubectl get pdb -n n8n` empty, n8n still Running, workflows still execute
EOF
)"
```

After merge:

```bash
flux reconcile kustomization base-apps --with-source
kubectl get pdb -n n8n
kubectl get pods -n n8n
kubectl get pdb -A -o json | jq -r '.items[] | select(.status.disruptionsAllowed==0) | "\(.metadata.namespace)/\(.metadata.name)"'
```

Expected: `No resources found in n8n namespace.`; all n8n pods `Running`; the last command lists exactly two — `cnpg-system/feather-core-cluster-pg-primary` and `harbor/harbor-registry`.

**If `n8n-main` is still listed after the HelmRelease reports `READY=True`:** helm-controller failed to prune it — the *exact* failure mode Task 1 is cleaning up for outline. Confirm the release no longer declares it, then delete it by hand and re-check:

```bash
flux get helmreleases -n n8n                       # must be READY=True first
helm get manifest n8n -n n8n | grep -c PodDisruptionBudget   # expect 0
kubectl delete pdb n8n-main -n n8n
kubectl get pdb -n n8n
```

Expected: `0`; `poddisruptionbudget.policy "n8n-main" deleted`; `No resources found in n8n namespace.` Do **not** delete it before the HelmRelease is Ready at the new values — otherwise Helm recreates it on the next upgrade and you have learned nothing.

**Option B — keep it and document it (only if the human picks B):** add the same comment block to `release.yaml` but **without** `pdb: {enabled: false}`, phrased as the harbor precedent does (`apps/clusters/feathre-core/base-apps/harbor/pdb.yaml:20-23`), and record it in Task 19's doc. Commit message: `docs(n8n): document that draining the main pod's node needs a force`.

**Rollback (option A):** revert the merge commit; the chart recreates the PDB on the next upgrade.

---

## Task 19: Document the remaining `disruptionsAllowed: 0` PDBs before theme 9's drain (PR 8, commit 2)

**Files:**
- Create: `docs/node-drain-pdb-notes.md`

**Interfaces:**
- Consumes: Task 1 (orphans deleted) and Task 18 (n8n resolved) — the doc must describe the *post-cleanup* set.
- Produces: the single reference the theme-9 operator reads before draining a node. **This must exist before theme 9 starts.**

- [ ] **Step 1: Regenerate the live set**

```bash
kubectl get pdb -A -o json | jq -r '.items[] | select(.status.disruptionsAllowed==0) | "\(.metadata.namespace)/\(.metadata.name) minAvailable=\(.spec.minAvailable) maxUnavailable=\(.spec.maxUnavailable) currentHealthy=\(.status.currentHealthy) expectedPods=\(.status.expectedPods) owner=\(.metadata.labels["app.kubernetes.io/managed-by"] // "repo")"'
```

Expected after Tasks 1 and 18-A: exactly two rows — `cnpg-system/feather-core-cluster-pg-primary` and `harbor/harbor-registry`.

- [ ] **Step 2: Write the doc**

Create `docs/node-drain-pdb-notes.md`:

```markdown
# Node drains and PodDisruptionBudgets

`talosctl upgrade` cordons and drains a node through the Kubernetes eviction
API. A PodDisruptionBudget with `disruptionsAllowed: 0` makes that API return
429 forever, so the drain never completes. This file lists every such PDB on
`feather-core` and what to do about it. Regenerate the list with:

```bash
kubectl get pdb -A -o json | jq -r '.items[] | select(.status.disruptionsAllowed==0) | "\(.metadata.namespace)/\(.metadata.name)"'
```

## Standing exceptions (as of 2026-08-03)

| PDB | Why it is 0 | What to do during a drain |
|---|---|---|
| `harbor/harbor-registry` | Deliberate. Single replica; chunked uploads are session-bound to one pod. Documented at `apps/clusters/feathre-core/base-apps/harbor/pdb.yaml:20-23`. | Cordon the node, then `kubectl delete pod -n harbor <registry-pod> --force`. Pushes in flight fail and must be retried. |
| `cnpg-system/feather-core-cluster-pg-primary` | CNPG creates this for the primary. `nodeMaintenanceWindow` is `None`. | Trigger a CNPG switchover first (`kubectl cnpg promote feather-core-cluster-pg <a-replica>`), then drain — do **not** force-delete a Postgres primary. |

## Resolved

- `outline/outline-worker`, `outline/outline-websockets` — orphaned PDBs left
  behind by a partial upgrade from chart `outline-0.4.0` to `0.5.1`; matched
  zero pods and fired `KubePdbNotEnoughHealthyPods` continuously for 46 days.
  Deleted 2026-08-03. If they ever reappear, helm-controller failed to prune
  again — check the outline HelmRelease before deleting them a second time.
- `n8n/n8n-main` — chart-generated, `minAvailable: 1` against
  `replicaCount: 1`. Disabled via `pdb.enabled: false` in
  `apps/clusters/feathre-core/base-apps/n8n/release.yaml`.

## Check before every drain campaign

Chart bumps reintroduce this shape silently. Run the `jq` command above before
starting a rolling node upgrade and reconcile the output against this table.
```

If decision gate 2 resolved to option B, move `n8n/n8n-main` from "Resolved" into the standing-exceptions table with the force-drain instruction instead.

- [ ] **Step 3: Commit**

```bash
git add docs/node-drain-pdb-notes.md
git commit -m "docs: list the pdbs that block a talos node drain"
```

(This commit rides on the Task 18 branch; if that branch is already merged, open a small follow-up PR titled `docs: list the pdbs that block a talos node drain`.)

**Rollback:** n/a — documentation only.

---

## Task 20: Tighten Mimir ingester/store-gateway spreading (PR 9)

> ⚠️ **This is the highest-risk change in the plan and is deliberately last.** It restarts every Mimir ingester and store-gateway (StatefulSets with Ceph RBD PVCs). It also lands on a HelmRelease that is **currently not Ready** — see Step 1.
> ⚠️ **Coordinate with theme 9.** `DoNotSchedule` makes node maintenance stricter. Either land this *after* theme 9's drain campaign, or complete Step 7 (single-worker drain rehearsal) before theme 9 starts.

**Files:**
- Modify: `apps/clusters/feathre-core/monitoring/mimir/release.yaml` (`ingester:` block at 165-188, `store_gateway:` block at 312-335)
- Modify: `apps/base/mimir/release.yaml` (add `timeout:`)

**Interfaces:**
- Consumes: everything above merged and healthy
- Produces: hostname-keyed `DoNotSchedule` spreading on the ingesters and store-gateways, and a Mimir HelmRelease that can actually reach `Ready`.

**Evidence:** `kubectl get sts -n grafana mimir-ingester -o jsonpath='{...affinity}'` → nodeAffinity on `topology.kubernetes.io/zone in [fr01]` and nothing else; its `topologySpreadConstraints` → `maxSkew: 1, topologyKey: kubernetes.io/hostname, whenUnsatisfiable: ScheduleAnyway`. Same for `mimir-store-gateway`. `zoneAwareReplication.enabled: false` (`release.yaml:187-188`, `:334-335`), so there is no zone-based spreading either. Self-healing is blocked twice over: `infrastructure/base/controllers/descheduler/release.yaml:56-57` sets `priorityThreshold: {name: feather-high}` and `:62-63` `podProtections.extraEnabled: [PodsWithPVC]`, while the Mimir postRenderer (`monitoring/mimir/release.yaml:17-23`) stamps `feather-high` on every Mimir StatefulSet — so the `RemoveDuplicates` plugin can never touch them, and `RemovePodsViolatingTopologySpreadConstraint` only acts on `DoNotSchedule` constraints anyway. With `replication_factor` 3 across 3 ingesters on 4 workers, `ScheduleAnyway` permits 2-3 ingesters to land on one node after a rolling upgrade, and once co-located they stay co-located forever. They happen to be spread today (`ingester-0` on xl-03, `-1` on xl-02, `-2` on xl-01) — that is luck, not policy.

Chart support verified against `mimir-distributed` 6.1.0: `ingester.topologySpreadConstraints` and `store_gateway.topologySpreadConstraints` are dicts defaulting to `{maxSkew: 1, topologyKey: kubernetes.io/hostname, whenUnsatisfiable: ScheduleAnyway}`; only the policy needs changing. The chart's own comment recommends exactly this.

**Pre-existing condition you must handle:** `flux get helmreleases -n grafana` shows `mimir` `READY=False`, `Stalled=True`, `RetriesExceeded`: *"Helm upgrade failed ... timeout waiting for: [StatefulSet/grafana/mimir-compactor status: 'InProgress', ...]"* from 19 days ago. All the StatefulSets are in fact converged (`currentRevision == updateRevision`, all `readyReplicas` at desired) — the release timed out, it did not fail. `apps/base/mimir/release.yaml` has **no** `upgrade.timeout`, so it uses the 5 m default, which is too short for a coordinated Mimir rollout. Raising it is part of this task; without it, your change re-stalls the same way.

- [ ] **Step 1: Create the branch and record the starting state**

```bash
git checkout main
git pull --rebase origin main
git checkout -b fix/mimir-ingester-spreading

flux get helmreleases -n grafana | grep mimir
kubectl get pods -n grafana -o wide | grep -E "mimir-(ingester|store-gateway)"
kubectl get sts -n grafana -o custom-columns=N:.metadata.name,READY:.status.readyReplicas,CUR:.status.currentRevision,UPD:.status.updateRevision | grep mimir
kubectl get pdb -n grafana | grep -E "mimir-ingester|mimir-store-gateway"
```

Expected: `mimir` `READY=False` with the stalled-upgrade message (pre-existing); 3 ingesters on 3 distinct nodes and 2 store-gateways on 2 distinct nodes, all `Running`; every mimir StatefulSet with `CUR == UPD`; both PDBs at `maxUnavailable: 1`.

**If any ingester is Pending or the StatefulSets are not converged, stop.** Fix that first — do not add a stricter scheduling constraint to a Mimir that is already unsettled.

- [ ] **Step 2: Raise the HelmRelease timeout**

In `apps/base/mimir/release.yaml`, replace lines 15-18:

```yaml
  install:
    remediation:
      retries: 0
  interval: 1m0s
```

with:

```yaml
  install:
    remediation:
      retries: 0
  upgrade:
    remediation:
      retries: 0
  # A coordinated Mimir rollout (ingesters + store-gateways + compactor, all
  # StatefulSets with PVCs) does not finish inside helm-controller's 5m
  # default — the release has been Stalled/RetriesExceeded on exactly that
  # timeout since 2026-07-15 while every StatefulSet was in fact converged.
  # Loki already uses 20m for the same reason.
  timeout: 20m0s
  interval: 1m0s
```

- [ ] **Step 3: Flip the ingester TSC to `DoNotSchedule`**

In `apps/clusters/feathre-core/monitoring/mimir/release.yaml`, inside the `ingester:` block, add after line 182 (the end of the `affinity:` block, before `      persistentVolume:`):

```yaml
      # Chart default is whenUnsatisfiable: ScheduleAnyway, which permits 2-3
      # ingesters on one node after a rolling upgrade — and nothing ever
      # separates them again: the descheduler's RemoveDuplicates plugin is
      # blocked twice over (priorityThreshold feather-high + PodsWithPVC
      # protection) and RemovePodsViolatingTopologySpreadConstraint only acts on
      # DoNotSchedule constraints. With replication_factor 3 across 3 ingesters,
      # co-location means one node holds the whole unflushed head.
      topologySpreadConstraints:
        maxSkew: 1
        topologyKey: kubernetes.io/hostname
        whenUnsatisfiable: DoNotSchedule
```

- [ ] **Step 4: Same for `store_gateway`**

In the same file, inside the `store_gateway:` block, add after line 333 (the end of its `affinity:` block, before `      zoneAwareReplication:`):

```yaml
      # See the ingester block above — same reasoning.
      topologySpreadConstraints:
        maxSkew: 1
        topologyKey: kubernetes.io/hostname
        whenUnsatisfiable: DoNotSchedule
```

- [ ] **Step 5: Render and verify**

```bash
kubectl kustomize apps/clusters/feathre-core/monitoring/mimir | grep -c "whenUnsatisfiable: DoNotSchedule"
kubectl kustomize apps/clusters/feathre-core/monitoring/mimir | grep -A 1 "timeout: 20m"
./scripts/validate.sh
```

Expected: `2`; the timeout present; validate exits `0`.

- [ ] **Step 6: Commit, push, open, merge**

```bash
git add apps/clusters/feathre-core/monitoring/mimir/release.yaml apps/base/mimir/release.yaml
git commit -m "fix(mimir): require host spreading for ingesters and store-gateways"
git pull --rebase origin main
git push -u origin fix/mimir-ingester-spreading
gh pr create --title "fix(mimir): require host spreading for ingesters and store-gateways" --body "$(cat <<'EOF'
## Summary
- ingester + store_gateway topologySpreadConstraints: whenUnsatisfiable ScheduleAnyway -> DoNotSchedule (maxSkew 1, kubernetes.io/hostname). Nothing else can separate them once co-located: the descheduler is blocked by both priorityThreshold feather-high and PodsWithPVC.
- HelmRelease upgrade timeout 5m (default) -> 20m. The release has been Stalled on that timeout since 2026-07-15 while every StatefulSet was actually converged.

Trade-off: with 3 ingesters on 4 workers, a pod will sit Pending rather than co-locate if two workers are simultaneously unavailable. That is intended.

Plan: docs/superpowers/plans/2026-08-03-workload-resilience-and-pod-hardening.md (Task 20)

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] After merge: mimir HelmRelease READY=True, ingesters on 3 distinct nodes, store-gateways on 2 distinct nodes, no Pending pods, Grafana metrics queries still return
- [ ] Single-worker drain rehearsal completes
EOF
)"
```

- [ ] **Step 7: Merge, reconcile once, and watch the rollout**

```bash
flux reconcile kustomization monitoring --with-source
kubectl -n grafana rollout status sts/mimir-ingester --timeout=20m
kubectl -n grafana rollout status sts/mimir-store-gateway --timeout=20m
kubectl get pods -n grafana -o wide | grep -E "mimir-(ingester|store-gateway)"
flux get helmreleases -n grafana | grep mimir
```

Expected: both rollouts complete inside the 20 m; 3 ingesters on **three distinct** nodes and 2 store-gateways on **two distinct** nodes, all `Running`, none `Pending`; the `mimir` HelmRelease now `READY=True` (this also clears the 19-day-old stall).

**If an ingester goes `Pending` with `didn't match pod topology spread constraints`,** you have fewer schedulable workers than replicas. Revert the merge commit immediately and reconcile once — `ScheduleAnyway` lets it place again.

- [ ] **Step 8: Confirm metrics still work**

```bash
kubectl -n grafana logs sts/mimir-ingester --tail=30 | grep -i error || echo "no ingester errors"
```

Then in Grafana, run a Mimir query over the last 15 minutes (e.g. `up`) and confirm it returns data, and confirm the Ceph and Flux dashboards still render.

- [ ] **Step 9: Single-worker drain rehearsal** ⚠️ **operational, requires cluster-admin**

> This is the check theme 9 depends on. Do it deliberately, on a chosen worker, with someone watching.

> ⚠️ **Read `docs/node-drain-pdb-notes.md` (Task 19) before running this — it must already exist.** `kubectl drain` evicts through the same eviction API, so any PDB at `disruptionsAllowed: 0` on the chosen node hangs the drain for reasons that have **nothing to do with Mimir** and would make you misdiagnose this rehearsal. Pick the node accordingly:
>
> ```bash
> kubectl get pdb -A -o json | jq -r '.items[] | select(.status.disruptionsAllowed==0) | "\(.metadata.namespace)/\(.metadata.name)"'
> kubectl get pods -A -o wide --field-selector spec.nodeName=$NODE | grep -E "harbor-registry|feather-core-cluster-pg|n8n-main"
> ```
>
> Expected: the first lists at most `cnpg-system/feather-core-cluster-pg-primary` and `harbor/harbor-registry` (n8n resolved by Task 18, outline by Task 1); the second returns nothing for the node you picked. If the CNPG primary is on that node, do a CNPG switchover first — **never** force-delete a Postgres primary.

```bash
NODE=fr01-wrk-xl-04   # pick the worker hosting the fewest Mimir pods AND none of the 0-disruption PDBs above
kubectl cordon $NODE
kubectl drain $NODE --ignore-daemonsets --delete-emptydir-data --timeout=15m
kubectl get pods -A -o wide | grep -E "Pending|$NODE"
kubectl uncordon $NODE
```

Expected: the drain **completes** within the timeout; no pod is left `Pending` afterwards; Mimir ingesters redistribute across the three remaining workers with `maxSkew: 1` still satisfied.

**If the drain hangs on a Mimir pod**, uncordon immediately and revert the merge commit — `DoNotSchedule` plus `maxUnavailable: 1` PDBs is too strict for this node count and the finding should be re-solved with a `preferred` podAntiAffinity instead.

**Rollback:** revert the merge commit and reconcile `monitoring` once. The TSC change is declarative and the pods reschedule; the 20 m timeout also reverts, which is undesirable — consider re-landing the timeout change on its own if you have to revert the spreading.

---

## Task 21: Investigate the cluster-wide leader-election lease timeouts (investigation, no PR)

**Files:**
- Create: a findings note (path at the executor's discretion, e.g. `docs/superpowers/research/2026-08-XX-apiserver-lease-latency.md`) **only if** the investigation produces something actionable. Do not create an empty stub.

**Interfaces:**
- Consumes: nothing in this plan
- Produces: a root-cause finding and a recommendation, for whoever owns the apiserver/etcd theme. **Produces no cluster or repo change.**

**Why this is not a config fix — verified 2026-08-03, contradicting the audit's recommendation:**

The audit recommends setting `--leader-elect-lease-duration=60s --leader-elect-renew-deadline=45s --leader-elect-retry-period=10s` on `cloudflare-tunnel-ingress-controller` and `step-issuer`. **Neither is possible.**

- `kubectl -n cloudflare-tunnel-ingress-controller exec deploy/cloudflare-tunnel-ingress-controller -- cloudflare-tunnel-ingress-controller --help` lists exactly one leader-election flag: `--leader-elect` (a bool). There are no timing flags. The chart also exposes no controller `extraArgs` (its `cloudflared.extraArgs` applies to the connector, not the controller).
- `kubectl -n step-issuer exec deploy/step-issuer -- /manager --help` lists `-enable-leader-election` and `-leader-election-id` only. No timing flags, and no `--health-probe-bind-address` either — so the audit's "add a `/healthz` probe" is also unavailable. `step-issuer` chart 1.10.2 hardcodes `deployment.args` as a fixed three-key map with no passthrough.

**What is actually true:** lease renewals against the apiserver time out cluster-wide — cf-tunnel 104/103 restarts in 14 d, step-issuer 323 in 43 d, step-ca 50, cert-manager-webhook 43 — and controller-runtime's designed response to losing a lease is to exit. Each event costs ~20 s of stalled reconciliation on a pod that is otherwise `1/1 Running`. The cf-tunnel replicas fail over correctly (their last restarts were 93 minutes apart; identical restart counts is the signature of leadership *alternating*, not simultaneous death), so the 2-replica HA design is working.

**Telemetry gap that blocks the investigation (verified against Mimir 2026-08-03):**
- `apiserver_request_duration_seconds_bucket` is **not** stored — only `_sum` and `_count`. `sum(rate(..._sum{resource="leases",verb="PUT"}[10m])) / sum(rate(..._count{...}[10m]))` over 12 h is a flat **5.6 ms**. The 5 s timeouts are a rare tail that is *invisible* without buckets.
- **No `etcd_*` metrics exist at all** — `etcd_disk_wal_fsync_duration_seconds`, `etcd_server_leader_changes_seen_total`, `etcd_disk_backend_commit_duration_seconds` all return no data. etcd is not being scraped.

- [ ] **Step 1: Re-confirm the restart pattern is still live**

```bash
kubectl get pods -A --sort-by=.status.containerStatuses[0].restartCount -o wide | awk 'NR==1 || $5>20'
kubectl logs -n step-issuer deploy/step-issuer --previous --tail=30 2>/dev/null | grep -iE "lease|leader"
kubectl logs -n cloudflare-tunnel-ingress-controller deploy/cloudflare-tunnel-ingress-controller --previous --tail=20 2>/dev/null | grep -iE "leader|panic"
```

Expected: the same four workloads with high restart counts; `Failed to update lease optimistically ... context deadline exceeded` in step-issuer's previous logs; `panic: leader election lost` in cf-tunnel's.

- [ ] **Step 2: Confirm the telemetry gap yourself**

Query Mimir for:
- `count({__name__="apiserver_request_duration_seconds_bucket"})` → expect **no data**
- `count({__name__=~"etcd_.*"})` → expect **no data**
- `sum(rate(apiserver_request_duration_seconds_sum{resource="leases",verb="PUT"}[10m])) / sum(rate(apiserver_request_duration_seconds_count{resource="leases",verb="PUT"}[10m]))` → expect ~0.005-0.007

- [ ] **Step 3: Correlate restarts with anything observable**

```bash
kubectl get events -A --field-selector reason=Unhealthy,reason=BackOff --sort-by=.lastTimestamp | tail -30
kubectl -n kube-system get pods -o wide | grep -E "kube-apiserver|etcd"
```

On Talos, etcd and kube-apiserver are static pods on `fr01-cp-01..03`. Cross-check the restart timestamps against control-plane node CPU/memory (`kubectl top nodes`) and against the known containerd 2.2.4 shim delete-hang already documented for this cluster.

- [ ] **Step 4: Write up the two candidate remediations and hand them off**

Neither is in this plan's scope; both belong to whoever owns the observability / control-plane theme.

**(a) Unmask the apiserver latency buckets.** kube-prometheus-stack's default apiserver ServiceMonitor drops `apiserver_request_duration_seconds_bucket` via `metricRelabelings`. Restoring it for `resource="leases"` only (to bound cardinality) would make the p99.9 tail visible and confirm or refute the apiserver-latency hypothesis. Repo change, in `infrastructure/clusters/feather-core/base-controllers/kube-prometheus-stack/`.

**(b) Enable etcd metrics scraping.** This requires `cluster.etcd.extraArgs.listen-metrics-urls` in the **Talos machine config**, which lives in the **separate repo** `/mnt/projects/lab/talos-cluster` (remote `TheMeinerLP/FeatherCore`), plus an `Endpoints`/`ServiceMonitor` pair in *this* repo pointing at the control-plane nodes. **This plan writes nothing to the Talos repo** — record the requirement and hand it over.

**Do not** attempt a per-controller workaround. The shared cause is worth more than four separate patches, and for these two controllers no per-controller patch exists.

**Rollback:** n/a — read-only investigation.

---

## Summary of PRs and their gates

| PR | Branch | Tasks | Health gate before the next PR |
|---|---|---|---|
| — | (operational) | 1 | `kubectl get pdb -n outline` empty; `KubePdbNotEnoughHealthyPods` clears |
| 1 | `fix/monitoring-gateway-ha` | 2-4 | tempo-gateway and loki-gateway each 2 pods on 2 distinct nodes; Loki + Tempo queries return |
| 2 | `fix/psa-and-controller-priority-classes` | 5-9 | connector restart admitted under `baseline`; public hostnames answer; `ceph status` HEALTH_OK with an active MDS |
| 3 | `feat/plane-requests-and-probes` | 10-13 | every Plane pod Ready; no `BestEffort`; `tasks.onelitefeather.net` answers |
| 4 | `chore/chart-housekeeping` | 14 | all four micronaut workloads Running at chart 0.5.3 |
| 5 | `feat/shlink-chart-hardening` | 15 | 3/3 shlink pods Running; `1lf.link` answers; no capability errors |
| 6 | `feat/leantime-chart-hardening` | 16 | leantime Running; site answers; `ProbeWarning` volume down (to zero under option (b), roughly halved under option (a)) |
| 7 | `feat/outline-chart-hardening` | 17 | both outline components Running; collaborative editing connects |
| 8 | `fix/n8n-pdb-blocks-drain` | 18-19 | `kubectl get pdb -n n8n` empty; doc merged **before theme 9** |
| 9 | `fix/mimir-ingester-spreading` | 20 | mimir HelmRelease `READY=True`; 3 ingesters on 3 nodes; single-worker drain rehearsal completes |
| — | (investigation) | 21 | n/a |
