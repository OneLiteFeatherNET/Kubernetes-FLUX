## Why

Dependency-Track (DT) holds no SBOMs for most of what runs in the cluster or sits in Harbor. Two producers already exist but neither feeds DT: Trivy-Operator builds an `SbomReport` per running container (about 1861 CRs today, many of them duplicates of the same image), and Harbor can generate SBOMs per artifact with Trivy. The CI pipeline (`.github/scripts/upload-sbom.py`) uploads only images built in CI. The result is that a vulnerability in a base image or in a Harbor-stored artifact is invisible in DT.

A small bridge that reuses the SBOMs both producers already create closes the gap without a second scanner.

## What Changes

- A new hourly `CronJob` `dependency-track-sbom-sync` in namespace `dependency-track`, running a Python standard-library script from a ConfigMap, `concurrencyPolicy: Forbid`.
- **Cluster source:** reads `SbomReport` CRs (read-only, paginated), uploads one DT project per unique image content, named `registry/repository`, version = tag.
- **Harbor source:** enumerates all artifacts in all Harbor projects, uploads their SBOMs (converted from SPDX to CycloneDX 1.6). Artifacts without an SBOM get one requested through the Harbor scan API and are picked up on a later run.
- **Retention:** a version the job created and that is no longer present is deleted after 30 days. The job never touches projects it did not create.
- A ServiceAccount with `get,list` on `sbomreports` only, and a read-only Harbor robot account.
- Two credentials, a DT API key and the Harbor robot login, in one SOPS-encrypted `*.sops.env` file.

**Commit type and scope:** the implementation ships as `feat(dependency-track)`.

## Out of scope

- Replacing Trivy-Operator or Harbor scanning. The bridge only reads their results.
- DT's own Trivy analyzer integration for vulnerability analysis. It is a possible later complement, not part of this change.
- The CI SBOM pipeline and its `DT_API_KEY` secret.
- Any change under `foundation/security/`.
- A Grafana dashboard for sync results. Results are logged; a dashboard is a follow-up.

## Impact

- New capability `dependency-track-sbom-sync` (see `specs/dependency-track-sbom-sync/spec.md`).
- New files: `services/development/base/dependency-track-sbom-sync/` and `services/development/clusters/feather-core/dependency-track-sbom-sync/`, listed in `services/layers/feather-core/development/kustomization.yaml`.
- Layer `services-development`; no new layer. The layer already depends on Harbor's and DT's networking and storage.
- Runtime load: one DT lookup or property read per unique image per run, plus uploads for changed content. A per-run upload budget caps the Harbor backlog on the first runs.
- Failures surface through the existing `kube-state-metrics` CronJob failure alerts. Those alert rules must filter `job="kube-state-metrics"`.
