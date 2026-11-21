## 1. Prerequisites

- [ ] 1.1 Confirm `lower-cilium-mtu` is live: a pod in fr01 reports MTU 1350. Verification: `ip link` inside a debug pod shows `mtu 1350` on its interface.
- [ ] 1.2 Confirm the talos change `add-helsinki-ingress-nodes` is applied. Verification: `kubectl get nodes -l topology.kubernetes.io/zone=hel01` lists `hel01-wrk-in-01` and `hel01-wrk-in-02` as Ready, and `kubectl describe node` shows `node-role.feather/ingress=true:NoSchedule` on both.
- [ ] 1.3 Operator: provide the fw01 ASN and the fw01 IPv6 ULA address; record them in `design.md`, open question 1. Verification: both values are written into the peer manifests in task 3.1 and 3.2.
- [ ] 1.4 Operator: record the current Helsinki route to `10.200.90.0/24` on fw01 (next hop and path). Verification: the output is attached to the change notes, without secrets, and open question 2 is answered.
- [ ] 1.5 Operator: on mkgfire, add an import filter that rejects the four ingress VIP host routes from fw01. Verification: `show route` on mkgfire shows no VIP route learned from the fw01 neighbour.
- [ ] 1.6 Operator: on fw01, configure the BGP neighbours toward the hel01 nodes with a higher local preference for the four VIPs, and no export of those VIPs to mkgfire or WG_ROSENAU. Verification: the advertised-routes list of the mkgfire and WG_ROSENAU neighbours contains none of the four VIPs.

## 2. Verify LB-IPAM sharing and finalize decision 1

- [ ] 2.1 Read the Cilium 1.20 documentation on LB IPAM IP sharing (`lbipam.cilium.io/sharing-key`, `sharing-cross-namespace`). Test in a scratch namespace in fr01: two Services with one sharing key and the same port 5432. Verification: the outcome (accepted or rejected) is recorded in decision 1; the scratch namespace is deleted afterwards.
- [ ] 2.2 If 2.1 is accepted, test two Services with one sharing key on different ports, and confirm both receive the same IP. Verification: `kubectl get svc` shows the same external IP for both. Skip if 2.1 is rejected.
- [ ] 2.3 Record the decision: option C if sharing works for the database ports; otherwise option D, and the operator confirms it before group 3 starts. Choose the database proxy implementation (open question 5). Verification: `design.md` decision 1 names the outcome and the proxy.

## 3. Manifests

- [ ] 3.1 Add `CiliumBGPClusterConfig` `hel01` in `foundation/networking/clusters/feather-core/cilium-bgp/`, with peers `fw01-v4` and `fw01-v6`. Verification: `kubectl kustomize` renders it, and `kubectl apply --dry-run=server` accepts it.
- [ ] 3.2 Add `CiliumBGPPeerConfig` `fw01-v4` and `fw01-v6` with the timers and graceful restart of the mkgfire peers, and advertisement selector `bgp.onelite.feather/advertise: hel01-ingress`. Verification: rendered timers equal those of `mkgfire-v4`.
- [ ] 3.3 Add `CiliumBGPAdvertisement` `hel01-ingress` selecting `bgp.onelite.feather/site: hel01`, without the label `bgp.onelite.feather/advertise: lb-ips`. Verification: `grep` over the rendered output shows that only the hel01 peers select this advertisement.
- [ ] 3.4 Label the four Services `bgp.onelite.feather/site: hel01`: Envoy (`EnvoyProxy` `envoyService.labels`), PostgreSQL `rw-external` (`serviceTemplate` labels), MaxScale `kubernetesService` labels and `dragonfly-master-lb`. Verification: `kubectl get svc -A -l bgp.onelite.feather/site=hel01` lists exactly four Services.
- [ ] 3.5 Add the ingress toleration to the Envoy DaemonSet, the Cilium agent (only if the chart default does not tolerate it), node-exporter, alloy (logs) and trivy. Verification: the rendered DaemonSets carry the toleration, and `grep` finds no other workload that tolerates `node-role.feather/ingress`.
- [ ] 3.6 Add the Helsinki database proxy in `foundation/databases/base/hel-db-proxy/` and overlay `foundation/databases/clusters/feather-core/hel-db-proxy/`. It runs as a DaemonSet on zone `hel01`, listens on 5432, 3306 and 6379, and forwards to the internal ClusterIP Services of the databases, never to the LoadBalancer Services. Packaging follows the repository rule. Verification: `kubectl kustomize` renders it, and `kubectl apply --dry-run=server` accepts it.
- [ ] 3.7 Update the access rules for the proxy pod CIDR: the pg_hba rules, the MaxScale host rules and the Dragonfly NetworkPolicy (which lists `10.200.2.0/24`). Verification: `kubectl kustomize` renders the changed policies, and the connection test in task 4.6 passes with the policies enforced.
- [ ] 3.8 Run `./.github/scripts/validate.sh`. Verification: the script exits 0.
- [ ] 3.9 Confirm that the change touches no file under `openspec/`. Verification: `git diff --stat origin/main` lists no `openspec/` path.

## 4. Rollout and verification (after the code PR of group 6 is merged)

- [ ] 4.1 Confirm `foundation-networking` and `foundation-databases` are Ready at the merged revision. Verification: `flux get kustomizations` shows both Ready at the same revision.
- [ ] 4.2 Confirm the v4 BGP sessions on both hel01 nodes are Established and advertise exactly the four VIPs. Verification: `cilium bgp peers` on each hel01 node shows the session state and the advertised routes.
- [ ] 4.3 Confirm the route tables: fw01 learns the VIPs from the hel01 neighbours with the higher preference; mkgfire and fr01 hold no VIP route learned from Helsinki. Verification: route output of fw01 and mkgfire attached to the change notes.
- [ ] 4.4 Operator: apply the fw01 import filter that drops the mkgfire-learned VIP prefixes. Only after 4.2 and 4.3 pass. Verification: fw01 shows the four VIPs with the hel01 next hop only.
- [ ] 4.5 Operator: apply the DNAT of 65.108.236.147:80 and :443 to `10.200.90.1` on fw01. Verification: `curl` to 65.108.236.147 with the expected Host header returns the Envoy response, and the Envoy access log shows the hel01 node as the receiving node.
- [ ] 4.6 Connection tests from mc01 to `10.200.90.1` (HTTP), `.3` (psql), `.4` (mysql) and `.5` (redis PING), with an existing read-only test role and no secret printed. Verification: each command succeeds, and the Envoy access log shows the Helsinki client address.
- [ ] 4.7 Traceroute from mc01 and from an fr01 host to the four VIPs. Verification: the mc01 path goes through fw01 to a hel01 node; the fr01 path stays on fr01 nodes.
- [ ] 4.8 Failover test in a maintenance window: drain and stop one hel01 node while a loop of HTTP requests and psql queries runs. Verification: the loop keeps succeeding apart from connections that were open on that node; the route returns when the node is back. Then, in the same window, record the Helsinki behaviour with both hel01 nodes down (fail closed, as decided) and the recovery time.

## 5. Documentation

- [ ] 5.1 Write the Outline runbook for the Helsinki ingress (collection Infrastruktur): topology, BGP sessions, router filters, fail-closed behaviour and the rollback. Verification: the document exists in Outline and is linked from the GitOps document.
- [ ] 5.2 Record the test results of task 4.8 (failover, double failure, recovery time) in the same runbook. Verification: the runbook contains the measured values and the date of the test.

## 6. Code pull request

- [ ] 6.1 Open the code pull request under the title `feat(networking): announce the ingress vips from helsinki`, after groups 1 to 3 are complete. It contains no file under `openspec/`. Verification: `gh pr view` shows the title, and `git diff --stat origin/main` lists no `openspec/` path.
