## 1. Prerequisites and routers

- [ ] 1.1 Confirm `lower-cilium-mtu` is live and the edge nodes are Ready with zone `hel01` and taint `node-role.feather/edge`. Verification: `kubectl get nodes -l topology.kubernetes.io/zone=hel01` lists `hel01-wrk-ed-01..03` as Ready.
- [ ] 1.2 Confirm the fw01 BGP configuration. Verification: `vtysh -c 'show bgp summary'` on fw01 lists `10.200.2.21`–`.23` with AS 64606, and `show route-map` lists `HEL01-IN`, `HEL01-OUT` and `TO-SITES-OUT`.
- [ ] 1.3 Operator: add the fw01 source NAT rule:
  - interface MGMT;
  - source `10.200.2.0/24`;
  - destination alias `HEL_INGRESS_VIPS` (the four VIP /32s);
  - translation `10.200.2.1`.

  Verification: `pfctl -s nat` shows the rule, and the pf states of `.35` and `.69` to `.1` are unchanged.
- [ ] 1.4 Optional operator task: add the mkgfire import filter (`FROM-HEL-IN`) on its sessions to fw01 and Ludwigsburg. Verification: no session resets on mkgfire, and its VIP routes still point to the fr01 nodes.

## 2. First code pull request: zone-local gateway traffic

- [ ] 2.1 Set `loadBalancer.serviceTopology: true` in `foundation/networking/clusters/feather-core/cilium/release.yaml`. Verification: `kubectl kustomize` renders `enable-service-topology: "true"`.
- [ ] 2.2 Set `trafficDistribution: PreferSameZone` on the Envoy Service through `envoyService.patch` in `foundation/networking/clusters/feather-core/gateway/envoyproxy.yaml`. Verification: after reconcile, the Envoy EndpointSlice endpoints carry the hint `forZones: fr01`.
- [ ] 2.3 Run `./.github/scripts/validate.sh`. Verification: exit code 0.
- [ ] 2.4 Open the code pull request `feat(networking): keep in-cluster gateway traffic in its zone`, without files under `openspec/`.
- [ ] 2.5 After the merge, restart the Cilium agents node by node, each one gated on these checks:
  - agent ready;
  - BGP sessions back;
  - Galera 3/3.

  Verification: `cilium-dbg config` on every agent shows `EnableServiceTopology: true`.

## 3. Second code pull request: announce the gateway from Helsinki

- [ ] 3.1 Add `CiliumBGPClusterConfig` `hel01`, `CiliumBGPPeerConfig` `fw01-v4` and `CiliumBGPAdvertisement` `hel01-ingress` in `foundation/networking/clusters/feather-core/cilium-bgp/`. Verification: `kubectl apply --dry-run=server` accepts them, and the timers equal those of `mkgfire-v4`.
- [ ] 3.2 Add the label `bgp.onelite.feather/site: hel01` to the Envoy Service (`envoyService.labels`). Add a toleration for `node-role.feather/edge` (`Exists`, `NoSchedule`) to `envoyDaemonSet.pod.tolerations`. Verification: `kubectl kustomize` renders both.
- [ ] 3.3 Run `./.github/scripts/validate.sh`. Verification: exit code 0.
- [ ] 3.4 Open the code pull request `feat(networking): announce the gateway vip from helsinki`, without files under `openspec/`, after group 2 is verified.

## 4. Rollout and verification

- [ ] 4.1 Confirm the Envoy DaemonSet rolled out and runs on the three edge nodes. Verification: `kubectl -n envoy rollout status ds`.
- [ ] 4.2 Confirm the sessions and routes on fw01. Verification:
  - `show bgp summary` shows `10.200.2.21`–`.23` Established, with 1 prefix each;
  - the best path for `10.200.90.1/32` has a hel01 next hop.
- [ ] 4.3 Confirm there is no leak. Verification: `show bgp ipv4 neighbors <site> advertised-routes` on fw01 contains no `10.200.90.1/32` with AS path `64504 64606`, for each of the four site sessions.
- [ ] 4.4 Confirm zone locality. Verification: from a debug pod on an fr01 node, 20 requests to `10.200.90.1` all appear in fr01 Envoy access logs.
- [ ] 4.5 Confirm the Helsinki path. Verification:
  - an HTTPS request from mc01 to `10.200.90.1` succeeds and appears in a hel01 Envoy access log;
  - a request to 65.108.236.147 still succeeds.
- [ ] 4.6 Fallback test: delete the Envoy pod on one hel01 node. Verification: fw01 withdraws that next hop, and requests from mc01 keep succeeding.

- [ ] 4.7 After `add-cloudnet-kubernetes-runtime` is rolled out, check the guard rails. Verification:
  - `kubectl get pods -A --field-selector spec.nodeName=hel01-wrk-ed-01` shows only the listed workloads;
  - every `cloudnet` pod has a PriorityClass below `feather-platform`;
  - every `cloudnet` container has its memory request equal to its limit.

## 5. Documentation

- [ ] 5.1 Write the Outline runbook for the Helsinki ingress (collection Infrastruktur). It covers:
  - the topology;
  - the BGP sessions and router filters;
  - the source NAT rule;
  - the fallback behaviour;
  - the rollback.

  Verification: the document exists and is linked from the GitOps document.
