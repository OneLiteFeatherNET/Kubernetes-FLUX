## Context

The Strimzi operator is installed by the HelmRelease `strimzi-system/strimzi-kafka-operator` (chart `=1.3.1`). The chart ships its CRDs in `crds/`. Helm installs that directory only on first install and never upgrades it; Flux exposes this through `install.crds` (default `Create`) and `upgrade.crds` (default `Skip`). The CRDs in the cluster were created on 2026-08-16 and never changed since.

## Decision drivers

- Operator CPU and apiserver request load.
- CRDs must match the running operator version after every chart bump, without a manual step.
- No disruption to the running Kafka cluster.

## Considered options

1. **`CreateReplace` for install and upgrade (chosen).** Flux applies the chart's CRDs on every upgrade. Future chart bumps keep them in sync.
2. **Apply the 1.3.1 CRDs once by hand.** Fixes the loop today, but the next chart bump repeats the drift. Rejected.
3. **Separate CRD source (raw CRD manifests in a Kustomization).** Duplicates what the chart ships and needs its own version pinning. Rejected.

## Decision

Option 1.

## Consequences

- The ten Strimzi CRDs are replaced with the chart's versions on the next Helm upgrade. A `CreateReplace` of a CRD never deletes custom resources.
- A future chart version that removes a CRD version still served by stored objects would fail the upgrade instead of silently skipping; Flux reports it on the HelmRelease.
- Because the HelmRelease spec changes, Flux runs a Helm upgrade right after merge; the operator Deployment itself does not change.
