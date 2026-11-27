## Why

Stop Trivy-Operator from generating SBOM reports and delete the existing ones. The kube-apiserver is being OOMKilled on all three control-plane nodes (fr01-cp-01..03, 12 GB each): its working set sits at 5 to 9 GB with spikes of 2 to 3 GB. The largest contributor is the 850 namespaced `SbomReport` objects. They take about 156 MB of the 949 MB etcd database, one LIST of them returns 403 MB of JSON, and every apiserver keeps them in its watch cache. The 115 `ClusterSbomReport` objects add another 25 MB.

Nothing consumes these SBOMs right now. Their only consumer, the `dependency-track-sbom-sync` CronJob, has been suspended since 2026-11-21 pending a redesign. Vulnerability, config-audit, exposed-secret and RBAC scans are not affected.

## What Changes

- Set `operator.sbomGenerationEnabled: false` and `operator.clusterSbomCacheEnabled: false` in `foundation/security/clusters/feather-core/trivy-operator/release.yaml`. The second key has no effect without SBOMs, so it is turned off with them.
- Delete the existing `SbomReport` and `ClusterSbomReport` objects once, after Flux has applied the release. The operator does not garbage-collect them.
- The two `ClusterVulnerabilityReport` objects disappear too, because the chart requires `sbomGenerationEnabled` for them. Nothing in the repository reads them.
- **Commit type and scope:** the implementation ships as `fix(security)`.
- **Rollback:** set `sbomGenerationEnabled` back to `true` and `clusterSbomCacheEnabled` back to `true`. The operator regenerates the reports within one scan cycle.

## Out of scope

- Re-enabling SBOMs for Dependency-Track. This comes with the redesign of the sync job (`add-dependency-track-sbom-sync`).
- The Kyverno `ephemeralreports` LIST bursts.
- Control-plane metrics for apiserver memory and etcd size beyond the checks listed in the tasks.

## Impact

- Capability `trivy-operator` (see `specs/trivy-operator/spec.md`).
- Changed file: `foundation/security/clusters/feather-core/trivy-operator/release.yaml` only.
- Dependency-Track receives no new SBOMs until the sync job is redesigned.
- Apiserver memory headroom and etcd size improve once the reports are gone. Expected effect is measured in the tasks, not assumed.
