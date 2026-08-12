# Discord alert notification redesign

> **Superseded 2026-08-12:** this bullet-list layout and its `reReplaceAll`-over-`summary`
> approach were replaced by a structured-annotation, monospace-table layout (commits `1c0ed60`,
> `8eee395`, `d7ea580`, `55ee41c`). The "no instance cap" decision under Out of scope below was
> also reversed — Discord's 2000-rune hard truncation risked leaving the new code-fenced table
> unclosed, so the current design caps the list at 8 instances. See
> `docs/superpowers/specs/2026-08-12-discord-alert-noise-reduction-design.md` for the current
> design, including a more precise version of the `.CommonLabels`/`.CommonAnnotations` invariant
> noted below. Kept here for history only — do not implement against this document.

**Date:** 2026-07-18
**Status:** Superseded — see note above

## Summary

Grafana's Discord notifications for grouped alerts (multiple instances of the same rule firing
at once, e.g. `flux-core-layer-not-ready` firing for `base-apps`, `configs`, `monitoring`, `rook`,
`rook-fr01` simultaneously) currently repeat the entire message block — including the identical
🔗 Rule link and 📊 Dashboard link — once per instance. For a 5-instance group this produces a
~40-line wall of text where only the one-sentence summary actually differs between blocks. This
redesigns the `discord.title` and `discord.message` templates
(`apps/clusters/feathre-core/base-apps/grafana/release.yaml`, under
`spec.values.alerting.templates.yaml`) to print the shared information once and list instances as
compact single-line bullets.

## Current state

- `discord.title` (line ~27586): `🔴 FIRING`/`✅ RESOLVED` + `· {{ .CommonLabels.alertname }}` — no
  indication of how many instances are in the group.
- `discord.message` (line ~27587-27601): `{{ range .Alerts }}` prints, per instance: the full
  `summary` annotation (including its `Check: <command>` suffix), the `severity` label, the 🔗
  Rule link (`.GeneratorURL`), and the 📊 Dashboard link (`.Annotations.dashboard_url`, if set) —
  every field except `summary` is identical across instances of the same rule, so this is pure
  repetition once more than one instance fires.
- `policies.yaml`'s `group_by: ['alertname']` is correct as-is and out of scope for this change —
  the problem is the per-instance formatting inside an already-correctly-grouped notification, not
  the grouping itself.

## Design

**`discord.title`:** unchanged apart from one addition — append ` (Nx)` after `FIRING`/`RESOLVED`
when more than one instance is in that status (`len .Alerts.Firing` / `len .Alerts.Resolved`).
Exactly one instance keeps today's un-suffixed form.

**`discord.message`:** restructured from "repeat everything per instance" to "shared info once,
then a compact list":

1. `severity:` printed once, from `.CommonLabels.severity` (safe: every current rule sets
   `severity` as a static rule-level label, not templated per instance, so it's always common
   across a group).
2. `🔗 Rule:` printed once, using `(index .Alerts 0).GeneratorURL` — this link points at the rule's
   view page, not a per-instance view, so it's identical across all instances of one rule already;
   taking it from the first alert is not a simplification, it's removing dead repetition.
3. `📊 Dashboard:` printed once, conditionally on `.CommonAnnotations.dashboard_url` (several rules,
   e.g. `mariadb-galera-backup-job-failed`, `pods-stuck-terminating`,
   `observability-pipeline-no-data`, don't set this annotation at all — the line is omitted
   cleanly for those, verified live).
4. `Firing (N):` / `Resolved (N):` sections (only rendered when that section is non-empty — a
   notification can in principle carry both simultaneously if some instances resolved while
   others are still firing), each listing one `•` bullet per instance:
   `{{ reReplaceAll "^(.*?\.) Check:.*$" "$1" .Annotations.summary }}` — strips the trailing
   `Check: <command>` clause via regex, keeping just the descriptive sentence. Chosen over a
   per-rule "short label" scheme (e.g. printing just `$labels.persistentvolumeclaim` /
   `$labels.node`) because the 15 existing rules have no consistent label shape to key off of
   generically (Flux rules use `name`/`exported_namespace`, PVC rules use
   `persistentvolumeclaim`/`node`, others carry only raw exporter labels) — the regex approach
   works against the one thing every rule's `summary` annotation already has in common: prose
   text, optionally ending in a `Check: ...` clause. If a future rule's summary doesn't end in
   `Check: ...`, the regex simply doesn't match and `reReplaceAll` returns the string unchanged
   (verified live) — no breakage, just no truncation for that one rule.

The `Check: <command>` text itself is not lost — it stays reachable via the 🔗 Rule link (opens the
rule in Grafana, which shows the full annotation) — this was an explicit trade-off the user
confirmed (compact bullets over inline commands).

## Verification performed during design

Every piece of this template was tested against Grafana's live template-test API
(`POST /api/alertmanager/grafana/config/api/v1/templates/test`) with realistic alert payloads
before being written up here — not assumed from documentation. Confirmed:
- `.Alerts.Firing` / `.Alerts.Resolved` give correct per-status counts and slices.
- `reReplaceAll` is available and behaves correctly both on matching input (strips the `Check:`
  clause) and non-matching input (returns the string unchanged).
- `.CommonLabels.severity` and `.CommonAnnotations.dashboard_url` correctly resolve to empty/falsy
  when not set, and the `{{ if }}` guards around them cleanly omit those lines.
- The full 5-instance `flux-core-layer-not-ready` scenario from the reported screenshot renders as
  intended: title `🔴 FIRING (5x) · flux-core-layer-not-ready`, message body 9 lines instead of the
  original ~40.

## Out of scope

- `policies.yaml` grouping strategy (`group_by: ['alertname']`) — already correct, not touched.
- `contactpoints.yaml` — untouched, same Discord webhook contact point.
- Per-rule annotation changes — no changes to any of the 15 alert rules' `summary`/`dashboard_url`
  annotations; this is purely a notification-rendering change.
- A cap on the number of bullets shown for very large groups (e.g. "and N more..." past some
  threshold) was considered but not included — no rule in this repo currently has a label
  cardinality large enough to make a single notification unreadably long even fully expanded
  (worst case observed: 5 instances). Revisit if a future rule's label set can realistically fan
  out to dozens of simultaneous instances.
