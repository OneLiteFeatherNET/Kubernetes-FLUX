## Why

Grus v2 declares its environments, groups and pools as Kubernetes custom resources. The controller watches them and stores them next to what the REST API creates. `feather-core` has no such resource types today, so the controller has nothing to watch.

BLUF: this change adds the three namespaced CustomResourceDefinitions `environments`, `groups` and `pools` in the API group `grus.onelitefeather.net` (version `v1alpha1`) to `feather-core`. The definitions are owned by the grus repository (`deploy/crds/`). `feather-core` applies them from the pinned `grus` source through a separate Flux Kustomization with `prune: false`. The CRDs are the only cluster-scoped objects of this change: no ClusterRole, no webhook.

## Current state

- `products/grus/clusters/feather-core/grus-dev` runs the Grus prototype from the GitRepository `grus` (`flux-system`), pinned to one commit in `foundation/sources/clusters/feather-core/sources/grus.yaml`. Chart and images move together with that pin.
- The grus repository is getting `deploy/crds/` with the three definitions. They are written there and not copied into this repository.
- Helm applies a chart's `crds/` directory on install only and never upgrades it. A chart-delivered CRD would stay at its first version.
- The `products-dev` Flux Kustomization has no `serviceAccountName`, so it applies objects with the kustomize-controller's own permissions (cluster-admin, as for every foundation layer). It may create cluster-scoped CRDs. See `design.md` for how this was checked.

## What Changes

- `products/grus/clusters/feather-core/grus-dev-crds/` (new): one Flux `Kustomization` named `grus-crds` in `flux-system`. Source: GitRepository `grus`, path `./deploy/crds`, `prune: false`, `deletionPolicy: Orphan`, `wait: true`, interval `10m0s`.
- `products/layers/feather-core/dev/kustomization.yaml`: list the new directory next to `grus-dev`.
- `foundation/sources/clusters/feather-core/sources/grus.yaml`: move `ref.commit` to a grus commit that contains `deploy/crds/` (the v2 merge commit, filled in the code pull request). The same pin moves chart and CRDs together.
- The CRD YAML itself is not part of this repository.

**Commit type and scope:** `feat(products)`. The code pull request is titled `feat(products): add the grus custom resource definitions`.

## Out of scope and non-goals

- No custom resources (pools, environments, groups) in Git. They are created at runtime or by `enable-grus-v2-dev`.
- No controller, RBAC or network change. The namespaced Role that lets the controller watch the kinds belongs to `enable-grus-v2-dev`.
- No ClusterRole, no webhook, no conversion webhook, no CRD in `foundation/`.
- No production layer. Only `products-dev`.

## Impact

- Capability `grus-crds` (see `specs/grus-crds/spec.md`).
- Three files change in the code pull request: the new Kustomization, the dev layer list and the source pin.
- Three cluster-scoped CRDs appear. Nothing else cluster-scoped.
- `enable-grus-v2-dev` depends on this change.
- The pin bump also repackages the chart (`reconcileStrategy: Revision`) at the new commit. The preflight checks that the new chart renders with the current prototype image tag, because the image tag stays unchanged here.

## Rollback

1. Revert the code pull request or remove the directory from the dev layer. Flux deletes the Kustomization object only; `deletionPolicy: Orphan` and `prune: false` keep the CRDs and every custom resource.
2. Deleting the CRDs is a deliberate manual step, because it deletes every custom resource of that kind: `kubectl delete crd pools.grus.onelitefeather.net groups.grus.onelitefeather.net environments.grus.onelitefeather.net`. Run it only after the pools are no longer needed.

## Verification after merge

Recorded in the code pull request: `kubectl get crd | grep grus.onelitefeather.net` lists three CRDs with `Established=True`, `flux get kustomizations -n flux-system grus-crds` is Ready at the pinned revision, and `kubectl -n grus-dev get pools,groups,environments` answers with an empty list instead of an error.
