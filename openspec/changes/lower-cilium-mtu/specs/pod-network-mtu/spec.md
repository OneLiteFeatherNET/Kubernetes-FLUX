## ADDED Requirements

### Requirement: Uniform pod MTU 1350 after rollout

THE cluster SHALL run every pod network interface with MTU 1350 once the rollout is complete.

#### Scenario: New pod gets the target MTU

- **GIVEN** the rollout is complete
- **WHEN** a test pod is started on any node and `eth0` is read
- **THEN** its MTU is 1350

#### Scenario: No pod keeps the old MTU

- **GIVEN** the rollout is complete
- **WHEN** the read-only scan from task 3.8 runs
- **THEN** no pod interface has MTU 1450

### Requirement: Cilium device MTU 1400 with unchanged node interfaces

THE Cilium agent on every node SHALL configure its tunnel and host devices with MTU 1400, and THE node interface `ens18` SHALL keep MTU 1500.

#### Scenario: Node interface is unchanged

- **GIVEN** a node whose Cilium agent was restarted
- **WHEN** the MTU of `ens18` is read
- **THEN** it is 1500

### Requirement: Galera quorum during the mixed state

WHILE pods with MTU 1450 and pods with MTU 1350 coexist, THE cluster SHALL keep Galera `wsrep_cluster_size` at 3 and every Galera node `Synced`.

#### Scenario: Galera stays in quorum between restarts

- **GIVEN** the rollout is in the mixed state
- **WHEN** the wsrep state is read after each Galera pod restart
- **THEN** `wsrep_cluster_size` is 3
- **AND** `wsrep_local_state_comment` is `Synced`

### Requirement: No stall on established TCP across the mixed state

WHILE pods with MTU 1450 and pods with MTU 1350 coexist, THE cluster SHALL keep an established TCP stream between them free of stalls.

#### Scenario: Stream survives an agent restart

- **GIVEN** an iperf3 stream from a pod with MTU 1450 to a pod with MTU 1350
- **WHEN** the Cilium agent on the receiving node restarts
- **THEN** the stream does not stall for longer than 5 seconds
- **AND** the stream is still running after the restart

### Requirement: Helsinki nodes use the fr01 MTU

WHEN a node in zone hel01 joins, THE cluster SHALL configure it with the same Cilium MTU (1400) as the fr01 nodes.

#### Scenario: Helsinki node matches fr01

- **GIVEN** a node in zone hel01 has joined
- **WHEN** the Cilium device MTU is read on that node and on an fr01 node
- **THEN** both values are 1400

### Requirement: Path MTU verified before a Helsinki node joins

BEFORE a node in zone hel01 joins, THE cluster SHALL verify that a 1400-byte IP packet with DF set passes between fr01 and hel01 without fragmentation.

#### Scenario: DF ping passes across the Helsinki path

- **GIVEN** an fr01 node and a hel01 node address
- **WHEN** `ping -M do -s 1372` runs from the fr01 node to the hel01 node address
- **THEN** the ping succeeds with 0 % loss
- **AND** no "fragmentation needed" reply is received

### Requirement: Ceph traffic unaffected by the pod MTU

THE rook-ceph-fr01 pods SHALL run with host network so that the pod MTU does not apply to Ceph traffic.

#### Scenario: Ceph pods use host network

- **GIVEN** the rollout is complete
- **WHEN** the pods in namespace `rook-ceph-fr01` are listed
- **THEN** `hostNetwork` is `true` for every pod

### Requirement: Rollback restores MTU 1500

IF the change is rolled back, THEN THE cluster SHALL restore the Cilium device MTU to 1500 and the pod MTU to 1450 with the same per-node and per-workload gates.

#### Scenario: Rollback reaches pod MTU 1450

- **GIVEN** the Cilium device MTU is set back to 1500
- **WHEN** the rollout procedure completes
- **THEN** a new test pod gets MTU 1450
- **AND** the Galera gates pass after every restart step
