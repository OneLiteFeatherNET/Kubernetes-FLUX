# Vikunja — Login über Microsoft Entra ID (OIDC)

Vikunja authentifiziert gegen **denselben Entra-Tenant** wie der Rest des
Clusters (penpot, dependency-track, apus, grafana):

```
Tenant   1a14dfb5-0eac-41bf-94cb-195c2e387520
Issuer   https://login.microsoftonline.com/1a14dfb5-0eac-41bf-94cb-195c2e387520/v2.0
```

`auth.local.enabled: false` und `service.enableregistration: false` schalten
den lokalen Login und die `/register`-Route ab. Neue Nutzer entstehen trotzdem:
`getOrCreateUser` im OIDC-Pfad legt sie beim ersten Login an und fragt
`enableregistration` gar nicht ab — anders als bei Penpot ist hier also kein
Kompromiss nötig.

⚠️ Fällt Entra aus, kommt niemand mehr rein. Der Weg zurück ist ein Commit, der
`auth.local.enabled` wieder auf `true` setzt — nur nützt das nichts, solange
niemand ein lokales Passwort hat, und Passwort-Reset per Mail gibt es mangels
SMTP-Relay auch nicht.

## Die Registrierung (bereits angelegt)

| | |
|---|---|
| Anzeigename | `Vikunja` |
| Application (client) ID | `6971afb6-dd60-465c-89a5-31bcaa2fdd81` |
| Objekt-ID der App | `83ff3222-53d0-40e6-922d-f15a69939f81` |
| Service-Principal-Objekt | `641c7a17-9a86-4a8e-a863-ab8e2c646d29` |
| Sign-in audience | `AzureADMyOrg` (Single Tenant) |
| Redirect URIs | `https://vikunja.onelitefeather.net/auth/openid/entra`<br>`https://vikunja.apps.onelite.feather/auth/openid/entra` |
| Graph-Scopes | `openid`, `profile`, `email`, `User.Read` (delegiert) |
| Optional Claim | `email` auf ID- und Access-Token |
| Admin-Consent | erteilt (`consentType: AllPrincipals`) |
| Client Secret | Anzeigename `vikunja-oidc`, **läuft 2 Jahre nach Anlage ab** |

Client ID und Secret liegen verschlüsselt in
`apps/clusters/feathre-core/base-apps/vikunja/vikunja-oidc.sops.env`.

Der Admin-Consent ist tenantweit erteilt, es sieht also niemand einen
Zustimmungsdialog. Nachprüfen:

```bash
az ad app permission list-grants --id 6971afb6-dd60-465c-89a5-31bcaa2fdd81 \
  --query '[].{scope:scope,consentType:consentType}'
```

## Der Redirect-Pfad hängt am Provider-Schlüssel

Das Frontend baut die Redirect-URI aus dem eigenen Origin und dem
Provider-Schlüssel: `<origin>/auth/openid/<key>`. Der Schlüssel ist in
`release.yaml` `entra` — wird er umbenannt, passt die in Entra hinterlegte
Redirect-URI nicht mehr und der Login bricht mit `AADSTS50011` ab.

Die URI wird bewusst aus dem aktuellen Origin gebaut, nicht aus
`service.publicurl`. Deshalb funktionieren beide Hostnamen, obwohl
`publicurl` nur den öffentlichen kennt — beide sind in Entra registriert.

`authurl` ist der **Issuer**, nicht der Authorize-Endpunkt: Vikunja gibt ihn an
`oidc.NewProvider()` und holt sich die Endpunkte aus
`/.well-known/openid-configuration`. Der Authorize-Endpunkt, den das Frontend
später anspringt, kommt aus dieser Discovery.

## Neu anlegen

```bash
cat > /tmp/vikunja-rra.json <<'EOF'
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
  --display-name "Vikunja" \
  --sign-in-audience AzureADMyOrg \
  --web-redirect-uris \
      "https://vikunja.onelitefeather.net/auth/openid/entra" \
      "https://vikunja.apps.onelite.feather/auth/openid/entra" \
  --required-resource-accesses /tmp/vikunja-rra.json

APP_ID="$(az ad app list --display-name Vikunja --query '[0].appId' -o tsv)"
OBJ_ID="$(az ad app list --display-name Vikunja --query '[0].id' -o tsv)"
az ad sp create --id "$APP_ID"
az ad app permission admin-consent --id "$APP_ID"
```

Die vier Scope-IDs sind die delegierten Microsoft-Graph-Berechtigungen
`openid`, `profile`, `email` und `User.Read` — dieselbe Kombination wie bei
Penpot und Outline.

Der `email`-Claim muss zusätzlich als *optional claim* eingetragen werden. Der
`email`-Scope allein genügt Entra nicht, auch wenn das `mail`-Attribut des
Nutzers gefüllt ist — bei Penpot äußerte sich das als
`hint=incomplete+user+info`:

```bash
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/${OBJ_ID}" \
  --headers "Content-Type=application/json" \
  --body '{"optionalClaims":{"idToken":[{"name":"email","source":null,"essential":false,"additionalProperties":[]}],"accessToken":[{"name":"email","source":null,"essential":false,"additionalProperties":[]}],"saml2Token":[]}}'
```

## Client Secret erzeugen und ins Repo bringen (auch die Rotation)

```bash
APP_ID="$(az ad app list --display-name Vikunja --query '[0].appId' -o tsv)"
SECRET="$(az ad app credential reset --id "$APP_ID" --display-name vikunja-oidc \
            --years 2 --query password -o tsv)"

cd apps/clusters/feathre-core/base-apps/vikunja
printf 'VIKUNJA_AUTH_OPENID_PROVIDERS_ENTRA_CLIENTID=%s\nVIKUNJA_AUTH_OPENID_PROVIDERS_ENTRA_CLIENTSECRET=%s\n' \
  "$APP_ID" "$SECRET" > vikunja-oidc.sops.env
unset SECRET
sops -e -i --input-type dotenv --output-type dotenv vikunja-oidc.sops.env
```

Danach committen und pushen. Weil `disableNameSuffixHash: true` gesetzt ist,
ändert sich der Secret-Name nicht — der Deployment muss also neu gestartet
werden, damit er die Werte liest:

```bash
kubectl -n vikunja rollout restart deploy/vikunja
```

⚠️ Das Secret läuft nach zwei Jahren ab. Ohne Erneuerung bricht der Login.
