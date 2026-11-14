## ADDED Requirements

### Requirement: Default backup location on the in-cluster RGW

THE Velero `BackupStorageLocation` `default` SHALL point to bucket `velero` on the in-cluster RGW and SHALL be the only location with `default: true`.

#### Scenario: Default location is in-cluster

- **GIVEN** Velero is reconciled
- **WHEN** the BackupStorageLocation `default` is read
- **THEN** its endpoint is `http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80`
- **AND** `spec.default` is `true`

### Requirement: External location is inactive until credentials exist

THE `BackupStorageLocation` `external` SHALL have `accessMode: ReadOnly` and `default: false` until its credentials Secret exists.

#### Scenario: No writes to the external target before activation

- **GIVEN** the credentials Secret for the external location does not exist
- **WHEN** a scheduled backup runs
- **THEN** no object is written to the external location

### Requirement: CSI snapshots for RBD volumes

THE cluster SHALL provide a `VolumeSnapshotClass` for driver `rook-ceph.rbd.csi.ceph.com` with deletion policy `Retain`, and Velero backups SHALL use CSI snapshots for RBD-backed PVCs.

#### Scenario: Backup of an RBD PVC creates a snapshot

- **GIVEN** a PVC on storage class `rbd`
- **WHEN** a backup that includes its namespace completes
- **THEN** a `VolumeSnapshot` for that PVC exists or existed during the backup
- **AND** the backup reports no error for that PVC

### Requirement: Scheduled daily and weekly backups

THE cluster SHALL create one `Schedule` `daily` and one `Schedule` `weekly`, with TTLs of 14 and 56 days.

#### Scenario: Schedule retention is set

- **GIVEN** the Schedule `daily`
- **WHEN** its template is read
- **THEN** `ttl` equals `336h`

### Requirement: Missing-backup alert

WHEN the daily schedule has no successful backup for 26 hours, THE Grafana alerting SHALL fire the rule `velero-backup-missing` in folder `Core Services`.

#### Scenario: Alert fires after 26 hours without a successful backup

- **GIVEN** the last successful backup of Schedule `daily` completed 27 hours ago
- **WHEN** the rule is evaluated
- **THEN** the rule is in state `Alerting`

### Requirement: Velero adds no admission webhook

THE Velero installation SHALL NOT register any admission webhook.

#### Scenario: No webhook configuration from Velero

- **GIVEN** Velero is installed
- **WHEN** the cluster's ValidatingWebhookConfigurations and MutatingWebhookConfigurations are listed
- **THEN** none is owned by the Velero release

### Requirement: Node agent is off until needed

THE node agent DaemonSet SHALL NOT be deployed in the first rollout, and it SHALL be enabled only when the PVC inventory shows a volume without a CSI snapshot class.

#### Scenario: No node agent after first rollout

- **GIVEN** the first rollout is complete
- **WHEN** DaemonSets in namespace `velero` are listed
- **THEN** no node agent DaemonSet exists
