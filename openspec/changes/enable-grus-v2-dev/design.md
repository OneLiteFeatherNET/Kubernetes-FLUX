## Context

`grus-dev` is a dev-only prototype (`add-grus-prototype`, `enable-grus-web-ui`, `expose-grus-web-ui-internally`). Grus v2 adds three things that touch the cluster: S3 as the template backend, a CRD watch for declarations, and an HTTP agent protocol for machines outside Kubernetes. The first external machine is `mc01`, the Helsinki host where CloudNet runs. Instances there are plain processes started by a Grus agent. The agent talks to the controller, and the Velocity proxy pods in `grus-dev` talk to the instances.

Decision drivers:

- Smallest exposure: the internal network may reach agent endpoints only, never the admin API.
- No secret in Git in plain text; the cluster holds a hash of the agent token.
- No foundation change for the bucket.
- Same shapes as existing objects (menura-dev and grus-dev hostnames, other bucket claims).
- Reversible by reverting one pull request.

## Findings

- Existing claims without `bucketOwner`: `services/development/clusters/feather-core/ingot-loadtest/bucket.yaml` (comment: Rook creates a user for that claim alone, which allows a per-user quota) and `foundation/storage/clusters/feather-core/rook-fr01/buckets/feather-core-cluster-pg-backup.yaml`. Claims with `bucketOwner` (for example `vulpes-assets-dev`) point at a `CephObjectStoreUser` defined in `foundation/storage/`. Without `bucketOwner` Rook creates a user per bucket and writes the Secret (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) and the ConfigMap (`BUCKET_NAME`, `BUCKET_HOST`, `BUCKET_PORT`) with the claim's name into the claim's namespace. That is why `grus-templates-dev` needs no foundation change.
- The RGW service `rook-ceph-rgw-feather-s3` in `rook-ceph-fr01` listens on port 80 (`gateway.port: 80`, frontend `beast port=80`). The etcd-backup policy in this repository allows both 80 and 8080 to the RGW pods (`app: rook-ceph-rgw`). The new policy opens only the pod port; task 1.5 confirms it is 80.
- `grus-dev-api.apps.onelite.feather` resolves to `10.200.90.1` (the gateway) today.
- The envoy proxy pods carry `app.kubernetes.io/name: envoy` and `app.kubernetes.io/component: proxy` in namespace `envoy` (used by `allow-envoy-gateway-ingress`).
- `networkpolicy.yaml` documents the policies of `grus-dev` as audit only. They document the intended paths; they do not block traffic until the cluster enforces them. The new policies follow the same shape so they hold when enforcement arrives.

## Options and decisions

### 1. Hostname for the agent API

**Options:** (a) a second hostname `grus-dev-api` with an allow-list route; (b) the existing `grus-dev` hostname with extra path rules; (c) the cluster-internal Service only (agent reaches it through a tunnel).

**Decision:** (a).

**Consequences:** the web UI hostname has no login. Adding agent paths to it would mix the unauthenticated UI with machine endpoints, and the UI proxies `/api/grus/` to the admin API with the admin token. A separate hostname keeps one route, one certificate and one set of allowed paths for the agent link. It costs one Certificate and one listener entry. Option (c) needs a VPN or tunnel to `mc01` that does not exist.

### 2. What the route exposes

**Decision:** an allow list. `/v1/agents` and `/v1/self` (prefix), `GET /v1/templates` (prefix), `/healthz` (exact). All other paths (admin API, pools, instances, events, hosts) are not matched and return 404 at the gateway.

**Alternatives considered:** exposing `/v1` and relying on token scopes. Rejected: the admin token and the agent tokens would then guard the same surface from the internal network.

**Consequences:**

- The agent and the instances on `mc01` can do their jobs and nothing else. Operators use the web UI or `kubectl port-forward` for the admin API.
- Method restriction on templates (GET only) keeps uploads off this route.
- Authentication stays in the controller: `/v1/agents/{host}` needs a token with scope `agent:<host>`; `/v1/self` needs the per-instance token. The gateway adds TLS and the path filter, not identity.
- The long-poll `GET /v1/agents/{host}/desired?wait=30s` holds a request for about 25 seconds. The gateway default of 15 seconds would cut it, so request and backend timeouts are 60s on that rule.

### 3. Agent authentication material

**Decision:** the controller reads `GRUS_AGENT_TOKENS` (`mc01=<sha256 hex>`) from the SOPS-encrypted `grus-dev-secrets`. Only the hash is in the cluster. The plain token is created by the operator, stored in a file on `mc01` (mode 0600, user `cloud`) and never committed.

**Alternatives considered:** a plain token in the secret (rejected: a cluster secret read would yield a credential that works from the network); mTLS client certificates (rejected for now: needs a CA flow for a host outside the cluster).

**Consequences:** token rotation is a hash change in Git plus a file change on `mc01`. Instances on `mc01` use per-instance tokens on `/v1/self`, issued by the controller in the desired state, so a leaked instance token is scoped to that instance.

### 4. Template bucket

**Options:** (a) claim without `bucketOwner`; (b) claim with `bucketOwner` and a new `CephObjectStoreUser` in `foundation/storage`; (c) keep templates on the volume.

**Decision:** (a).

**Consequences:** Rook creates a user for this claim. No foundation pull request and no shared user, and a quota could be added later (as in `ingot-loadtest`). The bucket and user are removed when the claim is removed if the StorageClass reclaim policy is `Delete`; task 1.6 records the policy. Option (b) is the pattern for buckets shared by several consumers, which this is not. Option (c) means agents cannot download the same templates that survive a controller volume loss.

The chart reads the credentials from the Secret and the endpoint from the ConfigMap (`BUCKET_HOST`, `BUCKET_PORT`), over plain http because the traffic stays inside the cluster, like the other products.

### 5. Network policies

**Decision:** three additions to the default-deny set.

| Policy | From | To | Port |
|---|---|---|---|
| ingress to the controller | envoy proxy pods in `envoy` | pods `app.kubernetes.io/name: grus` | TCP 8080 |
| egress to object storage | pods `app.kubernetes.io/name: grus` | pods `app: rook-ceph-rgw` in `rook-ceph-fr01` | TCP 80 |
| egress to the agent host | pods `grus.olf/role: proxy` | `10.200.2.35/32` (`mc01`) | TCP 30000-30100 (`endPort`) |

**Alternatives considered:** `allow-controller-api-ingress` already admits any pod of the namespace on 8080; it does not admit the gateway. A broader egress to `10.200.2.0/24` was rejected: the proxy needs one host and one port range.

**Consequences:** the controller API is reachable from the gateway on 8080, but the gateway route limits what an external client can ask for. The policies are audit only today. The egress to `mc01` is a documented, narrow intent, and the 2026-11-25 reachability test (TCP to port 22 over IPv4 and IPv6 from the proxy pod) shows the path exists. The ranges are the agent's `--port-range 30000-30100`; if that flag changes, the policy changes with it.

### 6. Pools and example resources

**Options:** (a) pools created at runtime through the API or CLI; (b) example Pool, Group and Environment CRs in Git.

**Decision:** (a). No custom resources in Git by default.

**Consequences:** objects with source `kubernetes` are read-only through the REST API, so a CR in Git cannot be changed for a quick demo without a pull request. The demo pools change often and depend on the agent being registered. The CRD path is exercised by one throw-away `kubectl apply` of a Pool during verification (task 4.7), which is deleted afterwards. If the maintainer wants a standing example, it is a follow-up with its own scope.

### 7. CRD watch

**Decision:** `crds.watch: true`. The chart's namespaced Role gains get/list/watch on `environments`, `groups` and `pools` and get/update/patch on `pools/status`. No ClusterRole.

**Consequences:** the controller sees only `grus-dev`. It needs the CRDs from `add-grus-crds`; without them the watch fails. Task 1.3 checks that and what the controller does when the CRDs are missing.

## Context outside this repository

`mc01` runs the agent as user `cloud` in `~/grus`, started by a systemd unit or a screen session, next to CloudNet, which is not changed. The agent advertises the `mc01` address and picks instance ports from 30000-30100. It reaches the controller at `https://grus-dev-api.apps.onelite.feather` and trusts the internal CA through its `--ca-file` flag. None of this is a task of this repository.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| New hostname exposes agent endpoints to the whole internal network | Allow-list route; token auth in the controller; hash only in the cluster; admin API not routed |
| Agent token or hash leaks | Rotate: new hash in the secret, new file on `mc01` |
| Gateway cuts the 25s long-poll | 60s timeouts on that rule; verify a poll runs past 15s |
| Controller or proxy start before the CRDs or bucket exist | `add-grus-crds` merges first; the Rook claim binds in seconds; the chart is retried by Flux |
| Policies are audit only, so they prove nothing about reachability | The reachability test is recorded in preflight; the policies document the intent |
| Pin and tags out of step | Tags and pin change together in one pull request; preflight checks the tag exists |
| Bucket and its data deleted on claim removal | Reclaim policy recorded before the rollback; templates are rebuildable from their sources |
| Agent port range in the policy diverges from the agent flag | Both are 30000-30100; the spec ties them together |

## Migration Plan

1. `add-grus-crds` code pull request is merged first.
2. The grus v2 image tag exists in Harbor; the placeholders are filled.
3. The operator creates the plain agent token, stores it on `mc01` and puts its SHA-256 into `grus-dev-secrets.sops.env`.
4. This change's code pull request merges. Flux creates the bucket, certificate, route and policies, and upgrades the release.
5. The operator starts the agent on `mc01`.

**Rollback:** revert the code pull request; see the proposal.

## Open Questions

- Does the controller start cleanly when the CRDs are missing (task 1.3)?
- Is the RGW pod port 80 (task 1.5) and the `ceph-bucket-fr01` reclaim policy `Delete` (task 1.6)?
- Does the agent's TLS trust of the step-ca root work with the `--ca-file` flag as designed?
