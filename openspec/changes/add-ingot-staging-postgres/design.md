## Context

`feather-core-cluster-pg` already manages roles and databases declaratively through `foundation/databases/clusters/feather-core/postgresql/cluster.yaml` and `database/` objects. Each role has a SOPS-encrypted basic-auth Secret. The cluster has `max_connections: 200` and runs near saturation. The staging data is small, and the copy from MariaDB is a later phase.

Constraint: this change is additive. It must not alter an existing role, database or workload.

## Decision drivers

- No credential carries over from the MariaDB era (rotation is part of the move).
- The new database must survive an accidental deletion of its CR.
- The connection budget must stay predictable.

## Options

1. Reuse the existing MariaDB password, changed only in the new Secret. Rejected: the old credential stays valid for the new system.
2. Freshly generated password in a new SOPS Secret, same layout as the other role secrets. Chosen.
3. Let the CNPG Database CR delete the database when the CR is removed. Rejected for staging data; `databaseReclaimPolicy: retain` keeps it.

## Decision

- Role `ingot_staging` with `login: true` and `connectionLimit: 10`, password from Secret `role-ingot-staging` (basic-auth, keys `username` and `password`).
- Database `ingot-staging` (`spec.name: ingot_staging`, owner `ingot_staging`, `databaseReclaimPolicy: retain`).
- The app uses the session pooler `feather-core-cluster-pg-pooler-rw`. The transaction pooler is not allowed for Ingot.

## Consequences

- Creating the role and database is reversible until data exists in them.
- The password is known only to the encrypted Secret and to the later app Secret, which is written in a later phase.
- Other staging objects stay as they are until the cutover.
