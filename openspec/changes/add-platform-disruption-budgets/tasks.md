## 1. Preflight

- [ ] 1.1 Read the CoreDNS replica count in `kube-system`; the PDB is merged only if it is at least two (design, decision 3)
- [ ] 1.2 Check `helm show values` of the Cilium chart 1.20.4 for an operator PDB value; record the decision (chart value or standalone PDB)
- [ ] 1.3 Read the labels of the cilium-operator pods for the standalone PDB selector
- [ ] 1.4 Confirm that the cilium-operator Deployment has two ready replicas
- [ ] 1.5 Confirm Talos does not prune a foreign PodDisruptionBudget in `kube-system` (open question in design); record the result

## 2. cilium-operator budget (a)

- [ ] 2.1 If the chart value exists: set the operator PDB value with `maxUnavailable: 1` in `foundation/networking/clusters/feather-core/cilium/release.yaml`
- [ ] 2.2 Otherwise: add a standalone PDB manifest in `foundation/networking/clusters/feather-core/cilium/` with `maxUnavailable: 1` and the operator selector, and list it in that `kustomization.yaml`
- [ ] 2.3 Validate with `kubectl kustomize foundation/networking/clusters/feather-core/cilium`

## 3. CoreDNS budget (b)

- [ ] 3.1 Add `foundation/platform/clusters/feather-core/coredns-pdb/` with a PDB in `kube-system`, `maxUnavailable: 1`, selector `k8s-app: kube-dns`
- [ ] 3.2 List the new directory in `foundation/layers/feather-core/platform/kustomization.yaml`
- [ ] 3.3 Validate with `kubectl kustomize foundation/platform/clusters/feather-core/coredns-pdb`

## 4. Validation

- [ ] 4.1 Run `./.github/scripts/validate.sh`
- [ ] 4.2 Run `kubectl drain --dry-run=server` on one control-plane node after merge and record the result

## 5. Rollout

- [ ] 5.1 Merge the code PR; confirm `foundation-networking` and `foundation-platform` are Ready
- [ ] 5.2 Confirm `kubectl get pdb -A` shows `allowedDisruptions: 1` for both budgets
- [ ] 5.3 Confirm the operator and CoreDNS pod start times are unchanged

## 6. Code pull request

- [ ] 6.1 Open the code pull request under the title `feat(platform): add disruption budgets for cilium operator and coredns`; no files under `openspec/` in it
