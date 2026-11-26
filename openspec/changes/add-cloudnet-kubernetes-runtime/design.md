## Context

The `cloudnet-kubernetes` module lets a CloudNet 4 node start services as pods. Its design (README of the module) keeps CloudNet as the control plane: no CRDs, no operator. The node creates bare pods, sets `spec.hostname` and `spec.subdomain` against one headless service, and reads pod state from an informer. The service directory travels through an S3 staging bucket. The connection key ships as a Secret. The module's `deploy/rbac.yaml` is namespaced throughout; there is no `ClusterRole`.

The cluster side must provide: the namespace and RBAC, the headless service, a bucket, a priority class, and a way for pods to reach the team proxy. The node runs outside the cluster on `mc01` and authenticates with a token.

Two repository facts constrain the design:

- Cilium LB-IPAM pool `lb-fr01` covers `10.200.90.1` to `10.200.90.15`. `.1` is the Envoy gateway VIP, `.3` to `.5` are database VIPs (`add-helsinki-anycast-ingress`). Address `.11` is claimed by the pending proposal `expose-grus-proxy-load-balancer`.
- The priority tiers in `foundation/platform/clusters/feather-core/priorityclasses/priorityclasses.yaml` are `feather-critical` (1000000000), `feather-platform` (900000000), `feather-high` (100000), `feather-standard` (1000, global default) and `feather-low` (-10).

## Goals / Non-Goals

**Goals**

- The CloudNet node on `mc01` can create, watch, log to and delete its pods in namespace `cloudnet`, and nothing beyond that namespace.
- New services run on the Helsinki edge nodes and never displace Cilium or Envoy.
- The team proxy has a stable internal address that Helsinki clients can reach.

**Non-Goals**

- Static servers, persistent storage, domain routing, public exposure (see proposal).

## Decision drivers

- Least privilege: the node holds rights in one namespace only.
- Hybrid operation: `mc01` keeps its services; nothing is migrated in one step.
- Platform first: edge nodes host Cilium and Envoy, which must keep running under pressure.
- Reuse the module's own RBAC and service shape, so the module's documentation stays valid.
- Nothing secret in Git.

## Decisions

### 1. Hybrid placement under the same CloudNet node

**Decision:** the CloudNet node on `mc01` stays the single control plane. Services it starts on `mc01` keep running there. Services with the Kubernetes runtime (`k8s-jvm`) run as pods in `cloudnet`.

**Alternatives considered:**

- *Move all services to the cluster at once.* Rejected: static servers need persistent storage, which is out of scope, and the team proxy would have to move with them.
- *A second CloudNet node in the cluster.* Rejected: two nodes would have to agree on ownership, and the module's split-brain protection is for nodes sharing one namespace, not for two control planes.

**Consequences:** the node on `mc01` must reach the cluster API and the staging bucket. Services on `mc01` and in the cluster need a route to each other for the proxy, which the network policies must allow.

### 2. Namespaced RBAC copied from the module

**Decision:** ServiceAccount `cloudnet-node`, a `Role` `cloudnet-service-runtime` and a `RoleBinding` with the rules of `deploy/rbac.yaml`: pods (get, list, watch, create, delete, deletecollection), pods/log (get, list), pods/attach (create, get), services (get, list, watch, create), configmaps (get, list, watch, create, update, delete), secrets (get, list, watch, create, delete), events (create, patch), and leases in `coordination.k8s.io` (get, list, watch, create, update). `pods/exec` and `pods/portforward` are omitted, as in the module.

**Alternatives considered:**

- *A broader `ClusterRole`.* Rejected: the module is designed to need no cluster scope.
- *Adding `pods/exec` now, for the web UI.* Rejected: the module does not need it yet, and the web UI is a later change.

**Consequences:** `pods/attach` is a separate subresource. Granting `pods/exec` would not cover console input, so the rule stays. Future module versions that need more rights change this file in their own proposal.

### 3. Long-lived token Secret for the node

**Decision:** a `Secret` of type `kubernetes.io/service-account-token` annotated for `cloudnet-node`. The cluster fills in the token. The kubeconfig for `mc01` is assembled on `mc01` out of band. The manifest in Git contains no token value.

**Alternatives considered:**

- *A short-lived `TokenRequest` refreshed by a job.* Rejected for now: `mc01` has no in-cluster job to refresh it, and the node would need a refresh mechanism that the module does not have.
- *A client certificate.* Rejected: certificates cannot be revoked short of rotating the CA.

**Consequences:** the token is valid until the Secret is deleted. Rotation is: delete and recreate the Secret, then rebuild the kubeconfig on `mc01`. The rollout notes must say so. The kubeconfig is never committed.

### 4. Headless service from the module, unchanged

**Decision:** `cloudnet-services` with `clusterIP: None`, `publishNotReadyAddresses: true`, selector `cloudnet.eu/component: service`, port 25565 named `game`, copied from `deploy/headless-service.yaml`.

**Consequences:** one service for all runtime pods, with per-pod DNS names `<pod>.cloudnet-services.cloudnet.svc.cluster.local`. The module reads that name back through its node-service DNS setting; the value must match the namespace (open question 6).

### 5. Staging bucket as an ObjectBucketClaim in the namespace

**Decision:** an `ObjectBucketClaim` in `cloudnet` with `storageClassName: ceph-bucket-fr01`, following `products/blackhole/clusters/feather-core/blackhole/evidence-bucket.yaml`. Rook writes a Secret and ConfigMap into `cloudnet`. The bucket is separate from `cloudnet-templates` (owned by the existing Ceph user `cloudnet`), which holds CloudNet templates and must not receive per-service staging objects.

**Alternatives considered:**

- *Reuse `cloudnet-templates`.* Rejected: staging objects are short-lived and would mix with templates the team edits by hand.
- *A `CephObjectStoreUser` secret.* The module's example names this shape (`accessKey`, `secretKey`), so it fits the module's own `config.json`. Chosen only if the bucket owner question (open question 3) requires a new user.

**Consequences:** the OBC Secret uses `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, not the keys the module reads. The credentials must be transferred to `mc01`'s `config.json` by hand or through a SOPS file (open question 4).

### 6. Priority below the platform tier

**Decision:** `PriorityClass` `cloudnet-workload` with `preemptionPolicy: PreemptLowerPriority` and a value that sits below `feather-platform` (900000000). Pods of this product never use `feather-platform`. The concrete value is open question 1, with a proposal in the code pull request.

**Alternatives considered:**

- *`feather-low` (-10).* Rejected: it is the first class evicted under pressure, and a team proxy must not be evicted before best-effort apps.
- *`feather-platform`.* Rejected by the brief: Envoy and databases use it, and a CloudNet pod must never share that tier.

**Consequences:** under pressure on an edge node, Cilium (system) and Envoy (`feather-platform`) keep their place. CloudNet pods are evicted before them.

### 7. Placement on the edge nodes

**Decision:** pods created by the node carry the node selector `topology.kubernetes.io/zone: hel01` and the toleration `node-role.feather/edge=true:NoSchedule`. Memory requests equal memory limits for every container. The node sets these fields in the pod spec it creates, so the layer does not template pods. The namespace's Pod Security labels are `enforce: baseline` and `warn`/`audit: restricted`, as for `grus-dev`, because game images may run as root.

**Alternatives considered:**

- *Any worker node.* Rejected: the brief names the edge nodes; the fr01 workers carry the storage and database load.

**Consequences:** the toleration is a request for the edge taint only. The edge-scheduling delta in `add-helsinki-anycast-ingress` decides whether these pods may run there. Edge nodes have about 3.9 CPU and 5.2 GiB each, so the total memory limit of all runtime pods is capped by three nodes minus Cilium and Envoy.

### 8. Team proxy as an internal LoadBalancer on the Helsinki site

**Decision:** `Service` `cloudnet-team-proxy` of type `LoadBalancer`, port 25565, selecting the team proxy pods. It is pinned with `lbipam.cilium.io/ips` to one free address of `lb-fr01` (open question 2). It carries `bgp.onelite.feather/site: hel01`, so the `hel01-ingress` advertisement announces it from the edge nodes. It uses `externalTrafficPolicy: Local`, so the address is announced only from a node that runs a proxy pod, and the client source address is kept.

**Alternatives considered:**

- *`externalTrafficPolicy: Cluster`.* Rejected: it hides the client address behind a node and can route to a node without a pod.
- *HTTPRoute or DNAT.* Rejected: the team proxy speaks Minecraft TCP, and public exposure is out of scope.

**Consequences:** the address is reachable from Helsinki and the internal network only once the fw01 side (dependency 2) is in place. With `Local`, the address is withdrawn when no proxy pod is on a node, which the team must expect during a rollout.

### 9. NetworkPolicy in the repository default-deny pattern

**Decision:** a default-deny policy for both ingress and egress in `cloudnet`, then explicit allows: DNS egress to `kube-dns`; ingress TCP 25565 from the internal ranges to the team proxy; ingress among pods of the namespace on game ports; egress to Ceph RGW for the staging bucket; egress to `mc01` on the ports the node and wrappers use (open question 5). Private ranges are excluded from general HTTPS egress, as in `menura-dev`.

**Consequences:** Cilium runs in policy audit mode, so these policies are recorded but not enforced. They describe the target state. Enforcement is decided cluster-wide.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| The `products` layer waits for a pending load balancer address and holds back other products | Verify the address is announced before the layer change; the layer-removal rollback in the proposal |
| An edge node runs out of memory and Cilium or Envoy are starved | Requests equal limits, priority below `feather-platform`, toleration only for the edge taint |
| The node token leaks | Token lives only in a Secret and on `mc01`; deleting the Secret revokes it; never committed |
| The address `.11` or another reserved address is taken by a parallel change | Choose from the free list live in preflight; record the address in the code pull request |
| The pod spec created by the node is not PSA-compliant | `baseline` is enforced; `restricted` violations are warnings to be fixed in the module |
| `externalTrafficPolicy: Local` withdraws the address during a rollout | Keep at least one proxy pod on an edge node at all times; a rollout notes item |

## Migration Plan

1. Preflight (tasks 1.x): choose the address, confirm the module commit, confirm the bucket owner, confirm edge capacity.
2. The proposal pull request merges to `main`.
3. The code pull request is opened from a fresh `main`.
4. Flux reconciles `products`. Verify read-only (tasks 4.x).
5. Operator: create the token Secret, build the kubeconfig on `mc01`, add the runtime to the node, start one test service (outside this repository).

**Rollback:** see the proposal.

## Open Questions

1. **PriorityClass value.** Which value below 900000000 is used? Proposed: a value between `feather-standard` (1000) and `feather-low` (-10), so pods preempt nothing above them and are not evicted first. The exact number is chosen in the code pull request and recorded there.
2. **Team proxy address.** Which free address of `lb-fr01` is pinned? Candidates from the brief: `.2`, `.6`, `.9`, `.12` to `.15`. Not `.11` (reserved by `expose-grus-proxy-load-balancer`), not `.1`, `.3` to `.5`. Also check the address reserved by `harden-observability-coverage` (task 1.2, not yet chosen). Verify with `kubectl get svc -A -o wide` before implementing.
3. **Bucket owner.** Does the staging bucket get a dedicated `CephObjectStoreUser`, or does the existing user `cloudnet` (which owns `cloudnet-templates`) own it too? The brief does not decide. Proposed: a dedicated user, so templates and staging stay separate.
4. **Credential transfer.** The OBC Secret uses `AWS_*` keys, but the module reads `accessKey` and `secretKey` from its own `config.json`. How do the values reach `mc01` without plaintext in Git: a SOPS file in this repository, or a manual copy?
5. **Ports between the node, wrappers and `mc01`.** Which `mc01` address and port range must pods reach, and which ports must `mc01` accept from the pods? The Grus proposal uses `10.200.2.35` on ports 30000 to 30100 for its agent. The module's ports are not in the brief and must be read from its configuration before the egress rule is written.
6. **Node-service DNS.** Does the module's `nodeServiceDns` setting match `cloudnet-services.cloudnet.svc.cluster.local`? Check against the module's `config.json` defaults.
7. **Source range for the team proxy.** Which VPN and internal ranges may reach the team proxy? Same question as task 1.4 of `expose-grus-proxy-load-balancer`; the answer is recorded in the code pull request.
8. **Layer wait behaviour.** The `products` layer waits for its resources. Does a pending load balancer address or a blocked runtime pod hold back other products? Proposed: keep `wait` as in the layer, and verify with the rollout tasks before merge.
9. **Pod sizing.** How much memory and CPU does each runtime pod get? Not in the brief. Set in the node's configuration, and bounded by the edge capacity of about 3.9 CPU and 5.2 GiB per node.
10. **Edge scheduling delta.** Does `add-helsinki-anycast-ingress` accept the edge toleration for these pods, and does its `hel01-ingress` advertisement announce a service labelled `bgp.onelite.feather/site: hel01` in namespace `cloudnet`? Owned by that change; confirm before the code pull request is opened.
