## 1. Preflight

- [ ] 1.1 Run `kubectl describe nodes` on `xl-01` to `xl-04` and record the free CPU and memory requests per worker in the rollout notes
- [ ] 1.2 Confirm that at least one worker has 500m CPU and 2 GiB free; if not, pause the rollout
- [ ] 1.3 Confirm the chart pin: `gha-runner-scale-set-controller` and `gha-runner-scale-set` at `0.15.1` (checked with `helm show chart`)
- [ ] 1.4 Derive the Role rules from `helm template` of `gha-runner-scale-set` 0.15.1 and the tenant's listed manifests; record them in the code PR; no rights on NetworkPolicy, ResourceQuota, LimitRange, admission policies, Kustomizations, GitRepositories or cluster-scoped objects
- [ ] 1.5 Maintainer creates the GitHub App on `IsukuVerlag` with only "Self-hosted runners: read and write" plus repository metadata read, and installs it (outside chat and outside git)
- [ ] 1.6 Maintainer creates a read-only deploy key for `IsukuVerlag/IsukuVerlagFlux` (outside chat and outside git)
- [ ] 1.7 Publisher generates its age key and sends only the public key to OneLiteFeather; the private key never leaves the publisher
- [ ] 1.8 Confirm the Alloy metrics collector (`grafana` namespace) PodMonitor discovery pick up monitors in `arc-runners` and `arc-systems`
- [ ] 1.9 Confirm that the Flux controllers run without `--default-service-account` (checked in `gotk-components.yaml`) and that no Flux object in this repository sets `serviceAccountName` for a tenant

## 2. Sources

- [ ] 2.1 Add the `OCIRepository` `gha-runner-scale-set-controller` (`ref.tag: "0.15.1"`) in `foundation/sources/clusters/feather-core/sources/` and list it in that directory's `kustomization.yaml`

## 3. Services-development base

- [ ] 3.1 Add `services/development/base/actions-runners/` with namespace `arc-systems` and a HelmRelease for `gha-runner-scale-set-controller` that references the OCIRepository by `chartRef` (namespace `flux-system`)
- [ ] 3.2 Add the ServiceAccount `isuku-flux`, a namespaced Role limited by task 1.4, and its RoleBinding in `arc-runners`
- [ ] 3.3 Add the ResourceQuota for `arc-runners`: 4250m CPU and 12544Mi on limits, 1050m CPU and 4224Mi on requests (two runners plus the listener)
- [ ] 3.4 Add a LimitRange with defaults for `arc-runners`
- [ ] 3.5 Add the NetworkPolicies: deny all ingress except listener metrics from Alloy in `grafana`; allow egress to DNS and to GitHub, the npm registry, and the CMS and Cloudflare endpoints, with a one-line comment and an Outline link
- [ ] 3.6 Add PodMonitors for the listener (in `arc-runners`) and the controller (in `arc-systems`, selected through `namespaceSelector` from a PodMonitor in `arc-runners`), keeping the default intervals
- [ ] 3.7 Add the ValidatingAdmissionPolicy and binding: reject HelmRelease and Kustomization in `arc-runners` unless `spec.serviceAccountName` is `isuku-flux`, and reject any that sets `spec.kubeConfig`

## 4. Cluster overlay and secrets

- [ ] 4.1 Add `services/development/clusters/feather-core/actions-runners/` with a `kustomization.yaml` that references the base
- [ ] 4.2 Add `sops-age-isuku`, the tenant's age private key, as a `*.sops.yaml` Secret in namespace `arc-runners`, encrypted to the cluster, maintainer and CI keys (not to the tenant key); the plaintext never appears in chat or in git
- [ ] 4.3 Add `isuku-flux-deploy-key`, the read-only deploy key, as a `*.sops.yaml` Secret in namespace `arc-runners`, encrypted the same way
- [ ] 4.4 Run `check-sops-encryption.py` on the new secrets

## 5. Layer wiring and link

- [ ] 5.1 Extend the existing `services/layers/feather-core/development/kustomization.yaml` with one resource line for `../../../development/clusters/feather-core/actions-runners`; do not rename or split the layer
- [ ] 5.2 Add `clusters/feather-core/tenant-isuku.yaml` with: Namespace `arc-runners` (pod security labels and the annotation `kustomize.toolkit.fluxcd.io/prune: disabled`); `GitRepository` `isuku-flux` in `arc-runners` (`ssh://git@github.com/IsukuVerlag/IsukuVerlagFlux.git`, branch `main`, interval 10m, `secretRef: isuku-flux-deploy-key`); `Kustomization` `tenant-isuku` in `arc-runners` (path `./clusters/feather-core`, `serviceAccountName: isuku-flux`, `targetNamespace: arc-runners`, decryption `sops` with `secretRef: sops-age-isuku`, `dependsOn` `services-development` in `flux-system`, interval 10m0s, retryInterval 30s, timeout 10m, `prune: true`, `wait: true`, `deletionPolicy: Orphan`)
- [ ] 5.3 Confirm `clusters/feather-core/` contains no SOPS-encrypted file (it is applied without decryption)

## 6. Validation

- [ ] 6.1 Run `./.github/scripts/validate.sh` locally and confirm the new paths build
- [ ] 6.2 Confirm the commit messages pass commitlint (Conventional Commits, lowercase subject)
- [ ] 6.3 Confirm `flux-validate` passes on the code PR; it is a required check

## 7. Rollout and evidence

- [ ] 7.1 After merge, run `flux reconcile kustomization services-development --with-source` once, then let the dependency graph settle
- [ ] 7.2 Verify `kubectl auth can-i --list --as=system:serviceaccount:arc-runners:isuku-flux -n arc-runners` and `-n flux-system` (expect nothing in `flux-system`) and the negative cases of the spec
- [ ] 7.3 Verify the controller and listener are healthy and that `isuku-runners` appears in the IsukuVerlag organization's runner groups
- [ ] 7.4 Verify the `gha_*` job metrics are visible in Prometheus and that OpenCost attributes cost to the runner namespace
- [ ] 7.5 Build the Grafana panel or saved query combining the `gha_*` job minutes, the OpenCost runner cost and the GitHub billing usage API for the GitHub-hosted remainder
- [ ] 7.6 Record the operating notes (secret rollout restart, key rotation, capacity check, public repositories excluded) in the Outline Infrastruktur collection

## 8. Dependencies outside this repository

- [ ] 8.1 Publisher repository `IsukuVerlag/IsukuVerlagFlux`: add `clusters/feather-core/` with the `gha-runner-scale-set` `OCIRepository` (`0.15.1`, in `arc-runners`), the `isuku-runners` HelmRelease (`serviceAccountName: isuku-flux`, `githubConfigUrl: https://github.com/IsukuVerlag`, `minRunners: 0`, `maxRunners: 2`, runner requests 500m CPU and 2 GiB, limits 2 CPU and 6 GiB, listener requests 50m / 128Mi and limits 250m / 256Mi, no container mode, non-root, ephemeral, `dependsOn` nothing in this repository) and `github-app.sops.yaml` encrypted to the maintainer and tenant keys
- [ ] 8.2 Follow-up in the IsukuVerlag repositories: change workflows to `runs-on: ${{ vars.RUNNER || 'ubuntu-latest' }}` and set `RUNNER=isuku-runners` in a first repository; this is not a task of the code PR

## 9. Code pull request

- [ ] 9.1 Rework the existing draft PR #609 to this design (not closed): only the implementation, no files under `openspec/`, branched from the remote `main` that already contains the merged proposal; retitle it `feat(tenants): link the isuku verlag flux repository`, keep the commitlint check green, and mark it ready for review
