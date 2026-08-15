# Penpot Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plane und Leantime vollständig aus dem `feather-core`-Cluster entfernen und durch eine Penpot-Team-Instanz mit aktiviertem offiziellem MCP-Server ersetzen.

**Architecture:** Penpot kommt aus dem offiziellen Helm-Chart (`https://helm.penpot.app`, Chart 1.7.0 / App 2.17.0) über eine neue `HelmRepository`-Source. Es nutzt die bestehende geteilte Infrastruktur: CNPG-Postgres (`-rw`-Service), die geteilte Dragonfly-Instanz auf DB 13, und Ceph RGW für Assets. Exposition läuft über den bestehenden Cloudflare-Tunnel-Ingress-Controller. Der MCP-Server wird allein durch `enable-mcp` in `config.flags` aktiviert und bekommt zwei eigene Ingresses (HTTP/SSE 4401, WebSocket 4402).

**Tech Stack:** FluxCD v2, Kustomize (Zwei-Tier base+overlay), Helm, SOPS/age, CloudNativePG, Rook Ceph RGW, Dragonfly, cloudflare-tunnel-ingress-controller.

**Spec:** `docs/superpowers/specs/2026-08-15-penpot-migration-design.md`

**Status:** Tasks 1–5 sind im Branch `feat/penpot-replaces-plane-leantime`
umgesetzt und validiert (`./scripts/validate.sh` und
`scripts/check-sops-encryption.py` beide grün). Task 6 (Merge, Ausrollen,
manueller Storage-Cleanup) steht aus.

**Beim Umsetzen gefundene Abweichungen von der ursprünglichen Planung:**

1. Der Frontend-Service heißt **`penpot`**, nicht `penpot-frontend` — das
   Chart benennt ihn nach dem Release ohne Suffix. Nur der MCP-Service trägt
   eines (`penpot-mcp`).
2. CNPG-Rollen müssen **zusätzlich** in `configs/postgresql/cluster.yaml`
   unter `managed.roles` deklariert werden. Ein `roles/*.sops.env` plus
   secretGenerator allein legt keine Rolle an. Entsprechend musste dort auch
   die `plane`-Rolle entfernt werden.
3. `penpot-s3.sops.env` enthält vorerst den Sentinel
   `REPLACE_AFTER_ROOK_CREATES_USER`: Rook erzeugt die Schlüssel erst, wenn
   der `CephObjectStoreUser` `penpot` im Cluster existiert. Nachfüllen nach
   dem ersten Reconcile — Kommando in `docs/penpot-mcp.md`.
4. `infrastructure/.../mariadb-galera/databases/.decrypted~kustomization.yaml`
   war als SOPS-Nebenprodukt eingecheckt (kein Geheimnis darin) und verwies
   auf Leantime. Entfernt, `.decrypted~*` in `.gitignore` aufgenommen.

## Global Constraints

- **Pfad-Schreibweise:** Infrastruktur nutzt `clusters/feather-core/`, Apps nutzen `clusters/feathre-core/` (mit dem absichtlichen Tippfehler "feathre"). Beide sind korrekt — niemals angleichen.
- **Chart-Pin:** `penpot` Chart Version exakt `1.7.0` (appVersion `2.17.0`).
- **Helm-Repo-URL:** `https://helm.penpot.app` (HTTPS, verifiziert mit HTTP 200 — die offizielle Doku nennt HTTP).
- **Postgres-Host:** `feather-core-cluster-pg-rw.cnpg-system.svc.cluster.local:5432` — der `-rw`-Service, **nicht** der PgBouncer-Pooler.
- **Redis:** `dragonfly.dragonfly.svc.cluster.local:6379`, **DB 13**. Passwort im Secret `dragonfly-auth` (Namespace `dragonfly`), Key `password`.
- **S3-Endpoint:** `https://s3.onelitefeather.net`, Region `us-east-1`, ObjectStore `feather-s3`, Bucket-StorageClass `ceph-bucket-fr01`.
- **PriorityClass:** `feather-standard`.
- **Log-Label:** `logs.onelitefeather.net/env: prod`.
- **Hosts:** `penpot.onelitefeather.net`, `penpot-mcp.onelitefeather.net`, `penpot-mcp-ws.onelitefeather.net`.
- **Overlays setzen immer** `generatorOptions.disableNameSuffixHash: true`.
- **Kein `cf-origin-tls`** für Penpot — das Secret wird in bestehenden Overlays zwar generiert, aber von keinem Ingress referenziert.
- **Verifikation nach jeder Aufgabe:** `./scripts/validate.sh` muss grün sein.
- **Commits:** Conventional Commits, Subject kleingeschrieben, Header ≤100 Zeichen.

---

### Task 1: Penpot-Infrastruktur — Source, Datenbank, Bucket

**Files:**
- Create: `infrastructure/clusters/feather-core/base-sources/penpot.yaml`
- Modify: `infrastructure/clusters/feather-core/base-sources/kustomization.yaml`
- Create: `infrastructure/clusters/feather-core/configs/postgresql/database/penpot.yaml`
- Create: `infrastructure/clusters/feather-core/configs/postgresql/roles/penpot.sops.env`
- Modify: `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`
- Modify: `infrastructure/clusters/feather-core/configs/postgresql/cluster.yaml` (`managed.roles`)
- Create: `infrastructure/clusters/feather-core/rook-fr01/buckets/penpot.yaml`
- Create: `infrastructure/clusters/feather-core/rook-fr01/users/penpot.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/buckets/kustomization.yaml`
- Modify: `infrastructure/clusters/feather-core/rook-fr01/users/kustomization.yaml`

**Interfaces:**
- Produces: CNPG-Datenbank `penpot` mit Owner-Rolle `penpot`; Ceph-Bucket `penpot` mit Owner `penpot`; `HelmRepository` `penpot` in `flux-system`. Task 2 konsumiert alle drei.

- [ ] **Step 1: HelmRepository anlegen**

`infrastructure/clusters/feather-core/base-sources/penpot.yaml`:

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: penpot
  namespace: flux-system
spec:
  interval: 5m
  url: https://helm.penpot.app
```

- [ ] **Step 2: Source registrieren**

In `infrastructure/clusters/feather-core/base-sources/kustomization.yaml` `- penpot.yaml` an die `resources:`-Liste anhängen (hinter `apus.yaml`).

- [ ] **Step 3: CNPG-Datenbank anlegen**

`infrastructure/clusters/feather-core/configs/postgresql/database/penpot.yaml`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: penpot
spec:
  name: penpot
  owner: penpot
  cluster:
    name: feather-core-cluster-pg
```

- [ ] **Step 4: Postgres-Rollenpasswort erzeugen und verschlüsseln**

Ein Passwort erzeugen und direkt als SOPS-Datei ablegen, ohne es im Terminal auszugeben:

```bash
PW="$(openssl rand -base64 33 | tr -d '/+=' | head -c 40)"
printf 'username=penpot\npassword=%s\n' "$PW" \
  > infrastructure/clusters/feather-core/configs/postgresql/roles/penpot.sops.env
sops -e -i --input-type dotenv --output-type dotenv \
  infrastructure/clusters/feather-core/configs/postgresql/roles/penpot.sops.env
# dasselbe Passwort wird in Task 2 für penpot-db.sops.env gebraucht:
printf 'username=penpot\npassword=%s\n' "$PW" > /tmp/penpot-db.plain
unset PW
```

- [ ] **Step 5: Rolle und Datenbank in der Postgres-Kustomization registrieren**

In `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`:
- `- database/penpot.yaml` an die `resources:`-Liste anhängen.
- Diesen secretGenerator-Eintrag anhängen:

```yaml
  - name: role-penpot
    options:
      labels:
        cnpg.io/reload: "true"
    envs:
      - roles/penpot.sops.env
```

- [ ] **Step 6: Ceph-User und Bucket anlegen**

`infrastructure/clusters/feather-core/rook-fr01/users/penpot.yaml`:

```yaml
apiVersion: ceph.rook.io/v1
kind: CephObjectStoreUser
metadata:
  name: penpot
  namespace: rook-ceph-fr01
spec:
  store: feather-s3
  displayName: penpot
  capabilities:
    bucket: "*"
    user: "*"
```

`infrastructure/clusters/feather-core/rook-fr01/buckets/penpot.yaml`:

```yaml
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: penpot
  namespace: rook-ceph-fr01
spec:
  bucketName: penpot
  storageClassName: ceph-bucket-fr01
  additionalConfig:
    bucketOwner: penpot
```

- [ ] **Step 7: Bucket und User registrieren**

`- penpot.yaml` jeweils an die `resources:`-Liste in
`infrastructure/clusters/feather-core/rook-fr01/buckets/kustomization.yaml` und
`infrastructure/clusters/feather-core/rook-fr01/users/kustomization.yaml` anhängen.

- [ ] **Step 8: Validieren**

Run: `./scripts/validate.sh`
Expected: PASS, keine Fehler.

Run: `python3 scripts/check-sops-encryption.py`
Expected: PASS — `roles/penpot.sops.env` trägt alle drei age-Recipients.

- [ ] **Step 9: Commit**

```bash
git add infrastructure/clusters/feather-core/
git commit -m "feat(penpot): add helm source, database and ceph bucket"
```

---

### Task 2: Penpot-App — Base, Overlay, Secrets, Ingresses

**Files:**
- Create: `apps/base/penpot/kustomization.yaml`
- Create: `apps/base/penpot/namespace.yaml`
- Create: `apps/base/penpot/release.yaml`
- Create: `apps/clusters/feathre-core/base-apps/penpot/kustomization.yaml`
- Create: `apps/clusters/feathre-core/base-apps/penpot/release.yaml`
- Create: `apps/clusters/feathre-core/base-apps/penpot/ingress.yaml`
- Create: `apps/clusters/feathre-core/base-apps/penpot/penpot-db.sops.env`
- Create: `apps/clusters/feathre-core/base-apps/penpot/penpot-redis.sops.env`
- Create: `apps/clusters/feathre-core/base-apps/penpot/penpot-s3.sops.env`
- Create: `apps/clusters/feathre-core/base-apps/penpot/penpot-core.sops.env`
- Modify: `apps/clusters/feathre-core/base-apps/kustomization.yaml`

**Interfaces:**
- Consumes: aus Task 1 die Datenbank `penpot`, den Bucket `penpot` und die `HelmRepository` `penpot`.
- Produces: Secrets `penpot-db` (Keys `username`, `password`), `penpot-redis` (Key `redis-uri`), `penpot-s3` (Keys `access-key-id`, `secret-access-key`, `endpoint-uri`), `penpot-core` (Key `api-secret-key`). Die Namen sind stabil, weil `disableNameSuffixHash: true` gesetzt ist.

- [ ] **Step 1: Base anlegen**

`apps/base/penpot/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: penpot
```

`apps/base/penpot/release.yaml`:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: penpot
  namespace: penpot
spec:
  releaseName: penpot
  chart:
    spec:
      chart: penpot
      version: "1.7.0"
      sourceRef:
        kind: HelmRepository
        name: penpot
        namespace: flux-system
  install:
    timeout: 15m0s
    remediation:
      retries: 3
  upgrade:
    timeout: 15m0s
    remediation:
      retries: 3
      remediateLastFailure: true
  interval: 1m0s
  # Default values
  #
  values: {}
```

`apps/base/penpot/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: penpot
resources:
  - namespace.yaml
  - release.yaml
```

- [ ] **Step 2: Secrets erzeugen und verschlüsseln**

Die Werte kommen aus dem laufenden Cluster; nichts davon darf im Klartext im Terminal landen.

```bash
cd apps/clusters/feathre-core/base-apps/penpot

# Postgres — dasselbe Passwort wie die CNPG-Rolle aus Task 1
cp /tmp/penpot-db.plain penpot-db.sops.env && rm /tmp/penpot-db.plain

# Redis — kompletter URI, Dragonfly DB 13
DFPW="$(kubectl get secret dragonfly-auth -n dragonfly -o jsonpath='{.data.password}' | base64 -d)"
printf 'redis-uri=redis://:%s@dragonfly.dragonfly.svc.cluster.local:6379/13\n' "$DFPW" \
  > penpot-redis.sops.env
unset DFPW

# S3 — Zugangsdaten des von Rook erzeugten CephObjectStoreUser
AK="$(kubectl get secret rook-ceph-object-user-feather-s3-penpot -n rook-ceph-fr01 -o jsonpath='{.data.AccessKey}' | base64 -d)"
SK="$(kubectl get secret rook-ceph-object-user-feather-s3-penpot -n rook-ceph-fr01 -o jsonpath='{.data.SecretKey}' | base64 -d)"
printf 'access-key-id=%s\nsecret-access-key=%s\nendpoint-uri=https://s3.onelitefeather.net\n' "$AK" "$SK" \
  > penpot-s3.sops.env
unset AK SK

# Penpot API secret key — stabil halten, ein Wechsel invalidiert alle Sessions
printf 'api-secret-key=%s\n' "$(openssl rand -base64 64 | tr -d '\n')" > penpot-core.sops.env

for f in penpot-db penpot-redis penpot-s3 penpot-core; do
  sops -e -i --input-type dotenv --output-type dotenv "$f.sops.env"
done
cd -
```

Voraussetzung: Task 1 ist gepusht und Flux hat den `CephObjectStoreUser` `penpot` bereits angelegt — sonst existiert das Secret `rook-ceph-object-user-feather-s3-penpot` noch nicht. Prüfen mit:

```bash
kubectl get secret rook-ceph-object-user-feather-s3-penpot -n rook-ceph-fr01
```

- [ ] **Step 3: Ingresses schreiben**

`apps/clusters/feathre-core/base-apps/penpot/ingress.yaml` — drei Dokumente in einer Datei:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: penpot-cloudflare-tunnel
  annotations:
    cloudflare-tunnel-ingress-controller.strrl.dev/backend-protocol: http
spec:
  ingressClassName: cloudflare-tunnel
  rules:
    - host: penpot.onelitefeather.net
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: penpot
                port:
                  number: 8080
---
# MCP HTTP/SSE endpoint for AI clients (Claude Code, Claude Desktop, Cursor).
# The chart's own ingress template only covers the frontend service.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: penpot-mcp-cloudflare-tunnel
  annotations:
    cloudflare-tunnel-ingress-controller.strrl.dev/backend-protocol: http
spec:
  ingressClassName: cloudflare-tunnel
  rules:
    - host: penpot-mcp.onelitefeather.net
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: penpot-mcp
                port:
                  number: 4401
---
# The Penpot MCP plugin runs in the team's browser, not in the cluster, so the
# WebSocket port needs to be reachable from outside just like the HTTP one.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: penpot-mcp-ws-cloudflare-tunnel
  annotations:
    cloudflare-tunnel-ingress-controller.strrl.dev/backend-protocol: http
spec:
  ingressClassName: cloudflare-tunnel
  rules:
    - host: penpot-mcp-ws.onelitefeather.net
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: penpot-mcp
                port:
                  number: 4402
```

- [ ] **Step 4: Overlay-Patch mit den Werten schreiben**

Die vollständige Datei liegt als
`apps/clusters/feathre-core/base-apps/penpot/release.yaml` im Repo. Sie setzt:

- `postRenderers` → `priorityClassName: feather-standard` auf `kind: Deployment`
  (deckt frontend, backend, exporter und mcp ab — alle vier sind Deployments).
- `config.publicUri`, `config.flags`, `config.telemetryEnabled: false`.
- `config.postgresql` gegen den `-rw`-Service mit `existingSecret: penpot-db`.
- `config.redis` mit `existingSecret: penpot-redis`, Key `redis-uri`.
- `config.objectsStorage` auf `s3`, Bucket `penpot`, Region `us-east-1`,
  `existingSecret: penpot-s3`.
- `config.existingSecret: penpot-core` mit `secretKeys.apiSecretKey`.
- `persistence.assets.enabled: false` und `ingress.enabled: false`.
- Explizite `resources` für backend, frontend, exporter und mcp — **alle vier
  Chart-Defaults sind `{}`**, was jede Komponente sonst BestEffort ließe.

- [ ] **Step 5: Overlay-Kustomization schreiben**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: penpot
generatorOptions:
  disableNameSuffixHash: true
resources:
  - ../../../../../apps/base/penpot/
  - ingress.yaml
patches:
  - path: release.yaml

secretGenerator:
  - name: penpot-db
    envs:
      - penpot-db.sops.env
  - name: penpot-redis
    envs:
      - penpot-redis.sops.env
  - name: penpot-s3
    envs:
      - penpot-s3.sops.env
  - name: penpot-core
    envs:
      - penpot-core.sops.env
```

- [ ] **Step 6: App registrieren**

`- penpot` an die `resources:`-Liste in `apps/clusters/feathre-core/base-apps/kustomization.yaml` anhängen.

- [ ] **Step 7: Validieren**

Run: `./scripts/validate.sh` → Expected: PASS
Run: `python3 scripts/check-sops-encryption.py` → Expected: PASS
Run: `kubectl kustomize apps/clusters/feathre-core/base-apps/penpot` → Expected: rendert HelmRelease, Namespace, 3 Ingresses, 4 Secrets

- [ ] **Step 8: Commit**

```bash
git add apps/base/penpot apps/clusters/feathre-core/base-apps/
git commit -m "feat(penpot): deploy penpot with the official mcp server"
```

---

### Task 3: Plane entfernen

**Files:**
- Delete: `apps/base/plane/`, `apps/clusters/feathre-core/base-apps/plane/`
- Delete: `infrastructure/clusters/feather-core/base-sources/plane.yml`
- Delete: `infrastructure/clusters/feather-core/configs/postgresql/database/plane.yaml`, `database/plane-pi.yaml`, `roles/plane.sops.env`
- Delete: `infrastructure/clusters/feather-core/rook-fr01/buckets/plane.yaml`, `users/plane.yaml`
- Modify: `apps/clusters/feathre-core/base-apps/kustomization.yaml`, `infrastructure/clusters/feather-core/base-sources/kustomization.yaml`, `infrastructure/clusters/feather-core/configs/postgresql/kustomization.yaml`, `infrastructure/clusters/feather-core/rook-fr01/{buckets,users}/kustomization.yaml`

- [ ] **Step 1: Verzeichnisse und Dateien löschen**

```bash
git rm -r apps/base/plane apps/clusters/feathre-core/base-apps/plane
git rm infrastructure/clusters/feather-core/base-sources/plane.yml
git rm infrastructure/clusters/feather-core/configs/postgresql/database/plane.yaml \
       infrastructure/clusters/feather-core/configs/postgresql/database/plane-pi.yaml \
       infrastructure/clusters/feather-core/configs/postgresql/roles/plane.sops.env
git rm infrastructure/clusters/feather-core/rook-fr01/buckets/plane.yaml \
       infrastructure/clusters/feather-core/rook-fr01/users/plane.yaml
```

- [ ] **Step 2: Referenzen aus den Kustomizations entfernen**

Je einen Eintrag streichen: `- plane` (base-apps), `- plane.yml` (base-sources), `- database/plane.yaml` und `- database/plane-pi.yaml` plus den kompletten `role-plane`-secretGenerator-Block (postgresql), `- plane.yaml` (rook buckets und users).

- [ ] **Step 3: Validieren**

Run: `./scripts/validate.sh` → Expected: PASS
Run: `grep -rn "plane" apps/ infrastructure/ --include="*.yaml" --include="*.yml" | grep -vi "control-plane\|data plane"` → Expected: keine Treffer

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(plane): remove the plane deployment and its storage claims"
```

---

### Task 4: Leantime entfernen

**Files:**
- Delete: `apps/base/leantime/`, `apps/clusters/feathre-core/base-apps/leantime/`, `helm/leantime/`
- Delete: `infrastructure/clusters/feather-core/configs/mariadb-galera/{databases,users,grants}/leantime.yaml`, `passwords/leantime.sops.env`
- Delete: `infrastructure/clusters/feather-core/rook-fr01/users/leantime.yaml`
- Modify: die fünf zugehörigen `kustomization.yaml`

- [ ] **Step 1: Verzeichnisse und Dateien löschen**

```bash
git rm -r apps/base/leantime apps/clusters/feathre-core/base-apps/leantime helm/leantime
git rm infrastructure/clusters/feather-core/configs/mariadb-galera/databases/leantime.yaml \
       infrastructure/clusters/feather-core/configs/mariadb-galera/users/leantime.yaml \
       infrastructure/clusters/feather-core/configs/mariadb-galera/grants/leantime.yaml \
       infrastructure/clusters/feather-core/configs/mariadb-galera/passwords/leantime.sops.env
git rm infrastructure/clusters/feather-core/rook-fr01/users/leantime.yaml
```

- [ ] **Step 2: Referenzen entfernen**

`- leantime` aus base-apps; `- leantime.yaml` aus mariadb `databases`, `users`, `grants`; den `user-leantime-secret`-secretGenerator aus `passwords`; `- leantime.yaml` aus rook `users`.

- [ ] **Step 3: Validieren**

Run: `./scripts/validate.sh` → Expected: PASS
Run: `grep -rni "leantime" apps/ infrastructure/ helm/` → Expected: keine Treffer

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(leantime): remove the leantime deployment and its in-repo chart"
```

---

### Task 5: Dokumentation nachziehen

**Files:**
- Modify: `docs/dragonfly-redis-allocations.md`
- Modify: `infrastructure/clusters/feather-core/base-configs/priorityclasses.yaml`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Create: `docs/penpot-mcp.md`

- [ ] **Step 1: Dragonfly-Tabelle aktualisieren**

Zeile für DB 12 (Plane) entfernen, Zeile `| 13 | Penpot | cache/sessions + realtime |` einfügen, die „Free"-Zeile auf `3, 4, 12, 14, 15` korrigieren.

- [ ] **Step 2: PriorityClass-Beschreibung korrigieren**

In `priorityclasses.yaml` `leantime` aus der Aufzählung streichen und `penpot` ergänzen.

- [ ] **Step 3: README und CLAUDE.md korrigieren**

Beide nennen `leantime` als In-Repo-Chart unter `helm/`. Streichen, sodass nur noch `shlink`, `outline` und `micronaut` gelistet sind.

- [ ] **Step 4: MCP-Betriebsdoku schreiben**

`docs/penpot-mcp.md` mit: Architektur (AI-Client → 4401 → MCP-Server → 4402 → Plugin im Browser), die Client-Konfiguration für Claude Code (`claude mcp add --transport http penpot https://penpot-mcp.onelitefeather.net/mcp`), der Hinweis auf die fehlende eingebaute Authentifizierung und die Pflicht zu Cloudflare Access, und der Hinweis, dass das MCP-Deployment keine Health-Probes hat.

- [ ] **Step 5: Validieren und committen**

```bash
./scripts/validate.sh
git commit -am "docs(penpot): document the mcp endpoint and update allocations"
```

---

### Task 6: Ausrollen und manueller Cleanup

- [ ] **Step 1: Branch pushen und PR öffnen**

Der PR-Titel wird der Squash-Merge-Subject und wird von commitlint geprüft:
`feat(penpot): replace plane and leantime with penpot`

- [ ] **Step 2: Reconciliation beobachten**

Nach dem Merge **einmal** anstoßen, dann den Abhängigkeitsgraphen selbst konvergieren lassen — nicht in einer Schleife reconcilen:

```bash
flux reconcile kustomization flux-system --with-source
flux get kustomizations -A
flux get helmrelease -n penpot
```

- [ ] **Step 3: Funktion prüfen**

- `https://penpot.onelitefeather.net` erreichbar, Login möglich.
- Asset-Upload landet im Bucket: `kubectl -n rook-ceph-fr01 get obc penpot`.
- `curl -sS https://penpot-mcp.onelitefeather.net/mcp` antwortet.
- Plugin verbindet sich über `penpot-mcp-ws.onelitefeather.net`.

- [ ] **Step 4: Cloudflare Access setzen**

Beide MCP-Hosts hinter Access-Policies legen. Der MCP-Endpunkt hat **keine** eigene Authentifizierung — dieser Schritt ist verpflichtend, bevor produktiv damit gearbeitet wird.

- [ ] **Step 5: Manueller Storage-Cleanup**

Diese Ressourcen überleben das Entfernen aus Git:

```bash
# Postgres: CNPG behält Datenbanken (reclaim policy retain)
kubectl -n cnpg-system exec -it feather-core-cluster-pg-1 -- \
  psql -c 'DROP DATABASE plane;' -c 'DROP DATABASE plane_pi;' -c 'DROP ROLE plane;'

# Ceph: StorageClass ceph-bucket-fr01 hat reclaimPolicy Retain
#   Bucket plane samt Inhalt über die RGW-Admin-API oder s3cmd löschen.

# PVCs: Helm löscht StatefulSet-Volumes nie
kubectl -n plane get pvc
kubectl -n plane delete pvc --all

# Namespaces auf Reste prüfen, dann entfernen
kubectl delete namespace plane leantime
```

MariaDB braucht keinen Schritt: `Database` und `User` `leantime` haben
`cleanupPolicy: Delete`, die Datenbank wird beim Entfernen der CR gedroppt.

- [ ] **Step 6: Cloudflare-Routen aufräumen**

Tunnel-Routen für `tasks.onelitefeather.net` und `leantime.onelitefeather.net` entfernen.
