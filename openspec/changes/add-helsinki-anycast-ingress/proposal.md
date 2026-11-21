## Why

Every LoadBalancer VIP in `feather-core` is announced only from the fr01 nodes. Clients in Helsinki (the public address 65.108.236.147 on fw01, and Helsinki hosts such as mc01 and mail01) therefore reach HTTP(S) and database services across the inter-site link, and an fr01 outage removes them completely.

This change adds two ingress nodes in Helsinki, `hel01-wrk-in-01` and `hel01-wrk-in-02`. They announce the same four VIPs as fr01 (anycast), so Helsinki clients enter the cluster on a local node:

- `10.200.90.1`: Envoy Gateway (HTTP/HTTPS)
- `10.200.90.3`: PostgreSQL read-write
- `10.200.90.4`: MaxScale (MariaDB Galera)
- `10.200.90.5`: Dragonfly master

The database pods stay in fr01, so the database VIPs need a Helsinki-side TCP proxy (design, decision 1). The routers keep the two sites apart: fr01 never learns these VIPs from Helsinki, and Helsinki never learns the Rosenau-only VIPs.

**Commit type and scope:** `feat(networking)`. The code pull request title is `feat(networking): announce the ingress vips from helsinki`.

## What Changes

- New `CiliumBGPClusterConfig` `hel01` (zone `hel01`, local ASN 64606) with one session per family to fw01.
- New `CiliumBGPAdvertisement` `hel01-ingress` that announces only the four ingress VIPs.
- Label `bgp.onelite.feather/site: hel01` on the four Services.
- A Helsinki database proxy on the hel01 nodes for `10.200.90.3`, `.4` and `.5` (design, decision 1).
- Tolerations for the taint `node-role.feather/ingress=true:NoSchedule` on the Envoy, database proxy, Cilium, node-exporter, alloy and trivy DaemonSets (design, decision 4).
- Router configuration on fw01 and mkgfire, and the DNAT of 65.108.236.147:80/443 on fw01. These are operator tasks outside this repository.

## Out of scope

- Node provisioning and the taint itself (talos change `add-helsinki-ingress-nodes`).
- Moving any database pod to Helsinki.
- The other LoadBalancer VIPs: Harbor `.6`, MaxScale GUI `.7` and alloy logs `.8` stay announced from fr01 only.
- IPv6 VIPs for Helsinki. The ingress Services have no IPv6 address today.

## Dependencies

- `lower-cilium-mtu` (pod MTU 1350 on every node). It must be live before the hel01 nodes join the pod network.
- The talos change `add-helsinki-ingress-nodes`. The nodes must be Ready, carry the zone label `hel01` and the ingress taint.

## Impact

- Layers: `foundation-networking` (Cilium BGP, Envoy, Service labels) and `foundation-databases` (database proxy). No new layer, no rename.
- Runtime: two BGP sessions from the new nodes, two more announcers for `.1`, and new DaemonSet pods on the hel01 nodes. No running pod is evicted or restarted by this change.
- Routing: the Helsinki entry point changes. This is the main risk and is controlled by the router steps in `tasks.md`, not by Git.
