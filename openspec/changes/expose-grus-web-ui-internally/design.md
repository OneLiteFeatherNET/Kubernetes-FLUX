## Context

The Grus web UI (`grus-web`, ClusterIP, port 3000, namespace `grus-dev`) is running since #723. It is reached only through port-forward. The shared internal gateway `eg` in namespace `envoy` already publishes menura-dev with an HTTPRoute, a step-ca Certificate and a listener `certificateRefs` entry. The UI has no login. Its server-side Nitro layer holds the admin token and rejects cross-site writes.

The decision drivers, from the maintainer:

- The UI must be reachable by name from the internal network, the way menura-dev is.
- The gateway does not add login for this prototype. The maintainer accepts that anyone on the internal network can control grus-dev.
- The UI keeps its own cross-site write guard and its server-side token.
- Blast radius stays small: only `grus-web` is opened to Envoy; the controller API, game pods and proxy are not.
- Nothing is exposed outside the internal network.

## Goals / Non-Goals

**Goals**

- `https://grus-dev.apps.onelite.feather` loads the UI from the internal network through the internal gateway.
- Live event and instance-log streams are not cut by the gateway.
- The change is one directory plus two shared files, mirroring menura-dev.

**Non-Goals**

- Login at the gateway, public access, a second replica, a separate layer.

## Options and decisions

### 1. Port-forward only (status quo)

**Decision:** not chosen.

**Consequences:** nothing changes, but reviewers need a local kubectl context and a port-forward session for every review. The maintainer asked for a stable name.

### 2. Internal route without authentication

**Decision:** chosen by the maintainer. HTTPRoute `grus-dev` on the internal gateway, one certificate, one network policy entry for `grus-web` only. No authentication at the gateway.

**Consequences:**

- Anyone on the internal network (or VPN) who resolves `grus-dev.apps.onelite.feather` can pause, scale or stop instances and read the event stream. The admin token never reaches the browser, but the UI acts with it on behalf of any caller.
- The UI's cross-site write guard stops a web page in another origin from sending writes in a visitor's browser. It does not authenticate a direct client on the network.
- No user identity is recorded for actions. The controller event stream shows the UI as the actor.
- The route is a small change to revert, and it keeps the same hostname when login is added later.

### 3. Envoy SecurityPolicy with OIDC (Entra)

**Decision:** not now. Recorded as the follow-up for the gateway.

**Alternatives considered:** this option gives per-user identity and removes the accepted risk. It needs an Entra application registration, a client secret in SOPS, the gateway's OIDC filter and a callback route. That is a separate proposal and a separate decision on the identity provider.

**Consequences:** the follow-up is needed before the UI is shared beyond the operators, or before grus-dev leaves the dev prototype.

### 4. Basic auth at the gateway

**Decision:** rejected.

**Alternatives considered:** a shared password in front of the UI. It protects against casual access but not against anyone who has the password, and it adds a second secret to rotate without a user identity.

**Consequences:** none; the option is not built.

## Route and policy design

| Path | Rule | Result |
|---|---|---|
| `/api/grus/v1/events`, `/api/grus/v1/instances` (prefix) | Route rule 1, timeouts `0s` | Live streams are not cut by the 15 s default |
| `/` (prefix) | Route rule 2, default timeouts | Pages and assets |
| Envoy proxy pods to `grus-web` on TCP 3000 | `allow-envoy-gateway-ingress` | Allowed |
| Envoy proxy pods to the controller (8080), game pods or proxy | No policy | Not reachable from the gateway |
| Outside the cluster | No LoadBalancer, no DNAT, no public hostname | Not reachable |

The hostname resolves to `10.200.90.1` through the internal wildcard. No DNS change is part of this change.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Anyone on the internal network controls grus-dev | Accepted by the maintainer for the dev prototype. Recorded in the proposal. OIDC at the gateway is the follow-up (option 3) |
| The write guard rejects the gateway Host or Origin | Preflight checks it with the hostname before the code pull request (task 1.3) |
| Event stream idles past the gateway's stream idle timeout | Route timeouts are `0s`. Preflight records the gateway default; acceptance checks a stream open past 15 s and past its idle time |
| Certificate not issued, listener shows an unresolved reference | Same shape as menura-dev; verified after merge |
| Port-forward or the Service changes | Service unchanged; port-forward keeps working |

## Migration Plan

1. Proposal pull request merges.
2. Code pull request merges. Flux reconciles `certificates`, `networking` and `products-dev`.
3. Verify the certificate, the route and the browser access from the internal network.

**Rollback:** revert the code pull request.

## Open Questions

- Does the gateway's default stream idle timeout end an idle event stream before the UI's own keep-alive? Answer in preflight (task 1.5). If it does, a `BackendTrafficPolicy` like quelea's is a follow-up, not part of this change.
