## Why

Nodes in Helsinki are reached over VLAN 4005 (MTU 1400) and the WG_ROSENAU WireGuard tunnel. Pods on fr01 nodes get MTU 1450 from Cilium's VXLAN tunnel, which needs a 1500 underlay. On the Helsinki path those pods would need fragmentation. Lowering the Cilium device MTU from 1500 to 1400 gives every pod MTU 1350 (VXLAN overhead 50 bytes), which fits the Helsinki path and lets fr01 and Helsinki nodes share one pod MTU.

This change is a prerequisite for the talos-cluster change `add-helsinki-ingress-nodes` and for the Kubernetes-FLUX change `add-helsinki-anycast-ingress`. Neither can join the cluster with a different MTU.

**Current state:** Cilium 1.20 runs in `routingMode: tunnel` with `tunnelProtocol: vxlan`, `MTU: 1500` and `devices: [ens18]` (`foundation/networking/clusters/feather-core/cilium/release.yaml`, HelmRelease `kube-system/cilium`). Node interfaces are 1500; pods are 1450.

**Commit type and scope:** the code ships as `feat(networking)`. It adds a site-wide network capability (Helsinki); it is not a bug fix.

## What Changes

- `MTU: 1500` becomes `MTU: 1400` in the Cilium HelmRelease values. No other value changes.
- Cilium agents restart one node at a time. Their tunnel and host devices take MTU 1400; new pod interfaces take MTU 1350.
- Workloads restart in a controlled order so that existing pods (MTU 1450) are replaced by pods with MTU 1350. Until then the cluster runs in a mixed state, which is the main risk of this change (see design).
- Verification gates: pod MTU scan, throughput re-measurement, Galera, CNPG, Dragonfly and Ceph health checks.
- Outline network page updated with the MTU values and the rollback runbook.
- Not changed: node interfaces (`ens18` stays 1500), Cilium features, the Helsinki path itself.

## Out of scope

- Adding Helsinki nodes or ingress (other changes).
- Raising the Helsinki path MTU (rejected in design, decision 1).
- TCP MSS clamping on the WireGuard path (rejected in design, decision 1).
- Jumbo frames on the underlay.

## Impact

- New capability `pod-network-mtu` (see `specs/pod-network-mtu/spec.md`).
- One value changed in `foundation/networking/clusters/feather-core/cilium/release.yaml`. No new objects, no new layer, no rename.
- Runtime impact: every existing pod is replaced once during the rollout. Cilium agent restarts take about 15 s per node and do not drop established traffic in the expected case; the verification gate checks this per node. Stateful services (Galera, CNPG, Dragonfly) restart one instance at a time with health checks in between.
