## 1. Layers and domain skeleton

- [ ] 1.1 Add layer CRs `clusters/feather-core/foundation-data-operators.yaml` (dependsOn `foundation-platform`) and `clusters/feather-core/foundation-data.yaml` (dependsOn `foundation-data-operators`, `foundation-databases`, `foundation-storage`), with `deletionPolicy: Orphan` and SOPS decryption as the other layers
- [ ] 1.2 Add entry kustomizations `foundation/layers/feather-core/data-operators/` and `foundation/layers/feather-core/data/` and the domain skeleton `foundation/data/base/` and `foundation/data/clusters/feather-core/`
- [ ] 1.3 Add the namespace `data` with the Pod Security label used by the other workload namespaces

## 2. Spark operator

- [ ] 2.1 Pin the `spark-kubernetes-operator` chart to version 1.9.0 (application 1.1.0), as verified on the cluster 2026-11-18
- [ ] 2.2 Add the `HelmRepository` `https://apache.github.io/spark-kubernetes-operator` in `foundation/sources/clusters/feather-core/sources/`
- [ ] 2.3 Add the HelmRelease with chart version 1.9.0, `install` and `upgrade` remediation as in the other operator layers, and confirm the CRDs `SparkApplication` and `SparkCluster` are installed

## 3. CloudNativePG databases and roles

- [ ] 3.1 Add the managed role `polaris` (`login: true`, `connectionLimit` sized against `max_connections: 200`) and the Database `polaris` (owner `polaris`, `databaseReclaimPolicy: retain`) in `foundation/databases/clusters/feather-core/postgresql/`
- [ ] 3.2 Add the managed role `airflow` (`login: true`, `connectionLimit` sized against `max_connections: 200`, separate from `polaris`) and the Database `airflow` (owner `airflow`, `databaseReclaimPolicy: retain`) in the same place
- [ ] 3.3 Add the SOPS-encrypted Secrets, encrypted in place with `sops`: `role-polaris` (keys `username`, `password`), the JDBC connection Secret for Polaris (keys `username`, `password`, `jdbcUrl`), `role-airflow` (keys `username`, `password`) and the Airflow metadata connection Secret
- [ ] 3.4 Register all of them in the postgresql `kustomization.yaml`, and run `check-sops-encryption.py` on the new files

## 4. Ceph RGW buckets

- [ ] 4.1 Add the ObjectBucketClaim `polaris-corvus` (one bucket for the catalog; the namespace `capture` and its tables live in it) in namespace `data` (`foundation/data/clusters/feather-core/buckets/`), owned by a new RGW user `polaris` in `rook-fr01/users/`, following the product bucket pattern
- [ ] 4.2 Register the buckets in the buckets `kustomization.yaml`
- [ ] 4.3 Use `stsUnavailable: true` for catalog `corvus`: STS is not configured on `feather-s3` (verified 2026-11-18). Consumers get their own static S3 credentials for their bucket, provided by their own changes

## 5. Entra ID

The app registrations are done and are not tasks. Their facts are in the design under "Verified facts":

- API app "Polaris" with app roles `corvus-ingest`, `corvus-ml` and `lake-maintenance`;
- client apps "Polaris Corvus Ingest", "Polaris Corvus ML" and "Polaris Lake Maintenance", with the roles assigned.

- [ ] 5.1 Create a client secret for "Polaris Corvus Ingest", "Polaris Corvus ML" and "Polaris Lake Maintenance" and store each as a SOPS Secret, not in plain text. The client IDs are not secrets and may appear in manifests

## 6. Polaris

- [ ] 6.1 Pin the Polaris chart to version 1.9.0 (application 1.9.0), as verified on the cluster 2026-11-18
- [ ] 6.2 Confirm the realm and root bootstrap for this chart (open question 1) and write the bootstrap Job or values accordingly
- [ ] 6.3 Add the `HelmRepository` `https://downloads.apache.org/polaris/helm-chart` and the HelmRelease with chart version 1.9.0, `persistence: relational-jdbc`, `authentication: external`, the shared token-key Secret, 2 replicas and a PodDisruptionBudget
- [ ] 6.4 Add the internal `ClusterIP` Service only; no Ingress or HTTPRoute
- [ ] 6.5 Create the catalog `corvus` pointing at bucket `polaris-corvus` with `stsUnavailable: true` (task 4.3), the namespace `capture` (no tables; the tables come with the Corvus change), and the catalog principal roles `corvus_ingest` (write), `corvus_ml` (read) and `lake_maintenance` (maintenance operations), mapped from the Entra ID app roles; record the bootstrap steps in Outline

## 7. Airflow

- [ ] 7.1 Maintainer creates the public repository `OneLiteFeatherNET/lake-dags` (tentative name) following the OLF standards, with a `dags/` folder and `main` as default branch; the Airflow part of the code pull request cannot sync before it exists
- [ ] 7.2 Pin the `airflow` chart to version 1.22.0 (Airflow 3.2.2) and add the `HelmRepository` `https://airflow.apache.org` in `foundation/sources/clusters/feather-core/sources/`
- [ ] 7.3 Add the HelmRelease in namespace `data` with the KubernetesExecutor (no Celery, no Redis), the metadata database from the Secret in task 3.3, and a ServiceAccount whose RBAC is limited to namespace `data` (creating `SparkApplication` objects and their pods)
- [ ] 7.4 Configure git-sync from `OneLiteFeatherNET/lake-dags` (`main`, subPath `dags`, decision 12); the repository is public, so no credential is needed
- [ ] 7.5 Expose the API server and the UI as `ClusterIP` only; no Ingress or HTTPRoute. Login through Entra ID is a follow-up
- [ ] 7.6 Ship the maintenance DAG from task 8.2 as the first DAG

## 8. Spark maintenance DAG

- [ ] 8.1 Use the official `apache/spark` image pinned by digest, with the Iceberg Spark runtime and the Iceberg AWS bundle added via `spark.jars.packages` at exact Maven Central versions; record the exact coordinates in the code pull request. A custom image in Harbor is the fallback only if the start-up download becomes a problem
- [ ] 8.2 Add a nightly Airflow DAG for catalog `corvus` (in `lake-dags`) that submits a `SparkApplication`. The application runs, per table, the deletion of day partitions older than the table's retention, then `rewrite_data_files`, `expire_snapshots` and `remove_orphan_files`, in that order. The retention per table is a DAG parameter (`capture.events` long, `capture.raw` 21 days by default). The DAG authenticates as Entra client "Polaris Lake Maintenance" with principal role `lake_maintenance`. No Kubernetes `CronJob`
- [ ] 8.3 Verify on a smoke-test table in catalog `corvus` that an expired day is removed as a whole after one run, that expired snapshots are removed, and that orphan files are removed from the bucket

## 9. Monitors

- [ ] 9.1 Add a ServiceMonitor or PodMonitor for Polaris, for the Spark operator and for Airflow (the statsd or metrics endpoint of its components), without extra labels required by alloy
- [ ] 9.2 Verify in Prometheus that each component has at least one target in state `up`

## 10. Validation and documentation

- [ ] 10.1 Run `./.github/scripts/validate.sh` and `kubectl kustomize` on each new overlay
- [ ] 10.2 Run `.github/scripts/check-sops-encryption.py` on all new encrypted files
- [ ] 10.3 Write the platform runbook (catalog bootstrap, Airflow operation, maintenance schedule, GDPR deletion path) in Outline, collection Infrastruktur

## 11. Code pull request

- [ ] 11.1 Open the pull request `feat(data): add apache iceberg data lake`; no files under `openspec/` in it
