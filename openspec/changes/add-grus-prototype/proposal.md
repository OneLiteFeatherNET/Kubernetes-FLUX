## Why

OneLiteFeather runs Minecraft game servers on a CloudNet installation on `mc01` (Helsinki). CloudNet is being replaced by Grus, a small Go control plane that we build ourselves. Grus starts server instances as processes, containers or Kubernetes pods, keeps pools at their target size, scales them with player load and tells the Velocity proxies which servers exist.

Before Grus is built out, we need proof that it runs on `feather-core`: a controller, a Velocity proxy and Minestom lobbies, all as pods, started by Flux from Git like every other product. This change deploys a dev-only prototype of exactly that and nothing more.

## What Changes

- New namespace `grus-dev` in the existing `products-dev` layer. Pod Security Admission: `enforce: baseline`, `warn: restricted`, `audit: restricted`.
- A `GitRepository` source `grus` in `foundation/sources/clusters/feather-core/sources/`, pointing at `https://github.com/OneLiteFeatherNET/grus.git` and pinned to one commit with `ref.commit`. The pin moves together with the image tags, in the same pull request.
- A `HelmRelease` `grus` that installs the chart `./deploy/helm/grus` from that source with `reconcileStrategy: Revision`, `interval: 10m0s`, remediation `retries: 3`, fixed image tags `prototype-<sha>` from Harbor, `priorityClassName: feather-low` and small requests (64Mi memory and 50m CPU for the controller).
- The controller's state on a `RWO` PersistentVolumeClaim of 1Gi, with `strategy: Recreate`, so a rollout never waits on a volume that is still attached to the old pod.
- SOPS-encrypted secrets: the copied Harbor pull secret `harbor-pull` and `grus-dev-secrets.sops.env` with the admin token and the Velocity forwarding secret.
- A NetworkPolicy set in the `menura-dev` pattern. It is audit only today, as on every product namespace.
- A namespace-scoped `Role` (not a `ClusterRole`) for the controller ServiceAccount. It allows `pods`, `pods/log`, `pods/attach` and `secrets` with `create`, `get` and `delete`. The controller creates game pods at runtime; those pods are not Flux objects.
- Access is by `kubectl port-forward` only. No LoadBalancer service, no HTTPRoute, no DNAT rule.
- One new line in `products/layers/feather-core/dev/kustomization.yaml`.

**Commit type and scope:** the implementation ships as `feat(products)`, under the title `feat(products): add the grus prototype`.

## Out of scope and non-goals

- No production deployment, no `products` (prod) layer change, no public hostname.
- No LoadBalancer IP, BGP announcement or Minecraft port exposure. Players do not reach the prototype.
- No database and no S3 bucket. Grus uses its own SQLite file on the PVC.
- No Kubernetes operator or CRDs. The controller talks to the API directly.
- No Kyverno enforcement and no ResourceQuota for `grus-dev`.
- Enforcing NetworkPolicies is a non-goal until the cluster-wide policy switch is decided.

## Impact

- New capability `grus-prototype` (see `specs/grus-prototype/spec.md`).
- Files in the code pull request only: `foundation/sources/clusters/feather-core/sources/grus.yaml` and its entry in that directory's `kustomization.yaml`; `products/grus/clusters/feather-core/grus-dev/` (namespace, release, network policy, kustomization, secrets); one line in `products/layers/feather-core/dev/kustomization.yaml`.
- Layer `products-dev` gets one more workload set. It keeps `wait: true`, so a `grus-dev` workload that never becomes Ready holds back `products-dev` until it is fixed or suspended. Kept small on purpose (see design).
- Layer `products-dev` already depends on `services-development`; no change to the dependency graph and no layer is renamed or split.
- Runtime: one controller pod, one Velocity proxy pod and a small number of Minestom lobby pods, created at runtime by the controller in `grus-dev`. Scheduled on the xl workers with `feather-low`, which is the lowest priority class.

## Rollback

1. **Remove the layer line.** Flux prunes everything in `grus-dev` that the layer still owns. The source and namespace go with it.
2. **Suspend.** If the `grus` HelmRelease blocks `products-dev` and removing the line is too slow, a follow-up pull request sets `spec.suspend: true` on the HelmRelease. The layer then stops waiting on it.
3. Runtime pods created by the controller are not Flux objects. They are removed with the namespace, or by deleting them with `kubectl` if the namespace stays.
