## Context

**LoadBalancer IPs**
- Pool `lb-fr01` hands out `10.200.90.1`–`.15`.
- Advertisement `lb-ips` announces every LoadBalancer IP to mkgfire (AS 64501) from the fr01 nodes (AS 64606). Its selector is the absence of `bgp.onelite.feather/no-advertise`.

**Envoy**
- Envoy (`EnvoyProxy` `eg-proxy`) runs as a DaemonSet on the four xl workers.
- The Service has `externalTrafficPolicy: Local`. Cilium therefore announces `.1` only from nodes that have a ready Envoy pod.

**Helsinki edge nodes**
- Nodes `hel01-wrk-ed-01..03` live in MGMT, `10.200.2.0/24`.
- They carry the label `topology.kubernetes.io/zone: hel01` and the taint `node-role.feather/edge=true:NoSchedule`.
- Their default gateway is fw01 (`10.200.2.1`, OPNsense, AS 64504).

**Current clients of the VIPs** (pf states on fw01, 2026-11-26)
- Helsinki: only `10.200.2.35` (mc01) and `10.200.2.69` (public reverse proxy, DNAT target of 65.108.236.147:80/443). Both are in MGMT, the same subnet as the edge nodes.
- Today fw01 routes them over WG_ROSENAU to mkgfire, with best path `64501 64606`.

**Other BGP peers and in-cluster use**
- Ludwigsburg (AS 64503) peers with fw01 and mkgfire. It prefers its direct path to mkgfire for the VIPs.
- In-cluster pods reach `.1` too: the `*.apps.onelite.feather` wildcard points at it.
- Cilium's socket LB balances such connections over all Envoy backends unless topology hints restrict them.

## Goals / Non-Goals

**Goals**

- Helsinki HTTP(S) clients reach `10.200.90.1` on a hel01 node, and return traffic is symmetric through fw01.
- Pods in fr01 never reach a hel01 Envoy pod while an fr01 Envoy pod is ready.
- If one hel01 node fails, clients do not notice. If all of them fail, Helsinki falls back to fr01 automatically.
- Rolling this out does not interrupt any service beyond resetting the connections that are open to `.1` when the path switches.

**Non-Goals**

- Database VIPs, a database proxy, and moving any backend out of fr01.
- Changing the fr01 ingress path, the public DNAT, or IPv6.

## Decisions

### 1. Database VIPs stay fr01-only (MADR)

**Context:** the first draft also announced `.3`, `.4` and `.5` from Helsinki through a TCP proxy on the edge nodes.

**Considered options:**
- **A. Proxy plus LB-IPAM IP sharing.** Cilium shares one IP between Services only when their ports do not conflict, and the database ports are fixed.
- **B. Proxy plus separate Helsinki database VIPs.** This means new client configuration, a new source address in pg_hba, the MaxScale host rules and the Dragonfly policy, and a new component to run.
- **C. Do nothing for the databases.**

**Decision:** C.
- Every database backend lives in fr01, so a Helsinki proxy would add a hop without removing the inter-site crossing.
- It also gives no resilience: when fr01 fails, the database fails too.
- The cost of A or B (new component, access-rule changes, a risk to database connections) buys nothing today.
- Revisit when a database replica runs in Helsinki.

### 2. BGP for hel01

- **`CiliumBGPClusterConfig` `hel01`:** `nodeSelector` `topology.kubernetes.io/zone: hel01`, local ASN 64606, one peer `fw01-v4` at `10.200.2.1` with peer ASN 64504.
- **`CiliumBGPPeerConfig` `fw01-v4`:** same timers (hold 180 s, keepalive 60 s) and graceful restart (120 s) as `mkgfire-v4`. Family IPv4 unicast, advertisement selector `bgp.onelite.feather/advertise: hel01-ingress`.
- **`CiliumBGPAdvertisement` `hel01-ingress`:** type `Service`, `LoadBalancerIP`, selector `bgp.onelite.feather/site: hel01`. Only the Envoy Service carries that label. The advertisement has no `lb-ips` label, so the fr01 peers never select it.
- **No IPv6 session:** the gateway Service has no IPv6 address.
- **Site community:** the advertisement tags every route with the standard community `64606:901`, meaning "anycast, site hel01". The routers filter by this tag, not by a list of VIPs. A new Helsinki VIP therefore needs only the Service label in Git and no router change.
- **fw01 side** (OPNsense model, applied without session reset):
  - `HEL01-IN` permits a route only if it carries `64606:901` **and** lies inside the LoadBalancer pool `10.200.90.0/24` (`le 32`), and sets local-preference 200. Everything else is denied. The pool range is a fixed guard: a wrong label in Git can never make fw01 accept another network or a default route.
  - `HEL01-OUT` denies all, so the nodes learn nothing and keep using their default route.
  - `TO-SITES-OUT` on the four site sessions denies routes that carry `64606:901`. Transit routes are unchanged.
  - The source NAT destination alias covers the pool `10.200.90.0/24`. That is safe because the rule only matches traffic that fw01 routes into MGMT.
- **mkgfire side** (optional, in place since 2026-11-26): `FROM-HEL-IN` denies routes with `64606:901` from fw01 and Ludwigsburg.

### 3. Symmetry for MGMT clients: source NAT on fw01

**Problem:**
- A MGMT client sends to `.1` through its gateway fw01, and fw01 forwards to a hel01 node in the same subnet.
- The node replies straight to the client on the link and bypasses fw01.
- pf sees only one direction and drops or resets the flow.
- WAN-sourced traffic is not affected, because its replies go back through fw01 anyway.

**Considered options:**
- **Move the edge nodes into their own subnet.** A Talos and Proxmox re-IP; too large for this change.
- **Use sloppy pf state.** Weakens the firewall and keeps the hairpin.
- **Use source NAT on fw01** (outbound NAT on interface MGMT): source `10.200.2.0/24`, destination the four VIPs, translated to `10.200.2.1`.

**Decision:** source NAT.
- The rule only matches packets that leave fw01 through MGMT. While the VIP route points to mkgfire, packets leave through WG_ROSENAU and the rule does nothing, so it can be applied before the rollout.
- Consequence: Envoy sees `10.200.2.1` instead of `.35` or `.69` as the source of Helsinki MGMT clients. That address is still inside `10.200.2.0/24`, the only network that rules in this repository allow for these clients.
- The reverse proxy at `.69` passes the real client address in `X-Forwarded-For`.

### 4. Keep in-cluster traffic in its zone

- **Problem:** without topology hints, 3 of 7 in-cluster connections to `.1` would cross to Helsinki and back.
- **Settings:** `loadBalancer.serviceTopology: true` in Cilium, and `trafficDistribution: PreferSameZone` on the Envoy Service (through `envoyService.patch`).
- **Cilium behaviour:** it then picks only backends whose hint matches its own node's zone. When no backend matches, it falls back to all backends.
- **Order:** this ships and the agents restart before any Envoy pod runs on a hel01 node.
- **Other Services:** none sets `trafficDistribution`, so the flag does not change them.

### 5. Fallback instead of fail-closed

- **Original reason for fail-closed:** the first draft did not want database traffic to fall back to fr01 asymmetrically. With the database VIPs out, that reason is gone.
- **What happens on failure:** if every hel01 Envoy is down, the hel01 routes are withdrawn and fw01 uses the mkgfire path again, which is today's symmetric path.
- **What is dropped:** the fw01 import filter that removes the mkgfire-learned VIP routes ("Part B") and the static fallback question.
- **Ludwigsburg:** loses only fw01's backup path for the VIPs, because fw01 never re-advertises a Helsinki-originated best path. Ludwigsburg already prefers its direct path to mkgfire.

### 6. Tolerations and game compute on the edge nodes

**Decision:** on 2026-11-26 the operator decided that the edge nodes serve both as ingress and as game compute.

**Tolerations:**
- **New in this change:** Envoy.
- **Already tolerated:**
  - Cilium and node-exporter tolerate all taints.
  - spegel and alloy-logs since #742.
- **CloudNet workloads:** the namespace `cloudnet` (change `add-cloudnet-kubernetes-runtime`) may tolerate `node-role.feather/edge`.
- **Kept off the edge nodes:** everything else, including Rook CSI, envoy-gateway (the controller), loki-canary and all other applications.

**Guard rails for game workloads** (each edge node has about 3.9 CPU and 5.2 GiB allocatable):
- **Lower priority:** CloudNet pods use a PriorityClass below `feather-platform`, normally `feather-standard`. They never use `feather-platform` or `feather-critical`, so the kubelet and the scheduler evict them before Envoy or a system DaemonSet.
- **Memory requests equal limits:** set on every CloudNet container, so the scheduler never overcommits node memory.
- **Fixed reserve:** the system DaemonSets plus Envoy keep about 1 GiB and 0.5 CPU free per node. CloudNet capacity is what remains.

## Risks / Trade-offs

- **Path switch resets open connections:** TCP connections from `.35` and `.69` to `.1` that are open when the hel01 route appears are reset once. Mitigation: apply in a quiet hour. The reverse proxy and HTTP clients reconnect.
- **In-cluster traffic crosses sites:** happens if the topology flag is not active before hel01 Envoy pods start. Mitigation: separate pull requests, and the second one is merged only after `cilium-dbg config` shows `EnableServiceTopology` on every agent.
- **Envoy rolling update on fr01:** maxUnavailable 1, drained by the shutdown manager, the same as a version upgrade.
- **Wrong router filter:** a Helsinki route leaks to mkgfire. Mitigation: `TO-SITES-OUT` on fw01 plus the optional mkgfire import filter, checked in task 4.3.

## Migration Plan

1. Operator: source NAT rule on fw01 (no effect yet). The optional mkgfire import filter can be added at any time.
2. First code PR: topology flag and `trafficDistribution`. Restart the Cilium agents one by one, with a gate per node, and verify the hints.
3. Second code PR: BGP objects, Service label and Envoy toleration. Flux reconciles `foundation-networking`.
4. Verify the sessions, the routes on fw01, and the path from `.35` and `.69`.

**Rollback:** revert the second PR. Envoy pods leave the edge nodes, the routes are withdrawn, and fw01 falls back to mkgfire by itself.
