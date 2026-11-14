## 1. Preflight

- [ ] 1.1 Confirm on the cluster that every `SbomReport` carries `report.artifact.digest` and `report.registry.server`, including untagged ones (`kubectl get sbomreports -A -o yaml`). Open question 1 is resolved by a live sample; this task checks the rest of the population
- [ ] 1.2 Fetch `/api/openapi.json` from the cluster's DT 4.14.6 and confirm the `/v1/bom` fields, `isLatest` on existing projects, CycloneDX 1.7 acceptance and the permission names (design open questions 2 to 4)
- [ ] 1.3 On a Harbor test artifact with an SBOM accessory: download `additions/sbom`, confirm SPDX, run the stdlib converter on it and compare component counts; confirm the robot permission mapping in the Harbor UI for chart 1.19.4 and record the Harbor app version (design open questions 5 and 6)
- [ ] 1.4 Decide where the stdlib unit tests run (`validate.sh` or a dedicated CI step) and record it in the design (open question 7)
- [ ] 1.5 Choose the Python base image and pin it by digest (open question 8)

## 2. Manual credentials (maintainer)

- [ ] 2.1 Create a DT team with the permissions in design decision 8, and an API key for that team
- [ ] 2.2 Create a Harbor robot account with read and SBOM-generate permissions only; no push, no delete
- [ ] 2.3 Store both credentials in 1Password per the naming scheme in Outline (collection Infrastruktur)

## 3. Secrets

- [ ] 3.1 Create `services/development/clusters/feather-core/dependency-track-sbom-sync/sbom-sync.sops.env` with `DT_API_KEY`, `HARBOR_USERNAME` and `HARBOR_PASSWORD`, encrypted with `sops -e -i`; no plaintext in git
- [ ] 3.2 Run `.github/scripts/check-sops-encryption.py`

## 4. Script (red, then green)

- [ ] 4.1 Write the unit tests first, following F.I.R.S.T.: digest identity, dedupe decision, SPDX to CycloneDX conversion, CycloneDX 1.6 rewrite, retention decision, Harbor pagination, incomplete-listing rule. They use fixtures, no network and no system time
- [ ] 4.2 Implement the stdlib script to make the tests pass: cluster source, Harbor source, upload, retention, dry run, summary line, no credentials in logs

## 5. Manifests

- [ ] 5.1 Add the base `services/development/base/dependency-track-sbom-sync/`: ServiceAccount, ClusterRole (`get,list` on `sbomreports`), ClusterRoleBinding, ConfigMap with the script and the SPDX license list, and a CronJob (hourly, `concurrencyPolicy: Forbid`, digest-pinned image, non-root, read-only root filesystem, resource limits, `DRY_RUN=true`)
- [ ] 5.2 Add the cluster overlay `services/development/clusters/feather-core/dependency-track-sbom-sync/` with `kustomization.yaml` (namespace `dependency-track`, `secretGenerator` from `sbom-sync.sops.env`, `DT_URL` and `HARBOR_URL`)
- [ ] 5.3 List the overlay in `services/layers/feather-core/development/kustomization.yaml`

## 6. Validation

- [ ] 6.1 Run the unit tests and `./.github/scripts/validate.sh`; fix findings
- [ ] 6.2 Run `kubectl kustomize services/development/clusters/feather-core/dependency-track-sbom-sync` (the SOPS env file is read as opaque bytes)

## 7. Documentation

- [ ] 7.1 Write a runbook in Outline, collection Infrastruktur: rotating the DT key and the Harbor robot, reading the `sbom-sync-summary` line, and what the retention rule deletes; link it from the overlay with one line

## 8. Rollout

- [ ] 8.1 Merge the code PR; confirm `services-development` is Ready
- [ ] 8.2 Run one job from the CronJob with `DRY_RUN=true` (`kubectl create job --from=cronjob/dependency-track-sbom-sync`); read the summary line in Loki and compare the planned count with the SbomReport count
- [ ] 8.3 Set `DRY_RUN=false` and run one job manually; verify in DT the project counts per source, the `isLatest` flags and the tags
- [ ] 8.4 Let the hourly schedule run; confirm that a failed run reaches the `kube-state-metrics` CronJob alert with `job="kube-state-metrics"`

## 9. Code pull request

- [ ] 9.1 Open the code pull request under the title `feat(dependency-track): sync cluster and harbor sboms into dependency-track`; it contains no files under `openspec/`
