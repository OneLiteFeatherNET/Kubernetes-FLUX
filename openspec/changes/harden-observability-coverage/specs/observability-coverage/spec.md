## ADDED Requirements

### Requirement: Log collection on every node

THE `alloy-logs` DaemonSet SHALL run a pod on every node of `feather-core`, including control-plane and storage nodes.

#### Scenario: Every node has a collector pod

- **GIVEN** the `alloy-logs` DaemonSet is reconciled
- **WHEN** the number of ready pods is compared with the number of nodes
- **THEN** both numbers are 10

#### Scenario: Existing pods are not restarted by the toleration change

- **GIVEN** `alloy-logs` pods running on four nodes before the change
- **WHEN** the tolerations are applied
- **THEN** no existing pod is deleted by the DaemonSet controller

### Requirement: Talos log intake independent of OTLP

THE cluster SHALL expose a Service `alloy-talos-logs` on a fixed LoadBalancer address from pool `lb-fr01`, and THE Service SHALL NOT share pods or ports with `alloy-receiver`.

#### Scenario: Intake address is fixed

- **GIVEN** the Service `alloy-talos-logs`
- **WHEN** its annotation `lbipam.cilium.io/ips` is read
- **THEN** it names one address inside the pool `lb-fr01`

#### Scenario: OTLP receiver is untouched

- **GIVEN** the `alloy-talos-logs` HelmRelease is reconciled
- **WHEN** the pods of `alloy-receiver` are listed before and after
- **THEN** the pod names and start times are unchanged

### Requirement: Talos json_lines reach Loki

WHEN a Talos node sends a `json_lines` message to `alloy-talos-logs`, THE message SHALL be stored in Loki with label `cluster="feather-core"`.

#### Scenario: Message is queryable

- **GIVEN** a Talos node configured with the intake address as `machine.logging` destination
- **WHEN** Loki is queried for `{cluster="feather-core", job="talos"}`
- **THEN** at least one line from that node is returned

### Requirement: Certificate expiry alerts

THE Grafana alerting SHALL fire when any of the following has less than 14 days of validity remaining: the Kubernetes API server client certificate, a kubelet serving certificate, or a cert-manager `Certificate`.

#### Scenario: Expiring cert-manager certificate alerts

- **GIVEN** a cert-manager `Certificate` that expires in 10 days
- **WHEN** the rule is evaluated
- **THEN** the rule is in state `Alerting` for that certificate

#### Scenario: Healthy certificate stays quiet

- **GIVEN** a cert-manager `Certificate` that expires in 60 days
- **WHEN** the rule is evaluated
- **THEN** the rule is in state `Normal` for that certificate

### Requirement: etcd health alerts

THE Grafana alerting SHALL fire when etcd reports no leader for 1 minute, or when more than three leader changes occur within 15 minutes.

#### Scenario: Leader loss alerts

- **GIVEN** etcd reports `etcd_server_has_leader` equal to 0 for 1 minute
- **WHEN** the rule is evaluated
- **THEN** the rule is in state `Alerting`

### Requirement: No change to running receivers

THE change SHALL NOT modify the Deployment or Service of `alloy-receiver`.

#### Scenario: Receiver manifests unchanged

- **GIVEN** the code PR
- **WHEN** the diff is checked for files under `services/observability/clusters/feather-core/alloy-receiver/` and `services/observability/base/alloy-receiver/`
- **THEN** no file is changed
