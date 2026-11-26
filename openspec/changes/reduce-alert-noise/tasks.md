## 1. Preflight

- [ ] 1.1 Confirm that the provisioned Grafana (12.4) accepts `unloadEvaluator` on the threshold expression; if not, use the fallback in design (keep 80% and `keepFiringFor: 15m`)
- [ ] 1.2 List the Flux error messages of the last 14 days and check each exclusion regex against them; confirm no build, SOPS, validation or install error matches
- [ ] 1.3 Confirm that a Kustomization that is not Ready for 15 minutes still produces one Grafana alert (rule "Flux core layer not ready")

## 2. Implementation

- [ ] 2.1 Extend `exclusionList` in `foundation/sources/base/flux-notifications/alert.yaml` with the five transient-timeout regexes (`timed out waiting for the condition`, `failed to update status`, `unable to update the healthy status`, `etcdserver: request timed out`, `health check failed after`)
- [ ] 2.2 "CNPG Postgres connection saturation high": add the recovery threshold (resolve below 70%) and `keepFiringFor: 15m` in `services/observability/clusters/feather-core/grafana/alerting/rules.yaml`
- [ ] 2.3 "Otis health check failing": `for: 10m`, `keepFiringFor: 10m`
- [ ] 2.4 "Otis container restarting": fire at 2 or more restarts within 1h (`increase(...[1h])`, threshold above 1)
- [ ] 2.5 "Ceph RGW failed request rate elevated": `keepFiringFor: 15m`
- [ ] 2.6 "Minecraft TPS low": `keepFiringFor: 10m`
- [ ] 2.7 Leave resolved notifications, `group_by` and the Discord template unchanged
- [ ] 2.8 Validate with `kubectl kustomize services/observability/clusters/feather-core/grafana` and `kubectl kustomize foundation/sources/clusters/feather-core/flux-notifications` (or the overlay path that exists)

## 3. Validation

- [ ] 3.1 Run `./.github/scripts/validate.sh`
- [ ] 3.2 Confirm the rule YAML stays free of `${` and `{{- define` (see the Grafana template notes in Outline)

## 4. Rollout

- [ ] 4.1 After merge, confirm `foundation-sources` and `services-observability` are Ready at the merge revision
- [ ] 4.2 Confirm the Grafana provisioning succeeded (no "Alerting provisioning failing" alert fires)
- [ ] 4.3 Confirm the Flux Alert `cluster-errors` is Ready

## 5. Measurement after 14 days

- [ ] 5.1 Grafana messages: `increase(grafana_alerting_notifications_total[14d])`
- [ ] 5.2 Flux messages: count of notification-controller `dispatching event` log lines over 14 days
- [ ] 5.3 Compare the average per day with the baseline of about 39 per day; success is fewer than 10 per day
- [ ] 5.4 Record the result in the Outline runbook

## 6. Code pull request

- [ ] 6.1 Open the code pull request under the title `fix(observability): reduce discord alert noise`; no files under `openspec/` in it
