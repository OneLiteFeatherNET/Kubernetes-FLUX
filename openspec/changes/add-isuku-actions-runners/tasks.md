## 1. Preflight

- [ ] 1.1 Run `kubectl describe nodes` on `xl-01` to `xl-04` and record the free CPU and memory requests per worker in the rollout notes
- [ ] 1.2 Confirm that at least one worker has 500m CPU and 2 GiB free; if not, pause the rollout
- [ ] 1.3 Confirm the chart pin: `gha-runner-scale-set-controller` and `gha-runner-scale-set` at `0.15.1` (checked with `helm show chart`)
- [ ] 1.4 Maintainer creates the GitHub App on `IsukuVerlag` with only "Self-hosted runners: read and write" plus repository metadata read, and installs it (outside chat and outside git)
- [ ] 1.5 Confirm the prometheus-stack ServiceMonitor and PodMonitor selectors pick up monitors in the runner and controller namespaces

## 2. Sources

- [ ] 2.1 Add `OCIRepository` sources for `gha-runner-scale-set-controller` and `gha-runner-scale-set` in `foundation/sources/clusters/feather-core/sources/` with `ref.tag: "0.15.1"`, and list them in that directory's `kustomization.yaml`

## 3. Base components

- [ ] 3.1 Add `services/development/base/actions-runners/` with the controller namespace and a HelmRelease for `gha-runner-scale-set-controller` referencing the OCIRepository by `chartRef`
- [ ] 3.2 Add the runner namespace, pod security labels following the existing namespace pattern, and a LimitRange with defaults
- [ ] 3.3 Add the ResourceQuota for the runner namespace: 4250m CPU and 12544Mi on limits, 1050m CPU and 4224Mi on requests (two runners plus the listener), and explicit listener resources (50m / 128Mi requests, 250m / 256Mi limits)
- [ ] 3.4 Add the NetworkPolicy: deny all ingress; allow egress to DNS and to GitHub, the npm registry, and the CMS and Cloudflare endpoints, with a one-line comment and an Outline link
- [ ] 3.5 Add the HelmRelease for `gha-runner-scale-set` named `isuku-runners`, with `githubConfigUrl: https://github.com/IsukuVerlag`, `minRunners: 0`, `maxRunners: 2`, runner requests 500m CPU and 2 GiB, limits 2 CPU and 6 GiB, no container mode (no Docker-in-Docker), non-root security context, ephemeral runners, and `dependsOn` the controller HelmRelease
- [ ] 3.6 Keep listener and controller polling at chart defaults; set no sync interval

## 4. Cluster overlay and secrets

- [ ] 4.1 Add `services/development/clusters/feather-core/actions-runners/` with a `kustomization.yaml` that references the base and patches the release if needed
- [ ] 4.2 Maintainer creates the GitHub App secret as `*.sops.yaml` (app ID, installation ID, private key, in the keys the chart expects) and encrypts it with `sops`; the plaintext never appears in chat or in git
- [ ] 4.3 Run `check-sops-encryption.py` on the new secret
- [ ] 4.4 Add PodMonitor or ServiceMonitor objects for the listener and controller metrics in the overlay

## 5. Layer wiring

- [ ] 5.1 Extend the existing `services/layers/feather-core/development/kustomization.yaml` with one resource line for `../../../development/clusters/feather-core/actions-runners`; do not rename or split the layer

## 6. Validation

- [ ] 6.1 Run `./.github/scripts/validate.sh` locally and confirm the new paths build
- [ ] 6.2 Confirm the commit messages pass commitlint (Conventional Commits, lowercase subject)
- [ ] 6.3 Confirm `flux-validate` passes on the code PR; it is a required check

## 7. Rollout and evidence

- [ ] 7.1 After merge, run `flux reconcile kustomization services-development --with-source` once, then let the dependency graph settle
- [ ] 7.2 Verify the controller and listener are healthy and that `isuku-runners` appears in the IsukuVerlag organization's runner groups
- [ ] 7.3 Verify the `gha_*` job metrics are visible in Prometheus and that OpenCost attributes cost to the runner namespace
- [ ] 7.4 Build the Grafana panel or saved query combining the `gha_*` job minutes, the OpenCost runner cost and the GitHub billing usage API for the GitHub-hosted remainder
- [ ] 7.5 Record the operating notes (secret rollout restart, key rotation, capacity check, public repositories excluded) in the Outline Infrastruktur collection

## 8. Dependencies outside this repository

- [ ] 8.1 Follow-up in the IsukuVerlag repositories: change workflows to `runs-on: ${{ vars.RUNNER || 'ubuntu-latest' }}` and set `RUNNER=isuku-runners` in a first repository; this is not a task of the code PR

## 9. Code pull request

- [ ] 9.1 Open the code pull request titled `feat(development): add actions runners for isuku verlag`, containing only the implementation (no files under `openspec/`), branched from the remote `main` that already contains the merged proposal
