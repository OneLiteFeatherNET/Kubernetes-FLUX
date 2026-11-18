## Context

The primary purpose of the lake is Minecraft network packets captured by Corvus. Corvus writes them as loose Parquet files to Ceph RGW and keeps its catalog in its own Postgres database. Compaction and retention are its own code. Blackhole does not need the packet data; it will later deliver labels (punishments and reports) for it. Both products already use Ceph RGW (`feather-s3`, path-style over the internal endpoint `rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc`) and CloudNativePG (`feather-core-cluster-pg`, `max_connections: 200`, close to saturation). The lake uses neither Kafka nor Strimzi for its data path (decision 6).

Expected volume is small. The pilot produces about 26 packets per second per player; 100 players give about 2,600 packets per second. That is well within what the direct ingest writer and a nightly batch maintenance run handle.

Constraint: the change is additive. It must not alter an existing role, bucket, topic or workload, and must not block `foundation-storage` or `foundation-databases` through a failing install.

## Verified facts (live cluster and Entra ID, 2026-11-18)

- `feather-s3` (CephObjectStore) has no STS configured: neither `rgw_sts_key` nor `rgw_s3_auth_use_sts` is set in its spec. See decision 3.
- Polaris Helm chart `polaris` 1.9.0 (application 1.9.0), repository `https://downloads.apache.org/polaris/helm-chart`.
- Spark Kubernetes Operator Helm chart `spark-kubernetes-operator` 1.9.0 (application 1.1.0), repository `https://apache.github.io/spark-kubernetes-operator`.
- Airflow Helm chart `airflow` 1.22.0 (Airflow 3.2.2), repository `https://airflow.apache.org`.
- Strimzi cluster operator 1.3.1; Kafka 4.2.0 on `feather-kafka`. Kafka is not used by this change.
- Entra ID, tenant of the OneLiteFeather apps:
  - API app "Polaris", appId `9e2548f4-5e10-440d-9444-cad813a20c87`, identifier URI `api://9e2548f4-5e10-440d-9444-cad813a20c87`, access tokens v2, with app roles `corvus-ingest` (write), `corvus-ml` (read) and `lake-maintenance` (maintenance);
  - client app "Polaris Corvus Ingest", appId `41380977-879c-4c49-8e0d-fb1047f1bb7a`, assigned `corvus-ingest`;
  - client app "Polaris Corvus ML", appId `cea8487b-adef-45ef-917e-e4506cc38922`, assigned `corvus-ml`;
  - client app "Polaris Lake Maintenance", appId `1f752161-5002-468d-a39b-12c46f6b874d`, assigned `lake-maintenance`.
  - Entra app roles use hyphens. Polaris principal roles use underscores: `corvus-ingest` maps to `corvus_ingest`, `corvus-ml` to `corvus_ml`, `lake-maintenance` to `lake_maintenance`.
  - Client IDs are not secrets. Client secrets do not exist yet for any of the three client apps; they go into SOPS Secrets (task 5.1).

## Decision drivers

- One lake for all consumers, with each consumer isolated to its own catalog and bucket.
- Reuse what the cluster already runs (RGW, CNPG, Entra ID). No second storage system and no second messaging system for the lake.
- Open standards with a REST catalog, so that Spark and PyIceberg use one API.
- One orchestrator for lake batch work, so that multi-step jobs have dependencies, retries and backfill in one place.
- GDPR deletion must be physical within a known time: deletes are only physical after compaction, `expire_snapshots` and orphan cleanup.
- Repeatable images and versions. Nothing is fetched at runtime from a public repository without a pin.

## Options and decisions

### 1. Object storage: Ceph RGW

**Options**

1. Ceph RGW (existing `feather-s3`). Chosen.
2. Apache Ozone. Rejected: it is a second storage system with its own Ozone Manager, Storage Container Manager, Datanodes, S3 gateway and Recon, each with Raft quorums and several GB of RAM. Datanodes on Ceph RBD would replicate data 3 times in Ceph and 3 times in Ozone (9 copies). Every client speaks S3 anyway, so Ozone adds no capability.
3. MinIO. Rejected: a second S3 system for no gain over the RGW that already exists; its licensing and community direction add review cost.

**Consequences:** buckets are created as ObjectBucketClaims in `rook-fr01/buckets/` with `bucketOwner` per catalog. Iceberg and Spark use path-style access against the internal endpoint over HTTP, as Loki, Mimir and Tempo already do.

### 2. Catalog: Apache Polaris

**Options**

1. Apache Polaris (REST catalog, Apache top-level project). Chosen.
2. Iceberg JDBC catalog (stored in Postgres, no REST endpoint). Rejected: it offers no REST API for several engines to share, so each engine would need direct database access.
3. Lakekeeper. Rejected: it is a capable REST catalog but not an Apache project, which the platform does not want to depend on.
4. Keep Corvus' own Postgres catalog. Rejected: it keeps the catalog private to one product and is the reason a second lake would appear.

**Consequences:** Polaris persists to CNPG through `relational-jdbc`, so its state survives pod restarts and is backed up with the other CNPG data. Its HA setup (2 replicas, shared token-key Secret) is the chart's responsibility and needs verification during implementation.

### 3. Credential vending

**Decision:** STS is not configured on `feather-s3` (verified 2026-11-18, see Verified facts). Polaris catalogs use `stsUnavailable: true`. Each consuming service gets its own static S3 credentials, scoped to its own RGW user and bucket access. Polaris does not vend credentials.

**Options considered**

1. Enable STS on `feather-s3` and let Polaris vend table-scoped credentials. Rejected for this change: it changes a shared object store that other products use, which the additive constraint excludes.
2. `stsUnavailable: true` with per-consumer static keys. Chosen.

**Consequences:** isolation is per bucket and per service, not per table. Static keys live in SOPS-encrypted Secrets of the consuming services (their own changes) and must be rotated by hand. Enabling STS later is a separate change that would replace the static keys; it is not planned here.

### 4. Authentication: Entra ID OIDC

**Options**

1. Entra ID OIDC with client credentials per consuming service, principal and role mapping from token claims. Chosen. It matches Corvus, Quelea and Blackhole, which already use the same tenant.
2. Polaris-internal principals with their own credentials. Rejected: a second identity system that has to be managed by hand and kept in SOPS.

**Consequences:** the Entra ID registrations exist (see Verified facts). The client secrets still have to be created and stored as SOPS Secrets (task 5.1). The Spark maintenance job authenticates as client "Polaris Lake Maintenance" with the OAuth2 client credentials flow of the Iceberg REST catalog. Polaris' authentication is set to `external`, so Polaris accepts no internal passwords. The ingress is internal, so the token issuer and audience are the only trust boundary.

### 5. Spark image

**Options**

1. Official `apache/spark` image, pinned by digest, with the Iceberg Spark runtime and the Iceberg AWS bundle added via `spark.jars.packages` with exact versions. Chosen.
2. A custom Spark image built in CI with the jars baked in, pushed to Harbor. Kept as the fallback.

**Decision:** option 1. The image is pinned by digest in the `SparkApplication` template of the Airflow DAG. The jar coordinates are exact Maven Central versions, which are immutable. Option 2 is used only if the start-up download becomes a problem.

**Consequences:** each job start needs egress to Maven Central, so the job depends on Maven Central being reachable and adds start-up time. The exact coordinates are recorded in the code pull request.

### 6. No Kafka in the lake platform

**Options**

1. No Kafka in this platform. Chosen. The packet path is plugin, then NATS, then the Corvus ingest service, then Iceberg through the Polaris catalog. It needs no Kafka. The only other planned source is Blackhole labels, which Blackhole publishes via RabbitMQ at low volume; a scheduled batch pull by an Airflow DAG is enough (follow-up change).
2. Apache Iceberg Kafka Connect sink (`org.apache.iceberg:iceberg-kafka-connect`) on a Strimzi `KafkaConnect` cluster. Rejected: it adds a long-running Connect cluster, an in-cluster plugin build (Strimzi `spec.build`) that pushes to Harbor with a robot account, and a Kafka topic for a low-volume, batch-friendly source.

Option 2 is also rejected for the packet path, for three reasons:

- one CaptureBatch must become many rows, and the sink maps one record to one row, so the flattening would move into Connect transforms;
- per-player encryption of raw bytes would move into the plugin or into Connect transforms, which changes the trust boundary of the capture;
- every proxy would need a new plugin release to change the packet format or routing.

**Consequences:** no `KafkaConnect`, no `KafkaConnector`, no Harbor push account, and the layer `foundation-data` does not depend on `foundation-messaging`. If a high-volume streaming source appears later, the question is reopened in its own change.

### 7. Maintenance: Spark run by Airflow, no long-running Spark cluster

**Decision:** Spark runs only as a nightly batch per catalog. An Airflow DAG (decision 11) submits one `SparkApplication` per run. Per table, the application first runs `DELETE FROM ... WHERE day < cutoff` for day partitions older than the table's retention (a metadata-only delete, because the table is day-partitioned), then `rewrite_data_files`, then `expire_snapshots`, then `remove_orphan_files`. The order matters: snapshot expiry alone does not drop data, because the expired files stay referenced by older snapshots until the delete is committed and those snapshots are expired. The data is physically gone only after `remove_orphan_files` has run. The day partitioning itself is defined by the Corvus change.

**Options**

1. Nightly `SparkApplication` submitted by an Airflow DAG. Chosen. Spark starts, works and stops.
2. `SparkCluster` running all the time. Rejected: idle workers would cost memory on a cluster that is already close to its limits, and the volume does not need them.
3. Apache Flink for streaming ingest. Deferred. The packet volume is written directly by the Corvus ingest (decision 9); Flink would add a second streaming system without a current requirement.

**Consequences:** GDPR deletes become physical at the next nightly run. The retention per table defines the window; it is a parameter of the DAG.

### 8. Table format version

**Decision:** format-version 2 is the default for all tables. Version 3 is used only after PyIceberg support for it is verified (not yet checked).

**Consequences:** version 3 features (for example deletion vectors) are not used until then. Migration to v3 is a table property change in a later change.

### 9. Packet path: direct Iceberg appends from the Corvus ingest

**Options**

1. The existing Corvus ingest service keeps batching and per-player encryption and writes Iceberg appends through the Polaris REST catalog. Chosen. The plugin and NATS stay unchanged; only the writer changes (Corvus repository, later change).
2. Packet path via the Iceberg Kafka Connect sink. Rejected, see decision 6.

**Consequences:** the lake platform in this change has to serve the ingest service directly: catalog `corvus`, namespace `capture`, principals and maintenance (decision 10). The packet path uses no Kafka.

### 10. Namespace, retention and access for catalog `corvus`

**Decision:**

- Catalog `corvus` with one namespace `capture`. This change creates the catalog and the namespace only. The Corvus change later creates the tables `capture.events` (long retention) and `capture.raw` (short retention, default 21 days).
- Retention is a per-table parameter of the maintenance DAG (table name to retention). It is not a property of the namespace.
- The table `capture.raw` is day-partitioned, so that an expired day is removed by a metadata-only delete of its partition, not by row-level deletes. The partitioning itself is defined by the Corvus change.
- Three Polaris principal roles on the catalog, mapped from the Entra ID app roles (decision 4): `corvus_ingest` with write access (Corvus ingest service), `corvus_ml` with read access (Corvus ML and training jobs), and `lake_maintenance` with the operations maintenance needs (delete, rewrite, expire snapshots, remove orphan files) on catalog `corvus`, and no ingest access.

**Consequences:** deleted or expired packet data is physically gone only after the nightly run has deleted the expired partitions, run `expire_snapshots` and run `remove_orphan_files` (decision 7). The access split means the ML jobs cannot modify packet data.

### 11. Orchestration: Airflow vs. a Kubernetes CronJob

**Options**

1. Apache Airflow (official chart `airflow` 1.22.0, Airflow 3.2.2) with the KubernetesExecutor, metadata database on CNPG, DAGs delivered by git-sync. Chosen.
2. A Kubernetes `CronJob` per lake batch job, each creating a `SparkApplication`. Rejected: a CronJob has no dependencies between steps, no per-step retries and no backfill. Each new lake batch job would need its own CronJob and its own monitoring. The planned follow-ups (Blackhole label pull, training dataset build on a fixed snapshot, training and calibration) are multi-step pipelines.

**Decision drivers:** dependencies between steps, retries, backfill, and one place for all lake batch jobs.

**Decision:** option 1. The first DAG is the nightly maintenance for catalog `corvus` (decision 7). The API server and UI are internal only (`ClusterIP`).

**Consequences:**

- Airflow runs a long-running scheduler, API server, DAG processor and triggerer in namespace `data`. This costs memory on a cluster that is already close to its limits.
- Airflow has its own metadata database on CNPG, which counts against the connection budget (`max_connections: 200`).
- The control-plane API of Corvus still launches its training jobs. Until a later change moves them into Airflow, two schedulers exist side by side.
- DAG code runs with the RBAC of the Airflow ServiceAccount, so that ServiceAccount is limited to namespace `data`.

### 12. DAG repository: dedicated `lake-dags`

**Options**

1. A new dedicated public repository `OneLiteFeatherNET/lake-dags` (name tentative), following the OLF standards: Trunk-Based development, Conventional Commits, the central Renovate preset and Release-Please. Chosen. Airflow git-sync reads the `main` branch, subPath `dags`.
2. The Corvus repository. Rejected: it would tie lake maintenance and the later lake DAGs to one product's release cycle and make the lake depend on Corvus' repository.

**Decision:** option 1.

**Consequences:** the repository is public, so no secret, host name or credential may appear in DAG code; connection details come from Airflow connections and Kubernetes Secrets. git-sync needs no credential. A merge to `main` reaches Airflow at the next git-sync poll. Creating the repository is a maintainer task (task 7.1), and the Airflow part of the code pull request cannot sync before it exists.

## Open questions

1. **Polaris realm and root bootstrap.** The chart documentation was not verified for how the realm and the root principal are bootstrapped in this setup. Confirm before task 6.2.
2. **Corvus writer change.** Resolved for this change: the plugin and NATS stay unchanged, and the Corvus ingest service writes Iceberg directly (decision 9). The writer change itself is a follow-up in the Corvus repository.
