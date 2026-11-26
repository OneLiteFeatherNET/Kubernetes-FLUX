## 1. Preflight

- [ ] 1.1 Verify live that `10.200.90.12` in `lb-fr01` is free: confirm no Service holds it (`kubectl get svc -A -o wide`; the 2026-11-26 check found `.1`, `.3`, `.4`, `.5`, `.7`, `.8`, `.10` and `.11` in use) and that `harden-observability-coverage` does not reserve it; if it is taken, revisit decision 8 before the code pull request; record the result in the code pull request
- [ ] 1.2 Record the commit of `feat/k8s-runtime-core` in the cloudnet-kubernetes repository that the `mc01` jar is built from, and confirm that `deploy/rbac.yaml` and `deploy/headless-service.yaml` at that commit match the spec
- [ ] 1.3 Record the bucket name of the dedicated `ObjectBucketClaim` (own credentials, not the `cloudnet` user, not `cloudnet-templates`) in the code pull request
- [ ] 1.4 Confirm against the module's `config.json` at the commit from 1.2 that the node listens on `10.200.2.35:1410` and that `nodeServiceDns` is set to `10.200.2.35`; list the game ports of the services that remain on `mc01` for the egress rule
- [ ] 1.5 Confirm the internal network and site VPN ranges (same as `expose-grus-proxy-load-balancer`) that may reach the team proxy; record them in the code pull request
- [ ] 1.6 Record allocatable memory and CPU of `hel01-wrk-ed-01` to `03` (`kubectl describe node`) and confirm that the edge-scheduling delta in `add-helsinki-anycast-ingress` is merged or scheduled

## 2. Product directory

- [ ] 2.1 Add `products/cloudnet/base/cloudnet/` with `namespace.yaml` (Pod Security labels from the spec), `rbac.yaml` (ServiceAccount `cloudnet-node`, Role `cloudnet-service-runtime`, RoleBinding, rules from the module), `headless-service.yaml` (`cloudnet-services`) and `priorityclass.yaml` (`cloudnet-workload`, value `0`, `preemptionPolicy: Never`), plus its `kustomization.yaml`
- [ ] 2.2 Add `products/cloudnet/clusters/feather-core/cloudnet/` with `kustomization.yaml` (`namespace: cloudnet`, base reference), `token-secret.yaml` (type `kubernetes.io/service-account-token`, no data), `staging-bucket.yaml` (`ObjectBucketClaim` as in `products/blackhole/clusters/feather-core/blackhole/evidence-bucket.yaml`), `team-proxy.yaml` (`LoadBalancer` `cloudnet-team-proxy` with the pinned address, label `bgp.onelite.feather/site: hel01`, `externalTrafficPolicy: Local`) and `networkpolicy.yaml` (default deny plus the allows from the design)
- [ ] 2.3 Add `../../../cloudnet/clusters/feather-core/cloudnet` to `products/layers/feather-core/prod/kustomization.yaml`
- [ ] 2.4 If a staging credential file is needed in Git, create it with `sops` as `*.sops.*`; never write plaintext to a tracked file

## 3. Validation

- [ ] 3.1 Run `./.github/scripts/validate.sh` and `kubectl kustomize products/cloudnet/clusters/feather-core/cloudnet`
- [ ] 3.2 Run `.github/scripts/check-sops-encryption.py` on every new encrypted file
- [ ] 3.3 Confirm the rendered manifests contain no `HTTPRoute`, no `Certificate`, no `ClusterRole`, no `ClusterRoleBinding` and no Service of type `LoadBalancer` besides `cloudnet-team-proxy`
- [ ] 3.4 Confirm that the code pull request changes nothing under `cilium-bgp/`, `gateway/` or `openspec/`

## 4. Rollout verification (read-only, after merge)

- [ ] 4.1 `flux reconcile source git flux-system` once, then `flux get kustomizations products`
- [ ] 4.2 `kubectl -n cloudnet get sa,role,rolebinding,svc,obc,secret` shows the expected objects; the token Secret has a populated `token` key only after the cluster fills it
- [ ] 4.3 `kubectl get svc -n cloudnet cloudnet-team-proxy -o wide` shows the pinned address and an external IP; the address is announced from a hel01 node
- [ ] 4.4 Record Pod Security warnings and the pod-level results in the rollout notes; no secret values in the notes

## 5. Code pull request

- [ ] 5.1 Open the code pull request under the title `feat(cloudnet): add the cloudnet kubernetes runtime resources`; no files under `openspec/` in it
