## Why

Dependency-Track runs as release 4.14.6 (chart 0.46.0). Upstream ships Dependency-Track 5.2.1 (chart 2.9.0) as the current line, and chart 2.x cannot be reached by an in-place `helm upgrade`. v5 needs a new PostgreSQL database filled by an offline migrator, a new key-encryption-key Secret, and an API server that runs as a Deployment with a new volume. Renovate PR #628 caps the chart below 2.x until this migration is planned, so Renovate cannot offer the v5 line at all, and every v4 change stays a hand-pinned edit.

The upstream route is an in-place migration: the v4 instance stops while the migrator runs, and the migration is one-way. This change migrates from a consistent copy of the v4 database instead, while v4 keeps serving reads. v5 runs beside v4 under a test hostname, is rehearsed on a full copy, and takes over the public hostname in a short cutover with a write freeze. v4 is never written to and stays in place as the rollback until it is retired in a later change.

## What Changes

- Migrate the Dependency-Track deployment from v4 (chart 0.46.0, apiserver and frontend 4.14.6) to v5 (chart pinned `=2.9.0`, images 5.2.1).
- A new PostgreSQL database `dependency-track-v5` in the shared CNPG cluster, owned by the existing role `dependency-track`.
- A temporary database `dependency-track-v4-copy`, filled by a consistent `pg_dump` of the v4 database. The migrator reads only from this copy. It is dropped after the rehearsal and after the cutover.
- A new namespace `dependency-track-v5` with a HelmRelease `dependency-track-v5`, running alongside v4 under the test hostname `dependency-track-next.onelitefeather.dev` behind Cloudflare Access.
- A rehearsal on a fresh copy: `bootstrap`, `verify`, `run --dry-run`, `run`, `verify`, `cleanup`, then a verification checklist that gates the cutover.
- A cutover that announces a write freeze, takes a fresh copy, migrates, verifies, moves the public ingress to v5, and then restores notification rules and integration credentials.
- A rollback that reverts the cutover change and leaves v4 untouched.
- Retirement of v4 (HelmRelease, copy database, data) only after a 14-day stability window, in a later change.

**Commit type and scope:** the change ships under `feat(dependency-track)`. The implementation lands as `feat(dependency-track): migrate to dependency-track 5` (code PR). The public cutover is a second PR of the same type and scope, described in the design. Retirement is a later `chore(dependency-track)` change.

## Out of scope

- The SBOM upload job (PR #641, `add-dependency-track-sbom-sync`). It targets v5 and follows after cutover.
- Changing the OIDC provider, the public hostname, or the Entra tenant.
- Adopting v5-only features (new permission model, new analyzers, S3 file storage).
- Removing the CycloneDX 1.7 to 1.6 rewrite in `.github/scripts/upload-sbom.py`. It becomes unnecessary once v5 is live, and belongs to the SBOM change.
- The stale `httproute.yaml` comment in the v4 `release.yaml`. It is fixed in the code PR, as a comment-only edit.

## Impact

- New capability `dependency-track-v5-migration` (see `specs/dependency-track-v5-migration/spec.md`).
- New files in `services/development/base/dependency-track-v5/` and `services/development/clusters/feather-core/dependency-track-v5/`, a new Database CR in `foundation/databases/clusters/feather-core/postgresql/database/`, and an entry in the postgresql kustomization.
- One new Flux layer `services-dependency-track-v5` (see design decision 3). No existing layer is renamed or split, and the v4 layer `services-development` keeps its contents.
- Cutover changes the Cloudflare tunnel Ingress (moved to the new namespace) and the Flux notification namespace list in `foundation/sources/base/flux-notifications/alert.yaml`.
- Runtime impact before cutover: none on v4. The v5 test instance consumes its own resources and writes nothing to v4. During cutover, UI and API writes are blocked for the length of the freeze, which the rehearsal measures. Each later v5 rollout restarts the single API pod, because its file-storage volume is ReadWriteOnce.
- Database impact: a second database in the shared cluster (`max_connections` 200) for the duration of the migration, plus a temporary copy of about 2.4 GB.
