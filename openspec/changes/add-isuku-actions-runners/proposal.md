## Why

Since 2026-11-11 GitHub Actions in the GitHub organization `IsukuVerlag` no longer starts jobs, because the organization has reached its billing spending limit. The affected private repositories are `IsukuVerlagWebsite` and `IsukuVerlagCMS`. Their CI and image builds therefore stop until someone raises the limit.

The publisher wants CI to run on OneLiteFeather's own cluster `feather-core` first. At month end it also needs to show how many job minutes ran on own resources, so that the remaining GitHub-hosted minutes can be judged before anything is paid to GitHub.

## What Changes

- Install GitHub's actions-runner-controller (ARC) through Flux: the controller chart `gha-runner-scale-set-controller` and one runner scale set named `isuku-runners`, scoped to the organization `https://github.com/IsukuVerlag`. Both charts are pulled as OCI artifacts from `ghcr.io/actions/actions-runner-controller-charts/`, pinned to chart version `0.15.1`.
- The scale set is placed in the existing `services-development` layer, with the base under `services/development/base/` and the cluster overlay under `services/development/clusters/feather-core/`. The chart sources are added to the existing source set under `foundation/sources/clusters/feather-core/sources/`.
- Hard limits: 0 to 2 runners, 500m CPU and 2 GiB memory requested per runner pod, 2 CPU and 4 GiB as limits, a namespace ResourceQuota and LimitRange sized for the limits, no privileged pods, no Docker-in-Docker, non-root runner pods, and ephemeral runners.
- Authentication through a GitHub App installed on the organization with only the permission "Self-hosted runners: read and write" (plus the repository metadata read that GitHub requires). Its app ID, installation ID and private key live in a SOPS-encrypted secret, created by the maintainer.
- Docker image builds (CMS, buildx) stay on GitHub-hosted `ubuntu-latest`.
- Month-end evidence: runner metrics and OpenCost attribution from the cluster, plus GitHub's billing usage for the remaining GitHub-hosted minutes.

**Commit type and scope:** the code PR ships as `feat(development): add actions runners for isuku verlag`. The scope `development` matches the folder `services/development/` and the layer `services-development`. The proposal PR is `docs(openspec): propose add-isuku-actions-runners`.

## Out of scope and follow-ups

- Workflow changes in the IsukuVerlag repositories are not part of this repository and are a dependency, not a task here. Each workflow uses `runs-on: ${{ vars.RUNNER || 'ubuntu-latest' }}`. Setting the organization or repository variable `RUNNER=isuku-runners` routes jobs to the cluster. Unsetting it falls back to GitHub-hosted runners.
- The GitHub App is created and installed by the maintainer. Its private key never appears in chat or in git in plain text.
- A public repository must never be added to this runner group. The runners execute workflow code, so the group is for the private repositories of `IsukuVerlag` only.

## Impact

- New capability `actions-runners` (see `specs/actions-runners/spec.md`).
- Files touched in this repository: `foundation/sources/clusters/feather-core/sources/` (new OCIRepository entries plus the kustomization entry), `services/layers/feather-core/development/kustomization.yaml` (one added resource line), and new base and overlay directories under `services/development/`.
- Cluster load: the runners add a Kubernetes API client. The listener and controller keep the default polling and sync intervals, because the API server already sees about 90 requests per second from workload service accounts.
- Node capacity: memory requests are 84 % booked on `fr01-wrk-xl-01`, 84.5 % on `fr01-wrk-xl-02`, 95.6 % on `fr01-wrk-xl-03` and about 100 % on `fr01-wrk-xl-04`. Only xl-01 and xl-02 can take a runner. Runners are therefore requested with low requests, and free requests per node are checked before the rollout.
