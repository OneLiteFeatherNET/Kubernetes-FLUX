## 1. Prepare (manual and read-only)

- [ ] 1.1 Generate the KEK as a Tink AES-GCM keyset (mode `keyset`) on the maintainer machine; store it SOPS-encrypted in the repository and in the maintainer's password store (manual)
- [ ] 1.2 Add `https://dependency-track-next.onelitefeather.dev` as an extra redirect URI on the Entra app registration (manual)
- [ ] 1.3 Create the Cloudflare Access application for `dependency-track-next.onelitefeather.dev` (manual)
- [ ] 1.4 Confirm the migrator image tag `ghcr.io/dependencytrack/v4-migrator:5.2.1` and how it takes the database passwords non-interactively (`--target-pass` and `--source-pass` prompt when given no value)
- [ ] 1.5 List the v4 integrations that the migration drops and that must be re-entered: repository credentials, analyzer keys, vulnerability-source keys, notification rules. Use the v4 UI, read-only; no secret value is copied into the repository or into this change
- [ ] 1.6 Check the status of Renovate PR #628 and record the action for cutover (close, or a follow-up `chore(renovate)` PR removing the cap)
- [ ] 1.7 Check which v4 API endpoints that are read-only use a write method (`POST`), to scope the edge write freeze; confirm that a Cloudflare rule can reject write methods on the host (manual)
- [ ] 1.8 Measure the connection budget on `feather-core-cluster-pg` (current use against `max_connections` 200) with read-only queries, to size the copy, migrator and v5 pools

## 2. Build (content of the code PR)

- [ ] 2.1 Add the Database CR `dependency-track-v5` (owner `dependency-track`, `databaseReclaimPolicy: retain`) and the temporary Database CR `dependency-track-v4-copy` (owner `dependency-track`, `databaseReclaimPolicy: delete`) under `foundation/databases/clusters/feather-core/postgresql/database/`, listed in the postgresql kustomization
- [ ] 2.2 Add the base `services/development/base/dependency-track-v5/` with namespace `dependency-track-v5` (labels as the v4 namespace) and a HelmRelease `dependency-track-v5` with chart `=2.9.0`, images pinned to 5.2.1, the `flux.onelitefeather.dev/remediation: retries-only` label, and `suspend: true` (the chart initializer must not run before the migrator, see design decision 2)
- [ ] 2.3 In the base release: `database.jdbcUrl` on the session pooler, `database.existingSecret`, `secretManagement.database.kek` in mode `keyset` via `existingSecret`, `fileStorage.provider: local` on `ceph-rbd-fr01` (ReadWriteOnce), chart default rollout strategy, `serviceMonitor` enabled
- [ ] 2.4 Add the overlay `services/development/clusters/feather-core/dependency-track-v5/`: SOPS Secret `dependency-track-v5-env` (DB credentials, OIDC issuer and client id), SOPS KEK keyset Secret, `DT_OIDC_*` environment variables, labels `logs.onelitefeather.net/env: prod`, no `ALPINE_*` variable
- [ ] 2.5 Set requests of 1 CPU and 2Gi for the apiserver; leave the limit to the rehearsal measurement, with a comment that points to the rehearsal record
- [ ] 2.6 Set priority class `feather-standard`, the frontend zone preference and anti-affinity through chart keys (`frontend.affinity`, `frontend.priorityClassName`), with post-render patches only where no chart key exists
- [ ] 2.7 Add a PodDisruptionBudget for the v5 frontend labels
- [ ] 2.8 Add the Cloudflare tunnel Ingress `dependency-track-next-cloudflare-tunnel` for `dependency-track-next.onelitefeather.dev` in namespace `dependency-track-v5`, with the same `/api` and `/health` split as v4
- [ ] 2.9 Add the migration Jobs (copy, migrator bootstrap, verify, run, cleanup) with run-specific names and `suspend: true`, using the primary read-write service; their credentials come from a SOPS Secret in namespace `dependency-track-v5`
- [ ] 2.10 Add the Flux layer `services-dependency-track-v5`: a Kustomization CR in `clusters/feather-core/` (path `services/layers/feather-core/dependency-track-v5/`, `deletionPolicy: Orphan`, `prune: true`, `interval: 10m0s`, SOPS decryption, `dependsOn` `foundation-databases`, `foundation-networking`, `foundation-storage`), and its layer kustomization
- [ ] 2.11 Fix the stale comment in the v4 `release.yaml` that refers to `httproute.yaml` (comment-only change; no v4 behaviour changes)
- [ ] 2.12 Run `./.github/scripts/validate.sh` and `.github/scripts/check-sops-encryption.py`; confirm that no file under `openspec/` is part of the code PR

## 3. Rehearse (after the code PR is merged)

- [ ] 3.1 Copy the v4 database into `dependency-track-v4-copy` with `pg_dump --format=custom` piped to `pg_restore`; record size and duration
- [ ] 3.2 Run the migrator `bootstrap`, `verify`, `run --dry-run`, `run --metrics-retention-days <N>`, `verify`; record duration of each step and the peak memory of each Job
- [ ] 3.3 After the migrator `verify` of 3.2, resume the v5 HelmRelease (`suspend: false`) against `dependency-track-v5` on the test hostname; confirm health, UTC time zone, no `alpine.*` warning in the logs
- [ ] 3.4 Run the verification checklist from design decision 5 (row counts, OIDC login, legacy API key `PUT /api/v1/bom`, permission review, `-CONFLICT-*` users, notification rules disabled, credentials absent)
- [ ] 3.5 Record the measurements that set the apiserver memory limit, the freeze length and the API restart time per rollout; record the chart-key findings from open question 6 in the code PR
- [ ] 3.6 Run the migrator `cleanup`; drop `dependency-track-v5` and `dependency-track-v4-copy`; confirm the v4 database is unchanged by comparing row counts with the values recorded before 3.1

## 4. Cutover (separate PR, in the cutover window)

- [ ] 4.1 Announce the cutover window and the expected write freeze; pause the CI job that uploads SBOMs
- [ ] 4.2 Enable the edge write freeze on `dependency-track.onelitefeather.dev` (Cloudflare rule rejecting `POST`, `PUT`, `PATCH`, `DELETE`; excludes any read endpoint found in 1.7) (manual)
- [ ] 4.3 Take a fresh `pg_dump` into `dependency-track-v4-copy`, run the migrator steps from 3.2 and the checklist from 3.4; stop if any check fails
- [ ] 4.4 Open and merge the cutover PR `feat(dependency-track): cut over to dependency-track 5`: move the Cloudflare tunnel Ingress to namespace `dependency-track-v5` and point it at the v5 services, delete the v4 Ingress, add `dependency-track-v5` to the Flux notification alert namespaces; the v4 HelmRelease stays deployed
- [ ] 4.5 Re-enable the reviewed notification rules; re-enter the repository, analyzer and vulnerability-source credentials from 1.5; confirm that the analyzers reach their sources
- [ ] 4.6 Remove the edge write freeze, announce the end of the window, and drop `dependency-track-v4-copy`
- [ ] 4.7 Record the rollback procedure in the announcement: revert the cutover PR, remove the freeze; state that writes made in v5 after cutover are lost on rollback

## 5. Verify and retire (follow-up changes, not in the code PR)

- [ ] 5.1 Observe v5 for a 14-day stability window (alerts, error rate in Loki, API restarts)
- [ ] 5.2 Resolve Renovate PR #628 as recorded in 1.6, and remove the cap in a follow-up `chore(renovate)` PR if it was merged
- [ ] 5.3 Take a final `pg_dump` of the v4 database into the S3 backups and test-restore it into a scratch database
- [ ] 5.4 Retire v4 in a `chore(dependency-track)` PR: remove the v4 HelmRelease and its overlay; drop `dependency-track-v4-copy`; drop the v4 database in a later change; delete PVC `data-dependency-track-api-server-0` and follow the Ceph volume cleanup procedure
- [ ] 5.5 Re-point the SBOM upload job (PR #641, `add-dependency-track-sbom-sync`) at v5, and review whether the CycloneDX 1.7 to 1.6 rewrite in `upload-sbom.py` is still needed
- [ ] 5.6 Write the runbook for the migration and the rollback in the Outline collection Infrastruktur

## 6. Code pull request

- [ ] 6.1 Open the code pull request under the title `feat(dependency-track): migrate to dependency-track 5`; no files under `openspec/` in it
