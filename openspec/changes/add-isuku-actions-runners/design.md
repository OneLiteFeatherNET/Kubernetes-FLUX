## Context

`feather-core` is a single cluster reconciled by Flux from `main`. The publisher's CI runs on GitHub-hosted runners in the organization `IsukuVerlag`, which stopped starting jobs on 2026-11-11 at the billing limit. A self-hosted runner pool on the cluster restores CI and produces the minute counts the publisher needs.

The publisher owns its runner configuration in its own private repository, `IsukuVerlag/IsukuVerlagFlux`. The cluster is shared, so the cluster-wide and protective parts stay with OneLiteFeather.

The cluster has constraints that shape the design:

- Shared cluster: no privileged pods are allowed. Docker-in-Docker needs them.
- Memory is tight: measured memory requests are 84 % booked on `fr01-wrk-xl-01`, 84.5 % on `fr01-wrk-xl-02`, 95.6 % on `fr01-wrk-xl-03` and about 100 % on `fr01-wrk-xl-04`. Only xl-01 and xl-02 can take a runner.
- The API server already sees about 90 requests per second from workload service accounts.
- Third-party software reaches the cluster only through the official Helm chart, referenced by an `OCIRepository`, not as raw manifests.
- Layers are never renamed or split. A new component extends an existing layer entry.
- The Flux controllers run without `--default-service-account` (checked in `clusters/feather-core/flux-system/gotk-components.yaml`). A Kustomization or HelmRelease without `spec.serviceAccountName` therefore applies as the controller's own identity, which is `cluster-admin`.

## Goals / Non-Goals

**Goals**

- Jobs of the private repositories of `IsukuVerlag` run on the cluster, selectable per repository through a variable.
- Month-end report: job minutes on own resources, attributed to a namespace, next to the GitHub-hosted remainder.
- The runner pool cannot starve the nodes or the shared API server.
- A tenant repository cannot change cluster-wide state, cannot weaken the guards, and cannot make another layer unready.

**Non-Goals**

- Docker image builds on the cluster (see decision 3).
- Changes to workflow files in the IsukuVerlag repositories (follow-up, outside this repository).
- Public repositories or fork pull requests on the runner group.

## Decision drivers

- No privileged pods in the shared cluster.
- Low memory headroom on the worker nodes.
- API server load: keep the controller and listener on default polling intervals.
- Secret lifetime and ownership: no credential bound to a person; the tenant holds its own key, the cluster key never leaves `flux-system`.
- Publisher ownership: the publisher can audit and change its own runner configuration.
- Blast radius: a compromised or careless tenant change must stay inside `arc-runners`.
- Cost evidence for the month-end report.

## Decisions

### 1. Runner platform: ARC runner scale sets, installed through Flux

**Decision:** GitHub's actions-runner-controller in the scale-set mode. The controller chart `gha-runner-scale-set-controller` runs in `arc-systems` from `services-development`. The runner scale set `isuku-runners` for the organization `https://github.com/IsukuVerlag` is a `gha-runner-scale-set` HelmRelease in the publisher's repository. Both charts are pinned to `0.15.1` through `OCIRepository` sources.

**Consequences:** Runner pods are ephemeral and created per job. The official charts are used. The controller is owned by OneLiteFeather; the scale set by the publisher.

### 2. Authentication: GitHub App, not a personal access token

**Decision:** A GitHub App installed on the organization, with only the organization permission "Self-hosted runners: read and write" (plus the repository metadata read that GitHub requires). App ID, installation ID and private key sit in `github-app.sops.yaml` in the publisher's repository, encrypted to the maintainer's key and the tenant's key (not the cluster key). The tenant's Kustomization decrypts it with `sops-age-isuku`.

**Rejected alternative:** a fine-grained personal access token. It expires, which would stop the runners silently, and it is bound to a person, which breaks when that person leaves the organization.

**Consequences:** The app key must be rotated before it expires. The publisher performs the rotation in its repository; the operating notes below describe the restart.

### 3. Docker builds stay on GitHub-hosted runners

**Decision:** The CMS image builds with buildx keep `runs-on: ubuntu-latest`. The runner pool is plain: no `containerMode`, no Docker-in-Docker, no privileged pods.

**Drivers:** no privileged pods in the shared cluster; the builds need a Docker daemon.

**Rejected alternative:** a second scale set with Docker-in-Docker. It needs privileged pods and is therefore rejected for now. It can be reconsidered only as a separate proposal with its own isolation design.

**Consequences:** Image builds keep consuming GitHub-hosted minutes. The month-end report therefore shows the remainder explicitly.

### 4. Resource sizing

**Decision:**

| Setting | Value | Reason |
|---|---|---|
| `minRunners` | 0 | No idle pods on the cluster |
| `maxRunners` | 2 | Caps concurrent jobs |
| Runner requests | 500m CPU, 2 GiB | Fits the memory headroom on the booked nodes |
| Runner limits | 2 CPU, 6 GiB | Hard cap per job. 6 GiB, not 4 GiB: the website's lint and build run Node with a 4 GB heap (`--max-old-space-size=4096`), which needs room beyond the heap itself |
| Listener requests / limits | 50m CPU, 128Mi / 250m CPU, 256Mi | Explicit, so the LimitRange default does not take a share of the quota. Set in the publisher's HelmRelease; the quota assumes these values |
| Namespace ResourceQuota (requests) | 1050m CPU, 4224Mi | Two runners at requests plus the listener |
| Namespace ResourceQuota (limits) | 4250m CPU, 12544Mi | Two runners at their limits (2 CPU, 6Gi each) plus the listener (250m, 256Mi) |
| Namespace LimitRange | default requests and limits for any pod in the namespace | Pods without limits are not admitted |

Runners request little but may burst to the limit, so the quota is sized for limits: exactly two runners at their limits plus the listener. A third runner at its limits is rejected by the quota until one of the two finishes. Free requests on each worker must be checked before the rollout (see tasks). Without that check, runner pods can stay Pending on the booked nodes.

The ResourceQuota and LimitRange belong to OneLiteFeather (decision 8), so the publisher cannot raise them; a job above 2 CPU or 6 GiB fails on the cluster.

### 5. Polling and API load

**Decision:** The listener and the controller keep their default polling and sync intervals. No aggressive sync interval is set.

**Driver:** the API server already receives about 90 requests per second from workload service accounts.

### 6. Network policy

**Decision:** The runner namespace `arc-runners` denies all ingress except scraping of the listener's metrics port from Alloy (`alloy-metrics`) in the `grafana` namespace. Egress is allowed to DNS and to the destinations the jobs already reach today: GitHub, the npm registry, and the CMS and Cloudflare endpoints. The policies follow the explicit allow-list pattern of `foundation/databases/clusters/feather-core/dragonfly/networkpolicy.yaml`, documented in one line with a link to Outline.

**Driver:** the cluster enforces Cilium policies, so an unrestricted pod does not get a default-deny for free. The allow list must be explicit and checked against a real job before the scale set is marked as in use.

### 7. Month-end evidence

**Decision:**

- ARC listener metrics (`gha_*`: started and completed jobs, job duration) are scraped by the existing Alloy metrics collector (`alloy-metrics` in the `grafana` namespace) through PodMonitors in `arc-runners`. The listener PodMonitor selects pods in `arc-runners`; the controller PodMonitor selects pods in `arc-systems` through `namespaceSelector`. The scrape selector must match the prometheus-stack configuration (task).
- OpenCost (deployed in `services/observability/clusters/feather-core/opencost`) attributes cost to the runner namespace.
- GitHub's billing usage API gives the GitHub-hosted remainder for the same month.
- A Grafana panel or saved query combines both sides. It is a task, built in Grafana, not in this repository.

### 8. Ownership split: guardrails here, scale set in the publisher's repository

This decision supersedes the earlier layer placement in this change (all runner manifests in `services-development`).

**Decision:** OneLiteFeather owns everything cluster-wide or protective, in the existing `services-development` layer and the link file:

- the controller HelmRelease and namespace `arc-systems` (controller `OCIRepository` in `foundation/sources`);
- in `arc-runners`: the ResourceQuota, LimitRange, NetworkPolicies, PodMonitors, the tenant ServiceAccount `isuku-flux`, a namespaced Role and RoleBinding for it, the SOPS secrets `isuku-flux-deploy-key` (read-only deploy key) and `sops-age-isuku` (the tenant's own age private key), and a ValidatingAdmissionPolicy with binding;
- the policy rejects any HelmRelease or Kustomization in `arc-runners` whose `spec.serviceAccountName` is not `isuku-flux`, and any that sets `spec.kubeConfig` (no remote cluster target).

The publisher's repository `IsukuVerlag/IsukuVerlagFlux` holds only: the `gha-runner-scale-set` `OCIRepository`, the `isuku-runners` HelmRelease with `serviceAccountName: isuku-flux`, and `github-app.sops.yaml`.

**Drivers:** the publisher owns and audits its CI infrastructure; the cluster must not hand a tenant cluster-admin; the guards must not be editable by the tenant.

**Options considered:**

- A. All runner manifests in `services-development` (the earlier decision). Rejected: the publisher cannot audit or change its own runner configuration, and every tuning becomes a OneLiteFeather change.
- B. All runner manifests in the publisher's repository, applied as the default identity. Rejected: without `serviceAccountName`, the tenant's charts and CRs run as `cluster-admin`, and nothing guards the quota, network policies or admission.
- C. Split as above (chosen): the publisher keeps the chart and its values; OneLiteFeather keeps identity, rights, quota, network and admission.

**Rule for the Role (exact rules to be derived from `helm template` of `gha-runner-scale-set` 0.15.1):** rights only for the objects the chart and the tenant's listed manifests create in `arc-runners`. No rights on `NetworkPolicy`, `ResourceQuota`, `LimitRange`, `ValidatingAdmissionPolicy` or any cluster-scoped object. No `escalate` or `bind` verbs. No rights on `kustomizations` or `gitrepositories`, so the tenant cannot edit its own link (path, sourceRef, decryption). `HelmRelease`, `OCIRepository` and `Secret` objects in `arc-runners` are allowed.

**Consequences:** The tenant's identity has exactly the Role's rights in `arc-runners`. The ValidatingAdmissionPolicy ships in `services-development` and must be rolled out before any tenant object reaches the namespace; `tenant-isuku` depends on `services-development`, which gives that order. The listener and runner values in the publisher's HelmRelease must match the quota (see decision 4); a mismatch is rejected by the quota, not silently accepted.

**Residual risk:** write access to `IsukuVerlagFlux` equals the rights of the Role in `arc-runners`: runner and listener pods, Helm release secrets, and secrets in that namespace (including `sops-age-isuku` and the read-only deploy key). It does not reach any other namespace or cluster-scoped object. Mitigations: private repository; only the maintainer has write; branch protection on `main`; the deploy key is read-only; the admission policy and the Role are owned here. Runner pods execute workflow code in `arc-runners`, so the runner ServiceAccount must not be able to read the listener's GitHub App secret; this is verified with `kubectl auth can-i --as` on the runner ServiceAccount.

### 9. Link placement and tenant identity

**Decision:** `clusters/feather-core/tenant-isuku.yaml` (root, no decryption needed) contains:

- `Namespace` `arc-runners` with the pod security labels and the annotation `kustomize.toolkit.fluxcd.io/prune: disabled`. It stays in the root file because the `GitRepository` apply needs the namespace. The annotation means deleting or renaming the link file never deletes the namespace or the services-development objects in it;
- `GitRepository` `isuku-flux` in `arc-runners`: `ssh://git@github.com/IsukuVerlag/IsukuVerlagFlux.git`, branch `main`, interval 10m, `secretRef: isuku-flux-deploy-key`;
- `Kustomization` `tenant-isuku` in `arc-runners`: path `./clusters/feather-core`, `sourceRef` the `GitRepository` above, `targetNamespace: arc-runners`, `serviceAccountName: isuku-flux`, decryption `sops` with `secretRef: sops-age-isuku`, `dependsOn` `services-development` in namespace `flux-system`, interval 10m0s, retryInterval 30s, timeout 10m, `prune: true`, `wait: true`, `deletionPolicy: Orphan`.

**Why not in `flux-system`:** the impersonated ServiceAccount and the decryption secret must live in the Kustomization's namespace, and the cluster age key must never sit in a namespace the tenant can read. The cluster key stays in `flux-system` and is used only by OneLiteFeather's own layers.

**Why not inside `services-development`:** a failing tenant must not make `services-development` (and `products` behind it) unready.

**Deletion:** `deletionPolicy: Orphan` keeps the runner objects when the link is removed. The namespace keeps them too, by the prune annotation. The `GitRepository` is removed with the link file; the tenant objects stay until the namespace is removed by hand.

**Secret placement:** `isuku-flux-deploy-key` and `sops-age-isuku` are `*.sops.yaml` Secrets in the `services/development/clusters/feather-core/actions-runners/` overlay, decrypted by `services-development` (`sops-age` in `flux-system`) and created in `arc-runners`. The `GitRepository` in the link file reads the deploy key from `arc-runners`. The tenant's own key is encrypted to the cluster key and the maintainer, because OneLiteFeather writes it; the tenant's `github-app.sops.yaml` is encrypted to the tenant's key, not the cluster key.

**Consequences:** `tenant-isuku` is not Ready until `services-development` is Ready and the publisher's repository has content at `./clusters/feather-core`; until then it reports not ready and blocks nothing.

## Operating notes

- **Secret changes:** the overlay's generators use `disableNameSuffixHash: true`, so a changed secret does not roll the consuming pods. After a change to the GitHub App secret in the publisher's repository, the publisher runs `kubectl rollout restart` on the listener in `arc-runners` and checks that it re-registers with GitHub.
- **Key rotation:** the publisher creates a new private key for the GitHub App, encrypts it with `sops` to the tenant and maintainer keys, commits it, and restarts the listener. If the tenant key changes, OneLiteFeather re-encrypts `sops-age-isuku` in the same commit as the recipient change; see the repository's SOPS guidance.
- **Capacity check:** before enabling the scale set, run `kubectl describe nodes` and record the free requests per worker. If no node has 500m CPU and 2 GiB free, the runner pods stay Pending and the rollout is paused.
- **Public repositories:** never add a public repository to the runner group. Fork pull requests from strangers would then execute on the cluster.
- **Commits and checks:** commit messages are linted by commitlint (Conventional Commits, lowercase subject). The `flux-validate` check is a required status check on the code PR.

## Risks / Trade-offs

- **Pending pods on booked nodes:** mitigated by low requests and the pre-rollout capacity check. Residual risk: a node fills up later.
- **Runner outage stops CI again:** the GitHub-hosted fallback stays one variable away (`RUNNER` unset) while the billing limit is still in place.
- **Quota too small for a heavy job:** a job above 2 CPU or 6 GiB fails on the cluster. Such jobs stay on GitHub-hosted runners by setting the variable per repository.
- **Key expiry:** a GitHub App private key that is not rotated stops registration. Covered by the operating notes.
- **Tenant write access:** bounded by the Role and the admission policy (decision 8, residual risk).

## Migration Plan

1. Check the free requests per worker (`kubectl describe nodes`) and record the result.
2. Maintainer creates the GitHub App and the read-only deploy key; the publisher generates its age key and gives the public part to OneLiteFeather. This update PR is merged first.
3. Merge the code PR (draft #609, reworked). Flux reconciles `services-development` (admission policy, Role, quota, controller) and then `tenant-isuku`.
4. Verify the rights with `kubectl auth can-i --list --as=system:serviceaccount:arc-runners:isuku-flux -n arc-runners` and `-n flux-system` (expect nothing in `flux-system`). Verify the controller and listener are healthy and that `isuku-runners` appears in the IsukuVerlag organization's runner groups.
5. Only then set `RUNNER=isuku-runners` in a first repository (follow-up, outside this repository). The rollback is to unset the variable.

## Open Questions

- Exact Role rules: to be derived from `helm template` of the chart, not in this proposal.
- Whether the Cilium egress allow list needs IP-based rules for GitHub or DNS names is to be settled when the policy is written.
