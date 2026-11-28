## REMOVED Requirements

### Requirement: grus-dev runs on feather-core

**Reason:** The prototype is put on ice by maintainer decision on 2026-11-28.
**Migration:** None. To resume, restore the layer entries and the Flux source; the bucket `grus-templates-dev` keeps its data.

## ADDED Requirements

### Requirement: Shelved grus leaves no workloads

WHILE grus is shelved, THE cluster SHALL run no grus workload and serve no grus CRD, and the bucket `grus-templates-dev` SHALL keep its objects.

#### Scenario: After the prune

- **GIVEN** the layer entries and the source are removed and `products-dev` has reconciled
- **WHEN** `kubectl get ns grus-dev` and `kubectl get crd | grep grus` run
- **THEN** neither returns a resource, and the bucket `grus-templates-dev` still exists in Ceph
