## 1. Preflight

- [ ] 1.1 Read the Talos hardening page (https://docs.siderolabs.com/talos/v1.14/security/talos-default-hardening-and-cis-compliance) and list every check it marks as not applicable or handled by Talos; record the list with reasons
- [ ] 1.2 Generate the `kube-bench-skips` ConfigMap content from task 1.1, one entry per check with its reason and link
- [ ] 1.3 Pin the kube-bench image by digest; confirm it supports benchmark 1.10 (open question in design)
- [ ] 1.4 Confirm the kube-state-metrics CronJob metric `kube_cronjob_status_last_successful_time` exists in Mimir

## 2. Base component

- [ ] 2.1 Add `foundation/security/base/kube-bench/` with namespace `kube-bench` labelled `pod-security.kubernetes.io/enforce: privileged`
- [ ] 2.2 Add the ConfigMap `kube-bench-skips` from task 1.2
- [ ] 2.3 Add the CronJob `kube-bench` (weekly, Sunday 04:00, `concurrencyPolicy: Forbid`, `ttlSecondsAfterFinished`), with two Job templates: control-plane and worker
- [ ] 2.4 Add tolerations for the control-plane `NoSchedule` taint on the control-plane Job, and for `node-role.feather/storage:NoSchedule` on the worker Job
- [ ] 2.5 Set requests 50m CPU and 128Mi memory, limits 500m and 256Mi
- [ ] 2.6 Set `suspend: true` for the first merge

## 3. Cluster overlay and alerting

- [ ] 3.1 Add `foundation/security/clusters/feather-core/kube-bench/` with `kustomization.yaml`
- [ ] 3.2 List the overlay in `foundation/layers/feather-core/security/kustomization.yaml`
- [ ] 3.3 Add the Grafana rule `kube-bench-stale` to `services/observability/clusters/feather-core/grafana/alerting/rules.yaml` (folder `Core Services`, 8 days)
- [ ] 3.4 Validate with `./.github/scripts/validate.sh`

## 4. Rollout

- [ ] 4.1 Merge the code PR; confirm `foundation-security` is Ready
- [ ] 4.2 Run one manual Job per role (`kubectl create job --from=cronjob/kube-bench`); read the summary line in Loki
- [ ] 4.3 Decide on the control-plane target (open question in design); record the baseline fail count
- [ ] 4.4 Resume the CronJob (`suspend: false`)

## 5. Follow-ups outside this change

- [ ] 5.1 Second Grafana rule on the fail count above the baseline (after 4.3)
- [ ] 5.2 Grafana dashboard panel for kube-bench results (its own change)

## 6. Code pull request

- [ ] 6.1 Open the code pull request under the title `feat(security): add weekly cis kube-bench scan with talos skip list`; no files under `openspec/` in it
