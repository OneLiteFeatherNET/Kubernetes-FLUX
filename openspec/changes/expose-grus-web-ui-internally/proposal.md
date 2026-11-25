## Why

The Grus web UI runs in `grus-dev` on `feather-core` (merged in #723). Today it is reachable only through `kubectl port-forward`, which is awkward for the operators who review pools, instances and the event stream from the internal network.

BLUF: this change publishes the `grus-web` Service on the shared internal Envoy gateway at `https://grus-dev.apps.onelite.feather`, the same way menura-dev is published. **There is no login at the gateway, by explicit decision of the maintainer.** The UI stays internal (internal network and VPN only, no public hostname). Its own cross-site write guard and the server-side admin token stay as they are. **Risk accepted:** anyone on the internal network can control grus-dev (start and stop instances, scale pools) without signing in. This is a dev prototype; the risk is recorded here so the decision is visible to reviewers.

## Current state

- Access is port-forward only. `enable-grus-web-ui` (design option 4, requirement "No external exposure") forbids an HTTPRoute and a Certificate for `grus-web`.
- DNS: `grus-dev.apps.onelite.feather` already resolves to the gateway IP `10.200.90.1` through the internal wildcard record. No DNS change is needed.
- The shared gateway `eg` in namespace `envoy` already serves `menura-dev.apps.onelite.feather` with an HTTPRoute, a step-ca Certificate and a listener `certificateRefs` entry.
- `products/grus/clusters/feather-core/grus-dev/networkpolicy.yaml` has `default-deny-all` for ingress. Envoy cannot reach `grus-web` on port 3000 today, so the route would return errors until a policy allows it.
- The running web pod carries `app.kubernetes.io/name: grus-web` and `app.kubernetes.io/component: web`.

## Decision and risk

- Chosen option: an internal HTTPRoute without authentication (see `design.md`, option 2). The maintainer decided this explicitly. Entra OIDC at the gateway is the recorded follow-up.
- Exposure stays internal. No LoadBalancer, no public hostname, no Cloudflare record, no DNAT.
- The network policy opens port 3000 on `grus-web` only. The controller API, the game pods and the proxy are not opened to Envoy.
- The UI write guard protects against cross-site writes from a browser. It does not authenticate a client on the internal network. Anyone who can reach the hostname can act with the admin token.

## What Changes

- `foundation/certificates/clusters/feather-core/internal-certs/internal-certificates.yaml`: add Certificate `grus-dev-apps-onelite-feather` in namespace `envoy`, secret `grus-dev-apps-onelite-feather-tls`, issuer `step-ca-issuer` (StepClusterIssuer), RSA 2048, rotation `Always`, DNS name `grus-dev.apps.onelite.feather`.
- `foundation/networking/clusters/feather-core/gateway/gateway.yaml`: add `grus-dev-apps-onelite-feather-tls` to the `certificateRefs` of the https listener, next to the menura-dev entry. Nothing else in that file changes.
- `products/grus/clusters/feather-core/grus-dev/httproute.yaml` (new): HTTPRoute `grus-dev` on parent `eg` / `envoy` / `https`, hostname `grus-dev.apps.onelite.feather`, backend `grus-web` port 3000.
  - Rule 1 covers `/api/grus/v1/events` and `/api/grus/v1/instances` (live log streams sit below the latter). Request and backend timeouts are `0s`, because server-sent event streams must not hit the gateway's default 15 s timeout.
  - Rule 2 covers `/` with default timeouts.
  - Both rules set HSTS, `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`, as menura-dev does.
- `products/grus/clusters/feather-core/grus-dev/networkpolicy.yaml`: add `allow-envoy-gateway-ingress`, from the envoy proxy pods only, to pods labelled `app.kubernetes.io/name: grus-web` on TCP 3000.
- `products/grus/clusters/feather-core/grus-dev/kustomization.yaml`: list `httproute.yaml`.
- `products/grus/clusters/feather-core/grus-dev/release.yaml`: update the comments that say the UI is reachable only through port-forward and that there is no HTTPRoute. Values are not changed; port-forward keeps working.

**Commit type and scope:** `feat(products)`. The code pull request is titled `feat(products): expose the grus web ui on the internal gateway`.

**Relation to `enable-grus-web-ui`:** this change supersedes that change's "No external exposure" scenarios for the internal gateway only. Its requirements for the workload, the security context, the token and the pins still hold.

## Out of scope and non-goals

- No login, no OIDC, no basic auth at the gateway (see the decision above).
- No public hostname, no Cloudflare, no LoadBalancer IP, no DNAT.
- No change to the controller, the game pods, the proxy, the source pin or the image tags.
- No change to the gateway's other listeners, routes or certificates.
- No `BackendTrafficPolicy` for streams. The route timeouts are set; the gateway's connection-level stream idle timeout is checked during rollout (see the tasks).

## Impact

- Capability `grus-web-internal-access` (see `specs/grus-web-internal-access/spec.md`).
- Six files change in the code pull request: the two shared files (certificates and gateway), one new route, the network policy, the kustomization and the release comments.
- The `products-dev` layer waits on the new objects with `wait: true`. The Certificate is in the `envoy` namespace, and the gateway picks it up through the listener.
- Anyone on the internal network who resolves the hostname can reach the UI. This is the accepted risk above.

## Rollback

1. Revert the code pull request. Flux removes the route, the policy entry and the Certificate. The gateway listener returns to its previous state.
2. Port-forward keeps working throughout, because the Service is unchanged.

## Verification after merge

Recorded in the code pull request: the route is accepted by the gateway, the certificate is issued by `step-ca-issuer`, a browser on the internal network loads the pools and instances pages at `https://grus-dev.apps.onelite.feather`, the event stream stays open past 15 s, and a cross-site write is still refused.
