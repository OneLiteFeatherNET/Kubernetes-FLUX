# Discord alert noise reduction & table layout — design

**Date:** 2026-08-12 (updated same day after `455edcf`, `959f39a`)
**Status:** Shipped — commits `1c0ed60`, `8eee395`, `d7ea580`, `55ee41c`, `455edcf`, `959f39a` on
`apps/clusters/feathre-core/base-apps/grafana/release.yaml` (branch
`feat/grafana-alert-noise-reduction`).

**Supersedes:** `docs/superpowers/specs/2026-07-18-discord-alert-notification-design.md` (bullet
list, `reReplaceAll` over `summary`, no instance cap — all replaced by what's below). Also revises
the `noDataState` cross-cutting decision and rule/group counts in
`docs/superpowers/specs/2026-07-18-monitoring-alerting-gaps-design.md`.

## Summary

Two problems, one change set: (1) Discord notification volume was dominated by two Flux rules
whose `for:` timer restarted on every ordinary reconcile blip, and (2) the per-instance bullet-list
message layout repeated shared fields and had no cap, risking a truncated code fence on a large
group. Both are fixed together because the fix for (1) — aggregating labels down to a stable
identity — is also the hard prerequisite the new table layout in (2) depends on.

## Part 1 — Notification volume

### Baseline (measured over 7 days, alert state history)

- 74,562 total state transitions; 1,568 `Pending → Alerting` crossings, both concentrated on
  `flux-core-layer-not-ready`.
- ~630 Discord messages/week at the old `for: 5m` / `group_interval: 5m` / `repeat_interval: 4h`
  settings.
- 22,102 not-ready episodes of `gotk_resource_info{ready!="True"}`: 87.9% lasted exactly 1 minute,
  94.3% lasted under 3 minutes, none lasted longer than 20 minutes.

### Root cause

`gotk_resource_info` goes `ready="False"` for a single scrape during every ordinary Flux
reconcile. The query wasn't aggregating, so `revision` (changes on every commit to `main`), `pod`,
`instance`, and `ready` were all part of the alert-instance identity — every new label combination
started a fresh `for:` timer and could produce a new notification, even though `for: 5m` should in
principle have filtered 1-minute blips. A 15-minute sustain window cleanly separates the 20-minute
worst case from real breakage (nothing in the measured history reached it).

### Fix: sustain check moves from `for:` into PromQL

```
min_over_time((max by (exported_namespace, name) (gotk_resource_info{..., ready!="True"}) or
               max by (exported_namespace, name) (gotk_resource_info{...}) * 0)[15m:1m])
```

`for:` becomes `0s`; `keepFiringFor: 15m` holds the alert open past a brief recovery. Aggregating
to exactly `(exported_namespace, name)` — 17 labels down to 2 — is not just cleanup: it is a hard
requirement of the Discord template (below), which builds its instance table from those two
labels, and it's what makes the alert instance identity stable across a reconcile instead of
changing on every `revision` bump.

Replayed against the recorded 7-day history, the new query fires **3 times** instead of 1,568.
Expected load: **~13 Discord messages/week instead of ~630** (~98% reduction, combines with the
grouping-interval changes in Part 3). This is an estimate from replaying historical data against
the new query — **check it against real traffic around 2026-08-19** (one week after shipping).

**Detection latency: ~15–17 minutes, not 14–16.** `[15m:1m]` is left-open, so it evaluates 15
points spanning **14 real minutes**, on top of which: `for: 0s` adds nothing; the `interval: 60s`
evaluation cadence adds up to 60s of jitter; and routing's `group_wait: 1m` adds up to another 60s
before the message actually sends. Best case ends up ~14–15 minutes, worst case ~16–17 minutes. An
earlier pass at this number ("14–16 min") missed the `group_wait` contribution.

The same `max by (...)` aggregation was applied to the two Galera PVC rules, so a pod reschedule
(which changes the `instance` label) no longer spawns a second alert instance for the same PVC.

### The `or … * 0` anchor

The second term of the `or` keeps a same-labelled series alive for every object at every minute,
purely so the alert instance doesn't disappear and trigger a MissingSeries/NoData transition (58
of the last 100 recorded state changes were MissingSeries/NoData — nearly as much churn as real
`for:` crossings). The anchor term must fall **inside** the `[15m:1m]` subquery window, or the
`min_over_time` has nothing to average outside of real not-ready samples and the anchor does
nothing.

### Verified against a real incident: overlapping-series false-alarm concern, cleared

The obvious worry with `max by (exported_namespace, name)`: it collapses *all* series for an
object, so if a `ready="True"` series and a `ready="False"` series exist for the same object at the
same instant (e.g. during a kube-state-metrics staleness overlap), `max` returns `1` even though
the object is healthy — a permanent false positive waiting to happen.

This was checked against a real event, not just reasoned about, and the result clears the design —
recorded here so the concern doesn't get re-raised from scratch:

- `max_over_time((count by (exported_namespace, name)(gotk_resource_info{customresource_kind="Kustomization"}))[30d:1m])`
  returns **2** for five objects (`base-apps`, `base-configs`, `base-controllers`, `controllers`,
  `monitoring`); a 7-day window shows a steady 1 for the same objects — so the overlap event sits
  between 7 and 30 days back.
- Localised to **2026-08-04, ~08:30–09:30 UTC**, `revision` label `main@sha1:d50090a7…` —
  commit `d50090a feat(rook): raise the bakery bucket quota to four`. An ordinary push triggered a
  cascading reconcile of dependent layers.
- Raw data for that hour shows exactly the feared pattern: all five objects briefly carried **three
  co-existing series** (`ready="True"`, `="False"`, `="Unknown"`) with tightly interleaved
  timestamps.
- **The production expression, evaluated across 08:00–12:00 (241 points × 13 objects, 60s step):
  returned 0 for all 13 objects throughout — no firing.** `min_over_time` requires all 15 embedded
  minutes to be non-`True`; a brief transition overlap doesn't satisfy that.
- **Open residual risk (suspected, not confirmed):** what was checked was staleness overlap from a
  *single* kube-state-metrics pod. The other plausible case — two pods running in parallel during a
  rolling restart, each reporting a contradictory `ready` value at the exact same timestamp — was
  not resolved down to the second. Over 30 days the maximum concurrent-pod count was 2, but that was
  never correlated against a concrete `gotk_resource_info` overlap.

### `keepFiringFor` — the expensive trap

**`keepFiringFor` must be written in camelCase in the provisioning-format YAML** — it maps to the
Go struct field `AlertRuleV1` with `yaml:"keepFiringFor"`. Writing `keep_firing_for` (the style
every other key in this file uses) is an **unknown key that is silently dropped — no validation
error, no Grafana error, nothing.** The rule looks correct, applies cleanly, and simply doesn't
keep firing.

Verify it actually took by reading back the *exported* rule, not the source YAML:

```
GET /api/v1/provisioning/alert-rules/<uid>/export?format=yaml
```

If `keepFiringFor` is missing from the export, the key was dropped on ingest.

### HelmRelease matcher cleanup

The old regex included `metallb` and `dragonfly`, which have **zero series** in 90 days of Mimir
retention (dead matchers — nothing to alert on). Both dropped; 9 real infra HelmReleases that were
missing from the list were added (`alloy-logs`, `alloy-metrics`, `alloy-receiver`,
`ceph-csi-drivers`, `kube-prometheus-stack`, `mariadb-operator-crds`, `spegel`, `step-ca`,
`step-issuer`). `descheduler` is deliberately **not** included — it's an optimiser, not a
dependency; nothing else in the cluster needs it Ready to function.

### `noDataState` revision

Six rules whose backing metrics are continuously present move `noDataState: Alerting → OK`
(the two Ceph OSD-usage rules, the RGW error-rate rule, Ceph HEALTH_ERR, and the two CNPG
connection-saturation rules). `pods-stuck-terminating` also moves to `OK`, for a different reason:
its query already ends in `OR vector(0)`, so it always returns a series and the no-data path was
unreachable dead configuration regardless of the setting.

Three rules deliberately keep `noDataState: Alerting` as sentinels for missing telemetry:
`ceph-cluster-health-warning`, `mariadb-galera-cluster-degraded`, `observability-pipeline-no-data`.

### `mariadb-galera-cluster-degraded`: `for: 2m → 5m`

Over 28 days, `wsrep_cluster_size < 3` totalled 7 minutes across exactly one episode of ≥2 minutes
and zero episodes of ≥5 minutes — `for: 2m` was paging roughly once a month for a routine rolling
restart, not a real degradation.

## Part 2 — Structured annotations replace regex-sliced `summary`

All 23 rules that existed at the time of `1c0ed60` gain five new annotations: `problem`,
`object_label`, `object`, `location`, `check_command` (`dashboard_url` already existed on most).
The 24th rule (`kube-state-metrics-down`, added later by `455edcf` — see below) was written
directly against this convention, so all **24** rules now carry it. The Discord template reads
these directly instead of regex-stripping the trailing `Check: ...` clause out of `summary`, which
was the old design's approach (see the superseded doc).

**Invariant, more precise than the one it replaces:** `problem`, `object_label`, `check_command`,
and `dashboard_url` are read via `(index .Alerts 0)` and **must be static per rule** — never
templated against `$labels`. Violating this produces **no error**; it silently shows instance 0's
value for the entire notification group. Only `object` and `location` are allowed to use `$labels`,
and they are read exclusively inside `range .Alerts` (once per instance), never via `index .Alerts
0`. `.CommonAnnotations.location` is used only as an "all instances agree on location" detector —
if it's set, the table renders a single shared location line instead of a per-row location column.

**Consequence:** a `check_command` that would need to embed the specific node or namespace of the
firing instance **cannot do that** — it's static. Where that specific value matters (e.g. the Flux
rules' `flux get kustomization -A` / `flux get helmrelease -A`), the command stays generic and the
concrete object is read from the row above it in the rendered table instead.

**Concrete example of what the invariant costs:** the clock-drift rules' `check_command` uses the
literal placeholder `<node>` rather than `{{ $labels.instance }}`. A reviewer proposed swapping in
`$labels.instance` for a directly runnable command; that was rejected — `check_command` is read via
`(index .Alerts 0)`, so templating it against `$labels` would make the whole notification group
show instance 0's node for every row, silently. `<node>` is the correct static answer; the real
value per instance is already visible one line up, in the object row.

## Part 3 — Grouping/timing (`d7ea580`, then corrected by `959f39a`)

Current, final policy: `group_by: ['alertname']`, `group_wait: 1m`, `group_interval: 5m`,
`repeat_interval: 24h` — ordering `1m ≤ 5m ≤ 24h` holds.

| Setting | Original | `d7ea580` | `959f39a` | Why |
|---|---|---|---|---|
| `group_wait` | 30s | **1m** (final) | unchanged | One full evaluation period, so every instance of a firing rule lands in one message instead of trickling in separately. |
| `group_interval` | 5m | 15m | **5m** (final — reverted) | See below. |
| `repeat_interval` | 4h | **24h** (final) | unchanged | A repeat carries no new information. At 4h the (at the time) permanently-Stalled `mimir` HelmRelease alone cost 42 messages/week; at 24h it costs 7. Two people, no on-call rotation — see the related mimir finding below, which is exactly that alert source. |

**`group_interval` went to 15m and back to 5m within the same day.** `d7ea580`'s reasoning was
sound at the time — damp the membership-churn noise the old, non-aggregated Flux queries produced
(a single group could otherwise re-send up to 12×/hour). But `1c0ed60` fixed that churn at its
source in the same change set: the sustain check moved into PromQL and the label set went from 17
labels down to 2, measured at 3 firings/week instead of 1,568. Once the root cause was gone, the
15m damping was pure cost with no remaining benefit:

- `keepFiringFor: 15m` stacked with `group_interval: 15m` meant a worst case of **~30 minutes**
  between a real recovery and the RESOLVED message actually going out.
- Because `group_by: ['alertname']` groups by rule, a newly-affected object (say, a second
  Kustomization going not-Ready) also waited up to a full `group_interval` for the next flush
  before being reported **at all** — up to 15 minutes of silence on a brand-new problem.

`959f39a` reverts to 5m, halving both: worst-case RESOLVED delay drops to ~20 minutes, and a new
object's first report is capped at 5 minutes instead of 15.

**`group_by: ['alertname']` is a hard requirement of the message layout and must not change.**
`discord.message` assumes a notification group holds exactly the instances of one rule; changing
`group_by` would mix instances of different rules into one table.

## Part 4 — Discord message table layout (`8eee395`, `55ee41c`)

### Layout

A monospace two-column table inside a code fence: a 12-rune label on the left, the value on the
right. Labels are chosen so `printf "%-12s"` (which counts **runes, not bytes**) aligns the
multi-byte label `Prüfen` correctly alongside ASCII labels like `Seit`/`Ort`/`Betroffen`. The
labels themselves live outside the code fence conceptually but render inside it — they are **not
clickable inside the fence**, which is why the rule/dashboard links are printed as a separate line
after the closing fence, not as a table row.

The object column is `%-27.26s` — precision 26 plus one guaranteed separator space before the
location column. It was originally 22 (`%-23.22s`); that truncated the Galera PVC names
(`storage-mariadb-galera-0/-1/-2`, 24 runes each) right at the part that told the three rows apart
— all three rendered as a bare `storage-mariadb-galera` above their (differing) node names. This
isn't a corner case: the two-column form is used whenever `location` differs between instances,
and for the PVC rules `location` is the node label, which differs by definition — the truncating
path is the *normal* path for that rule, not an edge case.

The separator lines are 44 × U+2500 (`─`, box-drawings light horizontal), and the "instances
hidden" line uses U+2026 (`…`, horizontal ellipsis) rather than three periods. If Discord ever
wraps these differently on mobile, `-` and `...` are the two ASCII fallbacks to swap in.

### Verified template engine limits

Tested live against Grafana 12.3.1 via the templates-test endpoint (below). **No Sprig functions,
no `humanizeDuration`, no arithmetic (`math.Sub` does not exist)** — all three fail with "function
not defined". Available: `printf`, `date`, `tz`, `reReplaceAll`, `time.Now.Sub`, and methods on
`time.Time`.

Consequence: alert age is derived by regexing `time.Duration.String()` output
(`^(([0-9]+h)?([0-9]+m)?).*$`), because there's no way to subtract components directly. The
"instances hidden" overflow line prints the **total** instance count rather than the remainder
past the cap, for the same reason — there's no subtraction available to compute "N more".

### Escaping constraints

The Grafana Helm chart renders the `alerting:` values block through `tpl`, so every Grafana
template has to be written as a Helm **raw string** (`{{ \`...\` }}`). Consequence: the effective
template text must contain **no backtick** (would close the raw string early) and **no single
quote** (Helm's `toYaml` may render a single-line scalar single-quoted, which would then appear
inside the raw string and break the surrounding Helm parse).

### Discord's own limits

- `message` → Discord's `content` field: hard-truncated at **2000 runes**, with a marker appended
  on truncation. A cut landing inside the code fence would leave it unclosed and wreck the
  rendering of the whole message — this is why the instance list is capped at **8** rows regardless
  of how many instances are actually firing.
- `title` → `embeds[0].title`: 256 runes.
- Measured worst case across the 9 test cases below: **726 runes** (~2.75× headroom under 2000).
  After the column-width fix in `55ee41c`, the worst observed render (3 full-length Galera PVC rows
  plus a 36-rune release name cut to 26) was **566 runes**.

### Testability

`POST /api/alertmanager/grafana/config/api/v1/templates/test` renders a template **without writing
any config and without sending a real message** — body shape: `{"name": "<define-name>",
"template": "<full template text>", "alerts": [...]}`. This is what both `8eee395` and `55ee41c`
were verified against, across 9 cases for the initial layout (single instance, 12 instances,
resolved-only scalar rule, mixed firing/resolved with differing locations, missing
`dashboard_url`, missing `check_command`, over-long object name, empty object, fresh `startsAt`)
and again for the column-width fix (3 Galera PVC rows, a 36-rune release name, the single-column
shared-location form).

**`POST /api/alertmanager/grafana/config/api/v1/receivers/test` is a different endpoint that
actually posts to the Discord channel** — do not use it for iterating on template text.

The contact point itself is untouched: `discord.title`/`discord.message` are still the names
referenced from `contactpoints.yaml`, so the existing Flux `valuesFrom` targetPath into
`contactPoints[0].receivers[0].settings.url` keeps working.

## Accepted blindness

Flux resources that are not-Ready for under 14 continuous minutes are now invisible to alerting —
that's 98.5% of all recorded episodes, which is normal reconcile noise, not something worth paging
on. `mariadb-operator` is a known case that stays under this radar: it flaps for a measured 418
minutes/week with individual streaks up to 14 minutes and will never trigger the new 15-minute
sustain rule. That belongs on a dashboard, not in an alert.

## Fixed in `455edcf`: kube-state-metrics outage was a blind spot for both Flux rules

If `kube-state-metrics` stops producing data, both Flux rules (`flux-core-layer-not-ready`,
`core-infra-helmrelease-not-ready`) read as healthy — `noDataState: OK`, and the `* 0` anchor
disappears along with the real data, so there was nothing left to alert on either. This was **not**
covered by `observability-pipeline-no-data`, which watches `job="kubelet"`, a different scrape
target.

**Closed** by a new rule, `kube-state-metrics-down` (group `observability`, folder `Observability`,
placed directly after `observability-pipeline-no-data`): `expr: count(up{job="kube-state-metrics"})`
with a `lt 1` threshold, `for: 5m`, `noDataState: Alerting`, `execErrState: Alerting`,
`severity: critical`.

**Why `count(...) < 1` and not `absent(up{job="kube-state-metrics"})`:** a reviewer proposed the
`absent()` form with `noDataState: OK`. Rejected for two reasons. (a) Consistency — the sibling
rule in the same group, `observability-pipeline-no-data`, already uses the `count()` pattern, and
its inline comment documents `absent()` as a *past real bug* in this file. (b) Robustness — with
`absent()`, the rule sits permanently in `NoData` while healthy and depends on `noDataState: OK` to
stay quiet, which would also swallow a genuine datasource outage or query error instead of
alerting on it. With `count()`, the rule sits in `Normal` while healthy, and `NoData` stays
reserved for the failure mode it's actually meant to catch. Both expressions were checked live
against Mimir: `count(...)` returns `1` in the healthy state (not `< 1`, not firing); `absent(...)`
returns an empty vector.

Verified facts worth keeping on record: kube-state-metrics runs in namespace **`monitoring`** (not
`grafana`), single replica, and `job="kube-state-metrics"` is the real label — hence
`check_command: "kubectl -n monitoring get pods -l app.kubernetes.io/name=kube-state-metrics"`.

**No `dashboard_url`, deliberately:** the kube-state-metrics dashboard exists (uid
`kubernetes-objects-native`, "Kubernetes Objects"), but isn't linked — it's rendered entirely from
the same metrics that are missing while this alert fires, so it would just be empty. Same reasoning
already used for the `mariadb-galera-backup` rule's missing `dashboard_url`.

## Measurement pitfall for future alert-noise analyses

A `[30d:1m]` subquery against Mimir reports roughly 275 missing minutes for **every** metric, not
because anything is actually down, but because Mimir's retention ends at ~29.81 days — `absent()`
(or any no-data check) sees the pre-retention edge and reads it as a ~4.5 hour outage. Measure
against **28 days**, not 30, to avoid this artifact.

## Rule and group count correction

The rule set is now **24** rules (not 23 — `455edcf` added `kube-state-metrics-down`), organised
into **8** groups, not 7: `core_services`, `backups`, `storage`, `databases`, `cluster_health`,
`observability`, `node_health`, `certificates` — all with `interval: 60s`.

## Related open finding: `mimir` HelmRelease bookkeeping stall

Separately from this branch, on `fix/mimir-upgrade-remediation` (commit `46000f7`, not yet
merged): the `mimir` HelmRelease has read `Ready=False`/`Stalled=RetriesExceeded` since
`2026-07-14T21:23:23Z`. Root cause: a Helm upgrade ran into the default 5-minute timeout while
mimir-distributed pods were crashlooping during the concurrent S3/RGW `AccessDenied` incident, and
`apps/base/mimir/release.yaml` carried no `upgrade:` stanza — so the Flux default
`upgrade.remediation.retries: 0` applied, one attempt was made, and the release has stayed
`Stalled` for 29 days since, even though every pod has run healthy the whole time. Pure bookkeeping
debt, not a live outage. `loki` and `tempo` already had the fix (`upgrade.remediation.retries: 3`).

**This interacts directly with the noise-reduction work above:** `mimir` is in the HelmRelease
matcher regex, so once this branch merges, the persistently-Stalled `mimir` release will keep the
new sustained `core-infra-helmrelease-not-ready` alert firing continuously until the remediation
fix lands — at `repeat_interval: 24h` that's one Discord message/day from a condition that isn't a
real incident. It's the same `mimir` release the `repeat_interval` rationale in Part 3 cited as the
costly example at the old 4h setting.

**Broader systemic gap, open, not yet actioned:** 12 more HelmReleases have no `upgrade:` stanza
and carry the identical risk (one failed attempt during a transient outage → permanently `Stalled`
until someone notices and deletes the failed-release history):

- `apps/base/`: `harbor`, `outline`, `uptime-kuma`, `leantime`, `dependency-track`, `bluemap`,
  `vulpes-backend`, `shlink`, `otis`, `reposilite` (10 — note: `reposilite`'s file is
  `release.yml`, not `release.yaml`, which is why a naive glob for `*/release.yaml` misses it).
- `apps/clusters/feathre-core/apps/`: `otis-dev`, `vulpes-backend-dev` (2).

`harbor` is the riskiest of these: a multi-component chart with PVCs and a Postgres dependency —
exactly the profile that outgrows a 5-minute default timeout. Its `values:` block does contain a
`timeout: 5m0s`, but that's a **red herring** — it's under `trivy:` (the vulnerability-scan
timeout), not `spec.timeout`; `harbor`'s Flux-level timeout is unset just like the other 11.

Recommendation: add `upgrade: {remediation: {retries: 3}, timeout: 20m0s}` (the `loki`/`tempo`/
`mimir` precedent) across these 12, as its own follow-up — not decided or scheduled here.
