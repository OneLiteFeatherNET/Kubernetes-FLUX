## ADDED Requirements

### Requirement: Safe applications use the transaction pooler

WHERE an application uses no session-level advisory locks, no `LISTEN`/`NOTIFY` and sends no startup parameters that PgBouncer rejects, the application SHALL connect through `feather-core-cluster-pg-pooler-rw-tx`.

#### Scenario: Penpot connects through the transaction pooler

- **GIVEN** Penpot's `postgresql.host` is set to `feather-core-cluster-pg-pooler-rw-tx.cnpg-system.svc.cluster.local`
- **WHEN** the Penpot backend opens its pool
- **THEN** its client connections appear as transaction-pooled clients of the pooler, not as direct `-rw` backends

#### Scenario: Application with a rejected startup parameter stays off the pooler

- **GIVEN** an application sends `statement_timeout` as a startup parameter
- **WHEN** its connection is configured
- **THEN** it SHALL connect through `-rw` or the session pooler and not through the transaction pooler

### Requirement: Migrations keep session locks on the direct service

WHERE an application's migrations take session-level advisory locks, the migration SHALL connect to the `-rw` service and not through a transaction pooler.

#### Scenario: Airflow migration uses the direct service

- **GIVEN** the Airflow runtime connection is routed through `feather-core-cluster-pg-pooler-rw-tx`
- **WHEN** the migrate job runs `airflow db migrate`
- **THEN** the migrate job connects to `feather-core-cluster-pg-rw` and holds the global `pg_advisory_lock` on that connection

#### Scenario: Airflow without a separate migration connection keeps bounded pools

- **GIVEN** the Airflow chart has no mechanism for a separate migration connection
- **WHEN** this change is applied
- **THEN** Airflow stays on `-rw` and sets `AIRFLOW__DATABASE__SQL_ALCHEMY_POOL_SIZE` and `AIRFLOW__DATABASE__SQL_ALCHEMY_MAX_OVERFLOW` instead

### Requirement: Grafana bounds its pool

Grafana SHALL hold at most 5 open database connections per replica.

#### Scenario: Grafana pool cap is set

- **GIVEN** Grafana runs with two replicas
- **WHEN** the release is reconciled
- **THEN** the environment sets `GF_DATABASE_MAX_OPEN_CONN` to `5` and `GF_DATABASE_MAX_IDLE_CONN` to `2`
- **AND** the pooler backends used by Grafana do not exceed 10

### Requirement: Transaction pooler stays free of rejected parameters

WHEN a wave has been live for 24 hours, the transaction pooler logs SHALL contain no rejected startup parameters and no protocol errors caused by the wave's applications.

#### Scenario: Clean logs after wave one

- **GIVEN** wave one (Penpot, Vikunja, Grafana cap) has been live for 24 hours
- **WHEN** the logs of the `pooler-rw-tx` pods are searched for `unsupported startup parameter`, `error` and `closing because`
- **THEN** no line is attributable to Penpot or Vikunja

### Requirement: Shared cluster stays below the alert threshold

WHEN both waves are live, the shared cluster SHALL use at most 125 client backends under normal load.

#### Scenario: Backend count after both waves

- **GIVEN** both waves are live
- **WHEN** `select usename, client_addr, count(*) from pg_stat_activity where backend_type='client backend' group by 1,2` is run during normal load
- **THEN** the total number of client backends is at most 125
- **AND** the "CNPG Postgres connection saturation high" alert is not firing

### Requirement: Secret-sourced hosts take effect on restart

WHEN a connection secret changes its host, the consuming application SHALL be restarted so that it uses the new host.

#### Scenario: Metabase after a host change

- **GIVEN** the `metabase-app-db` secret host is changed to the transaction pooler
- **WHEN** the change is reconciled
- **THEN** the Metabase Deployment is restarted and its pods connect through the transaction pooler
