## ADDED Requirements

### Requirement: Helsinki announces only the four ingress VIPs

WHEN a hel01 ingress node has an established IPv4 BGP session to fw01, THE hel01 Cilium BGP configuration SHALL announce exactly the LoadBalancer IPs `10.200.90.1`, `10.200.90.3`, `10.200.90.4` and `10.200.90.5`, and no other LoadBalancer IP.

#### Scenario: Only ingress VIPs are announced from Helsinki

- **GIVEN** both hel01 nodes run and their sessions to fw01 are established
- **WHEN** the advertised routes of each hel01 node are listed
- **THEN** the set of advertised prefixes equals the four ingress VIPs
- **AND** `10.200.90.6`, `10.200.90.7` and `10.200.90.8` are not advertised from any hel01 node

### Requirement: Helsinki clients enter and return on the same hel01 node

WHEN a Helsinki client connects to one of the four ingress VIPs, THE connection SHALL enter the cluster on a healthy hel01 ingress node and the return traffic SHALL leave through the same node's default gateway, fw01.

#### Scenario: HTTP request from Helsinki enters on a hel01 node

- **GIVEN** a hel01 node runs an Envoy pod and its session to fw01 is established
- **WHEN** a Helsinki client sends an HTTPS request to `10.200.90.1`
- **THEN** the Envoy access log records the request on that hel01 node
- **AND** the client receives the response from `10.200.90.1`

#### Scenario: Database connection from Helsinki enters on a hel01 node

- **GIVEN** a hel01 database proxy pod runs on a hel01 node
- **WHEN** a Helsinki client opens a PostgreSQL connection to `10.200.90.3:5432`
- **THEN** the connection enters the cluster on that hel01 node
- **AND** the database reports the proxy pod address as the client address

### Requirement: fr01 never learns the ingress VIPs from Helsinki

THE fr01 routers (mkgfire) SHALL NOT import the ingress VIP prefixes from fw01.

#### Scenario: mkgfire has no VIP route from fw01

- **GIVEN** the hel01 sessions are established
- **WHEN** the routes received from the fw01 neighbour on mkgfire are listed
- **THEN** none of `10.200.90.1`, `10.200.90.3`, `10.200.90.4` and `10.200.90.5` is present

### Requirement: fw01 prefers Helsinki and does not re-advertise it

THE fw01 router SHALL prefer the ingress VIP routes learned from the hel01 sessions over any other route to the same prefix, and SHALL NOT export those routes to mkgfire or WG_ROSENAU.

#### Scenario: fw01 keeps the Helsinki next hop

- **GIVEN** both hel01 nodes advertise the four ingress VIPs
- **WHEN** the route for `10.200.90.3` is read on fw01
- **THEN** the best path has a hel01 next hop
- **AND** the advertised routes to mkgfire and WG_ROSENAU contain none of the four VIPs

### Requirement: Helsinki fails closed when no hel01 node is healthy

WHILE no hel01 ingress node has an established session to fw01, THE fw01 router SHALL NOT route traffic for the four ingress VIPs over WG_ROSENAU.

#### Scenario: Both hel01 nodes down

- **GIVEN** both hel01 nodes are down and their sessions to fw01 are not established
- **WHEN** a Helsinki client connects to `10.200.90.3:5432`
- **THEN** the connection fails
- **AND** no packet for the VIP leaves fw01 towards WG_ROSENAU

### Requirement: One hel01 node failure is covered by the other

WHEN one hel01 ingress node stops, THE remaining hel01 ingress node SHALL serve the four ingress VIPs without any change in the routes of fr01.

#### Scenario: Single node failure

- **GIVEN** both hel01 nodes serve traffic
- **WHEN** one hel01 node is drained and stopped
- **THEN** its routes are withdrawn from fw01
- **AND** new connections to the four VIPs succeed through the other hel01 node

### Requirement: Rosenau traffic does not enter at Helsinki

THE routing configuration SHALL NOT use the hel01 sessions for traffic that originates in the Rosenau network, and SHALL NOT use WG_ROSENAU for Helsinki traffic to the ingress VIPs.

#### Scenario: Rosenau client reaches the fr01 side

- **GIVEN** a client in the Rosenau network
- **WHEN** it connects to `10.200.90.1`
- **THEN** the connection enters on an fr01 node
- **AND** no hel01 node receives the connection

### Requirement: Only listed DaemonSets tolerate the ingress taint

THE cluster SHALL allow the taint `node-role.feather/ingress=true:NoSchedule` to be tolerated only by the Envoy DaemonSet, the Helsinki database proxy, the Cilium agent (only if the chart default does not tolerate it), node-exporter, alloy (logs) and trivy.

#### Scenario: No other workload runs on the ingress nodes

- **GIVEN** the change is reconciled
- **WHEN** the tolerations of all workloads in the cluster are listed
- **THEN** the ingress taint appears only on the workloads listed in this requirement

### Requirement: Public HTTP(S) is forwarded to the Helsinki Envoy VIP

THE fw01 router SHALL forward `65.108.236.147` TCP ports 80 and 443 to `10.200.90.1`.

#### Scenario: Public HTTPS request reaches the gateway

- **GIVEN** the DNAT is in place on fw01
- **WHEN** a client sends an HTTPS request to `65.108.236.147:443` with a known Host header
- **THEN** the response comes from the Envoy Gateway
