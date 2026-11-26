## 1. Preflight

- [ ] 1.1 Re-measure the backends per user and path with `select usename, client_addr, count(*) from pg_stat_activity where backend_type='client backend' group by 1,2`; map client_addr to the pooler pod IPs
- [ ] 1.2 Confirm in Grafana's migrator and config that `GF_DATABASE_MAX_OPEN_CONN` and `GF_DATABASE_MAX_IDLE_CONN` are honoured by the deployed version (the release pins `grafana/grafana` 13.3.1)
- [ ] 1.3 Confirm in the Penpot and Vikunja releases that no startup parameter beyond the standard driver set is sent

## 2. Wave 1 implementation

- [ ] 2.1 `services/observability/clusters/feather-core/grafana/release.yaml`: add `GF_DATABASE_MAX_OPEN_CONN: "5"` and `GF_DATABASE_MAX_IDLE_CONN: "2"` to the environment; keep the session pooler host
- [ ] 2.2 `services/development/clusters/feather-core/penpot/release.yaml`: `postgresql.host` to `feather-core-cluster-pg-pooler-rw-tx.cnpg-system.svc.cluster.local`; update the one-line comment (the `-rw` note is obsolete)
- [ ] 2.3 `services/collaboration/clusters/feather-core/vikunja/release.yaml`: `database.host` to `feather-core-cluster-pg-pooler-rw-tx.cnpg-system.svc.cluster.local`; update the one-line comment
- [ ] 2.4 Validate with `kubectl kustomize` on the three overlay paths

## 3. Wave 1 validation and pull request

- [ ] 3.1 Run `./.github/scripts/validate.sh`
- [ ] 3.2 Open the code pull request under the title `fix(databases): route penpot and vikunja through the transaction pooler`; no files under `openspec/` in it
- [ ] 3.3 After merge, confirm `services-observability`, `services-development` and `services-collaboration` are Ready at the merge revision
- [ ] 3.4 Confirm Penpot and Vikunja pods are Ready and their UI or health endpoints work
- [ ] 3.5 Confirm the transaction pooler logs show no `unsupported startup parameter`, protocol errors or `closing because` lines from Penpot or Vikunja, for 24 hours

## 4. Wave 2 implementation

- [ ] 4.1 `services/collaboration/clusters/feather-core/metabase/metabase-app-db.sops.env`: change the `host` value to the transaction pooler, edited in place with `sops`, without printing plaintext
- [ ] 4.2 `services/collaboration/clusters/feather-core/outline/outline.sops.env`: change the database host to the transaction pooler, edited in place with `sops`; keep `DATABASE_CONNECTION_POOL_MAX` at 3 per pod
- [ ] 4.3 Confirm the Airflow chart 1.22.0 mechanism for a separate migration connection (`migrateDatabaseJob`); if absent, apply the bounded-pool fallback (`AIRFLOW__DATABASE__SQL_ALCHEMY_POOL_SIZE` and `MAX_OVERFLOW`) in `foundation/data/clusters/feather-core/airflow/release.yaml` and leave the connection on `-rw`
- [ ] 4.4 If the mechanism exists: point the runtime connection in `foundation/data/clusters/feather-core/database-secrets/airflow-db.sops.env` at the transaction pooler and set the migrate job's connection to `-rw`; keep the role cap of 20 in `cluster.yaml`
- [ ] 4.5 Run `python3 .github/scripts/check-sops-encryption.py` to confirm the encrypted files still carry every recipient

## 5. Wave 2 validation and pull request

- [ ] 5.1 Run `./.github/scripts/validate.sh`
- [ ] 5.2 Open the code pull request under the title `fix(databases): route metabase, outline and airflow through the transaction pooler`; no files under `openspec/` in it
- [ ] 5.3 After merge, restart the Metabase, Outline and Airflow Deployments (`kubectl rollout restart`), because secret names are stable and a content change does not roll the pods
- [ ] 5.4 Confirm each application's pods are Ready and its UI or health endpoint works
- [ ] 5.5 Confirm the transaction pooler logs are clean for 24 hours (same search as 3.5, for Metabase, Outline and Airflow)

## 6. Final measurement

- [ ] 6.1 Re-measure the backends with the query in 1.1 during normal load; success is at most 125 client backends
- [ ] 6.2 Confirm the "CNPG Postgres connection saturation high" alert is not firing
- [ ] 6.3 Record the result in the Outline runbook for the Postgres connection budget

## 7. Code pull requests

- [ ] 7.1 Open the wave 1 and wave 2 code pull requests under the titles above (tasks 3.2 and 5.2); no files under `openspec/` in either
