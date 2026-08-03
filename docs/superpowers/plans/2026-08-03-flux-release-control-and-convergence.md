# Flux Release Control and Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make what runs on `feather-core` match what is written in git. Seventeen external HelmReleases currently resolve `*` or an unbounded `>=` range against HelmRepositories polled every 5m, so cert-manager has already drifted from the repo's `>=1.14.4` to a running `v1.21.1` with no commit to revert. This plan pins every one of them to what is deployed today, teaches Renovate to see the 70 HelmReleases so the pins get bump PRs instead of rotting, clears a `severity: critical` alert that has fired every 4h for 20 days, adds drift detection and upgrade remediation, removes a wait:true gate that lets a database maintenance window freeze all app deploys, and deletes a large body of dead code.

**Architecture:** Twelve separately-mergeable PRs, ordered by blast radius: dead-code deletion first (inert), then pinning one Flux layer at a time from the least-gating layer (`monitoring`, wait:false) to the most-gating (`base-controllers`, wait:true, blocks the whole chain), then Renovate, then remediation/drift, then the interval+webhook change, then the two genuinely dangerous refactors (the `configs`→`databases` ownership transfer, and the `postBuild.substitute` removal) each with an explicit operator procedure.

**Tech Stack:** FluxCD v2.9.3 (source-controller v1.9.3, kustomize-controller v1.9.3, helm-controller v1.6.2, notification-controller v1.9.2), Kustomize, SOPS/PGP, Renovate, Helm v4.

---

## Prerequisites

- `kubectl` context `admin@feather-core` with read access, and `flux` CLI v2.9.x on PATH.
- `helm` on PATH (v4.2.2 confirmed). Used to prove every version constraint resolves *before* it is merged.
- **The SOPS PGP private key `0231831CB40B8E587B7353CBA3AF727721205A62` must be in the local GnuPG keyring.** Task 8 edits `infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml`, which is a *fully* encrypted file (it matches the `.*\.sops\.ya?ml$` rule in `.sops.yaml`, not the field-level rule). Verify with `gpg --list-secret-keys 0231831CB40B8E587B7353CBA3AF727721205A62` before starting Task 8. Without it, skip that one sub-step and say so in the PR.
- `kustomize` 5.7.1 and `kubeconform` 0.7.0 — `./scripts/validate.sh` pins and downloads these itself; they are not on PATH today, which is fine.
- Write access to `OneLiteFeatherNET/Kubernetes-FLUX` and permission to create a repository webhook (Task 16 only).

## Cross-theme dependencies

- **Nothing in this plan may start until the repo owner has read the decision gates below.** Two of them (webhook exposure, database-gate split) change operational behaviour permanently.
- Task 10 (`ceph-csi-drivers` pin, dead StorageClass region affinity) and Task 12 (delete `rook/storageclass.yaml`) touch the `rook` layer. If the *ceph-capacity-reclamation-and-retention* theme also edits that layer, land whichever lands first and rebase the other — do not merge both PRs concurrently into a wait:true storage layer.
- Task 19 (delete `postBuild.substitute`) edits `apps/clusters/feathre-core/monitoring/{mimir,tempo}/release.yaml`. The *observability-dr* theme owns other edits to those same files. Sequence Task 19 **after** any observability-theme change to mimir/tempo, or the un-escape sed will need re-running.
- This plan clears the *source* of the 20-day critical alert (Task 5). It does **not** add severity routing or a second contact point — that belongs to the *alert-coverage-and-escalation* theme and must land separately.
- The `flux-gitops/inrepo-charts-chartversion-strategy` finding (`reconcileStrategy: ChartVersion` on the in-repo `./helm/*` charts) is deliberately **not** addressed here; see "Deliberately out of scope".

---

## Decision gates

These need a human answer before the referenced task runs. Do not silently pick.

**DG-1 — Pin style: exact versions or minor-bounded ranges?** (Tasks 3, 5, 7, 8, 10, 11)

| Option | Effect |
|---|---|
| **A. Exact pins (`=10.5.15`)** — *recommended* | Nothing moves without a merged commit. Renovate has a concrete value to bump, so pins do not rot (this is why Task 13 must land). Matches `harbor: "=1.19.1"`, `cnpg: "=0.27.1"`, `uptime-kuma: "=4.1.0"` already in the repo. |
| B. Minor-bounded ranges (`>=10.5.15 <10.6.0`) | Patch releases still auto-apply within ~5 minutes, unreviewed. Matches `rook-ceph: ">=1.20.0 <1.21.0"` and `step-issuer: ">=1.10.2 <1.11.0"`. |

The plan below is written for **Option A**. If the owner picks B, substitute the range form everywhere and keep every other step identical. `rook-ceph`'s existing `>=1.20.0 <1.21.0` is left untouched under either option — it was deliberately set by a reviewed PR (#73) and is not part of this plan.

**DG-2 — Renovate preset.** (Task 13) The audit recommends adopting `github>OneLiteFeatherNET/renovate-config`. **I could not verify that this preset exists or what it contains.** Recommendation: keep `config:recommended` and add explicit repo-local config as written in Task 13; adopting the org preset is a separate, reviewable follow-up once someone has read it.

**DG-3 — Expose Flux's webhook receiver publicly?** (Task 15) Raising layer intervals from 1m to 10m without a webhook means push-to-apply latency goes from ~1m to up to 10m plus the dependency chain.

| Option | Effect |
|---|---|
| **A. Receiver + Cloudflare-tunnel Ingress** — *recommended* | Push-to-apply drops to seconds. Adds one publicly reachable HMAC-authenticated endpoint (`flux-webhook.<host>`). Hostname needs an owner decision. |
| B. No webhook, intervals to 5m instead of 10m | Halves the churn instead of removing it; ~5m worst-case latency; no new public surface. |
| C. No webhook, intervals to 10m | Lowest churn, worst latency; relies on `flux reconcile ks flux-system --with-source` after every push (already documented in CLAUDE.md). |

**DG-4 — Accept a short n8n restart?** (Task 11) Pinning `image.tag` from `stable` to a concrete version rolls n8n-main and both workers. Recommendation: yes, in a quiet window. n8n runs schema migrations on start; two n8n versions against one schema is the risk being removed.

**DG-5 — Accept a Mimir Helm upgrade retry?** (Task 3) Setting `spec.timeout` changes the HelmRelease generation, which clears the `Stalled` condition and makes helm-controller retry the upgrade. Because Task 3 pins the chart to the *same* version already deployed (`6.1.0`) and changes no values, Helm should render byte-identical manifests and perform no pod restarts. Recommendation: yes, but run it in a quiet window and watch `cortex_ingester_memory_series` in case the render is not identical.

**DG-6 — Accept that app deploys no longer wait for database health?** (Tasks 16-18) After the split, `base-apps` no longer blocks on `Cluster/feather-core-cluster-pg` and `MariaDB/mariadb-galera` being healthy. That is the entire point (a DB maintenance window stops freezing app deploys) but it means an app can roll out against a database that is mid-failover and CrashLoopBackOff until the DB returns. Recommendation: yes — this is the same trade the team already made deliberately for `monitoring` (see the comment at `clusters/feather-core/monitoring.yaml:17-20`).

---

## Global constraints

- A change takes effect **only** when committed and pushed to `main`. Nothing here is applied by running a command locally.
- Conventional Commits enforced by CI (`commitlint.config.mjs`): types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`; subject starts lowercase; header ≤100 chars. The PR title is the squash subject and is linted too.
- `./scripts/validate.sh` must pass locally before every commit — it is exactly what the `flux-validate` CI job runs.
- Never hammer `flux reconcile` in a loop. One reconcile per stage, then verify. Forcing a layer mid-flight flips it to `Reconciling` and every dependent reports `DependencyNotReady`.
- Renovate moves `main` under you. `git pull --rebase origin main` immediately before every push.
- **Every version constraint written into git must be proved to resolve before the commit is pushed** (Task 2 defines the check). A constraint narrower than what is deployed makes source-controller fail to resolve the chart, taking the HelmRelease not-Ready — and for anything in `base-controllers` (wait:true) that blocks `controllers` → `rook` → `configs` → `base-apps` → `apps`.
- Chart versions in this plan were read from `status.history[0].chartVersion` on **2026-08-03**. Upstream may have published newer versions since. **Re-read the live value with the Task 1 command before writing any pin**; do not copy a number from this document blindly.

---

## Deliberately out of scope

- **The `*` on `outline`, `shlink`, `leantime`, `otis`, `otis-dev`, `vulpes-backend`, `vulpes-backend-dev`.** These resolve `./helm/*` against the in-repo `helmcharts` GitRepository, where the chart version lives in `Chart.yaml` in git. `*` is correct there. Do not "fix" them.
- **`reconcileStrategy: Revision` for the in-repo charts.** The `helmcharts` GitRepository points at this whole repo, so `Revision` would repackage and Helm-upgrade all seven releases on *every* commit to *any* file. The right fix is a CI guard that fails when `helm/<chart>/**` changes without a `Chart.yaml` version bump — that belongs to the *ci-as-a-merge-gate* theme, not here.
- **`rook-ceph`'s `>=1.20.0 <1.21.0`** — set by a reviewed PR (#73) with a documented rationale. Only `ceph-csi-drivers` (which floats across the whole 1.x line) is tightened.
- **`envoyproxy`'s OCIRepository `>=1.7.0 <1.8.0`** — already minor-bounded, matches house style.
- **`upgrade.remediation` on stateful releases** (mimir, harbor, outline, cnpg, mariadb-operator, mariadb-operator-crds, plane, dependency-track). `remediateLastFailure: true` triggers an automatic Helm rollback, which on a chart that is merely slow to converge makes things strictly worse — that is precisely what happened to Mimir. Stateless controllers only.
- **`driftDetection: mode: enabled`.** Only `warn` is introduced, on five infrastructure controllers. `enabled` would fight the `kubectl rollout restart` workflow CLAUDE.md documents as normal. Moving to `enabled` is a follow-up, after the warn events show how much drift actually exists.
- **Notification routing / a second Discord contact point.** Owned by the alert-coverage theme.
- **Deleting the `rook-external-*` StorageClass *objects* by hand.** They are owned by the `rook` Kustomization with `prune: true`; removing the file is sufficient and is the GitOps-correct path.
- **The S3 data-path smoke test.** The `storage-rook/floating-rook-csi-chart-ranges` finding also asks for a PUT+GET+DELETE as a non-owner named user (e.g. `loki`) as a post-upgrade gate, per `docs/incidents/2026-07-18-mariadb-upgrade-and-rgw-access-denied.md:129` item 4. Task 10 pins the chart but does **not** build that gate. It is a CI/tooling deliverable, not a manifest change, and it belongs with the storage theme. Called out here so nobody reads Task 10 as having closed that recommendation.
- **Bumping any chart to a newer version.** Every pin in this plan is to the version already running. Catching up on the seven minors cert-manager has silently drifted through is a separate, reviewable decision once Renovate is opening PRs (Task 13).

---

### Task 1: Capture the live baseline (no changes)

**Files:** none — writes one scratch file outside the repo.

**Interfaces:**
- Consumes: nothing.
- Produces: `/tmp/flux-pin-baseline.txt`, the authoritative list of deployed chart versions that Tasks 3, 7, 8 and 12 pin to.

- [ ] **Step 1: Record every HelmRelease's declared constraint vs. deployed version**

```bash
kubectl get helmreleases -A -o json | python3 -c "
import json,sys
d=json.load(sys.stdin)
for i in sorted(d['items'], key=lambda x:(x['metadata']['namespace'],x['metadata']['name'])):
    m=i['metadata']; c=i.get('spec',{}).get('chart',{}).get('spec',{})
    h=(i.get('status',{}).get('history') or [{}])[0]
    src=(c.get('sourceRef') or {})
    print(f\"{m['namespace']}/{m['name']}\tchart={c.get('chart')}\tconstraint={c.get('version')}\tdeployed={h.get('chartVersion')}\tsrc={src.get('kind')}:{src.get('name')}\")
" | tee /tmp/flux-pin-baseline.txt
```

Expected: 36 lines. Sixteen of the seventeen rows this plan pins show `constraint=*` or `constraint=>=...` with a concrete `deployed=` value.

**The seventeenth (n8n) will show `constraint=None`** — it, plus `envoy`, `spegel` and `kube-prometheus-stack`, use `spec.chartRef` pointing at an OCIRepository, so the constraint lives on the *source*, not the HelmRelease. Read those separately:

```bash
kubectl get ocirepositories -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,SEMVER:.spec.ref.semver,REV:.status.artifact.revision
```

Expected (2026-08-03): `envoyproxy >=1.7.0 <1.8.0 → 1.7.5@sha256:…` (left alone — already minor-bounded), `n8n >=1.10.0 <2.0.0 → 1.11.0@sha256:…` (Task 11 pins this), `prometheus-stack =82.4.3` and `spegel =0.7.2` (already exact).

As of 2026-08-03 the pin table was:

| Release | Declared today | Deployed | Pin to |
|---|---|---|---|
| grafana/grafana | `*` | 10.5.15 | `=10.5.15` |
| grafana/loki | `*` | 7.2.0 | `=7.2.0` |
| grafana/mimir | `*` | 6.1.0 | `=6.1.0` |
| grafana/tempo | `*` | 1.61.3 | `=1.61.3` |
| grafana/alloy-logs | `*` | 1.11.0 | `=1.11.0` |
| grafana/alloy-metrics | `*` | 1.11.0 | `=1.11.0` |
| grafana/alloy-receiver | `*` | 1.11.0 | `=1.11.0` |
| node-red/node-red | `*` | 0.40.2 | `=0.40.2` |
| ollama/ollama | `*` | 1.71.0 | `=1.71.0` |
| reposilite/reposilite | `>=1.3.20` | 1.3.28 | `=1.3.28` |
| mariadb-operator/mariadb-operator | `*` | 26.6.0 | `=26.6.0` |
| mariadb-operator/mariadb-operator-crds | `*` | 26.6.0 | `=26.6.0` |
| cert-manager/cert-manager | `>=1.14.4` | **v1.21.1** | `=1.21.1` |
| cloudflare-tunnel-.../cloudflare-tunnel-... | `>=0.0.23` | 0.0.24 | `=0.0.24` |
| step-ca/step-ca | `>=1.28.2` | 1.30.1 | `=1.30.1` |
| rook-ceph/ceph-csi-drivers | `>=1.0.4 <2.0.0` | 1.0.4 | `=1.0.4` |
| n8n/n8n (OCIRepository) | `>=1.10.0 <2.0.0` | 1.11.0 | `=1.11.0` |

- [ ] **Step 2: Record the current stall and alert state, so the fix is provable**

```bash
kubectl get helmrelease mimir -n grafana -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'
```

Expected right now: `Stalled=True RetriesExceeded`, `Ready=False UpgradeFailed`, `Released=False UpgradeFailed`. Record it; Task 6 asserts this is gone.

---

### Task 2: Prove every constraint resolves before writing it (no changes)

**Files:** none.

**Interfaces:**
- Consumes: the pin table from Task 1.
- Produces: confirmation that each constraint string matches the deployed chart. This is the single guard against the highest-consequence failure mode in this plan.

Chart repositories use inconsistent version strings — `cert-manager` publishes `v1.21.1`, `rook-ceph` publishes `v1.20.3`, everything else publishes bare semver. Flux's semver matcher normalises the leading `v` (proved by `rook-ceph`'s `>=1.20.0 <1.21.0` resolving `v1.20.3` today), but do not take that on faith for an exact pin.

- [ ] **Step 1: Add the repos Helm needs**

```bash
helm repo add grafana-labs https://grafana.github.io/helm-charts/
helm repo add jetstack https://charts.jetstack.io
helm repo add strrl https://helm.strrl.dev
helm repo add smallstep https://smallstep.github.io/helm-charts/
helm repo add mariadb-operator https://helm.mariadb.com/mariadb-operator
helm repo add node-red https://schwarzit.github.io/node-red-chart/
helm repo add ollama https://otwld.github.io/ollama-helm/
helm repo add reposilite https://helm.reposilite.com/
helm repo add ceph-csi-operator https://ceph.github.io/ceph-csi-operator
helm repo update
```

- [ ] **Step 2: Resolve every pin**

```bash
check() { printf '%-45s ' "$1 $2"; helm search repo "$1" --version "$2" -o json 2>/dev/null \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["version"] if r else "!! NO MATCH")'; }
check grafana-labs/grafana                             "=10.5.15"
check grafana-labs/loki                                "=7.2.0"
check grafana-labs/mimir-distributed                   "=6.1.0"
check grafana-labs/tempo-distributed                   "=1.61.3"
check grafana-labs/alloy                               "=1.11.0"
check node-red/node-red                                "=0.40.2"
check ollama/ollama                                    "=1.71.0"
check reposilite/reposilite                            "=1.3.28"
check mariadb-operator/mariadb-operator                "=26.6.0"
check mariadb-operator/mariadb-operator-crds           "=26.6.0"
check jetstack/cert-manager                            "=1.21.1"
check strrl/cloudflare-tunnel-ingress-controller       "=0.0.24"
check smallstep/step-certificates                      "=1.30.1"
check ceph-csi-operator/ceph-csi-drivers               "=1.0.4"
```

Expected: every line prints exactly the version from the Task 1 `deployed` column (cert-manager prints `v1.21.1`, which is a **match** — the constraint normalised the `v`).

**Gate:** any `!! NO MATCH` means that constraint must not be written. For a `v`-prefixed chart that fails on `=1.21.1`, retry as `=v1.21.1`; if that also fails, fall back to the minor-bounded form `>=1.21.1 <1.22.0` for that one chart and note the exception in the PR body. **Do not merge an unresolvable constraint into a wait:true layer.**

---

### Task 3: Pin the monitoring-layer charts and fix Mimir's timeout (PR 1)

**Files:**
- Modify: `apps/base/mimir/release.yaml`
- Modify: `apps/base/loki/release.yaml`
- Modify: `apps/base/tempo/release.yaml`

**Interfaces:**
- Consumes: Task 1 baseline, Task 2 resolution proof.
- Produces: `mimir` off `Stalled`, three pinned charts. First PR because `monitoring` is `wait: false` (`clusters/feather-core/monitoring.yaml:20`) — a failure here cannot block any other layer.

> **This task performs a real Helm upgrade of Mimir.** See DG-5. Run the merge in a quiet window. Chart and values are unchanged, so the render should be identical and no pod should restart — but verify.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b fix/pin-monitoring-charts
```

- [ ] **Step 2: `apps/base/mimir/release.yaml` — pin the chart**

Insert a `version:` line after line 10 (`      chart: mimir-distributed`), so lines 8-14 read:

```yaml
  chart:
    spec:
      chart: mimir-distributed
      version: "=6.1.0"
      sourceRef:
        kind: HelmRepository
        name: grafana-labs
        namespace: flux-system
```

- [ ] **Step 3: `apps/base/mimir/release.yaml` — raise the Helm wait timeout**

Insert between the `      retries: 0` line and the `  interval: 1m0s` line, mirroring the existing pattern at `apps/base/loki/release.yaml:21-23`. (These were lines 17 and 18 before Step 2; Step 2's inserted `version:` shifts them to 18 and 19 — anchor on the text, not the number.)

```yaml
  # 21-pod rolling upgrade; the 5m helm default marks the release Stalled
  # even when the rollout actually completed.
  timeout: 15m0s
```

Do **not** add an `upgrade.remediation` block to mimir. `remediateLastFailure` would roll back a Mimir that is mid-convergence, which is worse than the timeout.

- [ ] **Step 4: `apps/base/loki/release.yaml` — pin the chart**

Insert after line 10 (`      chart: loki`):

```yaml
      version: "=7.2.0"
```

- [ ] **Step 5: `apps/base/tempo/release.yaml` — pin the chart**

Insert after line 10 (`      chart: tempo-distributed`):

```yaml
      version: "=1.61.3"
```

- [ ] **Step 6: Render and verify**

```bash
kubectl kustomize apps/clusters/feathre-core/monitoring | grep -E "chart: (mimir-distributed|loki|tempo-distributed)" -A1
kubectl kustomize apps/clusters/feathre-core/monitoring | grep -c "timeout: 15m0s"
```

Expected: `version: =6.1.0`, `version: =7.2.0`, `version: =1.61.3` each directly under their chart, and the timeout grep returns `1`.

- [ ] **Step 7: Validate**

Run: `./scripts/validate.sh`

Expected: exits `0`; no `Invalid`/`Errors` in the monitoring group.

- [ ] **Step 8: Commit and open the PR**

```bash
git add apps/base/mimir/release.yaml apps/base/loki/release.yaml apps/base/tempo/release.yaml
git commit -m "fix(monitoring): pin lgtm chart versions and raise mimir helm timeout"
git push -u origin fix/pin-monitoring-charts
gh pr create --title "fix(monitoring): pin lgtm chart versions and raise mimir helm timeout" --body "$(cat <<'EOF'
## Summary
- Pins mimir-distributed=6.1.0, loki=7.2.0, tempo-distributed=1.61.3 to the versions already deployed (read from status.history[0].chartVersion)
- Sets spec.timeout: 15m0s on mimir; the 5m Helm default was exceeded by a 21-pod rollout that actually succeeded, leaving the release Stalled=RetriesExceeded since 2026-07-14
- No upgrade.remediation on mimir on purpose: an automatic rollback mid-convergence is worse than the timeout

## Test plan
- [x] ./scripts/validate.sh passes
- [x] every constraint proved resolvable with `helm search repo --version`
- [ ] Merge in a quiet window, reconcile once, confirm mimir Ready=True and no mimir pod restarts
EOF
)"
```

Merging is a human decision — do not merge automatically.

**Rollback:** revert the merge commit. The constraints are declarative; Flux restores `*` and the previous timeout on the next reconcile.

---

### Task 4: Health gate after PR 1

**Files:** none (operational).

- [ ] **Step 1: Reconcile once**

```bash
flux reconcile kustomization monitoring --with-source
```

- [ ] **Step 2: Confirm the stall is cleared**

```bash
kubectl get helmrelease mimir -n grafana -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'
```

Expected: `Ready=True InstallSucceeded|UpgradeSucceeded` and **no `Stalled` condition at all**. Compare against the Task 1 Step 2 recording.

- [ ] **Step 3: Confirm no Mimir pods restarted**

```bash
kubectl get pods -n grafana -l app.kubernetes.io/instance=mimir \
  -o custom-columns=NAME:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.creationTimestamp
```

Expected: 21 pods, ages unchanged from before the merge (they were ~19d old on 2026-08-03). If pods *did* roll, confirm they all reach `1/1 Running` and that `cortex_ingester_memory_series` recovers in Grafana before continuing.

- [ ] **Step 4: Confirm all three charts resolved**

```bash
kubectl get helmcharts -n flux-system -o custom-columns=NAME:.metadata.name,VER:.spec.version,DEPLOYED:.status.artifact.revision | grep -E "mimir|loki|tempo"
```

Expected: `=6.1.0`/`6.1.0`, `=7.2.0`/`7.2.0`, `=1.61.3`/`1.61.3`.

- [ ] **Step 5: Confirm the 20-day critical alert stopped firing**

In Grafana, check rule `core-infra-helmrelease-not-ready`. Expected: state `Normal` (or `Firing` for a *different* release — if so, that release is a new finding, not this one).

**Gate:** do not start Task 5 until Steps 2-5 pass. If mimir is still not Ready, stop — the timeout was not the whole story and the plan's premise for this release needs re-checking.

---

### Task 5: Pin the base-apps charts (PR 2)

**Files:**
- Modify: `apps/base/grafana/release.yaml`
- Modify: `apps/base/alloy-logs/release.yaml`
- Modify: `apps/base/alloy-metrics/release.yaml`
- Modify: `apps/base/alloy-receiver/release.yaml`
- Modify: `apps/base/ollama/release.yaml`
- Modify: `apps/clusters/feathre-core/base-apps/node-red/release.yaml`
- Modify: `apps/clusters/feathre-core/base-apps/reposilite/release.yaml`

**Interfaces:**
- Consumes: a healthy cluster after Task 4.
- Produces: the whole `base-apps` layer pinned. `base-apps` is `wait: true` and `apps` depends on it, so a bad constraint here blocks app deploys — hence the Task 2 proof is mandatory before merging.

**Where the pin goes:** if the cluster overlay already carries a `chart.spec.version`, edit it there (node-red, reposilite). Otherwise add it to the base file. Do not do both — one source of truth per release.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b fix/pin-base-apps-charts
```

- [ ] **Step 2: Add `version:` to the five base files**

In each file, insert one line immediately after the `      chart: <name>` line (line 10 in all five):

| File | Line to insert after | Line to insert |
|---|---|---|
| `apps/base/grafana/release.yaml` | 10 `      chart: grafana` | `      version: "=10.5.15"` |
| `apps/base/alloy-logs/release.yaml` | 10 `      chart: alloy` | `      version: "=1.11.0"` |
| `apps/base/alloy-metrics/release.yaml` | 10 `      chart: alloy` | `      version: "=1.11.0"` |
| `apps/base/alloy-receiver/release.yaml` | 10 `      chart: alloy` | `      version: "=1.11.0"` |
| `apps/base/ollama/release.yaml` | 10 `      chart: ollama` | `      version: "=1.71.0"` |

- [ ] **Step 3: Edit the two overlay constraints**

`apps/clusters/feathre-core/base-apps/node-red/release.yaml:9` — change:

```yaml
      version: "*"
```

to:

```yaml
      version: "=0.40.2"
```

`apps/clusters/feathre-core/base-apps/reposilite/release.yaml:9` — change:

```yaml
      version: ">=1.3.20"
```

to:

```yaml
      version: "=1.3.28"
```

- [ ] **Step 4: Render and verify all seven**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps | python3 -c "
import sys,yaml
for d in yaml.safe_load_all(sys.stdin):
    if not d or d.get('kind')!='HelmRelease': continue
    c=(d['spec'].get('chart') or {}).get('spec') or {}
    print('%-22s %-26s %s' % (d['metadata']['name'], c.get('chart','<chartRef>'), c.get('version')))"
```

(Do **not** use `grep ... | paste - -` here. The rendered YAML contains both the parent key `  chart:` and the leaf `      chart: <name>`, so the pairing silently misaligns, and the four `./helm/*` releases plus `n8n` have no `version` line at all to pair with.)

Expected, for the seven this PR touches:

```
alloy-logs             alloy                      =1.11.0
alloy-metrics          alloy                      =1.11.0
alloy-receiver         alloy                      =1.11.0
grafana                grafana                    =10.5.15
node-red               node-red                   =0.40.2
ollama                 ollama                     =1.71.0
reposilite             reposilite                 =1.3.28
```

The other rows are expected to be unchanged: `bluemap =1.0.5`, `dependency-track =0.39.0`, `harbor =1.19.1`, `plane 3.0.0`, `uptime-kuma =4.1.0`, `n8n <chartRef> None`, and `leantime`/`outline`/`shlink` on `./helm/*` with `None` (correct — see "Deliberately out of scope"). **No remaining `*` or `>=` for the seven.**

- [ ] **Step 5: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 6: Commit and open the PR**

```bash
git add apps/base/grafana/release.yaml apps/base/alloy-logs/release.yaml \
        apps/base/alloy-metrics/release.yaml apps/base/alloy-receiver/release.yaml \
        apps/base/ollama/release.yaml \
        apps/clusters/feathre-core/base-apps/node-red/release.yaml \
        apps/clusters/feathre-core/base-apps/reposilite/release.yaml
git commit -m "fix(apps): pin base-apps chart versions to deployed releases"
git push -u origin fix/pin-base-apps-charts
gh pr create --title "fix(apps): pin base-apps chart versions to deployed releases" --body "$(cat <<'EOF'
## Summary
- Pins grafana=10.5.15, alloy=1.11.0 (x3), ollama=1.71.0, node-red=0.40.2, reposilite=1.3.28
- All values read from status.history[0].chartVersion, not upstream latest, so this is a no-op upgrade
- Removes unattended cross-major upgrades on the base-apps layer; Renovate wiring follows in a later PR

## Test plan
- [x] ./scripts/validate.sh passes
- [x] every constraint proved resolvable with `helm search repo --version`
- [ ] Merge, reconcile once, confirm all seven HelmReleases Ready=True at the same chart versions
EOF
)"
```

**Rollback:** `git revert <merge sha> && git push`, then `flux reconcile kustomization base-apps --with-source` once. The constraints are declarative and every pin is to the already-deployed version, so reverting restores `*`/`>=` and cannot roll a workload. If a release is stuck `ChartNotFound` while you revert, it stays not-Ready until the revert lands — `base-apps` is `wait: true`, so `apps` will report `DependencyNotReady` in the meantime. That is expected and clears with the revert.

---

### Task 6: Health gate after PR 2

**Files:** none (operational).

- [ ] **Step 1: Reconcile once**

```bash
flux reconcile kustomization base-apps --with-source
```

- [ ] **Step 2: Confirm the layer and all releases are Ready**

```bash
flux get kustomizations -A | grep -E "base-apps|apps"
flux get helmreleases -A | grep -E "grafana|alloy|ollama|node-red|reposilite"
```

Expected: `base-apps` and `apps` both `READY=True`; all seven HelmReleases `READY=True` with the pinned chart versions in the revision column.

- [ ] **Step 3: Confirm no unexpected rollouts**

```bash
kubectl get pods -n grafana -l app.kubernetes.io/name=alloy
kubectl get pods -n node-red,ollama,reposilite 2>/dev/null || \
  for ns in node-red ollama reposilite; do kubectl get pods -n $ns; done
```

Expected: all `Running`, restart counts unchanged.

**Gate:** if any HelmRelease reports `ChartNotFound` / `no chart version found for ...`, revert the merge immediately (`git revert <sha>` + push + `flux reconcile kustomization base-apps --with-source`) and re-run Task 2 for that chart. Do not proceed to Task 7 — `base-controllers` is strictly higher-risk than `base-apps`.

---

### Task 7: Pin the mariadb-operator charts (PR 3, commit 1)

**Files:**
- Modify: `infrastructure/base/controllers/mariadb-operator/release.yaml`
- Modify: `infrastructure/base/controllers/mariadb-operator-crds/release.yaml`

**Interfaces:**
- Consumes: a healthy cluster after Task 6.
- Produces: the two most dangerous floating charts in the repo pinned. `mariadb-operator-crds` on `*` means an upstream 27.0.0 would rewrite CRDs cluster-wide against a live Galera cluster with no PR.

> **`base-controllers` is `wait: true` (`clusters/feather-core/base-controllers.yaml:17`) and everything else depends on it.** `mariadb-operator-crds` is applied by `base-controllers`; `mariadb-operator` itself is applied by `controllers` (which is also `wait: true` and sits directly under `base-controllers`). An unresolvable constraint in either takes down the reconciliation of `rook`, `configs`, `base-apps` and `apps`. Task 2's proof is not optional for this PR.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b fix/pin-controller-charts
```

- [ ] **Step 2: Pin mariadb-operator**

`infrastructure/base/controllers/mariadb-operator/release.yaml` — insert after line 9 (`      chart: mariadb-operator`):

```yaml
      version: "=26.6.0"
```

- [ ] **Step 3: Pin mariadb-operator-crds**

`infrastructure/base/controllers/mariadb-operator-crds/release.yaml` — insert after line 9 (`      chart: mariadb-operator-crds`):

```yaml
      version: "=26.6.0"
```

- [ ] **Step 4: Commit**

```bash
git add infrastructure/base/controllers/mariadb-operator/release.yaml \
        infrastructure/base/controllers/mariadb-operator-crds/release.yaml
git commit -m "fix(controllers): pin mariadb-operator and crds charts to 26.6.0"
```

**Rollback (pre-merge):** this commit is not pushed on its own — it ships as part of PR 3 with Task 8. To back it out before pushing: `git checkout main -- infrastructure/base/controllers/mariadb-operator/release.yaml infrastructure/base/controllers/mariadb-operator-crds/release.yaml`. Post-merge rollback is the PR 3 revert described in Task 8.

---

### Task 8: Pin cert-manager, cloudflare-tunnel and step-ca (PR 3, commit 2)

**Files:**
- Modify: `infrastructure/clusters/feather-core/base-controllers/cert-manager/release.yaml`
- Modify: `infrastructure/clusters/feather-core/base-controllers/cloudflare-tunnel-ingress-controller/release.yaml`
- Modify: `infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml` **(SOPS-encrypted — requires the PGP private key)**

**Interfaces:**
- Consumes: Task 7's branch.
- Produces: cert-manager's seven-minor drift (`>=1.14.4` declared, `v1.21.1` running) written back into git.

> **cert-manager is the cluster's entire PKI.** `step-issuer`, `internal-certs` and every gateway certificate depend on it. This pin is a no-op upgrade (it pins to what already runs) but a *wrong* constraint here is the worst outcome in this plan.

- [ ] **Step 1: Pin cert-manager**

`infrastructure/clusters/feather-core/base-controllers/cert-manager/release.yaml:9` — change:

```yaml
      version: ">=1.14.4"
```

to:

```yaml
      version: "=1.21.1"
```

(Or `"=v1.21.1"` / `">=1.21.1 <1.22.0"` if Task 2 Step 2 showed `=1.21.1` did not match. Use whichever form Task 2 proved.)

- [ ] **Step 2: Pin cloudflare-tunnel-ingress-controller**

`infrastructure/clusters/feather-core/base-controllers/cloudflare-tunnel-ingress-controller/release.yaml:9` — change:

```yaml
      version: ">=0.0.23"
```

to:

```yaml
      version: "=0.0.24"
```

- [ ] **Step 3: Pin step-ca (encrypted file)**

Confirm the key first:

```bash
gpg --list-secret-keys 0231831CB40B8E587B7353CBA3AF727721205A62
```

Expected: the `sec rsa4096` line for `cluster0.onelite.feather (flux secrets)`. If this fails, **skip this step**, note it in the PR body, and leave step-ca's `>=1.28.2` for a follow-up by the key holder.

Then:

```bash
sops infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml
```

In the editor, change the `spec.chart.spec.version` value from `>=1.28.2` to `=1.30.1`. Save and exit — `sops` re-encrypts in place. Verify the file is still encrypted:

```bash
grep -c "ENC\[AES256_GCM" infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml
sops -d infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml | grep -A2 "chart:"
```

Expected: the first command returns a non-zero count (the file is still fully encrypted); the second prints `version: =1.30.1`. **If `grep` finds zero `ENC[` markers, do not commit — you have a plaintext secret. Restore with `git checkout -- <file>` and retry.**

- [ ] **Step 4: Render and verify**

```bash
show() { kubectl kustomize "$1" | python3 -c "
import sys,yaml
for d in yaml.safe_load_all(sys.stdin):
    if not d or d.get('kind')!='HelmRelease': continue
    c=(d['spec'].get('chart') or {}).get('spec') or {}
    print('%-38s %-30s %s' % (d['metadata']['name'], c.get('chart','<chartRef>'), c.get('version')))"; }
show infrastructure/clusters/feather-core/base-controllers
show infrastructure/clusters/feather-core/controllers
```

(Same reason as Task 5 Step 4: `grep | paste - -` misaligns because the rendered YAML has both a parent `chart:` key and a leaf `chart: <name>` key, and `spegel`/`envoy` use `chartRef` with no `version` at all.)

Note: the first build pulls in the SOPS-encrypted step-certificates patch and needs the GPG key. If it fails without the key, rely on `./scripts/validate.sh`, which strips SOPS patches by design — which also means **CI never checks the step-ca constraint**. The only proof for that one is the `sops -d` readback in Step 3 plus the post-merge check in Task 9 Step 3.

Expected from `base-controllers`: `cert-manager` → `=1.21.1`, `cloudflare-tunnel-ingress-controller` → `=0.0.24`, `mariadb-operator-crds` → `=26.6.0`, `step-certificates` → `=1.30.1`. Expected from `controllers`: `mariadb-operator` → `=26.6.0`.

- [ ] **Step 5: Validate**

Run: `./scripts/validate.sh` — expected exit `0`.

- [ ] **Step 6: Commit and open the PR**

```bash
git add infrastructure/clusters/feather-core/base-controllers/cert-manager/release.yaml \
        infrastructure/clusters/feather-core/base-controllers/cloudflare-tunnel-ingress-controller/release.yaml \
        infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml
git commit -m "fix(controllers): pin cert-manager, cloudflare-tunnel and step-ca charts"
git push -u origin fix/pin-controller-charts
gh pr create --title "fix(controllers): pin base-controllers chart versions to deployed releases" --body "$(cat <<'EOF'
## Summary
- cert-manager: >=1.14.4 -> =1.21.1. The repo said >=1.14.4 while the cluster ran v1.21.1 — seven minors of unattended upgrade with no commit to revert.
- cloudflare-tunnel-ingress-controller: >=0.0.23 -> =0.0.24
- step-ca: >=1.28.2 -> =1.30.1 (inside the SOPS-encrypted overlay patch)
- mariadb-operator + mariadb-operator-crds: * -> =26.6.0. A `*` on the CRD chart means an upstream major rewrites CRDs against a live Galera cluster unattended.
- Every version read from status.history[0].chartVersion; no-op upgrades.

## Test plan
- [x] ./scripts/validate.sh passes
- [x] every constraint proved resolvable with `helm search repo --version`
- [x] step-certificates/release.sops.yaml still fully encrypted after `sops` edit
- [ ] Merge, reconcile once, confirm base-controllers Ready and the full dependency chain settles
EOF
)"
```

**Rollback:** `git revert <merge sha> && git push`, then `flux reconcile kustomization base-controllers --with-source` once (not in a loop). Every pin is to the version already running, so a revert cannot roll cert-manager, step-ca or the operators.

The failure mode this protects against is an *unresolvable* constraint: `base-controllers` is `wait: true`, so while one of these four HelmReleases is `ChartNotFound` the layer never reports Ready and `controllers → rook → configs → base-apps → apps` all sit at `DependencyNotReady`. Nothing is deleted and nothing restarts — the cluster simply stops applying new revisions until the revert lands. Revert first, diagnose after.

**Rollback for the SOPS edit specifically (pre-commit):** `git checkout -- infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml` restores the file byte-for-byte. Do this rather than trying to hand-repair a partially re-encrypted file — that file also carries the step-ca root CA material.

---

### Task 9: Health gate after PR 3 (highest-risk gate in the pinning sequence)

**Files:** none (operational).

- [ ] **Step 1: Reconcile once**

```bash
flux reconcile kustomization base-controllers --with-source
```

- [ ] **Step 2: Watch the whole chain settle — once, not in a loop**

Wait 3 minutes, then:

```bash
flux get kustomizations -A
```

Expected: every layer `READY=True` at the new revision. Transient `dependency ... is not ready` on `controllers`/`rook`/`configs` during the first minute is normal churn (that is exactly what Task 15 fixes); it must clear.

- [ ] **Step 3: Confirm the four releases**

```bash
flux get helmreleases -A | grep -E "cert-manager|cloudflare-tunnel|step-ca|mariadb-operator"
```

Expected: all `READY=True`. cert-manager's revision shows `v1.21.1`.

- [ ] **Step 4: Confirm PKI still works (cert-manager is the blast radius)**

```bash
kubectl get certificates -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,READY:.status.conditions[0].status
kubectl get pods -n cert-manager
```

Expected: every Certificate `READY=True`; cert-manager pods `Running` with unchanged restart counts.

- [ ] **Step 5: Confirm the databases are untouched**

```bash
kubectl get mariadb -A
kubectl get cluster.postgresql.cnpg.io -A
```

Expected: `mariadb-galera` Ready `True`; `feather-core-cluster-pg` `3/3` "Cluster in healthy state".

**Gate:** all five steps must pass before Task 10. If cert-manager fails to resolve, revert PR 3 immediately — every dependent layer is blocked while it is broken.

---

### Task 10: Pin ceph-csi-drivers and drop the dead region affinity (PR 4)

**Files:**
- Modify: `infrastructure/clusters/feather-core/rook/csi-drivers-release.yaml:11`
- Modify: `infrastructure/clusters/feather-core/rook/release.yaml:43-52`

**Interfaces:**
- Consumes: a healthy cluster after Task 9.
- Produces: `ceph-csi-drivers` pinned. Today it floats across the entire 1.x line — a `1.9.0` chart would be pulled automatically, and ceph-csi is the mount path for all 37 PVCs.

> **This is the storage data path.** Merge in a window where you can watch it. The chart version does not change (`1.0.4` → `=1.0.4`), so no CSI pod should roll.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b fix/pin-ceph-csi-drivers
```

- [ ] **Step 2: Pin the CSI driver chart**

`infrastructure/clusters/feather-core/rook/csi-drivers-release.yaml:11` — change:

```yaml
      version: ">=1.0.4 <2.0.0"
```

to:

```yaml
      version: "=1.0.4"
```

- [ ] **Step 3: Delete the dead `fi-helsinki` node affinity**

`infrastructure/clusters/feather-core/rook/release.yaml` — delete lines 43-52 in their entirety:

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

Every node reports `topology.kubernetes.io/region: fr-rosenau`; this preference can never match. The file should now end after the `tolerations:` block (line 42).

- [ ] **Step 4: Render and verify**

```bash
kubectl kustomize infrastructure/clusters/feather-core/rook | grep -A2 "chart: ceph-csi-drivers"
kubectl kustomize infrastructure/clusters/feather-core/rook | grep -c "fi-helsinki"
```

Expected: `version: =1.0.4`; the second command returns `0`.

- [ ] **Step 5: Validate and commit**

```bash
./scripts/validate.sh
git add infrastructure/clusters/feather-core/rook/csi-drivers-release.yaml \
        infrastructure/clusters/feather-core/rook/release.yaml
git commit -m "fix(rook): pin ceph-csi-drivers to 1.0.4 and drop dead fi-helsinki affinity"
git push -u origin fix/pin-ceph-csi-drivers
gh pr create --title "fix(rook): pin ceph-csi-drivers to 1.0.4 and drop dead fi-helsinki affinity" --body "$(cat <<'EOF'
## Summary
- ceph-csi-drivers: ">=1.0.4 <2.0.0" -> "=1.0.4". The old range floats across the whole 1.x line and ceph-csi is the mount path for all 37 PVCs.
- Removes the rook-ceph operator's preferredDuringScheduling nodeAffinity on region fi-helsinki — the region was retired; every node is fr-rosenau, so the preference can never match.

## Test plan
- [x] ./scripts/validate.sh passes
- [x] =1.0.4 proved resolvable with `helm search repo --version`
- [ ] Merge, reconcile once, confirm rook Ready, ceph HEALTH_OK, CSI plugin pods running on all three storage nodes
EOF
)"
```

- [ ] **Step 6: After merge — reconcile once and verify**

```bash
flux reconcile kustomization rook --with-source
flux get helmreleases -n rook-ceph
kubectl -n rook-ceph get pods -l app=csi-rbdplugin -o wide
kubectl -n rook-ceph get pods -l app=csi-cephfsplugin -o wide
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph status
```

Expected: both HelmReleases `READY=True`; one Running RBD and one Running CephFS plugin pod on each of `fr01-str-01/02/03`; `health: HEALTH_OK`.

**Rollback:** revert the merge; the CSI chart returns to the range and the operator regains the (inert) affinity preference. No data-plane state is involved.

---

### Task 11: Pin n8n off the mutable `stable` tag (PR 5)

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/n8n/release.yaml:12`
- Modify: `infrastructure/clusters/feather-core/base-sources/n8n.yml:10`

**Interfaces:**
- Consumes: a healthy cluster after Task 9.
- Produces: n8n running a version that is written in git.

> **This restarts n8n-main and both workers** (see DG-4). n8n runs schema migrations on startup. Run in a quiet window with no long workflow executions in flight.

Spegel is confirmed running as a DaemonSet in `kube-system` with `--resolve-tags=true` and **no** `--registries` flag, so it mirrors `docker.n8n.io` too. Combined with `imagePullPolicy: IfNotPresent`, each pod runs whatever digest its node happened to cache. The repo already documents this exact hazard at `apps/clusters/feathre-core/apps/vulpes-backend-dev/release.yaml:26-29`.

- [ ] **Step 1: Establish the exact version that is running**

```bash
kubectl exec -n n8n deploy/n8n-main -- n8n --version
kubectl get pods -n n8n -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].imageID}{"\n"}{end}'
```

On 2026-08-03 this returned `2.26.8` and all three pods on `sha256:0afb71a39e51637b4d5b4010d90e68bc502d3ca1d2a4d953eb5fcd7d86330ccd`. **Use the version the command prints now, not the one in this document.**

- [ ] **Step 2: Confirm the concrete tag exists upstream and points at the same digest**

```bash
V=$(kubectl exec -n n8n deploy/n8n-main -- n8n --version | tr -d '\r')
echo "checking docker.n8n.io/n8nio/n8n:${V}"
if command -v crane >/dev/null; then
  crane digest "docker.n8n.io/n8nio/n8n:${V}"
elif command -v skopeo >/dev/null; then
  skopeo inspect --format '{{.Digest}}' "docker://docker.n8n.io/n8nio/n8n:${V}"
elif command -v docker >/dev/null; then
  docker manifest inspect "docker.n8n.io/n8nio/n8n:${V}" >/dev/null && echo "tag ${V} exists"
else
  echo "no registry client available — verify by hand at https://hub.docker.com/r/n8nio/n8n/tags"
fi
```

On this workstation `crane` and `skopeo` are **not** installed and `docker` is (verified 2026-08-03), so the `docker manifest inspect` branch is the one that will run; it needs a reachable Docker daemon.

Expected: a `sha256:…` digest, or `tag <version> exists`. If a digest is printed, it should equal the `imageID` digest from Step 1 — a mismatch is not fatal (the multi-arch manifest-list digest differs from the per-arch `imageID`), but a *missing* tag is.

**If the tag does not exist, stop.** Pinning to a non-existent tag puts n8n into `ImagePullBackOff`. Report it and leave `stable` in place. (I could not verify this tag from the planning session — treat it as unconfirmed.)

- [ ] **Step 3: Branch and pin the image tag**

```bash
git checkout main && git pull --rebase origin main
git checkout -b fix/pin-n8n-image-tag
```

`apps/clusters/feathre-core/base-apps/n8n/release.yaml:11-12` — change:

```yaml
    image:
      tag: "stable"
```

to (substituting the version from Step 1):

```yaml
    # Immutable tag: Spegel caches mutable tags P2P, so "stable" resolves to
    # whatever digest each node last pulled — two n8n versions, one schema.
    image:
      tag: "2.26.8"
```

- [ ] **Step 4: Pin the n8n chart's OCIRepository**

`infrastructure/clusters/feather-core/base-sources/n8n.yml:10` — change:

```yaml
    semver: ">=1.10.0 <2.0.0"
```

to:

```yaml
    semver: "=1.11.0"
```

(`1.11.0` is the deployed chart version from Task 1. Re-check before writing it:

```bash
kubectl get ocirepository n8n -n flux-system -o jsonpath='{.status.artifact.revision}{"\n"}'
```

Expected form: `1.11.0@sha256:a0bf4694f6e0…` — take only the part **before** the `@`; `semver:` does not accept a digest suffix.)

- [ ] **Step 5: Render, validate, commit**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps | grep -A2 "image:" | grep -B1 -A1 "tag:" | head
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/n8n/release.yaml \
        infrastructure/clusters/feather-core/base-sources/n8n.yml
git commit -m "fix(n8n): pin image tag and chart version off mutable stable"
git push -u origin fix/pin-n8n-image-tag
```

Open the PR with a title matching the commit subject.

- [ ] **Step 6: After merge — reconcile once and verify**

```bash
flux reconcile kustomization base-sources --with-source
flux reconcile kustomization base-apps
kubectl get pods -n n8n -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\t"}{.status.phase}{"\n"}{end}'
```

Expected: three pods, all `Running`, all on `docker.n8n.io/n8nio/n8n:<version>` (no `:stable`).

- [ ] **Step 7: Confirm n8n is functional**

Open `https://n8n.apps.onelite.feather` and confirm the UI loads and the workflow list renders. Check for migration errors:

```bash
kubectl logs -n n8n deploy/n8n-main --tail=50 | grep -i "migration\|error" || echo "clean"
```

**Rollback:** revert the merge. `stable` returns and pods roll back. If a *down*-migration ran (visible in the Step 7 logs), do **not** revert blindly — restore from the CNPG backup of the `n8n` database instead, because n8n down-migrations are not always reversible.

---

### Task 12: Delete dead manifests and sources (PR 6)

**Files:**
- Delete: `infrastructure/clusters/feather-core/rook/storageclass.yaml`
- Modify: `infrastructure/clusters/feather-core/rook/kustomization.yaml` (drop line 8)
- Delete: `infrastructure/clusters/feather-core/base-sources/checkmk.yml`
- Modify: `infrastructure/clusters/feather-core/base-sources/kustomization.yaml` (drop line 15)
- Delete: `infrastructure/base/controllers/{backstage,ccm,checkmk,crossplane,minio-operator,proxmox-csi}/`
- Delete: `apps/base/{action-runner-controller,autocert,backstage,minio,pushgateway,pyroscope}/`
- Delete: `helm/metabase/`
- Modify: `CLAUDE.md` (chart list)

**Interfaces:**
- Consumes: nothing.
- Produces: an inert cleanup. Everything deleted here is referenced by zero overlays and consumed by zero live workloads.

This task is inert but is sequenced *after* the pinning because it touches the `rook` layer's kustomization and there is no reason to add a second variable to Task 10's storage window.

- [ ] **Step 1: Re-prove nothing references what is about to be deleted**

```bash
kubectl get pvc -A -o json | python3 -c "
import json,sys
sc={ (i['spec'].get('storageClassName')) for i in json.load(sys.stdin)['items'] }
print('storage classes in use:', sorted(x for x in sc if x))"
grep -rn "rook-external" --include="*.yaml" --include="*.yml" apps infrastructure helm clusters | grep -v "rook/storageclass.yaml"
kubectl get helmrelease -A -o json | grep -c "checkmk-chart"
grep -rn "metabase" --include="*.yaml" --include="*.yml" apps infrastructure clusters
```

Expected: storage classes in use is `['ceph-rbd-fr01']` (or similar — **not** containing `rook-external-*`); the `rook-external` grep returns nothing; the checkmk count is `0`; the metabase grep returns nothing.

**Gate:** if any of these returns a hit, stop and remove that item from this task's scope.

- [ ] **Step 2: Branch and delete**

```bash
git checkout main && git pull --rebase origin main
git checkout -b chore/remove-dead-manifests

git rm infrastructure/clusters/feather-core/rook/storageclass.yaml
git rm infrastructure/clusters/feather-core/base-sources/checkmk.yml
git rm -r infrastructure/base/controllers/backstage \
          infrastructure/base/controllers/ccm \
          infrastructure/base/controllers/checkmk \
          infrastructure/base/controllers/crossplane \
          infrastructure/base/controllers/minio-operator \
          infrastructure/base/controllers/proxmox-csi
git rm -r apps/base/action-runner-controller \
          apps/base/autocert \
          apps/base/backstage \
          apps/base/minio \
          apps/base/pushgateway \
          apps/base/pyroscope
git rm -r helm/metabase
```

- [ ] **Step 3: Drop the two kustomization references**

`infrastructure/clusters/feather-core/rook/kustomization.yaml` — delete line 8 so the file reads:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - secrets.sops.yaml
  - release.yaml
  - csi-drivers-release.yaml
```

`infrastructure/clusters/feather-core/base-sources/kustomization.yaml` — delete line 15 (`  - checkmk.yml`). Every other entry stays.

- [ ] **Step 4: Update CLAUDE.md**

CLAUDE.md lists the in-repo charts as `` (`shlink`, `outline`, `leantime`, `metabase`, `micronaut`) ``. Remove `` `metabase`, `` from that list so the doc matches the tree.

- [ ] **Step 5: Validate**

```bash
./scripts/validate.sh
```

Expected: exit `0`. (`validate.sh` only builds paths that a Flux Kustomization points at, which is exactly why these directories were never validated — that is the finding.)

- [ ] **Step 6: Commit and open the PR**

```bash
git add -A
git commit -m "chore(flux): remove unreferenced base manifests and dead sources"
git push -u origin chore/remove-dead-manifests
gh pr create --title "chore(flux): remove unreferenced base manifests and dead sources" --body "$(cat <<'EOF'
## Summary
- Deletes 12 base directories referenced by no overlay: infrastructure/base/controllers/{backstage,ccm,checkmk,crossplane,minio-operator,proxmox-csi} and apps/base/{action-runner-controller,autocert,backstage,minio,pushgateway,pyroscope}. scripts/validate.sh only builds Flux spec.paths, so these were never kustomize-built or kubeconform-checked.
- Deletes the checkmk HelmRepository — source-controller has been polling it every 5m for 299 days with nothing consuming it.
- Deletes rook/storageclass.yaml: rook-external-rbd and rook-external-cephfs point at clusterID rook-ceph (the only CephCluster is rook-ceph-fr01) and allowedTopologies region fi-helsinki (every node is fr-rosenau). No PVC uses either.
- Deletes helm/metabase (never deployed) and updates CLAUDE.md's chart list to match.

## Test plan
- [x] ./scripts/validate.sh passes
- [x] verified no PVC uses rook-external-*, no HelmRelease references checkmk-chart, no manifest references metabase
- [ ] Merge, reconcile once, confirm the two StorageClasses and the checkmk HelmRepository are pruned
EOF
)"
```

- [ ] **Step 7: After merge — reconcile once and verify the prune**

```bash
flux reconcile kustomization rook --with-source
flux reconcile kustomization base-sources
kubectl get sc
kubectl get helmrepositories -n flux-system | grep checkmk || echo "checkmk pruned"
```

Expected: `rook-external-rbd` and `rook-external-cephfs` are gone from `kubectl get sc`; `checkmk pruned`.

**Rollback:** revert the merge; Flux re-creates the StorageClasses and HelmRepository. No data is involved (deleting a StorageClass does not affect existing PVs).

---

### Task 13: Teach Renovate about Flux (PR 7)

**Files:**
- Modify: `renovate.json`

**Interfaces:**
- Consumes: Tasks 3-12 — **all pinning must be merged first**. Broadening the Flux manager's detection while charts still say `*` produces nothing useful; broadening it *after* pinning is what turns 70 invisible HelmReleases into reviewable bump PRs.
- Produces: a Renovate config that sees the whole repo.

Today `renovate.json` is `{"extends": ["config:recommended"]}`. Renovate's `flux` manager defaults to matching only `flux-system/gotk-components.yaml` and `.flux.yaml`, which is why the Dependency Dashboard (issue #3) lists exactly one Flux file while `grep -rl "kind: HelmRelease" apps infrastructure | wc -l` is 70. Result: zero chart bump PRs in the repo's entire history, including for the charts that *are* correctly pinned (`prometheus-stack` `=82.4.3`, `spegel` `=0.7.2`).

See **DG-2** before starting.

- [ ] **Step 1: Branch and write the config**

```bash
git checkout main && git pull --rebase origin main
git checkout -b ci/renovate-flux-detection
```

Replace `renovate.json` entirely with:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    "config:recommended"
  ],
  "timezone": "Europe/Berlin",
  "schedule": [
    "before 8am on monday"
  ],
  "prConcurrentLimit": 5,
  "flux": {
    "managerFilePatterns": [
      "/^clusters/.+\\.ya?ml$/",
      "/^apps/.+\\.ya?ml$/",
      "/^infrastructure/.+\\.ya?ml$/"
    ]
  },
  "customManagers": [
    {
      "customType": "regex",
      "description": "container images written inline inside HelmRelease spec.values",
      "managerFilePatterns": [
        "/^apps/.+\\.ya?ml$/",
        "/^infrastructure/.+\\.ya?ml$/"
      ],
      "matchStrings": [
        "image:\\s+(?<depName>[a-z0-9._\\-]+(?:\\.[a-z]{2,}|:\\d+)/[^\\s:@\"']+):(?<currentValue>[^\\s@\"']+)"
      ],
      "datasourceTemplate": "docker"
    }
  ],
  "packageRules": [
    {
      "description": "group the grafana lgtm stack into one pr",
      "matchDatasources": ["helm"],
      "matchPackageNames": [
        "grafana",
        "loki",
        "mimir-distributed",
        "tempo-distributed",
        "alloy"
      ],
      "groupName": "grafana lgtm stack"
    },
    {
      "description": "group the flux toolkit into one pr",
      "matchPackageNames": [
        "fluxcd/flux2",
        "/^ghcr\\.io/fluxcd//"
      ],
      "groupName": "flux toolkit"
    },
    {
      "description": "operators and crds: never automerge, let a release settle first",
      "matchPackageNames": [
        "cert-manager",
        "mariadb-operator",
        "mariadb-operator-crds",
        "cloudnative-pg",
        "rook-ceph",
        "ceph-csi-drivers"
      ],
      "automerge": false,
      "minimumReleaseAge": "7 days"
    }
  ]
}
```

Notes on the choices:
- The `image:` regex deliberately requires a registry host (a dot or a `:port` before the first `/`) so it matches `ghcr.io/open-telemetry/...:2.29.0` at `apps/clusters/feathre-core/base-apps/reposilite/release.yaml:31` — the exact image Renovate already bumps at `helm/micronaut/values.yaml:243` via PR #89, and which will otherwise silently drift — without matching every bare word after an `image:` key.
- `managerFilePatterns` is the current option name (it replaced `fileMatch`). If the bot on this repo is pinned to an older Renovate major and rejects the key, rename both occurrences to `fileMatch`.
- No `automerge: true` anywhere. This repo has never merged a chart bump; turning on automerge in the same PR that turns on detection is two unproven changes at once.

- [ ] **Step 2: Validate the config**

```bash
npx --yes --package renovate -- renovate-config-validator renovate.json
```

Expected: `INFO: Config validated successfully`.

If npx is unavailable offline, at minimum: `python3 -c "import json;json.load(open('renovate.json'));print('json ok')"`.

- [ ] **Step 3: Commit and open the PR**

```bash
git add renovate.json
git commit -m "ci(renovate): detect flux manifests and inline helmrelease images"
git push -u origin ci/renovate-flux-detection
gh pr create --title "ci(renovate): detect flux manifests and inline helmrelease images" --body "$(cat <<'EOF'
## Summary
- Extends the flux manager to apps/**, infrastructure/** and clusters/** — it currently detects exactly one file (gotk-components.yaml) while the repo has 70 HelmReleases and 22 chart sources.
- Adds a custom regex manager for image tags written inline in HelmRelease spec.values (reposilite's autoinstrumentation-java pin drifts from the identical image in helm/micronaut/values.yaml, which Renovate already bumps).
- Groups the Grafana LGTM stack and the Flux toolkit; operators/CRDs get minimumReleaseAge 7d and no automerge.
- Adds a Monday-morning schedule so bumps land in a window someone is watching.

Depends on the chart-pinning PRs being merged first — a floating `*` gives Renovate nothing to bump.

## Test plan
- [x] renovate-config-validator passes
- [ ] After merge, check the Dependency Dashboard (issue #3) shows flux(70+) instead of flux(1)
- [ ] Triage the first batch of PRs; expect a one-time flood
EOF
)"
```

- [ ] **Step 4: After merge — verify detection changed**

Wait for Renovate's next run (or trigger it from the Dependency Dashboard checkbox), then:

```bash
gh issue view 3 | sed -n '/Detected Dependencies/,/^$/p' | head -40
```

Expected: the `flux` section now lists dozens of files under `apps/` and `infrastructure/`, not just `clusters/feather-core/flux-system/gotk-components.yaml`.

**Expect a one-time PR flood.** Triage it: close anything that is a major on an operator, and merge patches one layer at a time using the same health gates as Tasks 4/6/9.

**Rollback:** revert the merge. Renovate reverts to detecting one file; no cluster effect whatsoever.

---

### Task 14: Add upgrade remediation and drift detection (PR 8)

**Files:**
- Modify: `infrastructure/base/controllers/cert-manager/release.yaml`
- Modify: `infrastructure/base/controllers/cloudflare-tunnel-ingress-controller/release.yaml`
- Modify: `infrastructure/base/controllers/spegel/release.yaml`
- Modify: `infrastructure/base/controllers/envoy/release.yaml`
- Modify: `infrastructure/base/controllers/cnpg-operator/release.yaml`
- Modify: `apps/base/grafana/release.yaml`
- Modify: `apps/base/alloy-logs/release.yaml`
- Modify: `apps/base/alloy-metrics/release.yaml`
- Modify: `apps/base/alloy-receiver/release.yaml`
- Modify: `apps/base/node-red/release.yaml`
- Modify: `infrastructure/base/controllers/mariadb-operator/release.yaml`

**Interfaces:**
- Consumes: pinned charts (Tasks 3-10). Remediation on a floating chart just retries an unreviewed upgrade three times.
- Produces: stateless releases that retry and roll back on failure instead of stalling; five controllers that report drift instead of silently absorbing it.

26 of 36 HelmReleases have no `spec.upgrade.remediation` at all, so helm-controller defaults to `retries: 0` / `remediateLastFailure: false` — one failed upgrade and the release is `Stalled` until the spec changes. Separately, `driftDetection.mode` is unset on all 36, so any `kubectl edit`/`patch`/`scale` on a Helm-owned object persists forever and is invisible to `flux get helmrelease`.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/flux-remediation-and-drift-warn
```

- [ ] **Step 2: Add `upgrade.remediation` to the stateless releases only**

For each of these ten files, add (or extend, where an `upgrade:` key already exists) a top-level `spec.upgrade` block. Place it immediately after the existing `install:` block:

```yaml
  upgrade:
    remediation:
      retries: 3
      remediateLastFailure: true
```

> **cert-manager is an exception — do NOT give it `remediateLastFailure: true`.** `infrastructure/clusters/feather-core/base-controllers/cert-manager/release.yaml:31-32` sets `crds.enabled: true`, so cert-manager's CRDs are Helm-managed templates in this release. `remediateLastFailure: true` makes helm-controller run `helm rollback` on a failed upgrade, and that rollback reverts the CRDs along with everything else — against a live PKI that `step-issuer`, `internal-certs` and every gateway certificate depend on. This is the same reasoning the plan already applies to `mariadb-operator-crds` and to `mariadb-operator` below. Give cert-manager `retries: 3` only:
>
> ```yaml
>   upgrade:
>     remediation:
>       retries: 3
> ```
>
> The eight releases that get the full block are: cloudflare-tunnel-ingress-controller, spegel, envoy, grafana, alloy-logs, alloy-metrics, alloy-receiver, node-red. (cert-manager and mariadb-operator get `retries` only.) This does not change Step 4's expected counts — cert-manager is not in the `base-apps` build.

Files:
- `infrastructure/base/controllers/cert-manager/release.yaml` (after the `install:` block at lines 15-17) — **`retries: 3` only, no `remediateLastFailure`, see the warning above**
- `infrastructure/base/controllers/cloudflare-tunnel-ingress-controller/release.yaml`
- `infrastructure/base/controllers/spegel/release.yaml`
- `infrastructure/base/controllers/envoy/release.yaml`
- `apps/base/grafana/release.yaml`
- `apps/base/alloy-logs/release.yaml`
- `apps/base/alloy-metrics/release.yaml`
- `apps/base/alloy-receiver/release.yaml`
- `apps/base/node-red/release.yaml`
- `infrastructure/base/controllers/mariadb-operator/release.yaml` — **operator Deployment only**, add `retries: 3` but **not** `remediateLastFailure: true` (a rollback of the operator against a live Galera cluster is not something to automate)

**Do not add this to:** `mimir`, `loki` (already has `retries: 3`), `mariadb-operator-crds`, `cnpg`, `harbor`, `outline`, `plane`, `dependency-track`, or any in-repo `./helm/*` release. Rationale is in "Deliberately out of scope".

- [ ] **Step 3: Add `driftDetection: mode: warn` to five infrastructure controllers**

Add this top-level block to `spec` (place it after the `upgrade:` block) in exactly these five files:

```yaml
  driftDetection:
    mode: warn
    ignore:
      # The documented rollout-restart workflow (CLAUDE.md) mutates this.
      - paths: ["/spec/template/metadata/annotations/kubectl.kubernetes.io~1restartedAt"]
```

Files: `infrastructure/base/controllers/{cert-manager,cnpg-operator,mariadb-operator,envoy,spegel}/release.yaml`.

`warn` only logs and emits Events — it corrects nothing. The `ignore` path is added now, before anything moves to `mode: enabled`, so the escape hatch already exists when that decision comes up.

- [ ] **Step 4: Render and verify**

```bash
kubectl kustomize infrastructure/clusters/feather-core/base-controllers 2>/dev/null | grep -c "mode: warn"
kubectl kustomize infrastructure/clusters/feather-core/controllers | grep -c "mode: warn"
kubectl kustomize apps/clusters/feathre-core/base-apps | grep -c "remediateLastFailure: true"
```

Expected: `3` (cert-manager, cnpg-operator, spegel live in `base-controllers`), then `2` (envoy and mariadb-operator live in `controllers` — verified at `infrastructure/clusters/feather-core/controllers/kustomization.yaml:4,7`), then **`6`** for the third command — grafana, three alloys and node-red are the five this PR adds, **plus `plane`, which already carries `remediateLastFailure: true` at `apps/base/plane/release.yaml:24`** and is the only pre-existing one in the repo. Confirm the baseline first so `6` is meaningful:

```bash
git stash && kubectl kustomize apps/clusters/feathre-core/base-apps | grep -c "remediateLastFailure"; git stash pop
```

Expected baseline: `1`.

- [ ] **Step 5: Validate, commit, open the PR**

```bash
./scripts/validate.sh
git add -A
git commit -m "feat(flux): add upgrade remediation to stateless releases and drift warn mode"
git push -u origin feat/flux-remediation-and-drift-warn
```

PR title: `feat(flux): add upgrade remediation to stateless releases and drift warn mode`.

- [ ] **Step 6: After merge — reconcile once and verify**

```bash
flux reconcile kustomization base-controllers --with-source
flux get helmreleases -A | grep -E "cert-manager|spegel|envoy|cnpg|mariadb-operator|grafana|alloy|node-red"
```

Expected: all `READY=True`.

- [ ] **Step 7: Read the first drift reports**

After ~15 minutes:

```bash
kubectl get events -A --field-selector reason=DriftDetected --sort-by=.lastTimestamp | tail -20
kubectl logs -n flux-system deploy/helm-controller --tail=200 | grep -i drift
```

Expected: either nothing (no drift) or a list of concrete field paths. **Record whatever appears** — that list is the input to a future decision about `mode: enabled`, which is not part of this plan.

**Rollback:** revert the merge. `driftDetection` and `upgrade.remediation` are pure controller behaviour; removing them changes no rendered manifest.

---

### Task 15: Raise layer intervals and add a webhook Receiver (PR 9)

**Files:**
- Modify: `clusters/feather-core/{base-controllers,controllers,base-configs,configs,rook,rook-fr01,internal-certs,base-apps,apps,monitoring}.yaml` (`interval: 1m0s` → `10m0s`, one line each)
- Create: `infrastructure/clusters/feather-core/base-configs/flux-webhook/kustomization.yaml`
- Create: `infrastructure/clusters/feather-core/base-configs/flux-webhook/receiver.yaml`
- Create: `infrastructure/clusters/feather-core/base-configs/flux-webhook/ingress.yaml`
- Create: `infrastructure/clusters/feather-core/base-configs/flux-webhook/token.sops.env` (SOPS-encrypted)
- Modify: `infrastructure/clusters/feather-core/base-configs/kustomization.yaml`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: ~19k/day fewer `DependencyNotReady` events, and a push-to-apply path that does not depend on polling.

Every layer except `rbac` polls at `1m0s` while the two slowest layers take ~20s to reconcile, so dependents report `Ready=False dependency ... is not ready` for roughly a third of every minute. Measured over the 20-day event window: `apps` 22412, `monitoring` 20459, `rook` 19146, `configs` 18631 `DependencyNotReady` events. The `flux-core-layer-not-ready` alert (`gotk_resource_info{customresource_kind="Kustomization", ready="False"}`, `for: 5m`) sees this churn, and a human running `flux get kustomizations` cannot distinguish it from real breakage.

> **Interval and Receiver must land in the same PR.** Raising to 10m without the webhook means a merged commit takes up to 10m plus the dependency chain to apply. See **DG-3**; if the owner picks Option B or C, do only the interval half and set 5m/10m accordingly.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/flux-webhook-and-intervals
```

- [ ] **Step 2: Raise the ten intervals**

In each of these files change the single `interval: 1m0s` line to `interval: 10m0s`. Leave `retryInterval: 30s` and every `timeout:` untouched — genuine failures still retry in 30s.

| File | Line |
|---|---|
| `clusters/feather-core/base-controllers.yaml` | 9 |
| `clusters/feather-core/controllers.yaml` | 9 |
| `clusters/feather-core/base-configs.yaml` | 9 |
| `clusters/feather-core/configs.yaml` | 11 |
| `clusters/feather-core/rook.yaml` | 12 |
| `clusters/feather-core/rook-fr01.yaml` | 9 |
| `clusters/feather-core/internal-certs.yaml` | 12 |
| `clusters/feather-core/base-apps.yaml` | 9 |
| `clusters/feather-core/apps.yaml` | 9 |
| `clusters/feather-core/monitoring.yaml` | 9 |

Anchor on the **text** `interval: 1m0s`, not the line number — `clusters/feather-core/apps.yaml:11` is `timeout: 10m0s`, and overwriting it would produce a duplicate `interval` key and a YAML parse error. Sanity check before editing:

```bash
grep -n "interval: 1m0s" clusters/feather-core/*.yaml
```

Expected: exactly eleven hits — the ten files in the table above, plus `clusters/feather-core/base-sources.yaml:7`, which must **not** be changed.

Do **not** change `clusters/feather-core/base-sources.yaml:7` (`1m0s`). Sources are cheap to poll and are what the Receiver actually pokes.
Do **not** change `clusters/feather-core/rbac.yaml:7` (`1h`) or the root `gotk-sync.yaml:22` (`10m0s`, and it is a generated file marked DO NOT EDIT).

- [ ] **Step 3: Generate the webhook token**

```bash
TOKEN=$(head -c 32 /dev/urandom | base64 | tr -d '\n')
mkdir -p infrastructure/clusters/feather-core/base-configs/flux-webhook
printf 'token=%s\n' "$TOKEN" > infrastructure/clusters/feather-core/base-configs/flux-webhook/token.sops.env
sops -e -i infrastructure/clusters/feather-core/base-configs/flux-webhook/token.sops.env
grep -q "ENC\[AES256_GCM" infrastructure/clusters/feather-core/base-configs/flux-webhook/token.sops.env \
  && echo "encrypted OK" || echo "!! NOT ENCRYPTED — do not commit"
echo "Save this token somewhere safe, you need it for the GitHub webhook: $TOKEN"
```

Expected: `encrypted OK`. `.sops.yaml`'s first rule matches `*.sops.env` and encrypts the whole file. **If it prints the failure line, delete the file and stop — do not commit a plaintext token.**

- [ ] **Step 4: Create the Receiver**

`infrastructure/clusters/feather-core/base-configs/flux-webhook/receiver.yaml`:

```yaml
apiVersion: notification.toolkit.fluxcd.io/v1
kind: Receiver
metadata:
  name: flux-system
spec:
  type: github
  events:
    - ping
    - push
  secretRef:
    name: flux-webhook-token
  resources:
    - apiVersion: source.toolkit.fluxcd.io/v1
      kind: GitRepository
      name: flux-system
      namespace: flux-system
```

- [ ] **Step 5: Create the Ingress**

`infrastructure/clusters/feather-core/base-configs/flux-webhook/ingress.yaml` — hostname is **DG-3**; the value below is the recommendation, replace it with whatever the owner picks:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: flux-webhook-cloudflare-tunnel
  annotations:
    cloudflare-tunnel-ingress-controller.strrl.dev/backend-protocol: http
spec:
  ingressClassName: cloudflare-tunnel
  rules:
    - host: flux-webhook.onelitefeather.dev
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: webhook-receiver
                port:
                  number: 80
```

(`webhook-receiver` is a real ClusterIP Service in `flux-system`, port 80, confirmed live. The endpoint is HMAC-authenticated by the token from Step 3 — an unauthenticated request gets a 401.)

- [ ] **Step 6: Wire it into the base-configs layer**

`infrastructure/clusters/feather-core/base-configs/flux-webhook/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: flux-system
generatorOptions:
  disableNameSuffixHash: true
resources:
  - receiver.yaml
  - ingress.yaml
secretGenerator:
  - name: flux-webhook-token
    envs:
      - token.sops.env
```

`infrastructure/clusters/feather-core/base-configs/kustomization.yaml` — add `flux-webhook` to `resources:`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../infrastructure/base/configs/cert-manager
  - s3-proxy.yaml
  - metallb
  - priorityclasses.yaml
  - flux-webhook
```

`base-configs` already has SOPS decryption configured (`clusters/feather-core/base-configs.yaml`), and `dependsOn: base-controllers`, which is where the cloudflare-tunnel controller lives — so the Ingress cannot be applied before its controller exists.

> **Warning — `base-configs` is `wait: true` with `timeout: 5m0s` (`clusters/feather-core/base-configs.yaml:11,17`), and `rook`, `configs`, `base-apps`, `apps` and `monitoring` all sit behind it.** Adding resources here means their health now gates that entire chain. kstatus marks an Ingress `Current` only once `.status.loadBalancer.ingress` is populated, which for this ingress class means the cloudflare-tunnel controller has actually created the tunnel route. If it does not, `base-configs` times out at 5m and every downstream layer reports `DependencyNotReady` until it is fixed.
>
> This is the house pattern and is proven to work — nine Ingresses of the same class already live in `base-apps` (`wait: true`) and one in `rook-fr01` — but it is a new failure mode for the layer that gates everything. Watch Step 9's output; if `base-configs` does not go Ready within 5 minutes, revert immediately rather than debugging live.
>
> If the owner would rather not put a new gate in front of the whole cluster, the alternative placement is `infrastructure/clusters/feather-core/base-sources/` (`wait: false`). The Ingress may then be applied before the tunnel controller exists on a cold boot, which is harmless — it just sits unrouted until the controller reconciles it.

- [ ] **Step 7: Render and validate**

```bash
kubectl kustomize infrastructure/clusters/feather-core/base-configs | grep -A4 "kind: Receiver"
grep -c "interval: 10m0s" clusters/feather-core/*.yaml | grep -v ":0"
./scripts/validate.sh
```

Expected: the Receiver renders in namespace `flux-system` with `secretRef.name: flux-webhook-token`; exactly the ten files listed in Step 2 (plus `gotk-sync.yaml` is not in that glob) report a `10m0s`; validate exits `0`.

- [ ] **Step 8: Commit and open the PR**

```bash
git add -A
git commit -m "feat(flux): raise layer intervals to 10m and add a github webhook receiver"
git push -u origin feat/flux-webhook-and-intervals
```

- [ ] **Step 9: After merge — reconcile once and read the webhook path**

```bash
flux reconcile kustomization flux-system --with-source
flux get kustomizations -A
kubectl -n flux-system get receiver flux-system
kubectl -n flux-system get receiver flux-system -o jsonpath='{.status.webhookPath}{"\n"}'
kubectl -n flux-system get ingress flux-webhook-cloudflare-tunnel \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}{"\n"}'
```

Expected: `base-configs` `READY=True` (**this is the gate — see the warning above**; if it is `False` with the Ingress not Current, revert now), Receiver `READY=True`, a path like `/hook/<64-hex>`, and a `*.cfargotunnel.com` hostname on the Ingress. If the Receiver is not Ready, check the Secret exists: `kubectl -n flux-system get secret flux-webhook-token`.

- [ ] **Step 10: Register the webhook on GitHub** *(requires repo admin)*

```bash
HOOKPATH=$(kubectl -n flux-system get receiver flux-system -o jsonpath='{.status.webhookPath}')
gh api -X POST repos/OneLiteFeatherNET/Kubernetes-FLUX/hooks \
  -f name=web -F active=true -f 'events[]=push' \
  -f config[url]="https://flux-webhook.onelitefeather.dev${HOOKPATH}" \
  -f config[content_type]=json \
  -f config[secret]="$TOKEN"
```

- [ ] **Step 11: Prove the webhook works end to end**

Push a trivial commit (e.g. a comment change) to `main` and immediately:

```bash
kubectl -n flux-system get gitrepository flux-system -o jsonpath='{.status.artifact.revision}{"\n"}'
```

Expected: the new SHA within ~10 seconds — well under the 1m poll and far under the new 10m layer interval. Also check GitHub's webhook delivery log shows a `200`.

**Rollback:** revert the merge (intervals return to 1m, Receiver and Ingress are pruned) and delete the GitHub webhook. Everything is reversible; the token is single-purpose and can be discarded.

---

### Task 16: Freeze database pruning before the split (PR 10 — first of three)

**Files:**
- Modify: `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`
- Modify: `infrastructure/clusters/feather-core/configs/mariadb-galera/kustomization.yaml`

**Interfaces:**
- Consumes: a healthy cluster.
- Produces: `kustomize.toolkit.fluxcd.io/prune: disabled` on every live CNPG and MariaDB object, which is the **only** thing that stops Task 17 from deleting the production databases.

> **THIS IS THE HIGHEST-CARE SEQUENCE IN THIS PLAN.** `configs` has `prune: true`. Moving `postgresql` and `mariadb-galera` out of it in one commit makes the old owner prune them *before* the new owner adopts them — that deletes `Cluster/feather-core-cluster-pg` and `MariaDB/mariadb-galera`, i.e. every production database. Three PRs, in this order, with a verification gate between each. **Do not collapse them.**

> **PREREQUISITE — a fresh, completed backup of both databases must exist before Task 16 is committed.** Tasks 17 and 18 both end in a hard gate whose only remedy is "restore from backup"; that remedy has to be real before the risk is taken. Verify, do not assume:
>
> ```bash
> kubectl get backup.postgresql.cnpg.io -A \
>   -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,STOPPED:.status.stoppedAt
> kubectl get scheduledbackup.postgresql.cnpg.io -A \
>   -o custom-columns=NAME:.metadata.name,LAST:.status.lastScheduleTime
> kubectl get physicalbackup.k8s.mariadb.com -A \
>   -o custom-columns=NAME:.metadata.name,COMPLETE:.status.conditions[0].status
> ```
>
> Expected: at least one CNPG Backup with `PHASE=completed` and a `stoppedAt` within the last 24h, and a MariaDB physical backup with `COMPLETE=True`. If the newest completed backup is older than the last schema change, trigger one and wait for it before continuing. **Do not start this sequence on an unverified backup.**

> **Side effect to be aware of:** `commonAnnotations` rewrites `metadata.annotations` on the `Cluster` and `MariaDB` CRs. Neither `infrastructure/clusters/feather-core/configs/postgresql/cluster.yaml` nor `.../mariadb-galera/mariadb.yaml` sets `inheritedMetadata`/`inheritMetadata` (verified 2026-08-03), so neither operator propagates the annotation down to Pods or StatefulSets and no rollout should occur. Confirm rather than trust — Step 6 checks pod ages.

Current ownership, verified live:

```
feather-core-cluster-pg  {"kustomize.toolkit.fluxcd.io/name":"configs","kustomize.toolkit.fluxcd.io/namespace":"flux-system"}
mariadb-galera           {"kustomize.toolkit.fluxcd.io/name":"configs","kustomize.toolkit.fluxcd.io/namespace":"flux-system"}
```

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b refactor/databases-prune-freeze
```

- [ ] **Step 2: Annotate everything under postgresql**

`infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml` — insert after line 3 (`namespace: cnpg-system`):

```yaml
# Temporary: keeps the `configs` Kustomization from pruning these when they
# move to the `databases` Kustomization. Removed once ownership has moved.
commonAnnotations:
  kustomize.toolkit.fluxcd.io/prune: disabled
```

- [ ] **Step 3: Annotate everything under mariadb-galera**

`infrastructure/clusters/feather-core/configs/mariadb-galera/kustomization.yaml` — insert after line 3 (`namespace: mariadb-galera`) the identical block.

`commonAnnotations` applies to generated Secrets as well as declared resources, so the SOPS-generated `mariadb`, `cnpg-backup` and `role-*` Secrets are covered too.

- [ ] **Step 4: Verify the annotation lands on every object**

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs 2>/dev/null \
  | grep -c "kustomize.toolkit.fluxcd.io/prune: disabled"
kubectl kustomize infrastructure/clusters/feather-core/configs 2>/dev/null \
  | python3 -c "
import sys,yaml
docs=[d for d in yaml.safe_load_all(sys.stdin) if d]
missing=[f\"{d['kind']}/{d['metadata']['name']}\" for d in docs
         if d['metadata'].get('namespace') in ('cnpg-system','mariadb-galera')
         and (d['metadata'].get('annotations') or {}).get('kustomize.toolkit.fluxcd.io/prune')!='disabled']
print('MISSING:', missing or 'none')"
```

Expected: a non-zero count, and `MISSING: none`. Objects in `gateway`/`metallb` must **not** carry the annotation.

- [ ] **Step 5: Validate, commit, merge**

```bash
./scripts/validate.sh
git add infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml \
        infrastructure/clusters/feather-core/configs/mariadb-galera/kustomization.yaml
git commit -m "refactor(configs): disable pruning on database resources before ownership move"
git push -u origin refactor/databases-prune-freeze
```

- [ ] **Step 6: Gate — confirm the annotation is live on the actual objects**

```bash
flux reconcile kustomization configs --with-source
kubectl get cluster.postgresql.cnpg.io -n cnpg-system feather-core-cluster-pg \
  -o jsonpath='{.metadata.annotations.kustomize\.toolkit\.fluxcd\.io/prune}{"\n"}'
kubectl get mariadb -n mariadb-galera mariadb-galera \
  -o jsonpath='{.metadata.annotations.kustomize\.toolkit\.fluxcd\.io/prune}{"\n"}'
kubectl get secrets -n cnpg-system -o json | python3 -c "
import json,sys
for s in json.load(sys.stdin)['items']:
    a=s['metadata'].get('annotations') or {}
    if s['metadata']['name'].startswith(('role-','cnpg-backup')):
        print(s['metadata']['name'], a.get('kustomize.toolkit.fluxcd.io/prune'))"
```

Expected: **`disabled` on every line.** Databases, users, grants, backups, poolers, secrets — everything.

Also confirm nothing rolled (see the side-effect note above):

```bash
kubectl get pods -n cnpg-system -o custom-columns=NAME:.metadata.name,AGE:.metadata.creationTimestamp
kubectl get pods -n mariadb-galera -o custom-columns=NAME:.metadata.name,AGE:.metadata.creationTimestamp
```

Expected: creation timestamps unchanged from before the merge.

**HARD GATE: if any object does not print `disabled`, do not start Task 17.** Fix the annotation first. Proceeding past a partial freeze deletes whatever was missed.

**Rollback:** this PR only adds an annotation; nothing is moved or deleted yet, so it is the safest step in the sequence. `git revert <merge sha> && git push` then `flux reconcile kustomization configs --with-source` removes the annotation again. **Do not revert this PR while Task 17 is merged and Task 18 is not** — that would re-arm pruning on objects the `configs` Kustomization no longer owns, which is exactly the deletion this sequence exists to prevent. Revert in reverse order (18, then 17, then 16) or not at all.

---

### Task 17: Move the databases into their own wait:false Kustomization (PR 11 — second of three)

**Files:**
- Move: `infrastructure/clusters/feather-core/configs/postgresql/` → `infrastructure/clusters/feather-core/databases/postgresql/`
- Move: `infrastructure/clusters/feather-core/configs/mariadb-galera/` → `infrastructure/clusters/feather-core/databases/mariadb-galera/`
- Create: `infrastructure/clusters/feather-core/databases/kustomization.yaml`
- Create: `clusters/feather-core/databases.yaml`
- Modify: `infrastructure/clusters/feather-core/configs/kustomization.yaml`

**Interfaces:**
- Consumes: the verified prune freeze from Task 16.
- Produces: `configs` reduced to `gateway` + `metallb` (fast, always-healthy plumbing that is a legitimate wait:true gate), and a new `databases` layer with `wait: false`.

See **DG-6**. Both DB CRs are healthy today; this is a latent-risk fix, so do it while everything is green, not during an incident.

- [ ] **Step 1: Branch and move the directories**

```bash
git checkout main && git pull --rebase origin main
git checkout -b refactor/split-databases-from-configs
mkdir -p infrastructure/clusters/feather-core/databases
git mv infrastructure/clusters/feather-core/configs/postgresql \
       infrastructure/clusters/feather-core/databases/postgresql
git mv infrastructure/clusters/feather-core/configs/mariadb-galera \
       infrastructure/clusters/feather-core/databases/mariadb-galera
```

Both directories are self-contained (no `../../../../` references out of the overlay), and SOPS rules are suffix-based not path-based, so nothing needs re-encrypting.

- [ ] **Step 2: Create the databases kustomization**

`infrastructure/clusters/feather-core/databases/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - postgresql
  - mariadb-galera
```

- [ ] **Step 3: Shrink the configs kustomization**

`infrastructure/clusters/feather-core/configs/kustomization.yaml` — becomes:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - gateway
  - metallb
```

- [ ] **Step 4: Create the databases layer Kustomization**

`clusters/feather-core/databases.yaml`:

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: databases
  namespace: flux-system
spec:
  dependsOn:
    - name: base-configs
    - name: controllers
    - name: rook
  interval: 10m0s
  retryInterval: 30s
  timeout: 15m0s
  sourceRef:
    kind: GitRepository
    name: flux-system
  path: ./infrastructure/clusters/feather-core/databases
  prune: true
  # wait:false on purpose, same reasoning as monitoring.yaml: a Galera or
  # CNPG maintenance window must not freeze base-apps -> apps.
  wait: false
  decryption:
    provider: sops
    secretRef:
      name: sops-gpg
```

`dependsOn` mirrors what `configs` had (the CNPG and MariaDB operators live in `base-controllers`/`controllers`; the PVCs come from `rook`). `base-apps` continues to `dependsOn: configs` — which is now only gateway + metallb.

- [ ] **Step 5: Verify both trees build and the DB objects appear exactly once**

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs | grep -E "^kind:" | sort | uniq -c
kubectl kustomize infrastructure/clusters/feather-core/databases 2>/dev/null | grep -cE "kind: (Cluster|MariaDB)$"
./scripts/validate.sh
```

Expected: the `configs` build contains **no** `Cluster`/`MariaDB`/`Database`/`Grant`/`User` kinds; the `databases` build contains them; validate exits `0` (it discovers the new path from `clusters/feather-core/databases.yaml` automatically).

- [ ] **Step 6: Commit and open the PR**

```bash
git add -A
git commit -m "refactor(flux): move database crs into their own wait:false kustomization"
git push -u origin refactor/split-databases-from-configs
gh pr create --title "refactor(flux): move database crs into their own wait:false kustomization" --body "$(cat <<'EOF'
## Summary
- Moves postgresql/ and mariadb-galera/ out of the wait:true `configs` gate into a new `databases` Kustomization with wait:false.
- `configs` keeps only gateway + metallb — fast, always-healthy plumbing that is a legitimate health gate for base-apps -> apps.
- Same reasoning already written into clusters/feather-core/monitoring.yaml:17-20.
- SAFE ONLY BECAUSE the prune-freeze PR merged first: every moved object carries kustomize.toolkit.fluxcd.io/prune: disabled, so `configs` releases them instead of deleting them.

## Test plan
- [x] ./scripts/validate.sh passes; configs build contains no DB kinds, databases build does
- [ ] Merge, reconcile once, confirm ownership labels flipped to `databases` and both DBs still healthy
EOF
)"
```

- [ ] **Step 7: After merge — reconcile once and verify ownership transferred**

```bash
flux reconcile kustomization flux-system --with-source
flux get kustomizations -A | grep -E "configs|databases"
kubectl get cluster.postgresql.cnpg.io -n cnpg-system feather-core-cluster-pg -o jsonpath='{.metadata.labels}{"\n"}'
kubectl get mariadb -n mariadb-galera mariadb-galera -o jsonpath='{.metadata.labels}{"\n"}'
```

Expected: both layers `READY=True`; both label sets now read `"kustomize.toolkit.fluxcd.io/name":"databases"`.

- [ ] **Step 8: Verify the databases are still alive and complete**

```bash
kubectl get cluster.postgresql.cnpg.io -A
kubectl get mariadb,maxscale -A
kubectl get database.k8s.mariadb.com,grant.k8s.mariadb.com,user.k8s.mariadb.com -A --no-headers | wc -l
kubectl get database.postgresql.cnpg.io -A --no-headers | wc -l
kubectl get pods -n cnpg-system,mariadb-galera 2>/dev/null || \
  for ns in cnpg-system mariadb-galera; do kubectl get pods -n $ns; done
```

Expected: `feather-core-cluster-pg` `3/3` healthy; `mariadb-galera` Ready `True`; the MariaDB CR counts match what they were before the merge (13 databases, 14 grants, plus users — record them **before** merging so you can compare); 12 CNPG Databases; all pods `Running`.

**HARD GATE:** if any count dropped, a resource was pruned. Stop and restore from the CNPG/MariaDB backups immediately — do not continue to Task 18.

**Rollback:** revert the merge. Because the prune-disabled annotation is still in place, reverting also cannot delete anything; `configs` re-adopts the objects.

---

### Task 18: Re-enable pruning on the databases layer (PR 12 — third of three)

**Files:**
- Modify: `infrastructure/clusters/feather-core/databases/postgresql/kustomization.yaml`
- Modify: `infrastructure/clusters/feather-core/databases/mariadb-galera/kustomization.yaml`

**Interfaces:**
- Consumes: verified ownership transfer from Task 17 Step 7.
- Produces: a `databases` layer that can prune normally again. Leaving `prune: disabled` in place permanently would mean deleting a `Database` CR from git never removes it from the cluster.

- [ ] **Step 1: Branch and remove the temporary annotation blocks**

```bash
git checkout main && git pull --rebase origin main
git checkout -b refactor/databases-restore-prune
```

Delete the four-line `commonAnnotations` block (comment + `commonAnnotations:` + `kustomize.toolkit.fluxcd.io/prune: disabled`) added by Task 16 from both files.

- [ ] **Step 2: Verify it is gone from the render**

```bash
kubectl kustomize infrastructure/clusters/feather-core/databases 2>/dev/null | grep -c "prune: disabled"
./scripts/validate.sh
```

Expected: `0`, and validate exits `0`.

- [ ] **Step 3: Commit and merge**

```bash
git add -A
git commit -m "refactor(databases): re-enable pruning after ownership transfer"
git push -u origin refactor/databases-restore-prune
```

- [ ] **Step 4: After merge — reconcile once and re-verify the counts**

```bash
flux reconcile kustomization databases --with-source
kubectl get cluster.postgresql.cnpg.io -A
kubectl get mariadb -A
kubectl get database.k8s.mariadb.com,grant.k8s.mariadb.com,user.k8s.mariadb.com -A --no-headers | wc -l
```

Expected: identical to Task 17 Step 8. This is the moment pruning is armed again — if a count drops here, something in the moved tree does not render identically to what it rendered under `configs`.

**Rollback:** revert; the annotation returns and pruning is frozen again.

---

### Task 19: Delete the dead postBuild substitution (PR 13)

**Files:**
- Modify: `clusters/feather-core/base-apps.yaml` (delete lines 22-24)
- Modify: `clusters/feather-core/apps.yaml` (delete lines 22-24)
- Modify: `clusters/feather-core/monitoring.yaml` (delete lines 25-27)
- Modify: 7 files, un-escaping 117 `$${` → `${`

**Interfaces:**
- Consumes: nothing from earlier tasks; sequenced last because it needs an operator-driven suspend/resume window.
- Produces: envsubst disarmed over the entire apps + monitoring tree, and 117 escape sequences removed.

`ALLOWED_CIDRS` is declared in three layer Kustomizations and referenced by **zero** manifests — the Ingress that consumed it was deleted. But declaring *any* `postBuild.substitute` key turns envsubst on for every rendered manifest under that path, which is why 117 `$${` escapes exist. Commit `3d44843` records what happens when one is missed: *"This had blocked base-apps and monitoring from applying any revision, freezing the entire apps layer."*

> **This change is not safe to merge and walk away from.** The layer Kustomization CRs (which carry `postBuild`) and the app manifests (which carry the escapes) are applied by *different* Kustomizations on *different* schedules. If `base-apps` picks up the un-escaped commit while its own CR still has `postBuild.substitute`, envsubst replaces `${AWS_ACCESS_KEY_ID}` with an empty string and Mimir/Tempo lose their S3 credentials. The suspend/resume procedure below removes that race. Follow it exactly.

- [ ] **Step 1: Record the current rendered values, so "unchanged" is provable**

```bash
mkdir -p /tmp/postbuild-before
for hr in "grafana/grafana" "grafana/mimir" "grafana/tempo" "otis/otis" "vulpes/vulpes-backend"; do
  ns=${hr%%/*}; n=${hr##*/}
  kubectl get helmrelease "$n" -n "$ns" -o jsonpath='{.spec.values}' > "/tmp/postbuild-before/${ns}-${n}.json"
done
ls -la /tmp/postbuild-before/
```

- [ ] **Step 2: Branch and delete the three postBuild blocks**

```bash
git checkout main && git pull --rebase origin main
git checkout -b refactor/remove-dead-postbuild-substitution
```

`clusters/feather-core/base-apps.yaml` — delete lines 22-24:

```yaml
  postBuild:
    substitute:
      ALLOWED_CIDRS: "172.16.0.0/16,10.200.0.0/16,192.168.0.0/16"
```

`clusters/feather-core/apps.yaml` — delete the identical lines 22-24.
`clusters/feather-core/monitoring.yaml` — delete the identical lines 25-27.

- [ ] **Step 3: Un-escape all 117 placeholders in the same commit**

```bash
FILES="apps/clusters/feathre-core/base-apps/grafana/release.yaml
apps/clusters/feathre-core/monitoring/mimir/release.yaml
apps/clusters/feathre-core/monitoring/tempo/release.yaml
apps/clusters/feathre-core/apps/otis/release.yaml
apps/clusters/feathre-core/apps/otis-dev/release.yaml
apps/clusters/feathre-core/apps/vulpes-backend/release.yaml
apps/clusters/feathre-core/apps/vulpes-backend-dev/release.yaml"

echo "$FILES" | xargs grep -o '\$\${' | wc -l          # expect 117
echo "$FILES" | xargs sed -i 's/\$\${/${/g'
echo "$FILES" | xargs grep -o '\$\${' | wc -l          # expect 0
echo "$FILES" | xargs grep -o '\${'   | wc -l          # expect 117
```

Expected exactly: `117`, then `0`, then `117`. The last number is the invariant — every `$${` became a `${`, none were lost or doubled.

Per-file distribution (for reference): grafana 93, mimir 8, tempo 4, otis 3, otis-dev 3, vulpes-backend 3, vulpes-backend-dev 3.

The mimir and tempo occurrences are `$${AWS_ACCESS_KEY_ID}` / `$${AWS_SECRET_ACCESS_KEY}` feeding `-config.expand-env`; the otis/vulpes ones are `$${JDBC_URL}` / `$${DB_USER}` / `$${DB_PASS}`; the grafana ones are dashboard `$${datasource}` templating and alertmanager `$${POD_IP}`. All of them are consumed *by the application*, not by Flux — un-escaping restores exactly the string those applications already receive today.

- [ ] **Step 4: Confirm no unescaped placeholder was already present**

```bash
grep -rn '[^$]\${' --include="*.yaml" --include="*.yml" apps/clusters apps/base | grep -v '\$\${' | wc -l
```

Expected: this returned `0` before the change (verified 2026-08-03) — meaning nothing was relying on substitution. After the change it will return 117; that is the expected new state.

- [ ] **Step 5: Validate and commit**

`$FILES` is set in Step 3. If you are running these steps in separate shells (or as separate tool calls), **re-declare it here** — an unset `$FILES` makes `xargs git add` a no-op and you would commit the `postBuild` deletion *without* the un-escaping, which is the exact combination that blanks Mimir's and Tempo's S3 credentials.

```bash
FILES="apps/clusters/feathre-core/base-apps/grafana/release.yaml
apps/clusters/feathre-core/monitoring/mimir/release.yaml
apps/clusters/feathre-core/monitoring/tempo/release.yaml
apps/clusters/feathre-core/apps/otis/release.yaml
apps/clusters/feathre-core/apps/otis-dev/release.yaml
apps/clusters/feathre-core/apps/vulpes-backend/release.yaml
apps/clusters/feathre-core/apps/vulpes-backend-dev/release.yaml"

./scripts/validate.sh
git add clusters/feather-core/base-apps.yaml clusters/feather-core/apps.yaml \
        clusters/feather-core/monitoring.yaml
echo "$FILES" | xargs git add
git status --short
git commit -m "refactor(flux): remove dead allowed_cidrs substitution and unescape placeholders"
git push -u origin refactor/remove-dead-postbuild-substitution
```

`git status --short` must list **ten** modified files: the three layer Kustomizations plus all seven release files. Anything fewer means the un-escaping and the `postBuild` removal are about to be split across commits — stop and fix it.

- [ ] **Step 6: BEFORE MERGING — suspend the three layers**

```bash
flux suspend kustomization base-apps apps monitoring
flux get kustomizations -A | grep -E "base-apps|apps|monitoring"
```

Expected: all three show `SUSPENDED=True`. This is what removes the race — the layers cannot apply the un-escaped commit until their own CRs have been updated.

- [ ] **Step 7: Merge, then update the layer CRs first**

Merge the PR, then:

```bash
flux reconcile kustomization flux-system --with-source
kubectl get kustomization base-apps  -n flux-system -o jsonpath='{.spec.postBuild}{"\n"}'
kubectl get kustomization apps       -n flux-system -o jsonpath='{.spec.postBuild}{"\n"}'
kubectl get kustomization monitoring -n flux-system -o jsonpath='{.spec.postBuild}{"\n"}'
```

Expected: three empty lines. **If any still prints `{"substitute":{"ALLOWED_CIDRS":...}}`, do not resume.** Re-run `flux reconcile kustomization flux-system --with-source` once and re-check.

- [ ] **Step 8: Resume**

```bash
flux resume kustomization base-apps apps monitoring
```

- [ ] **Step 9: Prove the rendered values are unchanged**

```bash
for hr in "grafana/grafana" "grafana/mimir" "grafana/tempo" "otis/otis" "vulpes/vulpes-backend"; do
  ns=${hr%%/*}; n=${hr##*/}
  kubectl get helmrelease "$n" -n "$ns" -o jsonpath='{.spec.values}' > "/tmp/postbuild-after-${ns}-${n}.json"
  echo -n "$hr: "; diff -q "/tmp/postbuild-before/${ns}-${n}.json" "/tmp/postbuild-after-${ns}-${n}.json" \
    && echo IDENTICAL
done
```

Expected: `IDENTICAL` for all five. Any diff means substitution behaved differently than expected — investigate before proceeding.

- [ ] **Step 10: Prove the S3 credentials still expand (the one thing that actually breaks)**

```bash
kubectl get cm -n grafana mimir-config -o yaml | grep -A1 "access_key_id" | head -4
kubectl logs -n grafana -l app.kubernetes.io/component=ingester --tail=30 | grep -i "s3\|error" || echo "clean"
kubectl logs -n grafana -l app.kubernetes.io/name=tempo --tail=30 | grep -i "s3\|access denied" || echo "clean"
```

Expected: `access_key_id: ${AWS_ACCESS_KEY_ID}` (the *literal* string — Mimir expands it from env at runtime via `-config.expand-env`, and it must **not** be blank), and no S3 auth errors in either log.

- [ ] **Step 11: Prove Grafana dashboards still render**

Open Grafana and load any dashboard with a datasource variable. Expected: the datasource picker populates; no dashboard shows a literal `$${datasource}`.

**Rollback:** `flux suspend kustomization base-apps apps monitoring`, revert the merge commit, `flux reconcile kustomization flux-system --with-source`, verify `spec.postBuild` is back on all three CRs, then `flux resume`. Same ordering discipline in reverse — the escapes and the substitution must always be in agreement.

---

## What this plan does NOT fix

Stated so the next reader does not assume otherwise:

- The `core-infra-helmrelease-not-ready` alert still has **no severity routing** — a genuine 03:00 page and stale noise still land in the same unrouted Discord channel with `repeat_interval: 4h`. Task 5 removes the current *source* of the noise, nothing more.
- `reconcileStrategy: ChartVersion` on the in-repo `./helm/*` charts is unchanged. Editing a chart under `helm/` without bumping `Chart.yaml` still silently does nothing, and nothing in CI catches it.
- No OCI signature verification, no image provenance admission control, no policy engine. Drift detection is `warn` only.
- Renovate is given detection and grouping but **no automerge**. Someone still has to merge the bump PRs, and if nobody does, the new pins rot exactly the way `prometheus-stack: =82.4.3` has.

## Things I could not verify

- **That `docker.n8n.io/n8nio/n8n:<version>` exists as a concrete tag.** Task 11 Step 2 checks it at execution time and stops if it does not.
- **Whether `github>OneLiteFeatherNET/renovate-config` exists or what it contains** (DG-2). The plan deliberately does not adopt it.
- **Whether Renovate's regex manager produces a tolerable number of PRs.** The regex was written conservatively (registry host required) but has not been dry-run against the repo.
- **Whether Renovate actually bumps a `=x.y.z` constraint.** This is load-bearing: the entire argument for DG-1 Option A is "pins do not rot because Renovate bumps them". Renovate's flux manager reads `spec.chart.spec.version` as a range, and how it rewrites a `=`-prefixed value depends on the effective `rangeStrategy`. The repo cannot answer this from history — `harbor: "=1.19.1"` and `prometheus-stack: "=82.4.3"` have never produced a bump PR, but only because detection was off (Task 13 is what turns it on), so their silence proves nothing either way. **Verify at Task 13 Step 4:** if the Dependency Dashboard lists the pinned charts as detected but never proposes an update for an out-of-date one, drop the `=` prefix and write bare `10.5.15` (which Renovate treats as pinned and rewrites cleanly), or switch to DG-1 Option B. Do not discover this six months later when a CVE lands.
- **Whether `=1.21.1` matches cert-manager's `v1.21.1`.** The `rook-ceph` range resolving `v1.20.3` is strong evidence that Flux normalises the `v`, but Task 2 makes this an explicit pre-merge check rather than an assumption.
- **Whether the `configs` → `databases` move renders byte-identically.** The two sub-kustomizations are self-contained and set their own `namespace:`, so it should, but Task 17 Step 8 and Task 18 Step 4 count objects on both sides rather than trusting it.
- **Chart versions drift.** Every number in this document was read on 2026-08-03. Task 1 re-reads them; do not skip it.
