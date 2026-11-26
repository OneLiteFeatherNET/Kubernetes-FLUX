## ADDED Requirements

### Requirement: No CPU limit on core operator controllers

WHILE the cluster runs under normal load, the core operator controllers (cert-manager-cainjector, cert-manager-controller, step-issuer manager, mariadb-operator controller) SHALL NOT have a CPU limit.

#### Scenario: Leader-elected controller is not CPU-limited

- **GIVEN** the cert-manager-cainjector Deployment is rendered from the cluster overlay
- **WHEN** its container resources are read
- **THEN** no `limits.cpu` is set
- **AND** `requests.cpu` is set

#### Scenario: Throttling does not break leader election

- **GIVEN** the change has been live for 7 days
- **WHEN** the CFS throttled-periods ratio of cert-manager-cainjector is queried
- **THEN** no restart is recorded with the reason "leader election lost"

### Requirement: Memory limit sized from the measured maximum

THE memory limit of each core operator controller SHALL be at least 1.5 times its 14-day maximum working set.

#### Scenario: Limit covers the measured maximum

- **GIVEN** the 14-day maximum working set of cert-manager-cainjector is 214 MiB
- **WHEN** the memory limit is read from the rendered manifest
- **THEN** the limit is at least 321 MiB
- **AND** the configured limit is 384Mi

#### Scenario: Clipped container is re-measured

- **GIVEN** step-issuer's measured usage is pinned at its 128Mi limit
- **WHEN** the change has been live for 7 days
- **THEN** the 7-day maximum working set is queried again
- **AND** the limit is raised again if the new maximum is at or above 1.5 times the limit set by this change

### Requirement: Restart budget after the change

WHEN the change has been live for 7 days, each core operator controller SHALL have restarted at most once.

#### Scenario: Restart count after 7 days

- **GIVEN** the change was merged and the operators rolled once
- **WHEN** `increase(kube_pod_container_status_restarts_total{job="kube-state-metrics",namespace=~"cert-manager|step-issuer|mariadb-operator"}[7d])` is evaluated per container
- **THEN** each container reports at most 1 restart

#### Scenario: Restarts above the budget

- **GIVEN** a core operator controller restarts more than once in 7 days after the change
- **WHEN** the restart reasons are read from `lastState`
- **THEN** the cause is recorded as a follow-up task (memory, throttling or an unrelated failure) before the limits are changed again
