## 1. Code

- [ ] 1.1 Set `operator.sbomGenerationEnabled: false` and `operator.clusterSbomCacheEnabled: false` in `foundation/security/clusters/feather-core/trivy-operator/release.yaml`
- [ ] 1.2 Run `./.github/scripts/validate.sh`

## 2. Rollout and verification

- [ ] 2.1 After Flux has applied the release, delete the existing reports: `kubectl delete sbomreports -A --all` and `kubectl delete clustersbomreports --all`
- [ ] 2.2 Verify that `apiserver_storage_size_bytes` for `sbomreports` drops to about 0, and watch kube-apiserver memory on fr01-cp-01..03 for 7 days

## 3. Pull request

- [ ] 3.1 Open the code pull request `fix(security): stop generating trivy sbom reports`
