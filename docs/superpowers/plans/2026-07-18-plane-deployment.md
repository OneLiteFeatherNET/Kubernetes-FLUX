# Plane Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Plane (Community Edition, via the official `plane-ce` Helm chart) to the
`feather-core` cluster at `tasks.onelitefeather.net`, wired to existing shared Postgres/Redis/S3
infrastructure plus a chart-bundled RabbitMQ, as a replacement for leantime.

**Architecture:** A new `HelmRepository` source (`helm.plane.so`) feeds a `HelmRelease` that
installs the upstream `plane-ce` chart unmodified — no in-repo chart is authored. External
secrets (Postgres/Redis/S3/RabbitMQ connection strings and app keys) are supplied via the chart's
`external_secrets.*_existingSecret` values, generated from one SOPS-encrypted env file, matching
the existing leantime/outline overlay layout (`apps/base/plane/` + `apps/clusters/feathre-core/base-apps/plane/`).

**Tech Stack:** FluxCD (`HelmRepository`/`HelmRelease`), Kustomize, SOPS/PGP, CloudNativePG,
Dragonfly, Rook Ceph RGW, Cloudflare Tunnel Ingress.

## Global Constraints

- This is a GitOps repo: nothing takes effect until committed and pushed to `main`. No task in
  this plan pushes to `main` — that is an explicit, separate checkpoint (Task 10) requiring the
  user's go-ahead, since it triggers real changes on the live `feather-core` cluster.
- Every new/changed manifest must pass `./scripts/validate.sh` before being considered done.
- Secrets never appear in plaintext in any committed file — only inside `*.sops.env` /
  `*.sops.crt` / `*.sops.key` files, encrypted with `sops` before `git add`.
- Namespace: `plane`. Hostname: `tasks.onelitefeather.net`. Postgres role/DB name: `plane`.
  CephObjectStoreUser/bucket name: `plane`. Dragonfly Redis DB: `12`. Helm release name: `plane`.
- Chart version pin: `plane-ce` chart `1.6.0`, `planeVersion: v1.3.1` (confirm these are still
  the latest stable via `helm search repo makeplane/plane-ce --versions` at execution time — if a
  newer chart/app version exists, use it instead and note the change).

---

### Task 1: Add the Plane HelmRepository source

**Files:**
- Create: `infrastructure/clusters/feather-core/base-sources/plane.yml`
- Modify: `infrastructure/clusters/feather-core/base-sources/kustomization.yaml`

**Interfaces:**
- Produces: a `HelmRepository` named `plane` in namespace `flux-system`, consumed by Task 7's
  `HelmRelease` via `chart.spec.sourceRef: {kind: HelmRepository, name: plane, namespace: flux-system}`.

- [ ] **Step 1: Create the HelmRepository manifest**

```yaml
# infrastructure/clusters/feather-core/base-sources/plane.yml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: plane
  namespace: flux-system
spec:
  interval: 5m
  url: https://helm.plane.so/
```

- [ ] **Step 2: Register it in the base-sources kustomization**

Edit `infrastructure/clusters/feather-core/base-sources/kustomization.yaml`, adding `plane.yml`
to the `resources:` list (append at the end, matching the existing non-alphabetical style):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: flux-system
resources:
  - mariadb-operator.yml
  - cnpg.yml
  - helmcharts.yml
  - node-red.yml
  - reposilite.yml
  - strrl.yml
  - smallstep.yml
  - jetstack.yml
  - spegel.yaml
  - harbor.yml
  - checkmk.yml
  - dependency-track.yml
  - dirsigler.yml
  - envoy.yaml
  - prometheus-stack.yaml
  - grafana.yml
  - rook.yaml
  - n8n.yml
  - ollama.yml
  - descheduler.yml
  - plane.yml
```

- [ ] **Step 3: Validate**

Run: `kubectl kustomize infrastructure/clusters/feather-core/base-sources | grep -A5 "name: plane"`
Expected: the rendered `HelmRepository` for `plane` appears with `url: https://helm.plane.so/`.

- [ ] **Step 4: Commit**

```bash
git add infrastructure/clusters/feather-core/base-sources/plane.yml \
        infrastructure/clusters/feather-core/base-sources/kustomization.yaml
git commit -m "feat(plane): add plane-ce HelmRepository source"
```

---

### Task 2: Provision the CNPG Postgres role and database

**Files:**
- Modify: `infrastructure/clusters/feather-core/configs/postgresql/cluster.yaml`
- Modify: `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`
- Create: `infrastructure/clusters/feather-core/configs/postgresql/roles/plane.sops.env`
- Create: `infrastructure/clusters/feather-core/configs/postgresql/database/plane.yaml`

**Interfaces:**
- Produces: a `plane` Postgres role/database on `feather-core-cluster-pg`, reachable at
  `feather-core-cluster-pg-pooler-rw.cnpg-system.svc.cluster.local:5432/plane` — consumed by
  Task 6's `DATABASE_URL`.

- [ ] **Step 1: Add the `plane` role to the CNPG Cluster**

Edit `infrastructure/clusters/feather-core/configs/postgresql/cluster.yaml`, adding to
`spec.managed.roles` (after the `n8n` entry):

```yaml
      - name: plane
        login: true
        ensure: present
        passwordSecret:
          name: role-plane
```

- [ ] **Step 2: Generate a random password and create the encrypted role secret**

```bash
PLANE_PG_PASSWORD=$(openssl rand -base64 32 | tr -d '=+/')
cat > infrastructure/clusters/feather-core/configs/postgresql/roles/plane.sops.env <<EOF
password=${PLANE_PG_PASSWORD}
EOF
sops --encrypt --in-place infrastructure/clusters/feather-core/configs/postgresql/roles/plane.sops.env
# Save $PLANE_PG_PASSWORD somewhere safe (e.g. a password manager) — it's needed
# again in Task 6 to build DATABASE_URL, and sops won't show it back to you
# without decrypting the file.
```

(Match the existing file's key name — check `roles/n8n.sops.env`'s key with
`sops --decrypt infrastructure/clusters/feather-core/configs/postgresql/roles/n8n.sops.env` if
`password` turns out not to match; the `secretGenerator` in the next step must reference the
correct key name so CNPG's `passwordSecret` finds it.)

- [ ] **Step 3: Register the role secretGenerator**

Edit `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`, adding after
the `role-n8n` entry:

```yaml
  - name: role-plane
    options:
      labels:
        cnpg.io/reload: "true"
    envs:
      - roles/plane.sops.env
```

- [ ] **Step 4: Create the Database CR**

```yaml
# infrastructure/clusters/feather-core/configs/postgresql/database/plane.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: plane
spec:
  name: plane
  owner: plane
  cluster:
    name: feather-core-cluster-pg
```

- [ ] **Step 5: Register the Database resource**

Edit `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`, adding to
`resources:` (after `database/n8n.yaml`):

```yaml
  - database/plane.yaml
```

- [ ] **Step 6: Validate**

Run: `kubectl kustomize infrastructure/clusters/feather-core/configs/postgresql | grep -B2 -A8 "name: plane"`
Expected: both the `role-plane` Secret reference inside the `Cluster` spec and the `Database`
named `plane` render without error. Also run `./scripts/validate.sh` and confirm it still passes
(it strips SOPS patches, so the encrypted role secret content itself isn't validated here).

- [ ] **Step 7: Commit**

```bash
git add infrastructure/clusters/feather-core/configs/postgresql/
git commit -m "feat(plane): provision plane role and database on shared CNPG cluster"
```

---

### Task 3: Provision S3 credentials via Rook CephObjectStoreUser

**Files:**
- Create: `infrastructure/clusters/feather-core/rook-fr01/users/plane.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/users/kustomization.yaml`
- Create: `infrastructure/clusters/feather-core/rook-fr01/buckets/plane.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/kustomization.yaml`

**Interfaces:**
- Produces: an RGW user `plane` on store `feather-s3`, whose generated access/secret key
  (retrieved from the in-cluster Secret `rook-ceph-object-user-feather-s3-plane` in namespace
  `rook-ceph-fr01` once applied) feeds Task 6's `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`.

- [ ] **Step 1: Create the CephObjectStoreUser**

```yaml
# infrastructure/clusters/feather-core/rook-fr01/users/plane.yaml
apiVersion: ceph.rook.io/v1
kind: CephObjectStoreUser
metadata:
  name: plane
  namespace: rook-ceph-fr01
spec:
  store: feather-s3
  displayName: plane
  capabilities:
    bucket: "*"
    user: "*"
```

- [ ] **Step 2: Register it**

Edit `infrastructure/clusters/feather-core/rook-fr01/users/kustomization.yaml`, adding
`- plane.yaml` to `resources:` (after `outline.yaml`).

- [ ] **Step 3: Add the bucket-name reservation**

Per `docs/buckets.md`, the `ObjectBucketClaim` files under `buckets/` don't actually provision
anything, but stay as documentation of intent — add one for consistency:

```yaml
# infrastructure/clusters/feather-core/rook-fr01/buckets/plane.yaml
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: plane
  namespace: rook-ceph-fr01
spec:
  bucketName: plane
  storageClassName: ceph-bucket-fr01
```

- [ ] **Step 4: Register it**

Edit `infrastructure/clusters/feather-core/rook-fr01/buckets/kustomization.yaml`, adding
`- plane.yaml` to `resources:` (after `outline.yaml`).

- [ ] **Step 5: Validate**

Run: `kubectl kustomize infrastructure/clusters/feather-core/rook-fr01/users | grep -A8 "name: plane"`
Expected: the `CephObjectStoreUser` renders with `store: feather-s3`.
Run: `kubectl kustomize infrastructure/clusters/feather-core/rook-fr01/buckets | grep -A5 "name: plane"`
Expected: the `ObjectBucketClaim` renders with `bucketName: plane`.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/clusters/feather-core/rook-fr01/users/plane.yaml \
        infrastructure/clusters/feather-core/rook-fr01/users/kustomization.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/plane.yaml \
        infrastructure/clusters/feather-core/rook-fr01/buckets/kustomization.yaml
git commit -m "feat(plane): provision Rook RGW user and bucket reservation for Plane uploads"
```

**Note:** the access/secret key only exist once this is applied to the live cluster (i.e. after
Task 10 pushes to `main` and Flux reconciles). Task 6 (building the app secret) therefore has a
hard dependency on this task's changes being live — either push Tasks 1–3 to `main` first and let
them reconcile before continuing, or collect placeholder-free real values for Task 6 only once
this is live. Retrieve the values with:

```bash
kubectl get secret rook-ceph-object-user-feather-s3-plane -n rook-ceph-fr01 \
  -o jsonpath='{.data.AccessKey}' | base64 -d; echo
kubectl get secret rook-ceph-object-user-feather-s3-plane -n rook-ceph-fr01 \
  -o jsonpath='{.data.SecretKey}' | base64 -d; echo
```

If the secret name differs (Rook's naming convention can vary by version), find it with:
`kubectl get secrets -n rook-ceph-fr01 | grep plane`.

---

### Task 4: Allocate a Dragonfly Redis DB number

**Files:**
- Create: `docs/dragonfly-redis-allocations.md`

**Interfaces:**
- Produces: DB `12` reserved for Plane on the shared Dragonfly instance
  (`dragonfly.dragonfly.svc.cluster.local:6379`), consumed by Task 6's `REDIS_URL`.

- [ ] **Step 1: Confirm DB 12 is still free**

```bash
kubectl exec -it -n dragonfly deploy/dragonfly -- redis-cli -a "$(kubectl get secret dragonfly-auth -n dragonfly -o jsonpath='{.data.password}' | base64 -d)" -n 12 dbsize
```

Expected: `(integer) 0` (empty — confirms nothing else has claimed DB 12 since the design was
written). If it's non-zero, pick the next free DB from {3, 4, 13, 14, 15} instead and use that
number throughout the rest of this plan.

- [ ] **Step 2: Write the allocation doc**

The previous tracking doc (`docs/dragonfly-redis-cutover.md`) was removed from the tree; recreate
it under a clearer name with the current known allocations plus Plane's:

```markdown
# Dragonfly shared Redis — DB allocations

All apps below share one Dragonfly instance (`dragonfly.dragonfly.svc.cluster.local:6379`,
password in secret `dragonfly-auth` / namespace `dragonfly`), separated by Redis DB number
(`SELECT n`). Check this table before assigning a new DB to an app.

| DB | App | Purpose |
|---|---|---|
| 0 | Harbor | core |
| 1 | Harbor | jobservice |
| 2 | Harbor | registry |
| 5 | Harbor | trivy |
| 6 | Harbor | cache |
| 7 | Harbor | cache-layer |
| 8 | shlink | cache |
| 9 | Outline | cache/queues |
| 10 | Outline | collaboration |
| 11 | n8n | Bull queue |
| 12 | Plane | cache/sessions (`REDIS_URL`) |

Free: 3, 4, 13, 14, 15.
```

- [ ] **Step 3: Validate**

Run: `cat docs/dragonfly-redis-allocations.md` and confirm the table renders as expected markdown
(no broken pipes/alignment).

- [ ] **Step 4: Commit**

```bash
git add docs/dragonfly-redis-allocations.md
git commit -m "docs(redis): restore Dragonfly DB allocation tracking, add Plane's DB 12"
```

---

### Task 5: Obtain and encrypt the Cloudflare origin TLS certificate

**Files:**
- Create: `apps/clusters/feathre-core/base-apps/plane/cf-origin-tls.sops.crt`
- Create: `apps/clusters/feathre-core/base-apps/plane/cf-origin-tls.sops.key`

**Interfaces:**
- Produces: an origin TLS cert/key pair for `tasks.onelitefeather.net`, consumed by Task 8's
  `cf-origin-tls` `secretGenerator`.

This step is **manual** — it requires the Cloudflare dashboard, which this plan cannot script.

- [ ] **Step 1: Generate the origin certificate in Cloudflare**

In the Cloudflare dashboard for the `onelitefeather.net` zone: **SSL/TLS → Origin Server →
Create Certificate**. Add `tasks.onelitefeather.net` as a hostname (or reuse the existing
wildcard/multi-host origin cert if one already covers it — check whether leantime's
`cf-origin-tls.sops.crt` already includes a wildcard SAN by running
`sops --decrypt apps/clusters/feathre-core/base-apps/leantime/cf-origin-tls.sops.crt | openssl x509 -noout -text | grep -A2 "Subject Alternative Name"`;
if it does, that same cert/key pair can be reused verbatim for Plane instead of generating a new
one). Download the certificate (PEM) and private key (PEM).

- [ ] **Step 2: Encrypt and place them**

```bash
cp /path/to/downloaded/cert.pem apps/clusters/feathre-core/base-apps/plane/cf-origin-tls.sops.crt
cp /path/to/downloaded/key.pem apps/clusters/feathre-core/base-apps/plane/cf-origin-tls.sops.key
sops --encrypt --in-place apps/clusters/feathre-core/base-apps/plane/cf-origin-tls.sops.crt
sops --encrypt --in-place apps/clusters/feathre-core/base-apps/plane/cf-origin-tls.sops.key
```

- [ ] **Step 3: Validate**

Run: `sops --decrypt apps/clusters/feathre-core/base-apps/plane/cf-origin-tls.sops.crt | openssl x509 -noout -subject -dates`
Expected: subject/SAN covers `tasks.onelitefeather.net`, and the validity dates are current.

- [ ] **Step 4: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/plane/cf-origin-tls.sops.crt \
        apps/clusters/feathre-core/base-apps/plane/cf-origin-tls.sops.key
git commit -m "feat(plane): add Cloudflare origin TLS cert for tasks.onelitefeather.net"
```

---

### Task 6: Assemble the app secret

**Files:**
- Create: `apps/clusters/feathre-core/base-apps/plane/plane.sops.env`

**Interfaces:**
- Consumes: `DATABASE_URL` password from Task 2, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` from
  Task 3, Dragonfly password from Task 4's cluster (`dragonfly-auth` secret).
- Produces: one encrypted env file, referenced by four `secretGenerator` blocks in Task 8, keyed
  exactly as the chart's `external_secrets.*_existingSecret` templates expect (verified against
  the chart's `templates/config-secrets/*.yaml` — see Task 8's comments for which keys go where).

- [ ] **Step 1: Gather the raw values**

```bash
# From Task 2:
PLANE_PG_PASSWORD="<value saved in Task 2, step 2>"

# From Task 3 (requires Tasks 1-3 already pushed and reconciled — see Task 3's note):
AWS_ACCESS_KEY_ID=$(kubectl get secret rook-ceph-object-user-feather-s3-plane -n rook-ceph-fr01 -o jsonpath='{.data.AccessKey}' | base64 -d)
AWS_SECRET_ACCESS_KEY=$(kubectl get secret rook-ceph-object-user-feather-s3-plane -n rook-ceph-fr01 -o jsonpath='{.data.SecretKey}' | base64 -d)

# Shared Dragonfly password:
DRAGONFLY_PASSWORD=$(kubectl get secret dragonfly-auth -n dragonfly -o jsonpath='{.data.password}' | base64 -d)

# Generate new random values for Plane's own secrets:
SECRET_KEY=$(openssl rand -hex 32)
LIVE_SERVER_SECRET_KEY=$(openssl rand -hex 32)
RABBITMQ_PASSWORD=$(openssl rand -base64 24 | tr -d '=+/')
```

- [ ] **Step 2: Write the plaintext env file**

```bash
cat > apps/clusters/feathre-core/base-apps/plane/plane.sops.env <<EOF
SECRET_KEY=${SECRET_KEY}
LIVE_SERVER_SECRET_KEY=${LIVE_SERVER_SECRET_KEY}
DATABASE_URL=postgresql://plane:${PLANE_PG_PASSWORD}@feather-core-cluster-pg-pooler-rw.cnpg-system.svc.cluster.local:5432/plane
REDIS_URL=redis://:${DRAGONFLY_PASSWORD}@dragonfly.dragonfly.svc.cluster.local:6379/12
RABBITMQ_DEFAULT_USER=plane
RABBITMQ_DEFAULT_PASS=${RABBITMQ_PASSWORD}
AMQP_URL=amqp://plane:${RABBITMQ_PASSWORD}@plane-rabbitmq.plane.svc.cluster.local:5672/
FILE_SIZE_LIMIT=5242880
AWS_S3_BUCKET_NAME=plane
USE_MINIO=0
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
AWS_S3_ENDPOINT_URL=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80
AWS_REGION=us-east-1
EOF
```

- [ ] **Step 3: Encrypt it**

```bash
sops --encrypt --in-place apps/clusters/feathre-core/base-apps/plane/plane.sops.env
unset PLANE_PG_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY DRAGONFLY_PASSWORD SECRET_KEY LIVE_SERVER_SECRET_KEY RABBITMQ_PASSWORD
```

- [ ] **Step 4: Validate**

Run: `sops --decrypt apps/clusters/feathre-core/base-apps/plane/plane.sops.env | grep -c '^[A-Z_]*='`
Expected: `14` (one line per key above). Confirm no line is empty on the right-hand side (a blank
`AWS_ACCESS_KEY_ID=` means Task 3 hasn't been applied to the live cluster yet).

- [ ] **Step 5: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/plane/plane.sops.env
git commit -m "feat(plane): add encrypted app secret (db/redis/rabbitmq/s3/app keys)"
```

---

### Task 7: Create the portable Plane app base

**Files:**
- Create: `apps/base/plane/namespace.yaml`
- Create: `apps/base/plane/kustomization.yaml`
- Create: `apps/base/plane/release.yaml`

**Interfaces:**
- Produces: a `HelmRelease` skeleton (empty `values: {}`) that Task 8's overlay patches with
  concrete values — mirrors `apps/base/leantime/` exactly.

- [ ] **Step 1: Create the namespace**

```yaml
# apps/base/plane/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: plane
```

- [ ] **Step 2: Create the base HelmRelease**

```yaml
# apps/base/plane/release.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: plane
  namespace: plane
spec:
  releaseName: plane
  chart:
    spec:
      chart: plane-ce
      version: "1.6.0"
      sourceRef:
        kind: HelmRepository
        name: plane
        namespace: flux-system
  install:
    remediation:
      retries: 3
  interval: 1m0s
  # Default values
  #
  values: {}
```

- [ ] **Step 3: Create the base kustomization**

```yaml
# apps/base/plane/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - release.yaml
```

- [ ] **Step 4: Validate**

Run: `kubectl kustomize apps/base/plane`
Expected: renders a `Namespace` named `plane` and a `HelmRelease` named `plane` with
`chart.spec.chart: plane-ce`, no errors.

- [ ] **Step 5: Commit**

```bash
git add apps/base/plane/
git commit -m "feat(plane): add portable app base (namespace + HelmRelease skeleton)"
```

---

### Task 8: Create the cluster overlay (secrets, values, ingress) and register the app

**Files:**
- Create: `apps/clusters/feathre-core/base-apps/plane/kustomization.yaml`
- Create: `apps/clusters/feathre-core/base-apps/plane/release.yaml`
- Create: `apps/clusters/feathre-core/base-apps/plane/ingress.yaml`
- Modify: `apps/clusters/feathre-core/base-apps/kustomization.yaml`

**Interfaces:**
- Consumes: `apps/base/plane` (Task 7), `plane.sops.env` (Task 6), `cf-origin-tls.sops.{crt,key}`
  (Task 5).
- Produces: the fully wired `HelmRelease` and public `Ingress` — the deliverable this whole plan
  builds toward.

- [ ] **Step 1: Create the kustomization with secretGenerators**

The chart's four `external_secrets.*_existingSecret` values (see
`templates/config-secrets/{app-env,doc-store,live-env,rabbitmqdb}.yaml` in the pulled chart) each
expect a *separate* Secret name, but they only read the keys they need — pointing all four at
Secrets generated from the same `plane.sops.env` (which is a superset of every key) is safe;
unused keys in each are simply ignored by `envFrom`.

```yaml
# apps/clusters/feathre-core/base-apps/plane/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: plane
generatorOptions:
  disableNameSuffixHash: true
resources:
  - ../../../../../apps/base/plane/
  - ingress.yaml
patches:
  - path: release.yaml

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
  - name: cf-origin-tls
    type: kubernetes.io/tls
    files:
      - tls.crt=cf-origin-tls.sops.crt
      - tls.key=cf-origin-tls.sops.key
```

- [ ] **Step 2: Create the release patch with concrete values**

`ingress.enabled: false` because the chart's own Ingress/Traefik templates target
`nginx`/`traefik`, not this cluster's Cloudflare Tunnel controller — routing is handled by
Step 3's hand-written `Ingress` instead. `postgres.local_setup`/`redis.local_setup`/
`minio.local_setup` are `false` (external, already-provisioned services); `rabbitmq.local_setup`
stays `true` (chart-bundled, Plane-scoped, per the design). `assign_cluster_ip: true` per
component avoids relying on headless-Service routing through the Cloudflare Tunnel Ingress
controller (the chart defaults to headless `clusterIP: None` Services, which may or may not be
handled correctly by every ingress controller — this sidesteps the question). The
`postRenderers` patch injects `priorityClassName: feather-standard` on every component
Deployment, matching this repo's convention (see leantime's `release.yaml`); it targets `kind:
Deployment` with no `name`, so it applies to all seven Deployments the chart renders.

```yaml
# apps/clusters/feathre-core/base-apps/plane/release.yaml
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
  values:
    planeVersion: v1.3.1

    ingress:
      enabled: false
      appHost: tasks.onelitefeather.net

    postgres:
      local_setup: false
    redis:
      local_setup: false
    minio:
      local_setup: false
    rabbitmq:
      local_setup: true
      storageClass: ceph-rbd-fr01

    external_secrets:
      app_env_existingSecret: plane-app-env
      doc_store_existingSecret: plane-doc-store
      live_env_existingSecret: plane-live-env
      rabbitmq_existingSecret: plane-rabbitmq

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

    env:
      docstore_bucket: plane
      cors_allowed_origins: "https://tasks.onelitefeather.net"
      default_cluster_domain: cluster.local
```

- [ ] **Step 3: Create the Ingress**

Route table taken directly from the `plane-ce` chart's documented "Custom Ingress Routes"
(`/uploads/*` is omitted — that route only applies to the chart's bundled MinIO, which this
deployment doesn't use; file downloads go through the API's signed S3 URLs instead), mirroring
Outline's multi-path Cloudflare Tunnel `Ingress` pattern:

```yaml
# apps/clusters/feathre-core/base-apps/plane/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: plane-cloudflare-tunnel
  annotations:
    cloudflare-tunnel-ingress-controller.strrl.dev/backend-protocol: http
spec:
  ingressClassName: cloudflare-tunnel
  rules:
    - host: tasks.onelitefeather.net
      http:
        paths:
          - path: /spaces
            pathType: Prefix
            backend:
              service:
                name: plane-space
                port:
                  number: 3000
          - path: /god-mode
            pathType: Prefix
            backend:
              service:
                name: plane-admin
                port:
                  number: 3000
          - path: /live
            pathType: Prefix
            backend:
              service:
                name: plane-live
                port:
                  number: 3000
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: plane-api
                port:
                  number: 8000
          - path: /auth
            pathType: Prefix
            backend:
              service:
                name: plane-api
                port:
                  number: 8000
          - path: /
            pathType: Prefix
            backend:
              service:
                name: plane-web
                port:
                  number: 3000
```

(Path order matters for `Prefix` matching precedence with some controllers — the most specific
paths are listed before the catch-all `/`, matching Outline's existing ordering convention.)

- [ ] **Step 4: Register the app in the base-apps aggregator**

Edit `apps/clusters/feathre-core/base-apps/kustomization.yaml`, adding `- plane` to `resources:`
(after `- leantime`, so the two are adjacent while both exist):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - reposilite
  - shlink
  - bluemap
  - harbor
  - node-red
  - outline
  - dependency-track
  - uptime-kuma
  - leantime
  - plane
  - grafana
  - n8n
  - ollama
  # loki + mimir + tempo are managed by the dedicated
  # wait:false monitoring Kustomization
  # (clusters/feather-core/monitoring.yaml) so their external
  # Ceph-RGW S3 backend does not block base-apps -> apps.
  - alloy-logs
  - alloy-metrics
  - alloy-receiver
```

- [ ] **Step 5: Validate**

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/plane
```
Expected: renders without error — `Namespace`, `HelmRelease` (with the merged `values:` from the
patch), `Ingress`, and five `Secret`s (`plane-app-env`, `plane-doc-store`, `plane-live-env`,
`plane-rabbitmq`, `cf-origin-tls`), all base64-encoded (not plaintext SOPS ciphertext — confirms
`sops` decrypted them into the Kustomize build correctly).

```bash
./scripts/validate.sh
```
Expected: passes for the whole repo, including the new `plane` app.

- [ ] **Step 6: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/plane/ \
        apps/clusters/feathre-core/base-apps/kustomization.yaml
git commit -m "feat(plane): wire up cluster overlay (values, ingress) and register app"
```

---

### Task 9: Full-repo validation

**Files:** none (verification only)

- [ ] **Step 1: Run the full validation suite**

```bash
./scripts/validate.sh
```
Expected: exits 0, no kustomize build or kubeconform errors anywhere in the repo.

- [ ] **Step 2: Dry-run the CNPG and rook-fr01 layers specifically**

```bash
kubectl kustomize infrastructure/clusters/feather-core/configs/postgresql > /dev/null && echo OK
kubectl kustomize infrastructure/clusters/feather-core/rook-fr01/users > /dev/null && echo OK
kubectl kustomize infrastructure/clusters/feather-core/rook-fr01/buckets > /dev/null && echo OK
kubectl kustomize infrastructure/clusters/feather-core/base-sources > /dev/null && echo OK
```
Expected: `OK` printed four times.

- [ ] **Step 3: Confirm no plaintext secrets were committed**

```bash
git log --stat -- 'apps/clusters/feathre-core/base-apps/plane/*.sops.*' \
                  'infrastructure/clusters/feather-core/configs/postgresql/roles/plane.sops.env'
git show HEAD -- apps/clusters/feathre-core/base-apps/plane/plane.sops.env | grep -c "ENC\["
```
Expected: the grep count is `14` (matches the 14 keys — every value is SOPS-encrypted, none are
plaintext).

No commit for this task — it's a checkpoint, not a change.

---

### Task 10: Rollout checkpoint — push, reconcile, verify (requires explicit go-ahead)

**This task is not to be executed automatically.** Everything up to here only exists on a local
branch/worktree; nothing has touched the live cluster. Pushing to `main` triggers real changes on
production infrastructure (new Postgres role/database, new RGW user, a new namespace with 8+ new
pods, a new public ingress route) — get explicit confirmation before proceeding, per this repo's
own guidance on hard-to-reverse, shared-system-affecting actions.

- [ ] **Step 1: Push and let Flux reconcile the prerequisite layers first**

Push Tasks 1–5's commits (sources, CNPG role/database, Rook user/bucket, Redis allocation,
origin cert) to `main` and wait for `base-sources`, `configs` (which includes `postgresql`), and
`rook`/`rook-fr01` to report `Ready`:

```bash
flux get kustomizations -A | grep -E "base-sources|configs|rook"
```

Do **not** loop `flux reconcile` — per this repo's convention, forcing a layer mid-flight makes
dependents report "dependency not ready." Push once, then wait for the 1m/10m poll intervals.

- [ ] **Step 2: Complete and push Task 6 (the app secret)**

Task 6 needs the live `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` from Task 3's now-reconciled
`CephObjectStoreUser` — finish building `plane.sops.env` now if it was left with placeholder
values earlier, then push Tasks 6–8 (secret, app base, cluster overlay) to `main`.

- [ ] **Step 3: Reconcile and watch the rollout**

```bash
flux reconcile kustomization base-apps --with-source
kubectl get pods -n plane -w
```
Expected: `plane-web-wl`, `plane-space-wl`, `plane-admin-wl`, `plane-api-wl`, `plane-live-wl`,
`plane-worker-wl`, `plane-beatworker-wl`, `plane-rabbitmq` pods all reach `Running`/`1/1 Ready`,
and a `plane-app-api-migrate-<n>` Job completes (`kubectl get jobs -n plane`).

- [ ] **Step 4: Manual verification**

Visit `https://tasks.onelitefeather.net` and confirm:
- The login/signup page loads (`/` → web).
- Account creation and login succeed (exercises `api`, Postgres, Redis session storage).
- Creating a project and an issue works (exercises `api`, Postgres, `worker`/`beatworker` via
  RabbitMQ).
- Uploading a file/avatar succeeds and the file is retrievable afterward (exercises S3 —
  confirms the bucket auto-created correctly under the `plane` RGW user).
- Opening an issue and seeing live cursor/typing indicators from a second browser session works
  (exercises `live`, `/live` routing, Redis).
- `/god-mode` (admin) and `/spaces` (space) both load without a 502/404.

If `api`/`migrator`/`worker` fail to reach Postgres with an error mentioning
`unsupported startup parameter: statement_timeout`, switch `DATABASE_URL` in `plane.sops.env`
from the pooler host to `feather-core-cluster-pg-rw.cnpg-system.svc.cluster.local:5432` (the
direct `-rw` service, same fallback n8n uses — see `apps/clusters/feathre-core/base-apps/n8n/release.yaml`),
re-encrypt, and push.

- [ ] **Step 5: Burn-in, then decommission leantime separately**

Per the design spec's Phase 2, once Plane has been verified stable for a reasonable burn-in
period, leantime's removal (HelmRelease, base/overlay manifests, mariadb-galera
database/grants/user, namespace, secrets) is a **separate follow-up change** — out of scope for
this plan.

---

## Self-Review Notes

- **Spec coverage:** Architecture (Tasks 1, 7, 8), Postgres (Task 2), Redis (Task 4, 6), RabbitMQ
  (Task 8's `rabbitmq.local_setup: true` + Task 6's credentials), Object storage (Task 3, 6),
  Secrets & overlay structure (Tasks 5, 6, 7, 8), Rollout plan Phase 1 (Task 10), Phase 2 is
  explicitly called out as out of scope for this plan (Task 10, Step 5). All spec sections are
  covered.
- **Placeholder scan:** every `env.*`/`AWS_*`/`DATABASE_URL` value is either a literal or a
  concrete shell command producing a real value — no `TBD`/`TODO` left. The one deliberately
  open item (exact CNPG role secret key name, chart/app version currency) has an explicit
  verification command attached, not a guess.
- **Type/name consistency:** `plane-app-env`/`plane-doc-store`/`plane-live-env`/`plane-rabbitmq`
  secret names match between the `secretGenerator` (Task 8, Step 1) and the `external_secrets.*`
  values (Task 8, Step 2). Service names in the `Ingress` (Task 8, Step 3) match the chart's
  `{{ .Release.Name }}-<component>` pattern with `releaseName: plane` (Task 7, Step 2) — i.e.
  `plane-web`, `plane-space`, `plane-admin`, `plane-api`, `plane-live`.
