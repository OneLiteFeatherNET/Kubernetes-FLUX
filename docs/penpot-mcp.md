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

## ⚠️ Sicherheit

Der MCP-Endpunkt bringt **keine eigene Authentifizierung** mit. Wer die URL
kennt, kann Design-Daten lesen **und verändern** — der Server unterstützt
ausdrücklich auch Schreiboperationen.

Beide MCP-Hosts gehören deshalb hinter **Cloudflare Access**. Das wird in der
Cloudflare-Konfiguration gesetzt, nicht in diesem Repo — es gibt hier keine
Cloudflare-Access-Manifeste. Der Schritt ist verpflichtend, bevor produktiv
mit dem Endpunkt gearbeitet wird.

## Betriebshinweise

- Das MCP-Deployment des Charts definiert **weder Liveness- noch
  Readiness-Probes**. Ein hängender Pod wird nicht automatisch neu gestartet;
  im Zweifel `kubectl -n penpot rollout restart deploy/penpot-mcp`.
- Der Container läuft mit `PENPOT_MCP_REMOTE_MODE=true`.

## S3-Zugangsdaten nachfüllen

Die Datei `penpot-s3.sops.env` wurde initial mit dem Sentinel
`REPLACE_AFTER_ROOK_CREATES_USER` angelegt, weil Rook die Schlüssel erst
erzeugt, wenn der `CephObjectStoreUser` `penpot` im Cluster existiert. Sobald
die Infrastruktur-Layer reconciled hat:

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
