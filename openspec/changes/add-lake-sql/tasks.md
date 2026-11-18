## 1. Polaris: principal role lake_sql

- [ ] 1.1 Add the catalog role `lake_sql` with the read grants on catalog `corvus` (`TABLE_LIST`, `NAMESPACE_LIST`, `TABLE_READ_PROPERTIES`, `TABLE_READ_DATA`) and the principal role `lake_sql` in `foundation/data/clusters/feather-core/polaris/setup-catalog.py`
- [ ] 1.2 Add the principal "Polaris Trino" (`8832de30-7197-40ed-a2bc-68c88bc770cb`) mapped to principal role `lake_sql` in the same script
- [ ] 1.3 Extend the OIDC role mapping in `foundation/data/clusters/feather-core/polaris/release.yaml`: add `lake-sql` to the filter regex and add the mapping to `PRINCIPAL_ROLE:lake_sql`; leave the other mappings unchanged
- [ ] 1.4 Bump the setup Job name from `polaris-setup-3` to `polaris-setup-4` in `setup-job.yaml` (Jobs are immutable), and confirm the Job completes on the cluster after the code pull request is merged

## 2. Trino

- [ ] 2.1 Pin the `trino` chart to an exact version (latest at proposal time: 1.45.0, application 484) and add the `HelmRepository` `https://trinodb.github.io/charts` in `foundation/sources/clusters/feather-core/sources/`; record the chosen version in the code pull request
- [ ] 2.2 Create the client secret of "Polaris Trino" and store it as a SOPS Secret, delivered to the coordinator and worker as environment variables for the catalog properties (the client id is not a secret)
- [ ] 2.3 Add the Iceberg catalog `corvus` in `additionalCatalogs`: REST catalog `http://polaris.data.svc.cluster.local:8181/api/catalog`, warehouse `corvus`, OAuth2 client credentials with server URI `https://login.microsoftonline.com/1a14dfb5-0eac-41bf-94cb-195c2e387520/oauth2/v2.0/token` and scope `api://9e2548f4-5e10-440d-9444-cad813a20c87/.default`, vended credentials off. Verify that the Entra token is accepted with the scope parameter (open question 1)
- [ ] 2.4 Add the CephObjectStoreUser `trino-lake` in `foundation/storage/.../rook-fr01/users/`, following the existing user pattern
- [ ] 2.5 Copy the keys that Rook generates for `trino-lake` into a SOPS-encrypted Secret in namespace `data`; the plain keys do not go into git
- [ ] 2.6 Add the bucket policy on `polaris-corvus` that allows user `trino-lake` only `s3:GetObject` and `s3:ListBucket`, using the same mechanism as `feat(corvus): grant corvus access to the iceberg lake`; the policy document is plain and goes into git
- [ ] 2.7 Configure S3 for the catalog: `s3.endpoint` `http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc`, path-style access, region `us-east-1`, with the keys of the Secret of task 2.5 mounted into the coordinator and worker only. Confirm whether `s3:GetBucketLocation` is needed (open question 2)
- [ ] 2.8 Configure file-based access control (`accessControl`) with the catalog rule `allow: read-only` for `corvus`, and deny write statements on the `system` catalog
- [ ] 2.9 Set the resources of the coordinator (request and limit 2 Gi, JVM heap 1.5 Gi) and of the worker (request and limit 3 Gi, JVM heap 2.4 Gi); set the Trino query memory limits below the heap; CPU requests only, no CPU limits
- [ ] 2.10 Add the HelmRelease in namespace `data` with the chart version of task 2.1, `install` and `upgrade` remediation as in the other releases, and one worker
- [ ] 2.11 Expose Trino as a `ClusterIP` Service only, with a NetworkPolicy that admits port 8080 from Superset pods and from Trino's own pods, and nothing else
- [ ] 2.12 Confirm that the Trino Service has no Ingress or HTTPRoute

## 3. Superset

- [ ] 3.1 Pin the `superset` chart to an exact version (latest at proposal time: 0.22.8, application 6.1.0) and add the `HelmRepository` `https://apache.github.io/superset` in `foundation/sources/clusters/feather-core/sources/`; record the chosen version in the code pull request
- [ ] 3.2 Add the managed role `superset` (`login: true`, `connectionLimit: 10`) and the Database `superset` (owner `superset`, `databaseReclaimPolicy: retain`) in `foundation/databases/clusters/feather-core/postgresql/`, following the `airflow` pattern, and register them in the `kustomization.yaml`
- [ ] 3.3 Add the SOPS-encrypted Secrets `role-superset` (keys `username`, `password`) and the Superset metadata connection Secret, encrypted in place with `sops`
- [ ] 3.4 Create the client secret of the Superset app `b592b27d-d127-4de6-9e4d-d3e252b46852` and generate the Superset `SECRET_KEY`; store both in one SOPS-encrypted Secret and deliver them through `extraSecretEnv`, not in plain values
- [ ] 3.5 Add the HelmRelease in namespace `data` with `postgresql.enabled: false` and `redis.enabled: false`, the metadata database from task 3.2, and the Entra login: `AUTH_OAUTH`, provider `azure`, metadata URL `https://login.microsoftonline.com/1a14dfb5-0eac-41bf-94cb-195c2e387520/v2.0/.well-known/openid-configuration`, client id `b592b27d-d127-4de6-9e4d-d3e252b46852`, `AUTH_ROLES_SYNC_AT_LOGIN` on, `AUTH_USER_REGISTRATION_ROLE` `Gamma`, and the role mapping of the Entra app role `superset-admin` to `Admin`
- [ ] 3.6 Configure the file-system cache and disable asynchronous queries, so that no Redis or Celery worker is needed (design, decision 6)
- [ ] 3.7 Preconfigure the database connection `corvus` to Trino (`trino://superset@trino.data.svc.cluster.local:8080/corvus`); no Trino credential is stored, because access is controlled by the NetworkPolicy and Trino access control
- [ ] 3.8 Set the Superset web server resources (request and limit 1 Gi, limit 1.5 Gi) and the init Job resources; CPU requests only, no CPU limits
- [ ] 3.9 Expose Superset as a `ClusterIP` Service and add an HTTPRoute `superset.apps.onelite.feather` on the internal Gateway `eg` (namespace `envoy`, section `https`), the same pattern as `products/corvus/clusters/feather-core/corvus/httproute.yaml`; no public route
- [ ] 3.10 Add a NetworkPolicy for the Superset egress (Trino on 8080, CNPG on the database port, Entra ID over HTTPS for login)
- [ ] 3.11 Add `foundation-networking` to the `dependsOn` of the layer `foundation-data` in `clusters/feather-core/foundation-data.yaml`, because the HTTPRoute attaches to the Gateway

## 4. Validation

- [ ] 4.1 Run `./.github/scripts/validate.sh` and `kubectl kustomize` on each new overlay
- [ ] 4.2 Run `.github/scripts/check-sops-encryption.py` on all new and changed encrypted files
- [ ] 4.3 Confirm that the Trino and Superset Secrets mounted into pods match the keys named in the manifests

## 5. Smoke tests on the cluster

- [ ] 5.1 Create a smoke table `corvus.capture.smoke_sql` through the Polaris setup role (not through Trino), insert one row, then query it from Superset and from the Trino CLI as a user with the role `Admin`, and confirm the result
- [ ] 5.2 Load-test a few concurrent queries against the smoke table, check the memory of the coordinator and worker against the sizing of design decision 8, and adjust the limits if needed
- [ ] 5.3 Confirm that an `INSERT` submitted through Superset is refused by Trino (task 2.8), and that a commit with the `lake-sql` token is refused by Polaris with HTTP 403
- [ ] 5.4 Confirm that an object upload to `polaris-corvus` with the keys of `trino-lake` is refused by RGW with AccessDenied, and that GetObject and ListBucket succeed
- [ ] 5.5 Confirm that a Trino write which reaches S3 is refused by RGW even when the Trino access control is bypassed in a test setup, so that layer 3 of design decision 3 is proven on its own
- [ ] 5.6 Confirm that a user with the role `Gamma` and no permission on `corvus` cannot query it from Superset
- [ ] 5.7 Confirm that a user not assigned to the Superset enterprise app cannot sign in
- [ ] 5.8 Confirm that a token of "Polaris Corvus ML" cannot use the `lake_sql` grants and that the ML role is unchanged
- [ ] 5.9 Drop the smoke table after the test (through the maintenance role or the setup role), and confirm that no file of it remains in the bucket after the next nightly maintenance run
- [ ] 5.10 Confirm in Prometheus that Trino and Superset have at least one target in state `up`, or add a `ServiceMonitor` if they have none

## 6. Documentation

- [ ] 6.1 Write the runbook in Outline, collection Infrastruktur: how staff are granted and removed (Entra assignment and Superset permissions), how the Trino catalog and its read-only layers are verified, query-log retention, and the procedure for rotating the Polaris Trino client secret and the `trino-lake` keys
- [ ] 6.2 Record the exact chart versions of Trino and Superset in Outline, with the date of the pin

## 7. Code pull request

- [ ] 7.1 Open the pull request `feat(data): add trino and superset for lake sql`; no files under `openspec/` in it
