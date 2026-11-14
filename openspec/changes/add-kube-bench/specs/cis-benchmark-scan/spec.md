## ADDED Requirements

### Requirement: Scheduled CIS scan per node role

WHEN the CronJob `kube-bench` runs, THE cluster SHALL run kube-bench against CIS benchmark 1.10 once for control-plane nodes and once for worker nodes.

#### Scenario: Both roles are scanned

- **GIVEN** the CronJob `kube-bench` is not suspended
- **WHEN** the scheduled run completes
- **THEN** one Job pinned to control-plane nodes and one Job pinned to worker nodes have completed

### Requirement: Read-only scan

THE kube-bench pods SHALL NOT modify any node configuration, file or running workload.

#### Scenario: Scan changes nothing

- **GIVEN** a completed run of the CronJob
- **WHEN** the node configuration files are compared with their state before the run
- **THEN** no file has changed

### Requirement: Privileged rights only in the scan namespace

THE namespace `kube-bench` SHALL be the only namespace labelled `pod-security.kubernetes.io/enforce: privileged` that this change introduces, and no other workload SHALL run in it.

#### Scenario: Only kube-bench pods run in the namespace

- **GIVEN** namespace `kube-bench`
- **WHEN** its pods are listed
- **THEN** every pod belongs to the CronJob `kube-bench`

### Requirement: Talos false positives are skipped with a reason

EVERY check skipped in the kube-bench configuration SHALL have a reason and a link to the Talos hardening documentation in the ConfigMap `kube-bench-skips`.

#### Scenario: Skip without reason is rejected in review

- **GIVEN** a skip entry without a reason
- **WHEN** the code PR is reviewed
- **THEN** the entry is removed or given a reason before merge

### Requirement: Results are readable in Loki

THE result of each run SHALL include a summary line `kube-bench-summary` with pass, fail, warn and info totals, and THE line SHALL be queryable in Loki.

#### Scenario: Summary line is queryable

- **GIVEN** a completed run
- **WHEN** Loki is queried for `{namespace="kube-bench"} |= "kube-bench-summary"`
- **THEN** at least one line is returned per role

### Requirement: Stale-run alert

WHEN the CronJob `kube-bench` has no successful run for 8 days, THE Grafana alerting SHALL fire the rule `kube-bench-stale` in folder `Core Services`.

#### Scenario: Alert fires after 8 days

- **GIVEN** the last successful run completed 9 days ago
- **WHEN** the rule is evaluated
- **THEN** the rule is in state `Alerting`
