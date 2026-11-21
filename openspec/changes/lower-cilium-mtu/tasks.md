## 1. Preparation

- [ ] 1.1 Record the throughput baseline: iperf3 pod-to-pod between xl-01 and xl-02, single stream and 4 streams, plus TCP_RR P50. Verification: figures stored in the code PR description (2026-11-06 baseline: 11.3 Gbit/s single stream, 26.8 Gbit/s 4 streams, P50 173 µs).
- [ ] 1.2 Check the stateful health gate: MariaDB `GaleraReady=True` and `wsrep_cluster_size` 3; all CNPG clusters healthy without lag; Dragonfly healthy. Verification: output recorded; no operator restart while `GaleraReady` is not `True`.
- [ ] 1.3 Confirm that all rook-ceph-fr01 pods run with host network. Verification: `hostNetwork` is `true` for mons, OSDs, mgr, MDS and RGW.
- [ ] 1.4 Inventory UDP workloads and their datagram sizes above 1350 bytes. Verification: list in the code PR; each entry is TCP-only or has an accepted reason.
- [ ] 1.5 Render the Cilium chart with `MTU: 1400` and check whether the agent DaemonSet rolls on the ConfigMap change, and with which `maxUnavailable`. Verification: result recorded in design, open questions; the value change includes `maxUnavailable: 1` if the DaemonSet rolls.
- [ ] 1.6 Measure the Helsinki path MTU from an fr01 node with DF ping (1372-byte payload, 1400 IP) over VLAN 4005 and WG_ROSENAU. Verification: no fragmentation-needed reply; 0 % loss.
- [ ] 1.7 Run gate V1 (iperf3 across a 1450/1350 pair while an agent restarts) on a test node before the rollout. Verification: no stall longer than 5 s.

## 2. Change

- [ ] 2.1 Set `MTU: 1400` in `foundation/networking/clusters/feather-core/cilium/release.yaml`; the diff is one line (plus the agent update strategy only if task 1.5 requires it). Verification: `git diff` shows only these lines.
- [ ] 2.2 Confirm the Cilium layer in which the overlay sits (layer name to verify) is not renamed or split. Verification: the layer CR is unchanged.
- [ ] 2.3 Run `./.github/scripts/validate.sh` and `kubectl kustomize` on the Cilium overlay. Verification: both exit 0.

## 3. Rollout (after the code PR is merged)

- [ ] 3.1 Confirm the Cilium HelmRelease is upgraded at the new revision and the ConfigMap shows `MTU: 1400`. Verification: `flux get helmrelease` Ready; existing pods still 1450 until restarted.
- [ ] 3.2 Restart one Cilium agent. Verification: `cilium status` OK, two BGP sessions Established, a new test pod gets MTU 1350, `ping -M do -s 1322` passes between nodes, gate V1 holds.
- [ ] 3.3 Repeat 3.2 node by node; stop at the first failed gate. Verification: every agent reports the 1400 device MTU.
- [ ] 3.4 Restart stateless workloads per namespace in a controlled order. Verification: rollouts complete; PodDisruptionBudgets not violated.
- [ ] 3.5 Restart Galera pods one at a time. Verification: between pods `wsrep_local_state_comment` is Synced and `wsrep_cluster_size` is 3; no SST.
- [ ] 3.6 Restart CNPG instances: replicas first, switchover, then the former primary. Verification: cluster healthy after each step; no unplanned failover.
- [ ] 3.7 Restart Dragonfly instances one at a time. Verification: replication in sync after each step.
- [ ] 3.8 Run a read-only scan that lists every pod interface with MTU other than 1350. Verification: empty list.

## 4. Verification

- [ ] 4.1 Re-run the task 1.1 iperf3 and TCP_RR measurements at pod MTU 1350 and record the delta. Verification: a throughput drop above 10 % (proposed threshold, to confirm) blocks closing the change.
- [ ] 4.2 Check Galera after the rollout: `GaleraReady=True`, `wsrep_cluster_size` 3, no IST/SST triggered by the rollout. Verification: recorded values.
- [ ] 4.3 Check CNPG and Dragonfly after the rollout. Verification: healthy, no replication lag.
- [ ] 4.4 Check Ceph after the rollout: health OK, no increase in slow ops against the baseline. Verification: `ceph status` and the baseline comparison recorded.
- [ ] 4.5 Compare firing alerts before and after the rollout. Verification: no new firing alert that is not explained by the rollout window.

## 5. Documentation

- [ ] 5.1 Update the Outline network page (collection Infrastruktur, Kubernetes-FLUX) with the MTU values (node 1500, Cilium device 1400, pod 1350, Helsinki path 1400), the mixed-state gates and the rollback procedure. Verification: page lists all three values and the rollback steps.

## 6. Code pull request

- [ ] 6.1 Open the code pull request under the title `feat(networking): lower the cilium mtu to 1400 for the helsinki site`; no files under `openspec/` in it. Verification: PR title passes the pr-lint Conventional Commits check.
