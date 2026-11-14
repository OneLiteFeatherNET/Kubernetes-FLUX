## ADDED Requirements

### Requirement: Audit-only policies

EVERY Kyverno ClusterPolicy installed by this change SHALL set `validationFailureAction: Audit`, and THE repository SHALL NOT contain a policy with `validationFailureAction: Enforce` under `foundation/security/base/kyverno/`.

#### Scenario: Non-compliant pod is admitted

- **GIVEN** a Pod that violates the Pod Security `baseline` policy
- **WHEN** it is created in a namespace not excluded by the policy
- **THEN** the API server admits the Pod
- **AND** a `PolicyReport` entry records the violation

#### Scenario: CI rejects an enforcing policy

- **GIVEN** a change that sets `validationFailureAction: Enforce` on a policy under `foundation/security/base/kyverno/`
- **WHEN** the CI validation runs
- **THEN** the check fails

### Requirement: Fail-open webhooks

EVERY Kyverno webhook configuration SHALL set `failurePolicy: Ignore`.

#### Scenario: Webhook failure does not block requests

- **GIVEN** all Kyverno admission pods are unavailable
- **WHEN** a Deployment is created in a namespace
- **THEN** the API server admits the Deployment
- **AND** no error from Kyverno is returned to the client

### Requirement: Admission controller availability

THE Kyverno admission controller SHALL run at least two replicas, and a PodDisruptionBudget SHALL allow at most one unavailable replica.

#### Scenario: Node drain keeps one webhook endpoint

- **GIVEN** three admission controller replicas on three nodes
- **WHEN** one node is drained
- **THEN** at least two replicas remain available

### Requirement: Policy reports are visible in Mimir

THE cluster SHALL export Kyverno `PolicyReport` counts to Mimir through a ServiceMonitor.

#### Scenario: Report metrics are scraped

- **GIVEN** Kyverno is running with background scanning enabled
- **WHEN** the metrics endpoint of the reports controller is queried
- **THEN** the series for `policy_report_result` exist in Mimir

### Requirement: Exclusions for system namespaces

EVERY starter policy SHALL exclude the namespaces `kube-system`, `flux-system` and `kyverno`.

#### Scenario: System namespace produces no findings

- **GIVEN** a Pod in namespace `flux-system` that violates a starter policy
- **WHEN** the background scan runs
- **THEN** no `PolicyReport` entry is written for that Pod
