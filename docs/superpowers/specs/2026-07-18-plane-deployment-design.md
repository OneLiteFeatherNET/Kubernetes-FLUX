# Plane deployment design — replacing leantime

**Date:** 2026-07-18
**Status:** Approved for planning

## Summary

Replace the leantime project-management app with [Plane](https://plane.so) (Community Edition,
AGPL-3.0), deployed as a new in-repo Helm chart on the `feather-core` cluster. Plane goes live at
`tasks.onelitefeather.net`; leantime is decommissioned afterward in a separate follow-up change
once Plane is verified working. There is no data migration — this is a fresh start.

## Context

- leantime today (`apps/base/leantime`, `apps/clusters/feathre-core/base-apps/leantime`) is a
  single container, single replica (1 CPU / 1Gi), backed by its own mariadb-galera database,
  exposed via a Cloudflare Tunnel `Ingress` with an origin TLS cert.
- `feather-core` is a single on-prem/Talos cluster with no multi-AZ topology — every zone-scoped
  resource in the repo uses the single zone value `fr01`. Plane's official "high availability"
  docs (https://developers.plane.so/self-hosting/govern/high-availability) assume a multi-AZ
  cloud cluster (Karpenter, cross-zone LB, 3+ AZs) and do not apply here.
- Plane's documented Kubernetes/Helm deployment path (`helm.plane.so`, chart
  `plane/plane-enterprise`, images like `makeplane/backend-commercial`) is the Commercial/
  Enterprise edition. Licensing terms for that path could not be confirmed from public docs, so
  it is **not used**. This design instead hand-builds Kubernetes manifests around the free,
  AGPL-3.0-licensed Community Edition images that the docker-compose deployment uses
  (`makeplane/plane-frontend`, `-admin`, `-space`, `-backend`, `-live`, `-proxy`, or equivalent
  CE image names — confirmed at implementation time from the current `docker-compose.yml` on
  the `master` branch of github.com/makeplane/plane).
- Plane Community Edition's docker-compose stack requires Postgres, Redis (Valkey), RabbitMQ, and
  S3-compatible object storage. It does **not** require OpenSearch — that dependency is
  irrelevant here and out of scope.

## Architecture

One new in-repo Helm chart, `helm/plane`, following the same `components:` map pattern already
established by `helm/outline`: a single chart with one Deployment/Service/PDB/HPA block per
component, so the docker-compose service topology maps directly onto per-component Kubernetes
Deployments without inventing a new templating scheme.

New namespace: `plane`. Exposed publicly via a Cloudflare Tunnel `Ingress`
(`ingressClassName: cloudflare-tunnel`) at **`tasks.onelitefeather.net`**, routing `/` to the
`proxy` component's Service — the same ingress mechanism leantime uses today (see
`apps/clusters/feathre-core/base-apps/leantime/ingress.yaml` for the template).

## Components

All components start at **`replicaCount: 1`**. PDB and autoscaling fields exist in the chart
(mirroring `helm/outline`'s per-component `pdb`/`autoscaling` blocks) but are left at
conservative defaults — matching how leantime itself started as a single small replica. Scaling
any component to 2+ replicas later is a values change, not a chart change, except where noted
below as a hard singleton.

| Component | Image (CE) | Role | Replicas |
|---|---|---|---|
| `web` | `makeplane/plane-frontend` | Main web frontend | 1, scalable |
| `admin` | `makeplane/plane-admin` | Admin/"god mode" panel | 1, scalable |
| `space` | `makeplane/plane-space` | Public spaces UI | 1, scalable |
| `api` | `makeplane/plane-backend` | REST API | 1, scalable |
| `worker` | `makeplane/plane-backend`, cmd `docker-entrypoint-worker.sh` | Celery worker | 1, scalable |
| `beat-worker` | `makeplane/plane-backend`, cmd `docker-entrypoint-beat.sh` | Celery beat scheduler | **1, hard singleton** — no PDB, no autoscaling; running 2+ would double-fire scheduled jobs |
| `live` | `makeplane/plane-live` | Realtime collaboration (websocket) | 1, scalable |
| `proxy` | `makeplane/plane-proxy` (CE) | Ingress-facing nginx router | 1, scalable |
| `migrator` | `makeplane/plane-backend`, cmd `docker-entrypoint-migrator.sh` | DB migrations | Helm hook `Job`, run-once per release, not a Deployment |

Exact CE image repository names and current stable tags are confirmed against the upstream
`docker-compose.yml` at implementation time rather than pinned here, since they may have moved
since this doc was written.

**Why keep the `proxy` component instead of reimplementing routing as native Ingress path
rules** (as was done for Outline's web/collaboration split): Plane's proxy does more than path
routing — it enforces upload size limits and other request handling specific to the CE image.
Reimplementing that in Ingress annotations would duplicate logic Plane already maintains and is
a needless abstraction. The chart runs `proxy` as one more component; the Cloudflare Tunnel
`Ingress` points at it, and internal routing to `web`/`api`/`space`/`admin`/`live` happens inside
that container exactly as it does in the upstream docker-compose stack.

## Data dependencies

### Postgres — reuse shared CNPG cluster

New `plane` role and `Database` CR added under
`infrastructure/clusters/feather-core/configs/postgresql/`, following the existing per-app
pattern (see `database/outline.yaml`, `database/harbor.yaml`):

1. Add a role to `cluster.yaml` `spec.managed.roles` (`login: true`, `passwordSecret: role-plane`).
2. Add a `role-plane` `secretGenerator` entry in `kustomization.yaml`, sourced from a new
   `roles/plane.sops.env`, labeled `cnpg.io/reload: "true"`.
3. Add a `postgresql.cnpg.io/v1 Database` CR at `database/plane.yaml` (owner `plane`, cluster
   `feather-core-cluster-pg`).

Connect through the existing PgBouncer `Pooler`
(`feather-core-cluster-pg-pooler-rw.cnpg-system.svc.cluster.local:5432`, session mode) by
default. `n8n` bypasses this pooler and connects directly to the `-rw` service because PgBouncer
session mode rejects a `statement_timeout` startup parameter that n8n's client sends
(see `apps/clusters/feathre-core/base-apps/n8n/release.yaml`). Verify at implementation time
whether Django/psycopg behaves the same way; if it does, fall back to the same direct-connection
pattern n8n uses.

### Redis — reuse shared Dragonfly

Plane CE's Redis/Valkey usage (caching/sessions) is satisfied by the existing shared Dragonfly
instance (`dragonfly.dragonfly.svc.cluster.local:6379`), selecting an unused DB number rather
than deploying new infrastructure. Based on currently live allocations found in the repo
(Harbor: 0,1,2,5,6,7; shlink: 8; Outline: 9,10; n8n: 11), **DB 12** is the next free slot — confirm
against the live cluster state at implementation time before assigning, since the DB-allocation
tracking doc (`docs/dragonfly-redis-cutover.md`) was removed from the tree at some point after
being written. This design includes restoring/updating that allocation doc with Plane's entry.

### RabbitMQ — net-new, Plane-scoped

Not previously used anywhere in this cluster. Deployed as a single plain `Deployment` + PVC +
`Service` living inside the `plane` namespace (part of the app's own manifests, not
`infrastructure/`), image `rabbitmq:3.13.6-management-alpine` matching the CE default. No
operator, no clustering — a single instance is acceptable because it is scoped entirely to
Plane (nothing else depends on it) and Plane's architecture tolerates a broker outage gracefully
(API/web stay up; Celery tasks queue until the broker returns). If Plane is ever decommissioned,
this instance is removed with it — no shared-infra cleanup needed.

### Object storage — reuse Rook Ceph RGW

New `CephObjectStoreUser` at `infrastructure/clusters/feather-core/rook-fr01/users/plane.yaml`,
following the existing per-app pattern (see `users/outline.yaml`, `users/harbor.yaml`). Generated
access key/secret copied into Plane's SOPS secret; the upload bucket is created automatically on
first write, per the convention documented in `docs/buckets.md`. Uses the internal RGW endpoint
(`http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80`), not the public Cloudflare-tunneled one.

## Secrets & overlay structure

Mirrors the existing leantime/outline layout exactly:

- `apps/base/plane/` — `namespace.yaml`, `kustomization.yaml`, `release.yaml` (default/portable
  HelmRelease pointing at `./helm/plane`).
- `apps/clusters/feathre-core/base-apps/plane/` — `ingress.yaml` (Cloudflare Tunnel Ingress at
  `tasks.onelitefeather.net`), `kustomization.yaml` (secretGenerators for `plane-env` and
  `cf-origin-tls`), `release.yaml` (patch with concrete resources, replica counts,
  `priorityClassName: feather-standard`, and references to the generated secrets).
- One SOPS-encrypted `plane.sops.env` holding all app secrets: Postgres URL pieces, the assigned
  Redis DB number, RabbitMQ credentials, S3 credentials, and Plane's `SECRET_KEY`.

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
- The official `plane-enterprise` Helm chart — licensing for that path is unclear and it is
  avoided in favor of hand-built manifests around the free CE images.
- Data migration from leantime — this is a fresh start, not a data carryover.
