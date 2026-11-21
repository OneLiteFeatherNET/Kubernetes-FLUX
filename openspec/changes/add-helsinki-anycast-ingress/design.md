## Context

- The fr01 pool `lb-fr01` gives LoadBalancer IPs `10.200.90.1` to `.15`. Every Service pins its IP with `lbipam.cilium.io/ips`.
- The Cilium advertisement `lb-ips` announces all LoadBalancer IPs without the label `bgp.onelite.feather/no-advertise`. The fr01 peers select it through `bgp.onelite.feather/advertise: lb-ips`.
- The four VIPs use `externalTrafficPolicy: Local`. Cilium announces such an IP only from nodes with a local ready endpoint.
  - `.1` (Envoy `eg-proxy`) runs as a DaemonSet, so every node with an Envoy pod announces it. Once the Envoy DaemonSet tolerates the ingress taint, the hel01 nodes run Envoy and announce `.1` as soon as a BGP config selects them.
  - `.3` (PostgreSQL `rw-external`), `.4` (MaxScale `kubernetesService`) and `.5` (`dragonfly-master-lb`) have their endpoints in fr01 only. The hel01 nodes would never announce them.
- The Dragonfly NetworkPolicy allows the Helsinki management range `10.200.2.0/24` as a client source. Cilium enforces it since the 2026-11-05 cutover. Clients that reach a database through a proxy show the proxy pod address as source (decision 1).
- Assumption to confirm in task 1.4: Helsinki reaches the VIPs today through fw01 routes learned from mkgfire over WG_ROSENAU.

## Goals / Non-Goals

**Goals**

- Helsinki clients reach `.1`, `.3`, `.4` and `.5` on a hel01 node, and the return traffic leaves through the same node.
- Helsinki traffic never enters through fr01 while a hel01 node is healthy.
- A single hel01 node failure is invisible to clients, apart from connections on that node.

**Non-Goals**

- Moving database pods or the ingress VIPs' backends out of fr01.
- Changing the fr01 ingress path.
- Automatic fallback from Helsinki to fr01 (see decision 3).

## Decision drivers

- Source addresses and return paths must stay symmetric. An earlier incident came from Helsinki announcing the same VIPs without filters.
- No change to the fr01 path and no new layer.
- Third-party software arrives through official charts only.

## Decisions

### 1. How database traffic enters at Helsinki (MADR)

**Context:** `.3`, `.4` and `.5` need an entry point on the hel01 nodes. A Helsinki node announces such a VIP only if it has a local endpoint, and the databases have none there.

**Decision drivers:** one VIP per database for all clients; no asymmetric database traffic; no change to the fr01 database path; no new address space.

**Considered options:**

- **A. `externalTrafficPolicy: Cluster` on the database Services.** Rejected. Traffic is SNAT'd to the node address and may cross nodes on the return path. This breaks the rule that database traffic must never be asymmetric.
- **B. Envoy Gateway TCPRoutes on `eg`.** Rejected. The Gateway `eg` owns only `.1`. A second Gateway needs its own address, which is option D.
- **C. LB-IPAM IP sharing.** Two Services with the same `lbipam.cilium.io/sharing-key` (plus `lbipam.cilium.io/sharing-cross-namespace` if the namespaces differ) share one IP. The fr01 database Service keeps the VIP. A second Service, `ETP: Local`, selects the hel01 proxy pods and exposes the same port. The proxy forwards to the fr01 internal Service of the database, never to the LoadBalancer Service.
- **D. Separate Helsinki database VIPs from a new pool.** Rejected for now by the operator in favour of anycast. Kept as the fallback.

**Decision:** C, pending verification in task 2.1. Caveat: sharing an IP normally requires that the Services do not use the same port and protocol. The database ports (5432, 3306, 6379) are fixed, so C probably fails. If it does, D is the fallback. The operator decides between them, not automation.

**Rejected in passing:** one Service with endpoints in both sites. The proxy would forward into the same ClusterIP and can loop back to itself.

**Consequences:**

- The proxy's own Service selects only the hel01 proxy pods, so the forwarding target and the entry point are never the same Service.
- The database sees the proxy pod address as client address for Helsinki clients. The pg_hba rules, the MaxScale host rules and the Dragonfly NetworkPolicy must allow the proxy pod CIDR (task 3.7).
- The proxy packaging follows the repository rule: the official chart of the chosen proxy, or an existing in-repo chart.

### 2. BGP for hel01

**Decision:**

- `CiliumBGPClusterConfig` `hel01`: `nodeSelector` `topology.kubernetes.io/zone: hel01`, local ASN `64606`, peers `fw01-v4` (`10.200.2.1`) and `fw01-v6` (address to confirm). Peer ASN: fw01's ASN, to confirm (task 1.3).
- `CiliumBGPPeerConfig` `fw01-v4` and `fw01-v6`: same timers (hold 180 s, keepalive 60 s) and graceful restart (120 s) as the mkgfire peers. Advertisement selector `bgp.onelite.feather/advertise: hel01-ingress`.
- `CiliumBGPAdvertisement` `hel01-ingress`: `Service` `LoadBalancerIP`, selector `bgp.onelite.feather/site: hel01`. It carries no `lb-ips` label, so no fr01 peer selects it.
- Only the four Services carry `bgp.onelite.feather/site: hel01`. The other LoadBalancer VIPs (`.6`, `.7`, `.8`) are never announced from hel01.

**IPv6:** the v6 session is configured for symmetry with fr01. The ingress Services have no IPv6 address, so this session carries no routes in the first rollout.

### 3. Symmetry, preference and failure

**Symmetry:** a Helsinki client sends to the VIP, fw01 forwards to the hel01 node, and the reply uses the node's default gateway, fw01. The same node and the same router carry both directions.

**Route policy:**

- fw01 imports the four VIP prefixes from the hel01 sessions and gives them a higher local preference.
- fw01 does not export the Helsinki VIP prefixes to mkgfire or WG_ROSENAU.
- mkgfire does not import the VIP prefixes from fw01. mkgfire keeps the fr01-learned routes.
- fw01 does not import the VIP prefixes from mkgfire either. Without that, fw01 would hold a fallback to fr01 that the operator has ruled out.
- Rosenau clients never enter at Helsinki, and Helsinki clients never enter through Rosenau by default.

**Failure modes:**

- One hel01 node fails: its BGP session drops and its routes are withdrawn. The other node keeps serving. Both hel01 nodes announce each VIP, because the Envoy and proxy DaemonSets run on both.
- Both hel01 nodes fail: fw01 has no route to the VIPs, and Helsinki clients fail closed. The fr01 cluster is still up and reachable, but Helsinki does not fall back to it. This is the recommended behaviour: silent fallback would push database traffic across the inter-site link and risk asymmetry during partial failures.
- Operator-controlled option: a static route on fw01 for the four VIPs via WG_ROSENAU, active only while no hel01 route exists. Open question 4.

**Rollback:** the Helsinki entry is controlled on the routers. Re-allowing the fr01 VIP import on fw01 restores the old path. Reverting the Git change alone does not restore it, because the fail-closed filter lives on fw01.

### 4. Tolerations

Only these DaemonSets tolerate `node-role.feather/ingress=true:NoSchedule`:

- Envoy (`EnvoyProxy` `eg-proxy`, `envoyDaemonSet.pod.tolerations`): the entry point for `.1`.
- The Helsinki database proxy: the entry point for `.3`, `.4` and `.5`.
- Cilium agent: only if the chart default does not already tolerate it.
- node-exporter, alloy (logs) and trivy: so that the ingress nodes are observed and scanned like the others.

Everything else stays off the ingress nodes, including Ceph, CloudNativePG, Galera and the application workloads.

## Risks / Trade-offs

- **Wrong preference or leak:** Helsinki traffic enters fr01, or a VIP is announced from fr01 to Helsinki. Mitigation: the router filters are applied before the code PR merges, and task 4.3 checks the route tables on fw01 and mkgfire.
- **Sharing rejected (task 2.1):** option D changes the Helsinki IP for the databases. Mitigation: the decision is recorded before any manifest is written.
- **Source address change:** database access rules see the proxy pod address. Mitigation: task 3.7 updates the rules before rollout, and task 4.4 proves the path with a real client.
- **Full Helsinki outage on double failure:** accepted as the fail-closed trade-off. Mitigation: open question 4.
- **Proxy loop:** a proxy that forwards into its own Service. Mitigation: decision 1 consequences.

## Migration Plan

1. Talos change lands; hel01 nodes Ready, labelled and tainted (task 1.2).
2. Verify the LB-IPAM sharing behaviour and record decision 1 (group 2).
3. Operator: mkgfire import filter first (task 1.5). It has no effect on current traffic.
4. Operator: fw01 BGP neighbours and preference (task 1.6). Still no effect until the hel01 sessions come up.
5. Merge the code PR. Flux reconciles `foundation-networking` and `foundation-databases`.
6. Verify the hel01 sessions and the route tables on fw01 and mkgfire (tasks 4.2 and 4.3).
7. Operator: fw01 import filter that drops the mkgfire-learned VIP prefixes (task 4.4). This is the point where the fallback goes.
8. Operator: DNAT of 65.108.236.147:80/443 to `10.200.90.1` (task 4.5).
9. Connection tests and the failover test (tasks 4.6 to 4.8).

## Open Questions

1. **fw01 ASN and IPv6 ULA address:** pending from the operator (task 1.3). The peer manifests need both.
2. **Current Helsinki path:** whether fw01 learns the VIP prefixes from mkgfire today (task 1.4). Rollback depends on it.
3. **Sharing feasibility:** task 2.1 decides between C and D.
4. **Static fallback route on fw01:** the operator decides whether to add a static route to fr01 via WG_ROSENAU for double-failure cases. Default: none.
5. **Database proxy implementation:** HAProxy or an Envoy TCP listener, packaged per the chart rule. Chosen in task 2.3 with the decision.
6. **Proxy source address in database rules:** the exact CIDR of the proxy pods, taken from the cluster after task 3.6.
