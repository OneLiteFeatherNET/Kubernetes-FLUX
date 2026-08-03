# LAN Data-Plane Authentication and Unmanaged-Sniffer Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unmanaged privileged packet-capture DaemonSet that has been running on all 10 nodes for 15 days, then put HTTP Basic authentication in front of the three unauthenticated observability data planes published on the MetalLB gateway (`loki`, `mimir`, `tempo`), and cut one of the two plaintext MaxScale admin-port exposures.

**Architecture:** Task 1 is a pure `kubectl delete` against live cluster state — kubeshark exists in no git repo, so there is nothing to revert and no PR. Everything after it is a normal GitOps change in this repo, split into four separately-mergeable PRs sequenced by blast radius: MaxScale GUI service removal (no data path), then Basic auth on `tempo` alone as a canary, then `mimir` + `loki`, then documentation of the inert NetworkPolicies. Two items — authentication on the `otel` OTLP intake and MaxScale admin TLS — are written up as decision gates rather than executed, because both have client-side consequences outside this repo that this plan cannot verify.

**Tech Stack:** Envoy Gateway 1.7.5 (`gateway.envoyproxy.io/v1alpha1` `SecurityPolicy`), Gateway API `HTTPRoute`/`GRPCRoute`, Kustomize `secretGenerator`, SOPS/PGP, mariadb-operator 26.6.0 (`MaxScale` CR), FluxCD.

---

## Global Constraints

- A change takes effect **only** when committed and pushed to `main`; Flux then applies it (GitRepository polls 1m, `monitoring`/`configs` Kustomizations 1m).
- Conventional Commits enforced by CI (`commitlint.config.mjs`): types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`; subject starts **lowercase**; header ≤100 chars. The PR title is linted too.
- `./scripts/validate.sh` must pass locally before every commit.
- Never hammer `flux reconcile` in a loop — one reconcile per stage, then verify.
- Renovate moves `main` under you: `git pull --rebase origin main` immediately before every push.
- `generatorOptions.disableNameSuffixHash: true` is set in every overlay touched here, so generated Secret names are stable and changing their contents does **not** roll consumers. For the Secrets in this plan the consumer is `envoy-gateway` (which watches the Secret and re-renders xDS), not a Deployment mount — Task 6 Step 7 gives the explicit fallback restart if the change does not take.
- SOPS: PGP, one recipient (`0231831CB40B8E587B7353CBA3AF727721205A62`). The files this plan touches live under `apps/`, so the **root** `/.sops.yaml` rule applies — its `path_regex` covers `sops.conf`. (`clusters/feather-core/.sops.yaml` does *not* list `sops.conf`; that drift is real but only affects files under `clusters/feather-core/`. Fixing it belongs to theme `sops-key-custody-and-rotation-hygiene`, not here.)

## Prerequisites

- `kubectl` context `admin@feather-core` with delete rights on cluster-scoped RBAC objects and namespaces (Task 1 only; everything else is read-only against the cluster).
- The SOPS PGP private key `0231831CB40B8E587B7353CBA3AF727721205A62` present in the local GPG keyring (`gpg --list-secret-keys` must show it). Tasks 5 and 6 re-encrypt files.
- `sops` ≥ 3.8 (verified present: 3.13.3 — `--filename-override` is required by Task 5).
- `openssl` (present) — used instead of `htpasswd`, which is **not** installed on this workstation.
- A scratch directory for exported manifests and generated plaintext credentials. This plan writes `$SCRATCH` for it; export it once per session and never point it at the repo working tree:
  ```bash
  export SCRATCH=/tmp/claude-1000/-mnt-projects-oss-onelitefeather-Kubernetes-FLUX/6a7fbd35-4a60-4531-91e4-cf130f8aa8e8/scratchpad
  mkdir -p "$SCRATCH"
  ```

**Pre-flight health check — run before Task 1 and treat a failure as a stop:**

```bash
flux get kustomizations -A
```

Expected: 13 rows, all at the same revision (`main@sha1:ac16018` on 2026-08-03), and every row that is not `READY=True` carrying only a `dependency 'flux-system/<layer>' is not ready` or `Reconciliation in progress` message.

⚠️ **Do not treat "all 13 rows True" as a hard gate.** Every layer reconciles on a 1-minute interval and each one flips its dependents to `False` while it runs, so a snapshot with two or three `dependency ... is not ready` rows is the normal steady state, not a fault. Observed on 2026-08-03 within two minutes of each other: first `configs`/`rook`/`rook-fr01` False, then `controllers`/`internal-certs` False, with everything green in between. Re-run the command 2–3 times over ~2 minutes. **Stop and investigate only if** a row is `False` with a message that is *not* a dependency/in-progress message (e.g. a build, decryption or health-check error), or if the same row stays `False` across three consecutive runs, or if any row is stuck on an older revision. Quick non-flapping check:

```bash
flux get kustomizations -A | grep -v -E 'True|Reconciliation in progress|dependency .* is not ready'
```

Expected: only the header line.

## Cross-theme dependencies

| Direction | Theme | What it means for this plan |
|---|---|---|
| **This plan feeds** | `crown-jewel-rotation-leaked-pki-and-credentials` | Task 1 must complete **before** that theme's credential rotation begins. Rotating secrets while a privileged packet sniffer is still capturing on all 10 nodes re-exposes the new values immediately. Task 1 also produces the exposure window (2026-07-18 → removal) that theme needs to scope what to rotate. |
| **This plan defers to** | `talos-fleet-lifecycle` (theme 9) | `kubelet-serving-cert-approver` is **not** adopted here — see "Deliberately out of scope". Theme 9 owns pinning its `extraManifests` URL in the Talos repo. |
| **Independent** | `flux-release-control-and-convergence` | The `monitoring` Kustomization's unused `postBuild.substitute.ALLOWED_CIDRS` is that theme's finding. This plan does not use or remove it. |

## Audit-finding coverage

| Finding id | Covered by |
|---|---|
| `k8s-security/kubeshark-privileged-daemonset-drift` | Tasks 1–2 (delete), Appendix A (hardened reinstall if DG-5 = yes) |
| `k8s-workloads/kubeshark-unmanaged-privileged-daemonset` | Tasks 1–2; Appendix A carries the `priorityClassName` / resource-limit / `nodeSelector` requirements |
| `k8s-security/loki-mimir-tempo-unauthenticated-on-lan` | Tasks 5–8 (loki, mimir, tempo); Task 10 is the decision gate for the fourth route, `alloy-receiver` |
| `k8s-security/maxscale-admin-api-plaintext-on-lan` | Task 3–4 remove the `10.200.90.7` exposure; Task 11 is the decision gate for the remaining `10.200.90.4:8989` exposure |
| `flux-gitops/cluster-drift-untracked-namespaces` | Task 1 removes `kubeshark-debug`; Task 2 Step 3 adds the standing drift check. The `kubelet-serving-cert-approver` half is **explicitly out of scope** — see below; it is not drift, it is owned by Talos in the other repo |
| `k8s-security/no-networkpolicy-enforcement-flannel` | Task 2 Step 2 (4 of the 8 policies removed with kubeshark) and Task 9 (documenting the remaining 4). The CNI change the finding recommends is **explicitly out of scope** — see below |

---

## Corrections to the audit — verified live on 2026-08-03

The audit's scope note for this theme contains four statements that do not survive verification. The plan below follows the corrected facts, not the scope note. Do not "fix" the plan back toward the scope note.

1. **Applying a SecurityPolicy to the loki/mimir/tempo routes does NOT blank dashboards or stop ingestion.** Every in-cluster client uses cluster-local Service DNS and never touches the gateway:
   - Grafana datasources: `http://mimir-gateway.grafana.svc.cluster.local/prometheus`, `http://loki-gateway.grafana.svc.cluster.local`, `http://tempo-gateway.grafana.svc.cluster.local` (`apps/clusters/feathre-core/base-apps/grafana/release.yaml:100,110,123`).
   - `alloy-logs` → `http://loki-gateway.grafana.svc.cluster.local/loki/api/v1/push` (`.../alloy-logs/release.yaml:206`).
   - `alloy-metrics` → `http://mimir-gateway.grafana.svc.cluster.local/api/v1/push` (`.../alloy-metrics/release.yaml:71`).
   - `alloy-receiver` exporters → `tempo-gateway...:4317`, `mimir-gateway...`, `loki-gateway...` (`.../alloy-receiver/release.yaml:144,158,172`).

   7 h 11 min of Envoy access logs across all four `envoy-envoy-eg-*` pods (06:26–13:37 UTC, 25 573 requests) contained exactly **3 requests to `loki.apps.onelite.feather`, 2 to `mimir.apps.onelite.feather` and 2 to `tempo.apps.onelite.feather` — all of them `curl/8.21.0` from `10.1.1.2`, i.e. the auditor's own probes.** There is no production client on those three hostnames. Blast radius of Tasks 6 and 8 is therefore effectively zero, which is why they come before the genuinely risky items.

2. **`alloy-receiver` is the opposite case and must be treated separately.** In the same window `otel.apps.onelite.feather` took **25 455 requests, all from a single off-cluster source `10.200.2.35`, user-agent `OTel-OTLP-Exporter-Java/1.63.0`** (`/v1/logs` and `/v1/metrics`). That client is configured outside this repo. Adding auth to that route without changing it first stops all external telemetry ingestion. Task 11 is a decision gate, not an action.

3. **The existing htpasswd files cannot be used as-is.** `apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf` decrypts to `loki:$apr1$…` and `.../mimir/mimir-ingress-auth.sops.conf` to `metrics-writer:$apr1$…` — Apache MD5. The Envoy Gateway CRD vendored in this repo states plainly (`infrastructure/base/controllers/envoy-crds/crds.yaml:25605-25609`): *"the value needs to be the htpasswd format, for example: `user1:{SHA}hashed_user1_password`. Right now, only SHA hash algorithm is supported."* The hashes must be regenerated as `{SHA}`, and since the plaintexts were never recorded anywhere in git, new passwords must be minted anyway. Task 5 does this.

4. **`spec.admin.tls` does not exist on the MaxScale CRD installed here.** `kubectl explain maxscale.spec.admin --recursive` on mariadb-operator 26.6.0 returns only `guiEnabled` and `port`. The real field is `spec.tls` (`enabled`, `admin*`, `listener*`, `server*`, `verifyPeer*`) — a single `enabled: true` switch that turns on listener TLS as well as admin TLS, i.e. it lands on the 3306 data path used by every app. That is materially riskier than "restarts the MaxScale pods". Task 11 is a decision gate, not an action. `MariaDB.spec.tls` is already `{"enabled": true}`; `MaxScale.spec.tls` is unset.

Additional verified facts the plan relies on:

- `MaxScale.spec.kubernetesService` supports `loadBalancerSourceRanges` (confirmed in the CRD) — relevant to Task 11 option (b).
- The `maxscale-galera-gui` Service carries an `ownerReference` to the `MaxScale` CR but is **not** Flux-managed. Removing the field from the CR will not garbage-collect it (owner-ref GC only fires when the *owner* is deleted). Task 3 deletes it explicitly.
- Every application connects to MaxScale via `maxscale-galera.mariadb-galera.svc.cluster.local:3306`, not via `10.200.90.4` (spot-checked across the repo's decrypted `*.sops.env` files). In-cluster traffic reaches the Service ClusterIP and is unaffected by `loadBalancerSourceRanges`.
- Precedent for the `secretGenerator` pattern Task 6 uses already exists in-repo: `apps/clusters/feathre-core/base-apps/bluemap/kustomization.yaml:26-27` (`s3.conf=s3.sops.conf`) and `.../outline/kustomization.yaml:14-15` (`.env=outline.sops.env`).

---

## Decision gates

These require a human answer. Do not pick silently.

- **DG-1 (blocks Task 1): Is the kubeshark capture session finished?** It was installed 2026-07-18T14:05:16Z and has been running 15 days. *Recommendation: delete it.* Fifteen days is not a debugging session, nothing in git references it, and its ClusterRole can create and delete NetworkPolicies cluster-wide. If an investigation genuinely depends on it, capture what you need first, then delete — do not leave it running past this plan.
- **DG-2 (Task 5): username and password policy for the three new gateway credentials.** Options: (a) keep the existing usernames `loki` and `metrics-writer` and add `tempo`, minting new passwords for all three; (b) one shared `observability` credential for all three routes. *Recommendation: (a)* — per-route credentials mean a leak of one does not open the other two, and the existing filenames/usernames stay meaningful. Passwords must be recorded in the operator's password manager; this plan deliberately does not store the plaintext anywhere in the repo.
- **DG-3 (Task 10): does the `otel.apps.onelite.feather` OTLP intake get authentication?** The single external client at `10.200.2.35` (`OTel-OTLP-Exporter-Java/1.63.0`, 25 455 req / 7 h) lives outside this repo. Options: (a) leave as-is and accept that any LAN host can inject telemetry; (b) `SecurityPolicy` with `basicAuth` on the `HTTPRoute` **and** the `GRPCRoute`, with the exporter's `OTEL_EXPORTER_OTLP_HEADERS` updated in the CloudNet/server-side config in the same window; (c) `SecurityPolicy` with `authorization.rules` restricting `principal.clientCIDRs` to the known source subnet. *Recommendation: (c) first, then (b)* — a CIDR allow-list needs no client change and can ship immediately; Basic auth needs a coordinated change to a repo this plan does not control.
- **DG-4 (Task 11): how to close the MaxScale admin API on `10.200.90.4:8989`.** Options: (a) `spec.tls.enabled: true` — encrypts the admin API but also flips the 3306 listener to TLS-required, needing a maintenance window and verification that every client supports TLS; (b) `spec.kubernetesService.loadBalancerSourceRanges` scoped to the hosts that actually need LAN access to 3306 — no restart, but this plan could **not** enumerate those hosts read-only; (c) accept the residual exposure and rotate the MaxScale admin credential as part of theme `crown-jewel-rotation-leaked-pki-and-credentials`. *Recommendation: (c) now, (b) once the client list is known, (a) only in a planned window.* Do not run (a) casually.
- **DG-5 (Appendix A): is packet capture still wanted as a permanent, reviewable capability?** *Recommendation: no.* Reinstalling a privileged host-network DaemonSet permanently to solve a problem that has not recurred is a poor trade. If it is wanted, Appendix A specifies the hardened shape.

---

## Deliberately out of scope

- **Adopting `kubelet-serving-cert-approver` into Flux.** The audit finding `flux-gitops/cluster-drift-untracked-namespaces` claims it "exists only as live state". That is wrong, and adopting it would be actively harmful. Verified: the Deployment carries `config.k8s.io/owning-inventory: talos-bootstrap-manifests-inventory` and its only non-controller field manager is `talos` (server-side Apply, last at 2026-06-13T11:16:38Z). It is declared in the **other** repo at `/mnt/projects/lab/talos-cluster/clusters/feather-core/talos/defaults/roles/controlplane.yaml:17` and rendered into `/mnt/projects/lab/talos-cluster/clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml:163` (note: `generated/` sits directly under `clusters/feather-core/`, **not** under `clusters/feather-core/talos/`). Putting it into a Flux Kustomization as well would create two writers for the same object, fighting on every Talos config apply and node boot. What is actually wrong with it — the `extraManifests` URL points at a mutable `main` branch, and the image tag is `:main` — is theme 9's finding and belongs in the Talos repo. Task 2 records this in the drift-detection allow-list so it stops being re-reported as drift.
- **Replacing or supplementing the CNI to make NetworkPolicies enforceable.** Finding `k8s-security/no-networkpolicy-enforcement-flannel` recommends Calico policy-only or Cilium. This cluster already took a cluster-wide Galera outage from a Flannel change (mixed-MTU rolling host-gw change, reverted to vxlan — note that `docs/incidents/` holds only the 2026-07-18 MariaDB/RGW write-up, so there is no in-repo record of this one to read). A CNI change is a large, separately-planned, node-by-node maintenance operation and does not belong in a plan whose other tasks are 5-minute reversible edits. Task 9 covers the part that is actionable today: making sure nobody reads the inert policies as protection.
- **Deleting the `dragonfly/dragonfly` NetworkPolicy.** The audit lists this as a quick win. It is not: the object has `ownerReferences` to the `Dragonfly` CR and `app.kubernetes.io/managed-by: dragonfly-operator`, so the operator recreates it. Task 9 documents it instead.
- **Editing `clusters/feather-core/flux-system/gotk-components.yaml`** to remove the three inert `flux-system` NetworkPolicies. That file is regenerated by `flux bootstrap`; hand-edits are lost and the policies are harmless. Documented in Task 10.
- **Rotating any credential.** Task 1's completion note scopes the exposure window for theme `crown-jewel-rotation-leaked-pki-and-credentials`; the rotation itself is that theme's work.
- **Turning on `auth_enabled` in Loki or `multitenancy_enabled` in Mimir/Tempo.** The correct enforcement point is the gateway edge, per the audit's own recommendation. Flipping tenancy in the component config would additionally break every in-cluster client, which the gateway policy does not.

---

### Task 1: Delete the unmanaged kubeshark install and the stale node-debugger pods

> ⚠️ **This task performs destructive `kubectl delete` operations against live cluster state and requires cluster-admin.** It is not a git change and there is no PR to revert. Do not start until **DG-1** is answered. Deleting the namespace ends any in-flight packet capture and discards captured traffic dumps permanently.

**Files:**
- Create: `$SCRATCH/kubeshark-namespace.yaml`, `$SCRATCH/kubeshark-namespaced.yaml`, `$SCRATCH/kubeshark-clusterrole.yaml`, `$SCRATCH/kubeshark-clusterrolebinding.yaml` (scratch exports, **not** committed)

**Interfaces:**
- Consumes: DG-1 answered "delete"
- Produces: a cluster with no privileged unmanaged DaemonSet; the exports above as the only rollback material; the exposure window that the credential-rotation theme needs

- [ ] **Step 1: Export everything before deleting anything**

```bash
kubectl get ns kubeshark-debug -o yaml > "$SCRATCH/kubeshark-namespace.yaml"
kubectl get all,cm,sa,role,rolebinding,netpol -n kubeshark-debug -o yaml > "$SCRATCH/kubeshark-namespaced.yaml"
kubectl get clusterrole kubeshark-cluster-role-kubeshark-debug -o yaml > "$SCRATCH/kubeshark-clusterrole.yaml"
kubectl get clusterrolebinding kubeshark-cluster-role-binding-kubeshark-debug -o yaml > "$SCRATCH/kubeshark-clusterrolebinding.yaml"
ls -l "$SCRATCH"/kubeshark-*.yaml
```

Expected: four non-empty files. These are exports of live objects and contain `status`/`resourceVersion`/`uid`; they are a record and a last-resort rollback source, not clean manifests.

- [ ] **Step 2: Record the current state for the write-up**

```bash
kubectl get ds -n kubeshark-debug kubeshark-worker-daemon-set \
  -o jsonpath='{.metadata.creationTimestamp}{"\t"}{.status.desiredNumberScheduled}{"\t"}{.spec.template.spec.containers[*].image}{"\n"}'
```

Expected (verified 2026-08-03): `2026-07-18T14:05:16Z` · `10` · `docker.io/kubeshark/worker:v53.3 docker.io/kubeshark/worker:v53.3`.

Write that line down. It is the **exposure window** — from 2026-07-18T14:05Z until this task completes, a `privileged: true`, `hostNetwork: true` DaemonSet with a hostPath mount of `/` ran on all 10 nodes including the three control planes, reassembling every packet crossing them. Every plaintext credential that crossed the wire in that window (MariaDB, Postgres, Dragonfly, Harbor↔RGW HTTP, `alloy`→`loki-gateway:80`, MaxScale admin Basic auth on `10.200.90.4:8989`) must be treated as observed. Hand this window to the `crown-jewel-rotation-leaked-pki-and-credentials` theme.

- [ ] **Step 3: Delete the cluster-scoped RBAC first**

Binding before role, and both before the namespace, so the ServiceAccount loses its cluster-wide grants (including `networkpolicies: [create,update,delete]` and `tokenreviews: create`) before the pods are torn down.

```bash
kubectl delete clusterrolebinding kubeshark-cluster-role-binding-kubeshark-debug
kubectl delete clusterrole kubeshark-cluster-role-kubeshark-debug
```

Expected: two `... deleted` lines.

- [ ] **Step 4: Delete the namespace**

```bash
kubectl delete namespace kubeshark-debug --timeout=180s
```

Expected: `namespace "kubeshark-debug" deleted`. This removes 2 Deployments (`kubeshark-front`, `kubeshark-hub`), the 10-pod `kubeshark-worker-daemon-set`, 4 Services, 2 ConfigMaps, the `kubeshark-service-account`, the namespaced Role/RoleBinding, and the 4 kubeshark NetworkPolicies.

If it hangs in `Terminating` past the timeout, check for a stuck finalizer with `kubectl get ns kubeshark-debug -o jsonpath='{.spec.finalizers}'` — expected `["kubernetes"]` only. Do **not** force-remove finalizers without first identifying what is blocking; `kubectl get all -n kubeshark-debug` will show what is left.

- [ ] **Step 5: Delete the three Completed node-debugger pods**

These are leftovers of `kubectl debug node/...` sessions (`Completed`, 7 d 18 h old, hostNetwork, unmanaged).

```bash
kubectl get pods -n rook-ceph-fr01 --no-headers | awk '/^node-debugger-/ {print $1}'
```

Expected (verified 2026-08-03): `node-debugger-fr01-str-01-fn42q`, `node-debugger-fr01-str-02-tx7hz`, `node-debugger-fr01-str-03-lrscl`. The suffixes are random — use the names the command actually prints:

```bash
kubectl delete pod -n rook-ceph-fr01 $(kubectl get pods -n rook-ceph-fr01 --no-headers | awk '/^node-debugger-/ {print $1}')
```

Expected: three `pod "..." deleted` lines.

**Rollback:** none is needed — nothing in the cluster depends on kubeshark, and removing it only stops packet capture. If DG-1 was answered wrongly and capture must resume, reinstall from a workstation (`kubeshark tap`) or, better, follow Appendix A. Do not `kubectl apply -f` the Step 1 exports blindly; they carry `status`, `resourceVersion` and `uid` and will be rejected or produce a partly-broken install.

---

### Task 2: Verify removal and establish the untracked-namespace drift check

**Files:** none (verification + a check to add to routine operations)

**Interfaces:**
- Consumes: Task 1 complete
- Produces: proof of removal; the standing check that catches this class of drift next time

- [ ] **Step 1: Confirm nothing kubeshark-shaped is left**

```bash
kubectl get ns kubeshark-debug
```
Expected: `Error from server (NotFound): namespaces "kubeshark-debug" not found`

```bash
kubectl get clusterrole,clusterrolebinding -o name | grep -c kubeshark
```
Expected: `0` (grep exits 1 with no match, so the command's exit status will be non-zero — read the printed `0`, not `$?`).

```bash
kubectl get ds -A --no-headers | grep -c kubeshark
```
Expected: `0`

```bash
kubectl get pods -n rook-ceph-fr01 --no-headers | grep -c node-debugger
```
Expected: `0`

- [ ] **Step 2: Confirm the NetworkPolicy count dropped from 8 to 4**

```bash
kubectl get netpol -A --no-headers | wc -l
```
Expected: `4` — `flux-system/allow-egress`, `flux-system/allow-scraping`, `flux-system/allow-webhooks`, `dragonfly/dragonfly`. The four kubeshark policies went with the namespace. Task 9 documents the remaining four.

- [ ] **Step 3: Run the untracked-namespace drift check**

```bash
kubectl get ns -o json | python3 -c '
import json,sys
allow = {"default","kube-system","kube-public","kube-node-lease","kubelet-serving-cert-approver"}
for ns in json.load(sys.stdin)["items"]:
    n = ns["metadata"]["name"]
    if n in allow: continue
    if "kustomize.toolkit.fluxcd.io/name" not in (ns["metadata"].get("labels") or {}):
        print("UNTRACKED:", n)
'
```

Expected: no output.

The allow-list entry `kubelet-serving-cert-approver` is deliberate: that namespace is created by Talos `extraManifests` from `/mnt/projects/lab/talos-cluster/clusters/feather-core/talos/defaults/roles/controlplane.yaml:17` (verified — that line is the `kubelet-serving-cert-approver` `standalone-install.yaml` URL), is owned by field manager `talos`, and must **not** be adopted into Flux (see "Deliberately out of scope"). That file lives in the **Talos repo**, not this one; no step of this plan edits it. The first four allow-list entries are Kubernetes built-ins.

- [ ] **Step 4: Confirm cluster health is unchanged**

```bash
flux get kustomizations -A | grep -v -E 'True|Reconciliation in progress|dependency .* is not ready'
```
Expected: only the header line — i.e. the same "no real failures, dependency flapping is normal" state as the pre-flight check (see the ⚠️ note there). Removing kubeshark touches no Flux-managed object, so this must be identical to pre-flight.

**Gate:** do not start Task 3 until Steps 1–4 all pass. If Step 1 shows leftovers, finish Task 1 before proceeding — the point of this plan's ordering is that the sniffer is gone before anything else changes.

---

### Task 3: Drop the MaxScale GUI LoadBalancer service (PR 1)

> ⚠️ This frees the MetalLB address `10.200.90.7`. Before merging, confirm nothing points at it: check uptime-kuma monitors, browser bookmarks, and any external monitoring probe. This plan could **not** enumerate clients of `10.200.90.7` read-only.

**Honest scope note:** this removes one of the **two** plaintext exposures of admin port 8989. The admin REST API and GUI remain reachable at `http://10.200.90.4:8989` because that Service publishes 8989 alongside 3306, and `MaxScale.spec.kubernetesService` has no per-port control. Closing that second exposure is **DG-4 / Task 11**. Do not describe this task as "fixing" the MaxScale finding.

**Files:**
- Modify: `infrastructure/clusters/feather-core/configs/mariadb-galera/maxscale.yaml` (delete lines 94–99)

**Interfaces:**
- Consumes: Task 2's gate passed
- Produces: `10.200.90.7` released; the `maxscale-galera-gui` Service gone

- [ ] **Step 1: Create the branch**

```bash
git checkout main
git pull --rebase origin main
git checkout -b fix/maxscale-drop-gui-loadbalancer
```

- [ ] **Step 2: Delete the `guiKubernetesService` block**

In `infrastructure/clusters/feather-core/configs/mariadb-galera/maxscale.yaml`, delete lines 94–99 in their entirety (and the now-doubled blank line):

```yaml
  guiKubernetesService:
    type: LoadBalancer
    externalTrafficPolicy: Local
    metadata:
      annotations:
        metallb.io/loadBalancerIPs: 10.200.90.7
```

The `kubernetesService` block at lines 87–92 stays exactly as it is — that is the `10.200.90.4` Service carrying 3306, which every application uses. After the edit, `metrics:` (previously line 101) follows directly after the `kubernetesService` block. Leave `spec.admin.guiEnabled: true` alone: the GUI is still wanted, just via `kubectl port-forward`, not via a MetalLB address.

- [ ] **Step 3: Render and verify**

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs/mariadb-galera | grep -c "10.200.90.7"
```
Expected: `0`

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs/mariadb-galera | grep -c "10.200.90.4"
```
Expected: `1`

- [ ] **Step 4: Validate**

```bash
./scripts/validate.sh
```
Expected: exits `0`; the `configs` group reports `Invalid: 0, Errors: 0`.

- [ ] **Step 5: Commit, push, open the PR**

```bash
git add infrastructure/clusters/feather-core/configs/mariadb-galera/maxscale.yaml
git commit -m "fix(mariadb): drop the maxscale gui loadbalancer service"
git pull --rebase origin main
git push -u origin fix/maxscale-drop-gui-loadbalancer
gh pr create --title "fix(mariadb): drop the maxscale gui loadbalancer service" --body "$(cat <<'EOF'
## Summary
- Removes `guiKubernetesService` from the MaxScale CR, freeing MetalLB address 10.200.90.7
- The GUI stays available via `kubectl port-forward svc/maxscale-galera -n mariadb-galera 8989:8989`
- Part of docs/superpowers/plans/2026-08-03-lan-exposure-and-unmanaged-sniffer.md

## Note
This removes one of two plaintext exposures of admin port 8989. The admin REST API
remains on 10.200.90.4:8989 over HTTP; closing that is decision gate DG-4 in the plan.

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] Confirmed nothing (bookmark, uptime-kuma monitor, probe) targets 10.200.90.7
- [ ] After merge: maxscale-galera-gui Service gone, maxscale-galera still on 10.200.90.4, apps unaffected
EOF
)"
```

Merging requires human judgment — do not merge automatically as part of this task.

**Rollback:** nothing has reached the cluster yet — this task only produces a branch and an open PR. To abandon it: `gh pr close <n>` and `git checkout main && git branch -D fix/maxscale-drop-gui-loadbalancer`. Post-merge rollback is Task 4's.

---

### Task 4: Merge PR 1 and verify (gate)

**Files:** none (operational)

- [ ] **Step 1: Merge PR 1** (`gh pr merge --squash` or the GitHub UI — confirm with the repo owner first)

- [ ] **Step 2: Reconcile once**

```bash
flux reconcile kustomization configs --with-source
```
Run once. Do not loop.

`configs` `dependsOn` `base-configs`, `controllers` and `rook`, so this command can legitimately fail with `dependency 'flux-system/rook' is not ready` if one of those happens to be mid-reconcile (see the pre-flight ⚠️ note — this flaps constantly). That is **not** an error in your change: wait 60 s and run the reconcile **once** more. Do not loop it. If it fails three times in a row with the same dependency message, stop and diagnose that dependency instead.

- [ ] **Step 3: Confirm the layer applied**

```bash
flux get kustomizations -A | grep -E 'configs'
```
Expected: the `configs` row `READY=True` at the new revision (`flux get` output is indented and namespaced, so anchoring the grep with `^configs` matches nothing — do not use `^`).

- [ ] **Step 4: Delete the orphaned Service**

The `maxscale-galera-gui` Service has an `ownerReference` to the `MaxScale` CR but is not Flux-managed, so neither Flux prune nor owner-ref GC removes it when the field disappears. Delete it explicitly:

```bash
kubectl delete svc -n mariadb-galera maxscale-galera-gui
```
Expected: `service "maxscale-galera-gui" deleted`

- [ ] **Step 5: Confirm the operator does not recreate it**

Wait 60 seconds, then:

```bash
kubectl get svc -n mariadb-galera
```
Expected: `maxscale-galera-gui` absent; `maxscale-galera` still `LoadBalancer 10.200.90.4  8989:.../TCP,3306:.../TCP`.

If the operator *does* recreate it, the field removal did not reach the live CR — check `kubectl get maxscale -n mariadb-galera maxscale-galera -o jsonpath='{.spec.guiKubernetesService}'` (expected: empty) before deleting again.

- [ ] **Step 6: Confirm the data path is untouched**

```bash
kubectl get pods -n mariadb-galera -l app.kubernetes.io/name=maxscale
```
Expected: both `maxscale-galera-*` pods `Running`, **restart count unchanged** from before the merge. Removing a Service must not restart the pods; if they restarted, something else changed — stop and investigate.

```bash
kubectl get maxscale -n mariadb-galera maxscale-galera -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}'
```
Expected: `True`

**Gate:** all six steps must pass before Task 5. **Rollback:** revert the PR 1 merge commit on `main`; the operator recreates the GUI Service on the next reconcile (MetalLB will re-issue `10.200.90.7` if the pool still has it free). No data-plane state is involved.

---

### Task 5: Mint `{SHA}` htpasswd credentials for tempo, mimir and loki

> ⚠️ This task generates plaintext passwords. They must be written to the operator's password manager and must never be committed, pasted into a PR body, or left in shell history files. Everything written into the repo is SOPS-encrypted.

**Files:**
- Create: `apps/clusters/feathre-core/monitoring/tempo/tempo-ingress-auth.sops.conf`
- Rewrite: `apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf` (currently `loki:$apr1$…`, unusable — see Correction 3)
- Rewrite: `apps/clusters/feathre-core/monitoring/mimir/mimir-ingress-auth.sops.conf` (currently `metrics-writer:$apr1$…`, unusable)

**Interfaces:**
- Consumes: DG-2 answered
- Produces: three SOPS-encrypted single-line htpasswd files with `{SHA}` hashes, consumed by Tasks 6 and 8

- [ ] **Step 1: Create the branch for PR 2**

```bash
git checkout main
git pull --rebase origin main
git checkout -b feat/monitoring-gateway-basic-auth
```

- [ ] **Step 2: Confirm you can decrypt (fails fast if the PGP key is missing)**

```bash
sops -d apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf | cut -c1-11
```
Expected: `loki:$apr1$` — confirming both that the key works and that the existing hash is the unusable Apache-MD5 variant.

- [ ] **Step 3: Generate the three credentials**

`htpasswd` is not installed on this workstation; `openssl` produces the identical `{SHA}` output.

```bash
for svc in tempo mimir loki; do
  case "$svc" in
    tempo) user=tempo ;;
    mimir) user=metrics-writer ;;
    loki)  user=loki ;;
  esac
  pass="$(openssl rand -base64 24)"
  hash="$(printf '%s' "$pass" | openssl dgst -sha1 -binary | openssl base64)"
  printf '%s:{SHA}%s\n' "$user" "$hash" > "$SCRATCH/${svc}-htpasswd"
  printf '%s\t%s\t%s\n' "$svc" "$user" "$pass" >> "$SCRATCH/gateway-credentials.txt"
done
cat "$SCRATCH"/*-htpasswd
```

Expected: three lines of the form `tempo:{SHA}…=`, `metrics-writer:{SHA}…=`, `loki:{SHA}…=`.

**Now open `$SCRATCH/gateway-credentials.txt`, copy the three passwords into the password manager, and confirm they are stored.** Tasks 6 and 8 need them for the positive-auth verification curl, and nothing else in the world will ever be able to recover them.

- [ ] **Step 4: Encrypt each into its repo path**

`--filename-override` makes SOPS pick the creation rule from the *destination* path (`.sops.conf` → the root `/.sops.yaml` rule) while reading the plaintext from scratch, so no plaintext ever exists inside the working tree.

```bash
sops --encrypt --filename-override apps/clusters/feathre-core/monitoring/tempo/tempo-ingress-auth.sops.conf \
  "$SCRATCH/tempo-htpasswd" > apps/clusters/feathre-core/monitoring/tempo/tempo-ingress-auth.sops.conf

sops --encrypt --filename-override apps/clusters/feathre-core/monitoring/mimir/mimir-ingress-auth.sops.conf \
  "$SCRATCH/mimir-htpasswd" > apps/clusters/feathre-core/monitoring/mimir/mimir-ingress-auth.sops.conf

sops --encrypt --filename-override apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf \
  "$SCRATCH/loki-htpasswd" > apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf
```

- [ ] **Step 5: Verify the round-trip and the recipient**

```bash
for f in apps/clusters/feathre-core/monitoring/{tempo/tempo,mimir/mimir,loki/loki}-ingress-auth.sops.conf; do
  echo -n "$f -> "; sops -d "$f" | cut -d'{' -f1
done
```
Expected:
```
apps/clusters/feathre-core/monitoring/tempo/tempo-ingress-auth.sops.conf -> tempo:
apps/clusters/feathre-core/monitoring/mimir/mimir-ingress-auth.sops.conf -> metrics-writer:
apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf -> loki:
```

```bash
grep -c '"fp": "0231831CB40B8E587B7353CBA3AF727721205A62"' \
  apps/clusters/feathre-core/monitoring/tempo/tempo-ingress-auth.sops.conf
```
Expected: `1` — the file is encrypted to the cluster's `sops-gpg` recipient, so Flux can decrypt it. If this is `0`, the creation rule did not match; re-check the `--filename-override` path.

```bash
git diff --stat
```
Expected: exactly the two rewritten `.sops.conf` files (the tempo one is new and untracked). **No plaintext file may appear.** Confirm with `git status --short` that nothing under `$SCRATCH` leaked into the tree.

- [ ] **Step 6: Scrub the plaintext htpasswd scratch files once they are encrypted**

```bash
shred -u "$SCRATCH"/tempo-htpasswd "$SCRATCH"/mimir-htpasswd "$SCRATCH"/loki-htpasswd
```

Keep `$SCRATCH/gateway-credentials.txt` until **both** Task 6 Step 7 and Task 8 Step 7 have used the passwords for their positive-auth curls *and* the passwords are confirmed in the password manager, then `shred -u` it. Do not shred it at the end of this task — Task 8 is a separate session and there is no way to recover a shredded password.

- [ ] **Step 7: Commit all three encrypted files now, on the PR 2 branch**

All three `.sops.conf` files are re-encrypted here, but Task 6 only wires up *tempo*. Commit all three anyway — an `.sops.conf` that no `kustomization.yaml` references is inert on the cluster (nothing generates a Secret from it), and leaving mimir/loki as uncommitted working-tree changes is a trap: Task 8 Step 1 starts with `git pull --rebase origin main`, which **aborts** with `cannot pull with rebase: You have unstaged changes` on a dirty tree, and re-running Task 5 to recover would mint *different* passwords from the ones already stored in the password manager.

```bash
git add apps/clusters/feathre-core/monitoring/tempo/tempo-ingress-auth.sops.conf \
        apps/clusters/feathre-core/monitoring/mimir/mimir-ingress-auth.sops.conf \
        apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf
git status --short
```
Expected: exactly three `A `/`M ` lines and nothing else staged or modified. Do not commit yet — Task 6 Step 5 makes the single PR 2 commit.

**Rollback:** this task changes only tracked encrypted files on a throwaway branch and touches nothing live. To undo before committing:

```bash
git checkout -- apps/clusters/feathre-core/monitoring/mimir/mimir-ingress-auth.sops.conf \
                apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf
rm -f apps/clusters/feathre-core/monitoring/tempo/tempo-ingress-auth.sops.conf
```
Expected afterwards: `git status --short` clean. The old Apache-MD5 hashes come back, which is harmless — nothing consumes them today.

---

### Task 6: Attach a Basic-auth SecurityPolicy to the tempo route (PR 2, canary)

**Files:**
- Create: `apps/clusters/feathre-core/monitoring/tempo/securitypolicy.yaml`
- Modify: `apps/clusters/feathre-core/monitoring/tempo/kustomization.yaml`

**Interfaces:**
- Consumes: `tempo-ingress-auth.sops.conf` from Task 5
- Produces: a working, verified SecurityPolicy pattern that Task 8 copies for mimir and loki

> ⚠️ **Before merging, check for an external prober on these hostnames.** The "no production clients" evidence is a **7 h 11 min** window of Envoy access logs (the depth of the `envoy-envoy-eg-*` pods' buffers). A monitor that runs hourly would have appeared; one that runs daily or weekly would not. Open uptime-kuma and confirm no monitor targets `tempo.`, `mimir.` or `loki.apps.onelite.feather`, and ask the operator whether any scheduled job or dashboard outside the cluster scrapes them. If one exists, it starts failing with `401` the moment the policy applies — give it the credential from Task 5 first. Same check applies to Task 8's two hostnames.

**Why tempo first:** it is the smallest-consequence of the three (2 requests in 7 h, both the auditor's probes) and its tag list is the finding's most alarming evidence — `db.statement`, `db.connection_string`, `db.user`, `aws.s3.bucket` are all currently readable unauthenticated. If the mechanism is going to misbehave, it misbehaves here on one route rather than on all three at once.

- [ ] **Step 1: Create the SecurityPolicy**

Create `apps/clusters/feathre-core/monitoring/tempo/securitypolicy.yaml`:

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: tempo-basic-auth
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: tempo
  basicAuth:
    users:
      name: tempo-ingress-auth
```

Notes, all verified against the CRD vendored at `infrastructure/base/controllers/envoy-crds/crds.yaml`:
- No `namespace:` is set in this file — the overlay's `namespace: grafana` transformer stamps it. (Verified by rendering: kustomize applies the namespace transformer to `SecurityPolicy` even though it is an unknown CRD.) Note the only other `SecurityPolicy` in the repo, `rgw-external-proxy-cors` at `infrastructure/clusters/feather-core/base-configs/s3-proxy.yaml:74-84`, does the *opposite* — it hard-codes `namespace: storage-proxy` at line 77 because it is not under a namespace-setting overlay. Use it as a shape reference for `targetRefs`, not for the namespace handling.
- `basicAuth.users` is a Secret reference and **the Secret must live in the same namespace as the SecurityPolicy** (`grafana`). It does.
- `targetRefs` is namespace-local; the `tempo` HTTPRoute is in `grafana`.
- Do **not** add `forwardUsernameHeader` — Tempo has no use for it.

- [ ] **Step 2: Register the policy and the secret in the overlay**

Replace `apps/clusters/feathre-core/monitoring/tempo/kustomization.yaml` with:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: grafana
generatorOptions:
  disableNameSuffixHash: true
resources:
  - ../../../../../apps/base/tempo
  - httproute.yaml
  - securitypolicy.yaml
patches:
  - path: release.yaml

secretGenerator:
  - name: tempo-s3
    envs:
      - tempo-s3.sops.env
  - name: tempo-ingress-auth
    files:
      - .htpasswd=tempo-ingress-auth.sops.conf
```

The `.htpasswd=` rename is mandatory: Envoy Gateway reads only that exact key. The `files:` + SOPS-input pattern is already proven in this repo at `apps/clusters/feathre-core/base-apps/bluemap/kustomization.yaml:26-27` and `.../outline/kustomization.yaml:14-15`.

- [ ] **Step 3: Render and verify**

```bash
kubectl kustomize apps/clusters/feathre-core/monitoring/tempo | grep -A8 "kind: SecurityPolicy"
```
Expected:
```yaml
kind: SecurityPolicy
metadata:
  name: tempo-basic-auth
  namespace: grafana
spec:
  basicAuth:
    users:
      name: tempo-ingress-auth
  targetRefs:
```

```bash
kubectl kustomize apps/clusters/feathre-core/monitoring/tempo | grep -c "\.htpasswd:"
```
Expected: `1`

- [ ] **Step 4: Validate**

```bash
./scripts/validate.sh
```
Expected: exits `0`. `SecurityPolicy` is a CRD, so kubeconform skips it under `-ignore-missing-schemas`; the generated Secret is skipped by `-skip Secret`. What this step actually proves is that the overlay still *builds* — which is the failure mode that matters (a mistyped `files:` entry breaks the build outright).

- [ ] **Step 5: Commit, push, open PR 2**

```bash
git add apps/clusters/feathre-core/monitoring/tempo/securitypolicy.yaml \
        apps/clusters/feathre-core/monitoring/tempo/kustomization.yaml \
        apps/clusters/feathre-core/monitoring/tempo/tempo-ingress-auth.sops.conf \
        apps/clusters/feathre-core/monitoring/mimir/mimir-ingress-auth.sops.conf \
        apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf
git status --short   # expect exactly these 5 paths, nothing unstaged
git commit -m "feat(monitoring): require basic auth on the tempo gateway route"
git pull --rebase origin main
git push -u origin feat/monitoring-gateway-basic-auth
gh pr create --title "feat(monitoring): require basic auth on the tempo gateway route" --body "$(cat <<'EOF'
## Summary
- Adds an Envoy Gateway SecurityPolicy with basicAuth to the `tempo` HTTPRoute
- Adds a SOPS-encrypted `{SHA}` htpasswd as `tempo-ingress-auth` (key `.htpasswd`)
- Also re-encrypts the pre-existing `mimir-` and `loki-ingress-auth.sops.conf` from Apache-MD5
  to `{SHA}` (Envoy Gateway supports only SHA). Neither is referenced by a kustomization yet,
  so both are inert on the cluster until the follow-up PR wires them up.
- Canary for the same change on mimir and loki (docs/superpowers/plans/2026-08-03-lan-exposure-and-unmanaged-sniffer.md)

## Blast radius
None measurable. Grafana's Tempo datasource and Alloy's OTLP exporter both use
`tempo-gateway.grafana.svc.cluster.local` and never traverse the gateway. Over a
7h11m window of Envoy access logs, `tempo.apps.onelite.feather` served 2 requests,
both `curl/8.21.0` probes from the auditor's workstation.

## Test plan
- [x] ./scripts/validate.sh passes
- [ ] After merge: unauthenticated GET /api/search/tags returns 401, authenticated returns 200
- [ ] Grafana Tempo datasource still healthy, trace ingestion unaffected
EOF
)"
```

Merging requires human judgment — do not merge automatically.

- [ ] **Step 6: Merge, reconcile once, verify the policy was accepted**

Merge PR 2 (`gh pr merge --squash` or the GitHub UI — confirm with the repo owner first), then:

```bash
flux reconcile kustomization monitoring --with-source
```

Run once. Do not loop. `monitoring` `dependsOn` `configs`, so a `dependency 'flux-system/configs' is not ready` failure here is the usual flapping (see the pre-flight ⚠️ note) — wait 60 s and run it once more.

```bash
flux get kustomizations -A | grep monitoring
```
Expected: `READY=True` at the new revision (the squash-merge SHA).

```bash
kubectl get securitypolicy -n grafana tempo-basic-auth \
  -o jsonpath='{.status.ancestors[0].conditions[?(@.type=="Accepted")].reason}{"\n"}'
```
Expected: `Accepted`. Any other value (`Invalid`, `NotFound`, empty) means the policy did not attach — read the full `message` field before proceeding.

```bash
kubectl get secret -n grafana tempo-ingress-auth -o jsonpath='{.data.\.htpasswd}' | base64 -d | cut -d'{' -f1
```
Expected: `tempo:`

This is also the check that Flux actually **decrypted** the file. If the output is empty, the Secret still holds the raw SOPS JSON (which starts with `{`, so `cut -d'{' -f1` prints nothing) — the `monitoring` Kustomization's `decryption` block did not apply to it. Confirm with `kubectl get kustomization -n flux-system monitoring -o jsonpath='{.spec.decryption}'` (expected `{"provider":"sops","secretRef":{"name":"sops-gpg"}}`) and stop; do **not** proceed to Step 7, because an undecrypted htpasswd makes the route return `401` for *every* credential, including the correct one.

- [ ] **Step 7: Prove enforcement from the LAN**

```bash
curl -sk -o /dev/null -w '%{http_code}\n' \
  --resolve tempo.apps.onelite.feather:443:10.200.90.1 \
  https://tempo.apps.onelite.feather/api/search/tags
```
Expected: `401` (was `200` with a full tag list including `db.statement`, `db.connection_string`, `db.user`, `aws.s3.bucket`).

```bash
curl -sk -o /dev/null -w '%{http_code}\n' -u "tempo:<PASSWORD-FROM-TASK-5>" \
  --resolve tempo.apps.onelite.feather:443:10.200.90.1 \
  https://tempo.apps.onelite.feather/api/search/tags
```
Expected: `200`.

If the first returns `200`, the policy is not in the data path yet — Envoy Gateway needs a moment to push xDS. Wait 30 s and retry. If it is still `200` after two minutes, or if the second returns `401`, restart the control plane once:

```bash
kubectl -n envoy rollout restart deploy envoy-gateway
kubectl -n envoy rollout status deploy envoy-gateway --timeout=120s
```

then re-run both curls. (The Secret name is stable because of `disableNameSuffixHash: true`, so a content change does not roll anything by itself; `envoy-gateway` watches the Secret and normally re-renders without help — this restart is the fallback, not the expected path.)

- [ ] **Step 8: Prove nothing in-cluster broke**

```bash
kubectl exec -n grafana deploy/grafana -- \
  curl -s -o /dev/null -w '%{http_code}\n' http://tempo-gateway.grafana.svc.cluster.local/api/search/tags
```
Expected: `200` — the in-cluster path is deliberately unauthenticated and must stay that way.

Then open Grafana at `https://grafana.apps.onelite.feather`, go to Connections → Data sources → Tempo → **Save & test**. Expected: green. And confirm trace ingestion is still flowing:

```bash
kubectl logs -n grafana -l app.kubernetes.io/instance=alloy-receiver --tail=50 | grep -ci error
```
Expected: `0`.

**Rollback:** revert the PR 2 merge commit on `main` and `flux reconcile kustomization monitoring --with-source`. Flux prunes the SecurityPolicy and the route is unauthenticated again within one reconcile. No data is involved.

---

### Task 7: Health gate before extending to mimir and loki

**Files:** none (operational)

- [ ] **Step 1:** `flux get kustomizations -A | grep -v -E 'True|Reconciliation in progress|dependency .* is not ready'` → only the header line (see the pre-flight ⚠️ note: dependency flapping is normal, a non-dependency error is not).
- [ ] **Step 2:** `kubectl get securitypolicy -A` → two rows: `storage-proxy/rgw-external-proxy-cors` and `grafana/tempo-basic-auth`, both `Accepted`.
- [ ] **Step 3:** In Grafana, run a trace search covering the last 15 minutes. Expected: results present — proving ingestion continued *through* the policy change, not just that the datasource pings.
- [ ] **Step 4:** Give it at least one full hour of production traffic before Task 8. The point of a canary is elapsed time, not a passing test one minute after apply.

**Gate:** if any step fails, roll back PR 2 (Task 6 rollback) and diagnose before touching mimir or loki. Do not "fix forward" onto two more routes.

---

### Task 8: Attach Basic-auth SecurityPolicies to the mimir and loki routes (PR 3)

**Files:**
- Create: `apps/clusters/feathre-core/monitoring/mimir/securitypolicy.yaml`
- Create: `apps/clusters/feathre-core/monitoring/loki/securitypolicy.yaml`
- Modify: `apps/clusters/feathre-core/monitoring/mimir/kustomization.yaml`
- Modify: `apps/clusters/feathre-core/monitoring/loki/kustomization.yaml`

**Interfaces:**
- Consumes: Task 7's gate passed; the two rewritten `.sops.conf` files from Task 5
- Produces: all three observability data planes authenticated at the edge

Loki is last deliberately: its `loki-gateway` nginx config proxies `/loki/api/v1/push`, `/api/prom/push`, `/otlp/v1/logs` **and `/loki/api/v1/delete`** with `auth_basic off;`, and the compactor has `retention_enabled: true` with `delete_request_store: s3` — so the delete API is armed. It is the highest-consequence route and gets the most-proven mechanism.

- [ ] **Step 1: Branch from the post-PR-2 `main`**

```bash
git checkout main
git pull --rebase origin main
git checkout -b feat/monitoring-gateway-basic-auth-loki-mimir
```

Task 5 Step 7 committed all three re-encrypted `.sops.conf` files in PR 2, so `main` already carries the `{SHA}` hashes for mimir and loki. **Verify that, do not regenerate:**

```bash
for f in mimir/mimir loki/loki; do
  echo -n "$f: "
  sops -d "apps/clusters/feathre-core/monitoring/${f}-ingress-auth.sops.conf" | grep -o '{SHA}' || echo "STILL-APR1"
done
```
Expected: `mimir/mimir: {SHA}` and `loki/loki: {SHA}`.

If either prints `STILL-APR1`, PR 2 did not carry the file. Recover it from the PR 2 branch — **do not re-run Task 5 Step 3**, which mints new passwords and silently invalidates the ones already in the password manager:

```bash
git checkout feat/monitoring-gateway-basic-auth -- \
  apps/clusters/feathre-core/monitoring/mimir/mimir-ingress-auth.sops.conf \
  apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf
```

Only if that branch is also gone (deleted on merge) and the passwords are irrecoverable do you re-run Task 5 Steps 3–6 for mimir and loki — and then you must record the new passwords in the password manager before continuing.

> ⚠️ `git pull --rebase origin main` above **aborts on a dirty working tree** (`cannot pull with rebase: You have unstaged changes`). Run `git status --short` first; it must be clean before you start this task.

- [ ] **Step 2: Create `apps/clusters/feathre-core/monitoring/mimir/securitypolicy.yaml`**

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: mimir-basic-auth
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mimir
  basicAuth:
    users:
      name: mimir-ingress-auth
```

- [ ] **Step 3: Create `apps/clusters/feathre-core/monitoring/loki/securitypolicy.yaml`**

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: loki-basic-auth
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: loki
  basicAuth:
    users:
      name: loki-ingress-auth
```

- [ ] **Step 4: Register both in their overlays**

`apps/clusters/feathre-core/monitoring/mimir/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: grafana
generatorOptions:
  disableNameSuffixHash: true
resources:
  - ../../../../../apps/base/mimir
  - httproute.yaml
  - securitypolicy.yaml
patches:
  - path: release.yaml

secretGenerator:
  - name: mimir-s3
    envs:
      - mimir-s3.sops.env
  - name: mimir-ingress-auth
    files:
      - .htpasswd=mimir-ingress-auth.sops.conf
```

`apps/clusters/feathre-core/monitoring/loki/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: grafana
generatorOptions:
  disableNameSuffixHash: true
resources:
  - ../../../../../apps/base/loki
  - httproute.yaml
  - securitypolicy.yaml
patches:
  - path: release.yaml

secretGenerator:
  - name: loki-s3
    envs:
      - loki-s3.sops.env
  - name: loki-ingress-auth
    files:
      - .htpasswd=loki-ingress-auth.sops.conf
```

`mimir-remote-write.sops.env` is intentionally left unreferenced — it is not part of this change and wiring it up is not in scope.

- [ ] **Step 5: Render, validate, commit**

```bash
for d in mimir loki; do
  echo "== $d"; kubectl kustomize "apps/clusters/feathre-core/monitoring/$d" | grep -c "kind: SecurityPolicy"
done
```
Expected: `1` for each.

```bash
./scripts/validate.sh
```
Expected: exits `0`.

```bash
git add apps/clusters/feathre-core/monitoring/mimir apps/clusters/feathre-core/monitoring/loki
git commit -m "feat(monitoring): require basic auth on the loki and mimir gateway routes"
git pull --rebase origin main
git push -u origin feat/monitoring-gateway-basic-auth-loki-mimir
gh pr create --title "feat(monitoring): require basic auth on the loki and mimir gateway routes" --body "$(cat <<'EOF'
## Summary
- Extends the tempo SecurityPolicy pattern to the `mimir` and `loki` HTTPRoutes
- Wires up the `mimir-` and `loki-ingress-auth.sops.conf` htpasswd files that PR 2 already
  re-encrypted from Apache-MD5 to `{SHA}` (Envoy Gateway supports only SHA — see the CRD note
  at infrastructure/base/controllers/envoy-crds/crds.yaml:25608). They were inert until now.
- Closes the unauthenticated push and delete APIs on loki-gateway, whose nginx.conf has `auth_basic off;`
  and proxies /loki/api/v1/push, /api/prom/push, /otlp/v1/logs and /loki/api/v1/delete

## Blast radius
None measurable. All in-cluster clients use *-gateway.grafana.svc.cluster.local.
Over a 7h11m window, loki.apps.onelite.feather served 3 requests and
mimir.apps.onelite.feather 2 — all `curl/8.21.0` probes from the auditor.

## Test plan
- [x] ./scripts/validate.sh passes
- [x] Same mechanism already proven on tempo for >1h in production
- [ ] After merge: unauthenticated 401 / authenticated 200 on both hosts
- [ ] Grafana Loki + Mimir datasources healthy, log and metric ingestion unaffected
EOF
)"
```

- [ ] **Step 6: Merge, reconcile once, verify**

Merge PR 3 (confirm with the repo owner first), then:

```bash
flux reconcile kustomization monitoring --with-source
```

Run once. Do not loop; a `dependency 'flux-system/configs' is not ready` failure is the usual flapping — wait 60 s and retry once.

```bash
flux get kustomizations -A | grep monitoring
```
Expected: `READY=True` at the new revision.

```bash
for p in mimir-basic-auth loki-basic-auth; do
  echo -n "$p: "
  kubectl get securitypolicy -n grafana "$p" \
    -o jsonpath='{.status.ancestors[0].conditions[?(@.type=="Accepted")].reason}{"\n"}'
done
```
Expected: `mimir-basic-auth: Accepted` and `loki-basic-auth: Accepted`.

```bash
for s in mimir-ingress-auth loki-ingress-auth; do
  echo -n "$s: "
  kubectl get secret -n grafana "$s" -o jsonpath='{.data.\.htpasswd}' | base64 -d | cut -d'{' -f1
done
```
Expected: `mimir-ingress-auth: metrics-writer:` and `loki-ingress-auth: loki:`. An empty value means Flux did not decrypt the file (the Secret holds raw SOPS JSON starting with `{`) — in that state the route rejects **every** credential including the correct one. Stop and fix decryption before Step 7; do not leave loki in that state, because a `401` for everyone is indistinguishable from a working policy in the Step 7 negative test.

- [ ] **Step 7: Prove enforcement, including the write and delete paths**

```bash
curl -sk -o /dev/null -w 'mimir-read %{http_code}\n' \
  --resolve mimir.apps.onelite.feather:443:10.200.90.1 \
  https://mimir.apps.onelite.feather/prometheus/api/v1/label/__name__/values
curl -sk -o /dev/null -w 'loki-read  %{http_code}\n' \
  --resolve loki.apps.onelite.feather:443:10.200.90.1 \
  https://loki.apps.onelite.feather/loki/api/v1/labels
curl -sk -o /dev/null -w 'loki-push  %{http_code}\n' -X POST \
  --resolve loki.apps.onelite.feather:443:10.200.90.1 \
  https://loki.apps.onelite.feather/loki/api/v1/push
curl -sk -o /dev/null -w 'loki-del   %{http_code}\n' \
  --resolve loki.apps.onelite.feather:443:10.200.90.1 \
  'https://loki.apps.onelite.feather/loki/api/v1/delete?query={job="x"}'
```
Expected: all four `401`. The last two are the point of this task — they were previously reachable without credentials.

```bash
curl -sk -o /dev/null -w '%{http_code}\n' -u "loki:<PASSWORD-FROM-TASK-5>" \
  --resolve loki.apps.onelite.feather:443:10.200.90.1 \
  https://loki.apps.onelite.feather/loki/api/v1/labels
```
Expected: `200`.

- [ ] **Step 8: Prove nothing in-cluster broke**

```bash
kubectl exec -n grafana deploy/grafana -- sh -c '
  curl -s -o /dev/null -w "loki  %{http_code}\n" http://loki-gateway.grafana.svc.cluster.local/loki/api/v1/labels
  curl -s -o /dev/null -w "mimir %{http_code}\n" http://mimir-gateway.grafana.svc.cluster.local/prometheus/api/v1/label/__name__/values
'
```
Expected: `loki 200`, `mimir 200`.

In Grafana, **Save & test** the Loki and Mimir datasources (both green), then run `{cluster="feather-core"} |= ""` over the last 5 minutes and confirm fresh log lines are arriving — proving `alloy-logs` ingestion survived, not just that the datasource responds.

```bash
kubectl logs -n grafana -l app.kubernetes.io/instance=alloy-logs --tail=100 | grep -ci "401\|unauthorized"
```
Expected: `0`.

**Rollback:** revert the PR 3 merge commit and reconcile once. Both policies are pruned; both routes are unauthenticated again. The re-encrypted `.sops.conf` files revert with the commit, which is harmless because nothing else reads them.

---

### Task 9: Document that Flannel enforces no NetworkPolicies (PR 4)

**Files:**
- Create: `docs/networkpolicy-enforcement.md`

**Interfaces:**
- Consumes: Task 2's confirmation that the count dropped from 8 to 4
- Produces: a document that stops anyone reading `kubectl get netpol` as evidence of segmentation

This is the actionable half of finding `k8s-security/no-networkpolicy-enforcement-flannel`. The CNI change it recommends is explicitly out of scope (see above); making the remaining four inert policies legible is not.

- [ ] **Step 1: Branch**

```bash
git checkout main
git pull --rebase origin main
git checkout -b docs/networkpolicy-enforcement
```

- [ ] **Step 2: Create `docs/networkpolicy-enforcement.md`**

```markdown
# NetworkPolicies are not enforced on this cluster

`feather-core` runs Flannel (`kube-system/kube-flannel`,
`ghcr.io/siderolabs/flannel:v0.28.5`) as its only CNI. Flannel implements pod
networking and ships **no NetworkPolicy controller**, and no policy agent
(Calico, Cilium, kube-router) runs alongside it. Every `NetworkPolicy` object in
this cluster is therefore inert: it exists in the API server and is never
translated into a packet filter.

**Do not read `kubectl get netpol` as evidence that anything is segmented.**

## The four policies that exist, and who put them there

| Namespace | Name | Origin | What it *looks* like it does |
|---|---|---|---|
| `flux-system` | `allow-egress` | `flux bootstrap`, in `clusters/feather-core/flux-system/gotk-components.yaml` | default-deny cross-namespace ingress to the Flux controllers |
| `flux-system` | `allow-scraping` | same | allow Prometheus to scrape the controllers |
| `flux-system` | `allow-webhooks` | same | allow inbound webhooks to `notification-controller` |
| `dragonfly` | `dragonfly` | generated by `dragonfly-operator`, `ownerReferences` → the `Dragonfly` CR | restrict 6379 to same-namespace pods |

`dragonfly/dragonfly` is the most misleading of the four — it reads as if Redis
is namespace-restricted. It is not, and it cannot be deleted: the operator
recreates it. The three `flux-system` policies are part of the Flux bootstrap
manifest, which `flux bootstrap` regenerates; hand-edits there do not survive.

This repository declares **zero** NetworkPolicies of its own
(`grep -rln "kind: NetworkPolicy" apps infrastructure helm clusters` matches only
`gotk-components.yaml`), so nothing written here depends on enforcement.

## What this actually costs

There is no L3 barrier between internet-facing workloads (plane, reposilite,
shlink, uptime-kuma, harbor) and the datastores — MariaDB 3306, CNPG 5432,
Dragonfly 6379, Ceph RGW. A single RCE in any of them has unrestricted reach
across `10.244.0.0/16`, and there is no policy-drop telemetry to alert on.

This does **not** put `flux-system/sops-gpg` or the Flux git SSH credential at
network reach: those are Secrets, gated by RBAC and reachable only through the
API server or a container escape, neither of which a NetworkPolicy would have
stopped.

## If this is ever fixed

Add a policy-only enforcement agent rather than swapping the CNI: Calico in
policy-only mode (`calico-node` with `CALICO_NETWORKING_BACKEND=none`) keeps
Flannel's dataplane. This cluster has already taken a cluster-wide Galera outage
from a Flannel change (a mixed-MTU rolling host-gw change; it was reverted to
vxlan and is **not** written up under `docs/incidents/`), so it must be done
node-by-node in a maintenance window. Once enforcement is live, start with
default-deny-ingress in `mariadb-galera`, `cnpg-system`, `dragonfly` and
`flux-system` only — a cluster-wide default-deny across ~30 workloads will take
applications down.

*Historical note:* until 2026-08-03 there were eight policies. The other four
belonged to an unmanaged `kubeshark-debug` install that was removed along with
its namespace — see
`docs/superpowers/plans/2026-08-03-lan-exposure-and-unmanaged-sniffer.md`.
```

- [ ] **Step 3: Validate and commit**

```bash
./scripts/validate.sh
```
Expected: exits `0` (this PR adds no manifests, so the run is a no-op regression check).

```bash
git add docs/networkpolicy-enforcement.md
git commit -m "docs(security): record that flannel enforces no networkpolicies"
git pull --rebase origin main
git push -u origin docs/networkpolicy-enforcement
gh pr create --title "docs(security): record that flannel enforces no networkpolicies" \
  --body "Documents that all four remaining NetworkPolicies are inert under Flannel, why dragonfly/dragonfly and the flux-system ones cannot simply be deleted, and what a real fix would involve. Part of docs/superpowers/plans/2026-08-03-lan-exposure-and-unmanaged-sniffer.md"
```

- [ ] **Step 4: Confirm the file is not picked up as a manifest**

```bash
git show --stat HEAD | tail -3
```
Expected: one file changed, `docs/networkpolicy-enforcement.md`. `docs/` is outside every Flux `spec.path` and outside `validate.sh`'s discovered build paths, so this PR cannot affect the cluster.

**Rollback:** `git revert` the merge commit, or simply delete the file in a follow-up PR. Nothing on the cluster reads `docs/`, so there is nothing to reconcile and no verification beyond the merge itself.

### Task 10 (decision gate DG-3): authentication for the `otel` OTLP intake

> **Do not execute without answering DG-3.** This route carries all external telemetry ingestion.

**Files:** none until DG-3 is answered.

**Evidence the decision rests on:** over 7 h 11 min of Envoy access logs, `otel.apps.onelite.feather` served **25 455 requests from exactly one source, `10.200.2.35`**, user-agent `OTel-OTLP-Exporter-Java/1.63.0`, hitting `/v1/logs` and `/v1/metrics`. Two routes carry it: `apps/clusters/feathre-core/base-apps/alloy-receiver/httproute.yaml` (HTTP/4318) and `.../grpcroute.yaml` (gRPC/4317). The in-repo comment at `.../alloy-receiver/release.yaml:55-57` states the current rationale: *"otel.apps.onelite.feather is only reachable over the internal step-ca-issued TLS network (not the public internet), so no auth layer is needed here."* That rationale is about reachability from the internet, not from the LAN, and the LAN is exactly the threat this theme is about.

**If DG-3 = (c), CIDR allow-list (recommended first step):** create `apps/clusters/feathre-core/base-apps/alloy-receiver/securitypolicy.yaml` with a `SecurityPolicy` using `spec.authorization` — `defaultAction: Deny` and one rule with `action: Allow` and `principal.clientCIDRs` set to the subnet that `10.200.2.35` belongs to. **Before writing the CIDR, confirm with the operator which subnet the OTLP fleet actually occupies** — this plan observed one source address over one window and must not be assumed to be the complete list. Attach two policies (one per route) or one policy with two `targetRefs`, covering both the `HTTPRoute` and the `GRPCRoute` — an HTTP-only policy leaves gRPC/4317 wide open. Verify with a `curl` from a host outside the allowed range (expect `403`) and by confirming the request rate at `10.200.2.35` is unchanged in the Envoy logs.

**If DG-3 = (b), Basic auth:** the same `basicAuth` pattern as Tasks 6/8, but the exporter's `OTEL_EXPORTER_OTLP_HEADERS` must carry `Authorization: Basic …` **before** the policy merges, and that configuration lives outside this repository. Sequence it as: update the client → confirm it still ingests → merge the policy → confirm ingestion continues. A policy merged first stops all external telemetry immediately.

**If DG-3 = (a), accept:** update the comment at `.../alloy-receiver/release.yaml:55-57` so it says what is actually true — that any host on the LAN can inject arbitrary logs, metrics and traces into the observability stack — rather than implying no auth is needed.

---

### Task 11 (decision gate DG-4): closing the MaxScale admin API on `10.200.90.4:8989`

> **Do not execute without answering DG-4.** Option (a) touches the 3306 data path used by every application.

**Files:** none until DG-4 is answered.

**Facts the decision rests on, all verified on mariadb-operator 26.6.0:**
- `spec.admin.tls` **does not exist**. `kubectl explain maxscale.spec.admin --recursive` returns `guiEnabled` and `port` only. The live `spec.admin` is `{"guiEnabled": true, "port": 8989}`.
- The real field is `spec.tls`, currently unset on the MaxScale CR. Its `enabled: true` covers admin, listener **and** server TLS in one switch; there is no admin-only sub-switch. MaxScale marks a listener with `ssl=true` as TLS-**required**, so flipping this makes every client on 3306 need TLS.
- `MariaDB.spec.tls` is already `{"enabled": true}` — the operator already has PKI machinery in place, and `step-ca-issuer` (`StepClusterIssuer`) plus the `step-ca` ClusterIssuer are available for `adminCertIssuerRef`.
- `spec.kubernetesService.loadBalancerSourceRanges` **is** supported by the CRD and would filter traffic arriving at `10.200.90.4` without restarting anything. It does not affect in-cluster clients, which all connect via `maxscale-galera.mariadb-galera.svc.cluster.local:3306`.
- **Unverified and blocking for option (b):** this plan could not enumerate which off-cluster hosts connect to `10.200.90.4:3306`. Doing so read-only would need either MaxScale's own session list (which requires the admin credential) or a period of connection logging. Get that list before writing any `loadBalancerSourceRanges` value — an incomplete list takes applications offline.

**Recommended sequence:** (c) now — hand the MaxScale admin credential to the `crown-jewel-rotation-leaked-pki-and-credentials` theme for rotation, since it was transmitted in cleartext over a segment that a privileged sniffer was capturing for 15 days. Then (b) once the client list exists. Then (a) only inside a planned window, after confirming every 3306 client speaks TLS, and expecting a connection blip on all MaxScale pods (`externalTrafficPolicy: Local`, 2 replicas).

---

## Appendix A (optional, decision gate DG-5): reinstalling kubeshark under Flux

Only if DG-5 = yes. This is written as a specification, not as steps to execute blindly — the chart's repository URL, current version and values schema must be confirmed first, exactly as Task 3 of `docs/superpowers/plans/2026-07-18-rook-operator-upgrade.md` did for `ceph-csi-drivers`.

**Research step (do this first, record the findings in `$SCRATCH/kubeshark-chart-research.md`):** the chart's Helm repository URL and its latest non-prerelease version; whether the chart exposes `tap.nodeSelector` (or equivalent) to scope the DaemonSet to specific nodes; whether the ClusterRole's `networking.k8s.io/networkpolicies` verbs are switchable off via values or must be patched out; and whether resource limits are settable per container.

**Required shape of the result, all of which the deleted install violated:**

- `infrastructure/clusters/feather-core/base-sources/kubeshark.yaml` — a `HelmRepository` in `flux-system`, registered in that directory's `kustomization.yaml` `resources:` list (currently 22 entries, `mariadb-operator.yml` … `plane.yml`).
- `apps/base/kubeshark/` (namespace + `HelmRelease`) and `apps/clusters/feathre-core/base-apps/kubeshark/` (overlay patching the release), following the two-tier pattern every other workload uses. Note the deliberate `feathre` misspelling under `apps/`.
- **Pin the chart version explicitly.** Do not use a floating range.
- **`nodeSelector` scoping the DaemonSet to the nodes under investigation.** The deleted install ran `DESIRED 10`, including all three control-plane nodes. A capture tool has no business on a control plane by default.
- **The `networkpolicies` `create`/`update`/`delete` verbs stripped from the ClusterRole.** The deleted install held them cluster-wide, which is a policy-tampering primitive. If the chart hard-codes them, patch the rendered ClusterRole via a Kustomize patch or a Flux `postRenderers` entry.
- **`priorityClassName: feather-low`** (already defined; see `apps/clusters/feathre-core/base-apps/node-red/release.yaml:13` for usage) — packet capture is best-effort and must lose to real workloads.
- **Memory limits proportionate to observed usage.** The deleted install had `requests: 50Mi` / `limits: 5Gi` per container, i.e. 10 Gi of limit against 100 Mi of request per node, while actually consuming 516–894 Mi per worker (~6.3 Gi cluster-wide). Set `requests` at roughly observed usage and `limits` around 2 Gi.
- **An expiry.** If it goes back in, it goes in with a written date by which it is removed again, recorded in the overlay as a comment. The whole reason this finding exists is that a debugging tool ran for 15 days with nobody's reconciler owning it.

`privileged: true`, `hostNetwork: true` and the hostPath mount of `/` are inherent to what packet capture on Kubernetes does and cannot be removed; that is precisely why the answer to DG-5 should be "no" unless there is an active, bounded need.

---

## What this plan does not claim to have verified

- **Who, if anyone, uses `10.200.90.7`.** Task 3 asks the operator to check bookmarks, uptime-kuma monitors and probes; the check could not be performed read-only from here.
- **Which off-cluster hosts connect to `10.200.90.4:3306`.** Blocking for DG-4 option (b). One external OTLP source (`10.200.2.35`) was observed on a different port; that says nothing about the MySQL clients.
- **Whether `10.200.2.35` is the only OTLP client.** It was the only one in a 7 h 11 min window (the depth of the Envoy pods' log buffers). A daily or weekly batch exporter would not have appeared.
- **Whether anything outside the cluster probes `loki.`/`mimir.`/`tempo.apps.onelite.feather` less often than hourly.** The same 7 h 11 min window is all the evidence there is behind "no production clients on those three hostnames". Tasks 6 and 8 both carry an explicit pre-merge check for uptime-kuma monitors and scheduled scrapers; it must be answered by a human, not inferred from the logs.
- **Whether `spec.tls.enabled: true` on MaxScale makes the 3306 listener TLS-mandatory in this exact operator version.** The reasoning is from MaxScale's listener `ssl=true` semantics, not from an observed test on this cluster. It must be validated in a window before anyone flips it.
- **What the kubeshark front/hub captured, and who accessed it.** The install had no persistence configured that this plan could inspect, and the pods are being deleted. The exposure window in Task 1 Step 2 is therefore a worst-case bound, not a measurement.
