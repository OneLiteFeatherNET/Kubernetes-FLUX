## Why

Superset dashboards on the Corvus Iceberg tables time out, and the Trino pods are OOM-killed. The data set is small and the in-cluster RGW is fast, so a separate three-JVM query cluster costs more than it returns. This change replaces Trino with DuckDB embedded in the Superset process. Superset reads the Iceberg tables through the Polaris REST catalog and the Parquet files directly from RGW. The three JVMs go away, which removes about 12 GiB of memory limits. Superset's limit rises by about 4 GiB, so the net change is about 8 GiB less.

Current state, from the repository and the 2026-11-21 operations report:

- Coordinator limit 3Gi (heap 2G). Two workers, limit 4608Mi each (heap 3G). All three were OOM-killed at least once by 2026-11-21.
- Workers log announce timeouts to the coordinator under dashboard load.
- Four Trino tuning PRs (#685, #687, #688, #689) on 2026-11-21 did not stop the failures. (#686 is a dashboard PR, not a tuning PR.)

**Commit type and scope:** the code ships as `feat(data)`: `feat(data): query the lake from superset with duckdb instead of trino`. Removing Trino is a follow-up PR (task group 5), not part of the code PR.

## What Changes

- **Superset image:** a custom image built from `apache/superset:6.1.0` with pinned `duckdb`, `duckdb-engine`, and the DuckDB extensions `iceberg` and `httpfs` installed into the image, pushed to Harbor. Start-up installs from PyPI and from the extension repository stop (design, decision 3).
- **Superset connection:** the bundle database `corvus` (UUID `2601a04b-ec63-4dec-9763-91b41e285890`, kept so existing datasets still resolve) points to DuckDB. A second connection `corvus-trino` stays for side-by-side checks until removal.
- **Polaris:** one new Entra client, "Polaris Superset", mapped to the existing principal role `lake_sql` and its existing read-only catalog role `sql`. No new grants. The `PRINCIPALS` map in `setup-catalog.py` gets one entry, and the setup Job is renamed (Jobs are immutable).
- **Object access:** DuckDB uses the keys of the existing read-only RGW user `trino-lake`, which the bucket policy on `polaris-corvus` already limits to read. Polaris issues no vended credentials (`stsUnavailable: true`), so the attach uses `ACCESS_DELEGATION_MODE 'none'`.
- **Superset NetworkPolicy:** adds egress to Polaris (`polaris.data`, TCP 8181) and to RGW (`rook-ceph-rgw-feather-s3` in `rook-ceph-fr01`, TCP 80). The current policy allows neither.
- **Superset resources:** container memory request 1Gi to 2Gi, limit 2Gi to 6Gi. Each DuckDB connection gets `memory_limit` and `threads`. Spill files go to an `emptyDir` (design, decision 4).
- **Dataset and bundle files:** the database URI in `databases__corvus.yaml` and `import_datasources.yaml` changes. Dataset SQL changes only where the Trino dialect differs from DuckDB (inventory in task 1.2).
- **Removal (task group 5, own PR):** the `trino` HelmRelease, its `HelmRepository` source, the overlay secrets and NetworkPolicy, the Superset egress to Trino, the `trino` entry in the `foundation-data` layer, the `corvus-trino` connection, and the Polaris principal "Polaris Trino" (removed by hand, because the setup script only creates).

## Out of scope

- Corvus writer changes and the tables themselves (`add-data-lake`, Corvus repository).
- Per-user identity inside the query engine. All queries run as the one DuckDB connection identity, as with Trino today.
- Renaming the RGW user `trino-lake`. Renaming a Rook user changes its keys; a rename is a separate decision.
- Public access to Superset. The route stays internal.
- Rewriting dashboards. Only the connection and dialect-level SQL changes are in scope.

## Impact

- New capability `lake-query` (see `specs/lake-query/spec.md`). It supersedes the Trino decisions of `add-lake-sql`, whose code is already merged (#672) and whose openspec change still exists in `openspec/changes/`. Archiving `add-lake-sql` is the maintainer's call.
- Files: `foundation/data/clusters/feather-core/superset/` (`superset.yaml`, `networkpolicy.yaml`, `import_datasources.yaml`, `corvus-bundle/databases__corvus.yaml`, dataset files where needed), `foundation/data/clusters/feather-core/polaris/setup-catalog.py` and `setup-job.yaml`, and the image build (location decided in task 2.3).
- Layer: `foundation-data` (no new layer, no rename). Its dependencies do not change.
- Runtime impact: Superset web-server pods restart when the image or limits change. The Trino pods run in parallel until task group 5. Polaris gets one more principal and a Job re-run. The Corvus ingest path is unchanged.
