## 1. Preflight

- [ ] 1.1 Record free CPU and memory per storage node and the control-plane nodes; confirm one collector pod fits on each
- [ ] 1.2 Choose a free address from pool `lb-fr01` for the intake, check it is unused (`kubectl get svc -A -o wide`), record it in the code PR
- [ ] 1.3 Choose the Alloy component for Talos json_lines and test it with a sample message from a test Talos node (open question 1)
- [ ] 1.4 Verify the metric names exist in Mimir: `apiserver_client_certificate_expiration_seconds_bucket`, the kubelet certificate TTL metric, `certmanager_certificate_expiration_timestamp_seconds`, `etcd_server_has_leader`, `etcd_server_leader_changes_seen_total`
- [ ] 1.5 Check whether the Talos etcd metrics endpoint is reachable from an Alloy pod (open question 2); record the answer
- [ ] 1.6 Confirm the chosen Alloy chart version matches `alloy-logs` (`=1.15.0`)

## 2. Collector coverage (a)

- [ ] 2.1 Add `controller.tolerations` for `node-role.kubernetes.io/control-plane:NoSchedule` and `node-role.feather/storage:NoSchedule` to `services/observability/clusters/feather-core/alloy-logs/release.yaml`
- [ ] 2.2 Validate with `kubectl kustomize services/observability/clusters/feather-core/alloy-logs`

## 3. Talos log intake (b)

- [ ] 3.1 Add the base `services/observability/base/alloy-talos-logs/` (HelmRelease, pinned chart, two replicas, PDB `maxUnavailable: 1`, remediation settings)
- [ ] 3.2 Add the Alloy config for the chosen component (task 1.3) forwarding to `loki-gateway` with labels `cluster` and `job="talos"`
- [ ] 3.3 Add the overlay `services/observability/clusters/feather-core/alloy-talos-logs/` with the Service annotated `lbipam.cilium.io/ips` (task 1.2)
- [ ] 3.4 Add the overlay to the observability layer's `kustomization.yaml`

## 4. Alerts (c)

- [ ] 4.1 Add the rules for API server client certificate, kubelet serving certificate and cert-manager certificate expiry (14 days) to `services/observability/clusters/feather-core/grafana/alerting/rules.yaml`
- [ ] 4.2 Add the etcd rules (no leader for 1 minute; more than three leader changes in 15 minutes)
- [ ] 4.3 If task 1.5 says the etcd endpoint is not reachable, record the limitation in the rule annotation and in the open questions of the code PR

## 5. Validation

- [ ] 5.1 Run `./.github/scripts/validate.sh`
- [ ] 5.2 Confirm that no file under `services/observability/clusters/feather-core/alloy-receiver/` or `services/observability/base/alloy-receiver/` changed

## 6. Rollout

- [ ] 6.1 Merge the code PR; confirm `services-observability` is Ready
- [ ] 6.2 Confirm `alloy-logs` runs on 10 nodes
- [ ] 6.3 Confirm the intake Service has the reserved address
- [ ] 6.4 Confirm the alert rules evaluate to `Normal`

## 7. Dependencies outside this repository

- [ ] 7.1 Talos repository change: `machine.logging` destination to the reserved address, format `json_lines` (must be live for 7.2 to show data)
- [ ] 7.2 Verify json_lines from one Talos node in Loki

## 8. Code pull request

- [ ] 8.1 Open the code pull request under the title `feat(observability): cover all nodes with logs, add talos log intake and cert and etcd alerts`; no files under `openspec/` in it
