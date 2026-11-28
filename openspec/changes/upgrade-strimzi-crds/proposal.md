## Why

Let Flux install and upgrade the Strimzi CRDs together with the operator chart. Today the CRDs in the cluster date from 2026-08-16 while the operator runs chart 1.3.1. The HelmRelease sets no `install.crds` or `upgrade.crds`, and the Flux default for upgrades is `Skip`, so the CRDs from the chart's `crds/` directory were never upgraded. `kubectl diff` against the 1.3.1 CRDs shows about 400 changed lines.

The visible effect is a reconciliation loop. The operator reconciles `kafka/feather-kafka` about every two seconds (312 runs in ten minutes) instead of every two minutes. The only change per run is a new `lastTransitionTime` on the `Ready` condition; that status write arrives as a MODIFIED watch event and starts the next run. The operator uses about 0.74 CPU cores permanently, more than any kube-apiserver, and adds about two `strimzipodsets` LIST requests per second. Upstream issue strimzi/strimzi-kafka-operator#13079 describes the same symptom; applying the matching CRDs ended the loop without a broker restart.

## What Changes

- Set `install.crds: CreateReplace` and `upgrade.crds: CreateReplace` in `foundation/messaging/base/strimzi-operator/release.yaml`.
- Flux replaces the ten Strimzi CRDs with the 1.3.1 versions on the next upgrade. Existing custom resources stay; the CRDs only gain fields.
- **Commit type and scope:** the implementation ships as `fix(messaging)`.
- **Rollback:** remove both keys. CRDs already upgraded stay at 1.3.1, which matches the running operator.

## Out of scope

- Other HelmReleases without `upgrade.crds` (71 in the cluster). Whether their CRDs are stale depends on each chart and is checked separately.
- Kafka broker configuration and resources.

## Impact

- Capability `strimzi-operator` (see `specs/strimzi-operator/spec.md`).
- Changed file: `foundation/messaging/base/strimzi-operator/release.yaml` only.
- Expected effect: the operator idles at a few millicores and reconciles on its two-minute interval. Measured in the tasks, not assumed.
