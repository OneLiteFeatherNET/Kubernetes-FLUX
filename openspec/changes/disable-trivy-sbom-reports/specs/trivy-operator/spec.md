## ADDED Requirements

### Requirement: Trivy-Operator generates no SBOM reports

THE trivy-operator SHALL NOT create `SbomReport` or `ClusterSbomReport` objects.

#### Scenario: New workload is scanned without an SBOM

- **GIVEN** the trivy-operator runs with `sbomGenerationEnabled: false`
- **WHEN** a new workload with one container is deployed and scanned
- **THEN** a `VulnerabilityReport` exists for that container and no `SbomReport` exists for it

#### Scenario: No SBOM reports remain after rollout

- **GIVEN** the release has been applied by Flux and the existing reports have been deleted
- **WHEN** `kubectl get sbomreports -A` and `kubectl get clustersbomreports` are run
- **THEN** both commands return no resources
