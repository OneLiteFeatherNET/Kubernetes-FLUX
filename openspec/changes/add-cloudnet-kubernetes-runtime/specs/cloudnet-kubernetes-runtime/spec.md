## ADDED Requirements

### Requirement: Namespace with Pod Security Admission labels

THE namespace `cloudnet` SHALL set the Pod Security Admission label `enforce` to `baseline` and the labels `warn` and `audit` to `restricted`.

#### Scenario: Namespace carries the labels

- **GIVEN** the code pull request is applied and the layer `products` reconciles
- **WHEN** the namespace `cloudnet` is inspected
- **THEN** `pod-security.kubernetes.io/enforce` is `baseline`
- **AND** `pod-security.kubernetes.io/warn` and `pod-security.kubernetes.io/audit` are `restricted`

#### Scenario: Privileged pod is refused

- **GIVEN** a pod in `cloudnet` that sets `privileged: true`
- **WHEN** the pod is created
- **THEN** the API server rejects it under the `baseline` policy

### Requirement: Node RBAC limited to its namespace

THE ServiceAccount `cloudnet-node` SHALL be bound through a namespaced `Role` and `RoleBinding` in `cloudnet` whose rules equal the rules of `deploy/rbac.yaml` of the cloudnet-kubernetes module, and THE repository SHALL NOT bind it through a `ClusterRole` or `ClusterRoleBinding`. THE Role SHALL NOT grant `pods/exec` or `pods/portforward`.

#### Scenario: Node can manage its pods

- **GIVEN** the ServiceAccount `cloudnet-node` with the token of the Secret in `cloudnet`
- **WHEN** it creates, lists and deletes pods in `cloudnet`
- **THEN** the API server allows each request

#### Scenario: Node cannot act outside its namespace

- **GIVEN** the ServiceAccount `cloudnet-node`
- **WHEN** it attempts to list pods in namespace `menura`
- **THEN** the API server denies the request

#### Scenario: No cluster-scoped binding exists

- **GIVEN** the code pull request is merged
- **WHEN** the repository is searched for a `ClusterRoleBinding` that references `cloudnet-node`
- **THEN** no match exists

### Requirement: Long-lived token without a value in Git

THE repository SHALL define one `Secret` of type `kubernetes.io/service-account-token` for `cloudnet-node` in `cloudnet`, and THE repository SHALL NOT contain a token value, a kubeconfig or any other credential of the node.

#### Scenario: Token Secret is empty in Git

- **GIVEN** the code pull request
- **WHEN** its manifests are searched for a `token:` key with data
- **THEN** no match exists

### Requirement: Headless service for per-pod DNS

THE cluster SHALL provide a Service `cloudnet-services` in `cloudnet` with `clusterIP: None`, `publishNotReadyAddresses: true`, selector `cloudnet.eu/component: service` and the TCP port 25565 named `game`.

#### Scenario: Pod receives a stable DNS record before it is ready

- **GIVEN** a pod in `cloudnet` with `spec.hostname` and `spec.subdomain` set to `cloudnet-services`
- **WHEN** the pod is created but not yet ready
- **THEN** the name `<hostname>.cloudnet-services.cloudnet.svc.cluster.local` resolves to the pod address

### Requirement: Staging bucket as an ObjectBucketClaim

THE cluster SHALL provide an `ObjectBucketClaim` in `cloudnet` for the staging bucket, using the storage class `ceph-bucket-fr01`, and THE bucket SHALL NOT be the bucket `cloudnet-templates`.

#### Scenario: Claim is bound

- **GIVEN** the code pull request is applied
- **WHEN** the claim is inspected
- **THEN** its phase is `Bound`
- **AND** the bucket name differs from `cloudnet-templates`

### Requirement: Priority class below the platform tier

THE cluster SHALL define the PriorityClass `cloudnet-workload` with a value lower than 900000000 (`feather-platform`), and THE pods of `cloudnet` SHALL NOT use `feather-platform`.

#### Scenario: Runtime pod runs at the cloudnet priority

- **GIVEN** a runtime pod created by the node
- **WHEN** its spec is read
- **THEN** `priorityClassName` is `cloudnet-workload`

#### Scenario: Platform pod is not displaced

- **GIVEN** an edge node under memory pressure with a Cilium agent and a runtime pod
- **WHEN** the kubelet evicts pods
- **THEN** the runtime pod is evicted before the Cilium agent

### Requirement: Runtime pods target the Helsinki edge nodes

EVERY pod created by the node in `cloudnet` SHALL set the node selector `topology.kubernetes.io/zone: hel01` and the toleration `node-role.feather/edge=true:NoSchedule`, and EVERY container SHALL set the memory request equal to the memory limit.

#### Scenario: Pod is admitted on an edge node

- **GIVEN** a runtime pod with memory limit 768Mi
- **WHEN** the scheduler places it
- **THEN** it runs on a node with zone `hel01`
- **AND** its memory request is 768Mi

#### Scenario: Pod without memory limit is refused

- **GIVEN** a runtime pod whose container sets no memory limit
- **WHEN** the node attempts to create it
- **THEN** the pod is not created and the node records an error event

### Requirement: Internal team proxy on a pinned address

THE cluster SHALL expose the team proxy pods through a Service `cloudnet-team-proxy` of type `LoadBalancer` on TCP port 25565, with the address pinned through `lbipam.cilium.io/ips` to one address of the pool `lb-fr01` that no other Service holds, with the label `bgp.onelite.feather/site: hel01` and with `externalTrafficPolicy: Local`.

#### Scenario: Address is pinned and not shared

- **GIVEN** the code pull request
- **WHEN** the Service `cloudnet-team-proxy` is read
- **THEN** `lbipam.cilium.io/ips` names one address inside `10.200.90.1` to `10.200.90.15`
- **AND** no other Service in the cluster holds that address

#### Scenario: Client address is kept

- **GIVEN** a Helsinki client connected to the team proxy address
- **WHEN** a proxy pod on an edge node receives the connection
- **THEN** the source address of the connection is the client address

### Requirement: No public exposure

THE code pull request SHALL NOT add an `HTTPRoute`, a `Certificate`, a DNAT rule or a public hostname for `cloudnet`, and SHALL NOT change any file under `cilium-bgp/` or `gateway/`.

#### Scenario: Team proxy is not reachable from the public internet

- **GIVEN** the team proxy is running
- **WHEN** a client outside the internal network and the VPN connects to its address
- **THEN** the connection is refused or no route exists

### Requirement: Default-deny network policies

THE namespace `cloudnet` SHALL define a NetworkPolicy that denies all ingress and egress by default, and explicit NetworkPolicies that allow only DNS egress, the team proxy ingress on TCP 25565, the in-namespace game traffic, the staging bucket egress and the egress to `mc01` listed in the design.

#### Scenario: Default deny is present

- **GIVEN** the code pull request
- **WHEN** its NetworkPolicies are read
- **THEN** a policy with an empty pod selector and both `Ingress` and `Egress` in `policyTypes` exists

#### Scenario: Policies are audit only today

- **GIVEN** Cilium runs in policy audit mode
- **WHEN** a pod in `cloudnet` violates a policy
- **THEN** the connection is allowed and the violation is recorded in audit logs

### Requirement: No plaintext credentials in Git

THE repository SHALL NOT contain any token, kubeconfig, access key or secret key of the cloudnet node in plaintext. IF a credential file is needed in Git, THEN it SHALL be a SOPS-encrypted file with a `.sops.*` suffix.

#### Scenario: Change contains no plaintext credential

- **GIVEN** the code pull request
- **WHEN** its files are searched for `accessKey`, `secretKey`, `AWS_SECRET_ACCESS_KEY` or a `kubeconfig` with data
- **THEN** no plaintext match exists
