## Context

Grus is a Go control plane (REST API, server-sent events, SQLite state) that starts Minecraft server instances and reports them to Velocity. Report 04 of the Grus research lists the rules a new workload has to meet on `feather-core`: Pod Security Admission labels, fixed image tags, memory requests and limits, a Harbor pull secret, a PriorityClass, a Flux HelmRelease pinned to a revision, and a rollout strategy that works with a ReadWriteOnce volume.

The cluster has 10 nodes (3 control-plane, 3 storage, 4 xl). The xl workers `fr01-wrk-xl-03` and `fr01-wrk-xl-04` already have 87 to 100 percent of memory requested, so a large request stays `Pending` there. `products-dev` runs with `wait: true`. A workload that does not start therefore blocks every dev product, as happened during the Ingot load test.

Constraint: the prototype must never affect the production layer, must not expose anything outside the cluster, and must be removable by deleting one layer line.

## Goals / Non-Goals

**Goals**

- Controller, Velocity proxy and Minestom lobbies run as pods in `grus-dev`, deployed from Git.
- The demo in the acceptance task runs through `kubectl port-forward` only.
- The controller can create and delete its own game pods, and nothing beyond its namespace.

**Non-Goals**

- Production use, public access, persistent game worlds, multi-node controller.
- An operator or CRDs for game servers.

## Decision drivers

- A broken prototype must not stall `products-dev` for long.
- Least privilege: the controller holds rights in its own namespace only.
- Reuse the `menura-dev` pattern, so reviewers know the shape.
- Exposure: nothing reachable from outside the cluster.

## Decisions

### 1. Chart from the Git source, not from OCI

**Decision:** `GitRepository` `grus` pinned to `ref.commit`, chart path `./deploy/helm/grus`, `reconcileStrategy: Revision`. This is the `menura-dev` pattern.

**Alternatives considered:**

- *OCI chart from Harbor* (`oci://harbor.onelitefeather.dev/onelitefeather/charts/grus`). This is the target convention (report 04, section 4.1), but it needs a chart push step in a release workflow that does not exist for Grus yet. Rejected for the prototype, to be revisited when Grus has releases.
- *Branch instead of commit.* Rejected: a moving branch gives a new revision on every push and no reproducible deployment. The pin moves with the image tags, in one pull request.

**Consequences:** the commit pin and the image tags are updated together, by hand, in this repository. A chart change in the Grus repository reaches the cluster only after the pin is moved.

### 2. Stay in `products-dev`, no separate layer

**Decision:** the namespace and the HelmRelease belong to the existing `products-dev` layer, which keeps `wait: true`.

**Alternative considered:** a separate layer file with `wait: false` (report 04, section 7.4). It would isolate `products-dev` from a failing prototype. Rejected for now: it adds a layer object and a new ownership key for a prototype. The risk is contained by small requests, `retries: 3`, a short readiness path without external dependencies, and the suspend path. If the prototype blocks `products-dev` in practice, a separate layer is proposed then.

**Consequences:** a blocked `grus-dev` shows up as a blocked `products-dev`. Rollback by suspend or by removing the layer line is required in the rollout notes.

### 3. Runtime game pods are created by the controller, not by Flux

**Decision:** the controller creates game pods directly through the Kubernetes API. The ServiceAccount has a namespaced `Role`: `pods`, `pods/log`, `pods/attach`, and `secrets` with `create`, `get` and `delete`.

**Alternatives considered:**

- *One HelmRelease or Kustomization per game instance.* Rejected: it puts runtime state into Git, makes every scale-up a Flux reconcile, and runs into the Helm rate limits and the 10-minute interval.
- *`ClusterRole`.* Rejected: the controller needs no rights outside `grus-dev`.

**Consequences:**

- Flux does not prune these pods, because they are not in Git. Removing the namespace or the controller's `ownerReferences` removes them.
- Runtime pods must pass the namespace's Pod Security `baseline` rules, or the API server rejects them. They also set memory requests equal to limits, to avoid audit findings. Requirement 6 in the spec covers this.

### 4. Instance limit as a guard against runaway creation

**Decision:** the controller is configured with `maxInstances: 10` for the whole namespace (the default in the research design is 10 per namespace; this prototype uses the same number). Start failures count as backoff, not as retries without limit.

**Consequences:** a bug in the controller that creates pods in a loop stops at 10 pods. Ten small lobbies fit on the xl workers without touching the 90-plus percent nodes.

### 5. Secrets through SOPS and `secretGenerator`

**Decision:** `grus-dev-secrets.sops.env` holds the admin token and the Velocity forwarding secret. The Harbor pull secret is the same encrypted file used by the other products, copied into `grus-dev`. `disableNameSuffixHash: true`, so names are stable.

**Consequences:** a secret change does not restart pods. The rollout notes must say `kubectl rollout restart` after a secret change. Plaintext never enters git; the `.sops.yaml` recipients stay unchanged.

### 6. Access by port-forward only

**Decision:** no `Service` of type LoadBalancer, no HTTPRoute, no DNAT. The demo uses `kubectl -n grus-dev port-forward`.

**Alternatives considered:**

- *LoadBalancer from the Cilium pool* for port 25565. Rejected: it needs a free IP, a BGP route and a pinned address (report 04, section 5), and a Service waiting for an address can hold back `wait: true`.
- *HTTPRoute on `grus-dev.apps.onelite.feather`.* Rejected: it needs a certificate in two foreign files in `foundation` (report 04, section 4.3), which belongs in a separate pull request first.

**Consequences:** nobody but cluster operators can reach the prototype. Players cannot test it. This is intended.

### 7. Pod Security Admission `baseline` enforced, `restricted` as warning

**Decision:** `enforce: baseline`, `warn: restricted`, `audit: restricted`. The Minecraft images may run as root, and `restricted` would reject them.

**Alternative considered:** `enforce: restricted`, as in `menura-dev`. Rejected: the game images are not yet built for non-root and a read-only root filesystem. Report 04 and report 06 section 8.5 both record this.

**Consequences:** `restricted` violations show up as warnings and audit events. They are work items for the Grus image build, not blockers.

### 8. NetworkPolicy in the `menura-dev` pattern, audit only

**Decision:** default deny, Envoy ingress, DNS and HTTPS egress without private ranges, as in `menura-dev/networkpolicy.yaml`. The cluster does not enforce them today (policy audit mode), so they are a target state.

**Consequences:** the prototype is not isolated in practice. Enforcement is decided cluster-wide, not in this change.

### 9. Small requests and xl placement

**Decision:** `priorityClassName: feather-low`. Controller requests 64Mi memory and 50m CPU. Lobby and proxy pods 512 to 768 MiB. An optional `nodeSelector` pins the prototype to `fr01-wrk-xl-01` or `fr01-wrk-xl-02` if the scheduler cannot place it.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| `products-dev` is held back by a `grus-dev` workload that never becomes Ready (`wait: true`) | Small requests, `retries: 3`, readiness without external dependencies, no LoadBalancer in the first pull request, `helm template` and kubeconform before the pull request; suspend path in the rollout notes |
| Controller bug creates pods in a loop | `maxInstances: 10`, start failures back off, `kubectl -n grus-dev delete pod -l grus.olf/pool` as an operator stop |
| Memory on xl-03 and xl-04 is nearly full | `feather-low`, small pods, `Pending` stays `Pending` and does not evict anything, optional `nodeSelector` |
| Game images run as root and fail `restricted` | `baseline` enforced, `restricted` as warning; image work tracked in the Grus repository |
| Image or chart drift from a moving tag | Fixed `prototype-<sha>` tags, pinned `ref.commit`, both changed in one pull request |
| Velocity or Minestom API differs from assumptions | Plugins compile against the pinned Velocity version; early compile errors in the Grus repository |
| Harbor pull fails (`ImagePullBackOff`) | `harbor-pull` copied from an existing product, verified with the sops check before the pull request |
| Runtime pods outlive a broken controller | Pods carry `ownerReferences` where the controller can set them; the namespace is the cleanup boundary |

## Migration Plan

1. Preflight (tasks 1.x): confirm the pinned commit, the images in Harbor, node memory and the chart path.
2. Proposal pull request merges to `main`.
3. Code pull request from a fresh `main` (task 8.1 opens it).
4. Flux reconciles `products-dev`. Verify read-only (tasks 5.x).
5. Acceptance demo through port-forward (task 6.1).

**Rollback:** see the proposal. Remove the layer line, or suspend the HelmRelease.

## Open Questions

- Does the public repository `OneLiteFeatherNET/grus` exist and is it readable without credentials for Flux? Confirm before the code pull request.
- Are the image repositories `harbor.onelitefeather.dev/onelitefeather/grus`, `.../grus-velocity` and `.../grus-demo-minestom` pushed by a `workflow_dispatch` workflow in the Grus repository? Confirm in preflight.
- Which xl worker gets the optional `nodeSelector`, if any? Decided from live node memory in preflight.
