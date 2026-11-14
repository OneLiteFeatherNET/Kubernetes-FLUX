## Why

The staging instance of Ingot still runs on the MariaDB Galera cluster in `reposilite-staging`. Ingot runs on PostgreSQL, so the new staging needs a PostgreSQL role and database first. None exists on `feather-core-cluster-pg` (CloudNativePG, namespace `cnpg-system`). This change adds them and nothing else, so the old staging keeps running unchanged while the new one is prepared.

## What Changes

- A managed role `ingot_staging` in the CloudNativePG cluster `feather-core-cluster-pg` (login, connection limit 10).
- A database `ingot-staging` (PostgreSQL name `ingot_staging`) owned by `ingot_staging`, with `databaseReclaimPolicy: retain`.
- The role password as a new SOPS-encrypted basic-auth Secret `role-ingot-staging`. The password is freshly generated, so no MariaDB credential is reused.
- Kustomization entries for the role secret and the database object.

**Commit type and scope:** the code ships as `feat(databases)`. It adds a database capability for the new staging; it is one type and one change.

## Out of scope

- The Ingot workload, its HelmRelease, ingress and cutover (later phases).
- Copying data from MariaDB.
- Rook-Ceph buckets and users: the bucket names are kept, so no new ObjectBucketClaim is needed.
- Removing any MariaDB object.
- Application connection setup: Ingot must use the session pooler `feather-core-cluster-pg-pooler-rw`, never `-rw-tx`, because it relies on session-level advisory locks. Nothing consumes the new role yet.

## Impact

- Layer `foundation-databases` (depends on `foundation-databases-operators`, `foundation-storage`, `foundation-networking`). No new layer, no rename.
- Runtime: a new role and an empty database. No running workload changes. The role's connection limit of 10 keeps the budget of `max_connections: 200` safe.
