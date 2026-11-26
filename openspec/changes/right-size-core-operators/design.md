## Context

Four core operators run with resource values that were set before they had measured data. Restarts come from two different causes:

- **CPU throttling on leader-elected controllers.** cainjector (72% of CPU periods throttled) and cert-manager-controller (12.5%) lose their lease renewal while throttled and exit with "leader election lost". Their memory is far below the limit, so memory is not the cause.
- **Memory caps.** step-issuer and mariadb-operator run into their memory limit and are `OOMKilled`. Usage is clipped at the limit, so their true demand is unknown until the limit is raised.

The repository already has the pattern for CPU: the step-issuer HelmRelease carries the comment "No cpu limit: CFS throttling made lease renewals time out", and the cert-manager webhook has no CPU limit for the same reason.

## Decision drivers

- Throttling must not break lease renewal, because that is what triggers the restart loop.
- CPU is compressible. A CPU request still guarantees a share of the node, so removing the limit does not starve the controller.
- Memory is not compressible. A leak should stay bounded, so memory keeps a limit.
- Sizing must come from measurement, not guesses, and clipped measurements need a different rule.

## Decisions

### 1. CPU: remove the limit or raise it

**Decision:** remove the CPU limit on all four leader-elected controllers and keep their CPU requests.

**Options considered:**

- **Raise the CPU limit.** Throttling would move to a higher threshold and come back under load. It does not remove the failure mode, and the value would be a guess.
- **Remove the CPU limit (chosen).** The lease can no longer be starved by the limit. The request still reserves scheduling capacity.
- **Keep the limit and add a longer lease duration.** Out of scope: it changes the election timing for every consumer and hides the throttling.

**Consequences:** a runaway controller could take more CPU than its request on a busy node. Memory limits still bound the process, and the request guarantees its share.

### 2. Memory: sizing rule

**Decision:**

- Unclipped containers (14-day maximum below the limit): limit = measured 14-day maximum × 1.5.
- Clipped containers (usage pinned at the limit): limit = double the current limit, then re-measure after 7 days.

**Values:**

| Container | Clipped? | 14d max | New request / limit |
|---|---|---|---|
| cert-manager-cainjector | no (214 of 256) | 214 MiB | 224Mi / 384Mi |
| cert-manager-controller | effectively at limit (77 of 80) | 77 MiB | 96Mi / 160Mi |
| step-issuer manager | yes (127.4 of 128) | 127.4 MiB | 160Mi / 256Mi |
| mariadb-operator controller | yes (254 of 256) | 254 MiB | 256Mi / 512Mi |

**Consequences:** the cert-manager-controller value is set from a maximum that sits within 4% of its limit. Its real demand is therefore also not fully visible, and the 7-day re-measure applies to it as well. The memory limit stays at or above 1.5 times the 14-day maximum for every controller (see the spec).

## Risks / Trade-offs

- **Rollout restarts each operator once.** Covered by the rollout settings above. Certificate issuance is not interrupted for cert-manager; step-issuer pauses briefly.
- **Memory limits still too low for a clipped container.** The 7-day re-measure (task 6) decides whether a second increase is needed.
- **The restarts are not fully explained by CPU.** If restarts continue after the change, the apiserver and etcd latency investigation (out of scope) becomes the next step.

## Migration Plan

1. Merge the proposal PR for this change.
2. Implement the values in the three HelmRelease files, then open the code PR.
3. After merge, Flux reconciles `foundation-certificates-operators` and `foundation-databases-operators`. Confirm both are Ready and each Deployment rolled once.
4. After 7 days, re-measure with the PromQL queries in `tasks.md` and adjust the clipped containers if needed.

**Rollback:** revert the values commit. Helm rolls the Deployments back to the previous limits.

## Open Questions

- **Is the step-issuer and mariadb-operator demand higher than 2× the current limit?** Only the 7-day re-measure can answer that, because the current data is clipped.
- **Does cert-manager-controller need more than 160Mi?** Its 14-day maximum sits within 4% of the old 80Mi limit, so the 1.5× rule is an estimate. The 7-day re-measure decides it.
