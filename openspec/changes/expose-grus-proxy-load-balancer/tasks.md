## 1. Preflight

- [ ] 1.1 Confirm `10.200.90.11` is held by no Service (`kubectl get svc -A`) and is not referenced on `origin/main`
- [ ] 1.2 Confirm the proxy pods carry `grus.olf/pool=proxy` and `grus.olf/role=proxy` and expose container port 25565 named `minecraft`
- [ ] 1.3 Confirm the pool `lb-fr01` has no conflict condition
- [ ] 1.4 Confirm the VPN client range that the policy must admit, and record it in the code pull request

## 2. Proposal

- [ ] 2.1 Merge the proposal pull request before the code pull request is opened

## 3. Service and policy

- [ ] 3.1 Create `products/grus/clusters/feather-core/grus-dev/proxy-loadbalancer.yaml` with Service `grus-dev-proxy` (address, selector, port, `externalTrafficPolicy: Cluster`) and NetworkPolicy `allow-internal-proxy-ingress`
- [ ] 3.2 List `proxy-loadbalancer.yaml` in `products/grus/clusters/feather-core/grus-dev/kustomization.yaml`

## 4. Validation

- [ ] 4.1 Run `kubectl kustomize products/grus/clusters/feather-core/grus-dev` and `./.github/scripts/validate.sh`
- [ ] 4.2 Confirm the rendered manifests contain one LoadBalancer Service with `10.200.90.11`, no DNAT, no Cloudflare record and no new hostname
- [ ] 4.3 Run `openspec validate expose-grus-proxy-load-balancer --strict`

## 5. Code pull request

- [ ] 5.1 Open the code pull request under the title `feat(products): expose the grus dev proxies on an internal load balancer`; no files under `openspec/` in it
