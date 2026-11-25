## 1. Preflight

- [ ] 1.1 Confirm `prototype-4839dd3` exists in Harbor for `grus` (controller) and `grus-web` (web UI)
- [ ] 1.2 Confirm `https://github.com/OneLiteFeatherNET/grus.git` at `4839dd3` renders the chart with `web.enabled: true` (`helm template` with the release values)
- [ ] 1.3 Re-check the compare `4f5d516...4839dd3`: no Go file changed, and the controller Deployment, Role and RoleBinding templates unchanged
- [ ] 1.4 Confirm the web Deployment's selector labels do not match the controller Service or the controller NetworkPolicies
- [ ] 1.5 Run the `grus-web` image with the rendered security context (non-root, read-only root filesystem) and confirm `/healthz` answers
- [ ] 1.6 Record the tags the controller uses for proxy and lobby pods at runtime; confirm they are not changed by this pull request
- [ ] 1.7 Decide whether the web pod needs a priority class; record the decision in the rollout notes
- [ ] 1.8 Record free memory on the worker nodes (`kubectl describe nodes`) for 192Mi of web requests

## 2. Source and release values

- [ ] 2.1 Set `ref.commit` in `foundation/sources/clusters/feather-core/sources/grus.yaml` to `4839dd3c77dfd90c436a4f0bceabcc7e1128f2d7`
- [ ] 2.2 In `products/grus/clusters/feather-core/grus-dev/release.yaml` set `image.tag` to `prototype-4839dd3`
- [ ] 2.3 Add `web.enabled: true`, `web.image.repository: harbor.onelitefeather.dev/onelitefeather/grus-web` and `web.image.tag: prototype-4839dd3`; leave `web.imagePullSecrets` unset
- [ ] 2.4 Update the comment above the image tag in `release.yaml` if it still names the old commit; keep other values unchanged

## 3. Validation

- [ ] 3.1 Run `./.github/scripts/validate.sh`
- [ ] 3.2 Render the `grus-dev` overlay with `kubectl kustomize products/grus/clusters/feather-core/grus-dev` and confirm the `grus-web` Service is ClusterIP on port 3000
- [ ] 3.3 Confirm the rendered manifests contain no `LoadBalancer` Service, no HTTPRoute, no Certificate and no DNAT for `grus-web`
- [ ] 3.4 Confirm `grus-dev-secrets.sops.env` and the Harbor pull secret are unchanged, and no plaintext secret was added

## 4. Rollout verification (read-only, after the code pull request is merged)

- [ ] 4.1 `flux reconcile source git grus -n flux-system` once, then `flux get kustomizations products-dev`
- [ ] 4.2 `flux get helmreleases -n grus-dev` shows Ready at the merged revision
- [ ] 4.3 `kubectl -n grus-dev get pods,events` shows the `grus-web` pod Ready, no `ImagePullBackOff`, no `Denied`; record PSA warnings in the rollout notes
- [ ] 4.4 Confirm the running instances survived the controller restart and are adopted

## 5. Acceptance check (port-forward)

- [ ] 5.1 Run `kubectl -n grus-dev port-forward svc/grus-web 3000:3000`, open `http://localhost:3000` and check that the pools and instances list loads
- [ ] 5.2 Confirm the admin token value does not appear in the page source, loaded scripts or network responses
- [ ] 5.3 Send a cross-site write from another origin and confirm the UI rejects it
- [ ] 5.4 Scale one pool through the UI and confirm the change in the controller event stream
- [ ] 5.5 Record the results (not the tokens) in the rollout notes

## 6. Follow-ups outside this change

- [ ] 6.1 Entra OIDC login for the web UI (its own proposal)
- [ ] 6.2 Re-check port-forward against NetworkPolicy enforcement before the enforcement decision
- [ ] 6.3 Decision on any public access to the UI (its own proposal, with the certificate work)

## 7. Code pull request

- [ ] 7.1 Open the code pull request under the title `feat(products): enable the grus web ui in grus-dev`; no files under `openspec/` in it
