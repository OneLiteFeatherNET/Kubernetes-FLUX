## 1. Inventory

- [ ] 1.1 List every Trino consumer in the repository (HelmRelease, secrets, NetworkPolicy, Superset datasource, Polaris principal, layer entry, sources) and in Entra ID and Polaris, and record the list in the code PR. Verify: the list matches the design's inventory, and `grep -ril trino` (excluding `*.sops.*`) finds no other consumer.
- [ ] 1.2 Inventory every dataset and metric SQL in `corvus-bundle/datasets__*.yaml` and `charts__*.yaml` for Trino-only syntax; record each dataset as "unchanged" or "to change". Verify: the list covers all seven `datasets__*` files and every metric expression.
- [ ] 1.3 Check the Superset chart of the bundle for the `corvus` database UUID references, so the DuckDB connection keeps the UUID. Verify: `grep -r 2601a04b` shows only the database files and the dataset `database_uuid` fields.
- [ ] 1.4 Measure the size of bucket `polaris-corvus` with `radosgw-admin bucket stats --bucket=polaris-corvus` in the Rook toolbox (or, if the toolbox command fails, through the RGW admin API with the operator's credentials). Verify: object count and total bytes are recorded in the code PR.

## 2. Prerequisites

- [ ] 2.1 Create the Entra client "Polaris Superset" with app role `lake-sql` assigned, and add its client id to `PRINCIPALS` in `setup-catalog.py`; rename the setup Job (`polaris-setup-6` to `polaris-setup-7`). Check how the operator sets the gunicorn worker count. Verify: the setup Job completes and `GET` of the principal in Polaris returns `lake_sql` as its role.
- [ ] 2.2 Spike in a scratch pod (not in the manifests): attach catalog `corvus` with the iceberg extension, `ACCESS_DELEGATION_MODE 'none'`, the OAuth2 secret and the S3 secret; read `capture.events`; confirm the facts marked unverified in the design (token endpoint and scope, ENDPOINT form, path style on port 80, `avro` need, `to_hex` on BLOB, `approx_percentile`, `extension_directories` layout, offline `LOAD`). Verify: a written note in the code PR lists each fact as confirmed or changed, with the DuckDB version used.
- [ ] 2.3 Choose the exact DuckDB, `duckdb-engine` and Superset image versions; write the Dockerfile and the CI job that builds and pushes it to Harbor (location decided here). Verify: the image starts with `autoinstall_known_extensions` false, loads `iceberg` and `httpfs` from the image, and opens a catalog connection in CI.
- [ ] 2.4 Decide how the DuckDB session gets the RGW keys: reference the existing Secret `trino-lake-s3` in namespace `data` for step 1 (design, decision 2), or add SOPS values to `superset-secrets`. Verify: the overlay still builds with `kubectl kustomize`, and the secret values are not printed in CI logs or PR text.

## 3. Manifests

- [ ] 3.1 Superset: point `spec.image` to the new image and keep the Trino driver in the init container until task 5.2. Add `DB_CONNECTION_MUTATOR` in the `config:` block to pass `memory_limit`, `threads` and `temp_directory` through `connect_args`. The mutator cannot run SQL, so the per-connection `ATTACH` and secrets use the hook chosen in task 2.2. Verify: `kubectl kustomize foundation/data/clusters/feather-core/superset` renders the CR with the new image and the config block.
- [ ] 3.2 Superset resources: request 2Gi and limit 6Gi on the container; add the `emptyDir` spill volume with a size limit of 10Gi, mounted at the temp path. Verify: the rendered CR shows both values and the mount.
- [ ] 3.3 Superset NetworkPolicy: add egress to `polaris.data` on TCP 8181 and to `rook-ceph-fr01` RGW on TCP 80. Keep the existing Trino egress until task 5. Verify: `kubectl kustomize` output of the policy contains both new rules; the policy is reviewed against the spec scenario.
- [ ] 3.4 Datasource: in `databases__corvus.yaml` and `import_datasources.yaml`, change the URI of database `corvus` (same UUID) to DuckDB; add `corvus-trino` with a new UUID and the old Trino URI for comparison. Verify: the rendered ConfigMap contains both databases with distinct UUIDs.
- [ ] 3.5 Dataset SQL: change only the datasets marked "to change" in task 1.2. Verify: each changed dataset returns the same row count as the Trino version in the comparison run of task 4.2.
- [ ] 3.6 Run `./.github/scripts/validate.sh` and `kubectl kustomize` on every changed overlay. Verify: the script exits 0.

## 4. Verification

- [ ] 4.1 Load every dashboard of the bundle on DuckDB; record the load time per chart. Verify: all charts render within 30 seconds at the 95th percentile.
- [ ] 4.2 Compare each dataset's row count and one aggregate (for example `total_packets`) between `corvus` and `corvus-trino`. Verify: counts match for the same time window, or the difference is explained by a snapshot boundary.
- [ ] 4.3 Watch the Superset container's memory during 4.1 with two parallel users. Verify: no `OOMKilled` reason in the pod status; peak working set below 5Gi.
- [ ] 4.4 Confirm that a write statement through the DuckDB session fails and that the Polaris principal has only the `sql` grants (spec scenarios). Verify: the failing statement's error is recorded in the PR.
- [ ] 4.5 Confirm Trino pods stay unchanged during the verification (no edit to their manifests). Verify: their HelmRelease revision is the same before and after 4.1 to 4.4.

## 5. Removal of Trino (follow-up PR)

- [ ] 5.1 Remove the `trino` HelmRelease, the overlay `foundation/data/clusters/feather-core/trino/`, the base `foundation/data/base/trino/`, the `HelmRepository` `trino` source and its entry in `foundation/sources/clusters/feather-core/sources/kustomization.yaml`, and the layer entry in `foundation/layers/feather-core/data/kustomization.yaml`. Verify: `./.github/scripts/validate.sh` exits 0 and `grep -ril trino` finds no manifest except this change.
- [ ] 5.2 Remove the `corvus-trino` database from the bundle, the Trino egress from the Superset NetworkPolicy, and the `trino==` package from the init container. Verify: the rendered Superset ConfigMap has one database.
- [ ] 5.3 Move the RGW keys from `trino-lake-s3` into a Superset-owned Secret, then remove `trino-lake-s3` and `trino-oauth`. Verify: the Superset pods start and 4.1 passes again.
- [ ] 5.4 After Flux has reconciled, delete the Polaris principal "Polaris Trino" by hand through the management API, and remove its entry from `PRINCIPALS` and the Entra client. Verify: `GET` of the principal returns 404.
- [ ] 5.5 Decide the title: `feat(data): remove trino after superset moved to duckdb`, with `!` and a `BREAKING CHANGE:` footer only if 1.1 found a consumer outside Superset. Verify: the PR title passes the commitlint rules (lower case, header at most 100 characters).

## 6. Documentation

- [ ] 6.1 Write the runbook in Outline (collection Infrastruktur, under the Kubernetes-FLUX GitOps page): how to query the lake from Superset, the DuckDB settings, the image build, and the rollback to Trino before step 5. Verify: the page exists and links to this change.
- [ ] 6.2 Record the verified and unverified DuckDB facts from the spike (task 2.2) in the same Outline page. Verify: each fact has its source URL and status.

## 7. Archive (optional, maintainer decision)

- [ ] 7.1 If the maintainer asks, archive `replace-trino-with-duckdb` and `add-lake-sql` in a follow-up PR `docs(openspec): archive replace-trino-with-duckdb`. Verify: only files under `openspec/` change.

## 8. Code pull request

- [ ] 8.1 Open the code pull request under the title `feat(data): query the lake from superset with duckdb instead of trino`; no files under `openspec/` in it. Verify: `gh pr view` shows the title and no `openspec/` path in `gh pr diff --name-only`.
