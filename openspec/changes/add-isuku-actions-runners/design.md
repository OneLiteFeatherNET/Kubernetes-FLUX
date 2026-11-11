## Context

`feather-core` is a single cluster reconciled by Flux from `main`. The publisher's CI runs on GitHub-hosted runners in the organization `IsukuVerlag`, which stopped starting jobs on 2026-11-11 at the billing limit. A self-hosted runner pool on the cluster restores CI and produces the minute counts the publisher needs.

The cluster has constraints that shape the design:

- Shared cluster: no privileged pods are allowed. Docker-in-Docker needs them.
- Memory is tight: requests on `xl-03` and `xl-04` are about 99 % booked, on `xl-01` and `xl-02` 82 to 85 %.
- The API server already sees about 90 requests per second from workload service accounts.
- Third-party software reaches the cluster only through the official Helm chart, referenced by an `OCIRepository`, not as raw manifests.
- Layers are never renamed or split. A new component extends an existing layer entry.

## Goals / Non-Goals

**Goals**

- Jobs of the private repositories of `IsukuVerlag` run on the cluster, selectable per repository through a variable.
- Month-end report: job minutes on own resources, attributed to a namespace, next to the GitHub-hosted remainder.
- The runner pool cannot starve the nodes or the shared API server.

**Non-Goals**

- Docker image builds on the cluster (see decision 3).
- Changes to workflow files in the IsukuVerlag repositories (follow-up, outside this repository).
- Public repositories or fork pull requests on the runner group.

## Decision drivers

- No privileged pods in the shared cluster.
- Low memory headroom on the worker nodes.
- API server load: keep the controller and listener on default polling intervals.
- Secret lifetime and ownership: no credential bound to a person.
- Cost evidence for the month-end report.

## Decisions

### 1. Runner platform: ARC runner scale sets, installed through Flux

**Decision:** GitHub's actions-runner-controller in the scale-set mode. The controller chart `gha-runner-scale-set-controller` and one `gha-runner-scale-set` named `isuku-runners` for the organization `https://github.com/IsukuVerlag`, both pinned to chart `0.15.1` through `OCIRepository` sources.

**Consequences:** Runner pods are ephemeral and created per job. The Flux layer owns the lifecycle, and the Helm charts are the official ones.

### 2. Authentication: GitHub App, not a personal access token

**Decision:** A GitHub App installed on the organization, with only the organization permission "Self-hosted runners: read and write" (plus the repository metadata read that GitHub requires). App ID, installation ID and private key sit in a SOPS-encrypted `*.sops.yaml` secret in the cluster overlay, created by the maintainer.

**Rejected alternative:** a fine-grained personal access token. It expires, which would stop the runners silently, and it is bound to a person, which breaks when that person leaves the organization.

**Consequences:** The app key must be rotated before it expires. Rotation follows the operating notes below.

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
| Runner limits | 2 CPU, 4 GiB | Hard cap per job |
| Namespace ResourceQuota (limits) | 4 CPU, 8 GiB | Equals two runners at their limits |
| Namespace LimitRange | default requests and limits for any pod in the namespace | Pods without limits are not admitted |

Runners request little but may burst to the limit, so the quota is sized for limits. Free requests on each worker must be checked before the rollout (see tasks). Without that check, runner pods can stay Pending on the booked nodes.

### 5. Polling and API load

**Decision:** The listener and the controller keep their default polling and sync intervals. No aggressive sync interval is set.

**Driver:** the API server already receives about 90 requests per second from workload service accounts.

### 6. Network policy

**Decision:** The runner namespace denies all ingress. Egress is allowed to DNS and to the destinations the jobs already reach today: GitHub, the npm registry, and the CMS and Cloudflare endpoints. The NetworkPolicy follows the pattern of `foundation/databases/clusters/feather-core/dragonfly/networkpolicy.yaml`: an explicit allow list, documented in one line with a link to Outline.

**Driver:** the cluster enforces Cilium policies, so an unrestricted pod does not get a default-deny for free. The allow list must be explicit and checked against a real job before the scale set is marked as in use.

### 7. Month-end evidence

**Decision:**

- ARC listener metrics (`gha_*`: started and completed jobs, job duration) are scraped by the existing Prometheus stack through a PodMonitor or ServiceMonitor in the overlay. The scrape selector must match the prometheus-stack configuration (task).
- OpenCost (deployed in `services/observability/clusters/feather-core/opencost`) attributes cost to the runner namespace.
- GitHub's billing usage API gives the GitHub-hosted remainder for the same month.
- A Grafana panel or saved query combines both sides. It is a task, built in Grafana, not in this repository.

### 8. Layer placement

**Decision:** The sources go into the existing `foundation-sources` layer, the components into the existing `services-development` layer. The layer entry `services/layers/feather-core/development/kustomization.yaml` gets one added resource line. No layer is renamed, split or added.

## Operating notes

- **Secret changes:** the overlay's generators use `disableNameSuffixHash: true`, so a changed secret does not roll the consuming pods. After any change to the GitHub App secret, run `kubectl rollout restart` on the listener and check that it re-registers with GitHub.
- **Key rotation:** create a new private key for the GitHub App, encrypt it with `sops`, commit it together with the old key's removal, and restart the listener. `.sops.yaml` and the ciphertext change in the same commit if recipients change; see the repository's SOPS guidance.
- **Capacity check:** before enabling the scale set, run `kubectl describe nodes` and record the free requests per worker. If no node has 500m CPU and 2 GiB free, the runner pods stay Pending and the rollout is paused.
- **Public repositories:** never add a public repository to the runner group. Fork pull requests from strangers would then execute on the cluster.
- **Commits and checks:** commit messages are linted by commitlint (Conventional Commits, lowercase subject). The `flux-validate` check is a required status check on the code PR.

## Risks / Trade-offs

- **Pending pods on booked nodes:** mitigated by low requests and the pre-rollout capacity check. Residual risk: a node fills up later.
- **Runner outage stops CI again:** the GitHub-hosted fallback stays one variable away (`RUNNER` unset) while the billing limit is still in place.
- **Quota too small for a heavy job:** a job above 2 CPU or 4 GiB fails on the cluster. Such jobs stay on GitHub-hosted runners by setting the variable per repository.
- **Key expiry:** a GitHub App private key that is not rotated stops registration. Covered by the operating notes.

## Migration Plan

1. Check the free requests per worker (`kubectl describe nodes`) and record the result.
2. Maintainer creates the GitHub App and the SOPS secret; the proposal PR is merged first.
3. Merge the code PR. Flux reconciles `foundation-sources` and `services-development`.
4. Verify the controller and the listener are healthy and that `isuku-runners` appears in the IsukuVerlag organization's runner groups.
5. Only then set `RUNNER=isuku-runners` in a first repository (follow-up, outside this repository). The rollback is to unset the variable.

## Open Questions

- Exact memory and CPU requests per node: to be answered by the preflight check, not in this proposal.
- Whether the Cilium egress allow list needs IP-based rules for GitHub or DNS names is to be settled when the policy is written.
