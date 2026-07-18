# Plane CE → EE cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Swap the live `plane` HelmRelease in the `plane` namespace from the `plane-ce` chart to the `plane-enterprise` chart (v3.0.0), in place, reusing the existing CNPG role/database, Dragonfly Redis DB 12, and Rook RGW bucket, with OpenSearch bundled locally and Silo/Pi enabled.

**Architecture:** This is a GitOps repo (FluxCD) — every change is a manifest edit under `apps/` and `infrastructure/`, committed and pushed to `main`; Flux reconciles the cluster automatically. There is no application code and no test runner — "testing" a task means rendering the manifests locally (`kubectl kustomize`, `helm show`/`helm template`) and running `./scripts/validate.sh`, then (for the final task) actually pushing and watching `flux get helmrelease -n plane plane` / `kubectl get pods -n plane` converge.

**Tech Stack:** Kustomize, FluxCD (`HelmRelease`/`HelmRepository`/`Kustomization` CRs), CloudNativePG (`Database` CR), SOPS (PGP), Helm chart `plane-enterprise@3.0.0` from `https://helm.plane.so/`.

## Global Constraints

- Same namespace (`plane`), same release name (`plane`), same hostname (`tasks.onelitefeather.net`) — in-place swap, no side-by-side verification (operator-accepted downtime tradeoff).
- Fresh start: the existing `plane` Postgres database's contents are dropped before the EE migrator runs. No data migration.
- Reuse existing infra as-is: CNPG role `plane` / database `plane` (`infrastructure/clusters/feather-core/configs/postgresql/{roles,database}/plane*`), Dragonfly Redis **DB 12** (`docs/dragonfly-redis-allocations.md`), Rook RGW bucket + `CephObjectStoreUser` `plane` (`infrastructure/clusters/feather-core/rook-fr01/{buckets,users}/plane.yaml`).
- New: a second CNPG database `plane_pi` (Pi's dedicated DB), OpenSearch bundled locally (`services.opensearch.local_setup: true`, PVC on `ceph-rbd-fr01`).
- Components enabled: `web`, `space`, `admin`, `api`, `live`, `worker`, `beatworker`, `monitor` (chart defaults), `silo` (chart default), `pi` + `pi_beat_worker` + `pi_worker` (explicit override — chart default `false`). Everything else (`automation_consumer`, `webhook_consumer`, `outbox_poller`, `runner`, `external_api`, `worker_importers`, `email_service`, `iframely`) stays at its chart default (`false`).
- No AI provider key is configured (none available) — all of `services.pi.ai_providers.*.enabled` stay `false`. Pi's pods run; its chat UI loads; AI replies will error until a key is added later.
- License (`license.licenseDomain`) is **not secret** — it's just the public hostname (confirmed from the chart's `app-env.yaml` template: it feeds `APP_DOMAIN`, `WEB_URL` fallback, CORS origins, and `PRIME_HOST` machine-signature hashing, none of which are sensitive) — set directly in `release.yaml` as `tasks.onelitefeather.net`. The actual license *key* is entered later through the admin UI (`/god-mode`) against `prime.plane.so`, not stored in any chart value or Secret.
- `dockerRegistry.enabled` stays `false` — confirmed via Docker Hub API that all `makeplane/*-commercial` images are public.
- Service names/ports are unchanged from CE (`<release>-web:3000`, `-space:3000`, `-admin:3000`, `-live:3000`, `-api:8000`) — confirmed by reading the EE chart's `workloads/*.yaml` templates. The existing `apps/clusters/feathre-core/base-apps/plane/ingress.yaml` needs **no changes**.
- The EE chart has no values field for `priorityClassName` (confirmed: `plane.podScheduling` helper in `_helpers.tpl` only renders `nodeSelector`/`tolerations`/`affinity`) — the existing `postRenderers` patch targeting generic `kind: Deployment`/`StatefulSet` carries over unchanged.
- The EE chart's `app-env.yaml` template has a direct `env.web_url` values field (unlike CE, which hardcoded `http://` with no override) — set `env.web_url: "https://tasks.onelitefeather.net"` directly; **the CE `WEB_URL` ConfigMap `postRenderers` patch is dropped**, it's no longer needed.
- The EE chart's worker Deployment is still named `{{ .Release.Name }}-worker-wl` (`plane-worker-wl`) and still execs `./bin/docker-entrypoint-worker.sh` with no concurrency flag — same binary lineage as CE (`makeplane/backend-commercial` vs `makeplane/backend`). The CE Celery-concurrency `postRenderers` patch is kept as a precaution, flagged for post-deploy verification (it may or may not be needed — confirm from worker pod logs/memory after cutover; harmless if the bug doesn't exist, since it just pins concurrency to 2 either way).

---

### Task 1: Point the HelmRelease at `plane-enterprise` and confirm the chart resolves

**Files:**
- Modify: `apps/base/plane/release.yaml`

**Interfaces:**
- Produces: the base `HelmRelease` spec that `apps/clusters/feathre-core/base-apps/plane/release.yaml` patches with concrete values (Task 4).

- [ ] **Step 1: Change the chart spec**

Edit `apps/base/plane/release.yaml` — replace:

```yaml
    spec:
      chart: plane-ce
      version: "1.6.0"
```

with:

```yaml
    spec:
      chart: plane-enterprise
      version: "3.0.0"
```

- [ ] **Step 2: Confirm the chart version actually exists in the repo**

Run:
```bash
helm repo add plane https://helm.plane.so/ --force-update
helm show chart plane/plane-enterprise --version 3.0.0
```
Expected: prints the chart's `Chart.yaml` (`name: plane-enterprise`, `version: 3.0.0`, `appVersion: 3.0.0`), no error.

- [ ] **Step 3: Render the base kustomization to confirm the HelmRelease is structurally valid**

Run:
```bash
kubectl kustomize apps/base/plane
```
Expected: a `Namespace` and one `HelmRelease` (`chart: plane-enterprise`, `version: "3.0.0"`) — no kustomize errors.

- [ ] **Step 4: Commit**

```bash
git add apps/base/plane/release.yaml
git commit -m "feat(plane): point base HelmRelease at plane-enterprise@3.0.0"
```

---

### Task 2: Add the `plane_pi` CNPG database

**Files:**
- Create: `infrastructure/clusters/feather-core/configs/postgresql/database/plane-pi.yaml`
- Modify: `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`

**Interfaces:**
- Produces: a second database (`plane_pi`) on `feather-core-cluster-pg`, owned by the existing `plane` role — consumed by Task 4's `env.pg_pi_db_remote_url`.

- [ ] **Step 1: Create the Database CR**

Create `infrastructure/clusters/feather-core/configs/postgresql/database/plane-pi.yaml`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: plane-pi
spec:
  name: plane_pi
  owner: plane
  cluster:
    name: feather-core-cluster-pg
```

- [ ] **Step 2: Register it in the postgresql kustomization**

In `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`, add a line right after `- database/plane.yaml` in the `resources:` list:

```yaml
  - database/plane.yaml
  - database/plane-pi.yaml
```

- [ ] **Step 3: Render and confirm**

Run:
```bash
kubectl kustomize infrastructure/clusters/feather-core/configs/postgresql | grep -A6 "name: plane-pi"
```
Expected:
```
  name: plane-pi
spec:
  name: plane_pi
  owner: plane
  cluster:
    name: feather-core-cluster-pg
```

- [ ] **Step 4: Commit**

```bash
git add infrastructure/clusters/feather-core/configs/postgresql/database/plane-pi.yaml infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml
git commit -m "feat(plane): provision plane_pi database for Plane EE's Pi assistant"
```

---

### Task 3: Extend the SOPS secret with the new fields EE needs

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/plane/plane.sops.env` (manual `sops` edit — see Step 1)

**Interfaces:**
- Produces: new keys `OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD`, `SILO_HMAC_SECRET_KEY`, `AES_SECRET_KEY`, `CURSOR_WEBHOOK_SECRET`, `PI_INTERNAL_SECRET`, `PG_PI_DATABASE_URL` — consumed by the new secretGenerator entries in Task 4.

**⚠️ This step requires manual execution — automated `sops -d`/decrypt access is blocked by this environment's policy classifier.** Whoever executes this plan runs the command themselves.

- [ ] **Step 1: Open the file in `sops` and add the new keys**

Run:
```bash
sops apps/clusters/feathre-core/base-apps/plane/plane.sops.env
```

This decrypts the file into `$EDITOR`. The file already has `SECRET_KEY`, `LIVE_SERVER_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, `AMQP_URL`, `FILE_SIZE_LIMIT`, `AWS_S3_BUCKET_NAME`, `USE_MINIO`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL`, `AWS_REGION` — leave every existing key untouched (all reused as-is by EE) and append these new lines:

```env
OPENSEARCH_USERNAME=plane
OPENSEARCH_PASSWORD=vlxgDFexLUijETA6gzEs16sEtk6PzzE
SILO_HMAC_SECRET_KEY=GeH7WsGSYqeFSywLTs1MHX5fvojHe4THTml9s0ms
AES_SECRET_KEY=b88f3d7508b026eb309ffed48829f28d
CURSOR_WEBHOOK_SECRET=HswbnF1oIy3O1NFy4HovUCc4J2kgEm
PI_INTERNAL_SECRET=iQGD5aJzbFxviMq8ILBan2kkIYLLsDF4uN8GhA2D4Fo
```

For `PG_PI_DATABASE_URL`, copy the existing `DATABASE_URL` value's host/user/password segment and swap only the database name to `plane_pi`. E.g. if the current line reads:
```env
DATABASE_URL=postgresql://plane:<password>@<host>:5432/plane
```
add:
```env
PG_PI_DATABASE_URL=postgresql://plane:<password>@<host>:5432/plane_pi
```
(same host/port/user/password as `DATABASE_URL`, only the trailing path segment changes.)

Save and exit — `sops` re-encrypts the file in place.

- [ ] **Step 2: Confirm the file re-encrypted cleanly**

Run:
```bash
git diff --stat apps/clusters/feathre-core/base-apps/plane/plane.sops.env
```
Expected: the file shows as modified (new `ENC[...]` blocks appended; `sops_lastmodified` and `sops_mac` updated). Run `git diff` and confirm no line was deleted, only added.

- [ ] **Step 3: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/plane/plane.sops.env
git commit -m "feat(plane): add OpenSearch/Silo/Pi secrets for EE cutover"
```

---

### Task 4: Rewrite the `plane` overlay's HelmRelease values for the EE schema

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/plane/release.yaml`
- Modify: `apps/clusters/feathre-core/base-apps/plane/kustomization.yaml`

**Interfaces:**
- Consumes: SOPS keys from Task 3 (`OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD`, `SILO_HMAC_SECRET_KEY`, `AES_SECRET_KEY`, `CURSOR_WEBHOOK_SECRET`, `PI_INTERNAL_SECRET`, `PG_PI_DATABASE_URL`); reuses existing keys (`DATABASE_URL`, `REDIS_URL`, `AMQP_URL` via `plane-app-env`, and `plane-doc-store`/`plane-rabbitmq`'s existing keys).
- Produces: the final `HelmRelease` Flux applies.

- [ ] **Step 1: Add the new secretGenerator entries**

In `apps/clusters/feathre-core/base-apps/plane/kustomization.yaml`, extend `secretGenerator:` (keep all four existing entries unchanged, add three more, all sourced from the same `plane.sops.env`):

```yaml
secretGenerator:
  - name: plane-app-env
    envs:
      - plane.sops.env
  - name: plane-doc-store
    envs:
      - plane.sops.env
  - name: plane-live-env
    envs:
      - plane.sops.env
  - name: plane-rabbitmq
    envs:
      - plane.sops.env
  - name: plane-opensearch
    envs:
      - plane.sops.env
  - name: plane-silo
    envs:
      - plane.sops.env
  - name: plane-pi-api
    envs:
      - plane.sops.env
  - name: cf-origin-tls
    type: kubernetes.io/tls
    files:
      - tls.crt=cf-origin-tls.sops.crt
      - tls.key=cf-origin-tls.sops.key
```

(All seven app secrets are generated from the same single `plane.sops.env` file — each Kubernetes Secret ends up containing every key from that file, which is harmless: the chart's `envFrom.secretRef` per-component only reads the keys it cares about, extra keys in a Secret are just ignored. This matches the existing CE convention of reusing one file across `plane-app-env`/`plane-doc-store`/`plane-live-env`/`plane-rabbitmq` already.)

- [ ] **Step 2: Rewrite `release.yaml`'s values block**

Replace the full content of `apps/clusters/feathre-core/base-apps/plane/release.yaml` with:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: plane
  namespace: plane
spec:
  postRenderers:
    - kustomize:
        patches:
          - target:
              kind: Deployment
            patch: |
              - op: add
                path: /spec/template/spec/priorityClassName
                value: feather-standard
          # The chart renders RabbitMQ, Postgres (unused here), and OpenSearch as
          # StatefulSets, not Deployments, so they need their own patch target to
          # get the same priority class.
          - target:
              kind: StatefulSet
            patch: |
              - op: add
                path: /spec/template/spec/priorityClassName
                value: feather-standard
          # Carried over from the CE cutover: the shared makeplane/backend* image's
          # docker-entrypoint-worker.sh runs `celery -A plane worker` with no
          # --concurrency flag, forking one process per *node* CPU core (32 here)
          # regardless of cpuLimit. Kept as a precaution since this chart's worker
          # Deployment (plane-worker-wl) execs the same-named entrypoint script on
          # the "-commercial" variant of the same backend image; verify after
          # cutover whether the bug still applies (harmless either way — this pins
          # concurrency to 2 regardless).
          - target:
              kind: Deployment
              name: plane-worker-wl
            patch: |
              - op: replace
                path: /spec/template/spec/containers/0/command
                value: ["/bin/bash", "-c"]
              - op: add
                path: /spec/template/spec/containers/0/args
                value: ["python manage.py wait_for_db && python manage.py wait_for_migrations && celery -A plane worker -l info --concurrency=2"]
  values:
    planeVersion: v3.0.0

    ingress:
      enabled: false

    license:
      licenseDomain: tasks.onelitefeather.net

    services:
      postgres:
        local_setup: false
      redis:
        local_setup: false
      minio:
        local_setup: false
      rabbitmq:
        local_setup: true
        storageClass: ceph-rbd-fr01
      opensearch:
        local_setup: true

      web:
        assign_cluster_ip: true
      space:
        assign_cluster_ip: true
      admin:
        assign_cluster_ip: true
      api:
        assign_cluster_ip: true
      live:
        assign_cluster_ip: true

      # Same reasoning as the CE cutover: chart default (1000Mi) OOMKilled the
      # worker repeatedly under real load. Bump both async components.
      worker:
        memoryLimit: 2000Mi
        cpuLimit: "1"
      beatworker:
        memoryLimit: 1500Mi
        cpuLimit: 500m

      silo:
        enabled: true

      pi:
        enabled: true
      pi_beat_worker: {}
      pi_worker: {}

    external_secrets:
      app_env_existingSecret: plane-app-env
      doc_store_existingSecret: plane-doc-store
      live_env_existingSecret: plane-live-env
      rabbitmq_existingSecret: plane-rabbitmq
      opensearch_existingSecret: plane-opensearch
      silo_env_existingSecret: plane-silo
      pi_api_env_existingSecret: plane-pi-api

    env:
      docstore_bucket: plane
      cors_allowed_origins: "https://tasks.onelitefeather.net"
      default_cluster_domain: cluster.local
      web_url: "https://tasks.onelitefeather.net"
      pg_pi_db_name: plane_pi
```

- [ ] **Step 3: Render the full overlay**

Run:
```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/plane
```
Expected: no kustomize errors; output includes the `HelmRelease` with `chart: plane-enterprise`, the `Ingress` (`plane-cloudflare-tunnel`) unchanged, and 7 new/existing `Secret` stubs (`plane-app-env`, `plane-doc-store`, `plane-live-env`, `plane-rabbitmq`, `plane-opensearch`, `plane-silo`, `plane-pi-api`, `cf-origin-tls`) — kustomize won't inflate the Helm chart itself (Flux does that at apply time), so you won't see the chart's own Deployments/Services here; that's expected.

- [ ] **Step 4: Run repo-wide validation**

Run:
```bash
./scripts/validate.sh
```
Expected: passes (exits 0) — this strips SOPS-encrypted patches before validating, so it doesn't need the GPG key, and it runs `kustomize build` + `kubeconform` over every Flux path in the repo, catching any YAML/schema errors introduced by this task.

- [ ] **Step 5: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/plane/release.yaml apps/clusters/feathre-core/base-apps/plane/kustomization.yaml
git commit -m "feat(plane): rewrite HelmRelease values for the plane-enterprise schema"
```

---

### Task 5: Cut over — drop CE's data, push, reconcile, verify

This task is the actual production change. Steps 1 and 2 are destructive against the live cluster — confirm with the operator immediately before running them, even though this was already agreed in the design/brainstorming phase (in-place swap, fresh start, no data preservation).

**Files:** none (operational steps only).

- [ ] **Step 1: Drop the existing `plane` database's contents**

The `plane` CNPG role/database stay provisioned (Task 2's sibling, already existing) — only the *contents* are wiped so EE's migrator starts from an empty schema. Find the CNPG primary pod and run:

```bash
kubectl -n cnpg-system get pods -l cnpg.io/cluster=feather-core-cluster-pg,cnpg.io/instanceRole=primary
kubectl -n cnpg-system exec -it <primary-pod-name> -- psql -U postgres -d plane -c \
  "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO plane;"
```
Expected: `DROP SCHEMA` / `CREATE SCHEMA` / `GRANT` printed, no errors.

- [ ] **Step 2: Push and reconcile**

```bash
git push
flux reconcile kustomization base-apps --with-source
flux reconcile kustomization configs --with-source
```
(`base-apps` owns the `plane` HelmRelease/Ingress/secrets; `configs` owns the new `plane_pi` CNPG `Database`.)

- [ ] **Step 3: Watch the rollout**

```bash
flux get helmrelease -n plane plane
kubectl get pods -n plane -w
```
Expected: `plane` HelmRelease eventually reports `Ready: True` with a message like `Helm upgrade succeeded for release plane/plane.v<N> with chart plane-enterprise@3.0.0`. Pods to expect: `plane-web-*`, `plane-space-*`, `plane-admin-*`, `plane-api-*`, `plane-live-*`, `plane-worker-*`, `plane-beatworker-*`, `plane-monitor-*`, `plane-silo-*`, `plane-pi-api-*`, `plane-pi-beat-*`, `plane-pi-worker-*`, `plane-opensearch-0`, `plane-rabbitmq-0`, plus one-shot `plane-api-migrate-*` and `plane-pi-api-migrate-*` Jobs (`Completed`).

- [ ] **Step 4: Verify the app**

- Open `https://tasks.onelitefeather.net` — should load the EE login page (fresh instance, no existing users — create the first admin account).
- Log in, create a test project/issue — confirms Postgres + RabbitMQ + worker are functioning.
- Open `/god-mode` (admin) — confirms the `admin` component and its Service/Ingress path work; this is also where the EE license key gets activated against `prime.plane.so`.
- Open the integrations/Silo settings page — confirms the `silo` component is up (connectors themselves are unconfigured — expected, no OAuth credentials were provided).
- Open Pi's chat UI — confirms the `pi`/`pi-api` component is up; AI replies are expected to error (no provider key configured yet — tracked as follow-up work, not part of this cutover).

- [ ] **Step 5: Activate the license**

In `/god-mode`, follow Plane's license-activation flow (enters the actual license key, validated against `https://prime.plane.so` using the `APP_DOMAIN`/`PRIME_HOST` values already set from `license.licenseDomain`). No repo change needed for this step — it's stored by Plane itself, not in this repo.

---

## Self-Review

**Spec coverage:**
- Architecture (chart swap, same namespace/release/hostname) → Task 1, 4.
- Components table (web/space/admin/api/live/worker/beatworker/monitor/silo/pi enabled; rest default-off) → Task 4 (silo/pi explicit; everything else simply isn't mentioned, which leaves it at chart default `false`).
- Postgres reuse + new `plane_pi` DB → Task 2, 4 (`env.pg_pi_db_name`, `PG_PI_DATABASE_URL`).
- Redis DB 12 reuse → Task 4 (`services.redis.local_setup: false`, existing `REDIS_URL` secret key reused unchanged).
- RabbitMQ bundled, unchanged → Task 4 (`services.rabbitmq.local_setup: true`, existing `plane-rabbitmq` secret reused unchanged).
- S3 reuse → Task 4 (`services.minio.local_setup: false`, existing `plane-doc-store` secret reused unchanged).
- OpenSearch bundled locally → Task 3 (creds), Task 4 (`services.opensearch.local_setup: true`, `plane-opensearch` secret).
- License handling (not secret, placeholder key via UI) → Task 4 (`license.licenseDomain` plain value), Task 5 Step 5 (UI activation).
- Pi AI provider left unconfigured → Global Constraints + Task 5 Step 4 (documented expectation).
- Carried-over CE fixes re-verified → Global Constraints (WEB_URL fix dropped/superseded by `env.web_url`; priorityClassName and worker-concurrency patches kept).
- Ingress route table re-verified, unchanged → Global Constraints (ports/names confirmed identical from chart templates).
- Fresh start / drop existing data → Task 5 Step 1.
- Cutover sequence → Task 5 in full.

**Placeholder scan:** no TBD/TODO; the one open item (AI provider key) is explicitly called out as intentionally deferred, not a placeholder standing in for missing plan content.

**Type/name consistency:** secret names (`plane-app-env`, `plane-doc-store`, `plane-live-env`, `plane-rabbitmq`, `plane-opensearch`, `plane-silo`, `plane-pi-api`) match 1:1 between Task 4's `kustomization.yaml` secretGenerator names and its `release.yaml` `external_secrets.*_existingSecret` values. `plane-worker-wl` Deployment name matches between the design doc, Global Constraints, and Task 4's `postRenderers` patch target. `plane_pi` database name matches between Task 2's `Database` CR (`spec.name`) and Task 4's `env.pg_pi_db_name`.
