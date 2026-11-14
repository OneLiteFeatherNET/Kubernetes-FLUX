## Context

The cluster enforces three rules through ValidatingAdmissionPolicies in `foundation/platform/base/workload-admission-policies/`. There is no broader policy engine and no report of existing non-compliance. The cluster runs 10 nodes (3 control-plane, 3 storage, 4 xl), Cilium as CNI, and the Kubernetes API server already serves about 90 requests per second from workload service accounts.

Constraint for this change: deployable with no outage and no connection drops. Kyverno adds admission webhooks. A webhook that fails or is slow can stall every matching API request. The two protections are `failurePolicy: Ignore` and an audit-only policy set, which means Kyverno never denies a request.

## Goals / Non-Goals

**Goals**

- A per-namespace view of Pod Security and hygiene findings in Grafana.
- A set of policies that can later be promoted to Enforce one by one.
- No request is denied by Kyverno in this change.

**Non-Goals**

- Enforce mode (explicit).
- Mutating policies.
- Replacing the existing VAPs.

## Decision drivers

- Zero denied requests in this change.
- A failing Kyverno must not stall the API server.
- Findings must be visible without anyone running `kubectl`.

## Decisions

### 1. Kyverno from the official chart

**Decision:** `kyverno/kyverno` chart, pinned, through a `HelmRepository` in the sources set. The chart version is chosen in task 1.2.

**Rejected alternative:** the ValidatingAdmissionPolicy API alone. It is what the cluster already uses for three rules. It has no background report of existing objects in this form, and its policies are harder to share as a starter set.

### 2. Audit only, `failurePolicy: Ignore`

**Decision:** every ClusterPolicy has `validationFailureAction: Audit`. Every Kyverno webhook has `failurePolicy: Ignore`. Both are set in the Helm values, and task 4.3 verifies them on the live `ValidatingWebhookConfiguration` objects.

**Why:** `Audit` means the policy result is recorded and the request is admitted. `Ignore` means that if no Kyverno pod answers, the API server admits the request without waiting. Together they guarantee that Kyverno cannot block or stall an existing deployment.

**Consequences:** a policy that would have failed is visible only as a `PolicyReport` entry. This is the desired state for this change.

### 3. HA admission controller

**Decision:** `admissionController.replicas: 3` and a PodDisruptionBudget `maxUnavailable: 1` on the admission controller. Three replicas spread across the xl nodes by pod anti-affinity (`preferred`, same pattern as the alloy-receiver release).

**Consequences:** a node drain evicts at most one webhook pod at a time. A PDB only affects future evictions; the change does not evict any running pod.

**Resource check:** three admission pods add memory requests on nodes that are already well booked (`fr01-wrk-xl-03` and `fr01-wrk-xl-04` near 96 to 100 percent memory requests, per the IsukuVerlag runners change). The requests are recorded in task 1.1, and the replicas are reduced to two if no node can take three.

### 4. Starter policies

**Decision:** a `ClusterPolicy` set, all `Audit`, in `foundation/security/base/kyverno/policies/`:

- Pod Security Standards `baseline` (Audit).
- Pod Security Standards `restricted` (Audit).
- Disallow `latest` tags (Audit).
- Require memory `requests` and `limits` (Audit).

Every policy excludes the namespaces `kube-system`, `flux-system` and `kyverno`, and excludes Talos-managed system pods. The exclusion list is written in the policy manifests so that it is reviewed with them.

**Consequences:** Kyverno's policy list is a superset of the three VAPs in some areas. This is intentional for audit; the duplication is removed when a policy is promoted.

### 5. Reporting

**Decision:** background scan on; the `PolicyReport` objects are scraped through a ServiceMonitor into Mimir. A Grafana dashboard panel showing failing objects per namespace is a follow-up, not part of this change.

### 6. Placement

**Decision:** base `foundation/security/base/kyverno/`, overlay `foundation/security/clusters/feather-core/kyverno/`, listed in `foundation/layers/feather-core/security/kustomization.yaml`. No new layer.

## Risks / Trade-offs

- **Webhook latency:** every matching API request now calls Kyverno. Mitigation: `Ignore` on timeout and failure, and a `timeoutSeconds` value kept low (set in the chart values, verified in task 4.3).
- **Noise:** the first scan produces many findings in older namespaces. Mitigation: findings are read, not acted on, in this change.
- **Accidental Enforce:** a later edit could switch a policy to Enforce. Mitigation: a CI check (task 3.5) fails the PR if any `ClusterPolicy` in `foundation/security/base/kyverno/` contains `validationFailureAction: Enforce`.

## Migration Plan

1. Measure node memory requests (task 1.1) and confirm three replicas fit.
2. Merge the code PR. Flux installs Kyverno with `Ignore` and `Audit`.
3. Verify the webhook configurations and that no request is denied (task 4.3).
4. Read the first reports, record the findings count per namespace in the rollout notes.

**Rollback:** remove the security overlay entry; the policies and webhooks go with the HelmRelease. Existing objects are unaffected.

## Open Questions

- **Which namespaces are excluded by default** beyond the three named above? Decision needed after the first report.
- **Enforce promotion criteria:** what finding count per namespace is acceptable before a policy goes to Enforce? A separate proposal decides this.
- **Kyverno chart version** chosen at rollout and recorded in the code PR.
