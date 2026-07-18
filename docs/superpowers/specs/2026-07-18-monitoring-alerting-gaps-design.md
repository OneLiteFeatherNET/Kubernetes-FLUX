# Monitoring/alerting gaps — prioritized backlog

**Date:** 2026-07-18
**Status:** Approved for planning

## Summary

Grafana's native unified alerting (`apps/clusters/feathre-core/base-apps/grafana/release.yaml`,
`spec.values.alerting.rules.yaml`) currently has 5 alert rules total: Flux Kustomization/HelmRelease
not-Ready, CNPG backup stale/failing, and MariaDB Galera backup job failed — plus, as of this
session, a MariaDB Galera PVC-usage warning/critical pair. Everything else in the cluster —
Rook-Ceph, CNPG's own health beyond backups, Dragonfly, Envoy Gateway, MetalLB, node health,
cert-manager, and the observability stack watching itself — has no alerting at all.

This document is a prioritized backlog of the alerting gaps worth closing next, identified by
walking through documented past incidents first, then the highest-blast-radius cluster
foundations. It is not an implementation plan — each backlog item still needs its PromQL
expression validated against live data and its Grafana rule YAML written, one at a time, the same
way the MariaDB PVC alert was added.

## Scope

**In scope:** critical stateful services and cluster foundations — Rook-Ceph, CNPG Postgres,
MariaDB Galera, Dragonfly, Envoy Gateway, MetalLB, node health, cert-manager, and the
observability pipeline itself.

**Explicitly out of scope:**
- Generic per-app alerts (CrashLoopBackOff, OOMKill, restart rate, Pod Pending) applied uniformly
  across the ~20 individual application deployments (Outline, Harbor, n8n, Plane, Reposilite,
  etc.). Worth doing eventually, but as its own follow-up rather than folded into this
  incident-driven pass.
- Migrating alert evaluation from Grafana-native alerting to Mimir's ruler (Prometheus-native
  recording/alerting rules as separate GitOps-friendly files instead of one large embedded
  `values.alerting.rules.yaml` block). The current file already works and is consistent with the
  5 existing rules; a ruler migration is a real project (enabling Mimir's ruler + alertmanager
  component, migrating the Discord notification policy) and shouldn't be bundled with closing
  alerting gaps. Noted here so it isn't forgotten.

## Prioritization

Ordered by: **P0** — a gap that would directly have caught one of the documented past incidents
(proven need, not a guess); **P1** — no documented incident yet, but high blast radius (failure
takes down many dependents); **P2** — worth having, lower urgency, mostly rounding out coverage
once P0/P1 exist.

## Cross-cutting concern: `noDataState: OK` is a blind spot

All 5 existing alert rules (and the new PVC pair) use `noDataState: OK`. If the query against the
`mimir` datasource returns no data — because Alloy stops remote-writing, Mimir itself is down, or
a scrape target disappears — the rule evaluates as OK, not Alerting. A failure of the monitoring
pipeline itself would therefore leave every alert silently green instead of firing.

Decision for this backlog: every P0/P1 rule below uses `noDataState: Alerting` instead of `OK`.
P1-10 adds a dedicated meta-alert so "the pipeline stopped producing data" is itself an alert
condition, not silence.

(The 6 pre-existing rules — Flux ready-state, CNPG backup, MariaDB backup, MariaDB PVC — are left
as `noDataState: OK` for now; revisiting them is a separate, smaller follow-up, not part of this
backlog.)

## P0 — would have caught a documented incident

| ID | Alert | Incident it maps to | Sketch |
|---|---|---|---|
| P0-1 | CNPG Postgres connection saturation | Shared CNPG hit `max_connections=100`; idle Harbor/Dependency-Track pools starved outline-collab. | `sum(pg_stat_activity_count) / max(pg_settings_max_connections) * 100` per cluster; warn 80%, critical 90%. |
| P0-2 | MariaDB Galera cluster degraded | Flannel host-gw/MTU rollout caused a Galera split-brain, silently, until manually caught. | `wsrep_cluster_size < 3` OR `wsrep_cluster_status != "Primary"` for `>2m`. The Galera dashboard already has Cluster Size/Status panels — just no alert wired to them. |
| P0-3 | Ceph cluster health not HEALTH_OK | Root-cause class for the Rook operator 1.20 / RGW incident and the OSD disk-swap procedure. | `ceph_health_status > 0`. |
| P0-4 | RGW/S3 write errors for internal consumers | Rook 1.20 upgrade broke S3 access for non-owner named users cluster-wide — backups, Loki, Mimir, and Postgres WAL archiving all silently degraded. | Error rate on S3 requests from barman-cloud (CNPG WAL/backup archiving) and, if exposed, Loki/Mimir object-storage writes. Complements the existing `cnpg-backup-*` rules, which only catch the end symptom ("last backup failed"), not the RGW-layer cause. |
| P0-5 | Pods stuck in Terminating too long | containerd shim `task.Delete` hang on SIGKILL teardown wedged pod termination. | `time() - kube_pod_deletion_timestamp_seconds > 900` (15m). |

## P1 — high blast radius, no documented incident yet

| ID | Alert | Rationale | Sketch |
|---|---|---|---|
| P1-1 | Ceph OSD down / PG degraded | Finer-grained than P0-3; would catch an OSD disk-swap going wrong before it escalates to HEALTH_ERR. | `ceph_osd_up == 0`, `ceph_pg_degraded > 0`. |
| P1-2 | Ceph raw capacity nearing full | Cluster-wide counterpart to the per-PVC alert — individual PVC headroom doesn't help if the backing pool itself is full. | `ceph_cluster_total_used_bytes / ceph_cluster_total_bytes`. |
| P1-3 | Node NotReady / DiskPressure / MemoryPressure | Foundation everything else runs on. | `kube_node_status_condition{status="true", condition=~"Ready|DiskPressure|MemoryPressure"}`. |
| P1-4 | Node root filesystem nearing full | Independent of Ceph-backed PVCs — local node disk (image cache, logs) filling up on e.g. `fr01-str-01..03`. | `node_filesystem_avail_bytes / node_filesystem_size_bytes` on the root mount. |
| P1-5 | Dragonfly down/unhealthy | Post-migration single point of failure for shlink/outline/harbor/n8n sessions and caches. | `up{job=~"dragonfly.*"} == 0`, plus a replication/master-missing check. |
| P1-6 | CNPG cluster unhealthy / replication lag | Beyond backup-only checks: catches a missing primary or high replication lag that P0-1 (connection count) wouldn't. | `cnpg_pg_replication_lag`, cluster status metric. |
| P1-7 | Envoy Gateway error rate / down | Single HTTP entry point for the whole cluster. | 5xx rate, gateway pod-down. |
| P1-8 | MetalLB IP not assigned / speaker down | A LoadBalancer service silently stuck without an external IP goes unnoticed otherwise (relevant right now given the in-flight Dragonfly `service-lb.yaml` change). | speaker health, service `externalIP` pending duration. |
| P1-9 | cert-manager certificate expiring / not Ready | Classic silent failure until TLS actually breaks. | `certmanager_certificate_expiration_timestamp_seconds` nearing expiry; `certmanager_certificate_ready_status != True`. |
| P1-10 | Meta-alert: observability pipeline dead | Directly implements the cross-cutting fix above. | `noDataState: Alerting` on a time series that should always exist (e.g. `up{job="kubelet"}` count), so "no data" itself pages. |

## P2 — round out coverage once P0/P1 exist

| ID | Alert | Notes |
|---|---|---|
| P2-1 | Host resource trend (CPU/memory/load nearing limit) | Early warning ahead of P1-3. |
| P2-2 | Flux Kustomization stuck in `Reconciling` for a long time | Likely mostly covered already by the existing `ready=False` rule; lowest priority in this list. |
| P2-3 | Loki/Mimir/Tempo component down / ingestion error rate | Observability stack monitoring itself, beyond the bare "no data" check in P1-10. |

## Suggested Grafana folder/rule-group layout

Following the existing pattern (one `folder:`/`name:` rule group per concern, e.g. `storage` used
for the MariaDB PVC alerts): `Databases` (P0-1, P0-2, P1-5, P1-6), `Storage` (P0-3, P0-4, P1-1,
P1-2 — same folder the PVC alerts already use), `Nodes` (P1-3, P1-4, P2-1), `Networking` (P1-7,
P1-8), `Certificates` (P1-9), `Observability` (P1-10, P2-3), `Core Services` (P0-5, P2-2 — same
folder Flux/HelmRelease alerts already use). This is a suggestion, not a requirement — adjust
when actually implementing each item if a different grouping reads better once the queries are
written.

## Next steps

Not part of this document: each backlog item above still needs its exact PromQL validated against
live Mimir data (metric/label names sketched here are best-effort, not verified), and its full
Grafana rule YAML written into `apps/clusters/feathre-core/base-apps/grafana/release.yaml`,
following the two-query (raw expression `A` → threshold expression `B`) pattern already
established. Recommended: work through P0 items first, one PR-sized change at a time, the same
way the MariaDB PVC alert was added — validate the metric exists and the threshold is sane against
current cluster data before writing the rule.
