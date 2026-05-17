# Migration: ingress-nginx → Envoy Gateway (Gateway API)

Status: **Plan / Review** — noch nicht umgesetzt.
Branch: `claude/replace-nginx-ingress-uozB3`

## Motivation

ingress-nginx wird ersetzt wegen (a) wiederkehrender Annotation-/Snippet-Exploits
(aktuell ist sogar `allowSnippetAnnotations: true` gesetzt) und (b) Wartbarkeit.
Ersatz ist **Envoy Gateway** mit der Kubernetes **Gateway API** — Envoy Gateway
v1.6.0 inkl. CRDs, `GatewayClass eg` und ein HTTPRoute-Template
(`helm/metabase`) sind im Repo bereits vorhanden, nur deaktiviert.

## Architektur-Entscheidungen

| Thema | Entscheidung |
|---|---|
| Controller | Envoy Gateway v1.6.0 (bereits installiert) |
| Routing | Gateway API (`Gateway` + `HTTPRoute`), kein `kind: Ingress` mehr |
| Zentrales Gateway | `eg` im Namespace `envoy`, `allowedRoutes.namespaces.from: All` |
| TLS öffentliche Domains | Wildcard-`Certificate` via `letsencrypt-prod-dns` (Cloudflare DNS01): `*.onelitefeather.dev`, `*.onelitefeather.net`, `*.s3.onelitefeather.net` |
| TLS interne Domains | `*.apps.onelite.feather` via `step-ca` ACME, Solver = `gatewayHTTPRoute`; cert-manager Feature-Gate `ExperimentalGatewayAPISupport=true` |
| cert-manager ↔ Gateway | explizite `Certificate`-Ressourcen im `envoy`-NS (kein ingress-shim) |
| MetalLB Parallelbetrieb | Envoy temporär `10.200.90.8`; Cutover auf `10.200.90.1` in Phase 4 |
| Basic-Auth (Loki/Mimir) | nginx htpasswd-Secret → Envoy `SecurityPolicy.basicAuth` (Secret mit `.htpasswd`-Key, Format wird angepasst) |
| Rollback | je Phase ein Commit; ingress-nginx bleibt bis Phase 4 parallel aktiv |

## Chart-Strategie-Matrix

| App | Chart / Version | Native HTTPRoute | Vorgehen |
|---|---|---|---|
| grafana | grafana/grafana (unpinned) | ja (`httpRoute.enabled`) | Values-only |
| loki | grafana/loki (unpinned) | ja (`gateway.httpRoute.enabled`) | Values-only |
| mimir | mimir-distributed (unpinned) | ja (`gateway.httpRoute.enabled`) | Values-only |
| harbor | goharbor `=1.19.0` | ja (`expose.type: route`) | Values-only |
| dependency-track | `=0.39.0` | ja (`httpRoute.enabled`) | Values-only |
| node-red | schwarzit `*` | nein | Standalone-HTTPRoute |
| reposilite | `>=1.3.20` | nein | Standalone-HTTPRoute |
| uptime-kuma | dirsigler `=2.22.0` | nein | Standalone-HTTPRoute |
| leantime | repo `helm/leantime` | nein (nur ingress-Template) | httproute-Template ergänzen + Values |
| outline | repo `helm/outline` | nein | httproute-Template ergänzen + Values |
| shlink | repo `helm/shlink` | nein | httproute-Template ergänzen + Values |
| otis | repo `helm/micronaut` | nein | httproute-Template ergänzen + Values |
| s3-proxy | reines Manifest | – | Ingress → HTTPRoute + SecurityPolicy + ClientTrafficPolicy |

## Phase 1 — Fundament (Gateway, CRDs, MetalLB, TLS-Listener)

| Datei | Änderung |
|---|---|
| `infrastructure/clusters/feather-core/base-controllers/kustomization.yaml` | `envoy-crds` einkommentieren (Z. 11) — CRDs vor Controller |
| `infrastructure/clusters/feather-core/configs/kustomization.yaml` | `- gateway` einkommentieren |
| `.../configs/gateway/gateway.yaml` | HTTP-Listener (Port 80, Redirect→HTTPS), HTTPS-Listener (443) je Hostname-Gruppe mit `certificateRefs` auf Wildcard-Secrets, `allowedRoutes.namespaces.from: All` |
| `.../configs/gateway/envoyproxy.yaml` (neu) | `EnvoyProxy`-CR: LB-Service `metallb.io/loadBalancerIPs: 10.200.90.8`, Label `onelite.feather/bgp: announce`, `externalTrafficPolicy: Local` |
| `.../configs/gateway/certificates.yaml` (neu) | `Certificate` (Wildcards) im NS `envoy`, Issuer `letsencrypt-prod-dns`; intern `*.apps.onelite.feather` via `step-ca` |
| `.../configs/gateway/client-traffic-policy.yaml` (neu) | `ClientTrafficPolicy`: XFF/Client-IP (Ersatz `enable-real-ip`/`use-forwarded-headers`), Default-Body-Size |
| `.../configs/gateway/kustomization.yaml` | neue Ressourcen, `namespace: envoy` |

GatewayClass `eg` bleibt unverändert.

## Phase 2 — cert-manager umstellen

| Datei | Änderung |
|---|---|
| `infrastructure/clusters/feather-core/base-controllers/cert-manager/release.yaml` | `featureGates: ExperimentalGatewayAPISupport=true` |
| `infrastructure/base/configs/cert-manager/acme-olf-http-issuer.yaml` | `step-ca`: HTTP01-Solver `ingress` → `gatewayHTTPRoute` (parentRef Gateway `eg`) |
| `infrastructure/base/configs/cert-manager/acme-le-http-issuer.yaml` | `letsencrypt-prod`: nginx-HTTP01 entfernen; abgedeckt durch Wildcard `letsencrypt-prod-dns` |
| `infrastructure/base/configs/cert-manager/acme-le-http-staging-issuer.yaml` | auf DNS01 umstellen oder entfernen (falls ungenutzt) |

## Phase 3 — App-Migration

Reihenfolge: 3a unkritisch → 3b größere Uploads → 3c kritisch.

### Values-only (native HTTPRoute)

| App | release.yaml |
|---|---|
| grafana | `ingress.enabled: false`; `httpRoute.enabled: true`, parentRef Gateway `eg`/`envoy`, host `grafana.apps.onelite.feather` |
| loki | `gateway.ingress.enabled: false`; `gateway.httpRoute.enabled: true`, host `loki.apps.onelite.feather` |
| mimir | `*.ingress.enabled: false`; `gateway.httpRoute.enabled: true`, host `mimir-gateway.apps.onelite.feather` |
| harbor | `expose.type: ingress` → `route`; `expose.route` host `harbor.onelitefeather.dev`; `externalURL` unverändert |
| dependency-track | `ingress.enabled: false`; `httpRoute.enabled: true` |

### httproute-Template ergänzen (repo-eigene Charts)

Neues `templates/httproute.yaml` (abgeleitet von `helm/metabase/templates/httproute.yaml`)
in `helm/leantime`, `helm/outline`, `helm/shlink`, `helm/micronaut`; jeweils
`values.yaml` um `httpRoute`-Block erweitern; in der `release.yaml` der App
`ingress.enabled: false` + `httpRoute.enabled: true` + Hosts.

### Standalone-HTTPRoute-Manifest

node-red, reposilite, uptime-kuma: `ingress.enabled: false` in Values; neue
`httproute.yaml` neben `release.yaml`, in App-`kustomization.yaml` aufnehmen.
uptime-kuma server-snippet: Inhalt prüfen, Gateway-Äquivalent oder Verzicht.

### Policies (Ersatz für nginx-Annotationen)

| nginx-Annotation | Ersatz |
|---|---|
| `proxy-body-size` (2G/5G/0) | `ClientTrafficPolicy` (leantime/outline/dependency-track 5G, node-red/reposilite/harbor 2G, s3-proxy unlimited) |
| `limit-rps` / `limit-connections` | `BackendTrafficPolicy.rateLimit` (shlink, uptime-kuma) |
| `auth-type: basic` | `SecurityPolicy.basicAuth` (loki, mimir) — Secret-Format anpassen |
| CORS-Annotationen | `SecurityPolicy.cors` (s3-proxy) |
| `ssl-redirect` | Gateway HTTP-Listener Redirect (global) |
| `proxy-buffering: off` | `ClientTrafficPolicy`/`BackendTrafficPolicy` (s3-proxy) |

### s3-proxy

`infrastructure/clusters/feather-core/base-configs/s3-proxy.yaml`: `kind: Ingress`
entfernen; HTTPRoute (Hosts `s3.onelitefeather.net` + `*.s3.onelitefeather.net`)
→ Service `ceph-rgw-external:7480`; `SecurityPolicy` CORS (Origin
`https://outline.onelitefeather.dev`, Methods/Headers/Credentials wie bisher);
`ClientTrafficPolicy` body unlimited + buffering off.

## Phase 4 — NGINX-Abbau & Cutover

| Datei | Änderung |
|---|---|
| `.../configs/gateway/envoyproxy.yaml` | LB-IP `10.200.90.8` → `10.200.90.1` |
| `infrastructure/clusters/feather-core/controllers/kustomization.yaml` | `- ingress-nginx` entfernen |
| `infrastructure/clusters/feather-core/controllers/ingress-nginx/` | Verzeichnis löschen |
| `infrastructure/base/controllers/ingress-nginx/` | Verzeichnis löschen |
| `infrastructure/clusters/feather-core/base-sources/nginx.yml` | löschen (+ aus base-sources kustomization entfernen) |
| grafana `release.yaml` | Dashboard `controllers.ingress-nginx` (gnetId 9614) durch Envoy-Gateway-Dashboard ersetzen |

## Risiken

- **CRD-Reihenfolge**: `envoy-crds` muss vor Gateway-Controller reconcilen (Phase 1).
- **TLS-Ausfall**: cert-manager-Umstellung ist kritisch; Wildcards vorab ausstellen
  und im Gateway prüfen, bevor Apps migriert werden.
- **step-ca HTTP01 via Gateway**: braucht funktionierendes Gateway + Feature-Gate.
- **Service-Namen** der Standalone-HTTPRoutes sind chart-deterministisch, müssen
  aber bei Major-Chart-Upgrades nachgezogen werden.
- **Cutover** `.1`: kurzer Reconnect der LB-IP; ingress-nginx vorher entfernen.
