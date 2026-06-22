# Redis → Dragonfly Migration & DB-Belegung

> Status: in Migration (gestaffelt, ein Service nach dem anderen).
> Ziel: Ablösung des ungepatchten `bitnamilegacy/redis`-Images durch DragonflyDB.

## Warum

Der zentrale Redis (`redis`-Namespace, Bitnami-Chart, Sentinel, VIP `10.200.32.5`)
läuft auf `bitnamilegacy/redis:7.4.2` — seit Bitnamis Katalog-Umstellung
(28.08.2025) **eingefroren, keine Security-Patches mehr**. Ersatz: **Dragonfly**
(Operator-managed Replikation + automatischer Failover, **ohne Sentinel**).
Der Failover-Test war erfolgreich (Umschaltung ~1–2 s, kein Datenverlust,
Service folgt automatisch dem Master).

## Zugang (gemeinsame Dragonfly-Instanz)

| | |
|---|---|
| Endpoint (Service folgt dem Master) | `dragonfly.dragonfly.svc.cluster.local:6379` |
| Auth | Passwort identisch zum bisherigen zentralen Redis (1:1 übernommen) |
| Sentinel | entfällt — Clients nutzen **nur** den einen Service-Namen, **kein** `sentinelMasterSet` / `REDIS_SENTINEL_SERVICE` |
| Namespace | `dragonfly` (Operator: `dragonfly-operator-system`) |

## DB-Belegung (kollisionsfrei)

Jeder Service bekommt eine eigene Datenbank bzw. einen eigenen Block. Dragonfly
stellt wie Redis mehrere logische DBs bereit (`SELECT n`).

| DB | Service / Komponente | Hinweis |
|----|----------------------|---------|
| 0  | Harbor – core        | **muss 0 sein** (Harbor-Library-Limit) |
| 1  | Harbor – jobservice  | |
| 2  | Harbor – registry    | |
| 5  | Harbor – trivy       | |
| 6  | Harbor – harbor      | |
| 7  | Harbor – cache       | |
| 8  | **shlink**           | vorher DB 6 (kollidierte mit Harbor) |
| 9  | **outline** – Cache/Queues (`REDIS_URL`) | |
| 10 | **outline** – Collaboration (`REDIS_COLLABORATION_URL`) | |

Frei für künftige Services: 3, 4, 11–15.

## Rollout-Reihenfolge (gestaffelt)

Jeweils **ein** Service ausrollen, verifizieren, dann den nächsten. Die Patches
liegen als einzelne Commits auf Branch `feat/dragonfly-cutover` und werden per
Cherry-Pick auf `main` gebracht.

### 1. shlink — DB 8 *(Commit vorbereitet)*

Predis **Sentinel → Direkt**. Datei `apps/clusters/feathre-core/base-apps/shlink/release.yaml`:
`REDIS_SERVERS` → `tcp://dragonfly.dragonfly.svc.cluster.local:6379/8`,
`REDIS_SENTINEL_SERVICE` entfernt. Passwort unverändert.

Verifizieren:
```bash
flux reconcile kustomization base-apps --with-source
kubectl -n shlink rollout status deploy/shlink
kubectl -n shlink logs deploy/shlink --tail=50 | grep -iE "redis|error" || true
# Kurz-URL anlegen/auflösen testen
```

### 2. outline — DB 9 + 10 *(SOPS-Schritt durch euch)*

Die Redis-URLs liegen verschlüsselt in `apps/clusters/feathre-core/base-apps/outline/outline.sops.env`.
Bearbeiten:
```bash
cd apps/clusters/feathre-core/base-apps/outline
sops outline.sops.env
```
Werte setzen (`<pw>` = das zentrale Redis-Passwort, das Dragonfly übernommen hat):
```
REDIS_URL=redis://:<pw>@dragonfly.dragonfly.svc.cluster.local:6379/9
REDIS_COLLABORATION_URL=redis://:<pw>@dragonfly.dragonfly.svc.cluster.local:6379/10
```
Verifizieren:
```bash
flux reconcile kustomization base-apps --with-source
kubectl -n outline rollout status deploy/outline
# Realtime-Editing mit 2 Browsern testen (Collaboration), Worker/Cron prüfen
```

### 3. harbor — DB-Block 0–7 *(Commit vorbereitet)*

Datei `apps/clusters/feathre-core/base-apps/harbor/release.yaml`:
`redis.external.addr` → `dragonfly.dragonfly.svc.cluster.local:6379`.
`sentinelMasterSet` bleibt leer, DB-Indizes unverändert, Passwort unverändert.

Verifizieren:
```bash
flux reconcile kustomization base-apps --with-source
kubectl -n harbor get pods
# Push/Pull eines Images + Trivy-Scan testen
```

## Cherry-Pick-Ablauf (pro Schritt)

```bash
git fetch origin
git checkout main
git cherry-pick <commit-sha des Service>   # einzeln, in obiger Reihenfolge
git push origin main
# Flux rollt aus -> verifizieren -> nächster Service
```

## Rollback (pro Service)

Den jeweiligen Cherry-Pick-Commit auf `main` reverten und pushen — die alten
Werte zeigen wieder auf `redis.redis.svc.cluster.local` / die Sentinel-Nodes.
Der alte Bitnami-Redis bleibt bis zum vollständigen Abschluss bewusst stehen.

## Nicht betroffen

- **n8n** betreibt ein eigenes Redis (`n8n`-Namespace) — kein Konsument der zentralen Instanz.
- **leantime** hat Redis-Env-Variablen, nutzt sie aber nicht (leer).

## Vor Prod-Abschluss aktivieren (in der Dragonfly-CR)

Aktuell läuft die Dragonfly-Instanz im Trial-Modus (in-memory). Vor dem
endgültigen Abschalten des alten Redis in
`infrastructure/clusters/feather-core/controllers/dragonfly/dragonfly.yaml`:

- **Persistenz**: `snapshot` (PVC `ceph-rbd-fr01`) + `affinity` (Zone `fr01`)
- **Priorität**: `priorityClassName: feather-high`
- Threads/Memory hochskalieren (`--proactor_threads` ↔ `--maxmemory`, Regel: `maxmemory ≥ threads × 256 MiB`, unter dem Container-Limit)
