# Penpot ersetzt Plane und Leantime — Design

**Datum:** 2026-08-15
**Cluster:** `feather-core`
**Status:** freigegeben, Implementierung folgt

## Ziel

Plane (Projektmanagement, `tasks.onelitefeather.net`) und Leantime
(Projektmanagement, `leantime.onelitefeather.net`) verlassen den Cluster.
An ihre Stelle tritt **Penpot**, die Open-Source-Design- und
Prototyping-Plattform, als Team-Instanz — inklusive des offiziellen
MCP-Servers, damit Claude-Clients direkt mit den Design-Dateien arbeiten
können.

## MCP-Recherche: Ergebnis

Penpot liefert seit Version **2.17** einen eigenen, offiziellen MCP-Server
mit. Ein Drittanbieter-Server (`montevive/penpot-mcp`, `zcube/penpot-mcp-server`)
wird nicht gebraucht.

Das offizielle Helm-Chart deployt ihn als First-Class-Komponente:

- Image `penpotapp/mcp:2.17.0`, eigenes Deployment, Service, PDB, HPA, VPA.
- Aktiviert wird er **ausschließlich** über das Vorhandensein von `enable-mcp`
  in `config.flags`. Es gibt kein separates `mcp.enabled` — der Helper
  `penpot.mcpEnabled` prüft genau diesen String.
- Der Container läuft mit `PENPOT_MCP_REMOTE_MODE=true`.
- Zwei Ports auf **einem** Service:
  - **4401** HTTP/SSE — der Endpunkt für AI-Clients (Claude Code, Claude
    Desktop, Cursor). Streamable HTTP unter `/mcp`, Legacy-SSE unter `/sse`.
  - **4402** WebSocket — hier verbindet sich das Penpot-MCP-Plugin.

Ablauf: AI-Client → 4401 → MCP-Server → 4402 (WebSocket) → Plugin im Browser
→ Penpot Plugin-API. Der Server kann Design-Daten lesen **und** Elemente
anlegen und verändern.

**Konsequenz für das Netzwerk-Design:** Das Plugin läuft im Browser der
Team-Mitglieder, nicht im Cluster. Deshalb müssen **beide** Ports von außen
erreichbar sein, nicht nur 4401. Das Chart deckt das nicht ab — sein
`ingress.yml`-Template routet ausschließlich auf den Frontend-Service. Die
Ingresses für MCP schreiben wir selbst.

## Bestandsaufnahme: Entfernungsumfang

### Plane

| Pfad | Art |
|---|---|
| `apps/base/plane/` | Base (kustomization, namespace, release) |
| `apps/clusters/feathre-core/base-apps/plane/` | Overlay inkl. `plane.sops.env`, `cf-origin-tls.sops.*`, `silo-service.yaml` |
| `apps/clusters/feathre-core/base-apps/kustomization.yaml` | Eintrag `plane` |
| `infrastructure/clusters/feather-core/base-sources/plane.yml` | HelmRepository |
| `infrastructure/clusters/feather-core/base-sources/kustomization.yaml` | Eintrag `plane.yml` |
| `infrastructure/.../configs/postgresql/database/plane.yaml` | CNPG Database |
| `infrastructure/.../configs/postgresql/database/plane-pi.yaml` | CNPG Database |
| `infrastructure/.../configs/postgresql/roles/plane.sops.env` | CNPG Rolle |
| `infrastructure/.../configs/postgresql/kustomization.yaml` | 2 Resource-Einträge + `role-plane` secretGenerator |
| `infrastructure/.../rook-fr01/buckets/plane.yaml` | ObjectBucketClaim |
| `infrastructure/.../rook-fr01/users/plane.yaml` | CephObjectStoreUser |
| `infrastructure/.../rook-fr01/{buckets,users}/kustomization.yaml` | je ein Eintrag |

### Leantime

| Pfad | Art |
|---|---|
| `apps/base/leantime/` | Base |
| `apps/clusters/feathre-core/base-apps/leantime/` | Overlay inkl. `leantime.sops.env`, `cf-origin-tls.sops.*` |
| `apps/clusters/feathre-core/base-apps/kustomization.yaml` | Eintrag `leantime` |
| `helm/leantime/` | kompletter In-Repo-Chart (v0.2.0, appVersion 3.5.12) |
| `infrastructure/.../configs/mariadb-galera/databases/leantime.yaml` | MariaDB Database |
| `infrastructure/.../configs/mariadb-galera/users/leantime.yaml` | MariaDB User |
| `infrastructure/.../configs/mariadb-galera/grants/leantime.yaml` | MariaDB Grant |
| `infrastructure/.../configs/mariadb-galera/passwords/leantime.sops.env` | Passwort |
| `infrastructure/.../configs/mariadb-galera/{databases,users,grants,passwords}/kustomization.yaml` | je ein Eintrag |
| `infrastructure/.../rook-fr01/users/leantime.yaml` | CephObjectStoreUser |
| `infrastructure/.../rook-fr01/users/kustomization.yaml` | Eintrag |

### Dokumentation

- `infrastructure/.../base-configs/priorityclasses.yaml` — die Beschreibung von
  `feather-standard` nennt `leantime`.
- `README.md` — listet `leantime` unter den In-Repo-Charts.
- `CLAUDE.md` — nennt `leantime` als In-Repo-Chart.
- `docs/dragonfly-redis-allocations.md` — DB 12 (Plane) wird frei, DB 13 wird
  von Penpot belegt.

## Reclaim-Verhalten: was Git nicht löscht

Die Entscheidung lautet „alles in einem Rutsch entfernen". Das Entfernen aus
Git löscht jedoch nur Deklarationen; ob die Daten tatsächlich verschwinden,
hängt an der jeweiligen Reclaim-Policy. Diese unterscheiden sich:

| Ressource | Policy | Effekt |
|---|---|---|
| MariaDB `Database`/`User` `leantime` | `cleanupPolicy: Delete` | Datenbank wird **sofort gedroppt**. Leantime-Daten sind unwiederbringlich weg (außer über MariaDB-Backups im `mariadb-galera-backup`-Bucket). |
| CNPG `Database` `plane`, `plane-pi` | CNPG-Default `retain` | Postgres-Datenbanken **bleiben** bestehen. Manuelles `DROP DATABASE` nötig. |
| Ceph `ObjectBucketClaim` `plane` | StorageClass `ceph-bucket-fr01`, `reclaimPolicy: Retain` | Bucket **und Objekte bleiben** in Ceph und belegen weiter Kapazität. |
| PVCs `plane-rabbitmq-wl`, `plane-opensearch-wl` | StatefulSet-VolumeClaimTemplates | Helm löscht diese **nie**. Bleiben als verwaiste RBD-Images. |
| `CephObjectStoreUser` `plane`, `leantime` | Rook-verwaltet | Werden mit der CR entfernt. |

Deshalb gehört eine manuelle Cleanup-Checkliste zum Ergebnis. Der Cluster hat
bereits ein dokumentiertes Kapazitätsthema
(`docs/superpowers/plans/2026-08-03-ceph-capacity-reclamation-and-retention.md`);
verwaiste Buckets und RBD-Images dürfen nicht liegen bleiben.

## Penpot-Architektur

### Chart-Source

Neue `HelmRepository` `penpot` in
`infrastructure/clusters/feather-core/base-sources/penpot.yaml`, URL
`https://helm.penpot.app` (HTTPS bestätigt funktionsfähig, die offizielle Doku
nennt HTTP). Chart `penpot`, Version auf **1.7.0** gepinnt (appVersion 2.17.0).
Renovate übernimmt danach die Bumps.

### Zwei-Tier-Kustomize

- `apps/base/penpot/` — `kustomization.yaml`, `namespace.yaml`, `release.yaml`
  (HelmRelease-Skelett mit `values: {}`).
- `apps/clusters/feathre-core/base-apps/penpot/` — `kustomization.yaml` mit
  `patches: - path: release.yaml`, die Ingresses und die secretGenerator-Blöcke.
- Eintrag `penpot` in `apps/clusters/feathre-core/base-apps/kustomization.yaml`.

### PostgreSQL

Neue CNPG `Database` `penpot` (Owner `penpot`) im Cluster
`feather-core-cluster-pg`, plus Rolle über `roles/penpot.sops.env` und einen
`role-penpot`-secretGenerator mit `cnpg.io/reload: "true"`.

Verbindung gegen `feather-core-cluster-pg-rw.cnpg-system.svc.cluster.local:5432`
— den **`-rw`-Service direkt, nicht den PgBouncer-Pooler**. Dasselbe Muster wie
n8n; PgBouncer lehnt bestimmte Startup-Parameter ab und Penpot ist hier nicht
getestet.

Values:

```yaml
config:
  postgresql:
    host: feather-core-cluster-pg-rw.cnpg-system.svc.cluster.local
    port: 5432
    database: penpot
    existingSecret: penpot-db
    secretKeys:
      usernameKey: username
      passwordKey: password
```

Das Secret `penpot-db` wird aus `penpot-db.sops.env` generiert und muss
dasselbe Passwort tragen wie die CNPG-Rolle.

### Redis / Valkey

Die geteilte Dragonfly-Instanz, **DB 13** (laut
`docs/dragonfly-redis-allocations.md` frei; 3, 4, 14, 15 bleiben frei, 12 wird
durch Planes Abbau ebenfalls frei).

Das Chart erwartet einen **kompletten URI in einem einzigen Secret-Key**:

```yaml
config:
  redis:
    existingSecret: penpot-redis
    secretKeys:
      redisUriKey: redis-uri
```

Inhalt von `penpot-redis.sops.env`:
`redis-uri=redis://:<dragonfly-passwort>@dragonfly.dragonfly.svc.cluster.local:6379/13`

### Assets: Ceph S3

Neuer `ObjectBucketClaim` `penpot` (`storageClassName: ceph-bucket-fr01`,
`bucketOwner: penpot`) und `CephObjectStoreUser` `penpot` im Store `feather-s3`
— gleiches Muster wie Outline und Plane.

```yaml
config:
  objectsStorage:
    storageBackend: s3
    s3:
      bucket: penpot
      region: fr01
      existingSecret: penpot-s3
      secretKeys:
        accessKeyIDKey: access-key-id
        secretAccessKey: secret-access-key
        endpointURIKey: endpoint-uri
persistence:
  assets:
    enabled: false
```

`persistence.assets.enabled: false` ist wichtig: der Chart-Default ist `true`
und legt sonst zusätzlich ein ungenutztes 20-GiB-RWO-PVC an.

### Exposure

`ingress.enabled: false` im Chart — stattdessen eigene Cloudflare-Tunnel-
Ingresses, wie es Leantime und Plane bereits taten
(`ingressClassName: cloudflare-tunnel`, Annotation
`cloudflare-tunnel-ingress-controller.strrl.dev/backend-protocol: http`):

| Host | Ziel-Service | Port |
|---|---|---|
| `penpot.onelitefeather.net` | `penpot` | 8080 |
| `penpot-mcp.onelitefeather.net` | `penpot-mcp` | 4401 |
| `penpot-mcp-ws.onelitefeather.net` | `penpot-mcp` | 4402 |

Dazu das `cf-origin-tls`-Secret-Paar wie bei den anderen getunnelten Apps.

### Flags

Abweichend vom Chart-Default:

```yaml
config:
  flags: "disable-registration enable-login-with-password disable-email-verification enable-mcp"
  publicUri: "https://penpot.onelitefeather.net"
  telemetryEnabled: false
```

- `disable-registration` statt `enable-registration` — es ist eine
  Team-Instanz; ohne das kann jeder mit der URL ein Konto anlegen.
- `enable-smtp` entfällt: im Repo existiert keine geteilte SMTP-Konfiguration.
  Folge: keine Einladungs-Mails und kein Passwort-Reset per Mail. Team-Konten
  werden manuell angelegt. Sobald ein Relay verfügbar ist, kann `enable-smtp`
  plus `config.smtp.*` nachgezogen werden.
- `enable-mcp` bleibt — es ist der einzige Schalter für die MCP-Komponente.

### Scheduling und Ressourcen

`postRenderers` setzen `priorityClassName: feather-standard` auf alle
Deployments des Charts (Frontend, Backend, Exporter, MCP) — dasselbe Muster wie
bei Leantime und Plane. `podLabels` mit
`logs.onelitefeather.net/env: prod` je Komponente, damit Alloy die Logs
einsammelt. Requests und Limits werden explizit gesetzt statt Chart-Defaults zu
übernehmen.

Das MCP-Deployment des Charts definiert **keine** Liveness- oder
Readiness-Probes. Das ist beim Betrieb zu beachten: ein hängender MCP-Pod wird
nicht automatisch neu gestartet.

## Sicherheit

Der MCP-Endpunkt bringt **keine eigene Authentifizierung** mit. Wer die URL
kennt, kann über den MCP-Pfad Design-Daten lesen und verändern. Beide
MCP-Hosts (`penpot-mcp`, `penpot-mcp-ws`) müssen daher vor dem produktiven
Gebrauch hinter Cloudflare Access gelegt werden. Das geschieht in der
Cloudflare-Konfiguration, nicht in diesem Repo — im Repo existieren keine
Cloudflare-Access-Manifeste. Der Schritt ist verpflichtend, nicht optional.

## Reihenfolge

Getrennte Commits, Penpot zuerst, damit eine laufende Alternative existiert,
bevor Plane verschwindet:

1. Penpot-Infrastruktur — HelmRepository, CNPG Database + Rolle, Ceph Bucket +
   User.
2. Penpot-App — Base, Overlay, HelmRelease-Werte, Ingresses, Secrets.
3. Plane-Entfernung.
4. Leantime-Entfernung inklusive `helm/leantime/`.
5. Dokumentation — Dragonfly-Tabelle, PriorityClass-Beschreibung, README,
   CLAUDE.md, Cleanup-Checkliste.

## Verifikation

- `./scripts/validate.sh` — lokal, identisch zu CI, nach jedem Schritt.
- `scripts/check-sops-encryption.py` — nach jeder neu angelegten SOPS-Datei.
- Nach dem Push: `flux get kustomizations -A`, `flux get helmrelease -n penpot`.
- Funktionsprüfung: Penpot-UI erreichbar, Login möglich, Asset-Upload landet im
  Ceph-Bucket, MCP-Endpunkt `https://penpot-mcp.onelitefeather.net/mcp`
  antwortet, Plugin verbindet sich über den WS-Host.

## Manuelle Cleanup-Checkliste (nach dem Merge)

Diese Schritte laufen nicht über Git und müssen bewusst ausgeführt werden:

1. Postgres: `DROP DATABASE plane;` und `DROP DATABASE plane_pi;`, danach
   `DROP ROLE plane;`.
2. Ceph: Bucket `plane` samt Inhalt löschen (Retain-Policy lässt ihn sonst
   stehen).
3. PVCs im Namespace `plane` prüfen und löschen — insbesondere die von
   `plane-rabbitmq-wl` und `plane-opensearch-wl`.
4. Namespaces `plane` und `leantime` auf Reste prüfen, dann entfernen.
5. Cloudflare: Tunnel-Routen für `tasks.onelitefeather.net` und
   `leantime.onelitefeather.net` entfernen, Routen für die drei
   Penpot-Hosts anlegen und Access-Policies setzen.
