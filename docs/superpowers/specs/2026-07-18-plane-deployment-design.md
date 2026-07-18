# Plane deployment design — replacing leantime

**Date:** 2026-07-18
**Status:** Approved for planning

## Summary

Replace the leantime project-management app with [Plane](https://plane.so) (Community Edition,
AGPL-3.0), deployed via Plane's own official `plane-ce` Helm chart on the `feather-core` cluster.
Plane goes live at `tasks.onelitefeather.net`; leantime is decommissioned afterward in a separate
follow-up change once Plane is verified working. There is no data migration — this is a fresh
start.

## Context

- leantime today (`apps/base/leantime`, `apps/clusters/feathre-core/base-apps/leantime`) is a
  single container, single replica (1 CPU / 1Gi), backed by its own mariadb-galera database,
  exposed via a Cloudflare Tunnel `Ingress` with an origin TLS cert.
- `feather-core` is a single on-prem/Talos cluster with no multi-AZ topology — every zone-scoped
  resource in the repo uses the single zone value `fr01`. Plane's official "high availability"
  docs (https://developers.plane.so/self-hosting/govern/high-availability) assume a multi-AZ
  cloud cluster (Karpenter, cross-zone LB, 3+ AZs) and do not apply here.
- `helm.plane.so` hosts **two** charts: `plane-enterprise` (Commercial/Enterprise edition,
  licensing unclear from public docs — **not used**) and **`plane-ce`** (Plane Community Edition,
  `official: true` on Artifact Hub, license `AGPL-3.0`, images published under
  `artifacts.plane.so/makeplane/*`). This design uses `plane-ce`.
- Plane Community Edition requires Postgres, Redis (Valkey), RabbitMQ, and S3-compatible object
  storage. It does **not** require OpenSearch — that dependency is irrelevant here and out of
  scope.

## Architecture

Plane is deployed via the official `plane-ce` Helm chart, consumed as an external chart —
matching how this repo already brings in other upstream charts (e.g. Harbor: a `HelmRepository`
source under `infrastructure/clusters/feather-core/base-sources/`, referenced by a `HelmRelease`
with `chart.spec.sourceRef.kind: HelmRepository`). A new `HelmRepository` source is added at
`infrastructure/clusters/feather-core/base-sources/plane.yml` pointing at `https://helm.plane.so/`.
No in-repo chart is authored — `plane-ce` already implements exactly the topology this design
needs (per-component Deployments, external-service support, an optional bundled RabbitMQ), so
hand-rolling an Outline-style chart around the same images would only duplicate it.

New namespace: `plane`. `plane-ce`'s own `ingress.enabled` is set to `false` (its bundled
Ingress/Traefik templates target `nginx`/`traefik`, not this cluster's Cloudflare Tunnel
ingress). Instead, a hand-written Cloudflare Tunnel `Ingress` (`ingressClassName:
cloudflare-tunnel`) is added at **`tasks.onelitefeather.net`**, routing to the chart's generated
per-component Services, following the chart's own documented route table:

| Path | Routes to (chart-generated Service) |
|---|---|
| `/` | `<release>-web:3000` |
| `/spaces/*` | `<release>-space:3000` |
| `/god-mode/*` | `<release>-admin:3000` |
| `/live/*` | `<release>-live:3000` |
| `/api/*` | `<release>-api:8000` |
| `/auth/*` | `<release>-api:8000` |

(The chart's `/uploads/*` route only applies to its bundled MinIO, which this design doesn't use —
see Object storage below.) This mirrors the multi-path `Ingress` pattern already used for Outline
(`apps/clusters/feathre-core/base-apps/outline/ingress.yaml`).

## Components

`plane-ce` deploys one Deployment per component: `web`, `space`, `admin`, `api`, `live`,
`worker`, `beatworker`. Every component's chart default is **`replicas: 1`**, which matches the
conservative day-one scale already decided — no values override needed to get there. `beatworker`
must stay at 1 (the chart doesn't expose per-component autoscaling, so this isn't a foot-gun).
Migrations run as a chart-managed Job (`--wait-for-jobs` in the install/upgrade command waits for
it), not a component we manage directly.

Components are configured to use the images pinned by `planeVersion` (chart value, default
`v1.3.1` — confirm the current stable release tag at implementation time). Resource
requests/limits per component default small (e.g. `web`: 50m/500m CPU, 50Mi/1000Mi memory) and
are usable as-is initially; revisit only if a component is observed to be under-resourced.

## Data dependencies

The `plane-ce` chart's `local_setup` flag per dependency controls whether the chart deploys its
own stateful instance or expects an external one. This design sets it per-dependency as follows.

### Postgres — reuse shared CNPG cluster (`postgres.local_setup: false`)

New `plane` role and `Database` CR added under
`infrastructure/clusters/feather-core/configs/postgresql/`, following the existing per-app
pattern (see `database/outline.yaml`, `database/harbor.yaml`):

1. Add a role to `cluster.yaml` `spec.managed.roles` (`login: true`, `passwordSecret: role-plane`).
2. Add a `role-plane` `secretGenerator` entry in `kustomization.yaml`, sourced from a new
   `roles/plane.sops.env`, labeled `cnpg.io/reload: "true"`.
3. Add a `postgresql.cnpg.io/v1 Database` CR at `database/plane.yaml` (owner `plane`, cluster
   `feather-core-cluster-pg`).

The chart takes the full connection string via `env.pgdb_remote_url` (`DATABASE_URL`, e.g.
`postgresql://plane:<password>@<host>:5432/plane`). Connect through the existing PgBouncer
`Pooler` (`feather-core-cluster-pg-pooler-rw.cnpg-system.svc.cluster.local:5432`, session mode)
by default. `n8n` bypasses this pooler and connects directly to the `-rw` service because
PgBouncer session mode rejects a `statement_timeout` startup parameter that n8n's client sends
(see `apps/clusters/feathre-core/base-apps/n8n/release.yaml`). Verify at implementation time
whether Django/psycopg behaves the same way; if it does, fall back to the same direct-connection
pattern n8n uses.

### Redis — reuse shared Dragonfly (`redis.local_setup: false`)

Plane CE's Redis/Valkey usage (caching/sessions) is satisfied by the existing shared Dragonfly
instance (`dragonfly.dragonfly.svc.cluster.local:6379`), selecting an unused DB number rather
than deploying new infrastructure. The chart takes this via `env.remote_redis_url`
(`REDIS_URL`). Based on currently live allocations found in the repo (Harbor: 0,1,2,5,6,7;
shlink: 8; Outline: 9,10; n8n: 11), **DB 12** is the next free slot — confirm against the live
cluster state at implementation time before assigning, since the DB-allocation tracking doc
(`docs/dragonfly-redis-cutover.md`) was removed from the tree at some point after being written.
This design includes restoring/updating that allocation doc with Plane's entry.

### RabbitMQ — chart-bundled, Plane-scoped (`rabbitmq.local_setup: true`)

Not previously used anywhere in this cluster. Rather than hand-writing a separate Deployment, the
chart's own bundled RabbitMQ stateful deployment is used as-is (`rabbitmq.local_setup: true`),
pointed at the cluster's storage class (`rabbitmq.storageClass: ceph-rbd-fr01`). This still
satisfies the "Plane-scoped, no operator, no clustering" decision — it's a single instance
living inside the `plane` namespace as part of this release, removed automatically if Plane is
ever decommissioned. Plane's architecture tolerates a broker outage gracefully (API/web stay up;
Celery tasks queue until the broker returns), so a single non-HA instance is acceptable.

### Object storage — reuse Rook Ceph RGW (`minio.local_setup: false`)

New `CephObjectStoreUser` at `infrastructure/clusters/feather-core/rook-fr01/users/plane.yaml`,
following the existing per-app pattern (see `users/outline.yaml`, `users/harbor.yaml`). Generated
access key/secret passed to the chart via `env.aws_access_key` / `env.aws_secret_access_key` /
`env.aws_region` / `env.aws_s3_endpoint_url` (internal RGW endpoint
`http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80`, not the public Cloudflare-tunneled one)
and `env.docstore_bucket`; the bucket is created automatically on first write, per the convention
documented in `docs/buckets.md`.

## Secrets & overlay structure

Mirrors the existing leantime/outline layout, adapted for an external chart:

- `apps/base/plane/` — `namespace.yaml`, `kustomization.yaml`, `release.yaml` (default/portable
  HelmRelease referencing `chart: plane-ce` / `sourceRef: {kind: HelmRepository, name: plane,
  namespace: flux-system}`, matching the Harbor `HelmRelease` pattern).
- `infrastructure/clusters/feather-core/base-sources/plane.yml` — new `HelmRepository` pointing
  at `https://helm.plane.so/`, registered in that directory's `kustomization.yaml`.
- `apps/clusters/feathre-core/base-apps/plane/` — `ingress.yaml` (Cloudflare Tunnel Ingress at
  `tasks.onelitefeather.net`, multi-path per the route table above), `kustomization.yaml`
  (secretGenerators for the chart's expected secrets and `cf-origin-tls`), `release.yaml` (patch
  with concrete `values:` — `planeVersion`, `local_setup` flags, `env.*` remote-service URLs,
  `ingress.enabled: false`, `priorityClassName: feather-standard`).
- Per the chart's "External Secrets Config" table, secrets are split across the existing
  per-app SOPS convention: one `plane.sops.env` (or several, if clearer) providing
  `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS` (only needed as chart-generated defaults since
  RabbitMQ is chart-bundled), `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_S3_BUCKET_NAME`,
  `SECRET_KEY`, `REDIS_URL`, and `DATABASE_URL`. Exact secret names/keys are confirmed against
  the chart's `values.yaml` `*_existingSecret` fields at implementation time.

## Rollout plan

**Phase 1 (this work):** deploy Plane fresh alongside leantime, no data migration, verify
manually (login, project creation, file upload, realtime collaboration) before treating it as the
system of record.

**Phase 2 (separate follow-up change, after a burn-in period):** remove leantime's `HelmRelease`,
base/overlay manifests, its mariadb-galera database/grants/user/password, and its namespace and
secrets from the repo.

## Explicitly out of scope

- OpenSearch — not required by Plane Community Edition.
- Multi-AZ / Karpenter-specific guidance from Plane's official HA doc — this cluster has no AZ
  topology to spread across.
- The `plane-enterprise` Helm chart — licensing for that path is unclear and it is avoided in
  favor of the official free `plane-ce` chart, which covers the same deployment need.
- Data migration from leantime — this is a fresh start, not a data carryover.
