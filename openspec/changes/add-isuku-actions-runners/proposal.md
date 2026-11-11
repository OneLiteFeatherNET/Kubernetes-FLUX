## Why

Since 2026-11-11 GitHub Actions in the GitHub organization `IsukuVerlag` no longer starts jobs, because the organization has reached its billing spending limit. The affected private repositories are `IsukuVerlagWebsite` and `IsukuVerlagCMS`. Their CI and image builds therefore stop until someone raises the limit.

The publisher wants CI to run on OneLiteFeather's own cluster `feather-core` first. At month end it also needs to show how many job minutes ran on own resources, so that the remaining GitHub-hosted minutes can be judged before anything is paid to GitHub.

The publisher owns its runner configuration, but the cluster is shared. The split is therefore: OneLiteFeather keeps everything cluster-wide or protective, and the publisher's private repository `IsukuVerlag/IsukuVerlagFlux` holds only the runner scale set. This repository links to that repository and guards it (see design, decisions 8 and 9).

## What Changes

- Install GitHub's actions-runner-controller (ARC) through Flux in `services-development`: the controller chart `gha-runner-scale-set-controller` in namespace `arc-systems`, pinned to chart version `0.15.1` through an `OCIRepository` in the existing source set.
- Namespace `arc-runners` (created by the link in `clusters/feather-core/tenant-isuku.yaml`) carries, from this repository: a ResourceQuota and LimitRange sized for the runner limits, NetworkPolicies, PodMonitors for the listener and controller, the tenant ServiceAccount `isuku-flux` with a namespaced Role and RoleBinding, and a ValidatingAdmissionPolicy that refuses any HelmRelease or Kustomization in `arc-runners` that does not run as `isuku-flux`.
- The runner scale set `isuku-runners`, scoped to the organization `https://github.com/IsukuVerlag`, is a HelmRelease in the publisher's repository `IsukuVerlag/IsukuVerlagFlux`, path `./clusters/feather-core`. It is applied with the tenant ServiceAccount `isuku-flux`, so it runs with only the rights of the Role.
- This repository links to the publisher's repository: a `GitRepository` `isuku-flux` (ssh, branch `main`, read-only deploy key) and a Flux `Kustomization` `tenant-isuku` in namespace `arc-runners`, defined in `clusters/feather-core/tenant-isuku.yaml`.
- Hard limits: 0 to 2 runners, 500m CPU and 2 GiB memory requested per runner pod, 2 CPU and 6 GiB as limits (6 GiB because the website's lint and build run Node with a 4 GB heap), no privileged pods, no Docker-in-Docker, non-root runner pods, and ephemeral runners.
- Authentication through a GitHub App installed on the organization with only the permission "Self-hosted runners: read and write" (plus the repository metadata read that GitHub requires). Its app ID, installation ID and private key live in a SOPS-encrypted secret in the publisher's repository, encrypted to the maintainer's key and the tenant's key, not the cluster key.
- Docker image builds (CMS, buildx) stay on GitHub-hosted `ubuntu-latest`.
- Month-end evidence: runner metrics and OpenCost attribution from the cluster, plus GitHub's billing usage for the remaining GitHub-hosted minutes.

**Commit type and scope:** the code PR ships as `feat(tenants): link the isuku verlag flux repository`. The scope `tenants` names the link and the tenant guards; commitlint in this repository sets no scope list, so the scope is free. The existing draft PR #609 (`feat(development): add actions runners for isuku verlag`) is reworked to this design and retitled to this commit, not closed. The proposal PR `docs(openspec): propose add-isuku-actions-runners` is already merged; this update ships as `docs(openspec): move isuku runners into the publisher's flux repository`.

## Out of scope and follow-ups

- The scale set manifests and the GitHub App secret ship in `IsukuVerlag/IsukuVerlagFlux`, not in this repository. Changes to them are made and reviewed there.
- Workflow changes in the IsukuVerlag repositories are not part of this repository and are a dependency, not a task here. Each workflow uses `runs-on: ${{ vars.RUNNER || 'ubuntu-latest' }}`. Setting the organization or repository variable `RUNNER=isuku-runners` routes jobs to the cluster. Unsetting it falls back to GitHub-hosted runners.
- The GitHub App and the read-only deploy key are created by the maintainer. Their private keys never appear in chat or in git in plain text.
- A public repository must never be added to this runner group. The runners execute workflow code, so the group is for the private repositories of `IsukuVerlag` only.

## Impact

- New capability `actions-runners` (see `specs/actions-runners/spec.md`).
- Files touched in this repository: `foundation/sources/clusters/feather-core/sources/` (controller OCIRepository, `GitRepository` `isuku-flux`, and the encrypted deploy-key secret, with entries in the directory's `kustomization.yaml`); `services/development/base/actions-runners/` and `services/development/clusters/feather-core/actions-runners/` (controller, guards, quota, network policies, monitors, encrypted tenant key); one resource line in `services/layers/feather-core/development/kustomization.yaml`; and the new file `clusters/feather-core/tenant-isuku.yaml`. No layer is added, renamed or split.
- Files touched in `IsukuVerlag/IsukuVerlagFlux`: the scale set `OCIRepository` and `HelmRelease`, and `github-app.sops.yaml`. Those changes have their own review in that repository.
- Trust: write access to `IsukuVerlagFlux` cannot change cluster-wide state. Its reach is the rights of the Role in `arc-runners` (see design, decision 8, and its residual risk).
- Cluster load: the runners add a Kubernetes API client. The listener and controller keep the default polling and sync intervals, because the API server already sees about 90 requests per second from workload service accounts.
- Node capacity: memory requests are 84 % booked on `fr01-wrk-xl-01`, 84.5 % on `fr01-wrk-xl-02`, 95.6 % on `fr01-wrk-xl-03` and about 100 % on `fr01-wrk-xl-04`. Only xl-01 and xl-02 can take a runner. Runners are therefore requested with low requests, and free requests per node are checked before the rollout.
