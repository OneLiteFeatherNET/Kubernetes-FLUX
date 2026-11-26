## 1. Implementation

- [ ] 1.1 cert-manager-cainjector: remove `limits.cpu`, keep `requests.cpu: 30m`, set `requests.memory: 224Mi` and `limits.memory: 384Mi` in `foundation/certificates/clusters/feather-core/cert-manager/release.yaml`
- [ ] 1.2 cert-manager-controller: remove `limits.cpu`, keep `requests.cpu: 20m`, set `requests.memory: 96Mi` and `limits.memory: 160Mi` in the same file
- [ ] 1.3 step-issuer manager: set `requests.memory: 160Mi` and `limits.memory: 256Mi` in `foundation/certificates/base/step-issuer/release.yaml` (or its cluster overlay, whichever holds the values)
- [ ] 1.4 mariadb-operator controller: set `requests.memory: 256Mi` and `limits.memory: 512Mi` in `foundation/databases/base/mariadb-operator/release.yaml`
- [ ] 1.5 Leave the webhook values unchanged
- [ ] 1.6 Add a one-line comment next to each removed CPU limit that points to the Outline runbook, not a long explanation

## 2. Validation

- [ ] 2.1 Run `./.github/scripts/validate.sh`
- [ ] 2.2 Render the changed overlays with `kubectl kustomize foundation/certificates/clusters/feather-core/cert-manager` and `kubectl kustomize foundation/databases/clusters/feather-core/mariadb-operator` (or the overlay path that exists) and check the resource values
- [ ] 2.3 Confirm every memory limit is at least 1.5 times its 14-day maximum (values in design, decision 2)

## 3. Rollout

- [ ] 3.1 After merge, confirm `foundation-certificates-operators` and `foundation-databases-operators` are Ready at the merge revision
- [ ] 3.2 Confirm each Deployment rolled once and its pods are Ready

## 4. Verification after 7 days

- [ ] 4.1 Restarts: `increase(kube_pod_container_status_restarts_total{job="kube-state-metrics",namespace=~"cert-manager|step-issuer|mariadb-operator"}[7d])` per container; expect at most 1
- [ ] 4.2 Throttling: `sum by (container) (rate(container_cpu_cfs_throttled_periods_total{namespace=~"cert-manager|step-issuer|mariadb-operator"}[7d])) / sum by (container) (rate(container_cpu_cfs_periods_total{namespace=~"cert-manager|step-issuer|mariadb-operator"}[7d]))`; expect no sustained ratio on the controllers
- [ ] 4.3 Memory: `max_over_time(container_memory_working_set_bytes{namespace=~"cert-manager|step-issuer|mariadb-operator"}[7d])` per container; raise a clipped limit to 2 times its current value if usage sits at the limit
- [ ] 4.4 Record the results in the Outline runbook, not in the repository

## 5. Code pull request

- [ ] 5.1 Open the code pull request under the title `fix(operators): right-size cert-manager, step-issuer and mariadb-operator`; no files under `openspec/` in it
