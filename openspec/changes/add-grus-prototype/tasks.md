## 1. Preflight

- [ ] 1.1 Pick the Grus commit to pin and confirm that `prototype-<sha>` exists for `grus`, `grus-velocity` and `grus-demo-minestom` in Harbor
- [ ] 1.2 Confirm `https://github.com/OneLiteFeatherNET/grus.git` is readable without credentials and that `deploy/helm/grus` exists at the pinned commit
- [ ] 1.3 Record free memory requests on `fr01-wrk-xl-01` to `xl-04` (`kubectl describe nodes`); decide whether the optional `nodeSelector` is needed
- [ ] 1.4 Confirm the names `grus-dev` (namespace) and `grus` (source and HelmRelease) are unused on the cluster
- [ ] 1.5 Copy the Harbor pull secret file from `products/menura/clusters/feather-core/menura/` and verify with `sops --decrypt` that it has the `.dockerconfigjson` key, without printing it

## 2. Source and product directory

- [ ] 2.1 Add `foundation/sources/clusters/feather-core/sources/grus.yaml` (`GitRepository` `grus`, `ref.commit` pinned, `interval: 10m`) and list it in that directory's `kustomization.yaml`
- [ ] 2.2 Add `products/grus/clusters/feather-core/grus-dev/namespace.yaml` with the Pod Security labels (`enforce: baseline`, `warn`/`audit: restricted`)
- [ ] 2.3 Add `release.yaml`: HelmRelease `grus`, chart `./deploy/helm/grus`, `reconcileStrategy: Revision`, `interval: 10m0s`, remediation `retries: 3`, fixed `prototype-<sha>` tags, `priorityClassName: feather-low`, controller requests 64Mi and 50m, `maxInstances: 10`, RWO PVC 1Gi with `strategy: Recreate`
- [ ] 2.4 Add `networkpolicy.yaml` in the `menura-dev` pattern (default deny, Envoy ingress, DNS and HTTPS egress without private ranges)
- [ ] 2.5 Add `kustomization.yaml` with `namespace: grus-dev`, `disableNameSuffixHash: true`, the `harbor-pull` `secretGenerator` and `grus-dev-secrets.sops.env`
- [ ] 2.6 Add the copied `harbor.onelitefeather.dev.dockerconfigjson.sops.json` in the same directory
- [ ] 2.7 Confirm the Role and RoleBinding for the controller ServiceAccount are namespaced and match the verbs in the spec

## 3. Secrets

- [ ] 3.1 Create `grus-dev-secrets.sops.env` with `sops` (admin token and Velocity forwarding secret); never write plaintext to a tracked file
- [ ] 3.2 Run `.github/scripts/check-sops-encryption.py` on the new and copied files

## 4. Layer and validation

- [ ] 4.1 Add `../../../grus/clusters/feather-core/grus-dev` to `products/layers/feather-core/dev/kustomization.yaml`
- [ ] 4.2 Run `./.github/scripts/validate.sh` and `kubectl kustomize products/grus/clusters/feather-core/grus-dev`
- [ ] 4.3 Confirm the rendered manifests contain no `LoadBalancer` Service, no HTTPRoute, no Certificate and no ClusterRoleBinding for `grus`

## 5. Rollout verification (read-only)

- [ ] 5.1 After merge: `flux reconcile source git flux-system` once, then `flux get kustomizations products-dev`
- [ ] 5.2 `flux get helmreleases -n grus-dev` shows Ready at the merged revision
- [ ] 5.3 `kubectl -n grus-dev get pods,events` shows no `ImagePullBackOff` and no `Denied`; record PSA warnings in the rollout notes

## 6. Acceptance demo

- [ ] 6.1 Run the demo through `kubectl -n grus-dev port-forward` as described in the design: `/readyz`, pool creation, instance states on the event stream, proxy registration of the lobbies, scale-up with the debug status endpoint, scale-down, controller pod deletion and adoption
- [ ] 6.2 Record the results (not the tokens) in the rollout notes

## 7. Follow-ups outside this change

- [ ] 7.1 Suspend path, only if the prototype blocks `products-dev`: a separate pull request with `spec.suspend: true`
- [ ] 7.2 Image work for non-root game images and `restricted` compliance (in the Grus repository)
- [ ] 7.3 Decision on enforcing NetworkPolicies cluster-wide (its own proposal)

## 8. Code pull request

- [ ] 8.1 Open the code pull request under the title `feat(products): add the grus prototype`; no files under `openspec/` in it
