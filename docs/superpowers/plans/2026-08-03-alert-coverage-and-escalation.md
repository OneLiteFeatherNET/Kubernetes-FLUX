# Alert Coverage and Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `feather-core` alert on the failures it currently cannot see (node NotReady, kubelet down, any PVC filling, certificate expiry, clock drift, Mimir/Loki ingestion limits, etcd leader loss), and make sure at least one notification path survives the failure it is reporting — via a Flux `Provider`/`Alert` that does not touch Mimir, a `severity=critical` route to a second contact point, an off-cluster heartbeat, and a shipped API audit log.

**Architecture:** Eight separately-mergeable PRs, ordered by risk. PRs 1–4 are additive Grafana/Flux config in this repo. PR 5 adds an off-cluster heartbeat. PRs 6–7 span **two repos** — this GitOps repo *and* the Talos machine-config repo at `/mnt/projects/lab/talos-cluster` (remote `TheMeinerLP/FeatherCore`) — and involve a rolling control-plane machine-config apply. PR 8 is the `noDataState` flip, deliberately last because it is the only change that makes existing alerts *louder*.

**Tech Stack:** Grafana unified alerting (provisioning-format YAML inside `apps/clusters/feathre-core/base-apps/grafana/release.yaml`), Flux `notification.toolkit.fluxcd.io/v1beta3` `Provider`/`Alert`, Grafana Alloy (`loki.source.file`), Kustomize + SOPS/PGP, Talos machine config.

---

## Prerequisites and cross-theme dependencies

Read these before starting. Several are hard gates.

| # | Prerequisite | Why | Affects |
|---|---|---|---|
| P1 | Theme `ceph-capacity-reclamation-and-retention` (P0) is landed and Ceph is out of the nearfull window | Loki/Mimir write to the same Ceph. Shipping the audit log adds log volume; new alerts add series. Do not add ingest load while the cluster is ~3 days from `backfillfull`. | PR 6 (audit log) hard gate; PRs 1–5 are fine before it (they add <50 series total) |
| P2 | Theme `flux-release-control-and-convergence` has fixed the Kustomization interval flapping | 6 of 13 Kustomizations were `Ready=False` at planning time. Flipping `noDataState: OK → Alerting` on the two Flux rules before that is fixed guarantees Discord noise. | **PR 8 hard gate** |
| P3 | Theme `crown-jewel-rotation-leaked-pki-and-credentials` (P0) rotation window | The audit log is the only record that could answer "was the leaked PKI used against us". Ideally PR 6 lands **before** the rotation so the log survives the CP node reboots. If the rotation must go first, accept that the pre-rotation audit trail is unrecoverable and say so in the incident doc. | PR 6 ordering preference (not a hard gate) |
| P4 | You can `sops -d` files in this repo (the PGP key `0231831CB40B8E587B7353CBA3AF727721205A62` is in your keyring) | PRs 1 and 5 create new `*.sops.env` files. | PR 1, PR 5 |
| P5 | `talosctl` works against `fr01-cp-01..03` and you have the talosconfig from `/mnt/projects/lab/talos-cluster` | PRs 6 and 7 apply machine config. | PR 6, PR 7 |

**Ordering constraint inside this plan:** PR 4 (deleting the inert `PrometheusRule` CRs) must not merge before PR 2 (which adds the replacement coverage) is merged **and verified healthy**. Everything else is independent.

---

## Global Constraints

- A change only takes effect when committed and **pushed to `main`**. Flux polls the GitRepository every 1m; the root Kustomization every 10m.
- Conventional Commits are linted in CI (`commitlint.config.mjs`): types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`; subject starts **lowercase**; header ≤100 chars. **The PR title is linted too.**
- `./scripts/validate.sh` must pass locally before every commit.
- Renovate moves `main` under you. `git pull --rebase origin main` immediately before every push.
- **Do not hammer `flux reconcile`.** One reconcile per stage, then verify. Forcing a layer mid-flight flips it to `Reconciling` and makes every dependent report "dependency not ready".
- Grafana alert-rule YAML lives in `spec.values.alerting.rules.yaml` inside the HelmRelease **patch** at `apps/clusters/feathre-core/base-apps/grafana/release.yaml`. Indentation is load-bearing: group entries at 10 spaces (`          - orgId: 1`), group fields at 12, rule entries at 14 (`              - uid: ...`), rule fields at 16, `data:` items at 18, `relativeTimeRange`/`model` fields at 20/22. Copy an existing rule's shape; do not reformat.
- Every rule follows the two-query pattern already used by the 17 existing rules: `refId: A` = raw PromQL against `datasourceUid: mimir`; `refId: B` = a `__expr__` / `type: threshold` condition with `datasourceUid: '-100'`.
- `grafana` is an **external** chart (from the `grafana-labs` HelmRepository), not an in-repo chart under `helm/` — **no `Chart.yaml` version bump is needed** for any change in this plan. (The `helm/` bump rule only applies to `shlink`/`outline`/`leantime`/`metabase`/`micronaut`.)
- `generatorOptions.disableNameSuffixHash: true` means changing a Secret's *contents* does not roll its consumers. Every task in this plan that changes a Secret states the explicit rollout/restart, or explains why none is needed.
- Every PromQL expression in this plan was executed live against the `mimir` datasource on 2026-08-03 during planning. The values below are measurements, not estimates.

---

## Validated live data (2026-08-03 — do not re-derive, just implement)

| Query | Result | Consequence for this plan |
|---|---|---|
| `kubectl get prometheusrule -A` | 37 objects (35 `monitoring/kube-prometheus-stack-*`, 2 `grafana/loki-loki-*`) | All inert — see next two rows |
| `kubectl get prometheus,prometheusagent -A` | `No resources found` | Nothing evaluates the `PrometheusRule` CRs |
| `kubectl get sts -n grafana loki-ruler` | `0/0` | The two Loki `PrometheusRule` CRs are inert too |
| `mimir-config` ConfigMap, `ruler.enable_api` | `true` | The Mimir ruler *could* evaluate rules… |
| `mimir-alertmanager-fallback-config` ConfigMap | `receivers: [{name: default-receiver}]`, `route: {receiver: default-receiver}` | …but its Alertmanager has **a receiver with zero integrations**. Rules loaded into the Mimir ruler would fire into a black hole. This is the decisive reason this plan hand-ports to Grafana instead of `mimirtool rules sync`. |
| `kubectl get hr -n grafana mimir` | `READY=False` for 51d ("Helm upgrade failed … timeout waiting for") | Second reason not to build new machinery on the Mimir ruler |
| `max by (node) (kube_node_status_condition{condition="Ready", status=~"false\|unknown"})` | 10 series, all `0` | Safe expr for node-NotReady; keeps the `node` label; always has series → `noDataState: OK` is correct |
| `min by (node) (up{job="kubelet", metrics_path="/metrics"})` | 10 series, all `1` | Safe expr for kubelet-down; `node` label present |
| `bottomk(3, (certmanager_certificate_expiration_timestamp_seconds - time())/86400)` | 72.75 days (3 certs, labels `exported_namespace`, `name`, `issuer_name`) | 14d warning / 3d critical will not fire on merge. Cert namespace is in `exported_namespace`, **not** `namespace` (which is `cert-manager`). |
| `node_timex_sync_status` | 10 series, all `1`; labels have `instance` but **no `node`** | Clock rules must group `by (instance)` |
| `max(abs(node_timex_offset_seconds))` | `0.0000057` s | A 50 ms threshold is ~4 orders of magnitude above current noise |
| `max(cortex_ingester_memory_series)` | `345,405` = **57.6 %** of the 600k ceiling | 0.8 threshold gives real runway; will not fire on merge |
| `sum by (reason) (rate(cortex_discarded_samples_total[30m]))` over 24h | `0` throughout (only `reason="sample-out-of-order"` exists, counter frozen at ~76k) | `> 0` will not fire on merge; series **do** exist today but may vanish → `noDataState: OK` required |
| `sum by (reason) (rate(loki_discarded_samples_total[30m]))` over 24h | `0` throughout | same |
| `sum(loki_ingester_memory_streams)` | `418`; `replication_factor: 3` (loki/release.yaml:48) | distinct streams ≈ 139 of 5000 = 2.8 % |
| `cortex_limits_overrides` | **does not exist** (no overrides-exporter series in Mimir) | The 600k ceiling must be hardcoded in the rule with a comment pointing at `mimir/release.yaml:57` |
| `etcd_server_has_leader` | **no series at all** | etcd is not merely unalerted, it is **unmonitored**. The `kube-prometheus-stack-kube-etcd` ServiceMonitor and headless Service exist but the Service has **no endpoints**, and Talos sets no `listen-metrics-urls`. Needs a Talos change → PR 7. |
| PVC usage (`used/capacity*100`) | galera-0/1/2 at **76.61 / 76.62 / 76.58 %**; next highest `ollama` 36.6 %, `data-dependency-track-api-server-0` 31.2 %, `data-harbor-trivy-0` 25.8 % | Generalising the PVC rule to all PVCs adds no new firing alerts today, but galera will cross 80 % soon — that is the point |
| `kubectl get nodes -o custom-columns=…TAINTS` | cp-01..03 tainted `node-role.kubernetes.io/control-plane`; str-01..03 tainted `node-role.feather/storage` | `alloy-logs` DaemonSet is 4/4 (workers only) — it cannot see control-plane files |
| `kubectl get ns monitoring -o jsonpath='{.metadata.labels}'` | `pod-security.kubernetes.io/enforce: privileged` | A hostPath-mounting DaemonSet can live in `monitoring` with **no PSA change**. The `grafana` namespace has no PSA labels → cluster default `enforce: baseline` → hostPath **forbidden** there. |
| kube-apiserver pod volumes | `hostPath: /var/log/audit/kube` (rw), args `--audit-log-path=/var/log/audit/kube/kube-apiserver.log --audit-log-maxage=30 --audit-log-maxbackup=10 --audit-log-maxsize=100` | The file is on the CP node's `EPHEMERAL` partition and readable from a hostPath mount |
| `kubectl exec alloy-logs -c alloy -- id` | `uid=0(root)` | Alloy already runs as root → can read the `0600` audit log |
| helm chart `alloy@1.11.0` values | `alloy.mounts.extra`, `controller.volumes.extra`, `controller.tolerations`, `controller.nodeSelector`, `controller.volumeClaimTemplates` all exist | The audit collector and the alloy-metrics WAL PVC are both plain values changes |
| `kubectl -n flux-system get deploy notification-controller -o jsonpath='{…args}'` | `--watch-all-namespaces=true`, **no** `--no-cross-namespace-refs` | An `Alert` in `flux-system` may use `namespace: '*'` event sources |
| `kubectl get crd alerts…/providers… -o …versions` | `v1beta3` only (served + storage) | Use `notification.toolkit.fluxcd.io/v1beta3` |
| `clusters/feather-core/flux-system/gotk-sync.yaml` root Kustomization | **has no `decryption:` block** | A SOPS secret placed under `clusters/feather-core/flux-system/` would **never be decrypted**. This is why the Flux `Provider` secret goes in the `base-sources` layer instead (which has `decryption: {provider: sops, secretRef: {name: sops-gpg}}`, `dependsOn: []`, `wait: false` — the most independent layer in the graph). `scripts/validate.sh` also only builds paths declared in `clusters/feather-core/*.yaml`, so files under `flux-system/` are not validated at all. |

---

## Decision gates (resolve with the repo owner before the affected PR)

| Gate | Where | Options | Recommendation |
|---|---|---|---|
| **DG-1** | PR 1, Task 1 | Flux `Provider` webhook: (a) reuse the existing `#alerts` Discord webhook from `grafana-discord.sops.env`; (b) a **new** webhook to a dedicated `#flux` channel | **(b)**. The whole point is a second, independent path; sharing the webhook means one revoked webhook kills both paths, and Flux events are chattier than Grafana alerts. |
| **DG-2** | PR 1, Task 2 | Second contact point for `severity=critical`: (a) a second Discord webhook to `#alerts-critical`; (b) an ntfy.sh topic with phone push; (c) email via the existing (currently unrouted, empty-uid) stock `email receiver` | **(a) plus (b) later**. (a) is zero-cost and immediately visually distinct; (b) is what actually wakes someone at 03:00 and can be added as a third receiver on the same route without re-plumbing. |
| **DG-3** | PR 5, Task 14 | External heartbeat target: (a) healthchecks.io free tier; (b) ntfy.sh scheduled-message/poll; (c) uptime-kuma "push" monitor on a $5 VPS | **(a)**. Purpose-built dead-man's-switch semantics, configurable grace period, free for 20 checks, no infra to run. (c) is strictly better if a VPS already exists (it can also run external synthetic checks against `grafana.apps.onelite.feather`), but it is another box to maintain. |
| **DG-4** | PR 6, Task 17 | Audit-policy tightening scope: (a) exactly the four `RequestResponse` resources listed in Task 17; (b) blanket `RequestResponse`; (c) leave `level: Metadata` and only ship the log | **(a)**. (b) will bury Loki (and Ceph). (c) is a valid interim answer if P1 (Ceph capacity) is not yet resolved — shipping the log at `Metadata` is still a strict improvement over losing it on the next node reset. |
| **DG-5** | PR 7, Task 19 | etcd metrics exposure: (a) `listen-metrics-urls: http://0.0.0.0:2381` (unauthenticated, plaintext, reachable from any pod/host on the mgmt network); (b) leave etcd unmonitored | **(a), but scoped**: bind to the node's mgmt IP rather than `0.0.0.0` if Talos accepts a per-node value, and pair it with the `lan-exposure-and-unmanaged-sniffer` theme's work. The metrics endpoint exposes no key material, only counters. If the owner is not comfortable, take (b) and record it — do not ship a rule against a metric that has no series. |
| **DG-6** | Appendix A | alloy-metrics WAL persistence: (a) `ephemeral-storage` limit only; (b) limit **plus** a `volumeClaimTemplate` | **(a) in this plan.** (b) requires `kubectl delete sts --cascade=orphan` outside GitOps and re-introduces the Ceph dependency for the very component that must survive a Ceph outage. Documented in Appendix A, deliberately not scheduled. |

---

## Deliberately NOT doing (scope discipline)

1. **Not wiring the 35 `PrometheusRule` CRs into the Mimir ruler** (`mimirtool rules sync` / a rule-sync sidecar). Three independent reasons: the Mimir Alertmanager's fallback config has a receiver with **zero integrations**, so evaluated alerts would be silently dropped; the `mimir` HelmRelease has been `Ready=False` for 51 days; and the upstream set lights up dozens of alerts at once, several of which (`KubeCPUOvercommit`, `KubeletTooManyPods`, `CPUThrottlingHigh`) are structurally noisy on a 10-node cluster. Hand-porting the five that matter costs less and routes through a path that demonstrably works today.
2. **Not re-adding a Prometheus server.** `prometheus.enabled: false` is a deliberate, documented choice (kube-prometheus-stack/release.yaml:13–31 is the rationale comment, :32–33 the setting) — Alloy replaced the Prometheus Agent.
3. **Not deleting the two Loki `PrometheusRule` CRs** (`grafana/loki-loki-alerts`, `grafana/loki-loki-rules`). They are chart-owned (`helm.sh/chart: loki-7.2.0`); suppressing them means a Loki values change, and they are harmless. PR 4 removes only the 35 kube-prometheus-stack ones, which is where the "we have coverage" illusion lives.
4. **Not adding a `type: github` Flux Provider / commit statuses.** ~78 % of recent changes bypass PRs entirely, so a commit status has nothing to attach to. That fix belongs to the `ci-as-a-merge-gate` theme and should be sequenced after it makes PRs mandatory.
5. **Not adding `healthChecks` to the 13 Flux Kustomizations.** 11 of them already use `wait: true`, which is the same signal; this belongs to `flux-release-control-and-convergence`.
6. **Not touching uptime-kuma's topology** (single in-cluster replica, DB on the same Galera). The external heartbeat in PR 5 covers the gap uptime-kuma cannot; re-homing uptime-kuma is a separate, larger decision.
7. **Not adding a `volumeClaimTemplate` to alloy-metrics** — see DG-6 / Appendix A.
8. **Not changing the Discord message templates.** The known Grafana bug #114973 (raw `{{ $labels.x }}` in RESOLVED notifications) is recorded as accepted-cosmetic; new rules inherit the same behaviour and that is fine.

---

# PR 1 — Independent failure path, critical routing, and the cheap safety limit

Lowest risk in the plan: two additive CRs, one routing child node, one resource limit. Nothing here depends on Mimir.

### Task 1: Add a Flux notification-controller Provider + Alert

> **Requires a credential.** You need a Discord webhook URL (DG-1). Create it in Discord → Server Settings → Integrations → Webhooks. Do not paste it into any file that is not `*.sops.env`.

**Files:**
- Create: `infrastructure/clusters/feather-core/base-sources/flux-notifications.yaml`
- Create: `infrastructure/clusters/feather-core/base-sources/flux-discord.sops.env`
- Modify: `infrastructure/clusters/feather-core/base-sources/kustomization.yaml`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `Provider/flux-discord` + `Alert/flux-errors` in `flux-system` — the only alerting path in the cluster that depends on neither Mimir, nor Grafana, nor Ceph.

**Why `base-sources` and not `clusters/feather-core/flux-system/`:** the root Kustomization in `gotk-sync.yaml` has **no `decryption:` block**, so a SOPS secret placed there is never decrypted; and `scripts/validate.sh` does not build that path. `base-sources` has `decryption: {provider: sops, secretRef: {name: sops-gpg}}`, `dependsOn: []`, `wait: false`, and already sets `namespace: flux-system` for everything it renders.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/flux-notification-provider
```

- [ ] **Step 2: Create the encrypted webhook secret**

```bash
cd /mnt/projects/oss/onelitefeather/Kubernetes-FLUX/.claude/worktrees/happy-exploring-puddle
printf 'address=REPLACE_WITH_DISCORD_WEBHOOK_URL\n' \
  > infrastructure/clusters/feather-core/base-sources/flux-discord.sops.env
$EDITOR infrastructure/clusters/feather-core/base-sources/flux-discord.sops.env   # paste the real URL
sops -e -i infrastructure/clusters/feather-core/base-sources/flux-discord.sops.env
```

The key **must** be named `address` — that is the key notification-controller reads for a `discord` Provider.

Verify it is encrypted before anything else:

```bash
grep -c '^address=ENC\[' infrastructure/clusters/feather-core/base-sources/flux-discord.sops.env
```

Expected: `1`. **If this prints `0`, stop — the file is plaintext. `rm` it and redo Step 2.** (The root `.sops.yaml` rule `.*\.(env|sops\.env|…)$` covers this path.)

- [ ] **Step 3: Create the Provider + Alert**

Create `infrastructure/clusters/feather-core/base-sources/flux-notifications.yaml`:

```yaml
# The only alerting path in this cluster that does not terminate inside it:
# notification-controller -> Discord over egress. No Mimir, no Grafana, no Ceph.
apiVersion: notification.toolkit.fluxcd.io/v1beta3
kind: Provider
metadata:
  name: flux-discord
  namespace: flux-system
spec:
  type: discord
  username: flux
  secretRef:
    name: flux-discord-webhook
---
apiVersion: notification.toolkit.fluxcd.io/v1beta3
kind: Alert
metadata:
  name: flux-errors
  namespace: flux-system
spec:
  providerRef:
    name: flux-discord
  # error only: ReconciliationFailed, DependencyNotReady, HelmUpgradeFailed,
  # SOPS decryption errors. eventSeverity:info would emit on every successful
  # apply across 13 Kustomizations and 36 HelmReleases.
  eventSeverity: error
  eventSources:
    - kind: Kustomization
      name: '*'
      namespace: flux-system
    - kind: HelmRelease
      name: '*'
      namespace: '*'
    - kind: GitRepository
      name: '*'
      namespace: flux-system
  suspend: false
```

`namespace: '*'` on HelmRelease is required (HelmReleases live in `rook-ceph`, `grafana`, `monitoring`, …) and is permitted because notification-controller runs without `--no-cross-namespace-refs`.

- [ ] **Step 4: Register both in the kustomization**

In `infrastructure/clusters/feather-core/base-sources/kustomization.yaml`, add `flux-notifications.yaml` at the end of `resources:` and append the two new top-level blocks. The complete file becomes:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: flux-system
generatorOptions:
  disableNameSuffixHash: true
resources:
  - mariadb-operator.yml
  - cnpg.yml
  - helmcharts.yml
  - node-red.yml
  - reposilite.yml
  - strrl.yml
  - smallstep.yml
  - jetstack.yml
  - spegel.yaml
  - harbor.yml
  - checkmk.yml
  - dependency-track.yml
  - dirsigler.yml
  - envoy.yaml
  - prometheus-stack.yaml
  - grafana.yml
  - rook.yaml
  - ceph-csi-operator.yaml
  - n8n.yml
  - ollama.yml
  - descheduler.yml
  - plane.yml
  - flux-notifications.yaml

secretGenerator:
  - name: flux-discord-webhook
    envs:
      - flux-discord.sops.env
```

> ⚠️ **Do not retype this file from memory — diff it.** `ceph-csi-operator.yaml` (line 22 today) is load-bearing: it is the `HelmRepository` source for the `ceph-csi-drivers` HelmRelease in `infrastructure/clusters/feather-core/rook/`. Dropping it makes `base-sources` prune the HelmRepository and the CSI-driver release loses its source. After editing, prove you only added lines:
>
> ```bash
> git diff --stat infrastructure/clusters/feather-core/base-sources/kustomization.yaml
> git diff infrastructure/clusters/feather-core/base-sources/kustomization.yaml | grep '^-' | grep -v '^---'
> ```
>
> Expected: the second command prints **nothing** (no removed lines). If it prints a removed `resources:` entry, restore it before committing.

`disableNameSuffixHash: true` is required so `secretRef.name: flux-discord-webhook` stays valid. **No rollout restart is needed** when the webhook is later rotated: notification-controller reads the Secret per event, it does not mount it.

- [ ] **Step 5: Render and verify**

```bash
kubectl kustomize infrastructure/clusters/feather-core/base-sources | grep -A4 "kind: Provider"
kubectl kustomize infrastructure/clusters/feather-core/base-sources | grep -c "name: flux-discord-webhook"
```

Expected: the Provider block shows `type: discord` and `secretRef.name: flux-discord-webhook`; the second command returns `2` (the Provider's `secretRef` and the generated Secret's `metadata.name`).

- [ ] **Step 6: Validate**

Run: `./scripts/validate.sh`

Expected: exits `0`; the `./infrastructure/clusters/feather-core/base-sources` group reports `Invalid: 0, Errors: 0`. (The `Provider`/`Alert` CRs have no upstream schema and are skipped by `-ignore-missing-schemas`; the generated Secret is skipped by `-skip Secret`.)

- [ ] **Step 7: Commit**

```bash
git add infrastructure/clusters/feather-core/base-sources/flux-notifications.yaml \
        infrastructure/clusters/feather-core/base-sources/flux-discord.sops.env \
        infrastructure/clusters/feather-core/base-sources/kustomization.yaml
git commit -m "feat(flux): add discord notification provider and error alert"
```

**Rollback:** delete the three file changes and push; Flux prunes the `Provider`/`Alert`/`Secret` on the next `base-sources` reconcile. No state is involved.

---

### Task 2: Add a `severity=critical` child route and a second contact point

> **Requires a credential** (DG-2): a second Discord webhook URL, ideally to a different channel.
> **Loud warning:** a syntax error in `policies.yaml` can drop **all** Grafana alert routing silently. Step 4's render check is not optional.

**Files:**
- Create: `apps/clusters/feathre-core/base-apps/grafana/grafana-discord-critical.sops.env`
- Modify: `apps/clusters/feathre-core/base-apps/grafana/kustomization.yaml`
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml` (`valuesFrom` block at :26–29; `contactpoints.yaml` at :27465–27478; `policies.yaml` at :27501–27509)

**Interfaces:**
- Consumes: nothing
- Produces: contact point `discord-critical`, reached by every rule carrying `severity: critical` (**8** of the existing 17 — `grep -c "^                  severity: critical$" apps/clusters/feathre-core/base-apps/grafana/release.yaml` → `8` — plus the new ones from PR 2)

- [ ] **Step 1: Create the encrypted webhook secret**

```bash
printf 'webhookUrl=REPLACE_ME\n' > apps/clusters/feathre-core/base-apps/grafana/grafana-discord-critical.sops.env
$EDITOR apps/clusters/feathre-core/base-apps/grafana/grafana-discord-critical.sops.env
sops -e -i apps/clusters/feathre-core/base-apps/grafana/grafana-discord-critical.sops.env
grep -c '^webhookUrl=ENC\[' apps/clusters/feathre-core/base-apps/grafana/grafana-discord-critical.sops.env
```

Expected: `1`. If `0`, the file is plaintext — `rm` and redo.

- [ ] **Step 2: Register the secret**

In `apps/clusters/feathre-core/base-apps/grafana/kustomization.yaml`, append to `secretGenerator:`:

```yaml
  - name: grafana-discord-critical
    envs:
      - grafana-discord-critical.sops.env
```

- [ ] **Step 3: Wire the secret into the second contact point**

In `apps/clusters/feathre-core/base-apps/grafana/release.yaml`, after the existing `valuesFrom` entry that ends at line 29, add a fourth entry (note the index `[1]` — this is the *second* contactPoints element):

```yaml
    - kind: Secret
      name: grafana-discord-critical
      valuesKey: webhookUrl
      targetPath: alerting.contactpoints\.yaml.secret.contactPoints[1].receivers[0].settings.url
```

Then in the `contactpoints.yaml` block (currently lines 27465–27478), append a second contact point immediately after the existing `message:` line at 27478, at the same indentation as `- orgId: 1` on line 27469:

```yaml
            - orgId: 1
              name: discord-critical
              receivers:
                - uid: discord_webhook_critical
                  type: discord
                  settings:
                    url: "placeholder-overwritten-by-valuesFrom"
                    use_discord_username: true
                    title: '{{ `{{ template "discord.title" . }}` }}'
                    message: '{{ `{{ template "discord.message" . }}` }}'
```

- [ ] **Step 4: Add the child route**

Replace the `policies.yaml` block (lines 27501–27509) with:

```yaml
      policies.yaml:
        apiVersion: 1
        policies:
          - orgId: 1
            receiver: discord
            group_by: ['alertname']
            group_wait: 30s
            group_interval: 5m
            repeat_interval: 4h
            # group_by MUST stay ['alertname'] on every node: the discord.message
            # template prints severity/Rule/Dashboard from .CommonLabels, which is
            # only correct while a notification group holds one rule's instances.
            routes:
              - receiver: discord-critical
                object_matchers:
                  - ['severity', '=', 'critical']
                group_by: ['alertname']
                group_wait: 30s
                group_interval: 5m
                # 1h instead of the parent's 4h: a critical that is still firing
                # after an hour should re-announce itself.
                repeat_interval: 1h
                continue: false
```

- [ ] **Step 5: Render and verify the routing tree**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana | grep -c "discord-critical"
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana \
  | python3 -c "import sys,yaml; [print(yaml.safe_load(d)['spec']['values']['alerting']['policies.yaml']) for d in sys.stdin.read().split('\n---\n') if 'kind: HelmRelease' in d and 'name: grafana' in d]"
```

Expected: the first returns `≥3`. The second must print a parsed Python dict containing `'routes': [{'receiver': 'discord-critical', 'object_matchers': [['severity', '=', 'critical']], …}]` — **if it raises a YAML error or prints no `routes` key, do not commit.** That is the failure mode that drops all routing.

- [ ] **Step 6: Validate and commit**

```bash
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/grafana/
git commit -m "feat(grafana): route severity=critical to a second discord contact point"
```

**Rollback:** revert the commit. Grafana's provisioning is declarative — the child route and the second contact point disappear on the next reconcile, and the parent `discord` route resumes receiving everything.

---

### Task 3: Cap alloy-metrics ephemeral storage

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/alloy-metrics/release.yaml` (the `alloy.resources` block at :36–42)

**Interfaces:**
- Consumes: nothing
- Produces: a bounded WAL footprint on the worker root filesystem

Alloy's `prometheus.remote_write` WAL defaults to `truncate_frequency 2h` / `max_keepalive_time 8h`, so growth is bounded — but nothing today caps it, and the pod has no `ephemeral-storage` request or limit at all. This is the safe half of the WAL fix; see Appendix A for the half this plan deliberately skips.

- [ ] **Step 1: Add the limit**

In `apps/clusters/feathre-core/base-apps/alloy-metrics/release.yaml`, change the `resources` block from:

```yaml
      resources:
        requests:
          cpu: 100m
          memory: 512Mi
        limits:
          cpu: "1"
          memory: 2Gi
```

to:

```yaml
      resources:
        requests:
          cpu: 100m
          memory: 512Mi
          # The remote_write WAL lives on the container writable layer (no
          # volumeClaimTemplate on this StatefulSet). ~8h of ~350k series
          # bounded by Alloy's own truncate_frequency/max_keepalive_time
          # defaults; 4Gi is roughly 4x that, so a stuck remote_write is
          # evicted rather than eating the node's root filesystem.
          ephemeral-storage: 1Gi
        limits:
          cpu: "1"
          memory: 2Gi
          ephemeral-storage: 4Gi
```

- [ ] **Step 2: Render, validate, commit**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/alloy-metrics | grep -A2 "ephemeral-storage"
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/alloy-metrics/release.yaml
git commit -m "fix(alloy): cap alloy-metrics ephemeral storage for the remote_write wal"
```

Expected from the render: two `ephemeral-storage:` occurrences (`1Gi` under requests, `4Gi` under limits).

**Rollback:** revert. Note that this triggers a StatefulSet rolling restart of the 2 alloy-metrics pods (a resources change is a pod-template change) — each restart discards that replica's in-memory WAL, so expect a sub-minute gap in scraped metrics. Do it outside an active incident.

---

### Task 4: Merge PR 1 and verify (gate before PR 2)

**Files:** none (operational)

- [ ] **Step 1: Push and open the PR**

```bash
git pull --rebase origin main
git push -u origin feat/flux-notification-provider
gh pr create --title "feat(flux): add independent discord alert path and critical routing" --body "$(cat <<'EOF'
## Summary
- Adds a notification-controller Provider + Alert (eventSeverity: error) in the base-sources layer — the only alerting path that does not depend on Mimir/Grafana/Ceph
- Adds a severity=critical child route in the Grafana notification policy to a second Discord contact point
- Caps alloy-metrics ephemeral-storage so a stuck remote_write cannot eat a worker's root filesystem

Plan: docs/superpowers/plans/2026-08-03-alert-coverage-and-escalation.md (PR 1)

## Test plan
- [x] ./scripts/validate.sh passes
- [x] Rendered policies.yaml parses and contains the child route
- [ ] Merge, reconcile once, confirm Provider/Alert Ready and a test Flux error reaches Discord
EOF
)"
```

Merging is a human decision — do not merge automatically.

- [ ] **Step 2: Reconcile once**

```bash
flux reconcile kustomization base-sources --with-source
```

Do not repeat in a loop. `base-apps` (which carries the Grafana + Alloy changes) will pick up the same revision on its own 1m interval.

- [ ] **Step 3: Confirm the Provider and Alert are Ready**

```bash
kubectl -n flux-system get providers.notification.toolkit.fluxcd.io,alerts.notification.toolkit.fluxcd.io
```

Expected: `flux-discord` and `flux-errors` both listed with `READY=True`. A `READY=False` on the Provider with `failed to read token from secret` means the secret key is not named `address` — fix Task 1 Step 2.

- [ ] **Step 4: Prove the path end-to-end**

Make notification-controller emit one real `error` event without breaking anything:

```bash
kubectl -n flux-system annotate --overwrite gitrepository/flux-system \
  reconcile.fluxcd.io/requestedAt="$(date +%s)"
# then, in a scratch namespace, create a deliberately broken HelmRelease:
kubectl create ns alerttest
cat <<'YAML' | kubectl apply -f -
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: alert-smoketest
  namespace: alerttest
spec:
  interval: 1m
  chart:
    spec:
      chart: this-chart-does-not-exist
      sourceRef:
        kind: HelmRepository
        name: grafana-labs
        namespace: flux-system
YAML
```

Within ~2 minutes a message from username `flux` should appear in the DG-1 Discord channel naming `alert-smoketest` and the failing revision. Then clean up **immediately**:

```bash
kubectl delete ns alerttest
```

If nothing arrives: `kubectl -n flux-system logs deploy/notification-controller --tail=50 | grep -i discord`.

- [ ] **Step 5: Confirm the critical route took effect**

```bash
kubectl -n grafana rollout status deploy/grafana --timeout=180s
```

then, in Grafana UI → Alerting → Notification policies, confirm a child policy `severity = critical → discord-critical` exists under the default policy.

**Verified 2026-08-03 — the provisioning files are split across two objects, not one:**

| File | Lives in | Check |
|---|---|---|
| `contactpoints.yaml` | Secret `grafana-config-secret` (its only key) | `kubectl -n grafana get secret grafana-config-secret -o jsonpath='{.data.contactpoints\.yaml}' \| base64 -d \| grep -c discord-critical` → `2` |
| `policies.yaml`, `rules.yaml`, `templates.yaml` | ConfigMap `grafana` | `kubectl -n grafana get cm grafana -o jsonpath='{.data.policies\.yaml}' \| grep -c discord-critical` → `1` |

(The Secret named `grafana` holds only `admin-user`/`admin-password`/`ldap-toml` — do **not** grep it for `contactpoints`.)

The Deployment carries `checksum/config` and `checksum/secret` pod annotations, so a Helm upgrade that changes either object rolls the pods automatically; `rollout status` above is the confirmation, not a manual restart. Only if both objects show the new content **and** the pods did not roll should you `kubectl -n grafana rollout restart deploy/grafana`.

- [ ] **Step 6: Confirm the ephemeral-storage limit landed**

```bash
kubectl -n grafana get sts alloy-metrics \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="alloy")].resources}'
```

Expected: JSON containing `"ephemeral-storage":"4Gi"` under `limits` and `"1Gi"` under `requests`, and `kubectl -n grafana get pods -l app.kubernetes.io/instance=alloy-metrics` shows 2/2 Running on the new spec.

**Gate:** do not start PR 2 until Steps 3–6 all pass. **Rollback:** `git revert` the merge commit on `main`; one `flux reconcile kustomization base-sources --with-source` removes the Provider/Alert, and `base-apps` reverts the Grafana routing and the Alloy limit on its next interval.

---

# PR 2 — The four missing core alerts, plus PVC coverage for every volume

All rules are appended as **new groups at the tail of the `groups:` list**, immediately before the file's `resources:` block (currently line 28479). Each task's insertion point is the end of the previous task's insertion — no mid-file edits except Task 7, which is an in-place replacement.

### Task 5: Node health group — NotReady/Unreachable, kubelet down, clock drift

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml` (insert before line 28479 `    resources:`)

**Interfaces:**
- Consumes: nothing
- Produces: rule uids `node-not-ready`, `kubelet-down`, `node-clock-unsynchronised`, `node-clock-offset-high`

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/core-alert-coverage
```

- [ ] **Step 2: Insert the group**

Insert the following immediately **before** the line `    resources:` (currently 28479), i.e. as the new last element of `groups:`:

```yaml
          - orgId: 1
            name: node_health
            folder: Core Services
            interval: 60s
            rules:
              - uid: node-not-ready
                title: Node not Ready
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # kube_node_status_condition carries all 3 statuses for every
                      # condition (150 series = 10 nodes x 5 conditions x 3 statuses),
                      # so this always has data -> noDataState: OK is correct.
                      # status="false" is NotReady, status="unknown" is Unreachable
                      # (kubelet stopped reporting) - both are pages, one rule.
                      # Do NOT write `{status="true"} == 0`: the filtered value is 0,
                      # so a `gt 0` threshold would never fire.
                      expr: max by (node) (kube_node_status_condition{condition="Ready", status=~"false|unknown"})
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
                              - 0
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
                noDataState: OK
                execErrState: Alerting
                for: 5m
                annotations:
                  summary: "Node {{ `{{ $labels.node }}` }} is NotReady or Unreachable. Check: kubectl get node {{ `{{ $labels.node }}` }} -o wide; kubectl describe node {{ `{{ $labels.node }}` }} | tail -30"
                labels:
                  severity: critical
              - uid: kubelet-down
                title: Kubelet down
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # min by (node) keeps a real 0/1 value per node instead of
                      # filtering healthy nodes out of the result, so the series
                      # never disappears while the scrape pipeline is alive. Total
                      # pipeline loss is already covered by observability-pipeline-no-data.
                      expr: min by (node) (up{job="kubelet", metrics_path="/metrics"})
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
                              - 1
                            type: lt
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
                noDataState: OK
                execErrState: Alerting
                for: 5m
                annotations:
                  summary: "Kubelet on node {{ `{{ $labels.node }}` }} is not being scraped - the node is down, the kubelet is dead, or its serving cert expired. Check: talosctl -n {{ `{{ $labels.node }}` }} service kubelet status"
                labels:
                  severity: critical
              - uid: node-clock-unsynchronised
                title: Node clock not synchronised
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # machine.time.servers is /dev/ptp0 only (talos repo:
                      # clusters/feather-core/talos/patches/common/time-ptp.yaml) and
                      # Talos makes PTP and NTP mutually exclusive - there is NO NTP
                      # fallback. A live-migration onto a host without ptp_kvm leaves
                      # the clock free-running silently. node-exporter series carry
                      # instance (IP:9100), not node.
                      expr: min by (instance) (node_timex_sync_status)
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
                              - 1
                            type: lt
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
                noDataState: OK
                execErrState: Alerting
                for: 15m
                annotations:
                  summary: "Clock on {{ `{{ $labels.instance }}` }} is not synchronised - /dev/ptp0 is the only time source and there is no NTP fallback. Check: talosctl -n <node> read /sys/class/ptp/ptp0/clock_name; talosctl -n <node> dmesg | grep -i ptp"
                labels:
                  severity: warning
              - uid: node-clock-offset-high
                title: Node clock offset high
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # Current cluster-wide max is 5.7 microseconds; 50ms is ~4
                      # orders of magnitude above that and well below the point
                      # where Galera/etcd/TLS start misbehaving.
                      expr: max by (instance) (abs(node_timex_offset_seconds))
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
                              - 0.05
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
                noDataState: OK
                execErrState: Alerting
                for: 15m
                annotations:
                  summary: "Clock offset on {{ `{{ $labels.instance }}` }} exceeds 50ms. Galera certification, etcd leases and TLS validity all assume a tight cluster clock. Check: talosctl -n <node> time"
                labels:
                  severity: warning
```

- [ ] **Step 3: Verify the render**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana \
  | grep -cE "uid: (node-not-ready|kubelet-down|node-clock-unsynchronised|node-clock-offset-high)"
```

Expected: `4`.

- [ ] **Step 4: Commit**

```bash
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "feat(monitoring): alert on node NotReady, kubelet down and clock drift"
```

---

### Task 6: Certificate expiry group

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml` (insert before `    resources:`, i.e. after Task 5's group)

**Interfaces:**
- Consumes: Task 5's insertion point
- Produces: rule uids `certificate-expiring-soon`, `certificate-expiring-critical`

- [ ] **Step 1: Insert the group** (again immediately before the `    resources:` line, now after `node_health`)

```yaml
          - orgId: 1
            name: certificates
            folder: Core Services
            interval: 60s
            rules:
              - uid: certificate-expiring-soon
                title: Certificate expiring within 14 days
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # cert-manager's own metric. The certificate's namespace is in
                      # exported_namespace (namespace= is cert-manager, where the
                      # controller runs). Lowest value across the 20 series today is
                      # ~72.7 days, so this will not fire on merge.
                      # noDataState MUST stay OK: if a Certificate is deleted its
                      # series vanishes, and "no certs" is not an emergency.
                      expr: min by (exported_namespace, name) ((certmanager_certificate_expiration_timestamp_seconds - time()) / 86400)
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
                              - 14
                            type: lt
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
                noDataState: OK
                execErrState: Alerting
                for: 1h
                annotations:
                  summary: "Certificate {{ `{{ $labels.name }}` }} in namespace {{ `{{ $labels.exported_namespace }}` }} expires in under 14 days and has not renewed. Check: kubectl describe certificate {{ `{{ $labels.name }}` }} -n {{ `{{ $labels.exported_namespace }}` }}; kubectl get certificaterequest -n {{ `{{ $labels.exported_namespace }}` }}"
                labels:
                  severity: warning
              - uid: certificate-expiring-critical
                title: Certificate expiring within 3 days
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      expr: min by (exported_namespace, name) ((certmanager_certificate_expiration_timestamp_seconds - time()) / 86400)
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
                              - 3
                            type: lt
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
                noDataState: OK
                execErrState: Alerting
                for: 15m
                annotations:
                  summary: "Certificate {{ `{{ $labels.name }}` }} in namespace {{ `{{ $labels.exported_namespace }}` }} expires in under 3 days. Renewal has failed. Check: kubectl describe certificate {{ `{{ $labels.name }}` }} -n {{ `{{ $labels.exported_namespace }}` }}; kubectl -n cert-manager logs deploy/cert-manager --tail=100"
                labels:
                  severity: critical
```

- [ ] **Step 2: Verify, validate, commit**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana | grep -cE "uid: certificate-expiring-(soon|critical)"
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "feat(monitoring): alert on cert-manager certificate expiry"
```

Expected from the grep: `2`.

---

### Task 7: Generalise the PVC fill alerts to every PVC

> This is the one **in-place replacement** in PR 2. It deletes two rule uids and creates two new ones; Grafana's provisioning removes rules that are no longer in the file, so the old MariaDB-scoped rules disappear cleanly.

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml` (rules at :27804–27856 and :27857–27909, inside the `storage` group that starts at :27799)

**Interfaces:**
- Consumes: nothing
- Produces: rule uids `pvc-usage-high`, `pvc-usage-critical` (replacing `mariadb-galera-pvc-usage-high` / `mariadb-galera-pvc-usage-critical`)

**Live impact:** all three Galera PVCs are at 76.6 % right now. Nothing fires on merge, but the warning threshold (80 %) is close — that is the intended behaviour, and it is exactly the alert theme `ceph-capacity-reclamation-and-retention` needs.

- [ ] **Step 1: Replace both rules**

For `mariadb-galera-pvc-usage-high` (starting at line 27804), change:

- line 27804: `              - uid: mariadb-galera-pvc-usage-high` → `              - uid: pvc-usage-high`
- line 27805: `                title: MariaDB Galera PVC usage high` → `                title: PVC usage high`
- line 27815 (the `expr:`): replace the namespace/PVC-scoped selector with the unscoped one, and add the explanatory comment above it:

```yaml
                      # Generalised from the old mariadb-galera-only scoping: 36
                      # kubelet_volume_stats_* series exist cluster-wide and only 3
                      # were covered. Highest non-Galera PVC today is ollama at 36.6%.
                      expr: (kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes) * 100
```

- line **27853** (`summary:` — re-verified 2026-08-03; the audit's 27851 was off by two) → replace with:

```yaml
                  summary: "PVC {{ `{{ $labels.persistentvolumeclaim }}` }} in namespace {{ `{{ $labels.namespace }}` }} (node {{ `{{ $labels.node }}` }}) is above 80% capacity. Check: kubectl get pvc -n {{ `{{ $labels.namespace }}` }} {{ `{{ $labels.persistentvolumeclaim }}` }}"
```

- line **27854** (`dashboard_url:`) → replace the Galera-specific dashboard with the storage overview:

```yaml
                  dashboard_url: "https://grafana.apps.onelite.feather/d/mrjpdvx7k/kubernetes-persistent-volumes-custom"
```

Apply the identical four edits to `mariadb-galera-pvc-usage-critical` (starting at line 27857): uid → `pvc-usage-critical`, title → `PVC almost full`, the same `expr:` (line 27868), the summary at line **27906** with "above 90% capacity and at risk of filling up", and the same `dashboard_url` at line **27907**. Leave every `threshold`, `for:`, `noDataState: OK`, `execErrState: Alerting` and `severity:` value exactly as it is.

> **Dashboard uid — resolved 2026-08-03, do not re-derive.** The PV dashboard is provisioned by `gnetId: 15600` (release.yaml:9019, key `persistent-volumes`), so its uid is **not** greppable in this repo — the audit's earlier `919b92a8…` guess was wrong and a `grep` for it always returns 0. Queried live against this Grafana: the only match is **uid `mrjpdvx7k`**, title `kubernetes-persistent-volumes-custom`, folder `Kubernetes`. Re-confirm before committing with:
>
> ```bash
> curl -sf -u admin:"$(kubectl -n grafana get secret grafana-admin -o jsonpath='{.data.adminPassword}' | base64 -d)" \
>   'https://grafana.apps.onelite.feather/api/search?query=persistent' | python3 -m json.tool | grep -E '"uid"|"title"'
> ```
>
> Expected: `"uid": "mrjpdvx7k"`. If it returns nothing, drop both `dashboard_url` lines entirely rather than shipping a 404 — the annotation is optional and the Discord template skips it when absent.

- [ ] **Step 2: Verify the replacement is complete**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana | grep -c "mariadb-galera-pvc-usage"
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana | grep -cE "uid: pvc-usage-(high|critical)"
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana | grep -c 'persistentvolumeclaim=~"storage-mariadb-galera'
```

Expected: `0`, `2`, `0`.

- [ ] **Step 3: Validate and commit**

```bash
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "feat(monitoring): generalise pvc fill alerts to every persistent volume"
```

---

### Task 8: Merge PR 2 and verify (gate before PR 4)

- [ ] **Step 1: Push and open the PR**

```bash
git pull --rebase origin main
git push -u origin feat/core-alert-coverage
gh pr create --title "feat(monitoring): add core node, kubelet, certificate and pvc alerts" --body "$(cat <<'EOF'
## Summary
- Adds node NotReady/Unreachable, kubelet down, and two node clock-drift rules (no NTP fallback: machine.time.servers is /dev/ptp0 only)
- Adds cert-manager expiry rules at 14d (warning) and 3d (critical)
- Generalises the two MariaDB-scoped PVC fill rules to every PVC in the cluster (36 volumes were being watched as 3)

Plan: docs/superpowers/plans/2026-08-03-alert-coverage-and-escalation.md (PR 2)

## Test plan
- [x] ./scripts/validate.sh passes
- [x] Every expression executed live against the mimir datasource; none fires at current values
- [ ] Merge, reconcile once, confirm all 8 rules present and Normal in Grafana
EOF
)"
```

- [ ] **Step 2: Reconcile once and confirm the rules loaded**

```bash
flux reconcile kustomization base-apps --with-source
kubectl -n grafana rollout status deploy/grafana --timeout=180s
```

Then verify Grafana actually parsed them (a provisioning error rejects the **whole file**, not one rule):

```bash
kubectl -n grafana logs deploy/grafana --tail=200 | grep -iE "provisioning|alert rule" | grep -iE "error|failed" || echo "no provisioning errors"
```

Expected: `no provisioning errors`.

- [ ] **Step 3: Confirm rule count and state**

In Grafana UI → Alerting → Alert rules, or via the MCP `alerting_manage_rules(operation=list)`: expect **23** Grafana-managed rules total. The arithmetic (corrected — the audit's "25" double-counted the two renamed rules as new): 17 existing − 2 replaced + 2 renamed + **6** genuinely new (4 from Task 5 + 2 from Task 6) = **23**.

Confirm the same number from git before you merge, so a mis-paste is caught locally:

```bash
grep -c "^              - uid: " apps/clusters/feathre-core/base-apps/grafana/release.yaml
```

Expected: `23` (baseline before this PR is `17`).

All eight of `node-not-ready`, `kubelet-down`, `node-clock-unsynchronised`, `node-clock-offset-high`, `certificate-expiring-soon`, `certificate-expiring-critical`, `pvc-usage-high`, `pvc-usage-critical` must be present and in state `Normal`.

- [ ] **Step 4: Prove one of them can actually fire**

Pick the least invasive: `kubelet-down`. Do **not** stop a kubelet. Instead confirm the rule's query returns data and the threshold logic is right by evaluating it in Grafana Explore against `mimir`:

```
min by (node) (up{job="kubelet", metrics_path="/metrics"})
```

Expected: 10 series, all value `1`. A rule whose query returns 10 healthy series and whose condition is `lt 1` is proven wired; forcing an actual node failure is not worth the blast radius.

**Gate:** all of Steps 2–4 must pass before PR 4 (deleting the inert `PrometheusRule` CRs). **Rollback:** `git revert` the merge commit; Grafana's provisioning restores the previous rule set on the next Deployment reconcile + restart.

---

# PR 3 — Ingestion-limit alerts and the stale Mimir TODO

### Task 9: Ingestion limits group

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml` (insert before `    resources:`, after PR 2's groups)

**Interfaces:**
- Consumes: PR 2 merged (same file; rebase onto it to avoid a conflict at the tail)
- Produces: rule uids `mimir-series-limit-headroom`, `mimir-samples-discarded`, `loki-stream-limit-headroom`, `loki-samples-discarded`

> **No `dashboard_url:` on any rule in this group — deliberate.** Checked live 2026-08-03: `GET /api/search?query=mimir` on this Grafana returns **zero** dashboards, so the `/d/mimir-writes` link the audit suggested would be a 404 in every Discord notification. The `discord.message` template skips the Dashboard line when the annotation is absent (release.yaml:27493), so omitting it is clean. If a Mimir dashboard is provisioned later, add the annotation then.

- [ ] **Step 1: Branch from post-PR-2 `main`**

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/ingestion-limit-alerts
```

- [ ] **Step 2: Insert the group**

```yaml
          - orgId: 1
            name: ingestion_limits
            folder: Observability
            interval: 60s
            rules:
              - uid: mimir-series-limit-headroom
                title: Mimir series limit headroom low
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # Leading indicator. The 600000 divisor MUST be kept in sync with
                      # limits.max_global_series_per_user in
                      # apps/clusters/feathre-core/monitoring/mimir/release.yaml:57 -
                      # cortex_limits_overrides is NOT exported by this deployment, so
                      # the ceiling cannot be read from a metric. Current value 345405
                      # = 0.576, so this sits well below the 0.8 threshold today.
                      # Hitting this limit has caused two production incidents (2026-07-13):
                      # rejected samples back the remote_write queue up and take
                      # UNRELATED metrics down with them.
                      expr: max(cortex_ingester_memory_series) / 600000
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
                              - 0.8
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
                noDataState: OK
                execErrState: Alerting
                for: 15m
                annotations:
                  summary: "Mimir is above 80% of max_global_series_per_user (600k). New series will start being rejected with err-mimir-max-series-per-user, which backs up remote_write and drops unrelated metrics. Check: max(cortex_ingester_memory_series); topk(20, count by (__name__)({__name__=~\".+\"}))"
                labels:
                  severity: warning
              - uid: mimir-samples-discarded
                title: Mimir is discarding samples
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # Lagging indicator - catches every discard reason at once
                      # (series limit, ingestion rate, sample-too-old, out-of-order).
                      # noDataState MUST be OK: cortex_discarded_samples_total has no
                      # series at all while nothing is discarded. Using Alerting here
                      # would reproduce the bug fixed in commit 866a690.
                      # Measured: rate is flat 0 over the last 24h.
                      expr: sum(rate(cortex_discarded_samples_total[5m]))
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
                              - 0
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
                noDataState: OK
                execErrState: Alerting
                for: 10m
                annotations:
                  summary: "Mimir is discarding samples. This is silent by design (4xx + retry backlog) and takes unrelated metrics down with it. Check: sum by (reason) (rate(cortex_discarded_samples_total[5m]))"
                labels:
                  severity: warning
              - uid: loki-stream-limit-headroom
                title: Loki stream limit headroom low
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # loki_ingester_memory_streams counts each stream once per
                      # replica, so divide by replication_factor: 3
                      # (apps/clusters/feathre-core/monitoring/loki/release.yaml:48).
                      # The 5000 divisor tracks limits_config.max_global_streams_per_user
                      # at loki/release.yaml:68. Currently 418/3/5000 = 0.028.
                      expr: (sum(loki_ingester_memory_streams) / 3) / 5000
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
                              - 0.8
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
                noDataState: OK
                execErrState: Alerting
                for: 15m
                annotations:
                  summary: "Loki is above 80% of max_global_streams_per_user (5000). New streams will be rejected. Check: sum(loki_ingester_memory_streams); topk(20, count by (namespace)({namespace=~\".+\"}))"
                labels:
                  severity: warning
              - uid: loki-samples-discarded
                title: Loki is discarding log lines
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # Same noDataState:OK reasoning as mimir-samples-discarded.
                      # Measured: flat 0 over the last 24h (only a
                      # greater_than_max_sample_age series exists, counter frozen).
                      expr: sum(rate(loki_discarded_samples_total[5m]))
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
                              - 0
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
                noDataState: OK
                execErrState: Alerting
                for: 10m
                annotations:
                  summary: "Loki is discarding log lines. Check: sum by (reason) (rate(loki_discarded_samples_total[5m]))"
                labels:
                  severity: warning
```

- [ ] **Step 3: Verify and commit**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana \
  | grep -cE "uid: (mimir-series-limit-headroom|mimir-samples-discarded|loki-stream-limit-headroom|loki-samples-discarded)"
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "feat(monitoring): alert on mimir and loki ingestion limits"
```

Expected from the grep: `4`.

---

### Task 10: Resolve the stale Mimir limits TODO

**Files:**
- Modify: `apps/clusters/feathre-core/monitoring/mimir/release.yaml` (comment block at :45–57)

**Interfaces:**
- Consumes: Task 9 (the alert that replaces the manual check the comment asks for)
- Produces: a comment that matches reality

The comment at :51–56 says *"Revert to 400000 once `cortex_ingester_memory_series` settles back near its ~275k baseline."* Measured today: **345,405** — 26 % above that baseline and rising since 2026-07-13. The "temporary" 600k is permanent. This is a **comment-only change** — the `max_global_series_per_user: 600000` value on line 57 does **not** change.

- [ ] **Step 1: Replace lines 51–56**

Replace:

```yaml
          # Temporarily raised to 600k on 2026-07-13: the alloy-metrics
          # clustering bugfix (both replicas were scraping the full target
          # set, undeduplicated, for ~2.5h before the fix) filled the
          # ingesters to exactly 400k with duplicate series that hadn't
          # aged out yet. Revert to 400000 once cortex_ingester_memory_series
          # settles back near its ~275k baseline.
```

with:

```yaml
          # Raised to 600k on 2026-07-13 after the alloy-metrics clustering
          # bugfix (both replicas were scraping the full target set,
          # undeduplicated, for ~2.5h) filled the ingesters to exactly 400k.
          # The "revert to 400000 once it settles near ~275k" plan is CLOSED as
          # not-happening: measured 2026-08-03, max(cortex_ingester_memory_series)
          # is 345k - the duplicates aged out long ago and this is the real
          # steady-state series count. 600000 is the deliberate permanent ceiling.
          # Headroom is now watched by the Grafana rule
          # `mimir-series-limit-headroom` (fires at 80% = 480k), so this no
          # longer needs a human to remember to check it. If you change the
          # number below, change the divisor in that rule too.
```

- [ ] **Step 2: Verify the value did not change, then commit**

```bash
grep -n "max_global_series_per_user" apps/clusters/feathre-core/monitoring/mimir/release.yaml
./scripts/validate.sh
git add apps/clusters/feathre-core/monitoring/mimir/release.yaml
git commit -m "docs(mimir): close the stale 400k series-limit revert todo"
```

Expected: exactly one line, `max_global_series_per_user: 600000`.

> **Note (corrected):** this touches the `mimir` HelmRelease, whose Kustomization layer is `monitoring` — **not** `base-apps`. `monitoring` is `wait: false` (clusters/feather-core/monitoring.yaml:19), so nothing downstream can stall on it.
>
> Because `kustomize` re-serialises YAML and drops comments, the **rendered HelmRelease object is byte-identical** to what is already applied. Flux will therefore apply a no-op and **not** trigger a Helm upgrade at all. Two consequences the executor must expect:
> 1. There is no in-cluster signal that this change landed — the only verification is in git. Do not go looking for a new HelmRelease revision.
> 2. `mimir` stays `Ready=False` (Helm upgrade timeout, 51 days, unrelated). This change neither fixes nor worsens it. Fixing it belongs to `flux-release-control-and-convergence`.

---

### Task 11: Merge PR 3 and verify

```bash
git pull --rebase origin main
git push -u origin feat/ingestion-limit-alerts
gh pr create --title "feat(monitoring): alert on mimir and loki ingestion limits" --body "$(cat <<'EOF'
## Summary
- Adds a leading indicator (series/stream headroom > 80%) and a lagging indicator (discarded samples rate > 0) for both Mimir and Loki — the failure mode behind two production incidents on 2026-07-13, still unalerted
- Closes the stale "revert to 400000" TODO in mimir/release.yaml: measured usage is 345k, not the 275k baseline it waits for

Plan: docs/superpowers/plans/2026-08-03-alert-coverage-and-escalation.md (PR 3)

## Test plan
- [x] ./scripts/validate.sh passes
- [x] All four rates/ratios measured live over 24h; none crosses its threshold today
- [ ] Merge, reconcile once, confirm 4 new rules Normal and max_global_series_per_user still 600000
EOF
)"
```

- [ ] **Verification after merge**

```bash
flux reconcile kustomization base-apps --with-source
kubectl -n grafana rollout status deploy/grafana --timeout=180s
kubectl -n grafana logs deploy/grafana --tail=200 | grep -iE "provisioning.*(error|failed)" || echo "no provisioning errors"
kubectl -n grafana get cm mimir-config -o yaml | grep max_global_series_per_user
grep -c "^              - uid: " apps/clusters/feathre-core/base-apps/grafana/release.yaml
```

Expected: `no provisioning errors`; `max_global_series_per_user: 600000` unchanged; the uid count is now `27` (23 after PR 2 + 4 here). In the Grafana UI the four new rules are `Normal`.

Do **not** also `flux reconcile kustomization monitoring` for the Task 10 comment change — it renders to a no-op object (see the note under Task 10) and reconciling the `monitoring` layer only re-attempts the already-failing `mimir` Helm upgrade for no benefit. The layer will pick the revision up on its own 1m interval.

**Rollback:** revert the merge commit, then one `flux reconcile kustomization base-apps --with-source`. Grafana's provisioning is declarative — the four rules disappear on the next Deployment roll. Nothing stateful.

---

# PR 4 — Delete the 35 inert PrometheusRule CRs

> **Do not merge this before PR 2 is merged and verified.** These CRs are the reason someone might believe node/PVC/kubelet alerting exists. Deleting them before the replacement lands removes documentation of the gap without closing it.

### Task 12: Turn off `defaultRules` in kube-prometheus-stack

**Files:**
- Modify: `infrastructure/clusters/feather-core/base-controllers/kube-prometheus-stack/release.yaml`

**Interfaces:**
- Consumes: PR 2 merged and healthy
- Produces: 35 fewer `PrometheusRule` objects; 2 remain (`grafana/loki-loki-alerts`, `grafana/loki-loki-rules`, chart-owned — deliberately out of scope)

- [ ] **Step 1: Branch and edit**

```bash
git checkout main && git pull --rebase origin main
git checkout -b chore/remove-inert-prometheusrules
```

> ⚠️ **This triggers a Helm upgrade of `kube-prometheus-stack`, which lives in the `base-controllers` layer — and `base-controllers` is `wait: true` (clusters/feather-core/base-controllers.yaml:17, timeout 15m).** Three layers depend on it: `base-configs`, `controllers` and `rook`. If the upgrade hangs or fails, all three stall with "dependency not ready" until it is resolved. The change itself only removes 35 stateless `PrometheusRule` objects, so a hang is unlikely — but if `flux get kustomizations -A` shows `base-controllers` stuck `Reconciling` past 15 minutes, revert the commit immediately rather than waiting it out.

In `infrastructure/clusters/feather-core/base-controllers/kube-prometheus-stack/release.yaml`, insert this block immediately after `    fullnameOverride: kube-prometheus-stack` (**line 8** — re-verified 2026-08-03; the audit's "line 7" is `  values:`) and before `    grafana:` (line 9):

```yaml
    # 35 PrometheusRule CRs with nothing to evaluate them: prometheus.enabled is
    # false (below) and no Prometheus/PrometheusAgent exists. Confirmed 2026-08-03
    # that the deployed Mimir ruler holds zero rule groups, and that its
    # Alertmanager fallback config has a receiver with no integrations - so even
    # loading them there would drop every alert silently. Their presence made the
    # cluster look covered for KubeNodeNotReady / KubeletDown /
    # KubePersistentVolumeFillingUp when it was not. Equivalent coverage now lives
    # as Grafana rules in apps/clusters/feathre-core/base-apps/grafana/release.yaml
    # (groups: node_health, certificates, storage).
    # The recording rules in this set (k8s.rules.*, node.rules, kubelet.rules)
    # were equally unevaluated, so the dashboards depending on them were already
    # broken - this changes nothing for them.
    defaultRules:
      create: false
```

- [ ] **Step 2: Verify the render**

```bash
kubectl kustomize infrastructure/clusters/feather-core/base-controllers/kube-prometheus-stack | grep -A2 "defaultRules"
./scripts/validate.sh
git add infrastructure/clusters/feather-core/base-controllers/kube-prometheus-stack/release.yaml
git commit -m "chore(monitoring): stop generating unevaluated prometheusrule crs"
```

Expected: `defaultRules:` / `create: false` in the rendered HelmRelease values.

- [ ] **Step 3: Merge, reconcile once, verify**

```bash
flux reconcile kustomization base-controllers --with-source
flux get kustomizations -A
kubectl get prometheusrule -A --no-headers | wc -l
kubectl get prometheusrule -A --no-headers | awk '{print $1, $2}'
```

Expected: `base-controllers`, `base-configs`, `controllers` and `rook` all `READY=True` at the new revision; then `2`, and the two rows are `grafana loki-loki-alerts` and `grafana loki-loki-rules`. Baseline before the change is `37`.

Confirm nothing else broke:

```bash
flux get helmreleases -n monitoring
kubectl -n monitoring get pods
```

Expected: `kube-prometheus-stack` `READY=True`; kube-state-metrics, node-exporter and the operator all Running.

If `base-controllers` is still `Reconciling` after 15 minutes, or any of its three dependents reports "dependency not ready", stop and roll back — do not reconcile again in a loop.

**Rollback:** revert the commit. `defaultRules.create` returns to its chart default (`true`) and the 35 CRs are recreated by the next Helm upgrade — they are stateless chart output, nothing is lost.

---

# PR 5 — An external observer, so that silence becomes an alarm

> **Decision gate DG-3 must be resolved before starting.** This task assumes healthchecks.io; adapt the URL and the grace period if another provider is chosen.
> **Requires a credential:** the healthchecks.io ping URL (it is a bearer secret — anyone with it can suppress your alarm).

Every signal in this cluster originates inside it. Grafana's alert evaluation, its state database (CNPG on Ceph), Mimir, Loki and uptime-kuma all live on the same 3-node Ceph and the same 4 workers. A failure that takes Grafana itself down produces no notification and looks identical to a healthy cluster. A heartbeat inverts that: the external service alarms on *absence*.

### Task 13: Create the heartbeat CronJob

**Files:**
- Create: `apps/base/heartbeat/namespace.yaml`
- Create: `apps/base/heartbeat/cronjob.yaml`
- Create: `apps/base/heartbeat/kustomization.yaml`
- Create: `apps/clusters/feathre-core/base-apps/heartbeat/kustomization.yaml`
- Create: `apps/clusters/feathre-core/base-apps/heartbeat/heartbeat.sops.env`
- Modify: `apps/clusters/feathre-core/base-apps/kustomization.yaml`

**Interfaces:**
- Consumes: DG-3 resolved, a ping URL in hand
- Produces: a 5-minute outbound ping; the external service alarms if it stops

- [ ] **Step 1: Register the check externally**

Create a check at the DG-3 provider with **period 5 minutes, grace 15 minutes**. (Grace must exceed one Ceph/node hiccup or you will get false pages; 15 minutes tolerates two missed pings.) Copy its ping URL.

- [ ] **Step 2: Branch and create the base**

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/external-heartbeat
mkdir -p apps/base/heartbeat apps/clusters/feathre-core/base-apps/heartbeat
```

`apps/base/heartbeat/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: heartbeat
```

`apps/base/heartbeat/cronjob.yaml`:

```yaml
# Deliberately the dumbest possible component: no storage, no database, no
# dependency on Mimir/Loki/Grafana/Ceph. If this stops pinging, the external
# monitor alarms - which is the only alarm in this cluster that survives the
# cluster. Do NOT add health-checking logic here; "the scheduler still runs and
# a pod can still reach the internet" is exactly the signal we want, and any
# extra condition only adds ways for the heartbeat to fail while the cluster is fine.
apiVersion: batch/v1
kind: CronJob
metadata:
  name: heartbeat
  namespace: heartbeat
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 3
  startingDeadlineSeconds: 120
  jobTemplate:
    spec:
      backoffLimit: 2
      activeDeadlineSeconds: 60
      template:
        metadata:
          labels:
            logs.onelitefeather.net/env: prod
        spec:
          restartPolicy: OnFailure
          automountServiceAccountToken: false
          securityContext:
            runAsNonRoot: true
            runAsUser: 65534
            runAsGroup: 65534
            seccompProfile:
              type: RuntimeDefault
          containers:
            - name: ping
              image: curlimages/curl:8.11.1
              imagePullPolicy: IfNotPresent
              command:
                - /bin/sh
                - -c
                - 'curl -fsS -m 10 --retry 3 --retry-delay 5 "$HEARTBEAT_URL" > /dev/null'
              envFrom:
                - secretRef:
                    name: heartbeat-url
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities:
                  drop: ["ALL"]
              resources:
                requests:
                  cpu: 10m
                  memory: 16Mi
                limits:
                  cpu: 100m
                  memory: 64Mi
```

`apps/base/heartbeat/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: heartbeat
resources:
  - namespace.yaml
  - cronjob.yaml
```

- [ ] **Step 3: Create the overlay and the secret**

```bash
printf 'HEARTBEAT_URL=REPLACE_ME\n' > apps/clusters/feathre-core/base-apps/heartbeat/heartbeat.sops.env
$EDITOR apps/clusters/feathre-core/base-apps/heartbeat/heartbeat.sops.env
sops -e -i apps/clusters/feathre-core/base-apps/heartbeat/heartbeat.sops.env
grep -c '^HEARTBEAT_URL=ENC\[' apps/clusters/feathre-core/base-apps/heartbeat/heartbeat.sops.env
```

Expected: `1`.

`apps/clusters/feathre-core/base-apps/heartbeat/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: heartbeat
generatorOptions:
  disableNameSuffixHash: true
resources:
  - ../../../../../apps/base/heartbeat

secretGenerator:
  - name: heartbeat-url
    envs:
      - heartbeat.sops.env
```

Add `  - heartbeat` to the `resources:` list in `apps/clusters/feathre-core/base-apps/kustomization.yaml` (position does not matter; put it after `alloy-receiver`).

> **`disableNameSuffixHash` consequence:** if the ping URL is ever rotated, the Secret's contents change but nothing rolls. That is fine here — a `CronJob` reads the Secret at Job creation, so the *next* scheduled run picks up the new value within 5 minutes. No `rollout restart` needed (and there is no Deployment to restart).

- [ ] **Step 4: Verify and commit**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/heartbeat | grep -E "kind: (CronJob|Namespace|Secret)"
./scripts/validate.sh
git add apps/base/heartbeat apps/clusters/feathre-core/base-apps/heartbeat apps/clusters/feathre-core/base-apps/kustomization.yaml
git commit -m "feat(monitoring): add external heartbeat cronjob"
```

Expected: all three kinds present.

---

### Task 14: Merge PR 5 and prove the heartbeat actually alarms

```bash
git pull --rebase origin main
git push -u origin feat/external-heartbeat
gh pr create --title "feat(monitoring): add an external heartbeat so silence becomes an alarm" --body "$(cat <<'EOF'
## Summary
- Adds a 5-minute CronJob that pings an external dead-man's-switch. Every existing signal originates inside the cluster it monitors; this one alarms on absence.
- No storage, no DB, no dependency on Mimir/Loki/Grafana/Ceph.

Plan: docs/superpowers/plans/2026-08-03-alert-coverage-and-escalation.md (PR 5)

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] Merge, confirm a successful Job within 5 minutes and a green check externally
- [ ] Suspend the CronJob for 20 minutes and confirm the external alarm fires, then unsuspend
EOF
)"
```

- [ ] **Step 1: Reconcile and confirm the first ping**

```bash
flux reconcile kustomization base-apps --with-source
kubectl -n heartbeat get cronjob heartbeat
# wait for the next 5-minute boundary, then:
kubectl -n heartbeat get jobs
kubectl -n heartbeat logs job/$(kubectl -n heartbeat get jobs -o jsonpath='{.items[0].metadata.name}')
```

Expected: one Job `COMPLETIONS 1/1`; empty log output (curl is silent on success — a non-empty log means an HTTP error). The external check shows "up" with a recent ping.

- [ ] **Step 2: Prove the alarm side (this is the whole point — do not skip it)**

A bare `kubectl patch` does **not** work here: `base-apps` reconciles every 1m and reverts the drift, so the CronJob un-suspends long before the 15m grace expires. Suspend the Flux layer first, then the CronJob:

```bash
flux suspend kustomization base-apps          # stops Flux reverting the patch
kubectl -n heartbeat patch cronjob heartbeat -p '{"spec":{"suspend":true}}'
```

> ⚠️ **`flux suspend kustomization base-apps` freezes reconciliation of every app in that layer for the duration of the test** (grafana, harbor, outline, plane, the alloy instances, …). Nothing is deleted and running workloads are untouched — only new commits stop being applied. Do this in a quiet window, set a timer, and **do not** leave it suspended: a suspended layer is invisible to `flux-core-layer-not-ready`, so you would be blind for exactly as long as you forget.

Wait past the grace period (20 minutes for a 5m/15m check). Confirm the external provider notified you on whatever channel you configured. Then undo **in this order**:

```bash
kubectl -n heartbeat patch cronjob heartbeat -p '{"spec":{"suspend":false}}'
flux resume kustomization base-apps
kubectl get kustomization -n flux-system base-apps -o jsonpath='{.spec.suspend}{"\n"}'   # expect: empty/false
kubectl -n heartbeat get cronjob heartbeat -o jsonpath='{.spec.suspend}{"\n"}'           # expect: false
```

Confirm the check returns to "up" within 5 minutes, and that `flux get kustomizations -A` shows `base-apps` `READY=True` again.

> If you are not willing to suspend the layer, the fallback is weaker but honest: ask the provider to send a test alert from its own UI, confirm it reaches you, and accept the ping loop as proven by Step 1. Record in the PR which of the two you actually did.

**Rollback:** revert the merge commit; the namespace, CronJob and Secret are pruned. Delete the external check.

---

# PR 6 — Ship the Kubernetes API audit log

> **TWO REPOS.** Task 15/16 are in the **GitOps repo** (this one). Task 17 is in the **Talos repo** at `/mnt/projects/lab/talos-cluster` (remote `TheMeinerLP/FeatherCore`).
> **Task 17 applies machine config to all three control-plane nodes.** `--audit-policy-file` changes take effect on apiserver restart; Talos restarts the static pod. Do this one node at a time.
> **Hard prerequisite P1:** Ceph must be out of the nearfull window before this merges — the audit log adds sustained log volume to Loki, which writes to that same Ceph.

The apiserver already audits correctly (`--audit-log-path=/var/log/audit/kube/kube-apiserver.log`, maxage 30 / maxbackup 10 / maxsize 100). Nothing collects it. `/var/log` lives on Talos's `EPHEMERAL` partition, so the entire trail is destroyed by the next node reset, reinstall, or upgrade — including the upgrades this cluster is about to do.

### Task 15: Add an `alloy-audit` DaemonSet on the control-plane nodes

**Files:**
- Create: `apps/base/alloy-audit/release.yaml`
- Create: `apps/base/alloy-audit/kustomization.yaml`
- Create: `apps/clusters/feathre-core/base-apps/alloy-audit/release.yaml`
- Create: `apps/clusters/feathre-core/base-apps/alloy-audit/kustomization.yaml`
- Modify: `apps/clusters/feathre-core/base-apps/kustomization.yaml`

**Interfaces:**
- Consumes: nothing
- Produces: audit events in Loki under `{job="kube-audit"}`

**Why the `monitoring` namespace and not `grafana`:** the `monitoring` namespace already carries `pod-security.kubernetes.io/enforce: privileged` (re-verified 2026-08-03: `kubectl get ns monitoring -o jsonpath='{.metadata.labels}'` shows `enforce`/`audit`/`warn: privileged`). The `grafana` namespace has no PSA labels, so it falls back to the cluster default `enforce: baseline` — and **Baseline forbids hostPath volumes**. Putting this in `grafana` would be silently rejected at admission.

> ⚠️ **This adds a new HelmRelease to `base-apps`, which is `wait: true` (clusters/feather-core/base-apps.yaml, timeout 15m0s), and the `apps` layer `dependsOn: base-apps`.** helm-controller waits for the DaemonSet to become ready; if `alloy-audit` cannot roll out — bad Alloy config, missing hostPath, a toleration typo leaving pods `Pending` — the HelmRelease never goes Ready, `base-apps` never goes Ready, and **every workload in the `apps` layer stops reconciling**. Running pods keep running; new commits stop landing.
>
> If that happens, do not debug in place with the layer stalled. Revert and unstick first:
>
> ```bash
> flux get kustomizations -A                       # base-apps Reconciling, apps "dependency not ready"
> git revert <merge-commit> && git push
> flux reconcile kustomization base-apps --with-source
> ```
>
> Then reproduce the failure on a scratch branch. `install.remediation.retries: 0` in the base is deliberate: it fails fast instead of retrying for 45 minutes.

- [ ] **Step 1: Branch and create the base**

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/ship-api-audit-log
mkdir -p apps/base/alloy-audit apps/clusters/feathre-core/base-apps/alloy-audit
```

`apps/base/alloy-audit/release.yaml`:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: alloy-audit
  namespace: monitoring
spec:
  releaseName: alloy-audit
  chart:
    spec:
      chart: alloy
      sourceRef:
        kind: HelmRepository
        name: grafana-labs
        namespace: flux-system
  install:
    remediation:
      retries: 0
  interval: 1m0s
  values: {}
```

`apps/base/alloy-audit/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: monitoring
resources:
  - release.yaml
```

- [ ] **Step 2: Create the overlay patch**

`apps/clusters/feathre-core/base-apps/alloy-audit/release.yaml`:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: alloy-audit
  namespace: monitoring
spec:
  values:
    controller:
      type: daemonset
      podLabels:
        logs.onelitefeather.net/env: prod
      # Control-plane only: /var/log/audit/kube exists nowhere else. The
      # default alloy-logs DaemonSet is 4/4 (workers) precisely because it
      # has no tolerations.
      nodeSelector:
        node-role.kubernetes.io/control-plane: ""
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      volumes:
        extra:
          - name: audit
            hostPath:
              path: /var/log/audit/kube
              # DirectoryOrCreate, NOT Directory. With `Directory`, a node where
              # the path is momentarily absent (fresh CP node, apiserver not yet
              # started after a reset) leaves the pod stuck in
              # ContainerCreating -> the DaemonSet never becomes ready ->
              # the HelmRelease never goes Ready -> base-apps (wait: true)
              # stalls -> the `apps` layer stops reconciling. Failing open with
              # an empty directory (and no log lines) is strictly safer than
              # wedging a Flux layer.
              type: DirectoryOrCreate
    serviceMonitor:
      enabled: true
    alloy:
      # Runs as root (the chart's default, same as alloy-logs) - required to
      # read the 0600 audit log the apiserver writes.
      mounts:
        extra:
          - name: audit
            mountPath: /var/log/audit/kube
            readOnly: true
      configMap:
        content: |
          local.file_match "audit" {
            path_targets = [{
              __path__ = "/var/log/audit/kube/*.log",
              job      = "kube-audit",
              cluster  = "feather-core",
              env      = "prod",
              node     = coalesce(sys.env("HOSTNAME"), constants.hostname),
            }]
            sync_period = "30s"
          }

          loki.source.file "audit" {
            targets    = local.file_match.audit.targets
            forward_to = [loki.process.audit.receiver]
            // The apiserver rotates in place (maxsize 100 / maxbackup 10);
            // tail_from_end avoids re-ingesting a whole 100MB backup on restart.
            tail_from_end = true
          }

          loki.process "audit" {
            // Audit events are one JSON object per line. verb/user are low
            // cardinality and worth indexing; resource/namespace are not
            // (they would multiply streams against max_global_streams_per_user).
            stage.json {
              expressions = {
                verb       = "verb",
                audit_user = "user.username",
                audit_ts   = "requestReceivedTimestamp",
              }
            }

            stage.timestamp {
              source = "audit_ts"
              format = "RFC3339Nano"
            }

            stage.labels {
              values = {
                verb = "verb",
              }
            }

            stage.structured_metadata {
              values = {
                audit_user = "audit_user",
              }
            }

            forward_to = [loki.write.default.receiver]
          }

          loki.write "default" {
            endpoint {
              url = "http://loki-gateway.grafana.svc.cluster.local/loki/api/v1/push"
            }

            external_labels = {
              cluster = "feather-core",
            }
          }
      resources:
        requests:
          cpu: 50m
          memory: 128Mi
          ephemeral-storage: 256Mi
        limits:
          cpu: 500m
          memory: 512Mi
          ephemeral-storage: 1Gi
```

`apps/clusters/feathre-core/base-apps/alloy-audit/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../../apps/base/alloy-audit
patches:
  - path: release.yaml
```

Add `  - alloy-audit` to `resources:` in `apps/clusters/feathre-core/base-apps/kustomization.yaml`, next to the other `alloy-*` entries.

- [ ] **Step 3: Verify and commit**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/alloy-audit | grep -E "hostPath|/var/log/audit/kube|node-role.kubernetes.io/control-plane" 
./scripts/validate.sh
git add apps/base/alloy-audit apps/clusters/feathre-core/base-apps/alloy-audit apps/clusters/feathre-core/base-apps/kustomization.yaml
git commit -m "feat(monitoring): ship the kube-apiserver audit log to loki"
```

Expected: the render shows the hostPath volume, the mount path, the nodeSelector and the toleration.

---

### Task 16: Merge the collector and verify it reads the log (gate before Task 17)

- [ ] **Step 1: Push, PR, merge, reconcile once**

```bash
git pull --rebase origin main
git push -u origin feat/ship-api-audit-log
gh pr create --title "feat(monitoring): ship the kube-apiserver audit log to loki" --body "$(cat <<'EOF'
## Summary
- Adds an alloy-audit DaemonSet on the three control-plane nodes that tails /var/log/audit/kube/*.log into Loki
- Lives in the monitoring namespace because it already enforces pod-security privileged; the grafana namespace inherits the cluster default (baseline), which forbids hostPath
- The audit trail is currently bounded by the node's own lifetime (/var/log is on Talos EPHEMERAL) and is lost on every reset/upgrade

Plan: docs/superpowers/plans/2026-08-03-alert-coverage-and-escalation.md (PR 6)

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] 3/3 DaemonSet pods Running on fr01-cp-01..03
- [ ] {job="kube-audit"} returns lines in Loki
- [ ] Loki stream count and ingestion rate checked after 1h before tightening the policy
EOF
)"
flux reconcile kustomization base-apps --with-source
```

- [ ] **Step 2: Confirm the pods scheduled**

```bash
kubectl -n monitoring get ds alloy-audit -o wide
kubectl -n monitoring get pods -l app.kubernetes.io/instance=alloy-audit -o wide
```

Expected: `DESIRED 3 / READY 3`, one pod on each of `fr01-cp-01`, `fr01-cp-02`, `fr01-cp-03`. `Pending` with `didn't tolerate` means the toleration is wrong; `CreateContainerConfigError` / an admission denial mentioning `hostPath` means it landed in the wrong namespace.

- [ ] **Step 3: Confirm it can actually read the file**

```bash
POD=$(kubectl -n monitoring get pod -l app.kubernetes.io/instance=alloy-audit -o jsonpath='{.items[0].metadata.name}')
kubectl -n monitoring exec "$POD" -c alloy -- ls -la /var/log/audit/kube/
kubectl -n monitoring exec "$POD" -c alloy -- head -c 200 /var/log/audit/kube/kube-apiserver.log
```

Expected: `kube-apiserver.log` listed with a non-zero size, and a JSON fragment beginning `{"kind":"Event","apiVersion":"audit.k8s.io/v1"`. A `Permission denied` means Alloy is not running as root — add `alloy.securityContext: {runAsUser: 0}` to the overlay.

- [ ] **Step 4: Confirm it reaches Loki**

In Grafana Explore against the `loki` datasource:

```
{job="kube-audit"} | json | line_format "{{.verb}} {{.objectRef_resource}}"
```

Expected: a live stream of events over the last 5 minutes.

- [ ] **Step 5: Measure the cost before tightening anything**

Wait **1 hour**, then:

```
sum(rate({job="kube-audit"}[5m]))
```
and
```bash
kubectl -n grafana exec deploy/loki-distributor -- sh -c 'true'  # no-op; use the query below instead
```

In Grafana Explore against `mimir`:

```
sum(loki_ingester_memory_streams) / 3 / 5000
sum(rate(loki_discarded_samples_total[5m]))
```

Expected: the stream-headroom ratio is still far below 0.8 and the discard rate is 0. **If either has moved materially, stop here and do not proceed to Task 17** — the policy tightening will multiply this volume. Record the measured bytes/sec in the PR.

**Gate:** Steps 2–5 must all pass before Task 17. **Rollback:** revert the merge commit; the DaemonSet is pruned; the audit log keeps being written locally as before.

---

### Task 17: Tighten the audit policy — **TALOS REPO**

> **REPO: `/mnt/projects/lab/talos-cluster` (remote `TheMeinerLP/FeatherCore`). Not this repo.**
> **This restarts the kube-apiserver static pod on each control-plane node.** With 3 CP nodes behind the VIP and one node at a time, the API stays available — but this is a real control-plane change. Do it in a maintenance window, one node at a time, and verify between nodes.
> **Decision gate DG-4 must be resolved first.** The scope below is option (a).

**Files (Talos repo):**
- Modify: `clusters/feather-core/talos/defaults/roles/controlplane.yaml` (append `auditPolicy:` under the existing `cluster.apiServer:` key)
- Regenerate: `clusters/feather-core/generated/machineconfigs/fr01-cp-0{1,2,3}.yaml`

> 🛑 **Do NOT put this in `patches/cluster/` — the audit's suggestion was wrong and was corrected here on 2026-08-03 after reading `talos.sh`.**
>
> `talos.sh` line 30 declares `PATCH_DIRS=(common cluster cri extensions)` and `common_patches()` (lines 56–63) globs **every** `*.yaml` in **all four** directories into the patch list for **every** node render — control-plane, `xl` workers and `storage` alike. Proof: `patches/cluster/flannel-mesh-iface.yaml` is a `cluster:`-scoped patch, and `grep -c iface-regex clusters/feather-core/generated/machineconfigs/fr01-wrk-xl-01.yaml` → `1`. A file dropped into `patches/cluster/` would therefore inject `cluster.apiServer.auditPolicy` into all **10** generated machineconfigs, not 3 — and Task 17 Step 2's own abort gate ("if a worker or storage config changes, stop") would fire on every correct execution.
>
> The right home is the role layer. `render-node` resolves `role` from the node's directory (`nodes/<site>/<role>/<name>.yaml` → `controlplane` | `xl` | `storage`, talos.sh:236–248) and applies `defaults/roles/<role>.yaml` only to that role. `defaults/roles/controlplane.yaml` already carries `cluster.apiServer.certSANs` and `cluster.apiServer.extraArgs`, and its own header reads *"Carries all controlplane-only / cluster-bootstrap settings so they never land on workers."* That is exactly this change.

The current policy (generated `fr01-cp-01.yaml:139–143`) is Talos's default one-liner:

```yaml
        auditPolicy:
            apiVersion: audit.k8s.io/v1
            kind: Policy
            rules:
                - level: Metadata
```

You get who/what/when but no bodies — so you cannot tell what a `create secret` or `patch clusterrolebinding` actually contained, which is precisely the question the PKI-leak remediation will raise.

- [ ] **Step 1: Edit the control-plane role defaults**

In `clusters/feather-core/talos/defaults/roles/controlplane.yaml`, add an `auditPolicy:` key **under the existing `cluster.apiServer:` mapping**, as a sibling of `certSANs:` and `extraArgs:`. Do not create a second `cluster:` or `apiServer:` key — the file already has both. The resulting `apiServer:` block:

```yaml
# Layered audit policy. Order matters: the FIRST matching rule wins, so the
# high-volume None rules must come before the catch-all Metadata rule, and the
# RequestResponse rules must come before both.
#
# Deliberately narrow: blanket RequestResponse on a 10-node cluster would bury
# Loki (which writes to the same Ceph as everything else). Only the four
# resource classes that answer "was the leaked PKI used against us, and what
# did the attacker actually write" get bodies.
cluster:
  apiServer:
    certSANs: [api.k8s.onelite.feather]          # existing - do not change
    extraArgs:                                    # existing - do not change
      oidc-client-id: c40df302-3680-4cb8-90c1-41512d83084e
      oidc-groups-claim: groups
      oidc-groups-prefix: ""
      oidc-issuer-url: https://login.microsoftonline.com/1a14dfb5-0eac-41bf-94cb-195c2e387520/v2.0
      oidc-username-claim: email
      oidc-username-prefix: 'oidc:'
    # --- new below this line ---
    auditPolicy:
      apiVersion: audit.k8s.io/v1
      kind: Policy
      omitStages:
        - RequestReceived
      rules:
        # 1. Full bodies for the credential- and privilege-granting resources.
        - level: RequestResponse
          resources:
            - group: ""
              resources: ["secrets"]
            - group: ""
              resources: ["serviceaccounts/token"]
            - group: "rbac.authorization.k8s.io"
              resources: ["rolebindings", "clusterrolebindings"]
        # 2. Drop the read noise that would otherwise dominate the volume.
        - level: None
          verbs: ["get", "list", "watch"]
          resources:
            - group: ""
              resources: ["endpoints", "events", "configmaps"]
            - group: "coordination.k8s.io"
              resources: ["leases"]
            - group: "discovery.k8s.io"
              resources: ["endpointslices"]
        - level: None
          users: ["system:kube-scheduler", "system:kube-controller-manager"]
          verbs: ["get", "list", "watch"]
        - level: None
          nonResourceURLs:
            - /healthz*
            - /readyz*
            - /livez*
            - /version
            - /metrics
        # 3. Everything else keeps today's behaviour.
        - level: Metadata
```

- [ ] **Step 2: Re-render the control-plane machine configs**

Render **all ten** nodes, not just the three CPs — that is what proves the change is role-scoped:

```bash
cd /mnt/projects/lab/talos-cluster
./talos.sh render-all
git diff --stat clusters/feather-core/generated/machineconfigs/
```

Expected: **exactly three** files changed — `fr01-cp-01.yaml`, `fr01-cp-02.yaml`, `fr01-cp-03.yaml`. The four `fr01-wrk-xl-*` and three `fr01-str-*` files must show **0 changes**.

**If any worker or storage config changes, stop and move the change** — you have edited a `patches/*` file instead of `defaults/roles/controlplane.yaml` (see the boxed note above).

Then confirm the CP diff touches only the audit policy:

```bash
git diff -U0 clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml | grep -E '^[+-]' | grep -v '^[+-][+-]'
talosctl validate -m metal -c clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
```

Expected: every changed line is inside the `auditPolicy:` block (the previous content was the 5-line default at `fr01-cp-01.yaml:139–143`), and `validate` prints `is valid for metal mode`. If `validate` errors, fix the YAML before applying anything — a rejected machine config on a control-plane node is not a situation you want to discover with `apply-config`.

- [ ] **Step 3: Commit in the Talos repo**

```bash
git add clusters/feather-core/talos/defaults/roles/controlplane.yaml \
        clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml \
        clusters/feather-core/generated/machineconfigs/fr01-cp-02.yaml \
        clusters/feather-core/generated/machineconfigs/fr01-cp-03.yaml
git status --porcelain   # must show nothing else staged or modified
git commit -m "feat(audit): layered audit policy with request bodies for credential resources"
```

Note the explicit file list — **do not `git add -A`** in this repo. `clusters/feather-core/generated/` also holds decrypted transients and the seven other node configs; staging blindly is how an unrelated node config ends up committed.

(The Talos repo's own commit conventions apply; if it does not lint Conventional Commits this message is still fine.)

- [ ] **Step 4: Apply, ONE NODE AT A TIME**

```bash
talosctl -n 192.168.15.10 apply-config \
  -f clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
```

Then, **before touching the next node**:

```bash
kubectl get --raw='/readyz?verbose' | tail -5
kubectl -n kube-system get pods -l k8s-app=kube-apiserver -o wide
kubectl get nodes
```

Expected: `readyz check passed`, all three apiserver pods Running, all 10 nodes `Ready`. Confirm the new policy is live on that node:

```bash
kubectl -n monitoring exec "$POD" -c alloy -- \
  sh -c 'tail -1 /var/log/audit/kube/kube-apiserver.log' | head -c 400
```
(where `$POD` is the alloy-audit pod on `fr01-cp-01`.) You should see events again; a `create secret` should now include a `responseObject`.

Only then repeat for `192.168.15.11` (fr01-cp-02) and `192.168.15.12` (fr01-cp-03).

- [ ] **Step 5: Re-measure the log volume**

After all three nodes, wait 30 minutes and re-run Task 16 Step 5's two queries. If `sum(loki_ingester_memory_streams)/3/5000` has crossed 0.5, or the discard rate is non-zero, **roll back Step 4** (re-apply the previous generated config from `git show HEAD~1:...`) and revisit DG-4 with a narrower rule set.

**Rollback:** restore the previous generated config per node, one at a time, verifying between each:

```bash
cd /mnt/projects/lab/talos-cluster
for n in 01 02 03; do
  git show HEAD~1:clusters/feather-core/generated/machineconfigs/fr01-cp-${n}.yaml > /tmp/rollback-cp-${n}.yaml
done
talosctl -n 192.168.15.10 apply-config -f /tmp/rollback-cp-01.yaml
kubectl get --raw='/readyz?verbose' | tail -3      # must pass before the next node
# then .11, then .12
git revert <commit>    # so git and the cluster agree again
```

The audit policy is stateless config; nothing is lost except the extra detail. Log lines already shipped to Loki are unaffected.

---

# PR 7 — Make etcd observable, then alert on it

> **TWO REPOS**, and gated on **DG-5**. `etcd_server_has_leader` has **no series in Mimir at all** — the `kube-prometheus-stack-kube-etcd` ServiceMonitor and its headless Service exist, but the Service has zero endpoints and Talos configures no etcd metrics listener. Writing an alert rule first would produce a rule that silently never evaluates — the exact failure class this whole plan exists to remove.
>
> If DG-5 resolves to "leave etcd unmonitored", **skip this PR entirely** and record the decision in `docs/incidents/` — do not ship the rule.

### Task 18: Expose etcd metrics — **TALOS REPO**

> 🛑 **THIS RESTARTS ETCD ON EACH CONTROL-PLANE NODE, ONE AT A TIME.** Changing `cluster.etcd.extraArgs` rewrites the etcd static-pod manifest, so etcd on that node restarts and the cluster re-elects if it was the leader. With 3 members you can lose exactly one at a time and keep quorum — **losing two means the API server goes read-only and Flux, CNPG, Rook and every controller stall.** Never apply to a second node until `talosctl etcd status` shows all three members healthy again. Maintenance window only. Do not run this in the same window as PR 6's Task 17.

**Files (Talos repo):**
- Modify: `clusters/feather-core/talos/defaults/roles/controlplane.yaml` (add `cluster.etcd.extraArgs`)
- Regenerate: `clusters/feather-core/generated/machineconfigs/fr01-cp-0{1,2,3}.yaml`

> **Same scoping rule as Task 17 — `patches/cluster/` would hit all 10 nodes** (`PATCH_DIRS=(common cluster cri extensions)`, talos.sh:30). Put this in the control-plane role file, where the etcd config already lives for CP nodes only.

- [ ] **Step 1: Add the etcd extraArgs to the control-plane role**

In `clusters/feather-core/talos/defaults/roles/controlplane.yaml`, add a top-level `etcd:` key under the existing `cluster:` mapping (sibling of `apiServer:` and `extraManifests:`):

```yaml
# etcd exposes /metrics on a separate listener that Talos does not configure by
# default, which is why kube-prometheus-stack's kube-etcd Service has had zero
# endpoints since day one and etcd_server_has_leader has never had a series.
# This listener serves counters only - no key material, no read/write API.
# NOTE: setting listen-metrics-urls REPLACES etcd's default metrics listener
# set; /metrics is then served ONLY here, in plaintext, with no client-cert
# auth. That is why DG-5 exists.
cluster:
  etcd:
    extraArgs:
      listen-metrics-urls: http://0.0.0.0:2381
```

> DG-5 recommends binding to the node's management IP instead of `0.0.0.0`. That is **not** expressible in the role file (one file, three nodes) — it needs a per-node override in `clusters/feather-core/talos/nodes/fr01/controlplane/fr01-cp-0{1,2,3}.yaml`, each with its own `http://192.168.15.1{0,1,2}:2381`. Whichever you choose, Step 2's `talosctl validate` must pass before you apply.

- [ ] **Step 2: Render and verify the scope (no apply yet)**

```bash
cd /mnt/projects/lab/talos-cluster
./talos.sh render-all
git diff --stat clusters/feather-core/generated/machineconfigs/
talosctl validate -m metal -c clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
```

Expected: **exactly three** changed files (`fr01-cp-0{1,2,3}.yaml`), zero changes to the four `fr01-wrk-xl-*` and three `fr01-str-*` files, and `is valid for metal mode`. If a worker/storage config changed, the patch is in the wrong layer — fix it before applying.

- [ ] **Step 3: Commit**

```bash
git add clusters/feather-core/talos/defaults/roles/controlplane.yaml \
        clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml \
        clusters/feather-core/generated/machineconfigs/fr01-cp-02.yaml \
        clusters/feather-core/generated/machineconfigs/fr01-cp-03.yaml
git status --porcelain    # nothing else staged
git commit -m "feat(etcd): expose the etcd metrics listener on 2381"
```

(Explicit paths — **not** `git add -A clusters/feather-core`, which would sweep up unrelated generated node configs and any decrypted transient.)

- [ ] **Step 4: Apply, ONE NODE AT A TIME**

Record the current leader first, so you know whether to expect an election:

```bash
talosctl -n 192.168.15.10,192.168.15.11,192.168.15.12 etcd status
talosctl -n 192.168.15.10 apply-config -f clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
```

**After each node**, verify before moving on:

```bash
talosctl -n 192.168.15.10,192.168.15.11,192.168.15.12 etcd status
kubectl get --raw='/readyz?verbose' | tail -3
kubectl get nodes
```

Expected: **all three** members listed, none with a raised `ERRORS` column, exactly one leader, `readyz check passed`, all 10 nodes `Ready`. Only then repeat for `.11`, and only then `.12`.

**Rollback (any node, at any point):** the previous machine config is one commit back — re-apply it to the affected node and stop:

```bash
git show HEAD~1:clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml > /tmp/rollback-cp-01.yaml
talosctl -n 192.168.15.10 apply-config -f /tmp/rollback-cp-01.yaml
talosctl -n 192.168.15.10,192.168.15.11,192.168.15.12 etcd status
```

Then `git revert` the commit so git and the cluster agree again. The listener is stateless config — nothing is lost by removing it, etcd data is untouched.

- [ ] **Step 5: Confirm the listener answers, from inside the cluster**

Prove the port is actually serving **before** touching the GitOps repo, otherwise Task 19 debugs a scrape that was never going to work:

```bash
kubectl run etcd-metrics-probe --rm -i --restart=Never --image=curlimages/curl:8.11.1 -- \
  sh -c 'for ip in 192.168.15.10 192.168.15.11 192.168.15.12; do
           echo -n "$ip: "; curl -s -m 5 -o /dev/null -w "%{http_code}\n" "http://$ip:2381/metrics"; done'
```

Expected: `200` from all three. A `000` means the listener is not bound (re-check the rendered `extraArgs`) or the mgmt network blocks 2381 from the pod CIDR.

Then, separately:

```bash
kubectl -n kube-system get endpoints kube-prometheus-stack-kube-etcd
```

Expected: still `<none>` at this point — the chart only creates Endpoints once `kubeEtcd.endpoints` is set, which is Task 19 Step 1. This is confirmation, not a failure.

---

### Task 19: Point kube-prometheus-stack at the etcd endpoints and add the rule — **GITOPS REPO**

**Files:**
- Modify: `infrastructure/clusters/feather-core/base-controllers/kube-prometheus-stack/release.yaml`
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml`

> **This task is two separately-merged commits, not one.** Step 2 requires the endpoints half to be live in the cluster before the rule is written, so `git commit` twice and merge twice. Commit A = the kube-prometheus-stack file (Step 1). Commit B = the Grafana rule (Step 3). Step 4's `git add` reflects that split. Do **not** collapse them: a rule against a metric with no series is exactly the failure class this plan exists to remove.

- [ ] **Step 1: Configure the endpoints** *(commit A)*

In `infrastructure/clusters/feather-core/base-controllers/kube-prometheus-stack/release.yaml`, add this block under `spec.values:` at 4-space indentation, as a sibling of `fullnameOverride:`/`grafana:`/`prometheus:`. If PR 4 has already merged, put it directly after the `defaultRules:` block; if not, put it after `kubeApiServer:` — **the position does not matter, and this task does not depend on PR 4.**

⚠️ Same `base-controllers` stall warning as Task 12: that layer is `wait: true` and `base-configs`/`controllers`/`rook` depend on it. A failed kube-prometheus-stack upgrade blocks all three.

```yaml
    # The chart ships a headless Service + ServiceMonitor for etcd but no
    # endpoints, so it has been scraping nothing since install (confirmed:
    # `kubectl -n kube-system get endpoints kube-prometheus-stack-kube-etcd`
    # -> <none>, 155d old). These are the three control-plane mgmt IPs; the
    # listener is enabled in the Talos repo, in
    # clusters/feather-core/talos/defaults/roles/controlplane.yaml
    # (cluster.etcd.extraArgs.listen-metrics-urls).
    kubeEtcd:
      enabled: true
      endpoints:
        - 192.168.15.10
        - 192.168.15.11
        - 192.168.15.12
      service:
        enabled: true
        port: 2381
        targetPort: 2381
```

Validate, commit **A only**, open a PR, merge it:

```bash
kubectl kustomize infrastructure/clusters/feather-core/base-controllers/kube-prometheus-stack | grep -A6 "kubeEtcd"
./scripts/validate.sh
git add infrastructure/clusters/feather-core/base-controllers/kube-prometheus-stack/release.yaml
git commit -m "feat(monitoring): scrape etcd via the kube-etcd servicemonitor endpoints"
```

- [ ] **Step 2: Verify metrics arrive BEFORE adding the rule** *(gate)*

After commit A is merged:

```bash
flux reconcile kustomization base-controllers --with-source
flux get kustomizations -A
kubectl -n kube-system get endpoints kube-prometheus-stack-kube-etcd
```

Expected: `base-controllers` and its three dependents `READY=True`; the Endpoints object now lists **3 addresses on port 2381** (it was `<none>`). Then, in Grafana Explore against `mimir` (allow ~2 min for a scrape + remote_write round trip):

```
etcd_server_has_leader
```

Expected: **3 series, value 1**. If this returns nothing, **do not proceed to Step 3** — debug the scrape (`kubectl -n kube-system get endpoints kube-prometheus-stack-kube-etcd`, `kubectl -n grafana logs sts/alloy-metrics | grep -i etcd`). Shipping the rule against an empty metric reproduces the exact defect this plan is fixing.

**Rollback for commit A:** revert it and `flux reconcile kustomization base-controllers --with-source`. The Endpoints object disappears and etcd goes back to unscraped; nothing else is affected.

- [ ] **Step 3: Add the rule** *(commit B — only after Step 2 passed)* — append to the `node_health` group created in Task 5, as its fifth rule

```yaml
              - uid: etcd-no-leader
                title: etcd member has no leader
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # 3 series (one per control-plane member), value 1 when healthy.
                      # This metric had NO series in Mimir until the etcd metrics
                      # listener was enabled in the Talos repo - do not re-add this
                      # rule without that change, or it will never evaluate.
                      expr: min(etcd_server_has_leader)
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
                              - 1
                            type: lt
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
                noDataState: OK
                execErrState: Alerting
                for: 2m
                annotations:
                  summary: "At least one etcd member has no leader - the control plane is in or near a quorum loss. Check: talosctl -n 192.168.15.10 etcd status; talosctl -n 192.168.15.10 etcd members"
                labels:
                  severity: critical
```

- [ ] **Step 4: Verify, validate, commit**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana | grep -c "uid: etcd-no-leader"
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "feat(monitoring): alert on etcd leader loss"
```

Expected from the grep: `1`. Only `release.yaml` under `apps/` is staged here — the kube-prometheus-stack file was commit A and is already on `main`.

After merging commit B:

```bash
flux reconcile kustomization base-apps --with-source
kubectl -n grafana rollout status deploy/grafana --timeout=180s
kubectl -n grafana logs deploy/grafana --tail=200 | grep -iE "provisioning.*(error|failed)" || echo "no provisioning errors"
```

Expected: `no provisioning errors`, and `etcd-no-leader` visible in Grafana → Alerting → Alert rules in state `Normal`.

**Rollback:** revert commit B (removes the rule) and/or commit A (removes the endpoints; etcd goes back to unscraped), each followed by one reconcile of its layer. The Talos listener can be left in place — it is inert without a scraper. To remove it too, use Task 18's per-node rollback procedure.

---

# PR 8 — Flip `noDataState` on the two Flux rules

> **HARD GATE P2:** do not open this PR until theme `flux-release-control-and-convergence` has fixed the Kustomization interval flapping. At planning time 6 of 13 Kustomizations were `Ready=False`; flipping this first guarantees Discord noise and trains everyone to ignore the channel.

### Task 20: `noDataState: OK` → `Alerting` on `flux-core-layer-not-ready` and `core-infra-helmrelease-not-ready`

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml` lines **27563** and **27616**

**Interfaces:**
- Consumes: theme `flux-release-control-and-convergence` merged and all 13 Kustomizations stably `Ready=True`
- Produces: a metrics blackout on the `gotk_resource_info` series becoming loud instead of silent

**Precise scope, because the audit's line numbers were off by one:** line **27563** is `                noDataState: OK` and line **27564** is `                execErrState: Alerting` (for `flux-core-layer-not-ready`); line **27616** is `                noDataState: OK` and **27617** is `                execErrState: Alerting` (for `core-infra-helmrelease-not-ready`). **Change only the two `noDataState` lines. Leave `execErrState: Alerting` alone** — it is already correct and already covers the "Mimir is down" case (a dead datasource produces a query *error*, not NoData, so these two rules already fire in that scenario).

The genuine gap being closed is narrower and worth stating in the commit: *Mimir is up and answering, but the `gotk_resource_info` series is absent* — i.e. kube-state-metrics dies, its `customResourceState` config breaks, or a label is renamed. That currently evaluates NoData → OK → silence.

- [ ] **Step 1: Pre-flight — confirm the gate is actually met**

```bash
kubectl get kustomization -A -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status'
```

Expected: **all 13 rows `True`**. Re-run after 15 minutes and confirm they are still all `True`. If any flaps, **stop** — the gate is not met.

- [ ] **Step 2: Make the two edits**

Line 27563: `                noDataState: OK` → `                noDataState: Alerting`
Line 27616: `                noDataState: OK` → `                noDataState: Alerting`

Add a comment above each, at 16-space indentation, immediately before the changed line:

```yaml
                # Alerting, not OK: this rule's producer is kube-state-metrics'
                # customResourceState config (gotk_resource_info is NOT a native
                # Flux controller metric). If that config breaks or a label is
                # renamed, the query returns NoData while Mimir is perfectly
                # healthy - and "no Flux data" must never read as "Flux is fine".
                # execErrState below already covers Mimir being down.
```

- [ ] **Step 3: Verify only two lines changed**

Use **anchored** greps against the source file. An unanchored `grep -c "noDataState: Alerting"` also matches four explanatory comment lines that already exist in this file (e.g. :28372, :28434, :28436, :28437) and gives a misleading number.

```bash
f=apps/clusters/feathre-core/base-apps/grafana/release.yaml
git diff --stat $f
git diff $f | grep -c '^-                noDataState: OK$'        # expect 2
git diff $f | grep -c '^+                noDataState: Alerting$'  # expect 2
grep -c '^                noDataState: Alerting$' $f
grep -c '^                noDataState: OK$' $f
```

Expected: the two `git diff` counts are both exactly `2` (this is the real check — it is independent of which other PRs have landed).

**Corrected absolute baseline (measured 2026-08-03; the audit's "2 Alerting, 15 OK" was wrong):** before any PR in this plan the file has **10** `noDataState: Alerting` and **7** `noDataState: OK`, totalling the 17 existing rules. Every rule added by PRs 2, 3 and 7 uses `noDataState: OK`, so if all of them landed first the pre-edit state is **10 Alerting / 18 OK**, and after this task **12 Alerting / 16 OK**. If your absolute numbers do not match, count which PRs are actually merged before assuming the edit is wrong — the `git diff` counts above are authoritative.

Also confirm the diff touched nothing else:

```bash
git diff $f | grep -E '^[+-]' | grep -v '^[+-][+-]' | grep -v 'noDataState' | grep -v '^+ *#'
```

Expected: **no output** (the only additions besides the two flips are the comment lines from Step 2).

- [ ] **Step 4: Validate, commit, merge**

```bash
./scripts/validate.sh
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "fix(monitoring): make a flux metrics blackout alert instead of reading healthy"
```

- [ ] **Step 5: Watch for 24 hours**

After merging, check the Discord channel and:

```bash
kubectl -n grafana logs deploy/grafana --tail=500 | grep -i "flux-core-layer-not-ready"
```

If the rule fires on NoData while `flux get kustomizations -A` shows everything `True`, the producer is genuinely broken — investigate `kubectl -n monitoring logs deploy/kube-prometheus-stack-kube-state-metrics | tail -50` — that is the alert working, not misfiring. If it fires because the layers themselves are flapping, **revert** and go finish theme `flux-release-control-and-convergence`.

**Rollback:** revert the commit. Two-line change, no state.

---

## Appendix A — alloy-metrics WAL persistence (deliberately not scheduled)

DG-6 resolved to "ephemeral-storage limit only" (Task 3). If the owner later wants the WAL to survive a pod restart, this is the procedure — **it is not GitOps-safe and must be done by hand:**

1. Add to `apps/clusters/feathre-core/base-apps/alloy-metrics/release.yaml` under `controller:`:

```yaml
      volumeClaimTemplates:
        - metadata:
            name: wal
          spec:
            accessModes: ["ReadWriteOnce"]
            storageClassName: ceph-rbd-fr01
            resources:
              requests:
                storage: 5Gi
```
   and under `alloy.mounts.extra`: `- name: wal` / `mountPath: /var/lib/alloy/data`.

2. `volumeClaimTemplates` is **immutable** on an existing StatefulSet. Flux's Helm upgrade will fail with `Forbidden: updates to statefulset spec for fields other than 'replicas'...`. You must first:

```bash
kubectl -n grafana delete sts alloy-metrics --cascade=orphan
flux reconcile helmrelease alloy-metrics -n grafana
```

3. **The trade-off that made this a "no" for now:** `ceph-rbd-fr01` is the same Ceph cluster whose outage is the scenario the persistent WAL is meant to survive. A WAL PVC on Ceph does not help during a Ceph outage; it only helps during a Mimir-only outage or a routine pod reschedule. A local/hostPath-backed StorageClass would be the right answer, and none exists on this cluster today.

---

## Honest limitations of this plan

- **`etcd_server_has_leader` was never observed working here.** PR 7 is written from the Talos and kube-prometheus-stack documentation plus the confirmed absence of endpoints (`kubectl -n kube-system get endpoints kube-prometheus-stack-kube-etcd` → `<none>`, object 155d old); the exact `extraArgs` key acceptance and whether Talos rejects a per-node bind address were **not** verified against a live apply. Task 18 Step 2's `talosctl validate`, Step 5's in-cluster `curl` probe and the one-node-at-a-time rollout exist because of that.
- **Talos patch scoping is now verified, and the original plan had it wrong.** `talos.sh:30` sets `PATCH_DIRS=(common cluster cri extensions)` and `common_patches()` (talos.sh:56–63) globs all of them into **every** node render — proven by `grep -c iface-regex …/fr01-wrk-xl-01.yaml` → `1` for the `cluster:`-scoped flannel patch. Tasks 17 and 18 were therefore moved from `patches/cluster/` to `defaults/roles/controlplane.yaml`, which `render-node` applies only to nodes under `nodes/<site>/controlplane/`. What is still **not** exercised: an actual `render-all` after this specific edit. The "exactly three changed files" gate in both tasks is the safety net.
- **The kubelet-down rule cannot fire for a node that has been deleted from the API.** `min by (node) (up{job="kubelet"…})` relies on the kubelet target still being in service discovery; a NotReady node keeps its Endpoints entry (so `up` → 0 and the rule fires, which is the case that matters), but if the `Node` object itself is removed the series simply vanishes and the rule sees 9 healthy nodes. `node-not-ready` has the same blind spot. Cluster-wide scrape loss is covered by the existing `observability-pipeline-no-data` deadman; a *single* silently-deleted node is not covered by anything in this plan.
- **The Loki stream-headroom divisor (`/3` for replication_factor) is a modelling choice**, not something Loki exports directly. It is correct for how `loki_ingester_memory_streams` is produced, but if `replication_factor` ever changes at `loki/release.yaml:48`, the rule silently becomes wrong. The rule's comment says so.
- **Dashboard links are now verified, and two were wrong.** Queried live 2026-08-03: the PV dashboard uid is `mrjpdvx7k` (`kubernetes-persistent-volumes-custom`), **not** the `919b92a8…` the audit guessed — Task 7 was corrected. `/api/search?query=mimir` returns **zero** dashboards, so the two `/d/mimir-writes` links in PR 3 were removed rather than shipped as 404s.
- **Audit-log volume after the policy tightening is unknown.** The 4-resource `RequestResponse` list is a considered guess. Task 17 Step 5 exists because the honest answer is "measure it".
- **Task 14 Step 2 (proving the heartbeat alarm) costs a 20-minute `flux suspend` of the whole `base-apps` layer**, because Flux otherwise reverts a `kubectl patch suspend` within a minute. That is a real (if reversible) trade-off, and a suspended layer is invisible to `flux-core-layer-not-ready`. The fallback (provider-side test alert + a proven ping loop) is weaker evidence than a real 20-minute silence. Say in the PR which one you did.
- **Two Flux layers in this plan are `wait: true` with dependents:** `base-apps` (PRs 1/2/3/5/6; the `apps` layer depends on it) and `base-controllers` (PRs 4 and 7; `base-configs`, `controllers` and `rook` depend on it). Every change to those layers can stall their dependents if a Helm upgrade hangs. The affected tasks now carry an explicit warning and an unstick procedure, but the risk cannot be designed away — schedule PRs 4, 6 and 7 outside incident windows.
- **No claim is made that PR 1's Flux Alert covers everything.** `eventSeverity: error` catches `ReconciliationFailed`, `DependencyNotReady`, `HelmUpgradeFailed` and decryption errors. It does **not** catch a Kustomization that is suspended, or one that reconciles successfully to the wrong thing. Those belong to `flux-release-control-and-convergence`.
