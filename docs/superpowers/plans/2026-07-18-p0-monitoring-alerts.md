# P0 Monitoring Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 5 incident-driven (P0) alerting gaps identified in
`docs/superpowers/specs/2026-07-18-monitoring-alerting-gaps-design.md`, plus the P1-10
observability-pipeline meta-alert that implements that spec's cross-cutting `noDataState`
decision, by adding new Grafana native alert rules to the existing provisioning block.

**Architecture:** All rules are added to the same place the MariaDB PVC alert was added earlier:
`spec.values.alerting.rules.yaml` inside the `HelmRelease` patch at
`apps/clusters/feathre-core/base-apps/grafana/release.yaml`. Each rule follows the existing
two-query pattern (`refId: A` = raw PromQL against the `mimir` datasource, `refId: B` = a
`__expr__`/`threshold` condition), evaluated by Grafana's unified alerting engine, routed through
the existing Discord contact point/policy — no new infrastructure. Every new rule (all 8 in this
plan) is appended as new rule groups at the tail of the `groups:` list, right before the file's
`resources:` block, so each task's insertion point is the end of the previous task's insertion —
no mid-file edits, no anchor collisions between tasks.

**Tech Stack:** Grafana unified alerting (provisioning-format YAML), PromQL against Mimir, FluxCD
HelmRelease, Kustomize.

## Global Constraints

- Every new rule uses `noDataState: Alerting` (not `OK`) — the spec's cross-cutting decision: a
  monitoring pipeline going dark should page, not silently read as healthy.
- `execErrState: Alerting` on every rule, matching the existing 5 rules and the MariaDB PVC pair.
- Match existing YAML indentation exactly: group entries at 10 spaces (`          - orgId: 1`),
  group fields at 12, rule entries at 14, rule fields at 16, `data:` items at 18, `relativeTimeRange`/`model` fields at 20/22. Copy the structure from an existing rule rather than
  reformatting.
- No `Chart.yaml` version bump needed — `grafana` is an external chart (from the `grafana-labs`
  `HelmRepository`), not an in-repo chart under `helm/`. Flux picks up the values diff on its own.
- Every PromQL expression in this plan has already been run live against the `mimir` datasource
  (via the Grafana MCP `query_prometheus` tool) during planning — the exact metric names, label
  sets, and current values are confirmed to exist, not guessed.
- Commit messages follow Conventional Commits (repo CI lints them): use `feat(monitoring): ...`.
- After each task, validate with `kubectl kustomize apps/clusters/feathre-core/base-apps/grafana`
  (must build without error) and `grep` for the new rule `uid`(s) in its output — this repo's
  Kustomize/Helm-values setup has no unit test framework, so build-and-grep is the equivalent of
  "run the test."

---

## Validated live data (as of 2026-07-18, for reference — do not re-derive, just implement)

- `cnpg_backends_total{namespace="cnpg-system"}` summed = 43.5, `cnpg_pg_settings_setting{name="max_connections"}` = 200 on all 3 instances → current saturation ≈ 21.75%. (Note: this contradicts the
  `max_connections=100` figure in prior incident notes — the ceiling has apparently already been
  raised to 200 since that incident. The alert queries the live setting dynamically via
  `cnpg_pg_settings_setting`, so it stays correct regardless.)
- `mysql_global_status_wsrep_cluster_size{namespace="mariadb-galera"}` = 3 on all 3 nodes (healthy).
- `ceph_health_status` = 0 (HEALTH_OK; Ceph's mgr Prometheus module maps HEALTH_OK=0,
  HEALTH_WARN=1, HEALTH_ERR=2).
- `sum(rate(ceph_rgw_failed_req[5m]))` over the last 6h: baseline noise is 0.02–0.3 req/s, with one
  real anomalous spike to ~11–12 req/s roughly 6h before this plan was written that decayed back to
  baseline within ~50 minutes. A threshold of `5` sustained for `10m` sits well above baseline
  noise and below the observed real anomaly.
- `kube_pod_deletion_timestamp` returns **no series at all** when nothing is currently terminating
  (confirmed empty right now) — this means `count(time() - kube_pod_deletion_timestamp > 900)`
  alone would evaluate to "no data" in the normal case, which with `noDataState: Alerting` would
  misfire constantly. Must wrap as `... OR vector(0)` (same pattern the existing
  `cnpg-backup-failing` rule already uses) so the healthy state is a real `0`, not absence.
- `count(up{job="kubelet", metrics_path="/metrics"})` = 10 (matches the 10 cluster nodes: 3
  control-plane, 4 `wrk-xl`, 3 `str`).
- Dashboards confirmed to exist (for `annotations.dashboard_url`): Galera overview
  `https://grafana.apps.onelite.feather/d/pXgz0qFGk/galera-mariadb-overview`, CNPG overview
  `https://grafana.apps.onelite.feather/d/cloudnative-pg` (already used by the existing
  `cnpg-backup-*` rules), Ceph Cluster `https://grafana.apps.onelite.feather/d/tbO9LAiZK/ceph-cluster`.

---

### Task 1: Ceph storage alerts (P0-3, P0-4) — extend the existing `storage` group

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml`

**Interfaces:**
- Consumes: the tail of the existing `storage` rule group added earlier this session (ends with
  the `mariadb-galera-pvc-usage-critical` rule's `labels: / severity: critical`, immediately
  followed by `    resources:`).
- Produces: rule UIDs `ceph-cluster-health-warning`, `ceph-cluster-health-critical`,
  `ceph-rgw-failed-request-rate`, all in Grafana folder `Storage`. Later tasks append after this
  one's insertion, not before it.

- [ ] **Step 1: Locate the exact anchor**

Run: `grep -n "mariadb-galera-pvc-usage-critical" -A 40 apps/clusters/feathre-core/base-apps/grafana/release.yaml | tail -15`

Expected output ends with:
```
                labels:
                  severity: critical
    resources:
```

- [ ] **Step 2: Insert the two Ceph health rules and the RGW failed-request rule**

Using the Edit tool, replace this exact text:

```yaml
                labels:
                  severity: critical
    resources:
```

with:

```yaml
                labels:
                  severity: critical
              - uid: ceph-cluster-health-warning
                title: Ceph cluster health degraded
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      expr: max(ceph_health_status)
                      instant: true
                      intervalMs: 1000
                      maxDataPoints: 43200
                      range: false
                      refId: A
                  - refId: B
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: '-100'
                    model:
                      conditions:
                        - evaluator:
                            params:
                              - 0
                            type: gt
                          operator:
                            type: and
                          query:
                            params:
                              - B
                          reducer:
                            params: []
                            type: last
                          type: query
                      datasource:
                        type: __expr__
                        uid: '-100'
                      expression: A
                      intervalMs: 1000
                      maxDataPoints: 43200
                      refId: B
                      type: threshold
                noDataState: Alerting
                execErrState: Alerting
                for: 10m
                annotations:
                  summary: "Ceph cluster health is not HEALTH_OK (HEALTH_WARN or worse). Check: ceph -s (or kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph health detail)"
                  dashboard_url: "https://grafana.apps.onelite.feather/d/tbO9LAiZK/ceph-cluster"
                labels:
                  severity: warning
              - uid: ceph-cluster-health-critical
                title: Ceph cluster health critical
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      expr: max(ceph_health_status)
                      instant: true
                      intervalMs: 1000
                      maxDataPoints: 43200
                      range: false
                      refId: A
                  - refId: B
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: '-100'
                    model:
                      conditions:
                        - evaluator:
                            params:
                              - 1
                            type: gt
                          operator:
                            type: and
                          query:
                            params:
                              - B
                          reducer:
                            params: []
                            type: last
                          type: query
                      datasource:
                        type: __expr__
                        uid: '-100'
                      expression: A
                      intervalMs: 1000
                      maxDataPoints: 43200
                      refId: B
                      type: threshold
                noDataState: Alerting
                execErrState: Alerting
                for: 5m
                annotations:
                  summary: "Ceph cluster health is HEALTH_ERR. Check: ceph -s (or kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph health detail)"
                  dashboard_url: "https://grafana.apps.onelite.feather/d/tbO9LAiZK/ceph-cluster"
                labels:
                  severity: critical
              - uid: ceph-rgw-failed-request-rate
                title: Ceph RGW failed request rate elevated
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      expr: sum(rate(ceph_rgw_failed_req[5m]))
                      instant: true
                      intervalMs: 1000
                      maxDataPoints: 43200
                      range: false
                      refId: A
                  - refId: B
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: '-100'
                    model:
                      conditions:
                        - evaluator:
                            params:
                              - 5
                            type: gt
                          operator:
                            type: and
                          query:
                            params:
                              - B
                          reducer:
                            params: []
                            type: last
                          type: query
                      datasource:
                        type: __expr__
                        uid: '-100'
                      expression: A
                      intervalMs: 1000
                      maxDataPoints: 43200
                      refId: B
                      type: threshold
                noDataState: Alerting
                execErrState: Alerting
                for: 10m
                annotations:
                  # Baseline observed 2026-07-18 was 0.02-0.3 req/s; a real RGW degradation
                  # incident (Rook 1.20 upgrade, S3 access broken for non-owner users) would
                  # show as a sustained multi-request/s error rate, not baseline noise.
                  summary: "RGW failed request rate is elevated (baseline is <1/s). This is the class of failure that broke S3 access for backups/Loki/Mimir/Postgres WAL during the Rook 1.20 upgrade incident. Check: kubectl -n rook-ceph-fr01 logs -l app=rook-ceph-rgw --tail=200"
                  dashboard_url: "https://grafana.apps.onelite.feather/d/tbO9LAiZK/ceph-cluster"
                labels:
                  severity: warning
    resources:
```

- [ ] **Step 3: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load_all(open('apps/clusters/feathre-core/base-apps/grafana/release.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Validate the Kustomize build and confirm the new rules render**

Run: `kubectl kustomize apps/clusters/feathre-core/base-apps/grafana 2>&1 | grep -c "uid: ceph-cluster-health-warning\|uid: ceph-cluster-health-critical\|uid: ceph-rgw-failed-request-rate"`
Expected: `3`

- [ ] **Step 5: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "$(cat <<'EOF'
feat(monitoring): add Ceph cluster health and RGW error-rate alerts

Ceph health going to HEALTH_WARN/HEALTH_ERR, and elevated RGW failed
requests, previously had no alert - this is the failure class that
caused the Rook 1.20 upgrade's silent S3 access breakage.
EOF
)"
```

---

### Task 2: CNPG connection saturation and MariaDB Galera cluster health (P0-1, P0-2) — new `databases` group

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml`

**Interfaces:**
- Consumes: the tail produced by Task 1 (ends with `ceph-rgw-failed-request-rate`'s
  `labels: / severity: warning`, immediately followed by `    resources:`).
- Produces: rule UIDs `cnpg-connection-saturation-warning`, `cnpg-connection-saturation-critical`,
  `mariadb-galera-cluster-degraded`, all in a new Grafana folder `Databases`.

- [ ] **Step 1: Locate the exact anchor**

Run: `grep -n "ceph-rgw-failed-request-rate" -A 45 apps/clusters/feathre-core/base-apps/grafana/release.yaml | tail -6`

Expected output ends with:
```
                labels:
                  severity: warning
    resources:
```

- [ ] **Step 2: Insert the new `databases` rule group**

Using the Edit tool, replace this exact text:

```yaml
                labels:
                  severity: warning
    resources:
```

(this occurs once, at the true end of the `alerting.rules.yaml` block after Task 1's insertion —
if the earlier `grep` in Task 1 Step 4 also matched a `severity: warning` elsewhere, use enough
surrounding context from the actual file to keep the replacement unique)

with:

```yaml
                labels:
                  severity: warning
          - orgId: 1
            name: databases
            folder: Databases
            interval: 60s
            rules:
              - uid: cnpg-connection-saturation-warning
                title: CNPG Postgres connection saturation high
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      expr: sum(cnpg_backends_total{namespace="cnpg-system"}) / scalar(max(cnpg_pg_settings_setting{namespace="cnpg-system", name="max_connections"})) * 100
                      instant: true
                      intervalMs: 1000
                      maxDataPoints: 43200
                      range: false
                      refId: A
                  - refId: B
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: '-100'
                    model:
                      conditions:
                        - evaluator:
                            params:
                              - 80
                            type: gt
                          operator:
                            type: and
                          query:
                            params:
                              - B
                          reducer:
                            params: []
                            type: last
                          type: query
                      datasource:
                        type: __expr__
                        uid: '-100'
                      expression: A
                      intervalMs: 1000
                      maxDataPoints: 43200
                      refId: B
                      type: threshold
                noDataState: Alerting
                execErrState: Alerting
                for: 10m
                annotations:
                  # Threshold is against the live max_connections setting (queried dynamically),
                  # not a hardcoded value - stays correct if the setting is changed again.
                  summary: "CNPG Postgres connections are above 80% of max_connections. Idle pools from Harbor/Dependency-Track have starved other consumers (e.g. outline-collab) before. Check: kubectl -n cnpg-system exec feather-core-cluster-pg-1 -- psql -c \"select datname, state, count(*) from pg_stat_activity group by 1,2 order by 3 desc;\""
                  dashboard_url: "https://grafana.apps.onelite.feather/d/cloudnative-pg"
                labels:
                  severity: warning
              - uid: cnpg-connection-saturation-critical
                title: CNPG Postgres connection saturation critical
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      expr: sum(cnpg_backends_total{namespace="cnpg-system"}) / scalar(max(cnpg_pg_settings_setting{namespace="cnpg-system", name="max_connections"})) * 100
                      instant: true
                      intervalMs: 1000
                      maxDataPoints: 43200
                      range: false
                      refId: A
                  - refId: B
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: '-100'
                    model:
                      conditions:
                        - evaluator:
                            params:
                              - 90
                            type: gt
                          operator:
                            type: and
                          query:
                            params:
                              - B
                          reducer:
                            params: []
                            type: last
                          type: query
                      datasource:
                        type: __expr__
                        uid: '-100'
                      expression: A
                      intervalMs: 1000
                      maxDataPoints: 43200
                      refId: B
                      type: threshold
                noDataState: Alerting
                execErrState: Alerting
                for: 5m
                annotations:
                  summary: "CNPG Postgres connections are above 90% of max_connections - close to exhausting the shared pool. Check: kubectl -n cnpg-system exec feather-core-cluster-pg-1 -- psql -c \"select datname, state, count(*) from pg_stat_activity group by 1,2 order by 3 desc;\""
                  dashboard_url: "https://grafana.apps.onelite.feather/d/cloudnative-pg"
                labels:
                  severity: critical
              - uid: mariadb-galera-cluster-degraded
                title: MariaDB Galera cluster degraded
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      expr: min(mysql_global_status_wsrep_cluster_size{namespace="mariadb-galera"})
                      instant: true
                      intervalMs: 1000
                      maxDataPoints: 43200
                      range: false
                      refId: A
                  - refId: B
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: '-100'
                    model:
                      conditions:
                        - evaluator:
                            params:
                              - 3
                            type: lt
                          operator:
                            type: and
                          query:
                            params:
                              - B
                          reducer:
                            params: []
                            type: last
                          type: query
                      datasource:
                        type: __expr__
                        uid: '-100'
                      expression: A
                      intervalMs: 1000
                      maxDataPoints: 43200
                      refId: B
                      type: threshold
                noDataState: Alerting
                execErrState: Alerting
                for: 2m
                annotations:
                  # min() across all 3 nodes' own view of cluster size - a node that loses
                  # connectivity to its peers (e.g. the Flannel host-gw/MTU incident) reports a
                  # smaller cluster size on its own, so this catches split-brain, not just a
                  # fully-down node.
                  summary: "MariaDB Galera cluster size has dropped below 3 - the cluster is degraded or split-brained. Check: kubectl -n mariadb-galera exec mariadb-galera-0 -- mariadb -e \"show status like 'wsrep_cluster%';\""
                  dashboard_url: "https://grafana.apps.onelite.feather/d/pXgz0qFGk/galera-mariadb-overview"
                labels:
                  severity: critical
    resources:
```

- [ ] **Step 3: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load_all(open('apps/clusters/feathre-core/base-apps/grafana/release.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Validate the Kustomize build and confirm the new rules render**

Run: `kubectl kustomize apps/clusters/feathre-core/base-apps/grafana 2>&1 | grep -c "uid: cnpg-connection-saturation-warning\|uid: cnpg-connection-saturation-critical\|uid: mariadb-galera-cluster-degraded"`
Expected: `3`

- [ ] **Step 5: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "$(cat <<'EOF'
feat(monitoring): add CNPG connection saturation and Galera cluster-health alerts

Postgres connection saturation (previously starved outline-collab via
idle Harbor/Dependency-Track pools) and Galera cluster size dropping
below 3 (previously caused by the Flannel host-gw/MTU split-brain) both
had no alert until now.
EOF
)"
```

---

### Task 3: Pods stuck Terminating (P0-5) — new `cluster_health` group in the `Core Services` folder

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml`

**Interfaces:**
- Consumes: the tail produced by Task 2 (ends with `mariadb-galera-cluster-degraded`'s
  `labels: / severity: critical`, immediately followed by `    resources:`).
- Produces: rule UID `pods-stuck-terminating`, in the existing Grafana folder `Core Services`
  (reusing the folder the Flux/HelmRelease rules already use, via a new rule *group* — Grafana
  allows multiple rule groups per folder).

- [ ] **Step 1: Locate the exact anchor**

Run: `grep -n "mariadb-galera-cluster-degraded" -A 40 apps/clusters/feathre-core/base-apps/grafana/release.yaml | tail -6`

Expected output ends with:
```
                labels:
                  severity: critical
    resources:
```

- [ ] **Step 2: Insert the new `cluster_health` rule group**

Using the Edit tool, replace this exact text (use enough surrounding context, e.g. include the
`mariadb-galera-cluster-degraded` uid line above it, to disambiguate from the identical-looking
`severity: critical` / `resources:` pair Task 1 leaves behind mid-file):

```yaml
                labels:
                  severity: critical
    resources:
```

with:

```yaml
                labels:
                  severity: critical
          - orgId: 1
            name: cluster_health
            folder: Core Services
            interval: 60s
            rules:
              - uid: pods-stuck-terminating
                title: Pods stuck in Terminating
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # kube_pod_deletion_timestamp has NO series at all when nothing is
                      # terminating (confirmed live) - "OR vector(0)" turns that healthy state
                      # into a real 0 instead of "no data", which matters because this rule uses
                      # noDataState: Alerting.
                      expr: count(time() - kube_pod_deletion_timestamp > 900) OR vector(0)
                      instant: true
                      intervalMs: 1000
                      maxDataPoints: 43200
                      range: false
                      refId: A
                  - refId: B
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: '-100'
                    model:
                      conditions:
                        - evaluator:
                            params:
                              - 0
                            type: gt
                          operator:
                            type: and
                          query:
                            params:
                              - B
                          reducer:
                            params: []
                            type: last
                          type: query
                      datasource:
                        type: __expr__
                        uid: '-100'
                      expression: A
                      intervalMs: 1000
                      maxDataPoints: 43200
                      refId: B
                      type: threshold
                noDataState: Alerting
                execErrState: Alerting
                for: 5m
                annotations:
                  summary: "One or more pods have been stuck in Terminating for over 15 minutes. Matches the containerd shim task.Delete hang on SIGKILL teardown seen before. Check: kubectl get pods -A --field-selector=status.phase!=Running | grep Terminating"
                labels:
                  severity: warning
    resources:
```

- [ ] **Step 3: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load_all(open('apps/clusters/feathre-core/base-apps/grafana/release.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Validate the Kustomize build and confirm the new rule renders**

Run: `kubectl kustomize apps/clusters/feathre-core/base-apps/grafana 2>&1 | grep -c "uid: pods-stuck-terminating"`
Expected: `1`

- [ ] **Step 5: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "$(cat <<'EOF'
feat(monitoring): alert on pods stuck in Terminating

No alert previously existed for the containerd shim task.Delete hang
class of incident, where pods wedge in Terminating instead of
completing teardown.
EOF
)"
```

---

### Task 4: Observability pipeline meta-alert (P1-10) — new `observability` group

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml`

**Interfaces:**
- Consumes: the tail produced by Task 3 (ends with `pods-stuck-terminating`'s
  `labels: / severity: warning`, immediately followed by `    resources:`).
- Produces: rule UID `observability-pipeline-no-data`, in a new Grafana folder `Observability`.
  This is the last rule group in the file after this task.

- [ ] **Step 1: Locate the exact anchor**

Run: `grep -n "pods-stuck-terminating" -A 30 apps/clusters/feathre-core/base-apps/grafana/release.yaml | tail -6`

Expected output ends with:
```
                labels:
                  severity: warning
    resources:
```

- [ ] **Step 2: Insert the new `observability` rule group**

Using the Edit tool, replace this exact text:

```yaml
                labels:
                  severity: warning
    resources:
```

(use the `pods-stuck-terminating` uid as surrounding context to keep this unique, same as Task 3
Step 2)

with:

```yaml
                labels:
                  severity: warning
          - orgId: 1
            name: observability
            folder: Observability
            interval: 60s
            rules:
              - uid: observability-pipeline-no-data
                title: Observability pipeline producing no data
                condition: B
                data:
                  - refId: A
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: mimir
                    model:
                      editorMode: code
                      # absent() returns a real 1-valued series when up{job="kubelet"} has zero
                      # matching series - i.e. the whole Alloy remote-write pipeline into Mimir has
                      # gone dark, not just one target. A total Mimir outage instead trips
                      # execErrState: Alerting on this and every other rule, which is the
                      # existing belt-and-suspenders behavior already shared by all rules.
                      expr: absent(up{job="kubelet", metrics_path="/metrics"})
                      instant: true
                      intervalMs: 1000
                      maxDataPoints: 43200
                      range: false
                      refId: A
                  - refId: B
                    relativeTimeRange:
                      from: 600
                      to: 0
                    datasourceUid: '-100'
                    model:
                      conditions:
                        - evaluator:
                            params:
                              - 0
                            type: gt
                          operator:
                            type: and
                          query:
                            params:
                              - B
                          reducer:
                            params: []
                            type: last
                          type: query
                      datasource:
                        type: __expr__
                        uid: '-100'
                      expression: A
                      intervalMs: 1000
                      maxDataPoints: 43200
                      refId: B
                      type: threshold
                noDataState: Alerting
                execErrState: Alerting
                for: 5m
                annotations:
                  summary: "No kubelet metrics are reaching Mimir at all - the Alloy scrape/remote-write pipeline is likely down. Every other alert in this cluster depends on this pipeline and will read as falsely healthy while this fires. Check: kubectl -n grafana get pods -l app.kubernetes.io/name=alloy-metrics"
                labels:
                  severity: critical
    resources:
```

- [ ] **Step 3: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load_all(open('apps/clusters/feathre-core/base-apps/grafana/release.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Validate the Kustomize build and confirm the new rule renders**

Run: `kubectl kustomize apps/clusters/feathre-core/base-apps/grafana 2>&1 | grep -c "uid: observability-pipeline-no-data"`
Expected: `1`

- [ ] **Step 5: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "$(cat <<'EOF'
feat(monitoring): add meta-alert for a dark observability pipeline

Implements the noDataState:Alerting cross-cutting decision from the
monitoring-gaps spec: if Alloy stops remote-writing kubelet metrics to
Mimir, every other alert would otherwise read as silently healthy.
EOF
)"
```

---

### Task 5: Full-repo validation

**Files:** none (verification only)

**Interfaces:**
- Consumes: the fully updated `apps/clusters/feathre-core/base-apps/grafana/release.yaml` from
  Tasks 1-4.
- Produces: confirmation that no other Flux layer in the repo was broken by these changes.

- [ ] **Step 1: Run the full repo validation script**

Run: `./scripts/validate.sh 2>&1 | tail -40`
Expected: every `::group::kustomize build ...` block reports `Errors: 0`, matching the run from
earlier this session (16 layers, all `Invalid: 0, Errors: 0`).

- [ ] **Step 2: Confirm all 8 new rule UIDs are present in the rendered output**

Run:
```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana 2>&1 | grep -E "uid: (ceph-cluster-health-warning|ceph-cluster-health-critical|ceph-rgw-failed-request-rate|cnpg-connection-saturation-warning|cnpg-connection-saturation-critical|mariadb-galera-cluster-degraded|pods-stuck-terminating|observability-pipeline-no-data)" | wc -l
```
Expected: `8`

- [ ] **Step 3: Confirm total alert rule count**

Two naive approaches were tried and rejected while writing this plan: `grep -c "^ *- uid:"` over
the whole file also matches the `discord_webhook` contact point's `uid:` (8 matches today, not 7),
and a `sed` range from `rules.yaml:` to the next `    resources:` breaks because `kubectl
kustomize` re-serializes the merged Helm values with keys sorted alphabetically, so `resources:`
no longer immediately follows the `rules.yaml:` block in the rendered output (confirmed live: that
range returned `0` matches). Instead, match the exact union of all 15 known rule UIDs directly:

Run:
```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana 2>&1 | grep -cE "uid: (flux-core-layer-not-ready|core-infra-helmrelease-not-ready|cnpg-backup-stale|cnpg-backup-failing|mariadb-galera-backup-job-failed|mariadb-galera-pvc-usage-high|mariadb-galera-pvc-usage-critical|ceph-cluster-health-warning|ceph-cluster-health-critical|ceph-rgw-failed-request-rate|cnpg-connection-saturation-warning|cnpg-connection-saturation-critical|mariadb-galera-cluster-degraded|pods-stuck-terminating|observability-pipeline-no-data)"
```
Expected: `15` (confirmed the same command returns `7` today, before this plan's tasks run — i.e.
just the pre-existing 7 rules, since the 8 new UIDs don't exist yet).

No commit for this task — it's verification-only; Tasks 1-4 already each committed their own
change.

---

## After execution

Once merged to `main` and Flux reconciles, expect `cnpg-connection-saturation-*` and
`mariadb-galera-cluster-degraded` to stay quiet (current values are well under threshold: ~22%
connections, cluster size 3/3). `ceph-cluster-health-*` and `ceph-rgw-failed-request-rate` should
also stay quiet (current health status 0, current failed-request rate near baseline).
`pods-stuck-terminating` and `observability-pipeline-no-data` should both stay quiet under normal
operation — if either fires immediately after merge, treat it as a real signal, not a
false-positive from the rule itself (both were validated to correctly read "0"/healthy against
live data during planning).
