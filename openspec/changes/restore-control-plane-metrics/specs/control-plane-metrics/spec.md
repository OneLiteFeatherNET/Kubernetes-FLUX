## ADDED Requirements

### Requirement: etcd members are scraped by node address

WHEN the observability layer reconciles, THE kube-prometheus-stack release SHALL scrape etcd on each of the three control-plane nodes (`192.168.15.10`, `192.168.15.11`, `192.168.15.12`) on port 2381 over plain HTTP, without a bearer token.

#### Scenario: All etcd members are targets

- **GIVEN** the Talos metrics listener on :2381 is live on all three control-plane nodes
- **WHEN** Mimir is queried for `up{job="kube-etcd"}`
- **THEN** the value is 3 for the three members

#### Scenario: No pod selector is used for etcd

- **GIVEN** the rendered `kube-etcd` Service
- **WHEN** it is inspected
- **THEN** it has no `selector` and its Endpoints list the three node IPs

### Requirement: Only the etcd families needed for disk, leader and size signals are stored

WHEN etcd is scraped, THE scrape SHALL keep only the families listed in design.md (WAL fsync, backend commit, leader and proposal state, DB size, peer round-trip, heartbeat failures, resident memory, `up`) and SHALL drop all other etcd series.

#### Scenario: Unlisted etcd family is dropped

- **GIVEN** an etcd scrape that exposes `etcd_grpc_proposals_committed_total`
- **WHEN** the samples are stored
- **THEN** no series named `etcd_grpc_proposals_committed_total` is present in Mimir

#### Scenario: Budget holds

- **GIVEN** the etcd scrape is active on three members
- **WHEN** `count({__name__=~"etcd_.*"})` is evaluated
- **THEN** the result is fewer than 1.5k series in total

### Requirement: apiserver SLI histogram keeps a fixed set of buckets

WHEN the apiserver scrape stores `apiserver_request_sli_duration_seconds_bucket`, THE scrape SHALL keep only samples whose `le` label is one of `0.1`, `0.5`, `1`, `5` or `+Inf`.

#### Scenario: Kept bucket is stored

- **GIVEN** a sample `apiserver_request_sli_duration_seconds_bucket{le="1", verb="GET", ...}`
- **WHEN** it is scraped
- **THEN** the sample is stored

#### Scenario: Other bucket is dropped

- **GIVEN** a sample `apiserver_request_sli_duration_seconds_bucket{le="0.25", ...}`
- **WHEN** it is scraped
- **THEN** the sample is not stored

#### Scenario: Other apiserver histograms stay dropped

- **GIVEN** a sample `apiserver_request_duration_seconds_bucket{le="1", ...}`
- **WHEN** it is scraped
- **THEN** the sample is not stored

### Requirement: A PVC above 85 % full raises a warning

WHEN a PersistentVolumeClaim's used bytes exceed 85 % of its capacity for 15 minutes, as reported by kubelet, THE alerting configuration SHALL raise a warning alert labelled with the PVC's namespace and name.

#### Scenario: PVC above threshold fires

- **GIVEN** a PVC `data-x` in namespace `example` at 90 % used
- **WHEN** the condition has held for 15 minutes
- **THEN** the alert "PVC almost full" fires with `severity=warning`, `namespace=example` and `persistentvolumeclaim=data-x`

#### Scenario: PVC below threshold does not fire

- **GIVEN** a PVC at 70 % used
- **WHEN** the rule is evaluated
- **THEN** no alert instance is created for that PVC

### Requirement: The etcd scrape does not start before the Talos listener

WHILE the metrics listener on :2381 is not live on a control-plane node, THE release SHALL NOT be merged to `main`.

#### Scenario: Gate is checked before merge

- **GIVEN** the code pull request is ready
- **WHEN** a maintainer checks the gate in tasks.md section 1
- **THEN** the merge proceeds only if all three nodes answer on :2381
