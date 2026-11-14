## Context

The cluster runs Talos v1.13.9 on 10 nodes. Node drains happen during Talos upgrades and Kubernetes upgrades, and the descheduler (`foundation/platform/base/descheduler`) also evicts pods. Both use the Eviction API, which honours PodDisruptionBudgets.

`cilium-operator` runs two replicas, set in `foundation/networking/clusters/feather-core/cilium/release.yaml` (`operator.replicas: 2`), and has no PDB. CoreDNS is installed and reconciled by Talos. It is not in any Flux path. The repository already has PDBs for `snapshot-controller` (`foundation/storage/clusters/feather-core/snapshot-controller/pdb.yaml`) and the Rook CSI control plugin (`foundation/storage/clusters/feather-core/rook-operator/pdb.yaml`), which show the house pattern.

Constraint for this change: deployable with no outage and no connection drops. A PDB affects only future voluntary evictions. It does not evict, restart or change any running pod, and it is not a webhook. The only way it can stall an operation is a drain that cannot evict, which the CoreDNS replica pre-condition rules out.

## Goals / Non-Goals

**Goals**

- Voluntary evictions never remove all replicas of the cilium operator or of CoreDNS at once.
- Each budget is verified before it is merged.

**Non-Goals**

- Changing replica counts.
- Owning CoreDNS in Flux.

## Decision drivers

- No change to running pods.
- A budget that stalls a drain is worse than none, so the replica count must be verified.
- Use the chart's own mechanism where it exists.

## Decisions

### 1. cilium-operator: chart value or standalone PDB

**Decision:** prefer the Cilium chart value for the operator PDB if `cilium` 1.20.4 renders one (checked with `helm show values`, task 1.2). Otherwise a standalone `PodDisruptionBudget` in the networking cluster overlay selecting the operator pods by their labels (task 1.3).

**Rejected alternative:** a PDB via a `kustomize` patch on the Helm output. Not possible without rendering the chart in Kustomize, and the chart value is the supported path.

**Consequences:** either path creates only a PDB object. The Deployment's pod template does not change, so no operator pod is restarted by this change.

### 2. CoreDNS: standalone PDB in kube-system

**Decision:** a `PodDisruptionBudget` in namespace `kube-system`, `maxUnavailable: 1`, selector `k8s-app: kube-dns`, in `foundation/platform/clusters/feather-core/coredns-pdb/` and listed in the platform layer entry.

**Consequences:**

- Talos owns the CoreDNS Deployment. Flux owns only the PDB object. The PDB is not part of the Talos manifest, so Talos does not delete it on its own reconcile. Task 1.5 confirms this on the cluster.
- `deletionPolicy: Orphan` on the layer means that removing the layer later does not delete the PDB.

### 3. Pre-condition: at least two CoreDNS replicas

**Decision:** `maxUnavailable: 1` is only valid for two or more replicas. The change is merged only when `kubectl -n kube-system get deploy coredns` shows at least two ready replicas (task 1.1). With one replica, `maxUnavailable: 1` would allow the only pod to be evicted, and a stricter `minAvailable: 1` would block every drain of its node. Neither is acceptable as a silent default, so the replica count is raised on the Talos side first and this PDB waits for it.

**Consequences:** the pre-condition is a gate, not a best effort. It is the only risk of this change, and it is checked before merge.

## Risks / Trade-offs

- **Drain stall with one CoreDNS replica:** blocked by the pre-condition gate (decision 3).
- **Chart value not present for cilium 1.20.4:** falls back to the standalone PDB; no change in behaviour.
- **Talos reconcile removes the PDB:** checked in task 1.5; if Talos prunes it, the PDB is moved to a Talos-side change and this change is reduced to the cilium part.

## Migration Plan

1. Preflight: read CoreDNS replica count; check the chart value; check Talos does not prune foreign objects in `kube-system`.
2. Merge the code PR. Flux creates both PDBs.
3. Verify with `kubectl get pdb -A` that the budgets report `allowedDisruptions: 1`.
4. Verify with `kubectl drain --dry-run=server` on one control-plane node that no eviction is refused by these budgets for the current replica counts.

**Rollback:** delete the PDB objects through the Flux layer; no pod changes to undo.

## Open Questions

- **Does Talos prune foreign objects in `kube-system`?** Task 1.5. Decides whether the CoreDNS PDB stays in Flux.
- **Does the Cilium chart 1.20.4 expose an operator PDB value?** Task 1.2. Decides between the chart value and the standalone PDB.
