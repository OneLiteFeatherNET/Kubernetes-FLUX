## 1. Code

- [ ] 1.1 Set `install.crds: CreateReplace` and `upgrade.crds: CreateReplace` in `foundation/messaging/base/strimzi-operator/release.yaml`
- [ ] 1.2 Run `./.github/scripts/validate.sh`

## 2. Rollout and verification

- [ ] 2.1 After Flux has upgraded the release, verify `kubectl diff` against the chart 1.3.1 CRDs is empty
- [ ] 2.2 Verify the operator log shows no reconciliation every few seconds and its CPU drops to a few millicores

## 3. Pull request

- [ ] 3.1 Open the code pull request `fix(messaging): upgrade strimzi crds with the chart`
