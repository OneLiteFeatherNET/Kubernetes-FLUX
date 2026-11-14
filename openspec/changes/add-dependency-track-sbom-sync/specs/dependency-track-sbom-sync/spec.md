## ADDED Requirements

### Requirement: Cluster images are uploaded to Dependency-Track

WHEN the CronJob `dependency-track-sbom-sync` runs, THE job SHALL upload the SBOM of each distinct running image to Dependency-Track as a project named `registry/repository` with version equal to the image tag.

#### Scenario: Running image is uploaded

- **GIVEN** an `SbomReport` for container `otis` in image `otis` with tag `1.4.0` that has no matching project in Dependency-Track
- **WHEN** the job runs
- **THEN** a project `otis` with version `1.4.0` exists in Dependency-Track and carries the tag `sbom-sync-cluster`

### Requirement: Unchanged cluster images are not uploaded again

WHEN the image digest of a cluster image equals the digest stored on its project version, THE job SHALL NOT upload the SBOM again.

#### Scenario: Duplicate reports upload once

- **GIVEN** three `SbomReport` CRs for the same image `ghcr.io/onelitefeather/otis@sha256:...` with tag `1.4.0`, from three replicas
- **WHEN** the job runs
- **THEN** exactly one upload request is sent for `ghcr.io/onelitefeather/otis` version `1.4.0`

#### Scenario: Unchanged image is skipped

- **GIVEN** a project `ghcr.io/onelitefeather/otis` version `1.4.0` whose stored digest equals the current image digest
- **WHEN** the job runs
- **THEN** no upload request is sent for `otis:1.4.0` and the summary line counts it as skipped

### Requirement: Harbor artifacts are uploaded from their SBOM

WHEN a Harbor artifact has an SBOM accessory, THE job SHALL download it, convert it to CycloneDX 1.7, and upload it to Dependency-Track as a project named `<harbor host>/<project>/<repository>` with version equal to the artifact tag, or its digest when untagged.

#### Scenario: Harbor artifact with SBOM is uploaded

- **GIVEN** a Harbor artifact `library/app:2.0.0` with an SPDX SBOM accessory
- **WHEN** the job runs
- **THEN** the SBOM is converted to CycloneDX 1.7 and uploaded as project `<harbor host>/library/app` version `2.0.0` with tag `sbom-sync-harbor`

### Requirement: Missing Harbor SBOMs are generated, not uploaded

WHEN a Harbor artifact has no SBOM, THE job SHALL request SBOM generation through the Harbor scan API with body `{"scan_type": "sbom"}` and SHALL NOT create a Dependency-Track project for that artifact in the same run.

#### Scenario: Missing SBOM triggers generation

- **GIVEN** a Harbor artifact `library/app:2.0.1` without an SBOM accessory
- **WHEN** the job runs
- **THEN** a `POST .../artifacts/2.0.1/scan` request with `scan_type` `sbom` is sent, no project for `library/app:2.0.1` is created in that run, and the artifact is uploaded in a later run once its SBOM exists

#### Scenario: Non-scannable artifact is recorded, not retried as an error

- **GIVEN** a Harbor image index that rejects scan requests with `422`
- **WHEN** the job runs
- **THEN** the artifact is counted as skipped in the summary line and the run does not fail because of it

### Requirement: Retention deletes only stale projects the job created

WHEN a project carries the tag `sbom-sync-cluster` or `sbom-sync-harbor`, its version is absent from the source listing of the current run, and its `last-seen` property is 30 days old or more, THE job SHALL delete that project from Dependency-Track.

#### Scenario: Stale project created by the job is deleted

- **GIVEN** a project `otis` version `1.3.0` tagged `sbom-sync-cluster` with `last-seen` 31 days ago, and no running container with that version
- **WHEN** the job runs with a complete cluster listing
- **THEN** the project is deleted

#### Scenario: Project not created by the job is never deleted

- **GIVEN** a project `manual-tool` version `0.1` without any `sbom-sync` tag and with `last-seen` absent
- **WHEN** the job runs
- **THEN** the project is neither read as stale nor deleted

#### Scenario: Incomplete listing disables deletion

- **GIVEN** a stale Harbor-tagged project and a Harbor listing that failed on page 3
- **WHEN** the job runs
- **THEN** no project tagged `sbom-sync-harbor` is deleted in that run

### Requirement: A failed item leaves Dependency-Track unchanged

IF processing of one item fails (download, conversion, upload or property write), THEN THE job SHALL leave that item's existing Dependency-Track project unchanged, SHALL continue with the remaining items, and SHALL exit non-zero after the run.

#### Scenario: Conversion failure keeps the previous version

- **GIVEN** a project `library/app` version `2.0.0` with an existing SBOM and a new SPDX document that fails conversion
- **WHEN** the job runs
- **THEN** the existing project and its components are unchanged, the other items are processed, and the job exits with a non-zero status

### Requirement: Secrets never appear in logs

THE job SHALL NOT write the Dependency-Track API key, the Harbor password, any `Authorization` header or any credential-bearing URL to stdout or stderr.

#### Scenario: Logs contain no credentials

- **GIVEN** a run with a failing upload to Dependency-Track
- **WHEN** the run's log output is searched for the API key value and the Harbor password
- **THEN** neither value is found

### Requirement: Dry run writes nothing

WHILE the environment variable `DRY_RUN` is `true`, THE job SHALL log the actions it would take and SHALL NOT send any write request to Dependency-Track or Harbor.

#### Scenario: Dry run uploads nothing

- **GIVEN** `DRY_RUN=true` and five unique cluster images
- **WHEN** the job runs
- **THEN** no upload, property write, deletion or scan request is sent, and the summary line reports `dry_run=true`

### Requirement: Targets the Dependency-Track 5 API in-cluster

THE job SHALL send its Dependency-Track requests to the in-cluster v5 API service in namespace `dependency-track-v5`, authenticated with an API key sent in header `X-Api-Key`, and SHALL NOT target any Dependency-Track 4.x instance.

#### Scenario: Requests reach the v5 API

- **GIVEN** a run of the CronJob in namespace `dependency-track-v5`
- **WHEN** the job uploads one BOM
- **THEN** the request goes to the v5 API service and carries the header `X-Api-Key` with a key of prefix `odt_`

### Requirement: Every list call paginates explicitly

EVERY list request to Dependency-Track, Harbor or the Kubernetes API SHALL send its page size explicitly and SHALL continue until a page returns fewer items than the page size.

#### Scenario: Projects beyond one page are all seen

- **GIVEN** 250 projects tagged `sbom-sync-cluster` in Dependency-Track
- **WHEN** the job lists them for retention
- **THEN** it requests pages 1, 2 and 3 with `pageSize=100` and evaluates all 250 projects

### Requirement: Oversize BOMs are failed items

WHEN a BOM exceeds the Dependency-Track upload limit of 100 MiB, THE job SHALL record that item as failed and SHALL NOT truncate or alter the BOM to fit.

#### Scenario: Oversize BOM is rejected and recorded

- **GIVEN** a Harbor SBOM that converts to a document larger than 100 MiB
- **WHEN** the job uploads it
- **THEN** the existing Dependency-Track project for that version is unchanged, the item counts as failed in the summary line, and the other items are processed

### Requirement: Least-privilege Kubernetes access

THE ServiceAccount `dependency-track-sbom-sync` in namespace `dependency-track-v5` SHALL hold only `get` and `list` on `sbomreports` in group `aquasecurity.github.io`.

#### Scenario: Job cannot modify reports

- **GIVEN** the ServiceAccount `dependency-track-sbom-sync` in namespace `dependency-track-v5`
- **WHEN** its permissions are checked with `kubectl auth can-i delete sbomreports --as=system:serviceaccount:dependency-track-v5:dependency-track-sbom-sync -A`
- **THEN** the answer is `no`
