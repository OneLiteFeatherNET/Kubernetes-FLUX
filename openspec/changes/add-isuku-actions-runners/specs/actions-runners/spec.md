## ADDED Requirements

### Requirement: Runner controller installation

WHEN Flux reconciles the `services-development` layer, THE cluster SHALL install the `gha-runner-scale-set-controller` chart in namespace `arc-systems` from the OCI registry `ghcr.io/actions/actions-runner-controller-charts/` at the pinned version `0.15.1`, referenced through an `OCIRepository` source in the existing source set.

#### Scenario: Controller is installed from the pinned chart

- **GIVEN** the `services-development` layer is reconciled
- **WHEN** the controller HelmRelease is applied
- **THEN** its chart is taken from an `OCIRepository` pinned to `0.15.1`
- **AND** no third-party manifest is applied as raw YAML

### Requirement: Organization-scoped runner scale set

THE cluster SHALL run one runner scale set named `isuku-runners` that registers only with the organization `https://github.com/IsukuVerlag`, and THE scale set SHALL be defined only in the publisher's repository `IsukuVerlag/IsukuVerlagFlux`.

#### Scenario: Scale set is bound to the organization

- **GIVEN** the scale set `isuku-runners` is deployed
- **WHEN** its `githubConfigUrl` is read
- **THEN** it equals `https://github.com/IsukuVerlag`
- **AND** no other organization or repository URL is configured

#### Scenario: Scale set manifest is not in this repository

- **GIVEN** the repository `Kubernetes-FLUX` on `main`
- **WHEN** its files are searched for the `gha-runner-scale-set` HelmRelease
- **THEN** no such file exists

### Requirement: Tenant identity for runner workloads

EVERY HelmRelease and Kustomization in namespace `arc-runners` SHALL set `spec.serviceAccountName` to `isuku-flux`, and THE cluster SHALL reject any such object that does not.

#### Scenario: Tenant object without service account is rejected

- **GIVEN** a HelmRelease in namespace `arc-runners` without `spec.serviceAccountName`
- **WHEN** it is submitted to the API server
- **THEN** the ValidatingAdmissionPolicy rejects it

#### Scenario: Tenant object with another service account is rejected

- **GIVEN** a Kustomization in namespace `arc-runners` with `spec.serviceAccountName: default`
- **WHEN** it is submitted to the API server
- **THEN** the ValidatingAdmissionPolicy rejects it

#### Scenario: Tenant object with a remote kubeconfig is rejected

- **GIVEN** a HelmRelease in namespace `arc-runners` that sets `spec.kubeConfig`
- **WHEN** it is submitted to the API server
- **THEN** the ValidatingAdmissionPolicy rejects it

### Requirement: Tenant rights are limited to the runner namespace

THE ServiceAccount `isuku-flux` SHALL hold rights only in namespace `arc-runners`, through a namespaced Role limited to the objects the runner chart and the tenant manifests need, and it SHALL hold no rights on NetworkPolicies, ResourceQuotas, LimitRanges, admission policies, Flux Kustomizations or GitRepositories, or any cluster-scoped object.

#### Scenario: Tenant cannot weaken the network policy

- **GIVEN** the ServiceAccount `isuku-flux`
- **WHEN** `kubectl auth can-i --as=system:serviceaccount:arc-runners:isuku-flux create networkpolicies -n arc-runners` is run
- **THEN** the answer is `no`

#### Scenario: Tenant cannot edit its own link

- **GIVEN** the ServiceAccount `isuku-flux`
- **WHEN** `kubectl auth can-i --list --as=system:serviceaccount:arc-runners:isuku-flux -n arc-runners` and `-n flux-system` are run
- **THEN** no rights on `kustomizations` or `gitrepositories` appear in either list
- **AND** nothing is granted in `flux-system`

#### Scenario: Tenant cannot reach other namespaces

- **GIVEN** the ServiceAccount `isuku-flux`
- **WHEN** it is asked to create a Secret in namespace `flux-system`
- **THEN** the answer is `no`

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

EACH runner pod SHALL request 500m CPU and 2 GiB memory, and EACH runner pod SHALL be limited to 2 CPU and 6 GiB memory.

#### Scenario: Runner pod carries its requests and limits

- **GIVEN** a runner pod is created for a job
- **WHEN** its container spec is read
- **THEN** its requests are 500m CPU and 2 GiB memory
- **AND** its limits are 2 CPU and 6 GiB memory

### Requirement: Namespace quota

THE runner namespace SHALL enforce a ResourceQuota of 4250m CPU and 12544Mi memory on limits (two runners at 2 CPU and 6Gi each plus the listener at 250m and 256Mi) and of 1050m CPU and 4224Mi memory on requests, THE listener SHALL set explicit requests of 50m CPU and 128Mi memory and limits of 250m CPU and 256Mi memory, and THE runner namespace SHALL apply a LimitRange so that pods without explicit resources receive defaults. The ResourceQuota and LimitRange SHALL be owned by OneLiteFeather.

#### Scenario: Two runners at their limits fit beside the listener

- **GIVEN** the listener is running at its limits of 250m CPU and 256Mi memory
- **WHEN** two runner pods are created at their limits of 2 CPU and 6Gi each
- **THEN** both pods are admitted
- **AND** the namespace limits are fully used (4250m CPU and 12544Mi memory)

#### Scenario: Quota blocks a third runner at its limits

- **GIVEN** the listener and two runner pods are running at their limits, using the whole namespace quota
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

THE scale set SHALL authenticate with GitHub through a GitHub App whose only organization permission is "Self-hosted runners: read and write", and THE app credentials SHALL come only from a SOPS-encrypted secret in the publisher's repository that is encrypted to the maintainer's key and the tenant's key, and not to the cluster key.

#### Scenario: Credentials come from the encrypted secret

- **GIVEN** the scale set is deployed
- **WHEN** its registration secret is read from the publisher's repository
- **THEN** the file is named `*.sops.yaml` and is SOPS-encrypted
- **AND** it contains no plaintext private key

#### Scenario: Personal access token is not used

- **GIVEN** the scale set configuration
- **WHEN** its authentication settings are inspected
- **THEN** no personal access token is referenced

### Requirement: Deploy key and link

THE cluster SHALL reconcile the publisher's repository through a `GitRepository` `isuku-flux` in namespace `arc-runners` (`ssh://git@github.com/IsukuVerlag/IsukuVerlagFlux.git`, branch `main`, interval 10m) that authenticates with a read-only deploy key from the SOPS-encrypted secret `isuku-flux-deploy-key`.

#### Scenario: Deploy key is read-only

- **GIVEN** the deploy key `isuku-flux-deploy-key`
- **WHEN** its repository settings are read on GitHub
- **THEN** it is a deploy key without write access

#### Scenario: Link points at the publisher's repository

- **GIVEN** the file `clusters/feather-core/tenant-isuku.yaml` on `main`
- **WHEN** its `GitRepository` is read
- **THEN** its URL is `ssh://git@github.com/IsukuVerlag/IsukuVerlagFlux.git` on branch `main`

### Requirement: Tenant Kustomization

THE cluster SHALL apply path `./clusters/feather-core` of the publisher's repository through a Kustomization `tenant-isuku` in namespace `arc-runners`, which SHALL run as `isuku-flux`, SHALL decrypt with SOPS using `sops-age-isuku`, SHALL target namespace `arc-runners`, and SHALL depend on `services-development` in namespace `flux-system`.

#### Scenario: Tenant Kustomization runs as the tenant identity

- **GIVEN** the Kustomization `tenant-isuku`
- **WHEN** its spec is read
- **THEN** `serviceAccountName` is `isuku-flux`
- **AND** `targetNamespace` is `arc-runners`
- **AND** `dependsOn` lists `services-development` in namespace `flux-system`

#### Scenario: Failing tenant does not block OneLiteFeather layers

- **GIVEN** `tenant-isuku` is not Ready
- **WHEN** the layers `services-development` and `products` are checked
- **THEN** their readiness is unchanged

### Requirement: Reverting the link removes nothing unintended

WHEN the link file `clusters/feather-core/tenant-isuku.yaml` is removed or renamed, THE cluster SHALL keep the runner objects in namespace `arc-runners` and the namespace itself.

#### Scenario: Tenant objects survive removal of the link

- **GIVEN** the Kustomization `tenant-isuku` has `deletionPolicy: Orphan`
- **WHEN** the file is removed and reconciled
- **THEN** the HelmReleases and OCIRepositories applied by `tenant-isuku` remain in `arc-runners`

#### Scenario: Namespace survives removal of the link

- **GIVEN** namespace `arc-runners` carries the annotation `kustomize.toolkit.fluxcd.io/prune: disabled`
- **WHEN** the file is removed and reconciled
- **THEN** the namespace is not deleted

### Requirement: Admission guard placement

THE ValidatingAdmissionPolicy and its binding SHALL be part of the `services-development` layer, and THE policy SHALL be ready before `tenant-isuku` applies any object.

#### Scenario: Guard is rolled out before the tenant

- **GIVEN** a fresh reconcile of the layers
- **WHEN** `tenant-isuku` reconciles
- **THEN** the ValidatingAdmissionPolicy and binding already exist

### Requirement: Network isolation

THE runner namespace SHALL deny all ingress except scraping of the listener's metrics from Alloy (`alloy-metrics`) in the `grafana` namespace, and THE runner namespace SHALL allow egress only to DNS and to the destinations the jobs already reach.

#### Scenario: Inbound traffic to a runner is dropped

- **GIVEN** a runner pod is running
- **WHEN** a pod outside the runner namespace, other than the Prometheus stack, opens a connection to it
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
