## ADDED Requirements

### Requirement: Catalog is reachable only inside the cluster

THE Polaris Service SHALL be of type `ClusterIP`, and THE cluster SHALL NOT expose Polaris through any Ingress, HTTPRoute or LoadBalancer in this change.

#### Scenario: No external route

- **GIVEN** the code pull request is applied and the layer `foundation-data` is Ready
- **WHEN** the Services and routes that point at Polaris are listed
- **THEN** only a `ClusterIP` Service exists
- **AND** no Ingress or HTTPRoute references it

### Requirement: Unauthenticated catalog requests are rejected

THE Polaris authentication SHALL be `external`, and THE catalog API SHALL reject any request that carries no valid Entra ID token.

#### Scenario: Request without token

- **GIVEN** Polaris is running
- **WHEN** a client calls the catalog REST API without an `Authorization` header
- **THEN** the response is HTTP 401

### Requirement: Consumer tokens are limited to their own catalog

WHEN a request carries a valid Entra ID token whose role maps to consumer `corvus`, THE Polaris SHALL allow creating and reading tables only in the catalog `corvus`.

#### Scenario: Consumer creates a table in its catalog

- **GIVEN** a token with the role mapped to `corvus`
- **WHEN** the client creates a table in catalog `corvus`
- **THEN** the response is HTTP 200

#### Scenario: Consumer is refused in another catalog

- **GIVEN** a token with the role mapped to `corvus`
- **WHEN** the client reads a table in another consumer's catalog
- **THEN** the response is HTTP 403

### Requirement: Packet capture path is unchanged

THE code pull request SHALL NOT change the NATS manifests or the Corvus ingest manifests. The Minecraft plugin lives outside this repository and is not changed by it.

#### Scenario: No change to the capture path manifests

- **GIVEN** the code pull request is merged
- **WHEN** the NATS and Corvus ingest manifests are compared with their state before
- **THEN** no manifest of those components has changed

### Requirement: Catalog state survives a pod restart

THE Polaris SHALL persist its catalog state in the CloudNativePG database `polaris` (persistence `relational-jdbc`), not in pod memory.

#### Scenario: Table survives restart

- **GIVEN** a table `corvus.smoke` was created through the catalog
- **WHEN** every Polaris pod is deleted and recreated
- **THEN** a read of table `corvus.smoke` after the restart succeeds

### Requirement: Corvus catalog has the capture namespace

THE catalog `corvus` SHALL provide the namespace `capture`. This change SHALL NOT create any table; the tables `capture.events` and `capture.raw` are created by the Corvus change.

#### Scenario: Namespace exists

- **GIVEN** the catalog `corvus` is bootstrapped
- **WHEN** its namespaces are listed
- **THEN** `capture` is present
- **AND** no table exists in it

#### Scenario: Retention is a per-table maintenance parameter

- **GIVEN** the maintenance DAG for catalog `corvus` is configured
- **WHEN** its parameters are read
- **THEN** they map each table name to a retention
- **AND** the table `capture.raw` has a retention of 21 days by default

### Requirement: Ingest role can write, ML role can only read

WHEN a request carries a valid Entra ID token mapped to the Polaris principal role `corvus_ingest`, THE Polaris SHALL allow appending to tables in catalog `corvus`; WHEN a request carries a valid token mapped to the principal role `corvus_ml`, THE Polaris SHALL allow reading tables in catalog `corvus` and SHALL refuse writes.

#### Scenario: Ingest role appends

- **GIVEN** a token mapped to the principal role `corvus_ingest`
- **WHEN** the client commits an append to a table in `corvus.capture.raw`
- **THEN** the commit succeeds

#### Scenario: ML role cannot write

- **GIVEN** a token mapped to the principal role `corvus_ml`
- **WHEN** the client commits an append to a table in `corvus.capture.events`
- **THEN** the response is HTTP 403

### Requirement: Maintenance role runs maintenance on catalog corvus only

WHEN a request carries a valid Entra ID token mapped to the principal role `lake_maintenance`, THE Polaris SHALL allow the maintenance operations (delete, rewrite, expire snapshots, remove orphan files) in catalog `corvus`, and SHALL refuse creating or dropping namespaces and tables in that catalog. Polaris grants table writes as one privilege, so a maintenance append cannot be refused by the catalog; the maintenance job only runs delete, rewrite, expire and orphan-cleanup operations.

#### Scenario: Maintenance role expires snapshots

- **GIVEN** a token mapped to the principal role `lake_maintenance`
- **WHEN** the client commits an expire-snapshots operation on a table in catalog `corvus`
- **THEN** the response is HTTP 200

#### Scenario: Maintenance role cannot create tables

- **GIVEN** a token mapped to the principal role `lake_maintenance`
- **WHEN** the client creates a table in namespace `corvus.capture`
- **THEN** the response is HTTP 403

### Requirement: Each catalog has its own bucket

THE cluster SHALL provide one Ceph RGW bucket per catalog, created as an ObjectBucketClaim with `bucketOwner` set to that catalog's RGW user, and no catalog SHALL use another catalog's bucket.

#### Scenario: Catalog points at its own bucket

- **GIVEN** the catalog `corvus` is configured
- **WHEN** its storage location is read
- **THEN** it names the bucket `polaris-corvus` and no other bucket

### Requirement: Scheduled maintenance enforces retention

THE Airflow SHALL run a nightly maintenance DAG for catalog `corvus` that submits a `SparkApplication` which, per table, first deletes the day partitions older than the table's retention, then runs `rewrite_data_files`, then `expire_snapshots`, then `remove_orphan_files`, in that order.

#### Scenario: Expired partitions are deleted before snapshot expiry

- **GIVEN** the table `capture.raw` has day partitions older than its 21-day retention
- **WHEN** the nightly maintenance DAG runs
- **THEN** a delete of those partitions is committed before `expire_snapshots` runs
- **AND** no row of those days is readable in the current snapshot

#### Scenario: Snapshots older than retention are expired

- **GIVEN** the catalog `corvus` has snapshots older than the retention of their table
- **WHEN** the nightly maintenance DAG completes successfully
- **THEN** no snapshot older than that retention remains in the table metadata

#### Scenario: Expired packet data is physically removed

- **GIVEN** a day of `capture.raw` is older than its retention
- **WHEN** the nightly maintenance DAG completes successfully
- **THEN** the data files of that day are no longer present in the bucket

#### Scenario: Orphan files are removed

- **GIVEN** the bucket of catalog `corvus` holds data files that no snapshot references
- **WHEN** the nightly maintenance DAG completes successfully
- **THEN** those files are no longer present in the bucket

### Requirement: Airflow is reachable only inside the cluster

THE Airflow API server and UI SHALL be of type `ClusterIP`, and THE cluster SHALL NOT expose them through any Ingress or HTTPRoute in this change.

#### Scenario: No external route to Airflow

- **GIVEN** the code pull request is applied and the layer `foundation-data` is Ready
- **WHEN** the Services and routes that point at the Airflow API server are listed
- **THEN** only a `ClusterIP` Service exists
- **AND** no Ingress or HTTPRoute references it

### Requirement: Components expose metrics for alloy

THE Polaris, the Spark operator and Airflow SHALL each have a `ServiceMonitor` or `PodMonitor` that alloy scrapes without extra labels.

#### Scenario: Metrics are scraped

- **GIVEN** the code pull request is applied
- **WHEN** the scrape targets of these three components are listed in Prometheus
- **THEN** each component has at least one target in state `up`

### Requirement: Layers reconcile with declared dependencies

THE layers `foundation-data-operators` and `foundation-data` SHALL reconcile to Ready at the same git revision as the dependencies declared for them.

#### Scenario: Data layers become Ready

- **GIVEN** the code pull request is merged to `main`
- **WHEN** Flux reconciles the layers
- **THEN** `foundation-data-operators` depends on `foundation-platform`
- **AND** `foundation-data` depends on `foundation-data-operators`, `foundation-databases` and `foundation-storage`
- **AND** both layers report Ready
