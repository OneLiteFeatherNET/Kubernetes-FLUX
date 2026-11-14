## Context

Dependency-Track moves to 5.2.1 (chart 2.9.0). That migration is a separate change, `migrate-dependency-track-v5`, which runs the v5 instance in namespace `dependency-track-v5`. This change targets the v5 API only. It is not built against DT 4.14.6, and implementation starts after the cutover of that change.

Harbor runs from `services/development/base/harbor/` (chart 1.19.4). Trivy-Operator runs in ClientServer mode with its built-in Trivy server (`foundation/security/`) and writes one `SbomReport` per container.

Constraints:

- The cluster reconciles from `main`; a change is inert until merged.
- Secrets are SOPS/age-encrypted; the human maintainer creates the DT API key and the Harbor robot account by hand.
- The job must not change any workload, node or running image. It reads SBOMs and writes to DT and Harbor only.
- Python standard library only, pinned image by digest, non-root, read-only root filesystem.

## Verified facts

**Dependency-Track 5.2.1 source** (`DependencyTrack/dependency-track`, tag 5.2.1; paths relative to `apiserver/src/main/java/org/dependencytrack/`). These replace the DT 4.x OpenAPI facts used in the first revision.

- `PUT /v1/bom` is `resources/v1/BomResource.java`, method at line 398, permission `BOM_UPLOAD` at line 456. Body `BomSubmitRequest` (`resources/v1/vo/BomSubmitRequest.java`) with `projectName`, `projectVersion`, `autoCreate`, `projectTags` (list of `{"name": ...}`), `isLatest` (JSON alias `isLatestProjectVersion`, line 121), `parentUUID`/`parentName`/`parentVersion`, `isActive` and `bom` (Base64). The documented cap of the Base64 `bom` is 20,000,000 characters (line 426).
- `POST /v1/bom` is `BomResource.java`, method at line 606, multipart, permission `BOM_UPLOAD` at line 670. The same fields, with `projectTags` as one comma-separated string (line 690). Size cap `dt.bom-upload.max-size-bytes=104857600` (100 MiB, `apiserver/src/main/resources/application.properties` line 57), enforced in `readUpload` (`BomResource.java` lines 908 to 915).
- `isLatest` on an upload applies only when the upload creates the project (`qm.createProject(..., isLatest, ...)`, `BomResource.java` lines 528 to 547 for PUT, 756 to 775 for POST). An upload to an existing version does not change its flag.
- `isLatest` on an existing version is set with `PATCH /v1/project/{uuid}` (`resources/v1/ProjectResource.java`, method at line 868, permission `PORTFOLIO_MANAGEMENT` or `PORTFOLIO_MANAGEMENT_UPDATE` at line 905, field applied at line 964). Setting it to true clears the flag on the previous latest version (`persistence/ProjectQueryManager.java` line 300).
- Project lookup `GET /v1/project/lookup?name=&version=` is `ProjectResource.java` line 409, permission `VIEW_PORTFOLIO` at line 432.
- Project delete `DELETE /v1/project/{uuid}` is `ProjectResource.java` line 1082, permission `PORTFOLIO_MANAGEMENT` or `PORTFOLIO_MANAGEMENT_DELETE` at line 1110.
- Projects by tag `GET /v1/tag/{name}/project` is `resources/v1/TagResource.java` line 171. It is `@PaginatedApi` and needs `VIEW_PORTFOLIO` (line 197).
- Project properties `resources/v1/ProjectPropertyResource.java` at `/v1/project/{uuid}/property`: GET needs `PORTFOLIO_MANAGEMENT` or `_READ` (line 104), PUT needs `_CREATE` (line 156), POST needs `_UPDATE` (line 233), DELETE needs `_DELETE` (line 296). The GET is not annotated `@PaginatedApi`.
- Pagination: `@PaginatedApi` (`resources/v1/openapi/PaginatedApi.java` lines 35 to 50) documents `pageNumber` (default 1) and `pageSize` (default 100), with `offset`/`limit` as an alternative.
- Permissions (`auth/Permissions.java`): `BOM_UPLOAD` (line 28), `VIEW_PORTFOLIO` (line 29), `PORTFOLIO_ACCESS_CONTROL_BYPASS` (line 30), `PORTFOLIO_MANAGEMENT` (line 32), `PORTFOLIO_MANAGEMENT_CREATE`, `_READ`, `_UPDATE`, `_DELETE` (lines 33 to 36), `PROJECT_CREATION_UPLOAD` (line 69). The `Constants` class (line 90 onward) uses the same names.
- Portfolio access control: when enabled, a principal sees only the projects its team is assigned, unless it holds `PORTFOLIO_ACCESS_CONTROL_BYPASS` (`persistence/QueryManager.java` line 1077). Whether the flag is enabled by default on this instance is not verified here.
- API key header is `X-Api-Key` (`e2e/src/main/java/org/dependencytrack/e2e/api/ApiAuthInterceptor.java` line 32). v5 keys have the prefix `odt_` followed by 8 characters, `_` and 32 characters (`apiserver/src/test/java/org/dependencytrack/resources/v1/TeamResourceTest.java` line 484).
- CycloneDX 1.7 is accepted: `parser/cyclonedx/CycloneDxValidator.java` line 231 maps `"1.7"` to `VERSION_17`.

**Trivy-Operator SbomReport** (`https://github.com/aquasecurity/trivy-operator/blob/main/docs/docs/crds/sbom-report.md`). The documented sample has `report.artifact.repository` and `tag` only, but a live SbomReport on the `feather-core` cluster (Trivy 0.76.2) carries `report.artifact.digest` (`sha256:...`), `report.artifact.repository`, `report.artifact.tag` and `report.registry.server` (for example `ghcr.io`). `report.components` is CycloneDX specVersion 1.7. The CRD is namespaced, group `aquasecurity.github.io/v1alpha1`, plural `sbomreports`.

**Harbor API v2** (`https://raw.githubusercontent.com/goharbor/harbor/main/api/v2.0/swagger.yaml`):

- Trigger SBOM generation: `POST /api/v2.0/projects/{project_name}/repositories/{repository_name}/artifacts/{reference}/scan` with body `{"scan_type": "sbom"}` (`ScanType` enum `[vulnerability, sbom]`). Response `202`; `422` when the artifact has no scannable layers.
- List artifacts: `GET .../artifacts` with `page`, `page_size`, `query`, `with_sbom_overview`, `with_accessory`. The swagger default `page_size` is 10 (maximum 100), so the job sets `page_size=100` explicitly.
- Download SBOM: `GET .../artifacts/{reference}/additions/sbom` (the `addition` enum includes `sbom`).
- List accessories: `GET .../artifacts/{reference}/accessories`.
- Format: Harbor's SBOM is **SPDX** (`https://goharbor.io/docs/edge/administration/sbom-integration/`). Automatic generation exists "since version 2.11 ... in combination with its default scanner - Trivy".
- Robot RBAC resources (`https://raw.githubusercontent.com/goharbor/harbor/main/src/common/rbac/const.go`): `scan` (create, read, stop), `sbom` (create, read, stop), `artifact` (list, read, create, delete), `accessory` (list), `repository` (pull). The mapping to robot UI checkboxes is **unverified**.

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

### 2. Placement: next to the v5 API, in `dependency-track-v5`

**Decision:** the CronJob, its ServiceAccount, ClusterRole binding, ConfigMap and secret run in namespace `dependency-track-v5`, next to the v5 API. Base in `services/development/base/dependency-track-sbom-sync/`, cluster overlay in `services/development/clusters/feather-core/dependency-track-sbom-sync/` with `namespace: dependency-track-v5`. The overlay is listed in the layer entry that the migration change establishes for `dependency-track-v5` (open question 9).

`DT_URL` is the in-cluster service URL of the v5 API, as defined by `migrate-dependency-track-v5`. This change does not invent a service name.

**Rationale:**

- The job calls the v5 API inside one namespace, with no cross-namespace routing.
- The job's lifecycle follows its target. The 4.x namespace `dependency-track` is not a target of this change.
- The ClusterRole stays cluster-scoped for `sbomreports`; only the subject and the secret are namespaced.

**Consequence:** the layer ordering depends on the migration change. The bridge is not applied before the v5 API is Ready.

### 3. Identity and dedupe

**Cluster source:** the dedupe key is the image digest, `report.registry.server/report.artifact.repository@report.artifact.digest`. The DT project name is `registry.server/repository` and the version is `report.artifact.tag`, or the digest when the tag is empty.

Dedupe is applied per DT project. The job skips an upload when the project `(name, version)` already stores the same digest in property `sbom-sync` / `digest`. A tag that points at a digest already uploaded under another tag is still uploaded once under its own version. DT has no alias between versions, and this keeps every running tag visible in DT. The cost is one extra upload of identical content.

**Harbor source:** identity is `(harbor host/project/repository, tag or digest)`. The artifact digest is stored as `sbom-sync` / `digest` and compared on each run. An untagged artifact uses its digest as version.

**Collision rule:** if the same `(name, version)` appears in both sources in one run, the cluster source wins and the Harbor entry is skipped. The cluster copy is CycloneDX from the operator and needs no conversion.

### 4. Upload endpoint and format

**Decision:** `POST /v1/bom` multipart, fields `autoCreate=true`, `projectName`, `projectVersion`, `isLatest`, `projectTags` (comma-separated string, see decision 7 for tag values), `bom` (CycloneDX 1.7 JSON file).

**Rationale:** the POST path accepts up to 100 MiB (`dt.bom-upload.max-size-bytes`) and sends the document without Base64 inflation. The PUT path documents a 20,000,000-character Base64 cap, which large images can exceed.

**Format:** the Trivy output (CycloneDX 1.7) is uploaded unchanged. DT 5.2.1 validates 1.7 (`CycloneDxValidator.java` line 231), so the 1.6 downgrade and the license rewrite of `upload-sbom.py` are not carried over. A BOM above 100 MiB is rejected by DT and recorded as a failed item; it is never truncated.

**isLatest:** the upload sets the flag only when it creates the version. For an existing version that must become latest, the job sends `PATCH /v1/project/{uuid}` with `isLatest: true` (decision 7).

### 5. Harbor scope and SBOM generation

**Scope:** all artifacts in all projects (user decision). Enumeration uses `page` from 1 and `page_size=100`, and continues until a page returns fewer than 100 items. The Harbor robot needs `repository:pull`, `artifact:list`, `artifact:read`, `sbom:read`, `accessory:list` and, for generation, `scan:create` and `sbom:create`. No push and no delete.

**Missing SBOM:** an artifact whose `sbom_overview` is absent or failed gets `POST .../scan` with `{"scan_type": "sbom"}`. The job does not wait for it. The next hourly run finds the SBOM accessory and uploads it. A `202` or `422` response is logged; `422` means the artifact is not scannable (for example an image index) and is recorded as skipped, not as an error.

**Per-run budget:** `MAX_UPLOADS_PER_RUN` (default 200) caps uploads and generation requests per run, so a first run over a large Harbor does not flood the v5 database connection pool. Items beyond the budget are picked up on later runs; the summary line reports how many were deferred.

### 6. SPDX to CycloneDX conversion (Harbor only)

**Decision:** emit **CycloneDX 1.7**. The converter is a stdlib function inside the script. SPDX packages become CycloneDX `components` (`name`, `version`, `purl` from the `PACKAGE-MANAGER` external reference, licenses from the declared license); SPDX relationships of type `DEPENDS_ON` become `dependencies`.

**Why 1.7 and not 1.6:**

- DT 5.2.1 accepts 1.7 (`CycloneDxValidator.java` line 231).
- The cluster path already produces 1.7 from Trivy. Both sources then share one format and one validation path, and no downgrade step is needed.
- Emitting 1.6 would add a downgrade stage and a bundled license list for no gain on v5.

**Alternatives:** a conversion tool in the image (for example a CycloneDX CLI). Rejected: it adds a binary to a stdlib-only image and a second supply-chain item to pin.

**Risk:** the converter output must pass DT's 1.7 validation. Task 1.3 uploads a converted sample to the v5 instance before the job is enabled. Fidelity against real Harbor output is an open question.

### 7. Retention, ownership and pagination

**Ownership:** the job creates DT projects with tag `sbom-sync-cluster` or `sbom-sync-harbor`. Deletion considers only projects listed by `GET /v1/tag/{name}/project` for those tags. A project without the tag is never read as stale, never updated and never deleted.

**Pagination:** every list call sends `pageNumber` and `pageSize=100` explicitly and continues until a page returns fewer than 100 items. DT 5 defaults to 100 per page, and the job must not rely on that default. The lists the job reads are: the SbomReports (`limit` with `continue` tokens), Harbor artifacts (`page`, `page_size=100`), and projects by tag (`pageNumber`, `pageSize=100`). The property `GET` is a single-project read and is not paginated.

**State:** each owned project carries DT properties in group `sbom-sync`: `last-seen` (epoch seconds) and `digest` (the image digest, or the artifact digest for Harbor; values up to 1024 characters). The job reads and writes them through the property endpoints.

**Run logic for each owned version:**

- Present in this run's source listing: set `last-seen` to now; upload if the stored `digest` differs from the current one.
- Absent in this run, `last-seen` newer than 30 days (`RETENTION_DAYS`, default 30): keep, unchanged.
- Absent in this run, `last-seen` 30 days old or more: delete the project.

**Safety rules:**

- Deletion for a source runs only if that source's listing completed without error. A partial Harbor listing or a failed SbomReport listing disables deletion for that source in that run.
- `isLatest`: among the versions of one project name present in this run, the version with the newest creation time (SbomReport `metadata.creationTimestamp`, or Harbor artifact `push_time`) should be latest. If it is not yet latest, the job sends `PATCH isLatest=true`, which clears the previous latest in DT (`ProjectQueryManager.java` line 300).

### 8. Permissions

**DT API key** (prefix `odt_`, sent as `X-Api-Key`), exact names from `auth/Permissions.java`:

- `BOM_UPLOAD`: `POST /v1/bom`.
- `PROJECT_CREATION_UPLOAD`: create a project on upload (`autoCreate`). `PORTFOLIO_MANAGEMENT_CREATE` also allows it.
- `VIEW_PORTFOLIO`: project lookup and projects by tag.
- `PORTFOLIO_MANAGEMENT_READ`: read properties.
- `PORTFOLIO_MANAGEMENT_UPDATE`: write properties and `PATCH` isLatest.
- `PORTFOLIO_MANAGEMENT_DELETE`: delete projects.

The umbrella `PORTFOLIO_MANAGEMENT` is not used; the granular names cover every call. The key belongs to a dedicated DT team. If portfolio access control is enabled on the v5 instance, the team must be assigned the projects it works on, or the key needs `PORTFOLIO_ACCESS_CONTROL_BYPASS`. This change does not request the bypass (open question 2).

**Harbor robot:** read-only plus SBOM generation (decision 5). Created by the maintainer; its name and password are never written in clear.

**Kubernetes:** ServiceAccount `dependency-track-sbom-sync` in `dependency-track-v5`, ClusterRole with `get,list` on `aquasecurity.github.io` `sbomreports` only.

### 9. Secrets and logs

One SOPS-encrypted file `sbom-sync.sops.env` with `DT_API_KEY`, `HARBOR_USERNAME` and `HARBOR_PASSWORD`, attached through `secretGenerator` in the overlay. The job never logs these values, never logs `X-Api-Key` or `Authorization` headers, and logs URLs without credentials. HTTP error bodies are truncated before logging.

### 10. Failure handling and observability

- Per item: any error (download, conversion, upload, property write, `PATCH`) leaves that item's existing DT project unchanged and the job continues with the others.
- The job exits non-zero when any item failed. The CronJob failure then surfaces through the `kube-state-metrics` alerts.
- One summary line per run: `sbom-sync-summary uploaded=<n> skipped=<n> deleted=<n> deferred=<n> failed=<n> dry_run=<bool>`.
- `DRY_RUN=true` (set for the first deployment) logs the planned actions and writes nothing to DT or Harbor.

## Open questions

1. **Resolved.** A live SbomReport carries `report.artifact.digest`, `report.artifact.repository`, `report.artifact.tag` and `report.registry.server` (Trivy 0.76.2). Decision 3 rests on it. Task 1.1 checks the fields on every report, including untagged ones.
2. **Partly resolved.** The permission names are exact (see decision 8, verified in `Permissions.java`). Still open: that a team holding exactly those grants can create, read, update and delete on the live v5 instance, and whether portfolio access control is enabled there by default (task 1.2).
3. **Resolved.** DT 5.2.1 accepts CycloneDX 1.7 (`CycloneDxValidator.java` line 231). The 1.6 downgrade is dropped (decision 4). Task 1.3 still uploads one converted Harbor sample and one Trivy output to the v5 instance to confirm acceptance.
4. **Resolved in source, confirm live.** An upload sets `isLatest` only when it creates a version; an existing version is changed with `PATCH /v1/project/{uuid}` (`ProjectResource.java` line 964), which clears the previous latest (`ProjectQueryManager.java` line 300). Task 1.2 confirms this on the instance.
5. **Harbor robot permission mapping.** Which robot UI checkboxes map to `scan:create`, `sbom:create`, `sbom:read`, `artifact:list` and `accessory:list`, and whether `additions/sbom` needs `sbom:read` or `artifact:read` (task 1.3).
6. **Harbor SBOM format and version.** The docs say SPDX; the Harbor app version behind chart 1.19.4 is not confirmed, and the converter's fidelity against real output is not measured (task 1.3).
7. **Test location.** The repository has no Python test harness. Decide where the stdlib unit tests run (task 1.4).
8. **Image digest pin.** The base Python image digest is not chosen yet (task 1.5).
9. **Layer and service name.** The layer name, its `dependsOn` on the migration layer, and the in-cluster service name and port of the v5 API come from `migrate-dependency-track-v5`. Task 1.6 records them before the overlay is written.
