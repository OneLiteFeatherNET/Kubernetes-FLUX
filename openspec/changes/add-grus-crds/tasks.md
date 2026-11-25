## 1. Preflight

- [ ] 1.1 Confirm the grus repository contains `deploy/crds/` with exactly three CRDs (`environments`, `groups`, `pools`; `grus.onelitefeather.net`, `v1alpha1`, `Namespaced`) at the commit to pin
- [ ] 1.2 Render `deploy/crds` with `kubectl kustomize` (or list the files) at that commit and confirm no other kind appears, in particular no ClusterRole or webhook
- [ ] 1.3 Confirm `products-dev` applies as the kustomize-controller (no `serviceAccountName` in `clusters/feather-core/products-dev.yaml`) and that the controller may create CRDs (`kubectl auth can-i create customresourcedefinitions --as=system:serviceaccount:flux-system:kustomize-controller`)
- [ ] 1.4 Run `helm template` for the chart at the new commit with the current release values and the current prototype image tag; record whether it renders and whether the controller still starts with that tag

## 2. Kustomization

- [ ] 2.1 Create `products/grus/clusters/feather-core/grus-dev-crds/kustomization.yaml` and `crds.yaml` with the Flux Kustomization `grus-crds` (namespace `flux-system`, source GitRepository `grus`, path `./deploy/crds`, `prune: false`, `deletionPolicy: Orphan`, `wait: true`, `interval: 10m0s`)
- [ ] 2.2 List `../../../grus/clusters/feather-core/grus-dev-crds` in `products/layers/feather-core/dev/kustomization.yaml`
- [ ] 2.3 Set `ref.commit` in `foundation/sources/clusters/feather-core/sources/grus.yaml` to the grus commit from task 1.1 and update its comment

## 3. Validation

- [ ] 3.1 Run `kubectl kustomize products/layers/feather-core/dev` and confirm the Kustomization renders and no cluster-scoped kind is added by this repository
- [ ] 3.2 Run `./.github/scripts/validate.sh`
- [ ] 3.3 Run `openspec validate add-grus-crds --strict`

## 4. Rollout verification (read-only, after the code pull request is merged)

- [ ] 4.1 `flux reconcile source git grus -n flux-system`, then `flux get kustomizations -n flux-system grus-crds products-dev`
- [ ] 4.2 `kubectl get crd | grep grus.onelitefeather.net` shows three CRDs with `Established=True`
- [ ] 4.3 `kubectl -n grus-dev get pools,groups,environments` answers without an error
- [ ] 4.4 `flux get helmreleases -n grus-dev` is Ready at the new revision

## 5. Code pull request

- [ ] 5.1 Open the code pull request under the title `feat(products): add the grus custom resource definitions`; no files under `openspec/` in it
