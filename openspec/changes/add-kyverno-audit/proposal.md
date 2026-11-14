## Why

`feather-core` has admission policies for three rules (`olf-require-image-tag`, `olf-require-memory-resources`, `olf-drop-capabilities`, `foundation/platform/base/workload-admission-policies`). There is no general view of which workloads break Pod Security or other baseline rules. Kyverno in audit mode produces that view as `PolicyReport` objects without rejecting anything, so the team sees the findings before any rule is ever enforced.

Audit mode is the whole scope. Enforcement is a non-goal of this change and would be a separate proposal after a measured period of reports.

## What Changes

- Kyverno installed from the official `kyverno/kyverno` chart, pinned, in namespace `kyverno`, through a `HelmRepository` source in the existing source set. Layer: `foundation-security` (in `foundation/security/`).
- Admission controller with three replicas and a PodDisruptionBudget (`maxUnavailable: 1`), so that a node drain never removes all webhook endpoints.
- Webhook `failurePolicy: Ignore` for every Kyverno webhook.
- Starter policy set, every policy `validationFailureAction: Audit`:
  - Pod Security Standards `baseline` and `restricted` (both as Audit).
  - Disallow the `latest` image tag (Audit).
  - Require `requests` and `limits` on memory (Audit; the same rule as the existing VAP, kept as a second opinion).
- Background scanning on, reports in `PolicyReport` and `ClusterPolicyReport` objects, exported to Mimir through a ServiceMonitor, so a Grafana panel can show the count per namespace.
- Kyverno's own namespace is excluded from its policies.

**Commit type and scope:** the implementation ships as `feat(security)`.

## Out of scope and non-goals

- **Enforce mode is a non-goal.** No policy in this change sets `validationFailureAction: Enforce`. Switching a policy to Enforce is a separate proposal that needs a measured audit period, a per-namespace exception list, and its own rollout plan.
- No mutating policies. Kyverno mutation is not part of this change.
- No replacement of the existing three ValidatingAdmissionPolicies. They keep working as they are.

## Impact

- New capability `policy-audit` (see `specs/policy-audit/spec.md`).
- New files: `foundation/security/base/kyverno/` (namespace, HelmRelease, policies) and `foundation/security/clusters/feather-core/kyverno/` (overlay, PDB values, ServiceMonitor). The security layer's entry `foundation/layers/feather-core/security/kustomization.yaml` lists the new overlay.
- Layer `foundation-security` depends on `foundation-storage`; no change to the dependency graph.
- Runtime impact: a new webhook. This is the one place where a request could be affected, so the global constraint is applied strictly: `failurePolicy: Ignore` means that if every Kyverno pod is down, the API server admits the request without Kyverno's answer. No request is blocked by Kyverno in this change, because nothing is enforced. The PDB keeps at least two replicas during voluntary disruption.
