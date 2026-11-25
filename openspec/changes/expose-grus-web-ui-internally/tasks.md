## 1. Preflight

- [ ] 1.1 Confirm `grus-dev.apps.onelite.feather` resolves to `10.200.90.1` from an internal client
- [ ] 1.2 Confirm the running `grus-web` pod carries `app.kubernetes.io/name=grus-web` and `app.kubernetes.io/component=web`
- [ ] 1.3 Confirm the UI write guard accepts a same-origin request whose Host and Origin are `grus-dev.apps.onelite.feather`; record the result
- [ ] 1.4 Confirm the envoy proxy pods carry `app.kubernetes.io/name=envoy` and `app.kubernetes.io/component=proxy` in namespace `envoy`
- [ ] 1.5 Record the gateway's default stream idle timeout and whether an idle event stream is cut before the UI keep-alive

## 2. Certificate and gateway

- [ ] 2.1 Add Certificate `grus-dev-apps-onelite-feather` to `foundation/certificates/clusters/feather-core/internal-certs/internal-certificates.yaml`, placed like the others
- [ ] 2.2 Add `grus-dev-apps-onelite-feather-tls` to the https listener `certificateRefs` in `foundation/networking/clusters/feather-core/gateway/gateway.yaml`, next to the menura-dev entry; change nothing else

## 3. Route and policy

- [ ] 3.1 Create `products/grus/clusters/feather-core/grus-dev/httproute.yaml` with the two rules and the response headers from the design
- [ ] 3.2 Add NetworkPolicy `allow-envoy-gateway-ingress` to `products/grus/clusters/feather-core/grus-dev/networkpolicy.yaml`, selecting only `app.kubernetes.io/name: grus-web` on TCP 3000
- [ ] 3.3 List `httproute.yaml` in `products/grus/clusters/feather-core/grus-dev/kustomization.yaml`
- [ ] 3.4 Update the comments in `products/grus/clusters/feather-core/grus-dev/release.yaml` that say the UI is reachable only through port-forward; change no values

## 4. Validation

- [ ] 4.1 Run `kubectl kustomize` on the changed overlays and `kubeconform` as CI does
- [ ] 4.2 Run `./.github/scripts/validate.sh`
- [ ] 4.3 Confirm the rendered manifests contain no LoadBalancer Service, no DNAT, no Cloudflare record and no second hostname
- [ ] 4.4 Run `openspec validate expose-grus-web-ui-internally --strict`

## 5. Code pull request

- [ ] 5.1 Open the code pull request under the title `feat(products): expose the grus web ui on the internal gateway`; no files under `openspec/` in it
