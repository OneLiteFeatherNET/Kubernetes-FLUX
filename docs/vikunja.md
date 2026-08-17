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

## S3: nichts von Hand

Vikunja legt seinen Bucket **nicht** selbst an — der S3-Backend-Code kennt kein
`CreateBucket`, und beim Start prüft `ValidateFileStorage()` den Schreibzugriff.
Das übernimmt die `ObjectBucketClaim` in
`apps/.../base-apps/vikunja/bucket.yaml`. Sie liegt bewusst im Namespace
`vikunja` und nicht bei den älteren Claims in `rook-ceph-fr01`: Rook schreibt
das Ergebnis in den Namespace der Claim, und genau dort brauchen die Pods es
(siehe `docs/buckets.md`).

Aus der Claim entstehen zwei Objekte namens `vikunja-bucket`, die das Chart
über `env` in die passenden `VIKUNJA_FILES_S3_*`-Variablen umbenennt:

| Objekt | Schlüssel | wird zu |
|---|---|---|
| ConfigMap | `BUCKET_HOST` + `BUCKET_PORT` | `VIKUNJA_FILES_S3_ENDPOINT` |
| ConfigMap | `BUCKET_NAME` | `VIKUNJA_FILES_S3_BUCKET` |
| Secret | `AWS_ACCESS_KEY_ID` | `VIKUNJA_FILES_S3_ACCESSKEY` |
| Secret | `AWS_SECRET_ACCESS_KEY` | `VIKUNJA_FILES_S3_SECRETKEY` |

Der Endpoint wird aus zwei Variablen zusammengesetzt
(`http://$(BUCKET_HOST):$(BUCKET_PORT)`). Kubernetes löst `$(VAR)` nur gegen
Einträge auf, die in derselben `env`-Liste **davor** stehen — die Reihenfolge in
`release.yaml` ist also nicht kosmetisch.

Damit gibt es für S3 kein SOPS-Secret im Repo: die Zugangsdaten sind die des
`CephObjectStoreUser vikunja`, den `additionalConfig.bucketOwner` als
Bucket-Eigentümer setzt, und Rook reicht sie selbst durch. Nur `region` und
`usepathstyle` stehen noch in der ConfigMap-Config — `BUCKET_REGION` kommt leer
zurück, und das AWS SDK braucht zum Signieren irgendeine.

Der Endpoint aus der Claim zeigt auf den clusterinternen RGW-Service, nicht auf
`s3.onelitefeather.net`. Das ist hier ein Glücksfall und keine Einstellung:
über den öffentlichen Host schreibt Cloudflare `Accept-Encoding` um und zerlegt
damit die SigV4-Signatur.

Falls der Bucket beim ersten Start noch nicht steht, bleiben die Pods in
`CreateContainerConfigError`, bis ConfigMap und Secret existieren — das heilt
sich selbst, sobald Rook die Claim gebunden hat:

```bash
kubectl -n vikunja get obc vikunja-bucket
```

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
