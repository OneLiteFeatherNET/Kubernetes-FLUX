## ADDED Requirements

### Requirement: Strimzi CRDs follow the operator chart

WHEN the Strimzi operator HelmRelease is installed or upgraded, Flux SHALL create or replace the Strimzi CRDs with the versions shipped in that chart.

#### Scenario: CRDs match the chart after upgrade

- **GIVEN** the HelmRelease sets `install.crds: CreateReplace` and `upgrade.crds: CreateReplace`
- **WHEN** Flux has upgraded the release to chart 1.3.1
- **THEN** `kubectl diff` against the chart's `crds/` files reports no differences

#### Scenario: Operator stops reconciling in a loop

- **GIVEN** the CRDs match chart 1.3.1
- **WHEN** the operator log is read for ten minutes
- **THEN** `kafka/feather-kafka` is reconciled only on watch changes and the two-minute interval, and the `Ready` condition's `lastTransitionTime` does not change
