## ADDED Requirements

### Requirement: Trino is reachable only from Superset

THE Trino Service SHALL be of type `ClusterIP`, THE cluster SHALL NOT expose Trino through any Ingress, HTTPRoute or LoadBalancer, and THE Trino NetworkPolicy SHALL admit connections only from Superset pods and from Trino's own coordinator and worker pods.

#### Scenario: No external route to Trino

- **GIVEN** the code pull request is applied and the layer `foundation-data` is Ready
- **WHEN** the Services and routes that point at Trino are listed
- **THEN** only a `ClusterIP` Service exists
- **AND** no Ingress or HTTPRoute references it

#### Scenario: Other pods cannot query Trino

- **GIVEN** a pod in namespace `data` that is not Superset, Trino or its own worker
- **WHEN** the pod opens a connection to the Trino Service on port 8080
- **THEN** the connection is refused by the NetworkPolicy

### Requirement: Superset is reachable only by assigned staff

THE Superset Entra enterprise app SHALL require user assignment (`appRoleAssignmentRequired`), and THE Superset SHALL serve login only through Entra ID (`AUTH_OAUTH`, provider `azure`) and SHALL NOT offer local login.

#### Scenario: Unassigned user is refused

- **GIVEN** a user with a valid Entra account who is not assigned to the Superset enterprise app
- **WHEN** the user signs in to `superset.apps.onelite.feather`
- **THEN** Entra ID refuses the sign-in
- **AND** Superset creates no user for that account

#### Scenario: Assigned user without the admin role gets Gamma without data

- **GIVEN** a user assigned to the Superset enterprise app without the app role `superset-admin`
- **WHEN** the user signs in
- **THEN** Superset grants the role `Gamma`
- **AND** the user cannot query the database connection `corvus` until a permission on it is granted to `Gamma`

#### Scenario: Admin role maps to Admin

- **GIVEN** a user assigned the app role `superset-admin`
- **WHEN** the user signs in
- **THEN** Superset grants the role `Admin`

### Requirement: Staff can only read catalog corvus

THE Polaris principal role `lake_sql` SHALL hold read grants only on catalog `corvus` (`TABLE_LIST`, `NAMESPACE_LIST`, `TABLE_READ_PROPERTIES`, `TABLE_READ_DATA`), THE Trino access control SHALL allow the catalog `corvus` only in read-only mode, and THE RGW user `trino-lake` SHALL be allowed only to read objects of the bucket `polaris-corvus`.

#### Scenario: Read query succeeds

- **GIVEN** a query submitted through Superset against `corvus.capture` by a user with the role `Admin`
- **WHEN** the query selects from an existing table
- **THEN** the result is returned

#### Scenario: Write statement is refused by Trino

- **GIVEN** a query submitted through Superset
- **WHEN** the query inserts a row into `corvus.capture.raw`
- **THEN** Trino refuses the statement before it reaches the connector

#### Scenario: Commit with the lake-sql token is refused by Polaris

- **GIVEN** a token of client "Polaris Trino" with the app role `lake-sql`
- **WHEN** the client commits an append to a table in `corvus.capture`
- **THEN** the response is HTTP 403

#### Scenario: Object write with the Trino S3 keys is refused by RGW

- **GIVEN** the keys of the RGW user `trino-lake`
- **WHEN** a client uploads an object to the bucket `polaris-corvus` (PutObject)
- **THEN** RGW responds with AccessDenied (HTTP 403)

#### Scenario: Object read with the Trino S3 keys succeeds

- **GIVEN** the keys of the RGW user `trino-lake` and an existing object of a table in `polaris-corvus`
- **WHEN** a client reads the object (GetObject) and lists the bucket (ListBucket)
- **THEN** both requests succeed

### Requirement: Trino authenticates to Polaris with its own Entra client

THE Trino SHALL obtain its catalog token from Entra ID with the OAuth2 client credentials flow of client "Polaris Trino" (client id `8832de30-7197-40ed-a2bc-68c88bc770cb`) and scope `api://9e2548f4-5e10-440d-9444-cad813a20c87/.default`, and THE Trino SHALL NOT request vended credentials from Polaris.

#### Scenario: Token carries the lake-sql role

- **GIVEN** the Trino pod is running
- **WHEN** Trino connects to the Polaris catalog `corvus`
- **THEN** the Entra token carries the app role `lake-sql`
- **AND** Polaris maps the token to the principal role `lake_sql`

#### Scenario: No credentials are vended

- **GIVEN** the Iceberg catalog `corvus` in Trino is configured
- **WHEN** Trino loads a table from Polaris
- **THEN** no `X-Iceberg-Access-Delegation` header is sent
- **AND** the S3 access uses the keys of the RGW user `trino-lake`

### Requirement: Polaris role mapping admits lake-sql only from its app role

WHEN a token carries the app role `lake-sql`, THE Polaris SHALL map it to the principal role `PRINCIPAL_ROLE:lake_sql`, and THE mapping of the roles `corvus-ingest`, `corvus-ml` and `lake-maintenance` SHALL remain unchanged.

#### Scenario: Existing principal roles are unchanged

- **GIVEN** the code pull request is applied
- **WHEN** the OIDC role mapping of Polaris is compared with its state before
- **THEN** the mappings of `corvus-ingest`, `corvus-ml` and `lake-maintenance` are identical
- **AND** exactly one mapping for `lake-sql` is added

### Requirement: Superset keeps its metadata in CloudNativePG

THE Superset SHALL keep its metadata in the CloudNativePG database `superset`, owned by the managed role `superset`, and THE Superset SHALL NOT use the shared Dragonfly or any other shared cache.

#### Scenario: Metadata database is on CNPG

- **GIVEN** the Superset release is running
- **WHEN** its database connection is read
- **THEN** it points at `feather-core-cluster-pg` and database `superset`
- **AND** the bundled PostgreSQL and Redis subcharts are disabled

### Requirement: Trino and Superset never see the packet encryption keys

THE Trino and Superset pods SHALL NOT mount any Secret or ConfigMap that holds the per-player key material of the Corvus capture, so that queries against the raw payloads return only the encrypted bytes.

#### Scenario: Key material is not mounted

- **GIVEN** the Trino and Superset releases are applied
- **WHEN** the mounted Secrets and ConfigMaps of their pods are listed
- **THEN** none of them is the Corvus key material
- **AND** the only S3 Secret mounted by Trino is the Secret of the RGW user `trino-lake`

### Requirement: Every container declares memory requests and limits

EVERY container of Trino and Superset, including init containers and Jobs, SHALL declare a memory request and a memory limit, so that the Kyverno policy `require-memory-resources` reports no violation.

#### Scenario: No violation in the policy reports

- **GIVEN** the Trino and Superset pods are running
- **WHEN** the PolicyReports of namespace `data` are read
- **THEN** no result of `require-memory-resources` names a container of Trino or Superset

### Requirement: Superset secret key comes from SOPS

THE Superset `SECRET_KEY` SHALL be provided from a SOPS-encrypted Secret and SHALL NOT appear in plain text in any manifest or Helm value.

#### Scenario: Secret key is encrypted

- **GIVEN** the code pull request is merged
- **WHEN** the files of the Superset overlay are scanned
- **THEN** the key appears only inside SOPS-encrypted files

### Requirement: Superset has an internal route only

THE Superset SHALL be exposed through the internal Gateway `eg` as `superset.apps.onelite.feather` with an HTTPRoute, and THE cluster SHALL NOT expose Superset through any public route in this change.

#### Scenario: Internal route exists

- **GIVEN** the code pull request is applied
- **WHEN** the HTTPRoutes that reference the Superset Service are listed
- **THEN** exactly one HTTPRoute exists, for the hostname `superset.apps.onelite.feather`

### Requirement: Layers reconcile with declared dependencies

THE layer `foundation-data` SHALL depend on `foundation-networking` in addition to its existing dependencies, and SHALL reconcile to Ready at the same git revision as its dependencies.

#### Scenario: Layer waits for the Gateway

- **GIVEN** the code pull request is merged to `main`
- **WHEN** Flux reconciles the layers
- **THEN** `foundation-data` depends on `foundation-data-operators`, `foundation-databases`, `foundation-storage` and `foundation-networking`
- **AND** `foundation-data` reports Ready
