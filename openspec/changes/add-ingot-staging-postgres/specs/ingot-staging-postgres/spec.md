## ADDED Requirements

### Requirement: Dedicated PostgreSQL role for Ingot staging

THE CloudNativePG cluster `feather-core-cluster-pg` SHALL declare a managed role `ingot_staging` with login enabled and a connection limit of 10.

#### Scenario: Role exists with its limit

- **GIVEN** the code PR is applied and the cluster reconciles
- **WHEN** the role `ingot_staging` is inspected
- **THEN** it can log in and its connection limit is 10

### Requirement: Retained database owned by the Ingot role

THE database object `ingot-staging` SHALL create the PostgreSQL database `ingot_staging` owned by the role `ingot_staging`, with `databaseReclaimPolicy: retain`.

#### Scenario: Deleting the object keeps the data

- **GIVEN** the database `ingot_staging` contains data
- **WHEN** the CR `ingot-staging` is deleted
- **THEN** the PostgreSQL database and its data remain

### Requirement: Encrypted, newly generated role password

THE role password SHALL exist only in the SOPS-encrypted Secret `role-ingot-staging` and SHALL NOT be a credential from the MariaDB staging.

#### Scenario: Repository holds no plaintext

- **GIVEN** the code PR is merged
- **WHEN** the repository is searched for the password
- **THEN** the only occurrence is inside a file whose data values are SOPS ciphertext

### Requirement: No change to existing databases

THE code PR SHALL NOT alter any existing role, database, Secret or workload.

#### Scenario: Other objects are untouched

- **GIVEN** the code PR is applied
- **WHEN** the existing managed roles and databases are compared with their state before
- **THEN** no existing object has changed
