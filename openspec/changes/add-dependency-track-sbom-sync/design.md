## Context

Dependency-Track 4.14.6 runs in namespace `dependency-track` (HelmRelease in `services/development/clusters/feather-core/dependency-track/`, chart `=0.46.0`). Harbor runs from `services/development/base/harbor/` (chart 1.19.4). Trivy-Operator runs in ClientServer mode with its built-in Trivy server (`foundation/security/`) and writes one `SbomReport` per container.

Constraints:

- The cluster reconciles from `main`; a change is inert until merged.
- Secrets are SOPS/age-encrypted; the human maintainer creates the DT API key and the Harbor robot account by hand.
- The job must not change any workload, node or running image. It reads SBOMs and writes to DT and Harbor only.
- Python standard library only, pinned image by digest, non-root, read-only root filesystem.

## Verified facts

Each item states what was checked and against which source. Items marked **unverified** are listed under Open questions.

**Harbor API v2** (`https://raw.githubusercontent.com/goharbor/harbor/main/api/v2.0/swagger.yaml`, read at the time of writing):

- Trigger SBOM generation: `POST /api/v2.0/projects/{project_name}/repositories/{repository_name}/artifacts/{reference}/scan` with body `{"scan_type": "sbom"}`. The body schema `ScanType` has `scan_type` with enum `[vulnerability, sbom]`. Response `202`; `422` when the artifact has no scannable layers.
- List artifacts: `GET .../artifacts` with `page`, `page_size`, `query`, `with_sbom_overview`, `with_accessory`. `with_sbom_overview=true` returns `sbom_overview` (status and `sbom_digest` of the generation task) without one call per artifact.
- Download SBOM: `GET .../artifacts/{reference}/additions/sbom` (the `addition` enum includes `sbom`).
- List accessories: `GET .../artifacts/{reference}/accessories`.
- Format: Harbor's SBOM is **SPDX**. The Harbor integration page (`https://goharbor.io/docs/edge/administration/sbom-integration/`) says the download is "containing full SBOM details in SPDX format" and that automatic generation exists "since version 2.11 ... in combination with its default scanner - Trivy". The page does not mention CycloneDX.
- Robot permissions (source, `https://raw.githubusercontent.com/goharbor/harbor/main/src/common/rbac/const.go`): resources `scan` (create, read, stop), `sbom` (create, read, stop), `artifact` (list, read, create, delete), `accessory` (list), `repository` (pull). The docs page on robot accounts does not list these resources, so the mapping to UI checkboxes is **unverified**.

**Dependency-Track REST API** (source: the OpenAPI document served by a public DT 4.x instance, `https://dtrack-api.froeling.com/api/openapi.json`, and the CI/CD page `https://docs.dependencytrack.org/usage/cicd/`). The served version of that instance is not known, so these are checked against the cluster in task 1.2:

- `PUT /api/v1/bom` (`UploadBomBase64Encoded`), JSON body `BomSubmitRequest`: `bom` (Base64, max 20,000,000 characters), `projectName`, `projectVersion`, `autoCreate`, `isLatest`, `projectTags` (array of `{"name": ...}`), `parentName`/`parentUUID`/`parentVersion`, `isActive`. Requires `BOM_UPLOAD`. Auto-creation requires `PORTFOLIO_MANAGEMENT`, `PORTFOLIO_MANAGEMENT_CREATE` or `PROJECT_CREATION_UPLOAD`. Expects CycloneDX.
- `POST /api/v1/bom` (`UploadBom`), `multipart/form-data`: the same fields, with `bom` as a binary part and `projectTags` as a string. The description states that "When uploading large BOMs, the POST endpoint is preferred, as it does not have this limit." The CI/CD page's `autoCreate` example uses POST.
- `GET /api/v1/project/lookup?name=&version=`: requires `VIEW_PORTFOLIO`.
- `DELETE /api/v1/project/{uuid}`: requires `PORTFOLIO_MANAGEMENT` or `PORTFOLIO_MANAGEMENT_DELETE`.
- `GET|PUT|POST|DELETE /api/v1/project/{uuid}/property`: `PORTFOLIO_MANAGEMENT` or the matching `_READ`, `_CREATE`, `_UPDATE`, `_DELETE` variant.
- `GET /api/v1/tag/{name}/project` (paginated): requires `VIEW_PORTFOLIO`. Lists the projects carrying a tag, which is how the job finds its own projects.
- `Project` schema has `isLatest` and `tags`.
- Existing repo script `.github/scripts/upload-sbom.py` already uses `PUT /api/v1/bom` with `projectName`/`projectVersion`. It rewrites CycloneDX 1.7 to 1.6 because "Dependency-Track 4.13 only accepts the latter" and maps license IDs that CycloneDX 1.6 rejects to `name`.

**Trivy-Operator SbomReport** (`https://github.com/aquasecurity/trivy-operator/blob/main/docs/docs/crds/sbom-report.md`, CRD index `https://github.com/aquasecurity/trivy-operator/blob/main/docs/docs/crds/index.md`):

- Kind `SbomReport`, group `aquasecurity.github.io/v1alpha1`, namespaced, plural `sbomreports`. The CRD index lists no cluster-scoped SBOM CRD, so `clustersbomreports` is not used.
- Name convention `<workload kind>-<workload name>-<container name>`. Labels `trivy-operator.resource.kind|name|namespace` and `trivy-operator.container.name`. Owner reference to the workload pod, so a report is garbage-collected with its workload.
- `report.artifact` contains `repository` and `tag`. **The sample has no `digest` field.** The sample also has no `registry` field in `report`. `report.components` is CycloneDX (the sample shows `bomFormat: CycloneDX`, spec version not stated).
- Consequence: the image digest cannot be taken from the SbomReport as documented. Decision 3 below changes the dedupe key accordingly.

**DT permission names:** the permissions page (`https://docs.dependencytrack.org/operations/permissions/`) returned 404 at the time of writing. The names used here come from the `Requires permission` annotations in the OpenAPI document above, which is partial verification.

## Decisions

### 1. Bridge CronJob that reuses existing SBOMs (MADR)

**Context and problem:** DT lacks SBOMs for cluster workloads and Harbor artifacts. Neither producer can push to DT.

**Decision drivers:**

- No second scan of images that are already scanned.
- Harbor artifacts must be covered, not only running pods.
- Least privilege: the job can read SBOMs and write to DT, nothing else.
- Low DT load.

**Considered options:**

1. **Bridge CronJob reusing Trivy-Operator and Harbor SBOMs (chosen).** Reads `SbomReport` CRs and Harbor SBOMs, uploads to DT.
2. **Separate SBOM operator** that re-scans every running image with syft and uploads. Rejected: a second scan of images Trivy already scanned, no coverage of Harbor artifacts, and a new operator with registry pull credentials and broader RBAC.
3. **DT's Trivy analyzer integration only.** DT analyzes vulnerabilities through the Trivy server, but it does not create one project per image or upload SBOMs for them. Rejected as the primary path. Noted as a possible later complement, because the Trivy server already runs.

**Decision outcome:** option 1.

**Consequences:**

- The job depends on two producers being healthy. If Trivy-Operator stops producing reports, retention protects existing projects (no deletion without a complete listing, see decision 7).
- Harbor SBOMs need an SPDX-to-CycloneDX conversion (decision 6).
- The job's RBAC is one `get,list` on `sbomreports` plus network reachability to Harbor and DT.

### 2. Placement: sibling overlay, not inside the DT overlay

**Decision:** base in `services/development/base/dependency-track-sbom-sync/`, overlay in `services/development/clusters/feather-core/dependency-track-sbom-sync/`, listed in `services/layers/feather-core/development/kustomization.yaml`.

**Rationale:** the DT overlay is a HelmRelease with postRenderers; adding a CronJob, a ClusterRole and a secret generator there would mix two lifecycles. The sibling overlay keeps the bridge's RBAC and credentials reviewable on their own. It shares the namespace `dependency-track`.

**Consequence:** one more entry in the development layer list. The layer already orders after networking and storage.

### 3. Identity and dedupe

**Cluster source:** identity is `(registry/repository, tag)`. The digest is not in the documented SbomReport, so change detection uses a **content fingerprint**: SHA-256 over the canonical JSON of `report.components`. A DT project property `sbom-sync` / `fingerprint` holds it. The upload is skipped when the fingerprint is unchanged. If the live CRD carries a digest (task 1.1), it is stored as `sbom-sync` / `digest` for information.

This deviates from "dedupe by image digest" in the proposal request. The reason is the documented CRD shape, recorded here so the reviewer can decide.

**Harbor source:** identity is `(harbor host/project/repository, tag or digest)`. The artifact digest is stored as `sbom-sync` / `digest` and compared on each run. An untagged artifact uses its digest as version.

**Collision rule:** if the same `(name, version)` appears in both sources in one run, the cluster source wins and the Harbor entry is skipped. Both sources generate the SBOM with Trivy, but the cluster copy is CycloneDX from the operator and needs no conversion.

### 4. Upload endpoint and format

**Decision:** `POST /api/v1/bom` multipart, fields `autoCreate=true`, `projectName`, `projectVersion`, `isLatest`, `projectTags` (comma-separated string, see decision 7 for tag values), `bom` (JSON file, CycloneDX).

**Rationale:** `PUT` caps the Base64 BOM at 20,000,000 characters. Large images (many OS packages) can exceed that. The multipart POST has no such cap, as the endpoint description states.

**Format:** before upload the job rewrites `specVersion` to `1.6` and maps non-standard license IDs to `name`, the same rewrite as `upload-sbom.py`. The SPDX license list is bundled in the ConfigMap, not fetched at runtime; `upload-sbom.py` fetches it, which the bridge does not do so that a run never depends on the network beyond DT and Harbor. Whether DT 4.14.6 also accepts CycloneDX 1.7 is open (task 1.2); the 1.6 downgrade stays until then.

### 5. Harbor scope and SBOM generation

**Scope:** all artifacts in all projects (user decision). Enumeration is paginated (`page_size`, `page`). The Harbor robot needs `repository:pull`, `artifact:list`, `artifact:read`, `sbom:read`, `accessory:list` and, for generation, `scan:create` and `sbom:create`. No push and no delete.

**Missing SBOM:** an artifact whose `sbom_overview` is absent or failed gets `POST .../scan` with `{"scan_type": "sbom"}`. The job does not wait for it. The next hourly run finds the SBOM accessory and uploads it. A `202` or `422` response is logged; `422` means the artifact is not scannable (for example an image index) and is recorded as skipped, not as an error.

**Per-run budget:** `MAX_UPLOADS_PER_RUN` (default 200) caps uploads and generation requests per run, so a first run over a large Harbor does not flood DT's Postgres pool. The pool is already tuned small (`ALPINE_DATABASE_POOL_*` in `services/development/clusters/feather-core/dependency-track/release.yaml`). Items beyond the budget are picked up on later runs; the summary line reports how many were deferred.

### 6. SPDX to CycloneDX conversion (Harbor only)

**Decision:** a stdlib converter inside the script. SPDX packages become CycloneDX `components` (`name`, `version`, `purl` from the `PACKAGE-MANAGER` external reference, licenses from the declared license); SPDX relationships of type `DEPENDS_ON` become `dependencies`. The converter is tested with fixture SPDX documents in unit tests.

**Alternatives:** a conversion tool in the image (for example a CycloneDX CLI). Rejected: it adds a binary to a stdlib-only image and a second supply-chain item to pin. Fidelity of the stdlib converter against the real Harbor output is an open question (task 1.3 checks it on a sample artifact).

### 7. Retention and ownership

**Ownership:** the job creates DT projects with tag `sbom-sync-cluster` or `sbom-sync-harbor`. Deletion considers only projects listed by `GET /api/v1/tag/{name}/project` for those tags. A project without the tag is never read as stale, never updated and never deleted.

**State:** each owned project carries DT properties in group `sbom-sync`: `last-seen` (epoch seconds, type `TIMESTAMP` or `STRING`), and `fingerprint` or `digest` (values up to 1024 characters). The job reads and writes them through the property endpoints.

**Run logic for each owned version:**

- Present in this run's source listing: set `last-seen` to now; upload if the fingerprint or digest changed.
- Absent in this run, `last-seen` newer than 30 days (`RETENTION_DAYS`, default 30): keep, unchanged.
- Absent in this run, `last-seen` 30 days old or more: delete the project.

**Safety rules:**

- Deletion for a source runs only if that source's listing completed without error. A partial Harbor listing or a failed SbomReport listing disables deletion for that source in that run.
- `isLatest`: among the versions of one project name present in this run, the version with the newest creation time (SbomReport `metadata.creationTimestamp`, or Harbor artifact `push_time`, present in the swagger artifact model) is sent with `isLatest=true`. Behaviour for an existing project whose flag already points to another version is open (task 1.2).

### 8. Permissions

**DT API key:** `VIEW_PORTFOLIO`, `BOM_UPLOAD`, `PROJECT_CREATION_UPLOAD`, `PORTFOLIO_MANAGEMENT_CREATE`, `PORTFOLIO_MANAGEMENT_READ`, `PORTFOLIO_MANAGEMENT_UPDATE`, `PORTFOLIO_MANAGEMENT_DELETE`. The granular variants are used instead of the umbrella `PORTFOLIO_MANAGEMENT`, which the OpenAPI annotations also accept. The key belongs to a dedicated DT team. The exact set is confirmed in task 1.2, because the permissions page did not load.

**Harbor robot:** read-only plus SBOM generation (see decision 5). Created by the maintainer; its name and password are never written in clear.

**Kubernetes:** ServiceAccount `dependency-track-sbom-sync`, ClusterRole with `get,list` on `aquasecurity.github.io` `sbomreports` only.

### 9. Secrets and logs

One SOPS-encrypted file `sbom-sync.sops.env` with `DT_API_KEY`, `HARBOR_USERNAME`, `HARBOR_PASSWORD`, attached through `secretGenerator` in the overlay. The job never logs these values, never logs `Authorization` headers, and logs URLs without credentials. HTTP error bodies are truncated before logging.

### 10. Failure handling and observability

- Per item: any error (download, conversion, upload, property write) leaves that item's existing DT project unchanged and the job continues with the others.
- The job exits non-zero when any item failed. The CronJob failure then surfaces through the `kube-state-metrics` alerts.
- One summary line per run: `sbom-sync-summary uploaded=<n> skipped=<n> deleted=<n> deferred=<n> failed=<n> dry_run=<bool>`.
- `DRY_RUN=true` (set for the first deployment) logs the planned actions and writes nothing to DT or Harbor.

## Open questions

These are not verified and must be settled in the preflight tasks before the code PR.

1. **SbomReport digest and registry fields.** The documented sample has `artifact.repository` and `artifact.tag` but no digest, and no `registry` field. Check a live `SbomReport` (task 1.1). If there is no registry field, the project name falls back to the repository alone, which can collide across registries.
2. **DT 4.14.6 OpenAPI.** The schema above comes from a public instance of unknown version. Compare with the cluster's `/api/openapi.json` (task 1.2), and confirm `isLatest`, `projectTags` and the permission names.
3. **CycloneDX 1.7 on DT 4.14.6.** Whether the 1.6 downgrade is still needed (task 1.2).
4. **`isLatest` on an existing project** that already points to another version (task 1.2).
5. **Harbor robot permission mapping.** Which robot UI checkboxes map to `scan:create`, `sbom:create`, `sbom:read`, `artifact:list` and `accessory:list`, and whether `additions/sbom` needs `sbom:read` or `artifact:read` (task 1.3).
6. **Harbor SBOM format and version.** The docs say SPDX; the Harbor app version behind chart 1.19.4 is not confirmed, and the converter's fidelity against real output is not measured (task 1.3).
7. **Test location.** The repository has no Python test harness. Decide where the stdlib unit tests run (task 1.4).
8. **Image digest pin.** The base Python image digest is not chosen yet (task 1.5).
