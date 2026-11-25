## ADDED Requirements

### Requirement: Fixed internal address for the proxies

THE cluster SHALL expose the proxy pods of namespace `grus-dev` through a Service `grus-dev-proxy` of type `LoadBalancer` with the address `10.200.90.11` from the Cilium pool `lb-fr01`, on TCP port 25565.

#### Scenario: Address is assigned

- **GIVEN** the code pull request is merged and Flux has reconciled `products-dev`
- **WHEN** the Service `grus-dev-proxy` is listed
- **THEN** its external IP is `10.200.90.11` and its port is 25565/TCP

#### Scenario: Address is not shared

- **GIVEN** the code pull request is merged
- **WHEN** all LoadBalancer Services in the cluster are listed
- **THEN** exactly one of them holds `10.200.90.11`

### Requirement: Only proxy pods are selected

THE Service `grus-dev-proxy` SHALL select only pods labelled `grus.olf/pool=proxy` and `grus.olf/role=proxy`.

#### Scenario: Lobby pods are not selected

- **GIVEN** the Service is applied and lobby pods run in `grus-dev`
- **WHEN** the endpoints of the Service are listed
- **THEN** no lobby pod appears among them

### Requirement: Connections spread over all proxy instances

THE Service `grus-dev-proxy` SHALL set `externalTrafficPolicy: Cluster`, so that connections reach every ready proxy pod.

#### Scenario: Two proxies share the connections

- **GIVEN** the proxy pool runs two instances
- **WHEN** several clients connect to `10.200.90.11:25565`
- **THEN** each proxy instance receives at least one connection

### Requirement: Internal sources only

THE network policy SHALL allow TCP 25565 to pods labelled `grus.olf/role=proxy` only from the source ranges `192.168.15.0/24` and `10.200.2.0/24`, and from the VPN client range once it is confirmed.

#### Scenario: Internal client connects

- **GIVEN** the code pull request is merged
- **WHEN** a Minecraft client on the internal network connects to `10.200.90.11:25565`
- **THEN** the connection reaches a proxy pod

#### Scenario: Client from another range is refused

- **GIVEN** the code pull request is merged
- **WHEN** a client from a source outside the admitted ranges connects to `10.200.90.11:25565`
- **THEN** the connection is not established

### Requirement: No other pod is opened

THE network policy SHALL NOT open any port of the controller, the web UI, the lobby or game pods to the new sources.

#### Scenario: Lobby port is not reachable through the address

- **GIVEN** the code pull request is merged
- **WHEN** a client on the internal network connects to a lobby pod on TCP 25565 directly
- **THEN** the connection is denied by the policy

### Requirement: Internal exposure only

THE code pull request SHALL NOT add a public hostname, a Cloudflare record, a DNAT rule, an anycast address or a DNS record for the proxies.

#### Scenario: Only the internal address is added

- **GIVEN** the code pull request is merged
- **WHEN** the rendered manifests are listed
- **THEN** the only new external address is `10.200.90.11` and no new hostname appears

### Requirement: Scope limited to two files

THE code pull request SHALL change exactly `products/grus/clusters/feather-core/grus-dev/proxy-loadbalancer.yaml` (new) and `products/grus/clusters/feather-core/grus-dev/kustomization.yaml`, and no file under `openspec/`.

#### Scenario: Only the listed files change

- **GIVEN** the code pull request
- **WHEN** its changed files are listed
- **THEN** the list contains exactly the two files above
