# Plane CE → EE cutover design

**Date:** 2026-07-18
**Status:** Approved for planning
**Supersedes:** the "`plane-enterprise` — licensing unclear, not used" decision in
`docs/superpowers/specs/2026-07-18-plane-deployment-design.md`. That decision is reversed here:
the operator now holds a Plane Enterprise license and wants the Commercial edition's feature set
(SSO/permissions/governance, the Pi AI assistant, Silo integrations).

## Summary

Swap the existing `plane` HelmRelease in the `plane` namespace from the `plane-ce` chart
(Community Edition, live at `tasks.onelitefeather.net` since ~2026-07-18T12:00Z) to the
`plane-enterprise` chart (Commercial/Enterprise Edition), in place, same namespace, same release
name, same hostname. This is a **fresh start, not a data migration**: Plane's own docs confirm
CE→EE is not an in-place schema upgrade (it's backup/restore into a separate instance, with no
official Kubernetes tooling — Docker-compose only), and the CE instance has only been live for a
couple of hours with no real user data worth preserving. The existing `plane` CNPG database is
dropped and recreated empty; EE's own migrator job builds its schema from scratch.

Chosen over a side-by-side verify-then-cutover rollout (the pattern used for the original
leantime→Plane CE switch) because the operator explicitly accepted the downtime-risk tradeoff for
a single, faster cutover.

## Context

- Confirmed via Docker Hub API: all `makeplane/*-commercial` images (backend, web, space, admin,
  live, monitor, silo, pi, node-runner, email) are **public** — no registry credentials needed,
  `dockerRegistry.enabled` stays `false`.
- `helm.plane.so` hosts a second chart, `plane-enterprise` (pulled and inspected locally:
  `plane-enterprise@3.0.0`, appVersion `3.0.0` — a major jump from CE's `v1.3.1`). Its values
  schema is unrelated to `plane-ce`'s (`services.postgres.local_setup` vs `postgres.local_setup`,
  different secret names, a much larger component set) — this is a rebuild of the release, not a
  values tweak.
- The EE chart requires a license, activated post-install through the admin UI against
  `https://prime.plane.so` (chart value `license.licenseDomain`). No license key value is set in
  this design — left as a placeholder secret field, filled in via `sops` separately.
- Existing infra this design reuses: the shared CNPG cluster (`plane` role/database already
  provisioned), the shared Dragonfly instance (Plane already holds **Redis DB 12**, per
  `docs/dragonfly-redis-cutover.md`), and the Rook RGW bucket + `CephObjectStoreUser` (`plane`,
  already provisioned under `infrastructure/clusters/feather-core/rook-fr01/`). None of these are
  re-provisioned — EE points at the same role/bucket/DB-slot as CE did.
- No shared OpenSearch/Elasticsearch exists anywhere in the cluster (EE's search backend
  dependency) — bundled locally by the chart, same pattern as the already-bundled RabbitMQ.

## Architecture

- Chart source: add a new `HelmRepository` `plane-ee` (or repoint the existing `plane` source —
  confirmed at implementation time) at `infrastructure/clusters/feather-core/base-sources/`,
  still pointing at `https://helm.plane.so/`, since `plane-enterprise` lives in the same repo
  index as `plane-ce`.
- `apps/base/plane/release.yaml`: chart spec changes from `plane-ce@1.6.0` to
  `plane-enterprise@3.0.0`.
- `apps/clusters/feathre-core/base-apps/plane/release.yaml`: values block is rewritten against
  the EE schema (see Data dependencies below); existing `postRenderers` patches
  (`priorityClassName: feather-standard` on Deployment/StatefulSet) carry over as-is since they
  target generic `kind:`-based selectors, not CE-specific resource names.
- Ingress (`apps/clusters/feathre-core/base-apps/plane/ingress.yaml`): route table re-verified
  against the EE chart's generated Services at implementation time — EE adds `space`/`admin`
  Services under the same names as CE so the existing multi-path Cloudflare Tunnel Ingress
  (`/`, `/spaces/*`, `/god-mode/*`, `/live/*`, `/api/*`, `/auth/*`) is expected to carry over
  unchanged, but this is confirmed by rendering the chart, not assumed.
- The two CE-specific bugfix patches recorded in the CE design/git history (Celery
  forking-per-core on the worker container's entrypoint, and the hardcoded `http://` `WEB_URL` in
  the `app-vars` ConfigMap) are **re-verified against the EE chart's rendered manifests** at
  implementation time before deciding whether to re-apply them — the EE chart's worker
  entrypoint and config-secret templates are different files and may not have the same defects.

## Components

Enabled (EE chart component flags), matching CE parity plus the two feature areas the operator
asked for:

| Component | Enabled | Why |
|---|---|---|
| `web`, `space`, `admin`, `api`, `live`, `worker`, `beatworker` | yes (chart default) | CE parity — core app |
| `monitor` | yes (chart default, always-on) | ships unconditionally in this chart |
| `silo` | yes (chart default) | integrations (GitHub/GitLab/Slack/Sentry) — explicitly requested; individual connectors (`silo.connectors.*`) stay disabled until OAuth app credentials are supplied |
| `pi`, `pi_beat_worker`, `pi_worker` | yes (explicit override — chart default is `false`) | AI assistant — explicitly requested; no AI provider key is configured yet (see Secrets), so Pi's pods run but AI calls no-op until a key is added |

Left disabled (chart default `false`, none explicitly requested): `automation_consumer`,
`webhook_consumer`, `outbox_poller`, `runner`, `external_api`, `worker_importers`,
`email_service`, `iframely`. Each is a single `enabled: true` flip in a follow-up change; leaving
them off keeps the shared CNPG connection footprint smaller for this cutover (see Data
dependencies — Postgres). `automation_consumer`/`webhook_consumer` also depend on
`outbox_poller`, so enabling one later means enabling all three together.

## Data dependencies

### Postgres — reuse shared CNPG cluster (`services.postgres.local_setup: false`)

The existing `plane` role and `Database` CR
(`infrastructure/clusters/feather-core/configs/postgresql/{roles,database}/plane*`) are reused
as-is — same role, same connection secret shape. As part of the cutover, the `plane` database's
contents are dropped and recreated empty (fresh start, not preserved) before EE's migrator job
runs its own schema migrations from scratch.

**New:** Pi requires its own database (`env.pg_pi_db_name`, default `plane_pi`). A second CNPG
`Database` CR (`database/plane-pi.yaml`) is added, owned by the same `plane` role, following the
same per-app pattern as the primary `plane` database.

### Redis — reuse shared Dragonfly, same slot (`env.remote_redis_url`)

Reuses **DB 12**, the slot already allocated to Plane in `docs/dragonfly-redis-cutover.md` — no
new allocation needed, the DB is just logically reset since nothing is preserved across the
cutover.

### RabbitMQ — chart-bundled, unchanged (`services.rabbitmq.local_setup: true`)

Same as CE: no shared RabbitMQ exists in the cluster, so the chart's own bundled instance is kept,
pointed at `ceph-rbd-fr01`.

### Object storage — reuse Rook Ceph RGW (`services.minio.local_setup: false`)

Reuses the existing `CephObjectStoreUser` and bucket (`plane`,
`infrastructure/clusters/feather-core/rook-fr01/{buckets,users}/plane.yaml`) — same access
key/secret, same internal RGW endpoint. Bucket contents are not wiped (irrelevant — CE never
had meaningful uploads in its ~2-hour lifetime), but nothing carries over logically since the
whole Postgres-tracked file index is being rebuilt from an empty database anyway.

### OpenSearch — chart-bundled, new (`services.opensearch.local_setup: true`)

No shared OpenSearch exists. Bundled locally by the chart on a new PVC (`ceph-rbd-fr01`), same
reasoning as RabbitMQ: Plane-scoped, removed automatically if Plane is ever decommissioned.

## Secrets

Extends `apps/clusters/feathre-core/base-apps/plane/plane.sops.env` (or splits into additional
per-component env files if the chart's `external_secrets.*_existingSecret` fields don't map
cleanly onto the existing single-file `secretGenerator` layout — confirmed at implementation
time) with the fields the EE chart's `external_secrets` block expects beyond what CE already
provides: `pgdb_existingSecret` (now needs both the `plane` and `plane_pi` connection strings),
`opensearch_existingSecret`, `silo_env_existingSecret`, `pi_api_env_existingSecret`.

Two fields are added as **placeholders**, to be set via `sops` directly (not passed to the
assistant):
- EE license (`license.licenseDomain` / the license key itself, activated through the admin UI
  against `prime.plane.so`).
- Pi's AI provider credential (one of `pi.ai_providers.{openai,claude,groq,cohere}.api_key`) —
  none was available at design time; Pi pods deploy and run without one, but AI features no-op
  until a key is set.

## Cutover sequence

1. Remove the CE `HelmRelease` values/chart-spec content; point `chart.spec.chart` at
   `plane-enterprise` version `3.0.0` (or repoint/add the `HelmRepository` source, per
   Architecture above).
2. Add the `plane-pi` CNPG `Database` CR; extend SOPS secrets and the `plane` overlay's
   `kustomization.yaml` `secretGenerator` entries for the new `*_existingSecret` fields.
3. Rewrite `apps/clusters/feathre-core/base-apps/plane/release.yaml` values against the EE
   schema per Components/Data dependencies above; re-verify and re-apply (or drop) the two
   CE-specific `postRenderers` bugfix patches based on the EE chart's actual rendered manifests.
4. Re-verify the Ingress route table against the EE chart's rendered Services; update
   `ingress.yaml` if paths changed.
5. Drop and recreate the `plane` Postgres database's contents (fresh start).
6. Commit, push, `flux reconcile` — accept the downtime window on `tasks.onelitefeather.net`
   while EE installs; no maintenance page or traffic diversion is set up first (operator-accepted
   tradeoff for a single-shot cutover).
7. Verify: log in, activate the EE license via the admin UI, confirm core CRUD (issues/projects),
   confirm Silo's integrations page loads, confirm Pi's chat UI loads (AI replies will no-op
   without a provider key).

## Explicitly out of scope

- Preserving any data from the CE instance — confirmed unnecessary (fresh, ~2-hour-old
  deployment, no real usage yet).
- `automation_consumer`, `webhook_consumer`, `outbox_poller`, `runner`, `external_api`,
  `worker_importers`, `email_service`, `iframely` — none requested; left at chart defaults
  (disabled), each a follow-up `enabled: true` flip.
- Configuring Silo's individual connectors (GitHub/GitLab/Slack/Sentry OAuth apps) — the
  component is enabled, but no OAuth credentials were provided; connectors stay off until
  supplied.
- Configuring a working Pi AI provider — component is enabled, but no provider API key was
  available; follow-up once one is supplied.
- A side-by-side verify-then-cutover rollout — considered and rejected in favor of a single
  in-place cutover per the operator's explicit choice.
