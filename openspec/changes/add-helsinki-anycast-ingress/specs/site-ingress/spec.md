## ADDED Requirements

### Requirement: Helsinki announces only the gateway VIP

WHEN a hel01 node runs a ready Envoy pod and has an established IPv4 BGP session to fw01, THE hel01 Cilium BGP configuration SHALL announce `10.200.90.1` and no other LoadBalancer IP.

#### Scenario: Only the gateway VIP is announced from Helsinki

- **GIVEN** the hel01 nodes run Envoy pods and their sessions to fw01 are established
- **WHEN** the routes received from the hel01 neighbours are listed on fw01
- **THEN** the only prefix is `10.200.90.1/32`

### Requirement: Helsinki clients enter on a hel01 node with symmetric return

WHEN a client in the Helsinki MGMT network connects to `10.200.90.1`, THE connection SHALL enter the cluster on a hel01 node, and both directions SHALL pass through fw01.

#### Scenario: HTTPS request from mc01

- **GIVEN** a hel01 node announces `10.200.90.1`
- **WHEN** mc01 sends an HTTPS request to `10.200.90.1`
- **THEN** the request succeeds
- **AND** the Envoy access log records it on a hel01 Envoy pod with source `10.200.2.1`

### Requirement: In-cluster gateway traffic stays in its zone

WHILE at least one fr01 Envoy pod is ready, THE Cilium datapath on fr01 nodes SHALL NOT send in-cluster connections for `10.200.90.1` to a hel01 Envoy pod.

#### Scenario: Pod in fr01 calls the gateway

- **GIVEN** Envoy pods run in fr01 and in hel01
- **WHEN** a pod on an fr01 node opens connections to `10.200.90.1`
- **THEN** every connection lands on an fr01 Envoy pod

### Requirement: Helsinki routes never reach the other sites

THE fw01 router SHALL NOT advertise a `10.200.90.1` route learned directly from a hel01 node to mkgfire or Ludwigsburg.

#### Scenario: mkgfire keeps its fr01 path

- **GIVEN** the hel01 sessions are established
- **WHEN** the routes for `10.200.90.1` are read on mkgfire
- **THEN** no path contains AS 64504 followed by 64606 only

### Requirement: Helsinki falls back to fr01

WHEN no hel01 node announces `10.200.90.1`, THE fw01 router SHALL route Helsinki traffic for `10.200.90.1` over the mkgfire path again.

#### Scenario: All hel01 Envoy pods gone

- **GIVEN** no hel01 node announces `10.200.90.1`
- **WHEN** mc01 sends an HTTPS request to `10.200.90.1`
- **THEN** the request succeeds through an fr01 node

### Requirement: Only listed workloads run on the edge nodes

THE cluster SHALL schedule on nodes tainted `node-role.feather/edge` only these workloads:

- Cilium;
- cilium-envoy;
- node-exporter;
- spegel;
- alloy-logs;
- the Envoy Gateway data plane;
- pods in namespace `cloudnet`.

#### Scenario: Pods on an edge node

- **GIVEN** the change is reconciled
- **WHEN** the pods on `hel01-wrk-ed-01` are listed
- **THEN** each pod belongs to one of the listed workloads

### Requirement: Game workloads never starve the ingress

WHERE a pod in namespace `cloudnet` runs on an edge node, THE pod SHALL use a PriorityClass with a lower value than `feather-platform`, and every container SHALL set its memory request equal to its memory limit.

#### Scenario: Memory pressure on an edge node

- **GIVEN** an edge node runs Envoy and CloudNet pods
- **WHEN** the node comes under memory pressure
- **THEN** the kubelet evicts CloudNet pods before the Envoy pod
- **AND** the Envoy pod and the system DaemonSets stay Running
