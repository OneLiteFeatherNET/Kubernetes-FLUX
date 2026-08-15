# Penpot — Login über Microsoft Entra ID (OIDC)

Penpot authentifiziert gegen **denselben Entra-Tenant**, den der Rest des
Clusters schon nutzt (dependency-track, apus, grafana):

```
Tenant   1a14dfb5-0eac-41bf-94cb-195c2e387520
Issuer   https://login.microsoftonline.com/1a14dfb5-0eac-41bf-94cb-195c2e387520/v2.0
```

## Chart-Fallstricke

Zwei Defaults des Penpot-Charts brechen den Login, wenn man sie stehen lässt.
Beide sind in `release.yaml` überschrieben:

| Value | Chart-Default | Hier gesetzt | Warum |
|---|---|---|---|
| `config.providers.oidc.scopes` | `"scope1 scope2"` | `"openid profile email"` | Der Default ist ein wörtlicher Platzhalter. Penpot braucht mindestens `name` und `email` in der Userinfo-Antwort. |
| `config.providers.oidc.roles` | `"designer developer"` | `""` | Sonst wird ein Rollen-Claim erwartet, den Entra hier nicht liefert. Rollenprüfung ist bewusst aus. |

Zusätzlich braucht es `enable-login-with-oidc` in `config.flags` — die
`providers.oidc.enabled: true` allein reicht nicht.

`enable-login-with-password` bleibt vorerst zusätzlich aktiv, damit ein
kaputter Entra-Zustand niemanden aussperrt. Nimm es heraus, sobald OIDC im
Alltag trägt.

## Die Registrierung (bereits angelegt)

| | |
|---|---|
| Anzeigename | `Penpot` |
| Application (client) ID | `9c41505f-c12c-432a-837b-856366f67ca0` |
| Service-Principal-Objekt | `a6f551a5-03fe-49fb-833e-f2133e2fb1e0` |
| Sign-in audience | `AzureADMyOrg` (Single Tenant) |
| Redirect URI | `https://penpot.onelitefeather.net/api/auth/oidc/callback` |
| Graph-Scopes | `openid`, `profile`, `email`, `User.Read` (delegiert, Admin-Consent erteilt) |
| Client Secret | Anzeigename `penpot-oidc`, **läuft 2 Jahre nach Anlage ab** |

Client ID und Secret liegen verschlüsselt in
`apps/clusters/feathre-core/base-apps/penpot/penpot-oidc.sops.env`.

Die Abschnitte unten dokumentieren, wie das erzeugt wurde — für die
Neuanlage nach einem Verlust oder für die Secret-Rotation.

### Neu anlegen

Der Redirect-Pfad ist `/api/auth/oidc/callback` — **nicht**
`/api/auth/oauth/oidc/callback`.

```bash
cat > /tmp/penpot-rra.json <<'EOF'
[
  {
    "resourceAppId": "00000003-0000-0000-c000-000000000000",
    "resourceAccess": [
      {"id": "37f7f235-527c-4136-accd-4a02d197296e", "type": "Scope"},
      {"id": "14dad69e-099b-42c9-810b-d002981feec1", "type": "Scope"},
      {"id": "64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0", "type": "Scope"},
      {"id": "e1fe6dd8-ba31-4d61-89e7-88639da4683d", "type": "Scope"}
    ]
  }
]
EOF

az ad app create \
  --display-name "Penpot" \
  --sign-in-audience AzureADMyOrg \
  --web-redirect-uris "https://penpot.onelitefeather.net/api/auth/oidc/callback" \
  --required-resource-accesses /tmp/penpot-rra.json
```

Die vier Scope-IDs sind die delegierten Microsoft-Graph-Berechtigungen
`openid`, `profile`, `email` und `User.Read` — dieselbe Kombination, die die
Outline-Registrierung nutzt.

Danach den Service Principal anlegen und Admin-Consent erteilen, sonst
bekommt jeder Nutzer beim ersten Login einen Zustimmungsdialog:

```bash
az ad sp create --id "$APP_ID"
az ad app permission admin-consent --id "$APP_ID"
```

### Client Secret erzeugen und ins Repo bringen (auch die Rotation)

```bash
APP_ID="$(az ad app list --display-name Penpot --query '[0].appId' -o tsv)"
SECRET="$(az ad app credential reset --id "$APP_ID" --display-name penpot-oidc \
            --years 2 --query password -o tsv)"

cd apps/clusters/feathre-core/base-apps/penpot
printf 'oidc-client-id=%s\noidc-client-secret=%s\n' "$APP_ID" "$SECRET" > penpot-oidc.sops.env
unset SECRET
sops -e -i --input-type dotenv --output-type dotenv penpot-oidc.sops.env
```

Danach committen und pushen. Weil
`generatorOptions.disableNameSuffixHash: true` gesetzt ist, ändert sich der
Secret-Name nicht — das Backend muss also neu gestartet werden, damit es die
Werte liest:

```bash
kubectl -n penpot rollout restart deploy/penpot-backend
```

⚠️ Das Secret läuft nach zwei Jahren ab. Ohne Erneuerung bricht der Login.

## Aufräumen: verwaiste Registrierung

Im Tenant liegt noch eine App-Registrierung **Leantime**
(`7fadb430-898a-4bb5-8b0d-d3e8b2741a1f`). Leantime ist aus dem Cluster
entfernt; die Registrierung hat keinen Zweck mehr und sollte gelöscht werden:

```bash
az ad app delete --id 7fadb430-898a-4bb5-8b0d-d3e8b2741a1f
```

Für Plane existiert keine Registrierung — Plane nutzte kein Entra.
