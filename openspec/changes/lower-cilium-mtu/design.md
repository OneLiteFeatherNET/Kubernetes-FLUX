## Context

Cilium 1.20 is the CNI of `feather-core` with kube-proxy replacement, BGP control plane and LB-IPAM. Tunnel mode uses VXLAN on `ens18`, whose MTU is 1500. Cilium derives the pod MTU from the device MTU minus the 50-byte VXLAN overhead: 1500 gives pods 1450. The Helsinki path carries only 1400 (VLAN 4005 and the WG_ROSENAU tunnel), so a 1450 pod cannot send a full-size packet there.

Cilium sets the MTU on `cilium_vxlan`, `cilium_host` and each new pod veth. Existing pods keep their veth MTU until they are restarted. A change to the value therefore produces a mixed cluster for as long as the rollout runs.

Constraint: no Galera or CNPG outage and no connection drops on fr01 services during the rollout.

## Decision drivers

- No outage for stateful services during the mixed state (Galera, CNPG, Dragonfly).
- One pod MTU for both sites, so Helsinki nodes need no per-node exception.
- No router access on the Helsinki side; the fix must be on our cluster.
- Reversible with the same procedure.

## The mixed-MTU risk

**Background:** a rolling change with mixed MTUs between nodes broke MariaDB Galera during the Flannel host-gw incident. This design treats the mixed state as the main risk, not as a side effect.

**Why the mixed state is safe inside fr01 (reasoning, to be verified):**

- The fr01 underlay is 1500. It carries a 1450-pod packet (1500 with VXLAN) and a 1350-pod packet (1400 with VXLAN). Every packet any pod can emit fits every hop inside fr01.
- A new TCP connection takes its MSS from the receiving endpoint's MTU in the SYN. A 1450 pod talking to a 1350 pod therefore sends segments of at most 1350 bytes.
- Apps on TCP (Galera wsrep and IST/SST, CNPG, Dragonfly) are covered by this MSS behaviour for new connections.

**What MSS does not cover (why this is a gate, not an assumption):**

- An established TCP connection keeps the MSS negotiated before the change. If a 1350 pod or node then receives full-size 1450 packets, the forwarding decision on that node exceeds the 1350 egress MTU, and the packet is dropped with ICMP "fragmentation needed" when DF is set. Recovery depends on that ICMP reaching the sender and on TCP using it. The same applies to two pods on one node where one veth is still 1450 and the other 1350.
- UDP has no MSS. A UDP datagram larger than 1350 bytes towards a new pod is dropped unless the application reacts to the ICMP or to EMSGSIZE. Task 1.4 inventories UDP workloads.
- Galera group communication uses timeouts (evs). A PMTU black hole that stalls a member longer than the evs timeout would evict it. Galera is therefore checked before and after every restart step.
- Ceph (rook-ceph-fr01) runs on host network. Host-network traffic uses the node's ens18 MTU (1500), not the pod MTU, so Cilium's change does not touch Ceph. Task 1.3 verifies this for every Rook pod.

**Gate V1 (before the rollout):** an iperf3 stream from a pod with MTU 1450 to a pod with MTU 1350 keeps running while one node's agent restarts; no stall longer than 5 s (proposed; the Galera suspect timeout default is 5 s).

## Decisions

### 1. How to lower the MTU

**Decision:** option A.

**Options considered:**

- **A. Lower the Cilium device MTU to 1400 in the Helm values, restart Cilium agents node by node, then restart workloads so that pods reach 1350.** Chosen.
- **B. Clamp TCP MSS on the WireGuard path only.** Rejected. It covers TCP only; VXLAN-encapsulated UDP and ICMP still exceed the path and are fragmented or dropped. It also needs changes on the path, which we do not control on the Helsinki side.
- **C. Raise the Helsinki path MTU.** Rejected. A 1500 pod MTU needs a 1550 underlay for VXLAN. The Hetzner WAN at 1500 minus WireGuard overhead cannot carry that.

**Consequences of A:** every pod is replaced once. A mixed state lasts for the rollout window and is gated (see below). Rollback uses the same procedure with 1500.

### 2. Rollout order

**Decision:** change the value, then restart agents, then workloads, each step gated.

1. Stateful health check first: `GaleraReady=True` and `wsrep_cluster_size` 3 on the MariaDB CR. The mariadb-operator is not restarted unless `GaleraReady=True` has been checked.
2. Merge the value change. Confirm the Cilium HelmRelease upgrades (the ConfigMap changes, pods do not yet).
3. Restart one Cilium agent. Per-node gate: `cilium status` OK, two BGP sessions Established, a test pod on that node gets eth0 MTU 1350, and `ping -M do -s 1322` between pods on different nodes passes. Gate V1 runs during the first node. About 15 s per node.
4. Repeat node by node. Stop at the first failed gate.
5. Restart stateless workloads per namespace, respecting PodDisruptionBudgets.
6. Restart Galera pods one at a time. Between pods: `wsrep_local_state_comment` Synced and `wsrep_cluster_size` 3. The restart must not trigger SST; IST is expected.
7. CNPG clusters: restart replicas, switch over to a restarted replica, restart the former primary. No unplanned failover.
8. Dragonfly: one instance at a time, replicas first, replication in sync between steps.
9. Final scan: no pod interface with MTU other than 1350.

**Open question, answered in task 1.5:** whether the Cilium chart rolls the agent DaemonSet automatically when the ConfigMap changes (config checksum annotation). If it does, the default rolling update would restart agents without the per-node gate, and the agent DaemonSet's `maxUnavailable` must be set to 1 in the same commit as the value change.

### 3. Rollback

Set `MTU: 1500` and run the same procedure in reverse: agents node by node, then workloads, with the same gates. The mixed state appears again, so the gates apply in the same way.

## Open Questions

- **Agent DaemonSet auto-roll:** answered in task 1.5 (see decision 2).
- **Helsinki path MTU:** measured in task 1.6 with DF ping; must pass 1400 IP before any hel01 node joins.
- **UDP workloads above 1350 bytes:** from task 1.4; each one is TCP-only, or accepted with a reason.
