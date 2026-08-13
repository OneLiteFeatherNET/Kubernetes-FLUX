# Discord alert noise reduction & table layout — design

**Date:** 2026-08-12 (updated same day after `455edcf`, `959f39a`, then again after `20949ad`,
`8e03ff7`)
**Status:** Shipped — commits `1c0ed60`, `8eee395`, `d7ea580`, `55ee41c`, `455edcf`, `959f39a`,
`20949ad`, `8e03ff7` on `apps/clusters/feathre-core/base-apps/grafana/release.yaml` (branch
`feat/grafana-alert-noise-reduction` for the first six; `20949ad`/`8e03ff7` landed via PR #118/#119
directly on `main`); `33c51cc` (PR #120, embed URL absoluteness fix) landed the same way.

> **Part 4 (message layout) superseded 2026-08-12, same day:** the monospace code-block table
> described in Part 4 below shipped, then was replaced hours later by real Discord embeds —
> commits `20949ad` (PR #118) and `8e03ff7` (PR #119, hotfix). See the new **Part 4 — Discord
> message layout: embeds** section (replacing the original Part 4 content) and **Parts 5–8** below
> for the embed design, the delivery-reliability regression it introduced, the production incident
> it caused, and the fix — all new material appended to this same document, since Parts 1–3 (the
> PromQL sustain check, structured annotations, and routing/grouping timings) are unaffected and
> remain the current design. Parts 5–8 also cover PR #120 (`fix(grafana): only set the embed url
> when it is absolute`, commit `33c51cc`).

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

## Part 4 — Discord message layout: embeds (`20949ad`, `8e03ff7`)

**This section replaces the original Part 4 (below), which shipped a monospace code-block table
in `8eee395`/`55ee41c` and was live for a few hours on 2026-08-12 before being replaced by real
Discord embeds.** The original text is kept as a subsection (### Superseded: monospace table
layout) because the escaping constraints and Discord limits it documents are still the right
mental model, just applied to a different payload shape.

### Why webhook, not discord

The contact point's `type` changes from `discord` to `webhook`, because only the webhook notifier
supports `settings.payload.template` (new in Grafana 12.0, `receivers/webhook/v1/config.go`;
confirmed live via `GET /api/v1/ngalert/notifiers`). When `payload.template` is set,
`settings.title` and `settings.message` are ignored — they stay in the YAML as
`discord.title`/`discord.message` only as a rollback target (see Part 8).

`uid: discord_webhook` and `name: discord` are both unchanged. This matters because
`policies.yaml`'s routes reference the contact point by **name**, not `uid` — so the existing root
route and the two rule-level exceptions kept working across the type change with no edits to
`policies.yaml` itself. `settings.url` also stays at the exact same YAML position
(`contactPoints[0].receivers[0].settings.url`) so the Flux `valuesFrom` targetPath
`alerting.contactpoints\.yaml.secret.contactPoints[0].receivers[0].settings.url` keeps resolving —
the real webhook URL exists only in the `grafana-discord` Secret, never in git.

### The payload is a data structure, not a string

The template does not assemble JSON by string concatenation. `coll.Dict`, `coll.Slice`, and
`coll.Append` build a real Go data structure (map/slice), and `data.ToJSON` serializes it — that
call is `encoding/json.Marshal` under the hood (`templates/gomplate/data.go`). Because of that,
quotes, backslashes, newlines, and control characters inside a label or annotation value cannot
structurally break the JSON: `json.Marshal` handles all the escaping. Verified live with a label
value of `mon"i\tor` and an annotation containing `" \ & <b>`, both of which came back correctly
escaped (`\"`, `\\`, `&`, `<`) in the rendered payload.

**Grafana's `coll` package here only exposes `Dict`, `Slice`, and `Append`.** `coll.Merge`,
`coll.Omit`, `coll.Has`, and `coll.Keys` all fail live with `can't evaluate field X in type
interface {}` — none of them are wired into this template context. The practical consequence: a
key cannot be conditionally removed from an already-built dict. Where the payload needs two
variants of the same object that differ by one key (see the `url` handling in the PR #120 section
below), the template builds two separate `coll.Dict` calls rather than building one and stripping
a key. Field
order inside the dict is irrelevant either way — `json.Marshal` sorts map keys, so the two
approaches would render identically even if key removal were available.

### The backtick escape

A literal backtick in the effective template text would terminate the Helm raw string
(`{{ \`...\` }}`) early, the same constraint the original monospace-table design (below) already
had to work around. The payload template obtains one via `{{ $bt := "`" }}`: Helm does not
process `\u` escapes inside a raw string, but a Go template string literal does, so the ```
survives Helm's pass unevaluated and becomes a real backtick when the Go template itself
evaluates. The `check_command` field wraps its value in this backtick pair
(`` `%s` ``-equivalent) so Discord renders it as inline code.

### `object_matchers` is unusable here

Unrelated to the payload template itself, but shipped in the same change set: the fallback child
route in `policies.yaml` (Part 5) had to use the string form `matchers:` rather than
`object_matchers:`. The Grafana Helm chart re-serializes `spec.values` through `toYaml`, which
renders the `=` operator of an `object_matchers` triple as a bare YAML scalar — and YAML resolves a
bare `=` to the special `tag:yaml.org,2002:value`, not a string. The rendered `policies.yaml`
config failed to parse at all with that form. The string-matcher form has no standalone `=` token
and round-trips cleanly through the rendered ConfigMap. Found during a render round-trip check,
not in review — a silent config-parse failure here would have disabled alert routing entirely.

### Discord's hard limits (still the binding constraint)

Same category of constraint as the old table layout, applied to embed fields instead of a code
fence: `title` 256 runes, `description` 4096, 25 fields per embed, `field.name` 256, `field.value`
1024, `footer.text` 2048, 10 embeds per message, and 6000 summed across everything in the message.
Exceeding any of these is a hard failure — Discord returns HTTP 400 and the webhook notifier drops
the message with **no retry** (see Part 5). Every `printf` precision in the template is a limit
guard sized against one of these numbers.

**Instance cap: 12, computed, not guessed.** The binding limit is `field.value` ≤ 1024. Worst-case
row length is 73 runes (1 backtick + 27-rune padded object column + 1 backtick + 1 space + 30-rune
location + 12 runes for `  (resolved)` + 1 newline). `12 × 73 = 876`, plus 20 runes for the overflow
("N more") line = 896 ≤ 1024, with one more row (13 × 73 = 949 + 20 = 969) still under the limit
but cutting the margin closer — 12 was chosen to keep headroom. Measured worst case across the
template-test cases run for this change: 172–758 runes of 6000 total across all fields — nowhere
near the ceiling day-to-day; the cap exists for the pathological case, not the common one.

### Layout

- `title`: a status emoji (🔴 firing/critical, 🟠 firing/warning, ✅ resolved) plus the rule name,
  made clickable via the embed's top-level `url` (see the PR #120 section below for why that URL
  isn't always set).
- `description`: the `problem` annotation, plus an optional dashboard link line.
- Up to 3 inline header fields: `Severity`, `Location` (when common across instances),
  `Since`/`Resolved` (age or resolution time). The resolution time is `HH:MM` in `Europe/Berlin`.
- The instance list itself: one `inline: false` field, one row per instance (object + location).
- `Check` (`check_command`, wrapped in the backtick pair above): the last field.
- `footer.text`: `grafana_folder`. `timestamp` is passed through `date "2006-01-02T15:04:05Z07:00"`
  — Discord renders this in the *viewer's* local timezone, not Grafana's or UTC, and renders it
  **absolutely** ("Today at 09:20"), not as a relative "x minutes ago". That's the reason the
  age/resolution field still exists as its own line — the timestamp alone doesn't convey elapsed
  time the way a relative-time renderer would.
- Colors: `15548997` (red, firing/critical), `16426522` (orange, firing/warning), `5763719`
  (green, resolved).

**The alignment trick:** Discord renders inline code (`` `...` ``) in a monospace font and
preserves internal spaces, so the object column is padded with `printf "%-27.26s"` *inside* a
code span — the location column stays visually aligned without needing a full code-fenced block,
which was the thing that could tear open mid-message under truncation in the old design. Padding
is only applied when instances in the group actually differ by location; a single shared location
renders as one plain header field instead (see Part 1/Layout note above about
`.CommonAnnotations.location`).

**Honest limitation:** in the three header fields, Discord renders the field name *above* its
value, not to the left of it — the "label left, value right" mental model from the old monospace
table only applies inside the instance-list field (object column left, location column right via
padding), not to the header fields.

### Superseded: monospace table layout

Kept for history — the escaping/limits reasoning below still applies conceptually, but the
contact point is no longer `type: discord` and this layout no longer ships. Do not implement
against this subsection; see the embed design above instead.

#### Layout

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

#### Verified template engine limits

Tested live against Grafana 12.3.1 via the templates-test endpoint (below). **No Sprig functions,
no `humanizeDuration`, no arithmetic (`math.Sub` does not exist)** — all three fail with "function
not defined". Available: `printf`, `date`, `tz`, `reReplaceAll`, `time.Now.Sub`, and methods on
`time.Time`.

Consequence: alert age is derived by regexing `time.Duration.String()` output
(`^(([0-9]+h)?([0-9]+m)?).*$`), because there's no way to subtract components directly. The
"instances hidden" overflow line prints the **total** instance count rather than the remainder
past the cap, for the same reason — there's no subtraction available to compute "N more".

#### Escaping constraints

The Grafana Helm chart renders the `alerting:` values block through `tpl`, so every Grafana
template has to be written as a Helm **raw string** (`{{ \`...\` }}`). Consequence: the effective
template text must contain **no backtick** (would close the raw string early) and **no single
quote** (Helm's `toYaml` may render a single-line scalar single-quoted, which would then appear
inside the raw string and break the surrounding Helm parse).

#### Discord's own limits (for the old `discord.message` content field)

- `message` → Discord's `content` field: hard-truncated at **2000 runes**, with a marker appended
  on truncation. A cut landing inside the code fence would leave it unclosed and wreck the
  rendering of the whole message — this is why the instance list is capped at **8** rows regardless
  of how many instances are actually firing.
- `title` → `embeds[0].title`: 256 runes.
- Measured worst case across the 9 test cases below: **726 runes** (~2.75× headroom under 2000).
  After the column-width fix in `55ee41c`, the worst observed render (3 full-length Galera PVC rows
  plus a 36-rune release name cut to 26) was **566 runes**.

#### Testability

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

**This endpoint only renders — it never validates**, which is exactly the gap that caused the
outage in Part 6: it reported success for both the working table template and the later broken
`discord.payload` trim-marker case alike, because `Validate()` is a separate code path this
endpoint never calls.

At the time this subsection was written, the contact point was still `type: discord` with
`discord.title`/`discord.message` referenced directly from `contactpoints.yaml`. See Part 4 above
for the current `type: webhook` / `payload.template` setup.

## Part 5 — Notification delivery regression, and the fallback that catches it

The embed payload template buys a real Discord UI at the cost of a retry regression, and that
regression needs a safety net.

**The regression:** the webhook notifier returns `(false, tmplErr)` when the payload template
fails to render, and treats any non-2xx HTTP response as `retry=false`
(`receivers/webhook/v1/webhook.go`). `notify/notify.go`'s `RetryStage` gives up immediately on
`retry=false` — there is no retry, the message is simply gone. The old `discord` notifier only
logged a warning on a template error and sent anyway. This is the one genuine regression of the
embed switch: without a guard, a future typo in the template would silently cost notifications
with no error visible anywhere except a Grafana log line.

**Three pieces of mitigation, all shipped in `20949ad`:**

1. A second contact point, `discord-fallback` (`uid: discord_fallback`), of the **old** `type:
   discord` — the fault-tolerant notifier — with `use_discord_username: true` and no custom
   payload, so it uses Grafana's default rendering. It deliberately depends on **no template at
   all**, so a template error can never take down the exact path that's supposed to report template
   errors. Same Discord channel; the URL comes from a second `valuesFrom` entry on the same
   `grafana-discord` Secret targeting `contactPoints[1]`, so git still holds only a placeholder.
2. A meta alert rule, `alert-notifications-failing`, in the `observability` group:
   `(sum(increase(grafana_alerting_notification_requests_failed_total[10m])) or vector(0)) +
   (sum(increase(grafana_alerting_notifications_failed_total[10m])) or vector(0))`. Both counters
   are registered `WithSkipZeroValueMetrics`, so neither series exists at all while it's 0 — a bare
   `sum()` would evaluate to `NoData`, not 0, hence the `or vector(0)` around each term. Either
   counter may be the one that increments on the webhook failure path, so both are summed.
   `noDataState: OK` (not `Alerting`): with `or vector(0)` the expression always returns a value
   while Mimir itself is reachable, so `NoData` here could only mean the datasource is down —
   already covered by `observability-pipeline-no-data`, and not evidence that notifications
   specifically are broken. `execErrState: Alerting`, matching all other rules in the file.
3. A child route in `policies.yaml` sending that one rule to `discord-fallback`, so it doesn't
   report its own failure over the path it's reporting as broken. Everything else keeps routing to
   `discord` unchanged; the root route (`group_by: ['alertname']`, `group_wait: 1m`,
   `group_interval: 5m`, `repeat_interval: 24h`) is untouched.

**Limits of the meta rule, worth stating plainly:** `increase()` needs two samples to produce a
value, so a single isolated delivery failure stays invisible — only sustained failure trips this
rule. And if delivery is broken badly enough that *nothing* gets through, this rule's own
notification is subject to the same failure mode it's watching for; the honest floor is "a broken
template surfaces at the next alert that actually fires," not before.

**The mitigation proved itself the same day it shipped.** During the 2026-08-12 rollout, two
notification deliveries failed (the startup race described in Part 7). `alert-notifications-failing`
fired at 19:35:20, was delivered successfully via `discord-fallback`, and resolved at 19:38:49 —
recovering on its own once the affected pods finished loading the new provisioning config.

## Part 6 — The trim-marker outage (`8e03ff7`, hotfix for `20949ad`)

**This is the most expensive mistake in this change set — it caused a production outage — and is
documented in detail on purpose.**

**Root cause:** every notification template in this file must open with `{{ define`, never
`{{- define`. Grafana's `NotificationTemplate.Validate()`
(`pkg/services/ngalert/api/tooling/definitions/alertmanager_validation.go`) decides whether a
template body already declares a `define` block using `regexp.MatchString(`\{\{\s*define`,
content)`. `\s*` cannot consume a `-`, so `{{- define "discord.payload" -}}` does not match that
regex. Grafana concludes the body has no `define` at all and wraps it in a **second**
`{{ define "discord.payload" }} ... {{ end }}`. The resulting nested define then fails the real
`text/template` parse inside `TemplateDefinition.Validate()` (`grafana/alerting`,
`templates/template_data.go`) with `template: :2: unexpected <define> in command`.

`discord.title` and `discord.message` were never affected — they happen to open with `{{ define`
with no trim marker already. `discord.payload`, introduced in `20949ad`, opened with
`{{- define "discord.payload" -}}` and hit this exactly.

**Consequence — CrashLoopBackOff, silently:** Grafana's provisioning module aborts on this error at
startup, and the pod goes into `CrashLoopBackOff`. Because `maxUnavailable: 25%` of 2 replicas
rounds down to 0, the old pods were never torn down and kept serving the UI — so the outage was
not visible to anyone looking at Grafana. At the same time, the old pods (which poll the
provisioning DB every 60s) had already loaded the new config from the DB, including the contact
point now pointing at `discord.payload` — a template that, because provisioning aborted, was never
actually committed. The next real alert would have been silently dropped by a contact point
pointing at a template that doesn't exist.

**And the bitterest part:** provisioning aborts on this error *before* it gets to policies and
rules. The two safety nets built specifically to catch a failure like this one — the
`discord-fallback` child route and the `alert-notifications-failing` meta rule from Part 5 — were
themselves not provisioned yet, because they come later in the same provisioning pass that just
aborted.

**Trim markers inside the template body are fine.** The regex only inspects the opening `define`
line; every `-}}`/`{{-` elsewhere in `discord.payload` (there are many, for whitespace control) is
unaffected.

**Fix:** remove the single leading `-` from `{{- define "discord.payload" -}}`, i.e.
`{{ define "discord.payload" -}}`. Output-neutral: `Validate()` runs against
`strings.TrimSpace(template)`, so there was never any leading whitespace for that marker to trim in
the first place. The closing `-}}` and every other trim marker in the body are untouched.

**Neither existing guard would have caught this.** `scripts/validate.sh` checks YAML syntax and
kustomize/kubeconform schema conformance — this is Grafana-side semantic validation of a string
value, invisible to both. `POST /api/alertmanager/grafana/config/api/v1/templates/test` only
*renders* the submitted body; it never runs `Validate()`, so it reported success for the broken
template too. The only reliable pre-merge check is to replicate Grafana's exact two-step logic
(the regex, then the wrap) against the pinned `github.com/grafana/alerting` version Grafana itself
uses, and parse the result with `text/template` — which is how the fix in `8e03ff7` was verified:
against `grafana/alerting` pinned to the pseudo-version Grafana v12.3.1 itself resolves
(`v0.0.0-20251120161053-ee90fc928c01`, including its `prometheus/alertmanager` →
`grafana/prometheus-alertmanager` fork replace), with a control run that reproduced the exact
production error before the fix and confirmed a clean parse after it.

## Part 7 — Startup race (every Grafana restart, not just this one)

The Alertmanager applies the **stored** config on pod start before provisioning has a chance to
commit the new templates/contact points. Both new pods in the `20949ad` rollout therefore came up
briefly still running the previous config, tried to notify with it, and failed with `template
"discord.payload" not defined` — the two failed deliveries that tripped the meta rule in Part 5.
The new config landed roughly 33 seconds later, and the notification groups that were still open at
that point were retried successfully — nothing was actually lost in this particular rollout.

The general risk this leaves behind: because the webhook notifier does not retry (Part 5), any
notification whose delivery attempt lands inside this window — roughly the first 60 seconds after
a Grafana pod restart, based on the provisioning poll interval — can be silently dropped rather
than merely delayed. This is a standing cost of any Alerting-affecting change that triggers a pod
roll, not something specific to the embed switch, and there is no mitigation for it beyond Part 5's
fallback route (which itself depends on the new config already being live to route correctly).

## Part 8 — Deployment ordering and rollback

**`contactpoints.yaml` and `templates.yaml` changes must land together, in the same commit/PR.**
The pod's `checksum/config` annotation hashes `grafana.configData` — which is `templates.yaml` —
but not the config Secret that `contactpoints.yaml` renders into. A change to `contactpoints.yaml`
alone therefore does not trigger a pod rollout, and since Grafana only reads provisioning config at
startup, that change would silently not take effect until some unrelated change happened to roll
the pod later.

**`discord.message` (and `discord.title`) are kept, unused, as a rollback target.** Switching the
contact point's `type` back to `discord` with no other changes restores the pre-embed behavior
exactly, because those templates were never removed. Plan is to delete them once the embeds have
run stable for about a week; not done as of this writing.

**Side effect: the nflog key changes.** The Alertmanager's notification log key includes the
integration name, which moves from `discord[0]` to `webhook[0]` when the contact point's `type`
changes. Any alert that was actively firing at the moment of the cutover lost its `repeat_interval`
bookkeeping under the old key and got one immediate extra repeat notification under the new key —
a one-time, cosmetic side effect of the type change, not a recurring concern.

## PR #120 — embed URL must be absolute (`33c51cc`)

Grafana's built-in synthetic test alert (fired via the "Test" button in the UI) has no
`GeneratorURL`. Before this fix, the template's `url` expression was `or $f.GeneratorURL
.ExternalURL`, and Grafana appends `?orgId=1` to that empty `GeneratorURL`, making it a non-empty
string — so `or` picked the relative `?orgId=1` value instead of falling through to
`.ExternalURL`. Discord rejects an embed with a relative `url` outright, with HTTP 400, dropping
the whole message (no retry, per Part 5). Real alerts always carry an absolute `GeneratorURL`, so
only the built-in test button was ever affected — this was not seen in production alert traffic.

**Fix:** build the URL in two steps rather than a single `or`. First try `$f.GeneratorURL` against
`match "^https?://"`; if that doesn't match, try `.ExternalURL` the same way; if neither matches,
omit the `url` key from the embed dict entirely rather than setting it to something invalid. This
is the two-dict-variant pattern from Part 4 (`coll` has no way to delete a key from an
already-built dict, so the template builds the embed dict twice — with and without `url` — rather
than building it once and stripping the key). `match` is Alertmanager's `regexp.MatchString`
helper and is available in this template context (unlike the arithmetic/Sprig functions ruled out
in the original Part 4 below).

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
