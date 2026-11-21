## ADDED Requirements

### Requirement: Layer namespaces exist in catalog corvus

THE Polaris catalog `corvus` SHALL contain the namespaces `refined`, `features`, `labels`, `ref` and `quality`, next to `capture`.

#### Scenario: Namespaces are created

- **GIVEN** the setup Job `polaris-setup-7` has completed
- **WHEN** the namespaces of catalog `corvus` are listed
- **THEN** the list contains `capture`, `refined`, `features`, `labels`, `ref` and `quality`

### Requirement: Pipeline principal reads Corvus tables and writes only the layers

THE principal role `lake_pipeline` SHALL read the tables `capture.raw`, `capture.events`, `capture.sessions`, `capture.segments` and `capture.session_ends`, SHALL create, read and write tables in `refined`, `features`, `labels`, `ref` and `quality`, and SHALL NOT write, delete or drop any table in `capture`.

#### Scenario: Pipeline reads a Corvus table

- **GIVEN** a token whose role maps to `lake_pipeline`
- **WHEN** the client loads the table `capture.raw`
- **THEN** the response is HTTP 200

#### Scenario: Pipeline writes a layer table

- **GIVEN** a token whose role maps to `lake_pipeline`
- **WHEN** the client appends a snapshot to `refined.packets`
- **THEN** the response is HTTP 200

#### Scenario: Pipeline cannot write capture

- **GIVEN** a token whose role maps to `lake_pipeline`
- **WHEN** the client appends a snapshot to `capture.raw`
- **THEN** the response is HTTP 403

### Requirement: Erasure principal is separate and cannot write capture

THE principal role `lake_erasure` SHALL be a principal role separate from `lake_pipeline`, SHALL read `capture.raw` and `capture.events` for the erasure guards, SHALL delete and write in `refined`, `features`, `labels` and `quality`, SHALL have the expire and orphan-cleanup rights on those namespaces, and SHALL NOT write, delete or drop any table in `capture`.

#### Scenario: Erasure deletes a downstream row

- **GIVEN** a token whose role maps to `lake_erasure`
- **WHEN** the client commits a row-level delete on `refined.packets`
- **THEN** the response is HTTP 200

#### Scenario: Erasure cannot delete capture rows

- **GIVEN** a token whose role maps to `lake_erasure`
- **WHEN** the client commits a row-level delete on `capture.raw`
- **THEN** the response is HTTP 403

#### Scenario: Erasure guard reads capture

- **GIVEN** a token whose role maps to `lake_erasure`
- **WHEN** the client counts the rows of `capture.events` filtered by one key
- **THEN** the response is HTTP 200

### Requirement: Corvus ingest writes only the erasure request table in quality

THE principal `corvus_ingest` SHALL keep its existing rights on `capture`, and SHALL have table-level write rights on `quality.erasure_requests` only, with no right on any other table in `quality`.

#### Scenario: Corvus appends an erasure request

- **GIVEN** a token whose role maps to `corvus_ingest`
- **WHEN** the client appends a pending row to `quality.erasure_requests`
- **THEN** the response is HTTP 200

#### Scenario: Corvus cannot write other quality tables

- **GIVEN** a token whose role maps to `corvus_ingest`
- **WHEN** the client appends a row to `quality.check_results`
- **THEN** the response is HTTP 403

### Requirement: Maintenance and SQL grants cover the layer namespaces

THE principal roles `lake_maintenance` and `lake_sql` SHALL reach the new namespaces through their existing catalog-wide grants, and `lake_sql` SHALL NOT have any write right.

#### Scenario: SQL reads a layer table

- **GIVEN** a token whose role maps to `lake_sql`
- **WHEN** the client reads `features.session_stats`
- **THEN** the response is HTTP 200

#### Scenario: SQL cannot write a layer table

- **GIVEN** a token whose role maps to `lake_sql`
- **WHEN** the client appends a snapshot to `labels.sessions`
- **THEN** the response is HTTP 403

### Requirement: Entra ID roles map to principal roles

THE role mapper of Polaris SHALL map the Entra ID app roles `lake-pipeline` and `lake-erasure` on the API app "Polaris" to the principal roles `lake_pipeline` and `lake_erasure`, and SHALL ignore any other role that is not in its filter.

#### Scenario: Client with the pipeline role

- **GIVEN** a client app that has the app role `lake-pipeline` assigned
- **WHEN** it requests a token for the API app "Polaris"
- **THEN** the token's `roles` claim contains `lake-pipeline`
- **AND** Polaris maps the token to the principal role `lake_pipeline`

#### Scenario: Client without an assigned role

- **GIVEN** a client app with no app role assigned
- **WHEN** it calls the catalog with a token for the API app "Polaris"
- **THEN** Polaris grants it no principal role
- **AND** the response to a catalog read is HTTP 403

### Requirement: Client secrets are stored encrypted in namespace data

THE clients "Polaris Lake Pipeline" and "Polaris Lake Erasure" SHALL have their client secrets stored only as SOPS-encrypted Secrets in namespace `data`, and the repository SHALL NOT contain a plain secret value or an unencrypted key.

#### Scenario: Secret is encrypted

- **GIVEN** the code pull request is merged
- **WHEN** `.github/scripts/check-sops-encryption.py` runs
- **THEN** every new `*.sops.env` file carries every listed recipient

### Requirement: Airflow pool lake_spark limits Spark jobs to two slots

THE Airflow instance SHALL have a pool named `lake_spark` with 2 slots, created from a version-controlled pool file by an idempotent Job in namespace `data`.

#### Scenario: Pool exists after the Job

- **GIVEN** the pool Job has completed
- **WHEN** the pools of Airflow are listed
- **THEN** the list contains `lake_spark` with 2 slots

#### Scenario: Pool Job re-runs without change

- **GIVEN** the pool file has not changed
- **WHEN** the layer is reconciled again
- **THEN** the pool still has 2 slots and no other pool changes

### Requirement: Platform changes are additive

THE setup Job SHALL NOT revoke or change any existing principal, principal role, catalog role or grant, and SHALL NOT change the existing catalog `corvus` properties.

#### Scenario: Existing principals keep their access

- **GIVEN** the Job `polaris-setup-7` has completed
- **WHEN** Corvus ingest, Corvus ML, the maintenance client and the SQL client each request their existing operations
- **THEN** each request returns the same HTTP status as before the change
