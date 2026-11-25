## Context

Grus v2 keeps its declarations (Environment, Group, Pool) as namespaced custom resources and in its REST API. The CRD manifests live in the grus repository under `deploy/crds/`, next to the chart that consumes them. `feather-core` deploys Grus from the GitRepository `grus` pinned to one commit, with the chart built from `./deploy/helm/grus` at that commit.

A CRD is a cluster-scoped, long-lived object. Deleting it deletes every custom resource of that kind. Helm does not upgrade or delete CRDs from a chart's `crds/` directory.

Decision drivers:

- One source of truth for the schema: the grus repository, versioned with the controller that reads it.
- Schema changes reach the cluster (Helm would not do that).
- A removed manifest must never delete user data (pools).
- Smallest cluster-scoped footprint: CRDs only.
- Same review path as the chart: one pin, one pull request.

## Findings: how the products layer is applied

- `clusters/feather-core/products-dev.yaml` is a Flux `Kustomization` in `flux-system` for path `./products/layers/feather-core/dev`, `prune: true`, `wait: true`, `deletionPolicy: Orphan`, SOPS decryption through `sops-age`. It sets no `serviceAccountName`.
- Without `serviceAccountName`, the kustomize-controller applies as its own service account, which is bound to `cluster-admin` by the Flux bootstrap. Only the tenant layer `tenant-isuku` impersonates a service account (`isuku-flux`). So `products-dev` may create CRDs and any other cluster-scoped object.
- The layer is therefore allowed to apply a Flux `Kustomization` object, and that object (also applied as kustomize-controller) may create CRDs. No RBAC change is needed.
- The foundation layers already own cluster-scoped objects (operators, CRDs through Helm `crds: CreateReplace`, for example kube-prometheus-stack). This change adds product-owned CRDs, which is new for `products-dev`, and is why they are kept in one separate, non-pruning Kustomization.
- `products-dev` uses `wait: true`. It waits for the new Kustomization to become Ready, so a failing CRD apply blocks the dev layer, the same as a failing HelmRelease does today.

## Options and decisions

### 1. Flux Kustomization on the `grus` GitRepository (`./deploy/crds`, `prune: false`)

**Decision:** chosen.

A second Flux Kustomization object in `flux-system`, source GitRepository `grus`, path `./deploy/crds`. It lives in `products/grus/clusters/feather-core/grus-dev-crds/` and is listed in the dev layer.

**Consequences:**

- The schema comes from the same pinned commit as the chart. One pin bump moves both.
- Schema changes are applied on every pin bump (server-side apply), unlike Helm.
- `prune: false` and `deletionPolicy: Orphan` mean a removed manifest or a removed Kustomization never deletes a CRD or a pool.
- The CRD directory must contain only CRDs. A stray cluster-scoped object in the grus repository would be applied with cluster-admin rights. This is checked in the preflight and again at each pin bump (see Risks).
- The Kustomization object needs an explicit `metadata.namespace: flux-system`. It is not placed under the `grus-dev` directory, because that overlay sets `namespace: grus-dev` for everything it contains.

### 2. Copy the CRD YAML into this repository

**Decision:** rejected.

**Alternatives considered:** a directory with three files under `products/grus/`, applied by the dev layer. It needs no source access and shows the schema in review.

**Consequences:** two copies drift. Every schema change in grus needs a second pull request here, and the pin and the copy can disagree. The CRD shape is part of the controller contract, so it belongs with the controller.

### 3. Helm `crds/` directory of the grus chart

**Decision:** rejected.

**Alternatives considered:** the chart ships the CRDs; Flux's HelmRelease `install.crds` / `upgrade.crds` policy can create or replace them (`CreateReplace` exists for HelmReleases).

**Consequences:** with the default policy Helm installs `crds/` once and never upgrades it, and `helm uninstall` leaves them. With `upgrade.crds: CreateReplace` upgrades work, but removing the release then risks the chart's own lifecycle owning the CRDs. Also the chart creates its Role in the same release, and a CRD belongs to the cluster, not to one release. Option 1 is the same mechanism with a clear owner.

### 4. Foundation layer (cluster plumbing)

**Decision:** rejected.

**Consequences:** `foundation/` holds operators and platform CRDs. The Grus CRDs are product CRDs of one dev prototype. Putting them there would make the rollback a foundation change.

## Resulting objects

| Object | Scope | Owner | Pruned |
|---|---|---|---|
| `crd/environments.grus.onelitefeather.net` | cluster | Kustomization `grus-crds` | no |
| `crd/groups.grus.onelitefeather.net` | cluster | Kustomization `grus-crds` | no |
| `crd/pools.grus.onelitefeather.net` | cluster | Kustomization `grus-crds` | no |
| `Kustomization/grus-crds` (`flux-system`) | namespaced | `products-dev` | yes (the object only) |

No ClusterRole, no ClusterRoleBinding, no webhook, no conversion webhook. Access to the kinds is granted per namespace by the chart's Role in `enable-grus-v2-dev`.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Deleting a CRD deletes all custom resources of that kind | `prune: false`, `deletionPolicy: Orphan`; manual deletion documented in the proposal |
| `deploy/crds/` gains a non-CRD cluster-scoped object | Preflight lists the kinds at the pinned commit; `kubectl kustomize` of the path is read before each pin bump |
| Pin bump repackages the chart with the old image tag | Preflight renders the chart at the new commit with the current values; if it fails, the code pull requests of both changes are merged in one reconcile window |
| Path `./deploy/crds` does not exist at the pinned commit | Preflight `git ls-tree` at the commit; `wait: true` reports a failing Kustomization |
| A breaking schema change (v1alpha1) invalidates existing pools | v1alpha1 is allowed to change; pools are runtime data in dev. A storage-version change needs its own proposal |
| `products-dev` blocks if the Kustomization fails | Same exposure as a failing HelmRelease; fix forward or revert |

## Migration Plan

1. The grus repository merges `deploy/crds/` (not part of this change).
2. This proposal pull request merges.
3. The code pull request moves the pin and adds the Kustomization. Flux applies the CRDs.
4. `enable-grus-v2-dev` follows.

**Rollback:** see the proposal. CRDs stay; deletion is manual.

## Open Questions

- Does the new chart at the pinned commit render and run with the current prototype image tag (task 1.4)? If not, the two code pull requests land together.
