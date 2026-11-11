## ADDED Requirements

### Requirement: Runner controller installation

WHEN Flux reconciles the `services-development` layer, THE cluster SHALL install the `gha-runner-scale-set-controller` chart from the OCI registry `ghcr.io/actions/actions-runner-controller-charts/` at the pinned version `0.15.1`, referenced through an `OCIRepository` source.

#### Scenario: Controller is installed from the pinned chart

- **GIVEN** the `services-development` layer is reconciled
- **WHEN** the controller HelmRelease is applied
- **THEN** its chart is taken from an `OCIRepository` pinned to `0.15.1`
- **AND** no third-party manifest is applied as raw YAML

### Requirement: Organization-scoped runner scale set

THE cluster SHALL run one runner scale set named `isuku-runners` that registers only with the organization `https://github.com/IsukuVerlag`.

#### Scenario: Scale set is bound to the organization

- **GIVEN** the scale set `isuku-runners` is deployed
- **WHEN** its `githubConfigUrl` is read
- **THEN** it equals `https://github.com/IsukuVerlag`
- **AND** no other organization or repository URL is configured

### Requirement: Runner pool bounds

THE scale set SHALL scale between 0 and 2 runners, and THE scale set SHALL NOT keep idle runners when no job is queued.

#### Scenario: Idle pool has no runner pods

- **GIVEN** no job is queued for `isuku-runners`
- **WHEN** the scale set is reconciled
- **THEN** no runner pod exists in the runner namespace

#### Scenario: Concurrent jobs are capped

- **GIVEN** three jobs are queued for `isuku-runners`
- **WHEN** the scale set is reconciled
- **THEN** at most two runner pods exist at the same time

### Requirement: Runner pod resources

EACH runner pod SHALL request 500m CPU and 2 GiB memory, and EACH runner pod SHALL be limited to 2 CPU and 4 GiB memory.

#### Scenario: Runner pod carries its requests and limits

- **GIVEN** a runner pod is created for a job
- **WHEN** its container spec is read
- **THEN** its requests are 500m CPU and 2 GiB memory
- **AND** its limits are 2 CPU and 4 GiB memory

### Requirement: Namespace quota

THE runner namespace SHALL enforce a ResourceQuota of 4 CPU and 8 GiB memory on limits, and THE runner namespace SHALL apply a LimitRange so that pods without explicit resources receive defaults.

#### Scenario: Quota blocks a third runner at its limits

- **GIVEN** two runner pods are running at their limits of 2 CPU and 4 GiB each
- **WHEN** a third runner pod is created
- **THEN** the API server rejects the pod for exceeding the namespace quota
- **AND** the pod is not scheduled

#### Scenario: Pod without resources gets defaults

- **GIVEN** a pod without resource settings is created in the runner namespace
- **WHEN** it is admitted
- **THEN** the LimitRange sets its default requests and limits

### Requirement: Pod hardening

THE runner pods SHALL run as non-root, SHALL NOT run privileged, SHALL NOT use Docker-in-Docker, and SHALL run as ephemeral runners.

#### Scenario: Privileged runner is rejected

- **GIVEN** a runner pod spec that requests `privileged: true`
- **WHEN** the pod is submitted to the runner namespace
- **THEN** the pod is not admitted

#### Scenario: Runner runs as non-root

- **GIVEN** a runner pod is running
- **WHEN** its security context is read
- **THEN** it sets runAsNonRoot to true

#### Scenario: Docker builds are not served by the scale set

- **GIVEN** a workflow in an IsukuVerlag repository needs a Docker daemon
- **WHEN** it is configured for `runs-on: ubuntu-latest`
- **THEN** it runs on a GitHub-hosted runner and not on `isuku-runners`

### Requirement: GitHub App authentication

THE scale set SHALL authenticate with GitHub through a GitHub App whose only organization permission is "Self-hosted runners: read and write", and THE app credentials SHALL come only from a SOPS-encrypted secret.

#### Scenario: Credentials come from the encrypted secret

- **GIVEN** the scale set is deployed
- **WHEN** its registration secret is read from git
- **THEN** the file is named `*.sops.yaml` and is SOPS-encrypted
- **AND** it contains no plaintext private key

#### Scenario: Personal access token is not used

- **GIVEN** the scale set configuration
- **WHEN** its authentication settings are inspected
- **THEN** no personal access token is referenced

### Requirement: Network isolation

THE runner namespace SHALL deny all ingress, and THE runner namespace SHALL allow egress only to DNS and to the destinations the jobs already reach.

#### Scenario: Inbound traffic to a runner is dropped

- **GIVEN** a runner pod is running
- **WHEN** a pod outside the runner namespace opens a connection to it
- **THEN** the connection is dropped by policy

#### Scenario: Egress to GitHub is allowed

- **GIVEN** a runner pod runs a job
- **WHEN** it connects to GitHub
- **THEN** the connection is allowed by the egress policy

### Requirement: Private repositories only

THE runner group SHALL be used only by private repositories of the organization `IsukuVerlag`, and no public repository SHALL be added to it.

#### Scenario: Public repository is not added to the group

- **GIVEN** the runner group of `isuku-runners`
- **WHEN** its repository access is reviewed
- **THEN** every repository in it is private

### Requirement: Polling defaults

THE listener and the controller SHALL keep their default polling and sync intervals, and THE configuration SHALL NOT set an aggressive sync interval.

#### Scenario: No aggressive interval is configured

- **GIVEN** the controller and listener configuration
- **WHEN** their interval settings are read
- **THEN** no interval is set below the chart default

### Requirement: Month-end evidence

THE cluster SHALL expose runner job metrics for scraping, and THE cost attribution SHALL show the cost of the runner namespace, so that job minutes on own resources can be reported per month.

#### Scenario: Job metrics are scraped

- **GIVEN** a job has completed on `isuku-runners`
- **WHEN** Prometheus is queried for the `gha_*` job metrics
- **THEN** the completed job appears with its duration

#### Scenario: Runner cost is attributed

- **GIVEN** runner pods have run during a month
- **WHEN** OpenCost is queried for that month
- **THEN** the cost is attributed to the runner namespace

### Requirement: Node capacity check before rollout

BEFORE the scale set is enabled, THE rollout SHALL record the free CPU and memory requests of each worker node.

#### Scenario: Free requests are recorded

- **GIVEN** the rollout is prepared
- **WHEN** `kubectl describe nodes` is run on the workers
- **THEN** the free requests per worker are recorded before the scale set is enabled
