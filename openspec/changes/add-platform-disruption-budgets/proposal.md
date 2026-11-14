## Why

Two control-plane-adjacent components have no PodDisruptionBudget, so a node drain or an upgrade can take all of their replicas down at once:

- `cilium-operator` runs two replicas (`foundation/networking/clusters/feather-core/cilium/release.yaml`) but has no PDB. Cilium's operator handles IPAM and identity allocation. Losing both replicas during a drain stalls new identity and IP allocation for new pods.
- CoreDNS is managed by Talos, not by Flux. It has no PDB in this repository. Losing all CoreDNS pods during a node drain breaks name resolution for the whole cluster.

A PodDisruptionBudget only limits future voluntary evictions. It changes no running pod, restarts nothing and blocks no request, so it can be added without any outage.

## What Changes

- **(a) cilium-operator PDB** with `maxUnavailable: 1`, created through the Cilium Helm chart's PDB value if the chart version offers it, otherwise as a standalone PDB manifest selecting the operator pods. The choice is made in preflight (task 1.2).
- **(b) CoreDNS PDB** in namespace `kube-system`: `maxUnavailable: 1`, selector `k8s-app: kube-dns`. CoreDNS itself stays Talos-managed; only the PDB is owned by Flux.

**Commit type and scope:** the implementation ships as `feat(platform)`. Both PDBs are platform availability controls, one capability.

## Out of scope

- Changing CoreDNS replicas, its Talos manifest or its resources. Talos owns those.
- A PDB for any other component (for example, `snapshot-controller` and the Rook CSI control plugin already have PDBs in this repository).
- Changing the Cilium replica count (already 2).

## Impact

- New capability `disruption-budgets` (see `specs/disruption-budgets/spec.md`).
- Files: `foundation/networking/clusters/feather-core/cilium/release.yaml` (PDB value, if the chart supports it) or a new PDB manifest in the networking overlay; a PDB manifest for CoreDNS in `foundation/platform/clusters/feather-core/`, listed in its `kustomization.yaml`.
- Layers: `foundation-networking` (cilium) and `foundation-platform` (CoreDNS). Neither layer is renamed or split.
- Runtime impact: none on running pods. The cilium chart value, if used, is a Helm upgrade of the operator Deployment's metadata only where the chart renders a PDB; no operator pod restarts for the PDB itself. The operator's rolling update settings are not changed by this change.

**Pre-condition:** the CoreDNS PDB is only valid when CoreDNS runs at least two replicas. With one replica and `maxUnavailable: 1`, a drain can never evict the CoreDNS pod and the drain stalls. Task 1.1 checks the replica count before the PDB is merged.
