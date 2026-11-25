## ADDED Requirements

### Requirement: Web UI runs as one ClusterIP workload

THE web UI SHALL run in namespace `grus-dev` as the Deployment `grus-web` with `replicas: 1`, exposed by the ClusterIP Service `grus-web` on port 3000.

#### Scenario: Workload is rendered and ready

- **GIVEN** the code pull request is applied and `products-dev` reconciles
- **WHEN** the Deployment and Service `grus-web` are inspected in `grus-dev`
- **THEN** the Deployment has one replica that is Ready
- **AND** the Service type is `ClusterIP` with port 3000

### Requirement: No external exposure

THE code pull request SHALL NOT add a Service of type `LoadBalancer`, an `HTTPRoute`, a `Certificate` or a DNAT rule for `grus-web`.

#### Scenario: Web UI is not reachable from outside the cluster

- **GIVEN** the web UI is running in `grus-dev`
- **WHEN** a client outside the cluster connects to the UI address
- **THEN** the connection is refused or no route exists

#### Scenario: Port-forward reaches the web UI

- **GIVEN** the operator runs `kubectl -n grus-dev port-forward svc/grus-web 3000:3000`
- **WHEN** the operator requests `/healthz` on `localhost:3000`
- **THEN** the UI answers with HTTP 200

### Requirement: Admin token stays on the server side

THE web UI SHALL read `GRUS_ADMIN_TOKEN` from the existing Secret `grus-dev-secrets` on the server side only, and THE token SHALL NOT appear in any HTTP response, page, client bundle or browser storage.

#### Scenario: Token is not delivered to the browser

- **GIVEN** the operator has opened the UI through port-forward
- **WHEN** the operator inspects the page source, the loaded scripts and the network responses
- **THEN** the admin token value does not appear in any of them

#### Scenario: Token comes from the existing Secret

- **GIVEN** the rendered Deployment `grus-web`
- **WHEN** its environment is read
- **THEN** `NUXT_GRUS_TOKEN` is taken from Secret `grus-dev-secrets`, key `GRUS_ADMIN_TOKEN`
- **AND** no new Secret is added by the code pull request

### Requirement: Cross-site writes are rejected

THE web UI SHALL reject write requests that do not originate from the UI itself.

#### Scenario: Cross-site write is refused

- **GIVEN** the web UI is reachable through port-forward
- **WHEN** a page from another origin sends a write request to the UI
- **THEN** the UI rejects the request and does not forward it to the controller

### Requirement: Restricted pod security and fixed resources

THE web pod SHALL run as non-root with a read-only root filesystem, drop all Linux capabilities, use the RuntimeDefault seccomp profile, not mount a ServiceAccount token, and SHALL set memory and CPU requests equal to their limits at 192Mi and 100m.

#### Scenario: Container security settings are applied

- **GIVEN** the rendered Deployment `grus-web`
- **WHEN** its pod and container security contexts are read
- **THEN** `runAsNonRoot` is true, `readOnlyRootFilesystem` is true and all capabilities are dropped
- **AND** `automountServiceAccountToken` is false

#### Scenario: Requests equal limits

- **GIVEN** the rendered Deployment `grus-web`
- **WHEN** its resources are read
- **THEN** the memory request and limit are both 192Mi
- **AND** the CPU request and limit are both 100m

### Requirement: Health probes on the UI

THE web pod SHALL use `/healthz` as readiness and liveness probe.

#### Scenario: Pod is not ready without the UI answering

- **GIVEN** the UI does not answer `/healthz`
- **WHEN** the readiness probe runs
- **THEN** the pod is not Ready and receives no traffic

### Requirement: Pinned source and image tags move together

THE `grus` GitRepository SHALL pin `ref.commit` to `4839dd3c77dfd90c436a4f0bceabcc7e1128f2d7`, THE controller image tag SHALL be `prototype-4839dd3`, and THE web image tag SHALL be `prototype-4839dd3` from the repository `harbor.onelitefeather.dev/onelitefeather/grus-web`.

#### Scenario: Every tag matches the pinned commit

- **GIVEN** the code pull request is merged
- **WHEN** the source and the HelmRelease values are read
- **THEN** `ref.commit` is `4839dd3c77dfd90c436a4f0bceabcc7e1128f2d7`
- **AND** the controller and web image tags are both `prototype-4839dd3`

#### Scenario: Pull secret comes from the chart fallback

- **GIVEN** the HelmRelease sets no `web.imagePullSecrets`
- **WHEN** the rendered web Deployment is read
- **THEN** its `imagePullSecrets` is `harbor-pull`

### Requirement: Controller restart keeps the instances

WHEN the controller pod is replaced by the new image tag, THE controller SHALL keep the running game instances and adopt them.

#### Scenario: Instances survive the controller restart

- **GIVEN** instances are running in `grus-dev` before the rollout
- **WHEN** the controller pod is replaced at `prototype-4839dd3`
- **THEN** the instances are still running afterwards
- **AND** the controller lists them as adopted

### Requirement: Rollback by disabling the UI

THE HelmRelease values SHALL allow the web UI to be removed by setting `web.enabled` to `false`, without changing the controller.

#### Scenario: Disabling the UI removes its objects

- **GIVEN** the web UI is enabled and running
- **WHEN** a pull request sets `web.enabled: false` and is merged
- **THEN** the Deployment and Service `grus-web` are removed
- **AND** the controller pod is not restarted by that change

### Requirement: Scope limited to grus-dev

THE code pull request SHALL change only `foundation/sources/clusters/feather-core/sources/grus.yaml` and `products/grus/clusters/feather-core/grus-dev/release.yaml`, and SHALL NOT change any NetworkPolicy, layer, namespace, other product or node.

#### Scenario: Only the two files change

- **GIVEN** the code pull request
- **WHEN** its changed files are listed
- **THEN** the list contains exactly the two files above
