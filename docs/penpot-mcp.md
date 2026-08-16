# Penpot MCP — Betrieb und Client-Anbindung

Penpot bringt seit Version 2.17 einen **offiziellen MCP-Server** mit. Er wird
vom offiziellen Helm-Chart als eigenes Deployment ausgerollt; ein
Drittanbieter-Server wird nicht gebraucht.

## Wie es zusammenhängt

```
AI-Client (Claude Code / Desktop / Cursor)
   │  HTTP/SSE, Port 4401
   ▼
penpot-mcp  ──── WebSocket, Port 4402 ────►  MCP-Plugin im Browser
   │                                              │
   │                                              ▼
   │                                        Penpot Plugin-API
```

Der entscheidende Punkt: das **Plugin läuft im Browser** des Team-Mitglieds,
nicht im Cluster. Deshalb müssen beide Ports von außen erreichbar sein — 4401
für den AI-Client, 4402 für das Plugin. Das Chart-eigene Ingress-Template
routet ausschließlich auf den Frontend-Service, die beiden MCP-Ingresses in
`apps/clusters/feathre-core/base-apps/penpot/ingress.yaml` sind daher
handgeschrieben.

| Host | Service | Port | Zweck |
|---|---|---|---|
| `penpot.onelitefeather.net` | `penpot` | 8080 | Penpot-UI |
| `penpot-mcp.onelitefeather.net` | `penpot-mcp` | 4401 | MCP HTTP/SSE für AI-Clients |
| `penpot-mcp-ws.onelitefeather.net` | `penpot-mcp` | 4402 | WebSocket für das Browser-Plugin |

## Aktivierung

Es gibt **kein** `mcp.enabled` im Chart. Der Helper `penpot.mcpEnabled` prüft,
ob der String `enable-mcp` in `config.flags` steht — das ist der einzige
Schalter. Wird er aus `flags` entfernt, verschwinden Deployment und Service.

## Client-Konfiguration

Claude Code:

```bash
claude mcp add --transport http penpot https://penpot-mcp.onelitefeather.net/mcp
```

Für Clients ohne HTTP-Transport steht der Legacy-SSE-Endpunkt unter `/sse`
bereit; `mcp-remote` überbrückt auf stdio.

## Das Plugin verbinden

Ein AI-Client allein reicht nicht. Der MCP-Server führt keine Operationen
selbst aus — er reicht sie an das **Penpot-MCP-Plugin** weiter, das im Browser
in der geöffneten Design-Datei läuft. Beide Seiten werden über ein
**User-Token** einander zugeordnet.

Solange kein Plugin verbunden ist, antworten nur die Metawerkzeuge
(`high_level_overview`, `penpot_api_info`). Alles, was Designdaten anfasst,
scheitert mit:

```
No plugin instance connected for user token.
Please ensure the plugin is running and connected with the correct token.
```

Serverseitig sieht man dasselbe im Log:

```
Session initialized with id=… for userTokenFp=…
Tool execution failed: execute_code; No plugin instance connected for user token
```

Ablauf: Penpot im Browser öffnen, das MCP-Plugin starten und dort dasselbe
Token hinterlegen, das auch im AI-Client konfiguriert ist. Danach greifen
`execute_code` und `export_shape`.

## Sicherheit

Die Zugriffsschichten, von außen nach innen:

1. Eine MCP-Session lässt sich **ohne Token** eröffnen — ein anonymer
   `initialize` bekommt eine gültige Antwort mit `serverInfo`, und die
   Metawerkzeuge funktionieren.
2. Für alles Weitere bindet der Server die Session an ein User-Token.
3. Designzugriff gibt es erst, wenn unter **demselben** Token ein Plugin
   verbunden ist.

Das heißt: die URL allein genügt nicht, um Designs zu lesen oder zu
verändern. Wer aber ein Token erbeutet, zu dem gerade ein Plugin verbunden
ist, kann darüber schreiben — der Server unterstützt ausdrücklich
Schreiboperationen.

Beide MCP-Hosts gehören deshalb zusätzlich hinter **Cloudflare Access**, als
Schranke vor dem Token statt als einziger Schutz. Das wird in der
Cloudflare-Konfiguration gesetzt, nicht in diesem Repo — Access-Manifeste gibt
es hier nicht.

## Betriebshinweise

- Das MCP-Deployment des Charts definiert **weder Liveness- noch
  Readiness-Probes**. Ein hängender Pod wird nicht automatisch neu gestartet;
  im Zweifel `kubectl -n penpot rollout restart deploy/penpot-mcp`.
- Der Container läuft mit `PENPOT_MCP_REMOTE_MODE=true`.

## S3-Zugangsdaten

Die Schlüssel in `penpot-s3.sops.env` stammen aus dem Secret
`rook-ceph-object-user-feather-s3-penpot` (Namespace `rook-ceph-fr01`), das
Rook beim Anlegen des `CephObjectStoreUser` erzeugt. Sie sind eingetragen.

Neu kopieren muss man sie nur, wenn dieser User je neu angelegt wird — dann
ändern sich die Schlüssel:

```bash
cd apps/clusters/feathre-core/base-apps/penpot
AK="$(kubectl get secret rook-ceph-object-user-feather-s3-penpot -n rook-ceph-fr01 -o jsonpath='{.data.AccessKey}' | base64 -d)"
SK="$(kubectl get secret rook-ceph-object-user-feather-s3-penpot -n rook-ceph-fr01 -o jsonpath='{.data.SecretKey}' | base64 -d)"
printf 'access-key-id=%s\nsecret-access-key=%s\nendpoint-uri=https://s3.onelitefeather.net\n' "$AK" "$SK" > penpot-s3.sops.env
unset AK SK
sops -e -i --input-type dotenv --output-type dotenv penpot-s3.sops.env
```

Danach committen, pushen, und weil
`generatorOptions.disableNameSuffixHash: true` gesetzt ist, den Backend-Pod
neu starten, damit er die neuen Werte liest:

```bash
kubectl -n penpot rollout restart deploy/penpot-backend
```
