## ADDED Requirements

### Requirement: Scheduled etcd snapshot

WHEN the CronJob `etcd-snapshot` runs, THE cluster SHALL take one etcd snapshot with `talosctl etcd snapshot` from a control-plane node and SHALL store it on every active S3 target.

#### Scenario: Daily snapshot is stored in-cluster

- **GIVEN** the CronJob `etcd-snapshot` is not suspended and the Talos role `os:etcd:backup` is live
- **WHEN** the scheduled run completes
- **THEN** a new object with prefix `etcd/` exists in bucket `etcd-snapshots` on the in-cluster RGW
- **AND** the object's size is greater than zero

#### Scenario: Missing external target does not fail the run

- **GIVEN** the Secret for the external S3 target does not exist
- **WHEN** the scheduled run completes the in-cluster upload
- **THEN** the job logs `external target not configured`
- **AND** the job is marked successful

### Requirement: Least-privilege Talos access

THE `ServiceAccount` for the snapshot SHALL hold only the Talos role `os:etcd:backup`, and THE snapshot pod SHALL NOT mount any Kubernetes ServiceAccount token for the Kubernetes API.

#### Scenario: Snapshot pod has no Kubernetes API token

- **GIVEN** a running pod of the CronJob `etcd-snapshot`
- **WHEN** its volume mounts are listed
- **THEN** no mount of `/var/run/secrets/kubernetes.io/serviceaccount` exists

### Requirement: Snapshot pod security

THE namespace `etcd-backup` SHALL enforce the Pod Security `baseline` level, and THE snapshot pod SHALL run as non-root with all capabilities dropped.

#### Scenario: Privileged snapshot pod is rejected

- **GIVEN** a pod in namespace `etcd-backup` that sets `privileged: true`
- **WHEN** it is submitted to the API server
- **THEN** the API server rejects it

### Requirement: Retention

THE job SHALL keep at most 14 snapshots on the in-cluster target and SHALL remove older snapshots with prefix `etcd/` on each run.

#### Scenario: Old snapshots are pruned

- **GIVEN** 15 snapshots exist in prefix `etcd/`
- **WHEN** a scheduled run completes
- **THEN** exactly 14 snapshots remain
- **AND** the oldest snapshot has been removed

### Requirement: Missing-snapshot alert

WHEN no successful run of `etcd-snapshot` has completed for 26 hours, THE Grafana alerting SHALL fire the rule `etcd-snapshot-missing` in folder `Core Services`.

#### Scenario: Alert fires after 26 hours without success

- **GIVEN** the last successful run of CronJob `etcd-snapshot` completed 27 hours ago
- **WHEN** the rule is evaluated
- **THEN** the rule is in state `Alerting`

#### Scenario: Alert stays quiet after a successful run

- **GIVEN** the last successful run completed 2 hours ago
- **WHEN** the rule is evaluated
- **THEN** the rule is in state `Normal`

### Requirement: Restore is proven before completion

THE change SHALL NOT be marked complete until one snapshot has been restored into a throwaway cluster and the restored cluster's API server returned the expected namespaces and Secrets.

#### Scenario: Restore test passes

- **GIVEN** a snapshot from target 1 and an empty throwaway Talos cluster
- **WHEN** the snapshot is restored with the documented procedure
- **THEN** `kubectl get namespaces` on the restored cluster lists the namespaces that existed at snapshot time
