## ADDED Requirements

### Requirement: Grus CRDs are installed from the pinned grus source

THE `feather-core` cluster SHALL have the CustomResourceDefinitions `environments.grus.onelitefeather.net`, `groups.grus.onelitefeather.net` and `pools.grus.onelitefeather.net`, applied by a Flux Kustomization from the GitRepository `grus` at path `./deploy/crds`.

#### Scenario: CRDs are established after merge

- **GIVEN** the code pull request is merged and the pinned grus commit contains `deploy/crds/`
- **WHEN** Flux has reconciled the Kustomization `grus-crds`
- **THEN** the three CRDs exist with condition `Established=True`, in version `v1alpha1`, scope `Namespaced`

#### Scenario: Schema and chart move together

- **GIVEN** a pull request changes `ref.commit` of the GitRepository `grus`
- **WHEN** it is merged
- **THEN** the chart and the CRDs are both read from that commit

### Requirement: CRDs are never pruned

THE Kustomization `grus-crds` SHALL set `prune: false` and `deletionPolicy: Orphan`.

#### Scenario: Removing the Kustomization keeps the CRDs

- **GIVEN** the CRDs and some Pool objects exist
- **WHEN** the directory is removed from the dev layer and Flux deletes the Kustomization object
- **THEN** the three CRDs and every custom resource remain in the cluster

#### Scenario: A manifest removed from deploy/crds is not deleted

- **GIVEN** a new pinned commit no longer contains one CRD manifest
- **WHEN** Flux reconciles `grus-crds`
- **THEN** the CRD stays in the cluster

### Requirement: Only CRDs are cluster-scoped

THE change SHALL NOT add a ClusterRole, ClusterRoleBinding, validating or mutating webhook or conversion webhook.

#### Scenario: Rendered output has no other cluster-scoped kind

- **GIVEN** the code pull request
- **WHEN** `kubectl kustomize` is run on `./deploy/crds` of the pinned commit and on the changed overlays
- **THEN** the only cluster-scoped objects are the three CustomResourceDefinitions

### Requirement: Deleting the CRDs is a manual decision

THE rollback documentation SHALL state that removing the Kustomization leaves the CRDs and that deleting a CRD deletes all of its custom resources.

#### Scenario: Operator rolls back

- **GIVEN** an operator reverts the code pull request
- **WHEN** Flux reconciles
- **THEN** the CRDs and the pools are still present and the proposal names the manual delete command

### Requirement: The dev layer waits for the CRDs

THE `products-dev` layer SHALL list the new Kustomization so that its readiness is part of the layer, and THE change SHALL NOT touch the production layer.

#### Scenario: Production is unaffected

- **GIVEN** the code pull request
- **WHEN** its changed files are listed
- **THEN** no file under `products/layers/feather-core/prod` changes
