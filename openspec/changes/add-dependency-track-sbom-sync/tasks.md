**Gate:** `migrate-dependency-track-v5` is cut over and the v5 API is the live Dependency-Track. Sections 1 and 2 may run earlier, against the v5 instance once it is up. Sections 3 onward start only after the cutover.

## 1. Preflight

- [ ] 1.1 Confirm on the cluster that every `SbomReport` carries `report.artifact.digest` and `report.registry.server`, including untagged ones (`kubectl get sbomreports -A -o yaml`). Open question 1 is resolved by a live sample; this task checks the rest of the population
- [ ] 1.2 On the v5 instance, with a test team holding exactly the seven permissions in design decision 8: create, read, update (`PATCH` isLatest) and delete one test project. Check whether portfolio access control is enabled by default and what it requires of the team (open question 2). Confirm `PATCH` isLatest clears the previous latest (open question 4)
- [ ] 1.3 Harbor test artifact with an SBOM accessory: download `additions/sbom`, confirm SPDX, run the stdlib converter to CycloneDX 1.7 and compare component counts. Upload one converted sample and one Trivy output to the v5 instance in a test project; confirm both are accepted (open question 3). Confirm the robot permission mapping in the Harbor UI for chart 1.19.4 and record the Harbor app version (open questions 5 and 6)
- [ ] 1.4 Decide where the stdlib unit tests run (`validate.sh` or a dedicated CI step) and record it in the design (open question 7)
- [ ] 1.5 Choose the Python base image and pin it by digest (open question 8)
- [ ] 1.6 Record from `migrate-dependency-track-v5`: the layer name, its `dependsOn`, the in-cluster service name and port of the v5 API (open question 9)

## 2. Manual credentials (maintainer)

- [ ] 2.1 On the v5 instance, create a DT team with the permissions in design decision 8 and an API key for that team (key prefix `odt_`)
- [ ] 2.2 Create a Harbor robot account with read and SBOM-generate permissions only; no push, no delete
- [ ] 2.3 Store both credentials in 1Password per the naming scheme in Outline (collection Infrastruktur)

## 3. Secrets

- [ ] 3.1 Create `services/development/clusters/feather-core/dependency-track-sbom-sync/sbom-sync.sops.env` with `DT_API_KEY`, `HARBOR_USERNAME` and `HARBOR_PASSWORD`, encrypted with `sops -e -i`; no plaintext in git
- [ ] 3.2 Run `.github/scripts/check-sops-encryption.py`

## 4. Script (red, then green)

- [ ] 4.1 Write the unit tests first, following F.I.R.S.T.: digest identity, dedupe decision, SPDX to CycloneDX 1.7 conversion, retention decision, `isLatest` selection, pagination (short last page, exact page boundary), oversize BOM as failed item, incomplete-listing rule. They use fixtures, no network and no system time
- [ ] 4.2 Implement the stdlib script to make the tests pass: cluster source, Harbor source, upload, `PATCH` isLatest, retention, dry run, summary line, no credentials in logs

## 5. Manifests

- [ ] 5.1 Add the base `services/development/base/dependency-track-sbom-sync/`: ServiceAccount, ClusterRole (`get,list` on `sbomreports`), ClusterRoleBinding, ConfigMap with the script and the SPDX license list, and a CronJob (hourly, `concurrencyPolicy: Forbid`, digest-pinned image, non-root, read-only root filesystem, resource limits, `DRY_RUN=true`)
- [ ] 5.2 Add the cluster overlay `services/development/clusters/feather-core/dependency-track-sbom-sync/` with `kustomization.yaml` (`namespace: dependency-track-v5`, `secretGenerator` from `sbom-sync.sops.env`, `DT_URL` set to the in-cluster v5 service from task 1.6, `HARBOR_URL`)
- [ ] 5.3 List the overlay in the layer entry from task 1.6, with its `dependsOn` on the migration layer

## 6. Validation

- [ ] 6.1 Run the unit tests and `./.github/scripts/validate.sh`; fix findings
- [ ] 6.2 Run `kubectl kustomize services/development/clusters/feather-core/dependency-track-sbom-sync` (the SOPS env file is read as opaque bytes)

## 7. Documentation

- [ ] 7.1 Write a runbook in Outline, collection Infrastruktur: rotating the DT key and the Harbor robot, reading the `sbom-sync-summary` line, and what the retention rule deletes; link it from the overlay with one line

## 8. Rollout

- [ ] 8.1 Merge the code PR; confirm the layer from task 1.6 is Ready
- [ ] 8.2 Run one job from the CronJob with `DRY_RUN=true` (`kubectl create job -n dependency-track-v5 --from=cronjob/dependency-track-sbom-sync`); read the summary line in Loki and compare the planned count with the SbomReport count
- [ ] 8.3 Set `DRY_RUN=false` and run one job manually; verify in DT the project counts per source, the `isLatest` flags and the tags
- [ ] 8.4 Let the hourly schedule run; confirm that a failed run reaches the `kube-state-metrics` CronJob alert with `job="kube-state-metrics"`

## 9. Code pull request

- [ ] 9.1 Open the code pull request under the title `feat(dependency-track): sync cluster and harbor sboms into dependency-track`; it contains no files under `openspec/`
