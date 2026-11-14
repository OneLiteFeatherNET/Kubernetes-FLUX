## ADDED Requirements

### Requirement: cilium-operator disruption budget

THE cluster SHALL define a PodDisruptionBudget for the cilium-operator pods with `maxUnavailable: 1`.

#### Scenario: Budget selects the operator pods

- **GIVEN** the cilium-operator Deployment runs two replicas
- **WHEN** the PodDisruptionBudget is read
- **THEN** its selector matches both operator pods
- **AND** `status.disruptionsAllowed` is `1`

#### Scenario: Drain cannot remove both operator replicas

- **GIVEN** both operator replicas run on nodes that are drained one after the other
- **WHEN** the second eviction is requested while the first replica is not ready
- **THEN** the eviction is refused

### Requirement: CoreDNS disruption budget

THE cluster SHALL define a PodDisruptionBudget in namespace `kube-system` for pods with label `k8s-app: kube-dns` with `maxUnavailable: 1`.

#### Scenario: Budget selects CoreDNS pods

- **GIVEN** CoreDNS runs at least two replicas
- **WHEN** the PodDisruptionBudget is read
- **THEN** its selector is `k8s-app: kube-dns`
- **AND** `maxUnavailable` is `1`

### Requirement: Budgets do not restart running pods

THE change SHALL NOT restart, evict or modify any running pod of cilium-operator or CoreDNS.

#### Scenario: Pod start times are unchanged after merge

- **GIVEN** the operator and CoreDNS pods before the merge
- **WHEN** the change is reconciled
- **THEN** their pod names and start times are unchanged

### Requirement: Budget precondition

THE CoreDNS PodDisruptionBudget SHALL NOT be merged while CoreDNS runs fewer than two replicas.

#### Scenario: One replica blocks the merge

- **GIVEN** CoreDNS runs one replica
- **WHEN** the code PR is checked against task 1.1
- **THEN** the PR is not merged until the replica count is at least two
