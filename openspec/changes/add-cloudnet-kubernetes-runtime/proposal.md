## Why

CloudNet runs OneLiteFeather's Minecraft services on `mc01` (Helsinki, outside the cluster). Its CloudNet 4 node starts services as local processes or docker containers, not as Kubernetes pods. The `cloudnet-kubernetes` module (branch `feat/k8s-runtime-core`, CloudNet `4.0.0-RC17`) adds a `k8s-jvm` runtime: the node creates bare pods in one namespace and reads their state from an informer. That runtime needs cluster-side objects first: a namespace, a ServiceAccount with the RBAC the module asks for, a headless service for per-pod DNS, a staging bucket and a priority class.

This change adds those objects to `feather-core` and allows the new services to run on the Helsinki edge nodes. The rollout is hybrid. Services keep running on `mc01`; new ones run in the cluster under the same CloudNet node. The order is the team proxy first, then lobbies, then dynamic minigames. Static servers such as survival stay on `mc01` for now.

**Commit type and scope:** the implementation ships as `feat(cloudnet)`, under the title `feat(cloudnet): add the cloudnet kubernetes runtime resources`. The change adds one new product and no other type.

## What Changes

- New namespace `cloudnet` in the product directory `products/cloudnet`, wired into the layer `products` (`products/layers/feather-core/prod`).
- ServiceAccount `cloudnet-node`, a namespaced `Role` and a `RoleBinding`. The rules match `deploy/rbac.yaml` of the module. There is no `ClusterRole` and no CRD, because the module creates bare pods.
- A long-lived token `Secret` for that ServiceAccount. The node on `mc01` builds its kubeconfig from it. `mc01` can reach the API server; this was verified. No token and no kubeconfig content go into Git.
- Headless `Service` `cloudnet-services` from `deploy/headless-service.yaml`: `clusterIP: None`, `publishNotReadyAddresses: true`, selector `cloudnet.eu/component: service`, port 25565 named `game`.
- `ObjectBucketClaim` for the staging bucket that the node uploads prepared service directories to. It is a bucket of its own, separate from the existing `cloudnet-templates` bucket.
- `PriorityClass` `cloudnet-workload` with value `0` and `preemptionPolicy: Never`: below `feather-standard` (1000), above `feather-low` (-10), far below `feather-platform` (900000000). Game pods yield first under memory pressure. Pods of this product never use `feather-platform`.
- `LoadBalancer` `Service` for the team proxy, internal only. Its address is pinned with `lbipam.cilium.io/ips` to `10.200.90.12` in pool `lb-fr01`, verified live before implementation. It carries the label `bgp.onelite.feather/site: hel01` and uses `externalTrafficPolicy: Local`.
- `NetworkPolicy` objects in the repository's default-deny pattern. Cilium runs in policy audit mode, so they describe the target state and do not enforce yet.
- Placement rules for the runtime pods that the node creates at runtime: `topology.kubernetes.io/zone=hel01`, a toleration for `node-role.feather/edge=true:NoSchedule`, memory request equal to memory limit, and priority class `cloudnet-workload`. Each edge node has about 3.9 CPU and 5.2 GiB, so the request budget is bounded per node.

## Out of scope and non-goals

- **Static servers and their storage:** survival and other persistent servers stay on `mc01`. Their storage is a later change, Helsinki-local.
- **Routing:** mc-router and domain routing are separate optional modules and not part of this change.
- **Public exposure:** the team proxy is internal only (internal network and VPN). No public hostname, no DNAT, no HTTPRoute, no Certificate.
- **Enforcing NetworkPolicies:** the cluster-wide switch from audit to enforce is decided separately.
- **Router and BGP objects:** the fw01 side and the `hel01` BGP objects belong to `add-helsinki-anycast-ingress`.
- **The `mc01` node:** installing the module jar and writing its `config.json` happen outside this repository.
- **`cilium-bgp/` and `gateway/`:** the code pull request changes nothing under these paths.

## Dependencies

These are dependencies, not tasks of this change. Both are owned by `add-helsinki-anycast-ingress`.

1. **Edge-node scheduling:** a delta to `add-helsinki-anycast-ingress` that allows CloudNet workloads on the Helsinki edge nodes (`node-role.feather/edge=true:NoSchedule`). This change only sets the toleration and the node selector on its own pods.
2. **fw01 side for the new VIP:** the prefix list `HEL-INGRESS-VIPS` and the alias `HEL_INGRESS_VIPS` on fw01 must cover the address of the team proxy. It must be in place before the team proxy is reachable from Helsinki.

## Impact

- **New capability** `cloudnet-kubernetes-runtime` (see `specs/cloudnet-kubernetes-runtime/spec.md`).
- **Files in the code pull request only:** `products/cloudnet/base/` (namespace, RBAC, headless service, priority class), `products/cloudnet/clusters/feather-core/cloudnet/` (token secret, bucket claim, team proxy service, network policies, kustomization) and one line in `products/layers/feather-core/prod/kustomization.yaml`.
- **Layer `products`:** gets one more workload set. Whether a pending load balancer or a blocked pod can hold the layer back is an open question (see design).
- **Runtime:** no pod runs until the node on `mc01` is pointed at the runtime. The namespace, RBAC, service and bucket exist before that.
- **Cluster capacity:** the pods target three edge nodes. Memory requests equal limits and the priority is below `feather-platform`, so Cilium and Envoy are not starved.

## Rollback

1. **Remove the layer line.** Flux prunes the Flux-managed objects of the product: namespace, RBAC, service, bucket claim and the load balancer service.
2. **Stop the node.** The CloudNet node on `mc01` stops creating pods. Pods it already created are removed by the node or with `kubectl -n cloudnet delete pod -l cloudnet.eu/task`. Removing the namespace cleans up the rest.
3. **Revoke the token.** Deleting the token `Secret` invalidates the kubeconfig on `mc01`.

The `PriorityClass` can be pruned only when no pod uses it. Remove the pods first.
