## ADDED Requirements

### Requirement: Dashboards load within 30 seconds

WHEN a user opens a dashboard of the Corvus bundle, THE Superset SHALL render every chart of that dashboard within 30 seconds.

#### Scenario: Dashboard renders within the limit

- **GIVEN** the Superset instance runs the DuckDB connection `corvus`
- **AND** the lake holds the last 7 days of `capture.events`
- **WHEN** a user opens the dashboard "corvus-aktionen"
- **THEN** all of its charts show data within 30 seconds
- **AND** the same timing holds for the other dashboards of the bundle at the 95th percentile of the task 4 measurements

### Requirement: Superset is not OOM-killed under dashboard load

THE Superset container SHALL NOT be terminated with reason `OOMKilled` while the dashboards of the bundle are used.

#### Scenario: Peak memory stays below the limit

- **GIVEN** the Superset web-server container has a memory limit of 6Gi
- **WHEN** every dashboard of the bundle is loaded once, in parallel by two users
- **THEN** the container's peak working set stays below 5Gi
- **AND** no container of the Superset pods reports `OOMKilled`

### Requirement: DuckDB reads the lake only through the Iceberg catalog

THE dashboard datasets SHALL read Corvus tables only through the attached Iceberg catalog `corvus` on Polaris, and SHALL NOT read Parquet files by path. This is enforced by dataset review, not by the credentials: the RGW user can read the whole bucket (see design, risks).

#### Scenario: Datasets reference catalog tables only

- **GIVEN** the bundle datasets of this change
- **WHEN** their SQL is listed
- **THEN** every table reference is of the form `corvus.<namespace>.<table>`
- **AND** no dataset SQL contains `read_parquet`, `read_csv` or an `s3://` path

#### Scenario: Catalog access uses the attached catalog

- **GIVEN** the connection `corvus` is open
- **WHEN** a dataset query runs
- **THEN** the table metadata is loaded from Polaris through the attached catalog `corvus`
- **AND** no table data is read from a location outside the snapshot returned by Polaris

### Requirement: Credentials are read-only

THE DuckDB session SHALL authenticate to Polaris with the principal role `lake_sql` and to RGW with the keys of user `trino-lake`, and neither credential SHALL allow a write to catalog `corvus` or to bucket `polaris-corvus`.

#### Scenario: Write through SQL is denied

- **GIVEN** a DuckDB session attached to catalog `corvus`
- **WHEN** a statement tries to create, insert into or drop a table in `corvus.capture`
- **THEN** the statement fails
- **AND** Polaris and RGW record no write

#### Scenario: Principal has only read grants

- **GIVEN** the Polaris principal "Polaris Superset" is mapped to `lake_sql`
- **WHEN** its catalog grants are listed
- **THEN** they are exactly the grants of the catalog role `sql`: `TABLE_LIST`, `VIEW_LIST`, `NAMESPACE_LIST`, `TABLE_READ_PROPERTIES`, `TABLE_READ_DATA`

### Requirement: Extensions load from the image

THE Superset image SHALL contain the DuckDB extensions it uses, and THE DuckDB connection SHALL NOT download an extension at runtime.

#### Scenario: Pod starts without internet access to extension repositories

- **GIVEN** a Superset pod whose egress allows only DNS, Polaris, RGW and Entra ID
- **WHEN** the pod starts and a dashboard query runs
- **THEN** the extensions load from the image
- **AND** no `INSTALL` request is made to an extension repository

### Requirement: DuckDB memory and spill are bounded per connection

EACH DuckDB connection SHALL use at most the configured `memory_limit` and `threads`, and SHALL spill to the `emptyDir` volume named in the Superset pod.

#### Scenario: A large query spills instead of exhausting memory

- **GIVEN** a DuckDB connection with `memory_limit` 2GB and `temp_directory` on the spill volume
- **WHEN** a query needs more than 2GB of working memory
- **THEN** intermediate data is written to the spill volume
- **AND** the query completes or fails with a DuckDB error, not with an OOM kill

### Requirement: Trino is removed

WHEN the removal step is complete, THE namespace `data` SHALL NOT contain a Trino HelmRelease, Deployment, Service, NetworkPolicy or secret, and no datasource SHALL reference Trino.

#### Scenario: No Trino objects remain

- **GIVEN** the removal PR is reconciled by Flux
- **WHEN** the workloads of namespace `data` are listed
- **THEN** no Deployment or StatefulSet with the label `app.kubernetes.io/name=trino` exists
- **AND** no database in Superset has a URI starting with `trino://`

### Requirement: Superset NetworkPolicy allows only the lake path

THE Superset NetworkPolicy SHALL allow egress to Polaris (TCP 8181), to RGW (TCP 80) and to Entra ID (TCP 443), and SHALL allow no other egress besides DNS and CloudNativePG.

#### Scenario: Egress to Polaris and RGW succeeds

- **GIVEN** the Superset pod runs under the NetworkPolicy of this change
- **WHEN** a DuckDB session attaches catalog `corvus` and reads a Parquet file
- **THEN** the connections to Polaris on 8181 and to RGW on 80 succeed
- **AND** a connection to any other pod in namespace `data` is refused
