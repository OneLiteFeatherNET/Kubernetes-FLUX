## Why

The Grus prototype runs in `grus-dev` on `feather-core`: a controller, a Velocity proxy and Minestom lobbies, all as pods, started by Flux from Git. Today an operator inspects pools, instances and the event stream with `curl` against the controller API. The Grus repository now contains a web UI for exactly that (Nuxt, Nitro server, chart component added in grus PR #33). Turning it on lets the acceptance demo run in a browser.

BLUF: this change enables the optional web UI of the Grus chart in `grus-dev` at image tag `prototype-4839dd3`, and moves the pinned Grus source and the controller image to the same commit, `4839dd3`. The UI stays behind `kubectl port-forward`. It has no login yet.

## Current state

- `foundation/sources/clusters/feather-core/sources/grus.yaml` pins the Grus repository to `4f5d5169e20f6e1f8f35a728e1f1f8d4de708b59`.
- `products/grus/clusters/feather-core/grus-dev/release.yaml` runs the controller at `prototype-4f5d516` and leaves `web` disabled (chart default `web.enabled: false`).
- Between `4f5d516` and `4839dd3` the Grus repository changes the web UI, its chart templates and values, the CI workflows and the Velocity Dockerfile. It changes no Go file. The controller's Deployment, Role and RoleBinding templates are unchanged. The controller code is therefore the same; only its image tag moves.

## What Changes

- `grus` GitRepository: `ref.commit` moves from `4f5d5169e20f6e1f8f35a728e1f1f8d4de708b59` to `4839dd3c77dfd90c436a4f0bceabcc7e1128f2d7`.
- `grus` HelmRelease values in `grus-dev/release.yaml`:
  - `image.tag` moves from `prototype-4f5d516` to `prototype-4839dd3` (the controller, same code, new tag).
  - `web.enabled: true`.
  - `web.image.repository: harbor.onelitefeather.dev/onelitefeather/grus-web`, `web.image.tag: prototype-4839dd3`.
  - No `web.imagePullSecrets`: the chart falls back to `imagePullSecrets: [{name: harbor-pull}]`.
- The web UI, as the chart renders it:
  - Nuxt/Nitro, one replica, ClusterIP Service `grus-web` on port 3000.
  - Reads the existing admin token from Secret `grus-dev-secrets`, key `GRUS_ADMIN_TOKEN`, on the server side only (backend-for-frontend). The token never reaches the browser, and cross-site writes are rejected.
  - Restricted security context, `runAsNonRoot`, read-only root filesystem, no ServiceAccount token.
  - Requests equal limits: 192Mi memory, 100m CPU.
  - Probes on `/healthz`.
- Access is by port-forward only: `kubectl -n grus-dev port-forward svc/grus-web 3000:3000`. No LoadBalancer, no HTTPRoute, no Certificate, no DNAT.

**Commit type and scope:** the change ships as `feat(products)`. The code pull request is titled `feat(products): enable the grus web ui in grus-dev`.

## Out of scope and non-goals

- No login. Entra OIDC for the web UI is a later change.
- No public hostname, no HTTPRoute, no LoadBalancer, no DNAT.
- No change to the controller's RBAC, PVC, instance limit or pod spec beyond the image tag.
- No change to the proxy and lobby images or their tags. `release.yaml` does not reference them; preflight confirms which tags the controller uses at runtime.
- No NetworkPolicy change (see design).
- No other product, layer, foundation component or node is touched. The source bump only affects the `grus` GitRepository.

## Impact

- Capability `grus-web-ui` (see `specs/grus-web-ui/spec.md`).
- Files in the code pull request only: `foundation/sources/clusters/feather-core/sources/grus.yaml` and `products/grus/clusters/feather-core/grus-dev/release.yaml`.
- Namespace `grus-dev` gets two more objects, the `grus-web` Deployment and Service. The `products-dev` layer keeps `wait: true`, so a web pod that never becomes Ready holds back `products-dev`, as the prototype already can. The web pod requests 192Mi and 100m CPU and has no priority class, because the chart does not set one (see design).
- The controller pod restarts once, because its image tag changes. Its Recreate strategy and the adoption behaviour of the prototype keep the running game instances; the rollout check in the tasks confirms it.

## Rollback

1. **Disable the UI.** Set `web.enabled: false` in `release.yaml` and merge. Flux removes the `grus-web` Deployment and Service. The controller stays on the new tag.
2. **Revert the pull request.** Restores `ref.commit` `4f5d516` and tag `prototype-4f5d516` together, the state the prototype spec requires.
