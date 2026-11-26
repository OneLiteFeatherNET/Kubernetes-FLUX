## Why

Discord receives about 39 messages a day, and most of them are not actionable. Over the last 14 days (measured 2026-11-12 to 2026-11-26) there were about 550 messages: 466 from Grafana alerting (293 transitions into Alerting) and 81 from the Flux notification Alert `cluster-errors`. People stop reading a channel that is this noisy, so the alerts that matter get lost in it.

The noise has two sources, and each needs a different fix:

- **Flux transient errors.** The `cluster-errors` Alert forwards Flux status-update timeouts ("failed to update status: timed out waiting for the condition", "etcdserver: request timed out", and similar). All 222 Flux objects are Ready now, so these are rollout and control-plane latency, not stalls. A stuck Kustomization is caught by the Grafana rule "Flux core layer not ready". A stuck HelmRelease is caught only by "Core infrastructure HelmRelease not ready", which covers a fixed list of names. Once the Flux timeouts are excluded, a stuck HelmRelease outside that list would not be reported at all, so this change adds a rule for it.
- **Flapping Grafana rules.** "CNPG Postgres connection saturation high" fired 36 times, 34 of which resolved within 15 minutes, because the connection ratio hovers around the 80% threshold. "Otis health check failing" fired 17 times, all 17 under 15 minutes. "Ceph RGW failed request rate elevated" (5 transitions, 4 flaps) and "Minecraft TPS low" (3 transitions, 3 flaps) show the same pattern at a smaller scale.

Core operator restarts are the largest real source (150 "Core container OOM-killed" transitions). They are fixed by `right-size-core-operators`, not by this change.

This change fixes the flapping rules with hysteresis and debounce, and it removes the transient Flux timeouts from Discord. Grafana becomes the single channel for "not Ready" states. Real failures still reach Discord, and the change does not touch resolved notifications or the notification grouping.

**Success measure:** after 14 days, fewer than 10 Discord messages per day on average (from about 39). Measured with `increase(grafana_alerting_notifications_total[14d])` plus the count of notification-controller `dispatching event` log lines.

**Commit type and scope:** the implementation ships as `fix(observability)`. All changes tune alerting in the observability stack, one capability.

## What Changes

- **Flux Alert `cluster-errors`** (`foundation/sources/base/flux-notifications/alert.yaml`): extend `exclusionList` with regexes for transient timeouts: `timed out waiting for the condition`, `failed to update status`, `unable to update the healthy status`, `etcdserver: request timed out`, `health check failed after`. Stuck Kustomizations and HelmReleases are still reported by Grafana (15 minutes sustained, with resolved) through the rules "Flux core layer not ready", "Core infrastructure HelmRelease not ready" and the new "Flux HelmRelease not ready". Build, SOPS, validation and install errors still reach Discord immediately.
- **Grafana rule "Flux HelmRelease not ready"** (new): in the same group and folder as "Core infrastructure HelmRelease not ready" (`core_services`, folder `Core Services`). Same 15-minute `min_over_time` sustain pattern and `keepFiringFor: 15m` as the core rule. It matches every HelmRelease NOT in the core name list (`name!~"<same list as the core rule>"`), aggregated `by (exported_namespace, name)` (the Discord template requires exactly these two labels), severity `warning`.
- **"CNPG Postgres connection saturation high"**: add a recovery threshold (hysteresis: fire above 80%, resolve only below 70%) through the threshold expression's recovery threshold (`unloadEvaluator`), and `keepFiringFor: 15m`. The implementer must confirm that the field is supported by the provisioned Grafana version (12.4) before relying on it.
- **"Otis health check failing"**: `for: 10m` (was 5m), `keepFiringFor: 10m`.
- **"Ceph RGW failed request rate elevated"**: `keepFiringFor: 15m`.
- **"Minecraft TPS low"**: `keepFiringFor: 10m`.
- **Unchanged on purpose (maintainer decisions):** resolved notifications stay for all severities; `group_by: ['alertname']` stays, because the Discord template depends on it; "Core container OOM-killed" stays as is, because its noise is real and it is handled by `right-size-core-operators`.

## Out of scope

- Adding new alerts. The open change `harden-observability-coverage` adds certificate and etcd alerts. Its new rules should follow the debounce conventions in this change (`for`, `keepFiringFor`, recovery thresholds).
- Changing the Discord template, the contact points or the notification policy tree.
- Alertmanager. It is disabled, and Grafana is the only alert sender besides Flux.
- Otis restart bursts (11 in 14 days, more than 2 restarts in 30 minutes each) are a real application problem and are investigated separately; the rule stays unchanged.

## Impact

- New capability `alert-noise` (see `specs/alert-noise/spec.md`).
- Files: `foundation/sources/base/flux-notifications/alert.yaml`, `services/observability/clusters/feather-core/grafana/alerting/rules.yaml` (one new rule, "Flux HelmRelease not ready", in group `core_services`).
- Layers: `foundation-sources` (Flux Alert) and `services-observability` (Grafana rules). No layer is renamed or split.
- Runtime impact: Flux stops forwarding the excluded messages. A stuck HelmRelease outside the core name list produces one Grafana alert where previously only the Flux message existed. Grafana provisioning reloads the rules from the sidecar ConfigMap; no Grafana pod restart is needed. Currently firing alerts may resolve or re-fire once after the change.

## Relation to other changes

- `right-size-core-operators` removes the largest real source of alerts. This change does not depend on it, and the two can ship in either order.
- `harden-observability-coverage` is open and does not conflict with this change.
