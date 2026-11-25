## Why

The Grus Velocity proxies run in `grus-dev` on `feather-core` (proxy pods labelled `grus.olf/role=proxy`, container port 25565). Today nothing outside the namespace can reach them, so the maintainer cannot test a Minecraft connection to the dev proxies from the internal network or the VPN.

BLUF: this change adds one Service of type LoadBalancer, `grus-dev-proxy`, with the fixed internal address `10.200.90.11` from Cilium pool `lb-fr01`, on TCP 25565. It selects only the proxy pods, so connections spread over all proxy instances Grus runs. A network policy admits the internal source ranges to those pods on that port and nothing else. **Nothing is published outside the internal network.** **Risk accepted:** anyone on the internal network or VPN who can reach `10.200.90.11` can join the dev proxies with a Minecraft account that Mojang accepts, and reach the lobbies behind them. This is a dev prototype; the risk is recorded here so reviewers see the decision.

## Current state

- The proxies have no ingress from outside the namespace. `allow-namespace-minecraft-ingress` admits TCP 25565 only from pods of `grus-dev` itself, and `default-deny-all` denies everything else.
- The proxy pods carry `grus.olf/pool=proxy` and `grus.olf/role=proxy`. The lobby pods carry `grus.olf/pool=lobby`, so a selector on the proxy labels keeps them out of the Service.
- The proxy runs in online mode. The header comment of `products/grus/clusters/feather-core/grus-dev/networkpolicy.yaml` names the proxy's Mojang session check as the reason for its HTTPS egress, so a client needs a valid Minecraft account.
- Address `10.200.90.11` is not held by any Service in `kubectl get svc -A` and is not referenced anywhere on `origin/main`. The pool `lb-fr01` covers `10.200.90.1` to `10.200.90.15`.
- Other LoadBalancer Services in the repo pin their address with `lbipam.cilium.io/ips`. The dragonfly, maxscale and postgres ones use `externalTrafficPolicy: Local`, because they front one primary endpoint.

## Decision and risk

- Chosen option: a LoadBalancer Service with a fixed internal address and `externalTrafficPolicy: Cluster`, so that connections spread over every proxy pod (see `design.md`).
- Exposure stays internal: no public hostname, no Cloudflare record, no DNAT, no anycast. Anycast for proxies is a Grus v2 design topic.
- Anyone on the internal network or VPN who reaches `10.200.90.11` can join the dev proxies. The proxies do not hold admin rights; the controller API stays closed to them, as today.
- The source ranges admitted are the ones the cluster already admits for LoadBalancer clients (dragonfly): the node network `192.168.15.0/24` and the management network `10.200.2.0/24`. The VPN client range is not in this repository and is an open question for the code pull request.

## What Changes

- `products/grus/clusters/feather-core/grus-dev/proxy-loadbalancer.yaml` (new): Service `grus-dev-proxy` in namespace `grus-dev`, type `LoadBalancer`, annotation `lbipam.cilium.io/ips: 10.200.90.11`, `externalTrafficPolicy: Cluster`, selector `grus.olf/pool=proxy` and `grus.olf/role=proxy`, port 25565/TCP to the named container port `minecraft`. The same file carries NetworkPolicy `allow-internal-proxy-ingress`, which selects the proxy pods and admits TCP 25565 from the two internal ranges only.
- `products/grus/clusters/feather-core/grus-dev/kustomization.yaml`: list `proxy-loadbalancer.yaml`.

Nothing else changes. No file under `products/grus/` that the Grus v2 work owns is touched.

**Commit type and scope:** `feat(products)`. The code pull request is titled `feat(products): expose the grus dev proxies on an internal load balancer`.

## Out of scope and non-goals

- No change to the proxy, the controller, the game pods, the lobbies, the image tags or the source pin.
- No public hostname, no Cloudflare, no DNAT, no anycast, no BGP change.
- No authentication layer in front of the proxy. Minecraft's own online-mode check is the only gate.
- No change to `release.yaml`, the web UI or the gateway.

## Impact

- Capability `grus-proxy-internal-access` (see `specs/grus-proxy-internal-access/spec.md`).
- Two files change in the code pull request: one new file and the kustomization line.
- The `products-dev` layer applies the Service and the policy with `wait: true`. The Service is immediate; the address comes from the existing pool.

## Rollback

1. Revert the code pull request. Flux removes the Service (and its address) and the policy.
2. The proxies keep running and stay reachable in-namespace as before.

## Verification after merge

Recorded in the code pull request: the Service has `EXTERNAL-IP 10.200.90.11`; a Minecraft client on the internal network connects to `10.200.90.11:25565`; with two proxy pods, connections land on both (checked through the proxy logs); a lobby pod is not reachable through the address; a client outside the internal ranges is refused.
