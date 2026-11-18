## Why

The primary purpose of the lake is the storage of Minecraft network packets captured by Corvus (ML anti-cheat). Today Corvus writes them as loose Parquet files to Ceph RGW, keeps its catalog in its own Postgres database, and runs its own compaction and retention code. Blackhole (ban system) will later deliver labels for the same data. This change builds one shared lake platform on Apache components that Corvus can write to and that other sources can use later: Apache Iceberg tables on the existing Ceph RGW, an Apache Polaris REST catalog, Apache Spark for table maintenance, and Apache Airflow to orchestrate all lake batch work.

This change builds the platform only. The packet path itself does not change: the plugin and NATS stay as they are, and the Corvus ingest service keeps its batching and per-player encryption. Only its writer switches from loose Parquet files to Iceberg appends through the Polaris catalog, and that code change lives in the Corvus repository. Until it lands, nothing writes to the lake. Kafka is not used by the lake platform (see design, decision 6).

## What Changes

- New foundation domain `data` with two new Flux layers. No existing layer is renamed or split:
  - `foundation-data-operators` (path `foundation/layers/feather-core/data-operators`, dependsOn `foundation-platform`): the Apache Spark Kubernetes Operator, CRDs `SparkApplication` and `SparkCluster`.
  - `foundation-data` (path `foundation/layers/feather-core/data`, dependsOn `foundation-data-operators`, `foundation-databases`, `foundation-storage`).
- Apache Polaris (graduated Apache top-level project, Helm chart `polaris` 1.9.0, application 1.9.0), in namespace `data`:
  - Persistence `relational-jdbc` on the existing CloudNativePG cluster `feather-core-cluster-pg`: a Database and a managed role `polaris`, credentials in a SOPS-encrypted Secret with keys `username`, `password` and `jdbcUrl`.
  - Authentication `external` against Entra ID OIDC, with principal and role mapping from token claims. The Entra ID app registrations already exist; their facts are recorded in the design.
  - One shared token-key Secret, so that two replicas issue and verify tokens consistently; 2 replicas and a PodDisruptionBudget.
  - Internal-only `ClusterIP` Service. No Ingress or public route in this change.
- Catalog `corvus`, backed by one Ceph RGW bucket, with one namespace `capture`. The catalog uses `stsUnavailable: true`, because STS is not configured on `feather-s3`; each consumer gets its own static S3 credentials for its bucket. This change creates only the catalog and the namespace. The Corvus change later creates the tables `capture.events` (long retention) and `capture.raw` (short retention, default 21 days, day-partitioned so that expired days can be dropped). Retention is a per-table parameter of the maintenance DAG.
  - The table schemas and partition specs are defined by the Corvus change, not here. This change provides only the catalog, the namespace, the retention parameters and the access.
- Three Polaris principal roles on catalog `corvus`, mapped from the Entra ID app roles of the Polaris API app:
  - `corvus_ingest` (Entra app role `corvus-ingest`) with write access, used by the Corvus ingest service;
  - `corvus_ml` (Entra app role `corvus-ml`) with read access, used by Corvus ML and training jobs;
  - `lake_maintenance` (Entra app role `lake-maintenance`) for the maintenance operations, used by the Spark maintenance job through its own client.
- Object storage: the Ceph RGW bucket through the existing pattern (`ObjectBucketClaim` with `bucketOwner`, as in `rook-fr01/buckets/`), accessed path-style over the internal RGW endpoint.
- Apache Spark: the operator, plus a nightly maintenance run for catalog `corvus`. Per table, it first deletes the day partitions older than the table's retention (`DELETE FROM ... WHERE day < cutoff`, a metadata-only delete because the table is day-partitioned), then runs `rewrite_data_files`, `expire_snapshots` and `remove_orphan_files`. Only then are the expired files physically gone. Spark runs as a `SparkApplication` per run, with no long-running Spark cluster.
- Apache Airflow (official chart `airflow` 1.22.0, Airflow 3.2.2, from `https://airflow.apache.org`), in namespace `data`, in layer `foundation-data`:
  - executor KubernetesExecutor, so no Celery workers and no Redis;
  - metadata database on the existing CloudNativePG cluster: a managed role `airflow` and a Database `airflow`, with a SOPS-encrypted Secret;
  - DAGs delivered by git-sync from the public repository `OneLiteFeatherNET/lake-dags` (branch `main`, subPath `dags`);
  - API server and UI internal only (`ClusterIP`, no Ingress in this change); login through Entra ID is a follow-up;
  - metrics scraped by alloy.
  - Airflow is the orchestrator of all lake batch work. The first DAG is the nightly maintenance for catalog `corvus`: it submits the `SparkApplication` that runs the steps above. It replaces a plain Kubernetes `CronJob`.
- Observability: `ServiceMonitor` and `PodMonitor` objects for Polaris, the Spark operator and Airflow, so that alloy picks them up without extra labels.

**Commit type and scope:** the code ships as `feat(data)`. The change adds one capability (the lake platform) under one domain, so it is one type.

## Out of scope

- The Corvus ingest writer change: switching its writer from loose Parquet files to Iceberg appends through the Polaris catalog. It lives in the Corvus repository and is a separate later change, a follow-up to this one.
- The tables `capture.events` and `capture.raw`, their schemas, partition specs and the flattening of CaptureBatch into rows. Defined by the Corvus change.
- Any change to the plugin, to NATS or to the per-player encryption of raw bytes. These stay as they are.
- Migrating Corvus' existing Postgres catalog and its compaction code onto the lake. Part of the Corvus writer change.
- Migrating Blackhole onto the lake and its labels pipeline. A separate later change. Its label pull will be an Airflow DAG (follow-up).
- Further Airflow DAGs: the Blackhole label pull into Iceberg, a training dataset build on a fixed snapshot, and training and calibration runs. Each is a follow-up change.
- Moving Corvus' training job launch from its control-plane API into Airflow. A later change; until then both run side by side.
- Login to Airflow through Entra ID. A follow-up change.
- Apache Kafka and the Iceberg Kafka Connect sink. The lake platform does not use Kafka (design, decision 6).
- Apache Flink (streaming). Deferred; the expected volume does not need it (see design).
- Apache Ozone as object storage (considered and rejected in design).
- Polaris and Airflow Ingress or any public endpoint, and external client access.
- Iceberg format version 3 (after PyIceberg support is verified).
- Consumer-side credentials in application Secrets, which belong to each consumer's change.

## Impact

- New capability `data-lake` (see `specs/data-lake/spec.md`).
- New domain `foundation/data/` (base and cluster overlay), new layer entries `foundation/layers/feather-core/data-operators/` and `foundation/layers/feather-core/data/`, and two layer CRs in `clusters/feather-core/`.
- Existing objects touched, all additive: the CloudNativePG cluster `feather-core-cluster-pg` gets two new Databases (`polaris`, `airflow`) and two managed roles, the same pattern as the other roles; `rook-fr01` gets a new bucket. The Strimzi `kafka` namespace is not touched. No existing role, bucket, topic, plugin or workload changes.
- Runtime impact: new Deployments (Polaris, Spark operator, Airflow API server, scheduler, DAG processor and triggerer), a nightly Spark job pod, and a new Ceph RGW bucket. The Polaris and Airflow roles get connection limits sized against `max_connections: 200` on the shared cluster (tasks 3.1 and 3.2).
- Layers `foundation-data-operators` and `foundation-data` must reconcile Ready at the same revision as their dependencies, as with every layer.
