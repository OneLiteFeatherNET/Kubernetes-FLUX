**Gate:** the companion change `expose-etcd-metrics` (talos-cluster repository) is live: a plain-HTTP metrics listener on :2381 answers on 192.168.15.10, .11 and .12. Tasks 1.x may be prepared earlier; the merge in section 4 happens only after the gate.

## 1. Preparation

- [ ] 1.1 Confirm the gate: `curl -s http://192.168.15.1x:2381/metrics | head` answers on all three nodes (from a node-network host, not from the cluster)
- [ ] 1.2 Re-check the chart keys for `kubeEtcd` against the pinned version 93.0.0 (`helm show values` with `--version 93.0.0`)
- [ ] 1.3 Record the current series counts in Mimir for `apiserver_request_sli_duration_seconds_bucket` and `count({__name__=~"etcd_.*"})` as the baseline

## 2. Implementation (kube-prometheus-stack)

- [ ] 2.1 In `foundation/observability/clusters/feather-core/kube-prometheus-stack/release.yaml`, set `kubeEtcd.endpoints` to the three node IPs, `kubeEtcd.service.port` and `targetPort` to 2381, `kubeEtcd.serviceMonitor.scheme` to `http`, `kubeEtcd.serviceMonitor.authorization: null`, and the etcd keep-list in `kubeEtcd.serviceMonitor.metricRelabelings`
- [ ] 2.2 In the same file, replace the `kubeApiServer` drop rule for `apiserver_request_sli_duration_seconds_bucket` with a drop on `__name__;le` that keeps `le` in {0.1, 0.5, 1, 5, +Inf}; keep the other drops unchanged

## 3. Alert rule (Grafana)

- [ ] 3.1 In `services/observability/clusters/feather-core/grafana/alerting/rules.yaml`, add rule "PVC almost full" to the `storage` group with `uid: pvc-almost-full`, the expression from design.md, `for: 15m`, `severity: warning`, and annotations in the style of the existing rules (no `${` in any value)

## 4. Validation

- [ ] 4.1 Run `./.github/scripts/validate.sh`; fix findings
- [ ] 4.2 Run `kubectl kustomize foundation/observability/clusters/feather-core/kube-prometheus-stack` if it renders standalone, or confirm via validate.sh output

## 5. Rollout

- [ ] 5.1 Merge the code PR only after the gate in 1.1 holds; confirm the `foundation-observability` layer is Ready at the merged revision
- [ ] 5.2 Verify `up{job="kube-etcd"} == 3` in Mimir within two scrape intervals
- [ ] 5.3 Verify `count({__name__=~"etcd_.*"})` is non-zero and the etcd keep-list adds fewer than 1.5k series
- [ ] 5.4 Verify `count(apiserver_request_sli_duration_seconds_bucket)` is about 9k and that only `le` values in the keep set are stored
- [ ] 5.5 Verify the Mimir tenant head series stays below 520k over 24 hours
- [ ] 5.6 Check the `etcd-no-leader` and `etcd-leader-changes-high` rules now evaluate real data and do not fire spuriously

## 6. Code pull request

- [ ] 6.1 Open the code pull request under the title `feat(observability): restore etcd and apiserver latency metrics`; it contains no files under `openspec/`
