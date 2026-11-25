## ADDED Requirements

### Requirement: grus-dev runs the pinned v2 build

THE release `grus` in namespace `grus-dev` SHALL use the controller and web image tags of the Grus v2 build, and THE GitRepository `grus` SHALL be pinned to the commit of that build.

#### Scenario: Pin and tags move together

- **GIVEN** the code pull request is merged
- **WHEN** the pin, the controller tag and the web tag are read
- **THEN** all three name the same Grus v2 merge commit

### Requirement: Templates are stored in S3

THE release SHALL set `templates.backend` to `s3` and read credentials and endpoint from the Secret and ConfigMap `grus-templates-dev`, created by the ObjectBucketClaim `grus-templates-dev` with `storageClassName: ceph-bucket-fr01` and without `bucketOwner`.

#### Scenario: Bucket claim is bound

- **GIVEN** the code pull request is merged
- **WHEN** Rook has reconciled the claim
- **THEN** the claim is Bound and the Secret `grus-templates-dev` and the ConfigMap `grus-templates-dev` exist in `grus-dev`

#### Scenario: No foundation change is needed

- **GIVEN** the code pull request
- **WHEN** its changed files are listed
- **THEN** it contains no `CephObjectStoreUser` and no file under `foundation/storage`

### Requirement: Controller reaches object storage only through a policy

THE network policy SHALL allow egress from pods labelled `app.kubernetes.io/name: grus` to the RGW pods in namespace `rook-ceph-fr01` on TCP port 80 only.

#### Scenario: Controller stores a template

- **GIVEN** the policy is applied
- **WHEN** the controller uploads a template version
- **THEN** it reaches `rook-ceph-rgw-feather-s3` in `rook-ceph-fr01` on port 80

#### Scenario: Game pods are not opened to object storage

- **GIVEN** the policy is applied
- **WHEN** a pod without the label `app.kubernetes.io/name: grus` connects to the RGW pods
- **THEN** the policy does not allow it

### Requirement: Controller watches the Grus CRDs in its namespace only

WHEN `crds.watch` is true, THE chart SHALL grant a namespaced Role in `grus-dev` with get, list and watch on `environments`, `groups` and `pools` of group `grus.onelitefeather.net` and get, update and patch on `pools/status`, and THE change SHALL NOT add a ClusterRole.

#### Scenario: Controller lists pools

- **GIVEN** the CRDs exist and the release is Ready
- **WHEN** the controller lists `pools` in `grus-dev`
- **THEN** the call is allowed and no permission error is logged

#### Scenario: Other namespaces are out of reach

- **GIVEN** the release is Ready
- **WHEN** the controller service account lists `pools` in another namespace
- **THEN** the API server denies the request

### Requirement: Agent API is published on its own internal hostname

THE internal gateway SHALL route `grus-dev-api.apps.onelite.feather` on the https listener to Service `grus` port 8080 for exactly these requests: PathPrefix `/v1/agents`, PathPrefix `/v1/self`, method GET with PathPrefix `/v1/templates`, and Exact `/healthz`, over a certificate from `step-ca-issuer`.

#### Scenario: Agent registers through the gateway

- **GIVEN** the code pull request is merged and the controller is Ready
- **WHEN** the agent on `mc01` sends `PUT /v1/agents/mc01` with a valid agent token
- **THEN** the controller answers 200

#### Scenario: Admin API is not routed

- **GIVEN** the route is applied
- **WHEN** a client requests `/v1/pools`, `/v1/instances`, `/v1/events` or `/v1/hosts` on `grus-dev-api.apps.onelite.feather`
- **THEN** the gateway answers 404 and the request does not reach the controller

#### Scenario: Template upload is not routed

- **GIVEN** the route is applied
- **WHEN** a client sends `PUT` or `POST` to a path below `/v1/templates`
- **THEN** the gateway answers 404

#### Scenario: Long-poll survives the gateway

- **GIVEN** the agent holds `GET /v1/agents/mc01/desired?wait=30s`
- **WHEN** the request stays open for 25 seconds
- **THEN** the gateway does not close it, because request and backend timeouts of that rule are 60 seconds

### Requirement: Gateway reaches the controller port only

THE network policy SHALL allow ingress from the envoy proxy pods in namespace `envoy` to pods labelled `app.kubernetes.io/name: grus` on TCP 8080, and SHALL NOT open any other pod of `grus-dev` to the gateway beyond the existing web policy.

#### Scenario: Gateway forwards to the controller

- **GIVEN** the policy is applied
- **WHEN** an envoy proxy pod connects to the controller pod on TCP 8080
- **THEN** the connection is allowed

#### Scenario: Gateway cannot reach game pods

- **GIVEN** the policy is applied
- **WHEN** an envoy proxy pod connects to a game or proxy pod
- **THEN** the policy does not allow it

### Requirement: Only the token hash is stored in the cluster

THE controller SHALL read the agent credential from `GRUS_AGENT_TOKENS` in the Secret `grus-dev-secrets` as `mc01=<sha256 hex>`, and THE repository SHALL NOT contain the plain agent token or any instance token.

#### Scenario: Secret holds a hash

- **GIVEN** the code pull request
- **WHEN** the decrypted `grus-dev-secrets.sops.env` is read by a reviewer
- **THEN** `GRUS_AGENT_TOKENS` contains a 64-character hex digest after `mc01=`

#### Scenario: Wrong token is refused

- **GIVEN** the route is applied
- **WHEN** a client calls `PUT /v1/agents/mc01` with a token that does not match the hash
- **THEN** the controller answers 401 or 403

### Requirement: Proxy egress to mc01 is limited to the agent port range

THE network policy SHALL allow egress from pods labelled `grus.olf/role: proxy` to `10.200.2.35/32` on TCP 30000 to 30100 (`endPort`) and to no other address or port at that host.

#### Scenario: Proxy joins an instance on mc01

- **GIVEN** an agent instance on `mc01` listens on a port in 30000-30100
- **WHEN** the Velocity proxy connects to it
- **THEN** the connection is allowed by the policy

#### Scenario: Other ports on mc01 are not opened

- **GIVEN** the policy is applied
- **WHEN** the proxy connects to `10.200.2.35` on a port outside 30000-30100
- **THEN** the policy does not allow it

### Requirement: Out-of-scope items stay out

THE code pull request SHALL NOT change `mc01` or CloudNet, SHALL NOT add anycast for the proxies, and SHALL NOT add Pool, Group or Environment objects to Git.

#### Scenario: No standing custom resources

- **GIVEN** the code pull request
- **WHEN** the rendered `grus-dev` overlay is searched for kinds `Pool`, `Group` and `Environment` of group `grus.onelitefeather.net`
- **THEN** none are found
