## Context

Trivy-Operator runs in ClientServer mode (`foundation/security/clusters/feather-core/trivy-operator/release.yaml`) and, with `operator.sbomGenerationEnabled` at its chart default, writes one `SbomReport` per container and one `ClusterSbomReport` per image digest (`clusterSbomCacheEnabled: true`). The `SbomReport` CRD is namespaced in group `aquasecurity.github.io`.

Measured on `feather-core`:

- 850 namespaced `SbomReport` objects: 156 MB in etcd, about one sixth of the 949 MB database and the largest resource by far.
- One LIST of `sbomreports` returns 403 MB of JSON. Every kube-apiserver keeps them in its watch cache.
- 115 `ClusterSbomReport` objects: 25 MB.
- kube-apiserver working set 5 to 9 GB with spikes of 2 to 3 GB on 12 GB control-plane nodes; OOMKills on all three.

Constraints:

- The cluster reconciles from `main`; a change is inert until merged and applied by Flux.
- The operator does not delete existing reports when generation is turned off. They must be deleted by hand.
- `sbomGenerationEnabled` governs both report kinds, and the chart requires it for `ClusterVulnerabilityReport`.

## Decision drivers

- Apiserver memory headroom on the control plane.
- etcd database size.
- Reversibility: the change must be undoable by one value.

## Considered options

1. **Disable SBOM generation and delete the existing reports (chosen).** Set `sbomGenerationEnabled: false` and `clusterSbomCacheEnabled: false`, then delete all `SbomReport` and `ClusterSbomReport` objects once.
2. **Keep only `ClusterSbomReport`.** Not possible. `sbomGenerationEnabled` governs both kinds. `clusterSbomCacheEnabled` only deduplicates scans into one `ClusterSbomReport` per digest; it does not remove the namespaced reports, and it does not stop generation.
3. **Measure first, decide later.** Rejected. The apiserver and etcd latency metrics needed for a before/after comparison are missing, the change is cheap, and the reports have no active consumer. Measurement is still part of the tasks, after the change, to confirm the effect.

## Decision

Option 1. Set both values to `false` in the cluster overlay, validate, let Flux apply, then delete the existing reports once and watch the apiserver for seven days.

## Consequences

- Dependency-Track gets no new SBOMs until the `dependency-track-sbom-sync` redesign lands. This is accepted because the job is suspended.
- Vulnerability, config-audit, exposed-secret and RBAC reports continue unchanged.
- The two `ClusterVulnerabilityReport` objects disappear with the flag. Nothing in the repository reads them.
- The operator does not garbage-collect existing SBOM reports after the flag is turned off, so the manual `kubectl delete` in the tasks is required.
- Rollback is one value per flag (`true`). Regeneration then runs on the operator's scan cycle.
