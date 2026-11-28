## 1. Code

- [ ] 1.1 Remove the two grus entries from `products/layers/feather-core/dev/kustomization.yaml`
- [ ] 1.2 Remove `foundation/sources/clusters/feather-core/sources/grus.yaml` and its kustomization entry
- [ ] 1.3 Run `./.github/scripts/validate.sh`

## 2. Rollout and verification

- [ ] 2.1 Confirm `products-dev` and `foundation-sources` are Ready at the merge revision
- [ ] 2.2 Confirm namespace `grus-dev` and the grus CRDs are gone, and the bucket `grus-templates-dev` still exists

## 3. Pull request

- [ ] 3.1 Open the code pull request `chore(products): shelve the grus prototype`
