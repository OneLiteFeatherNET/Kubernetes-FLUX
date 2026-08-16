# Sentry self-hosted auf feather-core — Design

**Datum:** 2026-08-16
**Cluster:** `feather-core`
**Status:** freigegeben, Implementierung folgt

## Ziel

Sentry self-hosted produktionsreif im Cluster betreiben, mit SSO über
Microsoft Entra ID und unter Nutzung der vorhandenen Datenbanken.

## Ausgangslage

Sentry ist keine einzelne App. Das Chart `sentry` 33.3.0 (App **26.7.2**)
deployt Sentry web/worker/cron, Snuba-Consumer, Relay, Symbolicator,
Uptime-Checker, Taskworker und Memcached.

Nachgemessen mit `helm template` gegen die tatsächlichen Values: **52
Deployments** — davon allein 23 Snuba-Consumer — plus 4
Taskbroker-StatefulSets, also rund **56 Pods**. Die ursprüngliche Schätzung
von 25–30 in diesem Dokument war deutlich zu niedrig.

Vorhanden im Cluster: CNPG-Postgres, Dragonfly (Redis-kompatibel), MariaDB
Galera, Ceph RGW, Cloudflare-Tunnel-Ingress, Entra ID als SSO-Provider.

**Nicht vorhanden und daher neu zu bauen: Kafka und ClickHouse.**

Kapazität ist unkritisch: vier Worker mit je 32 CPU / 64 GiB bei rund 33 %
Speicherauslastung. Sentry braucht geschätzt 16–24 GiB.

## Entscheidungen

| Baustein | Entscheidung |
|---|---|
| ClickHouse | `clickhouse`-Chart 4.1.1 aus dem sentry-kubernetes-Repo, Image auf **25.3.6 Altinity-stable** überschrieben |
| Kafka | Strimzi-Operator, Chart 1.1.0 |
| Redis | geteilte Dragonfly, **DB 3** |
| Filestore | Ceph S3 / RGW |
| Postgres | CNPG, `-rw`-Service direkt |

### ClickHouse: das Chart ja, seine Version nein

⚠️ **Korrektur nach dem ersten Deployment.** Dieser Abschnitt behauptete
ursprünglich, das Chart pinne „die Version, gegen die Snuba getestet wird".
Das war falsch und hat den ersten Install gekostet.

Das Chart trägt appVersion **23.8**. Sentry 26.7 baut upstream jedoch aus
`altinity/clickhouse-server:25.3.6.10034.altinitystable`. Auf 23.8 sterben die
`events_analytics_platform`-Migrationen 0049 und 0050 mitten im Lauf und
bleiben als `in_progress` stehen, was jede weitere Migration blockiert —
`sentry-snuba-migrate` scheitert dann dauerhaft mit `MigrationInProgress`.

Bleibt es beim Chart, muss das Image also **explizit überschrieben** werden.
Beim nächsten Chart-Update ist zu prüfen, ob die Version dann noch passt; die
Kopplung Snuba ↔ ClickHouse ist eng und das Chart hinkt hinterher.

Zweiter Fallstrick: der Replica-StatefulSet hängt an
`configmap.remote_servers.replica.backup.enabled`, **nicht** an `replicas`.
`replicas: 1` allein lässt einen zweiten ClickHouse laufen, der ohne Zookeeper
nie synchron sein kann.

Der Altinity-Operator wäre betrieblich stärker (Replikation, Rolling
Upgrades). Er bleibt die naheliegende Alternative, falls die Versionspflege
über das Chart lästig wird.

### Kafka: Strimzi ohne Topic Operator

`Kafka`-CR im **KRaft-Modus** (kein Zookeeper) mit 3 Replicas über
`KafkaNodePool`, Storage auf `ceph-rbd-fr01`.

**Der Topic Operator bleibt bewusst deaktiviert.** Sentry braucht **116
Topics** mit teils spezieller Konfiguration — `cleanup.policy: compact,delete`
für die Commit-Log-Topics, `max.message.bytes: 15000000` für
`ingest-replay-events`, `message.timestamp.type: LogAppendTime` für die
Ingest-Topics. Diese Topics anzulegen übernimmt das Chart selbst: das Template
`kafka-provisioning` ist über

```
{{- if and (not .Values.kafka.enabled) .Values.externalKafka.provisioning.enabled }}
```

gegated und läuft damit **auch gegen ein externes Kafka**. Die Topic-Liste
fällt dabei auf die vollständige Chart-Liste zurück:

```
{{- $topics := $provisioning.topics | default .Values.kafka.provisioning.topics }}
```

Angewendet werden sie mit Segment `topicctl`. 116 `KafkaTopic`-CRs von Hand zu
pflegen entfällt damit — und ein aktiver Topic Operator würde mit `topicctl`
konkurrieren.

### Redis: geteilte Dragonfly, mit Vorbehalt

DB 3 (frei laut `docs/dragonfly-redis-allocations.md`; ebenfalls frei: 4, 12,
14, 15).

Sentry nutzt Redis nicht nur als Cache, sondern als **Celery-Broker** — eine
stoßige Schreiblast auf einer Instanz, die sich bereits Harbor, Outline, n8n,
shlink und Penpot teilen. Das ist eine bewusste Entscheidung gegen Isolation
zugunsten geringerer Komplexität. Zur Absicherung gehört ein Alert auf die
Dragonfly-Latenz zum Lieferumfang, damit eine Beeinträchtigung anderer Apps
auffällt, bevor Nutzer sie melden. Ein späterer Umzug auf eine eigene Instanz
ist ein reiner Values-Change.

### Postgres: -rw statt Pooler

CNPG-Datenbank `sentry` mit Rolle `sentry`, verbunden gegen
`feather-core-cluster-pg-rw.cnpg-system.svc.cluster.local:5432`. Der
**chart-eigene PgBouncer bleibt aus** (`pgbouncer.enabled: false`) und der
CNPG-Pooler wird nicht verwendet — dieselbe Begründung wie bei n8n: PgBouncer
lehnt bestimmte Startup-Parameter ab.

Die Rolle muss zusätzlich in `configs/postgresql/cluster.yaml` unter
`managed.roles` eingetragen werden; ein `roles/*.sops.env` plus
secretGenerator allein legt keine Rolle an (Lehre aus der Penpot-Migration).

### Filestore

`filestore.backend: s3` gegen `feather-s3`, neuer `ObjectBucketClaim` und
`CephObjectStoreUser` `sentry`, Endpoint `https://s3.onelitefeather.net`,
Region `us-east-1` — dasselbe Muster wie Penpot und Outline.

## SSO über Entra ID

SAML2 ist in self-hosted Sentry **seit 20.6.0 eingebaut**, ohne
Lizenzschranke. Konfiguriert wird es in Sentrys `config.yaml`, nicht in
`sentry.conf.py`.

Entra-Seite analog zu Penpot: App-Registrierung per `az` im Tenant
`1a14dfb5-0eac-41bf-94cb-195c2e387520`, Zugangsdaten SOPS-verschlüsselt.

⚠️ **Reihenfolge ist kritisch.** Sentrys Doku ist eindeutig: sobald SSO aktiv
ist, ist es der einzige Login-Weg. Daher zwingend:

1. Sentry ohne SSO hochfahren.
2. Ersten Superuser anlegen und Login prüfen.
3. Erst dann SSO aktivieren.

Andersherum sperrt man sich aus der eigenen Instanz aus.

## Exposure

Cloudflare-Tunnel-Ingress auf `sentry.onelitefeather.net`, wie bei den
übrigen getunnelten Apps (`ingressClassName: cloudflare-tunnel`, Annotation
`cloudflare-tunnel-ingress-controller.strrl.dev/backend-protocol: http`).
Der chart-eigene Ingress und nginx bleiben aus.

Relay nimmt die SDK-Events unter demselben Host entgegen.

## Flux-Einordnung

| Ressource | Layer |
|---|---|
| `HelmRepository` strimzi, sentry-kubernetes | `base-sources` |
| Strimzi-Operator | `base-controllers` |
| `Kafka`-CR, ClickHouse | `configs` |
| CNPG-Datenbank + Rolle | `configs` |
| OBC + CephObjectStoreUser | `rook-fr01` |
| Sentry | `base-apps` |

## Vorgehen: vier PRs

Jeder für sich lauffähig und prüfbar.

1. **Strimzi-Operator** — Source und Controller. Verifikation: Operator-Pod
   Ready, CRDs vorhanden.
2. **Infrastruktur** — Kafka-CR, ClickHouse, CNPG-Datenbank und Rolle, Ceph
   Bucket und User, Sentry-HelmRepository. Verifikation: Kafka-Cluster Ready,
   ClickHouse erreichbar.
3. **Sentry ohne SSO** — HelmRelease, Ingress, Secrets. Verifikation: UI
   erreichbar, Superuser angelegt, Test-Event kommt an.
4. **SSO** — Entra-App-Registrierung und Sentry-Konfiguration. Erst nach
   erfolgreichem Schritt 3.

## Verifikation

- `./scripts/validate.sh` und `scripts/check-sops-encryption.py` nach jedem PR.
- `flux get kustomizations -A`, `flux get helmrelease -n sentry`.
- Kafka: alle 116 Topics angelegt (`kafka-topics --list`).
- Funktionsprüfung: Test-Event über einen SDK-DSN erzeugen und im UI sehen.
