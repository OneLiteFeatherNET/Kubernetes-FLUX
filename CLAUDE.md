# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **FluxCD GitOps** repository that declaratively manages OneLiteFeather's single Kubernetes cluster, **`feather-core`**. There is no application source code here — only Kubernetes/Flux manifests, Kustomize overlays, Helm values, and a few in-repo Helm charts. The cluster continuously reconciles itself to `main`: a change takes effect **only when committed and pushed to `main`**, after which Flux applies it (GitRepository polls every 1m, root Kustomization every 10m).

## Repository layout

- `clusters/feather-core/` — Flux control plane. `flux-system/` is the bootstrap (GitRepository + root sync). Each `*.yaml` here is one Flux `Kustomization` CR (a "layer") pointing at a path under `infrastructure/` or `apps/`.
- `infrastructure/` — cluster plumbing: Flux **sources**, **controllers/operators**, and **configs** (databases, storage, PKI).
- `apps/` — actual workloads.
- `helm/` — in-repo Helm charts (`shlink`, `outline`, `leantime`, `metabase`, `micronaut`). `micronaut` is the generic chart reused by several Micronaut services (e.g. otis, vulpes).
- `scripts/validate.sh` — local/CI manifest validation. `docs/sops.md` — secrets workflow.

**Two-tier Kustomize pattern.** Everything is a `base` + cluster `overlay`:
- `infrastructure/base/<kind>/<name>/` and `apps/base/<name>/` — portable definitions (HelmRelease, namespace, etc.).
- `infrastructure/clusters/feather-core/<layer>/...` and `apps/clusters/feathre-core/<layer>/...` — cluster overlays that reference a base and patch it (`patches: - path: release.yaml`) and attach secrets.

⚠️ **Path-spelling gotcha:** infrastructure uses `clusters/feather-core/` (correct) but apps uses `clusters/feathre-core/` (misspelled "feathre"). Both are real, intentional paths — don't "fix" one to match the other.

## Flux layer dependency graph

Root `GitRepository flux-system` (ssh, branch `main`) → root `Kustomization` at `./clusters/feather-core`. Layers (all decrypt SOPS via provider `sops` / secret `sops-gpg`, except `internal-certs`):

| Layer | Path | dependsOn |
|---|---|---|
| `base-sources` | infrastructure/.../base-sources | — (root, `wait:false`) |
| `rbac` | infrastructure/.../rbac | — |
| `base-controllers` | infrastructure/.../base-controllers | base-sources |
| `controllers` | infrastructure/.../controllers | base-controllers |
| `base-configs` | infrastructure/.../base-configs | base-controllers |
| `rook` | infrastructure/.../rook | base-sources, base-controllers, base-configs, controllers |
| `rook-fr01` | infrastructure/.../rook-fr01 | rook |
| `configs` | infrastructure/.../configs | base-configs, controllers, rook |
| `internal-certs` | infrastructure/.../internal-certs | controllers |
| `base-apps` | apps/clusters/feathre-core/base-apps | configs |
| `apps` | apps/clusters/feathre-core/apps | base-apps |
| `monitoring` | apps/clusters/feathre-core/monitoring | configs |

Most layers use `wait: true`, so a layer is only "Ready" once its applied resources are healthy — and its dependents block until then. Flux requires a dependency to be `Ready` **at the same git revision** before a dependent reconciles.

## Common commands

```bash
# Validate ALL manifests the way CI does (kustomize build every Flux path + kubeconform).
# Pins kustomize 5.7.1 / kubeconform 0.7.0 / k8s 1.31; skips Secrets; strips SOPS patches.
./scripts/validate.sh

# Render/inspect a single overlay locally (fast iteration).
kubectl kustomize infrastructure/clusters/feather-core/controllers/<name>
# NOTE: a build that pulls in a sops-encrypted *patch* needs the GPG key;
# secretGenerator inputs (*.sops.env) build fine (read as opaque bytes).

# Apply a pushed change immediately instead of waiting for the poll interval:
flux reconcile kustomization <layer> --with-source
flux reconcile helmrelease <name> -n <ns>
flux get kustomizations -A          # health of all layers

# Edit / inspect a secret (see SOPS section)
sops <file>
```

⚠️ **Don't hammer `flux reconcile` in a loop.** Forcing a layer mid-flight flips it to `Reconciling`, which makes every dependent report "dependency not ready" — you create the churn you're trying to clear. After a push, reconcile the changed source once and let the dependency graph settle on its own.

## Secrets — SOPS (PGP)

Full workflow in `docs/sops.md`. Essentials:

- Recipients are listed in **two** files: `.sops.yaml` (repo root) and `clusters/feather-core/.sops.yaml`. Both must stay in sync.
- Encrypted file suffixes: `*.sops.env`, `*.sops.yaml`, `*.sops.json`, `*.sops.crt`, `*.sops.key`, `*.sops.conf` — **and plain `*.env`** (the root `.sops.yaml` regex encrypts those too). `*.yaml` files use field-level encryption (`encrypted_regex` for keys like `*_password`, `*_ca_key`).
- Secrets reach pods via Kustomize `secretGenerator` (`envs:`/`files:`) or `generators:` in an overlay's `kustomization.yaml`; Flux decrypts at apply time.
- Edit in place: `sops path/to/file.sops.env`. Add/remove a member: update both `.sops.yaml`, then re-encrypt everything with `sops updatekeys` (one per file).

## In-repo Helm charts

Charts under `helm/` are pulled by the `helmcharts` **GitRepository** source (which points back at this repo's `main`). External charts come from `OCIRepository`/`HelmRepository` sources defined in `infrastructure/clusters/feather-core/base-sources/`.

⚠️ **When you edit a chart in `helm/`, bump its `Chart.yaml` `version:`.** Flux/Helm caches by chart version; without a bump, edits to templates/values are not re-rendered onto the cluster.

## Conventions & non-obvious behaviors

- **Conventional Commits are enforced in CI** (`.github/workflows/pr-lint.yaml` + `commitlint.config.mjs`): allowed types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`, subject must start **lowercase**, header ≤100 chars. The PR title is the squash-merge subject and is linted too.
- **`flux-validate` CI** runs `scripts/validate.sh` on every PR/push touching `clusters|infrastructure|apps|helm`. Run it locally before opening a PR.
- Overlays set `generatorOptions.disableNameSuffixHash: true`, so generated Secret/ConfigMap **names are stable**. Consequence: changing a secret's contents does **not** roll the consuming Deployment — `kubectl rollout restart` it to pick up new values.
- **Renovate** (`renovate.json`) opens PRs to bump image tags and chart versions; expect `main` to move under you. Re-fetch/rebase before pushing.
- A HelmRelease change updates the cluster ConfigMap/Deployment via a Helm upgrade; if values come from a chart-rendered ConfigMap, the new values only land after the upgrade completes — verify the ConfigMap before restarting a pod to apply them.
