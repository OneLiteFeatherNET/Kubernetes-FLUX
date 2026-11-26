## Why

Every LoadBalancer VIP in `feather-core` is announced only from the fr01 nodes. Helsinki clients therefore reach the Envoy Gateway VIP `10.200.90.1` across the inter-site link. This affects two clients:

- the public reverse proxy behind 65.108.236.147 (`10.200.2.69`);
- mc01 (`10.200.2.35`).

The three edge nodes `hel01-wrk-ed-01..03` have now joined the cluster, with taint `node-role.feather/edge=true:NoSchedule`. This change lets them announce `10.200.90.1` as well (anycast), so Helsinki HTTP(S) traffic enters the cluster on a local node.

The database VIPs `.3`, `.4` and `.5` are out of scope for now (design, decision 1).

**Commit type and scope:** `feat(networking)`. Two code pull requests ship it, in this order:

1. `feat(networking): keep in-cluster gateway traffic in its zone`
2. `feat(networking): announce the gateway vip from helsinki`

## What Changes

- **Cilium topology:** turn on topology-aware service routing (`loadBalancer.serviceTopology`).
- **Envoy Service:** set `trafficDistribution: PreferSameZone`, so in-cluster clients in fr01 keep using the fr01 Envoy pods.
- **New BGP objects:** a `CiliumBGPClusterConfig` `hel01` (zone `hel01`, local ASN 64606) with one IPv4 session per node to fw01, plus a `CiliumBGPAdvertisement` `hel01-ingress` that announces only Services labelled `bgp.onelite.feather/site: hel01`.
- **Service label:** `bgp.onelite.feather/site: hel01` goes on the Envoy Service only.
- **Edge toleration:** the Envoy DaemonSet tolerates `node-role.feather/edge`.
- **Routers (operator work outside this repository):**
  - fw01 BGP neighbours and filters, already in place since 2026-11-26;
  - a source NAT rule on fw01 for MGMT clients that connect to the anycast VIPs;
  - an optional import filter on mkgfire.

## Out of scope

- **Database VIPs:** `10.200.90.3`, `.4` and `.5`, and with them any Helsinki database proxy.
- **DNAT change:** the DNAT of 65.108.236.147 is unchanged. It keeps pointing at `10.200.2.69`, which then reaches the gateway on a hel01 node.
- **Other LoadBalancer VIPs:** all of them stay announced from fr01 only.
- **IPv6:** no IPv6 VIPs and no IPv6 session from Helsinki.
- **Node provisioning:** done in the Talos repository (FeatherCore#19).

## Dependencies

- **Pod MTU:** `lower-cilium-mtu` is live (#741), so pods have MTU 1350. A Path MTU test from HEL to fr01 passed at 1400 bytes.
- **spegel and alloy-logs:** both already run on the edge nodes (#742).
- **fw01 BGP configuration:** live since 2026-11-26. Neighbours `10.200.2.21`–`.23` with AS 64606:
  - `HEL01-IN` imports only the four VIPs, with local-preference 200;
  - `HEL01-OUT` sends nothing to the nodes;
  - `TO-SITES-OUT` keeps Helsinki-originated VIP routes away from mkgfire and Ludwigsburg.

## Impact

- **Layers:** `foundation-networking` only. No new layer and no rename.
- **Runtime, first PR:** one rolling restart of the Cilium agents to load the topology flag. The Envoy Service changes only in metadata and spec.
- **Runtime, second PR:**
  - one rolling update of the Envoy DaemonSet, the same as any Envoy upgrade;
  - three new Envoy pods on the edge nodes;
  - three BGP sessions to fw01.
- **Routing:** when the first hel01 node announces `.1`, fw01 switches the best path from mkgfire to Helsinki. TCP connections that MGMT clients hold open to `.1` at that moment are reset once and reconnect. Fallback to fr01 stays automatic.
