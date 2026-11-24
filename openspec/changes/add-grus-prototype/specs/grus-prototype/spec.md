## ADDED Requirements

### Requirement: Namespace with Pod Security Admission labels

THE namespace `grus-dev` SHALL set the Pod Security Admission label `enforce` to `baseline` and the labels `warn` and `audit` to `restricted`.

#### Scenario: Namespace carries the labels

- **GIVEN** the code pull request is applied and `products-dev` reconciles
- **WHEN** the namespace `grus-dev` is inspected
- **THEN** `pod-security.kubernetes.io/enforce` is `baseline`
- **AND** `pod-security.kubernetes.io/warn` and `pod-security.kubernetes.io/audit` are `restricted`

#### Scenario: Privileged pod is refused

- **GIVEN** a Pod in `grus-dev` that sets `privileged: true`
- **WHEN** the Pod is created
- **THEN** the API server rejects it under the `baseline` policy

### Requirement: Pinned source and revision strategy

THE `GitRepository` `grus` SHALL pin `ref.commit` to one commit and SHALL NOT track a branch, and THE `HelmRelease` `grus` SHALL set `reconcileStrategy: Revision`, `interval: 10m0s` and `retries: 3` for install and upgrade remediation.

#### Scenario: Source is pinned

- **GIVEN** the repository is searched for the source `grus`
- **WHEN** its `spec.ref` is read
- **THEN** it contains `commit` and no `branch` or `tag`

#### Scenario: Chart changes reach the cluster

- **GIVEN** a new commit in the Grus repository and a pull request that moves `ref.commit` to it
- **WHEN** Flux reconciles the HelmRelease after the merge
- **THEN** the rendered chart reflects the new commit, without a change to `Chart.yaml` version

### Requirement: Fixed image tags

EVERY image of the prototype SHALL use a tag of the form `prototype-<sha>` from the Harbor project `onelitefeather`, and no image SHALL use the tag `latest` or a tag without a commit.

#### Scenario: Image tag matches the pinned commit

- **GIVEN** the pinned `ref.commit` is `<sha>`
- **WHEN** the HelmRelease values are read
- **THEN** every image tag is `prototype-<sha>` with the same `<sha>`

### Requirement: Controller RBAC limited to its namespace

THE controller ServiceAccount SHALL hold a namespaced `Role` in `grus-dev` that grants `create`, `get` and `delete` on `pods`, `pods/log`, `pods/attach` and `secrets` only, and THE repository SHALL NOT bind it through a `ClusterRole` or `ClusterRoleBinding`.

#### Scenario: Controller cannot act outside its namespace

- **GIVEN** the controller ServiceAccount in `grus-dev`
- **WHEN** it attempts to list pods in namespace `menura`
- **THEN** the API server denies the request

#### Scenario: No cluster-scoped binding exists

- **GIVEN** the code pull request is merged
- **WHEN** the repository is searched for `ClusterRoleBinding` that references the controller
- **THEN** no match exists

### Requirement: No external exposure

THE code pull request SHALL NOT add a `Service` of type `LoadBalancer`, an `HTTPRoute`, a `Certificate`, or a DNAT rule for `grus-dev`.

#### Scenario: Prototype is not reachable from outside

- **GIVEN** the prototype is running in `grus-dev`
- **WHEN** a client outside the cluster connects to the controller or proxy address
- **THEN** the connection is refused or no route exists

#### Scenario: Port-forward reaches the controller

- **GIVEN** the operator runs `kubectl -n grus-dev port-forward svc/grus 8080:8080`
- **WHEN** the operator requests `/readyz` on `localhost:8080`
- **THEN** the controller answers with HTTP 200

### Requirement: Runtime pods comply with the namespace policy

THE controller SHALL create game pods that satisfy the Pod Security `baseline` policy of `grus-dev` and SHALL set the memory request equal to the memory limit on every container.

#### Scenario: Game pod is admitted

- **GIVEN** the controller creates a lobby pod with a memory limit of 768Mi
- **WHEN** the API server evaluates the pod
- **THEN** the pod is admitted
- **AND** its memory request is 768Mi

#### Scenario: Pod without memory limit is not created

- **GIVEN** a pod template without a memory limit
- **WHEN** the controller attempts to create it
- **THEN** the controller does not create the pod and reports an error event

### Requirement: Instance limit

THE HelmRelease values SHALL set the controller limit `maxInstances` to `10` for `grus-dev`.

#### Scenario: Scale-up beyond the limit is refused

- **GIVEN** ten running instances in `grus-dev`
- **WHEN** a pool requests an eleventh instance
- **THEN** the controller does not create a pod and records the refusal in its event stream

### Requirement: Secrets are encrypted in Git

THE secrets of `grus-dev` SHALL be stored only as SOPS-encrypted files with a `.sops.env` suffix, and THE Harbor pull secret SHALL be the encrypted file already used by the other products.

#### Scenario: No plaintext secret in the change

- **GIVEN** the code pull request
- **WHEN** its files are searched for the admin token or the Velocity forwarding secret in plaintext
- **THEN** no match exists
- **AND** `grus-dev-secrets.sops.env` contains `sops:` metadata

### Requirement: Suspend path for a blocked prototype

IF the HelmRelease `grus` keeps `products-dev` from becoming Ready, THEN a follow-up pull request SHALL set `spec.suspend: true` on the HelmRelease, and `products-dev` SHALL become Ready at the same revision.

#### Scenario: Suspended release no longer blocks the layer

- **GIVEN** `products-dev` is blocked by the HelmRelease `grus`
- **WHEN** the follow-up pull request with `suspend: true` is merged and reconciled
- **THEN** the layer `products-dev` reports Ready without waiting for `grus`

### Requirement: Layer removal rolls the prototype back

THE removal of the line `../../../grus/clusters/feather-core/grus-dev` from `products/layers/feather-core/dev/kustomization.yaml` SHALL remove the namespace `grus-dev` and its Flux-managed objects through pruning.

#### Scenario: Rollback by removing the layer line

- **GIVEN** the prototype is deployed
- **WHEN** a pull request removes the layer line and is merged
- **THEN** the Flux-managed objects of `grus-dev` are pruned
