## ADDED Requirements

### Requirement: Internal hostname reaches the web UI

THE internal gateway SHALL route requests for `grus-dev.apps.onelite.feather` on the https listener to the Service `grus-web` on port 3000 in namespace `grus-dev`.

#### Scenario: Browser on the internal network loads the UI

- **GIVEN** the code pull request is merged and `grus-web` is Ready
- **WHEN** a client on the internal network requests `https://grus-dev.apps.onelite.feather/`
- **THEN** the gateway answers with the UI over a valid certificate issued for `grus-dev.apps.onelite.feather`

#### Scenario: Other hostnames are unaffected

- **GIVEN** the code pull request is merged
- **WHEN** a request for `menura-dev.apps.onelite.feather` is sent
- **THEN** it is routed to the menura-dev backends as before

### Requirement: Gateway reaches only the web pod

THE network policy SHALL allow ingress from the envoy proxy pods in namespace `envoy` only to pods labelled `app.kubernetes.io/name: grus-web` on TCP port 3000 in namespace `grus-dev`.

#### Scenario: Controller API is not reachable from the gateway

- **GIVEN** the network policy is applied
- **WHEN** the envoy proxy pods connect to the controller pod on TCP 8080
- **THEN** the connection is denied by the policy

#### Scenario: Game pods are not reachable from the gateway

- **GIVEN** the network policy is applied
- **WHEN** the envoy proxy pods connect to a game or proxy pod
- **THEN** the connection is denied by the policy

### Requirement: No login at the gateway

THE gateway route SHALL NOT enforce authentication, and THE web UI SHALL keep its own cross-site write guard, its server-side admin token and its existing security settings.

#### Scenario: No login is requested at the gateway

- **GIVEN** the code pull request is merged
- **WHEN** a client on the internal network requests the UI hostname without credentials
- **THEN** the gateway returns the UI without an authentication challenge

#### Scenario: Cross-site write through the gateway is refused

- **GIVEN** the UI is reachable at `https://grus-dev.apps.onelite.feather`
- **WHEN** a page from another origin sends a write request to the UI through the gateway
- **THEN** the UI rejects the request and does not forward it to the controller

### Requirement: Event and log streams are not cut by the gateway

THE route SHALL set request and backend timeouts to `0s` for the paths `/api/grus/v1/events` and `/api/grus/v1/instances`.

#### Scenario: Event stream stays open past the default timeout

- **GIVEN** a client has opened `https://grus-dev.apps.onelite.feather/api/grus/v1/events`
- **WHEN** the stream stays open for longer than 15 seconds
- **THEN** the gateway does not close it for the request timeout

#### Scenario: Pages keep the default timeouts

- **GIVEN** the code pull request is merged
- **WHEN** the route rules are read
- **THEN** the rule for `/` sets no timeout override

### Requirement: Internal exposure only

THE code pull request SHALL NOT add a public hostname, a LoadBalancer Service, a DNAT rule or a Cloudflare record for `grus-dev`, and SHALL NOT change the DNS records.

#### Scenario: No public route

- **GIVEN** the code pull request is merged
- **WHEN** the rendered manifests are listed
- **THEN** the only hostname added is `grus-dev.apps.onelite.feather`, and no Service of type LoadBalancer is added

### Requirement: Port-forward keeps working

THE Service `grus-web` SHALL remain ClusterIP on port 3000, so that `kubectl -n grus-dev port-forward svc/grus-web 3000:3000` keeps working.

#### Scenario: Port-forward still reaches the UI

- **GIVEN** the code pull request is merged
- **WHEN** an operator runs the port-forward command
- **THEN** `http://localhost:3000/healthz` answers with HTTP 200

### Requirement: Scope limited to six files

THE code pull request SHALL change exactly these files: `foundation/certificates/clusters/feather-core/internal-certs/internal-certificates.yaml`, `foundation/networking/clusters/feather-core/gateway/gateway.yaml`, `products/grus/clusters/feather-core/grus-dev/httproute.yaml` (new), `products/grus/clusters/feather-core/grus-dev/networkpolicy.yaml`, `products/grus/clusters/feather-core/grus-dev/kustomization.yaml` and `products/grus/clusters/feather-core/grus-dev/release.yaml` (comments only).

#### Scenario: Only the listed files change

- **GIVEN** the code pull request
- **WHEN** its changed files are listed
- **THEN** the list contains exactly the six files above and no file under `openspec/`
