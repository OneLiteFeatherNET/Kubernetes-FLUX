## Context

Two alert paths reach Discord: the Flux notification Alert `cluster-errors` (all Kustomizations and HelmReleases in its listed namespaces, severity `error`) and Grafana alerting (all rules in `services/observability/clusters/feather-core/grafana/alerting/`). Alertmanager is disabled. Both paths report "not Ready" states, so a single stall produces messages from both.

The Flux Alert already has one exclusion, `dependency .* is not ready`, for cascade noise. The Grafana rules "Flux core layer not ready" and "Core infrastructure HelmRelease not ready" already use a 15-minute sustained check through `min_over_time`. The HelmRelease rule matches a fixed list of names, so a new rule "Flux HelmRelease not ready" covers every other HelmRelease (decision 1). The noise comes from transient timeouts that Flux reports as errors and from threshold-edge flapping in Grafana rules.

## Decision drivers

- One channel per state. A stuck layer should produce one alert, not two.
- Real failures must still reach Discord quickly (build, SOPS, validation and install errors).
- Hysteresis and debounce change only when an alert fires and resolves, not what is measured.
- Resolved notifications stay, so the channel shows when a problem ends.

## Decisions

### 1. Single channel for "not Ready"

**Decision:** Grafana is the only channel for "not Ready" states. Flux keeps forwarding build, SOPS, validation and install errors. Transient timeouts and status-update errors are excluded from the Flux Alert.

**Options considered:**

- **Only Grafana.** Chosen for "not Ready". Sustained checks already exist there: the Kustomization rule covers all Kustomizations, and the core HelmRelease rule plus the new "Flux HelmRelease not ready" rule cover all HelmReleases.
- **Only Flux.** Flux reports each error the moment it happens, so every transient timeout would still reach Discord. This keeps the noise.
- **Both.** This is the current state. Every stall produces two messages, and the Flux one fires on transient timeouts that never become stalls.

**Consequences:** a Kustomization that fails to build is still reported by Flux immediately (the exclusion regexes do not match build errors). A Kustomization or HelmRelease that is not Ready for 15 minutes is reported by Grafana, with a delay of up to 15 minutes. HelmReleases in the core name list use "Core infrastructure HelmRelease not ready"; all others use the new "Flux HelmRelease not ready". No stuck HelmRelease goes unreported, so there is no accepted gap.

### 2. Debounce: hysteresis vs. longer `for` vs. higher thresholds

**Decision:** use the mechanism that matches the flap:

- **CNPG connection saturation:** hysteresis (fire above 80%, resolve below 70%) plus `keepFiringFor: 15m`. The metric hovers around the threshold, so the resolve point must be lower than the fire point.
- **Otis health check and Minecraft TPS:** `for` and `keepFiringFor`. The signal is a short dip, so the alert must stay pending or firing long enough to be real.

**Rejected alternatives:**

- **Only a longer `for`.** Delays firing but still resolves in the middle of a flap, so a flapping signal still produces two messages per cycle.
- **Raising the threshold (for example 85%).** Hides a real saturation trend and moves the flap to a new value.

**Consequences:** each alert fires later for a real incident by at most its `for` or `keepFiringFor`. The recovery threshold must be verified against the provisioned Grafana 12.4 before the change is merged.

### 3. Keep resolved notifications

**Decision:** resolved notifications stay for all severities. The Discord template and `group_by: ['alertname']` also stay.

**Rejected alternative:** suppressing resolved messages. This would make the reduction look better, but a firing alert with no resolve message leaves the channel in a wrong state.

**Consequences:** the number of messages per alert cycle stays at two (firing and resolved). Reducing the number of cycles is the goal of decisions 1 and 2.

## Risks / Trade-offs

- **Hysteresis not supported by Grafana 12.4 provisioning.** Fallback: keep the fire threshold at 80% and rely on `keepFiringFor: 15m` alone.
- **Over-excluding a Flux error.** The regexes are narrow phrases. Each is checked against the Flux messages in the 14-day measurement.

## Migration Plan

1. Merge the proposal PR for this change.
2. Implement the Flux exclusion and the Grafana rule changes, then open the code PR.
3. After merge, confirm the Grafana provisioning succeeded (no "Alerting provisioning failing") and the Flux Alert is Ready.
4. After 14 days, measure the Discord volume with the queries in `tasks.md` and compare it with the baseline of about 39 per day.

**Rollback:** revert the values commit. Flux and Grafana reconcile back to the previous rules.

## Open Questions

- **Does the provisioned Grafana 12.4 accept `unloadEvaluator` on the threshold expression?** Confirm before the code PR is opened. Fallback described above.