## Context

Current state, read from the repository and the live cluster:

- Overlay `services/development/clusters/feather-core/dependency-track/` on chart 0.46.0. Apiserver is a StatefulSet with one replica and a 10Gi `ceph-rbd-fr01` PVC (`data-dependency-track-api-server-0`). Frontend is a Deployment with three replicas. Both run 4.14.6.
- Environment uses `ALPINE_OIDC_*` and `ALPINE_DATABASE_*`. Dependency-Track 5.2.1 refuses to start with any `alpine.*` property (`LegacyConfigPropertyValidator` in the 5.2.1 source). v5 uses `DT_OIDC_*` names (`properties.md` in the upstream docs).
- Database: CNPG cluster `feather-core-cluster-pg` (3 instances, PostgreSQL 17.11, `max_connections` 200, 75Gi each). Database `dependency-track` is about 2.4 GB, owned by role `dependency-track`, reached through the session pooler `feather-core-cluster-pg-pooler-rw`. `max_locks_per_transaction` and `max_wal_size` are not set in `cluster.yaml` and run at PostgreSQL defaults (64 and 1GB).
- Public access: Cloudflare tunnel Ingress `dependency-track-cloudflare-tunnel` in namespace `dependency-track`, host `dependency-track.onelitefeather.dev`. `/api` and `/health` go to the apiserver, everything else to the frontend.
- The CI job `sbom-scan.yaml` uploads nothing today, because `DT_API_KEY` is unset. It only builds SBOMs and dry-runs the upload.
- Renovate PR #628 (open) keeps the chart below 2.x until the migration is planned.

Upstream constraints that shape the design (migrating-from-v4 and rehearsing-the-v4-migration, upstream docs):

- "The v4 instance must be on 4.14.2 or later." Our 4.14.6 qualifies.
- The migrator "does not write to the v4 source". The migrating page asks for a quiescent source and says to stop the v4 API server. The rehearsal page accepts "a snapshot or restored copy" as the source.
- The target must be empty, and the migrator refuses a target that already holds user data. The migrating page describes it as "a Postgres 14+ cluster, dedicated to the v5 instance". The bootstrap user owns the v5 schema, and the apiserver must use the same owner.
- The load step is not resumable. Recovery is a reset of the target and a new run.
- After migration, every notification rule is disabled, repository and analyzer and vulnerability-source credentials are dropped (they must be re-entered), and users with duplicate identities get a `-CONFLICT-*` suffix.
- The `run` and `extract` steps require `--metrics-retention-days`.

Chart 2.9.0 (`charts/dependency-track`, UPGRADING.md in the chart repository) moves to `database.jdbcUrl` plus `database.existingSecret`, `secretManagement.database.kek.existingSecret` (keyset mode recommended), `fileStorage.provider: local` with a PVC, and an apiServer `deployment` strategy. The chart's own default is `maxSurge: 0`, `maxUnavailable: 1`.

Constraint for this change: no read outage on the public hostname, v4 data never modified, and a rollback that is a single revert.

## Decision drivers

- Reads must stay available during the migration. The upstream route stops the API for the whole run.
- v4 must stay byte-for-byte usable as the rollback until the stability window ends.
- The migrator needs a quiescent, consistent source and an empty target. Both can be provided without stopping v4.
- The migration is one-way and not resumable, so every attempt must be repeatable from scratch.
- The shared CNPG cluster has a connection budget of 200 and no spare capacity for extra clusters.
- Everything is Git-managed. Each change is one reviewable commit, and Flux prunes or orphans as the layer rules say.

## Options

### A. Upstream in-place migration (v4 stopped during the run)

Stop the v4 apiserver, run the migrator against the live database, start v5 on the same hostname.

- Pro: the documented path. No extra database or copy.
- Con: a full read and write outage for the length of the run, which is not measured yet. The migrator is one-way, so a failed run means a restore of the v4 database before anything can be served again.
- Rejected: an outage on the public hostname, and no rollback that leaves v4 intact.

### B. Parallel v5 fed by BOM replay

Run v5 beside v4 and mirror BOM uploads into it through the `BOM_PROCESSED` webhook (parallel-run page).

- Pro: no outage, and v5 gets the live BOM stream.
- Con: the upstream page states that only BOM uploads replicate. Manual project edits, policy changes, findings triage, notification rules, users and team membership do not. Today nothing uploads BOMs (`DT_API_KEY` is unset), so the replay would move almost no data.
- Rejected alone. It is not needed here, because the cutover takes a fresh copy under the write freeze.

### C. Migrate from a consistent copy of v4 (chosen)

Copy the v4 database with `pg_dump` (one transaction snapshot), migrate the copy with the migrator into a new empty database, and run v5 beside v4 under a test hostname. Rehearse on a full copy. At cutover, block writes at the edge, take a fresh copy, migrate, verify, move the public ingress, and then restore what the migration disables.

- Pro: reads never stop. v4 is never written to. Rollback is one revert of the ingress change. Each attempt starts from a fresh copy.
- Con: a write freeze at cutover for the length of copy, migrate, verify and ingress switch. Writes made in v5 after cutover are lost if the cutover is rolled back.

### D. Dedicated CNPG cluster for v5

The parallel-run and migrating pages describe the v5 target as a cluster dedicated to v5.

- Pro: matches the documented layout. Tuning such as `max_locks_per_transaction` 256 and `max_wal_size` 8GB can be applied without touching other tenants.
- Con: a third CNPG cluster with its own instances, backups, pooler and monitoring, for a 2.4 GB database. The shared cluster is already near its connection budget.
- Rejected for now. The migrator needs an empty, exclusively owned database, and a dedicated database in the shared cluster provides that. The documented tuning is listed as a rehearsal gate instead (see open questions).

## Decision

Option C, with these sub-decisions.

### 1. Database placement

- Create database `dependency-track-v5` (CNPG Database CR, `spec.name: dependency-track-v5`, owner `dependency-track`, `databaseReclaimPolicy: retain`). The migrator's bootstrap user is the same role as the apiserver, so ownership matches.
- Create a temporary database `dependency-track-v4-copy` (owner `dependency-track`, `databaseReclaimPolicy: delete`). Removing its CR drops it. It is refilled from the v4 database by `pg_dump --format=custom | pg_restore` on each run.
- The migrator and copy jobs connect to the primary read-write service (`feather-core-cluster-pg-rw`), not the pooler. The apiserver connects through the session pooler, as v4 does. The transaction pooler is not used, because the migrator holds long transactions.
- The shared cluster's connection budget is checked in the rehearsal: the copy, migrator and v5 apiserver pools must fit beside the existing consumers under `max_connections` 200.

### 2. Namespace and release

- Namespace `dependency-track-v5`, labelled like the v4 namespace (`baseline` enforce, `restricted` warn and audit).
- HelmRelease `dependency-track-v5` with chart `=2.9.0` and images pinned to 5.2.1. The release is in a new namespace so that no chart-1.x object (secret-key Secret, StatefulSet PVC) can collide with v4 or be inherited by it.
- The release is merged with `suspend: true`. The chart's initializer runs as a pre-install Helm hook and would apply the v5 schema to the empty target before the migrator's `bootstrap` runs, which the upstream page forbids ("don't boot v5 before migrating"). The release is resumed in rehearsal step 4, after the migrator's `verify`.
- Secrets in a new SOPS Secret `dependency-track-v5-env` (DB credentials, OIDC issuer and client id) and a KEK keyset Secret, referenced through `secretManagement.database.kek.existingSecret` in mode `keyset`. The KEK is never set inline.
- The file storage is `fileStorage.provider: local` on `ceph-rbd-fr01`, `ReadWriteOnce`.
- Pod labels `logs.onelitefeather.net/env: prod` on all v5 pods, so Alloy keeps shipping their logs.
- The v5 environment carries no `ALPINE_*` variable. A grep over the rendered manifests is a review gate.
- Priority class `feather-standard`, the frontend zone preference, and anti-affinity use chart keys where they exist (`frontend.affinity`, `frontend.priorityClassName`). Where a chart key does not exist, the post-render patch that v4 uses is kept.
- Frontend: three replicas, stateless, with a PodDisruptionBudget for the v5 frontend labels (the v4 PDB selects v4 labels and does not cover v5).
- API server: one replica, chart default rollout (`maxSurge: 0`, `maxUnavailable: 1`). The file-storage PVC is ReadWriteOnce, so a surge pod could not mount it while the old pod runs. The alternatives are a `maxSurge: 1` rollout, which deadlocks on a ReadWriteOnce volume, and S3 file storage, which adds an object-store dependency and a credential path that one replica does not need. Each API restart is therefore a short outage of the API only; the frontend stays up.
- Resource requests 1 CPU and 2Gi. The limit is set from the rehearsal measurement. The v4 comment about a 4GB heap minimum is not carried over until the rehearsal confirms it for v5. The v4 limit of 6Gi is not copied.
- ServiceMonitor enabled for the apiserver, as v4 has it.
- Time zone: the chart's image sets `TZ=Etc/UTC`. The chart value is not overridden.

### 3. Flux layer

- New layer `services-dependency-track-v5`: a Flux Kustomization CR in `clusters/feather-core/`, path `services/layers/feather-core/dependency-track-v5/`, `deletionPolicy: Orphan`, `prune: true`, `interval: 10m0s`, SOPS decryption, `dependsOn` `foundation-databases`, `foundation-networking` and `foundation-storage`. The `services-development` layer settings are the template.
- Why a new layer and not an entry in `services-development`: `products` and `products-dev` depend on `services-development`. A failing v5 release (realistic during rehearsal) would then mark that layer not Ready and hold back both product layers, even though no product uses Dependency-Track. The new layer contains the failure.
- This is an addition, not a rename or split. The v4 HelmRelease stays in `services-development` and is not moved.
- Alternative (same layer) is recorded as rejected for the reason above. The review may still choose it, if the blast radius is judged acceptable.

### 4. Test hostname

- `dependency-track-next.onelitefeather.dev` through the `cloudflare-tunnel` class, behind a Cloudflare Access application (manual).
- The Entra app registration gets the test hostname as an extra redirect URI (manual). The public hostname already has its redirect URI.
- The v5 frontend's `API_BASE_URL` points to the test hostname during rehearsal and to the public hostname after cutover.

### 5. Rehearsal

On a fresh copy, in order:

1. `pg_dump` the v4 database into `dependency-track-v4-copy`, and record its size and duration.
2. Migrator `bootstrap` against `dependency-track-v5`, then `verify` (expect the empty-target output, Flyway head `202605111028`).
3. Migrator `run --dry-run`, then `run --metrics-retention-days <N>`, then `verify`. `N` is set in the rehearsal (the upstream example uses 90).
4. Start the v5 apiserver against the migrated database and wait for it to become healthy. Confirm no `alpine.*` warnings and that TZ is UTC.
5. Run the verification checklist (below).
6. Run `cleanup`, drop `dependency-track-v5` and `dependency-track-v4-copy`, and record the measurements.

Verification checklist (must pass before cutover):

- Row counts for projects, components, vulnerabilities, findings, analyses, policies, teams, users and API keys, compared with the v4 copy. Differences must match the migrator's own `verify` notes (for example, findings no analyzer reports are deactivated).
- OIDC login through the test hostname by a maintainer in the Entra group.
- A legacy API key from v4 can `PUT /api/v1/bom` a test BOM and the project receives the analysis.
- The permission review: holders of `ACCESS_MANAGEMENT` and `SYSTEM_CONFIGURATION` received `PORTFOLIO_ACCESS_CONTROL_BYPASS` and `SECRET_MANAGEMENT`. Each automatic grant is either accepted or removed.
- Users with a `-CONFLICT-*` suffix are listed, and each one is resolved.
- Every migrated notification rule is disabled, and no publisher can send to a real channel from the test instance.
- Measurements: copy time, migrator time per step, peak memory, heap, and the API restart time per rollout.

### 6. Cutover

Cutover is a window, with these steps in order:

1. Announce the window. Pause the CI job that uploads SBOMs (inactive today, active after the SBOM change).
2. Enable the write freeze at the edge: a Cloudflare rule that rejects `POST`, `PUT`, `PATCH` and `DELETE` on `dependency-track.onelitefeather.dev` (manual). Reads continue. The rehearsal confirms that no read endpoint in v4 uses a write method. If one does, the rule excludes that path.
3. `pg_dump` the v4 database fresh into `dependency-track-v4-copy`, then run the migrator from the rehearsal steps 2 to 3, then the checklist.
4. Merge the cutover PR `feat(dependency-track): cut over to dependency-track 5`. It moves the Cloudflare tunnel Ingress into namespace `dependency-track-v5`, points it at the v5 services, deletes the v4 Ingress, and adds the new namespace to the Flux notification alert. The v4 HelmRelease stays deployed.
5. Restore: re-enable each notification rule after review, re-enter the repository, analyzer and vulnerability-source credentials, and confirm the v5 analyzers reach their sources.
6. Remove the edge write freeze, announce the end of the window, and drop `dependency-track-v4-copy`.

Freeze length is the rehearsal's total of steps 3 to 4, plus a margin. The duration is not known until measured.

### 7. Rollback

- Before step 5 of cutover: revert the cutover PR. Flux restores the v4 Ingress in namespace `dependency-track`. The v4 pods never stopped, so reads and writes resume at once. Remove the edge freeze.
- After cutover: the same revert. Any v5 write made after cutover is lost, because v4 has not seen it. The window and the list of changes made in v5 are recorded in the announcement, so the decision to roll back is made with that loss in view.
- v4 is never written to. Its database stays as it was at the freeze.

### 8. Retirement

Retirement is a later change, after 14 days of stable operation on v5:

1. Take a final `pg_dump` of the v4 database and store it in the S3 backups, with a test restore.
2. Remove the v4 HelmRelease (`chore(dependency-track): retire dependency-track 4`).
3. Drop `dependency-track-v4-copy` and, in a later change, `dependency-track` (v4 database).
4. Delete the v4 PVC and follow the Ceph volume cleanup procedure, so the RBD image is freed.

### 9. Renovate

- If PR #628 is merged before cutover, its cap is removed by a follow-up `chore(renovate)` PR after cutover, so that 2.x and 5.x updates are offered. The images stay pinned by digest.
- If #628 is still open at cutover, it is closed, because the cap no longer describes anything.

## Consequences

- Reads are not interrupted before cutover. The write freeze at cutover is the only outage, and it is measured in the rehearsal.
- Rollback is a single revert. Its cost is the loss of v5-only writes made after cutover.
- The API restarts on each v5 rollout (single replica, ReadWriteOnce file storage). Accepted for one replica; S3 file storage is the way out if that changes.
- The migration deviates from the documented "dedicated cluster" layout. The database is exclusive to v5, and the rehearsal gate decides whether the defaults are enough.
- Two extra Flux objects (new layer, new namespace) and one extra database for the migration period. Removed in the retirement change.
- After migration, a one-time drop in active findings and a metrics step-down follow the first analysis cycle, as the upstream page describes. Users should expect it.
- The `list` endpoints default to 100 results, so any client that pages beyond 100 changes behaviour (verified for team, project-finding and vulnerability-project listing).

## Open questions

Each question has an owner and a point at which it must be answered.

1. **Edge write block.** Is a Cloudflare rule that blocks write methods available on our plan, and does any v4 read endpoint use a write method? Answered in task 1.7 and again in the rehearsal. Fallback if not available: an announced freeze plus a comparison of row counts and last-updated timestamps between the cutover copy and v4 at the switch.
2. **Non-interactive password.** `--target-pass` and `--source-pass` prompt when given no value. How does the migrator take the password in a Job (environment, file or option)? Answered in task 1.4 by reading the image's help output.
3. **Metrics retention.** `N` for `--metrics-retention-days`. The upstream example uses 90. The rehearsal sets the value, and it affects load time because one partition is created per retained day.
4. **Pool and memory settings.** Names of the v5 database pool properties, and the apiserver memory limit. Answered from `properties.md` and the rehearsal. The v4 values (6Gi limit, pool sizes 6 and 4) are starting points, not targets.
5. **Documented tuning.** The rehearsal page recommends `max_wal_size` 8GB or more and `max_locks_per_transaction` 256 for the migrator. These are not required by the migrator. Run the rehearsal at defaults first. A change to the shared cluster's parameters needs a CNPG rolling restart, so it is only made if the rehearsal fails without it.
6. **Chart keys for the API server.** Whether the chart's top-level `nodeSelector` applies to the API server (its values say it does for web, worker, frontend and initializer), and whether the API server has a priority class key. If not, the v4 post-render patch is kept.
7. **Renovate #628.** Its status at cutover decides the action in decision 9.
8. **Ingress move.** Whether the Cloudflare tunnel controller serves a host from an Ingress in a new namespace without a visible gap. Measured in the rehearsal with the test hostname, using the same mechanics.
9. **Migration job mechanism.** Copy and migrator Jobs are created with run-specific names and `suspend: true`, and a commit resumes them. Whether Flux handles suspended Jobs cleanly is confirmed in the rehearsal, and the mechanism is replaced if it does not.
10. **Staging disk.** The migrator needs roughly twice the dataset size as scratch space. The CNPG instances have 75Gi each. Checked in the rehearsal.

## References

- Migration: https://dependencytrack.github.io/docs/next/guides/administration/migrating-from-v4
- Rehearsal: https://dependencytrack.github.io/docs/next/tutorials/rehearsing-the-v4-migration
- Parallel run: https://dependencytrack.github.io/docs/next/guides/administration/running-v4-and-v5-in-parallel
- Secret management (KEK): https://dependencytrack.github.io/docs/next/guides/administration/configuring-secret-management
- OIDC: https://dependencytrack.github.io/docs/next/guides/administration/configuring-oidc
- v5 breaking changes: https://dependencytrack.github.io/docs/next/reference/api/v5-breaking-changes
- Property reference: https://dependencytrack.github.io/docs/next/reference/configuration/properties
- Chart 2.9.0 upgrade notes: https://github.com/DependencyTrack/helm-charts/blob/dependency-track-2.9.0/charts/dependency-track/UPGRADING.md
- Renovate cap: PR #628 (open), https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/pull/628
- SBOM upload target: PR #641 (open), https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/pull/641
