## Context

The Grus Velocity proxy (`grus-velocity` image, pool `proxy`, port 25565) is the entry point for Minecraft clients. It runs in `grus-dev` as one pod per proxy instance. Testers on the internal network and the VPN need a stable address for it. The shared Cilium pool `lb-fr01` (`10.200.90.1` to `10.200.90.15`) already serves the gateway, databases and log ingestion.

The decision drivers, from the maintainer:

- A fixed, documented address that testers can type into a Minecraft client.
- Connections spread over all proxy instances, so a pool scaled to two or more proxies is exercised.
- Nothing outside the internal network, and nothing that changes the lobbies, the controller or the game pods.
- Small blast radius: only the proxy pods are opened.

## Goals / Non-Goals

**Goals**

- `10.200.90.11:25565` reaches the proxy pods from the internal network and the VPN.
- Every proxy instance Grus runs receives connections.
- The change is one new file and one kustomization line.

**Non-Goals**

- Anycast, BGP advertisement, public exposure, DNS records, a login layer, a second pool or any change to the proxy itself.

## Options and decisions

### 1. Port-forward only (status quo)

**Decision:** not chosen.

**Consequences:** testers need a kubectl context and a port-forward per proxy; a port-forward reaches one pod only, so it cannot show the spread over instances.

### 2. LoadBalancer Service with a fixed internal address

**Decision:** chosen by the maintainer. Service `grus-dev-proxy`, type `LoadBalancer`, `lbipam.cilium.io/ips: 10.200.90.11`, selector on the proxy labels, TCP 25565, plus one NetworkPolicy for the proxy pods.

**Consequences:**

- The address is fixed for the dev prototype and documented in the proposal and the code pull request.
- Anyone in the admitted ranges can connect. Online mode is the only gate (accepted risk, see the proposal).
- The Service is the only new address in `lb-fr01` for Grus.

### 3. NodePort

**Decision:** rejected.

**Alternatives considered:** a NodePort on the fr01 nodes exposes a high port on every node and needs the testers to know a node address and a port that changes with the Service.

**Consequences:** none; not built.

### 4. Anycast for the proxies

**Decision:** deferred to Grus v2.

**Alternatives considered:** a shared anycast address announced from several nodes gives failover between proxies. It needs a BGP design for the proxy pods and a decision on the advertisement path. That is a separate topic.

**Consequences:** the fixed address here is a single-VIP design. Its successor is a Grus v2 design, not a change to this Service.

## Sub-decision: externalTrafficPolicy

| Value | Behaviour with the proxy pool | Result |
|------|------|------|
| `Local` | Only nodes that run a ready proxy pod receive the VIP traffic | Traffic can fail to spread over instances when proxies run on different nodes |
| `Cluster` (chosen) | Traffic reaches any ready proxy pod, through an extra hop when the pod is on another node | Spread over every instance; the source address seen by the pod can be SNATed |

The repo uses `Local` for single-endpoint services (dragonfly master, maxscale, postgres primary). This Service fronts a pool, so `Cluster` is the choice. The consequence is handled in the policy: the source may be the node address, so the policy admits the node network as well as the client networks.

## Policy design

| Source | Port | Target | Result |
|------|------|------|------|
| `192.168.15.0/24` (fr01 node network) | TCP 25565 | pods `grus.olf/role=proxy` | Allowed. Covers SNATed traffic from nodes |
| `10.200.2.0/24` (management network) | TCP 25565 | pods `grus.olf/role=proxy` | Allowed. Same range dragonfly admits for its VIP |
| VPN client range | TCP 25565 | pods `grus.olf/role=proxy` | Open question: the range is not in this repository. Added to the code pull request once confirmed |
| Any other source | any | any | Denied by `default-deny-all` |
| Any source | TCP 25565 | lobby pods, controller, web | Not opened by this change |

## Risks / Trade-offs

| Risk | Mitigation |
|------|------|
| Anyone in the admitted ranges can join the dev proxies | Accepted by the maintainer for the dev prototype; recorded in the proposal. Online mode is the gate |
| SNAT hides the client address | Accepted for the prototype; the address the proxy sees is recorded in verification after merge |
| VPN range unknown | Open question; the code pull request adds it only after it is confirmed |
| Pool scale-down drains a proxy with clients connected | The test asks for a drain of one proxy instance; the connection moves to a remaining proxy on reconnect |
| `Cluster` policy adds a hop | Accepted; the lobbies are on the same cluster and the latency cost is small |

## Migration Plan

1. Proposal pull request merges.
2. Code pull request merges. Flux reconciles `products-dev`.
3. Verify the address, the spread over proxies and the refusal from outside the ranges.

**Rollback:** revert the code pull request.

## Open Questions

- Which VPN client range should the policy admit? Answer before the code pull request is opened (task 1.4).
- Does a connection from an internal client keep its client address or the node address at the proxy? Answer in verification after merge, and record it.
