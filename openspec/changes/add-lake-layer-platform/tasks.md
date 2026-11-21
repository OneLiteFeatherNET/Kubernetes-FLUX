## 1. Verify assumptions on the cluster

- [ ] 1.1 Record the deployed Airflow version and chart source (`airflowVersion: 3.4.0` on main versus chart 1.22.0 / Airflow 3.2.2 in `add-data-lake`). Close design question 1
- [ ] 1.2 Check whether the pinned Airflow chart exposes pools as values. If it does, replace the Job in group 4 with the value (design decision 5, question 2)
- [ ] 1.3 In a scratch namespace (never in a layer namespace), with a temporary catalog role, confirm the Polaris 1.9 privilege names used in design decision 4: table-level grants with `TABLE_READ_DATA` and `TABLE_READ_PROPERTIES`, row-level deletes covered by `TABLE_WRITE_DATA`, branch writes covered by `TABLE_WRITE_PROPERTIES`, and whether a table grant needs namespace read. Remove the scratch objects afterwards. Update the table in design decision 4 if anything differs
- [ ] 1.4 Confirm that catalog-level `maintenance` and `sql` grants reach a namespace created after the grant (design decision 4, option 1)
- [ ] 1.5 Confirm the bucket policy PR `fix(data): grant polaris access to corvus lake objects` is merged, or record the blocker

## 2. Entra ID (manual, maintainer, with `az`)

These are one-time steps in the tenant. Their IDs are recorded in the code pull request, not in this change. Use the `az` CLI with the maintainer's own login.

- [ ] 2.1 Add the app roles `lake-pipeline` and `lake-erasure` to the API app "Polaris" (`az ad app update` with the `appRoles` list, same value style as `lake-sql`; keep the existing roles unchanged)
- [ ] 2.2 Create the client apps "Polaris Lake Pipeline" and "Polaris Lake Erasure" (`az ad app create`), with no redirect URI and a client secret for each
- [ ] 2.3 Assign the role `lake-pipeline` to the pipeline client and `lake-erasure` to the erasure client (`az rest` on the app role assignments, or the Entra portal)
- [ ] 2.4 Record the two client ids in the code pull request (not in Outline secrets, and not in this change)
- [ ] 2.5 Store each client secret as a SOPS Secret (group 4), never in plain text in the shell history or in this repository

## 3. Polaris (test first)

- [ ] 3.1 Write a test for the grant tables of `setup-catalog.py`: each new catalog role has exactly the privileges of design decision 4, `capture` has no write privilege for `pipeline` or `erasure`, and `lake_sql` has no write privilege. The test loads the module without a network call
- [ ] 3.2 Write a test that the schema of `quality.erasure_requests` in the script has the columns listed in `add-lake-layers` design decision 14, with the types of that contract
- [ ] 3.3 Extend `setup-catalog.py`: namespaces `refined`, `features`, `labels`, `ref` and `quality`; the table `quality.erasure_requests` through the Iceberg REST API; catalog roles `pipeline` and `erasure`; the grants of decision 4; the assignments `lake_pipeline` to `pipeline` and `lake_erasure` to `erasure`; the table-level write grant for `corvus_ingest`; the `PRINCIPALS` entries for both new client ids
- [ ] 3.4 Rename the Job in `setup-job.yaml` to `polaris-setup-7` and point the comment at the new version
- [ ] 3.5 Add `lake-pipeline` and `lake-erasure` to the filter and the `mappings` of `principalRolesMapper` in `polaris/release.yaml`

## 4. Airflow

- [ ] 4.1 Add `lake-pipeline-oauth.sops.env` and `lake-erasure-oauth.sops.env` (placeholders until task 2.5), each with `client_id` and `client_secret`, and generate the Secrets in `airflow/kustomization.yaml` the same way as `lake-maintenance-oauth`. Encrypt with `sops`
- [ ] 4.2 Add the pool file `pools.json` (`lake_spark`, 2 slots) and a ConfigMap generator for it
- [ ] 4.3 Add the pool Job: image of the Airflow release, metadata secret `airflow-db`, command `airflow pools import` on the pool file, `restartPolicy` and resources as the other Jobs in `data`. Name it with a version suffix (Jobs are immutable). Unless task 1.2 found a chart value
- [ ] 4.4 Confirm the Job runs after `airflow` is Ready (same layer, `dependsOn` already satisfied by `foundation-data`)

## 5. Validation

- [ ] 5.1 Run `./.github/scripts/validate.sh` and `kubectl kustomize` on the changed overlays (`polaris`, `airflow`)
- [ ] 5.2 Run `.github/scripts/check-sops-encryption.py` on all new encrypted files
- [ ] 5.3 Run the unit test of task 3.1 and 3.2 (fast, no cluster)
- [ ] 5.4 Run `openspec validate add-lake-layer-platform --strict`

## 6. Runbook (Outline, collection Infrastruktur)

- [ ] 6.1 Record the lake platform runbook: the Entra objects and how to add a client, how the setup Job is re-run, how to read the pool, and the grant matrix of design decision 4

## 7. Post-merge verification (on the cluster)

- [ ] 7.1 Run `polaris-setup-7` to completion and record its log
- [ ] 7.2 For each principal (`corvus_ingest`, `corvus_ml`, `lake_maintenance`, `lake_sql`, `lake_pipeline`, `lake_erasure`), request a token and run the positive and negative cases of the spec with `curl` against the catalog REST API (for example append to `capture.raw` as `lake_pipeline` must return 403)
- [ ] 7.3 Confirm `lake_sql` cannot write any table, including `labels.sessions`
- [ ] 7.4 Confirm that `flux get kustomizations` shows `foundation-data` Ready at the merged revision and that the pool shows 2 slots in the Airflow UI
- [ ] 7.5 Record the results in the runbook (task 6.1)

## 8. Code pull request

- [ ] 8.1 Open the pull request `feat(data): add lake layer platform` from the implementation branch on the remote main, with the proposal already merged; no files under `openspec/` in it
