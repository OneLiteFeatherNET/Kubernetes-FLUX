## 1. Preflight

- [ ] 1.1 Record free CPU and memory requests per worker (`kubectl describe nodes`); confirm three admission replicas fit, else plan two
- [ ] 1.2 Choose the Kyverno chart version (`helm show chart kyverno/kyverno`); record it in the code PR
- [ ] 1.3 Confirm the Kyverno chart keys for `failurePolicy`, `timeoutSeconds` and PDB on the chosen version (`helm show values`)
- [ ] 1.4 Confirm that the existing VAP bindings do not overlap with a Kyverno webhook in a way that needs a `namespaceSelector`

## 2. Sources and base

- [ ] 2.1 Add the `HelmRepository` for Kyverno in `foundation/sources/clusters/feather-core/sources/` and list it in that `kustomization.yaml`
- [ ] 2.2 Add `foundation/security/base/kyverno/` with namespace `kyverno`, HelmRelease with pinned version and remediation settings
- [ ] 2.3 Set `admissionController.replicas: 3`, `failurePolicy: Ignore` and PDB `maxUnavailable: 1` in the values

## 3. Starter policies

- [ ] 3.1 Add the ClusterPolicy `baseline` (Audit) with exclusions for `kube-system`, `flux-system`, `kyverno`
- [ ] 3.2 Add the ClusterPolicy `restricted` (Audit) with the same exclusions
- [ ] 3.3 Add the ClusterPolicy `disallow-latest-tag` (Audit)
- [ ] 3.4 Add the ClusterPolicy `require-memory-resources` (Audit)
- [ ] 3.5 Add a CI check (extend `.github/scripts/validate.sh` or a new script) that fails on any `validationFailureAction: Enforce` under `foundation/security/base/kyverno/`

## 4. Overlay and reporting

- [ ] 4.1 Add `foundation/security/clusters/feather-core/kyverno/` with `kustomization.yaml` and the ServiceMonitor
- [ ] 4.2 List the overlay in `foundation/layers/feather-core/security/kustomization.yaml`
- [ ] 4.3 Validate: `./.github/scripts/validate.sh`; after merge, check that every Kyverno webhook has `failurePolicy: Ignore`

## 5. Rollout

- [ ] 5.1 Merge the code PR; confirm `foundation-security` is Ready
- [ ] 5.2 Confirm no request was denied by Kyverno (API server audit log or `kubectl get events` for `Denied`)
- [ ] 5.3 Read the first `PolicyReport`s and record the findings count per namespace in the rollout notes

## 6. Follow-ups outside this change

- [ ] 6.1 Grafana panel for policy findings per namespace (its own change)
- [ ] 6.2 Enforce promotion of any policy: a separate proposal, not part of this change

## 7. Code pull request

- [ ] 7.1 Open the code pull request under the title `feat(security): add kyverno in audit mode with starter policies`; no files under `openspec/` in it
