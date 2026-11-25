## Context

The Grus web UI is a Nuxt application in `web/` of the Grus repository, shipped as its own image `grus-web`. It calls the controller HTTP API inside the cluster. The Grus chart already has an optional `web` component (`web.enabled`, off by default) that renders a Deployment `grus-web` and a ClusterIP Service on port 3000. The component was added at `4839dd3c77dfd90c436a4f0bceabcc7e1128f2d7`, the commit the prototype pin must move to.

Facts checked against the Grus repository (compare `4f5d516...4839dd3`: no Go file in the range; outside `web/` nine files changed):

- The controller Deployment, Role and RoleBinding templates are unchanged. `_helpers.tpl` only adds `web` defines; the controller selector labels (`app.kubernetes.io/name: grus`, `instance`) are unchanged, so the controller Deployment selector is not touched (the selector is immutable).
- The web pod has selector labels `app.kubernetes.io/name: grus-web` and `app.kubernetes.io/component: web`. The controller Service and the controller-specific NetworkPolicies do not select it.
- The web pod sets `automountServiceAccountToken: false` and reads the token through `secretKeyRef` (`NUXT_GRUS_TOKEN`) and the API URL `http://grus:8080`.
- `values.yaml` sets the web resources to 192Mi and 100m, requests equal limits, and the web service port to 3000.

Constraint: the prototype spec requires every image tag to be `prototype-<sha>` with the `<sha>` of the pinned `ref.commit`. Moving the pin therefore moves the controller tag too, even though the controller code is the same.

## Goals / Non-Goals

**Goals**

- A browser reaches the Grus UI in `grus-dev` through port-forward, shows pools, instances, the event stream and templates.
- The admin token stays on the server side.
- The change is one commit range in one directory plus one source pin.

**Non-Goals**

- Authentication, public access, a multi-replica UI, a separate layer.

## Decision drivers

- Keep the pin, the image tags and the chart in one state (the prototype invariant).
- Minimal blast radius: `products-dev` is the only layer that waits on this workload.
- No new secret: reuse `grus-dev-secrets`.
- Exposure stays at zero outside the cluster.

## Options and decisions

### 1. Enable the chart's web component

**Decision:** set `web.enabled: true` and the web image values in `release.yaml`.

**Alternatives considered:**

- *Separate Kustomize Deployment for the UI.* Rejected: it duplicates labels, probes and security settings the chart already renders, and it drifts from the chart.
- *Wait for an OCI chart release.* Rejected for the same reason as the prototype's source decision (design 1 of `add-grus-prototype`): no chart release process exists yet.

**Consequences:** the web UI follows the same pin as the controller. Any later chart change to the UI comes through the same pull request pattern.

### 2. Move the pin and both image tags in one pull request

**Decision:** `ref.commit` and the controller tag move to `4839dd3`, and the web tag is `prototype-4839dd3`.

**Alternatives considered:**

- *Keep the controller at `prototype-4f5d516`.* Rejected: it breaks the prototype invariant that every tag matches the pinned commit, and it leaves two code states in one release.
- *Track a branch for the UI.* Rejected: the prototype rejects moving refs.

**Consequences:** the controller pod restarts once. Its code is unchanged. Preflight confirms that `prototype-4839dd3` exists in Harbor for `grus` and `grus-web`.

### 3. Backend-for-frontend, token stays server-side

**Decision:** the web pod reads `GRUS_ADMIN_TOKEN` from `grus-dev-secrets` and calls the controller on the server. The browser talks only to the UI. Cross-site writes are rejected by the UI server.

**Alternatives considered:**

- *Token typed into the browser.* Rejected: the token would sit in browser storage and every request would carry it.
- *Anonymous UI with the controller open.* Rejected: the controller API accepts writes.

**Consequences:** anyone who can port-forward to the UI acts with the admin token. Access is limited to cluster operators, as the prototype already is. Entra OIDC is the later fix.

### 4. Access by port-forward only

**Decision:** ClusterIP Service only. Operators run `kubectl -n grus-dev port-forward svc/grus-web 3000:3000`, as the chart's NOTES.txt describes.

**Alternatives considered:**

- *HTTPRoute on a dev hostname.* Rejected: a route and a certificate for a UI without login exposes the admin token path to the internet.
- *LoadBalancer.* Rejected: the same reasons as the prototype's access decision.

**Consequences:** nothing changes outside the cluster. The UI cannot be shared with testers until login exists.

### 5. NetworkPolicy: no change

The policy set in `products/grus/clusters/feather-core/grus-dev/networkpolicy.yaml` already covers the web pod. Checked against the rendered chart:

| Path | Policy that allows it | Result |
|---|---|---|
| web to controller, TCP 8080 | `allow-namespace-egress` (`podSelector: {}` egress, every pod in the namespace) and `allow-controller-api-ingress` (selects `app.kubernetes.io/name: grus`, from any pod in the namespace, port 8080) | Allowed |
| web to DNS | `allow-dns-egress` | Allowed |
| web to the Kubernetes API | none; `automountServiceAccountToken: false` and the token comes from a Secret through the kubelet | Not needed |
| port-forward to web port 3000 | `default-deny-all` denies ingress to the web pod on 3000 for pod-to-pod traffic. Port-forward is served through the kubelet into the pod network namespace, not as pod-to-pod traffic, so the policy does not apply to it | Allowed by design, to re-check before enforcement |
| anything outside the cluster | no Service of type LoadBalancer, no route, no DNAT | Not reachable |

Enforcement is still audit only. The re-check of the port-forward path belongs to the decision on enforcing policies, not to this change.

### 6. Resources and security

The chart sets requests equal to limits (192Mi, 100m), `runAsNonRoot` with uid 65532, read-only root filesystem, dropped capabilities, RuntimeDefault seccomp. Probes on `/healthz`. The chart sets no priority class on the web pod, so it runs at the default priority. Preflight decides whether it needs one (see open questions).

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Pod stuck Pending or not Ready holds back `products-dev` (`wait: true`) | Requests are small; readiness has no external dependency; rollback by `web.enabled: false` |
| `grus-web` fails under read-only root filesystem (Nitro writes to a temp directory) | Verified in preflight by running the image with the same securityContext; acceptance checks the first probe |
| Controller pod restart drops an instance | Prototype behaviour: instances keep running, the controller adopts them; checked in the rollout task |
| Port-forward user holds the admin token power | Accepted for the dev prototype; limited to cluster operators; OIDC is the follow-up |
| Image tag missing in Harbor | Preflight checks `prototype-4839dd3` for `grus` and `grus-web` before the pull request |
| Runtime proxy and lobby images differ from the ones tested | Out of scope; preflight records the tags the controller uses |

## Migration Plan

1. Preflight (tasks 1.x), before the code pull request.
2. Code pull request merges to `main`.
3. Flux reconciles `grus` and `products-dev`. Verify read-only (tasks 4.x).
4. Acceptance check through port-forward (tasks 5.x).

**Rollback:** see the proposal. Disable the UI with `web.enabled: false`, or revert the pull request.

## Open Questions

- Which tag does the controller use for proxy and lobby pods at runtime, and does the Velocity Dockerfile change in `4f5d516...4839dd3` affect them? Answer in preflight; it does not block this change because `release.yaml` does not reference those images.
- Does `grus-web` need a priority class in the chart, or is `feather-low` for the controller enough? Decide in preflight from the rendered manifest.
