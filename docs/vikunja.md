# Vikunja

Selbst gehostete To-do-/Projektverwaltung. Ein einziger Container liefert API
und Frontend auf Port 3456; der gesamte Zustand liegt außerhalb des Pods:

| | |
|---|---|
| Datenbank | CNPG `feather-core-cluster-pg`, Datenbank + Rolle `vikunja` |
| Dateien | Ceph-RGW-Bucket `vikunja` (S3), Prefix `files/` |
| Cache / Rate-Limit / OIDC-Provider-Cache | Dragonfly, DB 14 |
| Login | Microsoft Entra ID (OIDC), lokaler Login abgeschaltet |
| Öffentlich | `https://vikunja.onelitefeather.net` (Cloudflare Tunnel) |
| Intern | `https://vikunja.apps.onelite.feather` (Envoy Gateway) |

Deshalb laufen zwei Replicas ohne PVC.

## Konfiguration: Datei plus Umgebungsvariablen

Vikunja liest `config.yml` aus u. a. `/etc/vikunja/`. Das Chart rendert die
`config`-Values in eine ConfigMap und mountet sie genau dorthin; alles
Geheime kommt über `envFrom` als `VIKUNJA_*`-Variablen dazu und wird beim
Start über die Datei gemergt (`setConfigFromEnv` → `viper.MergeConfigMap`).

Der Variablenname entsteht durch Aufsplitten an `_`, also
`VIKUNJA_FILES_S3_SECRETKEY` → `files.s3.secretkey`. Das heißt umgekehrt:
**Config-Schlüssel mit Unterstrich im Namen lassen sich nicht über
Umgebungsvariablen setzen** (z. B. der ganze `defaultsettings`-Block) — solche
Werte müssen in die ConfigMap.

⚠️ Der OIDC-Provider ist die eine Stelle, an der die Aufteilung nicht frei ist.
`auth.openid.providers` ist eine Map, und Vikunja kennt nur Provider, die die
**Config-Datei** deklariert. Der Schlüssel `entra` samt `name`, `authurl` und
`scope` steht deshalb in `release.yaml`; nur `clientid` und `clientsecret`
kommen aus dem Secret.

Was der Pod tatsächlich geladen hat:

```bash
kubectl -n vikunja logs deploy/vikunja | grep "Using config file"
```

## Einmalig: den S3-Bucket anlegen

Vikunja legt seinen Bucket **nicht** selbst an — der S3-Backend-Code kennt kein
`CreateBucket`, und beim Start prüft `ValidateFileStorage()` den Schreibzugriff.
Die `ObjectBucketClaim` in `infrastructure/.../rook-fr01/buckets/vikunja.yaml`
provisioniert ebenfalls nichts, sie ist nur Namensreservierung (siehe
`docs/buckets.md`). Nach dem ersten Sync des `security`- bzw. `rook-fr01`-Layers
also einmal von Hand:

```bash
# wartet, bis Rook den RGW-User mit den vorgegebenen Keys angelegt hat
kubectl -n rook-ceph-fr01 get cephobjectstoreuser vikunja

kubectl run bucket-init --rm -i --restart=Never -n vikunja \
  --image=amazon/aws-cli:2.17.60 \
  --overrides='{"spec":{"containers":[{"name":"bucket-init","image":"amazon/aws-cli:2.17.60","command":["aws","--endpoint-url=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80","--region=us-east-1","s3","mb","s3://vikunja"],"env":[{"name":"AWS_ACCESS_KEY_ID","valueFrom":{"secretKeyRef":{"name":"vikunja-s3","key":"VIKUNJA_FILES_S3_ACCESSKEY"}}},{"name":"AWS_SECRET_ACCESS_KEY","valueFrom":{"secretKeyRef":{"name":"vikunja-s3","key":"VIKUNJA_FILES_S3_SECRETKEY"}}}]}]}}'
```

Der Bucket gehört damit dem User `vikunja` — die Voraussetzung dafür, dass er
über die eigenen Keys erreichbar bleibt (siehe den RGW-`AccessDenied`-Vorfall
in `docs/incidents/`).

### Warum die S3-Keys im Repo stehen

Der `CephObjectStoreUser` bekommt seine Zugangsdaten über `spec.keys`
vorgegeben, statt sie sich von Rook generieren zu lassen. Dadurch entfällt der
sonst übliche Handgriff, die Keys aus dem operator-erzeugten Secret in
`rook-ceph-fr01` in das App-Secret zu kopieren. Beide Seiten lesen dieselben
Werte:

- `infrastructure/.../rook-fr01/users/vikunja-s3.sops.env` (`access-key` / `secret-key`)
- `apps/.../base-apps/vikunja/vikunja-s3.sops.env` (`VIKUNJA_FILES_S3_*`)

Wer eines der beiden dreht, muss das andere mitziehen.

Endpoint ist bewusst der clusterinterne RGW-Service, nicht
`s3.onelitefeather.net`: Cloudflare schreibt dort `Accept-Encoding` um und
zerlegt damit die SigV4-Signatur.

## Observability

**Traces** entstehen am Envoy Gateway. Der EnvoyProxy exportiert OTel-Spans mit
10 % Sampling nach Tempo (`infrastructure/.../configs/gateway/envoyproxy.yaml`),
und die HTTPRoute für `vikunja.apps.onelite.feather` hängt an genau diesem
Gateway. Der öffentliche Weg über den Cloudflare Tunnel geht direkt auf den
Service und **umgeht Envoy** — dort entstehen keine Spans (cloudflared kann
selbst kein Tracing, siehe cloudflare/cloudflared#671).

Vikunja selbst ist nicht OTel-instrumentiert; es gibt weder einen OTLP-Exporter
noch Trace-Propagation im Backend. Serverseitige Spans über den Gateway-Hop
hinaus sind damit heute nicht zu haben. Was ginge, wenn mehr gebraucht wird:
`sentry.frontendenabled` + `sentry.frontenddsn` gegen das clusterinterne Sentry
— das Frontend läuft mit `tracesSampleRate: 1.0` und liefert echte
Performance-Traces. Dafür fehlt bisher nur ein Sentry-Projekt samt DSN.

**Metriken** liegen auf `/api/v1/metrics` und werden per ServiceMonitor
gescraped. Der Endpunkt kennt keine Session-Prüfung, und der Cloudflare Tunnel
veröffentlicht jeden Pfad — deshalb ist er mit Basic Auth abgesichert.
Prometheus liest dieselben Secret-Schlüssel, die auch der Container liest
(`VIKUNJA_METRICS_USERNAME` / `VIKUNJA_METRICS_PASSWORD` in `vikunja-core`).

**Logs** gehen als JSON nach stdout und werden über das Pod-Label
`logs.onelitefeather.net/env: prod` von Alloy eingesammelt.

## Betriebsfallen

**`service.secret` nicht anfassen.** Damit werden die JWTs signiert; ein neuer
Wert wirft alle aktiven Sessions raus. Er liegt in `vikunja-core`.

**Secret-Änderungen rollen nichts.** Die Overlays setzen
`disableNameSuffixHash: true`, der Secret-Name bleibt also gleich. Nach jeder
Änderung an einem `*.sops.env`:

```bash
kubectl -n vikunja rollout restart deploy/vikunja
```

Änderungen an den `config`-Values rollen dagegen von selbst — das Chart hängt
eine `checksum/config`-Annotation an die Pod-Vorlage.

**Kein SMTP-Relay im Cluster.** `service.enableemailreminders` ist deshalb
aus; ohne das würden Erinnerungsmails nur in die Queue laufen. Auch
Passwort-Reset per Mail gibt es nicht — was folgenlos ist, solange Entra der
einzige Login-Weg ist.

**Connection-Pool.** Vikunja würde per Default 100 offene und 50 idle
Verbindungen pro Instanz halten. Der geteilte Postgres steht bei
`max_connections=200` insgesamt, zwei Replicas auf den Defaults hätten also das
ganze Budget für sich beansprucht. Gesetzt sind 10/2.
