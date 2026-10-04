# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **FluxCD GitOps** repository that declaratively manages OneLiteFeather's single Kubernetes cluster, **`feather-core`**. There is no application source code here — only Kubernetes/Flux manifests, Kustomize overlays, Helm values, and a few in-repo Helm charts. The cluster continuously reconciles itself to `main`: a change takes effect **only when committed and pushed to `main`**, after which Flux applies it (GitRepository polls every 1m, root Kustomization every 10m).

## Repository layout

- `clusters/feather-core/` — Flux control plane. `flux-system/` is the bootstrap (GitRepository + root sync). Each `*.yaml` here is one Flux `Kustomization` CR (a "layer") pointing at a path under `foundation/layers/`, `services/layers/` or `products/layers/`.
- `foundation/` — cluster plumbing, grouped by **domain** (`sources`, `access`, `certificates`, `networking`, `storage`, `databases`, `messaging`, `observability`, `security`, `platform`): Flux sources, controllers/operators, and configs (databases, storage, PKI). `foundation/layers/feather-core/<layer>/` holds one entry `kustomization.yaml` per Flux layer; it only lists domain overlays.
- `services/` — third-party software, grouped by domain (`observability`, `development`, `collaboration`, `automation`, `media`). `services/layers/feather-core/<layer>/` holds one entry `kustomization.yaml` per Flux layer (`automation`, `collaboration`, `development`, `media`, `observability`).
- `products/` — OneLiteFeather's own projects (`otis`, `stelaris`, `vulpes`, `apus`, `sturnus`, `bluemap`); `-dev` variants are siblings. `products/layers/feather-core/{prod,dev}/` are the entries for the `products` and `products-dev` layers.
- `helm/` — in-repo Helm charts (`shlink`, `outline`, `vikunja`, `micronaut`). `micronaut` is the generic chart reused by several Micronaut services (e.g. otis, vulpes).
- `.github/scripts/validate.sh` — local/CI manifest validation.

**There is no `docs/` directory.** Prose documentation lives in Outline, collection *Infrastruktur*, under [Kubernetes-FLUX — GitOps für feather-core](https://outline.onelitefeather.dev/doc/kubernetes-flux-gitops-fur-feather-core-x27ljhcgMA) — architecture, runbooks, secrets handling, incidents, and an archive of design documents and implementation plans. Read it via the Outline MCP tools. New operational findings belong there, not as comment blocks in a manifest; keep in-repo comments to a line or two plus a link.

**Two-tier Kustomize pattern.** Everything is a `base` + cluster `overlay`:
- `foundation/<domain>/base/<component>/`, `services/<domain>/base/<component>/` and `products/<project>/base/<component>/` — portable definitions (HelmRelease, namespace, etc.).
- `foundation/<domain>/clusters/feather-core/<component>/...` (multi-stage components such as `metallb` have stage subdirs) and `services/<domain>/clusters/feather-core/<component>/`, `products/<project>/clusters/feather-core/<component>/` — cluster overlays that reference a base and patch it (`patches: - path: release.yaml`) and attach secrets.

## Flux layer dependency graph

Root `GitRepository flux-system` (ssh, branch `main`) → root `Kustomization` `flux-system` at `./clusters/feather-core` (`prune: true`). The 22 layers below all decrypt SOPS via provider `sops` / secret `sops-age`, have `deletionPolicy: Orphan` (deleting a layer CR never deletes what it owns) and `interval: 10m0s` (`foundation-access`: `1h`):

| Layer | Path | dependsOn |
|---|---|---|
| `foundation-sources` | foundation/layers/feather-core/sources | flux-system (`wait:false`) |
| `foundation-access` | foundation/layers/feather-core/access | — |
| `foundation-observability` | foundation/layers/feather-core/observability | foundation-sources |
| `foundation-platform` | foundation/layers/feather-core/platform | foundation-observability |
| `foundation-certificates-operators` | foundation/layers/feather-core/certificates-operators | foundation-platform |
| `foundation-networking-operators` | foundation/layers/feather-core/networking-operators | foundation-platform |
| `foundation-storage-operators` | foundation/layers/feather-core/storage-operators | foundation-platform |
| `foundation-databases-operators` | foundation/layers/feather-core/databases-operators | foundation-platform, foundation-certificates-operators |
| `foundation-messaging-operators` | foundation/layers/feather-core/messaging-operators | foundation-platform |
| `foundation-certificates` | foundation/layers/feather-core/certificates | foundation-certificates-operators, foundation-networking-operators (`wait:false`) |
| `foundation-networking` | foundation/layers/feather-core/networking | foundation-networking-operators, foundation-certificates |
| `foundation-storage` | foundation/layers/feather-core/storage | foundation-storage-operators, foundation-networking |
| `foundation-databases` | foundation/layers/feather-core/databases | foundation-databases-operators, foundation-storage, foundation-networking |
| `foundation-messaging` | foundation/layers/feather-core/messaging | foundation-messaging-operators, foundation-storage |
| `foundation-security` | foundation/layers/feather-core/trivy | foundation-platform, foundation-storage |
| `services-automation` | services/layers/feather-core/automation | foundation-networking, foundation-storage, foundation-databases, foundation-messaging, foundation-certificates |
| `services-collaboration` | services/layers/feather-core/collaboration | foundation-networking, foundation-storage, foundation-databases, foundation-messaging, foundation-certificates |
| `services-development` | services/layers/feather-core/development | foundation-networking, foundation-storage, foundation-databases, foundation-messaging, foundation-certificates |
| `services-media` | services/layers/feather-core/media | foundation-networking, foundation-storage, foundation-databases, foundation-messaging, foundation-certificates |
| `services-observability` | services/layers/feather-core/observability | foundation-networking, foundation-storage, foundation-databases, foundation-messaging, foundation-certificates (`wait:false`) |
| `products` | products/layers/feather-core/prod | services-development |
| `products-dev` | products/layers/feather-core/dev | services-development |

**Never rename or split a layer by editing its CR.** The Kustomization name is the ownership key; with `prune: true` a rename deletes everything the old layer owned. Instead: freeze the old layer (`prune: false`) → move the objects (one commit per move) → delete the emptied old CR. Walkthrough: [Struktur umbauen ohne Ausfall](https://outline.onelitefeather.dev/doc/how-to-struktur-umbauen-ohne-ausfall-QCsbUjcnXt).

Most layers use `wait: true`, so a layer is only "Ready" once its applied resources are healthy — and its dependents block until then. Flux requires a dependency to be `Ready` **at the same git revision** before a dependent reconciles.

## Common commands

```bash
# Validate ALL manifests the way CI does (kustomize build every Flux path + kubeconform).
# Pins kustomize 5.7.1 / kubeconform 0.7.0 / k8s 1.31; skips Secrets; strips SOPS patches.
./.github/scripts/validate.sh

# Render/inspect a single overlay locally (fast iteration).
kubectl kustomize foundation/<domain>/clusters/feather-core/<component>
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

## Secrets — SOPS (age)

Full workflow: [SOPS — Secrets im Kubernetes-FLUX-Repo](https://outline.onelitefeather.dev/doc/sops-secrets-im-kubernetes-flux-repo-eC2BagUEa9). Essentials:

- Recipients are listed in exactly **one** file: `.sops.yaml` at the repo root — three age public keys, one each for the human maintainer, the cluster, and CI. (`clusters/feather-core/.sops.pub.asc` is the public half of the retired PGP key, kept only to read pre-migration git history.)
- Encrypted file suffixes: `*.sops.env`, `*.sops.yaml`, `*.sops.json`, `*.sops.crt`, `*.sops.key`, `*.sops.conf` — **and plain `*.env`** (the root `.sops.yaml` regex encrypts those too). Everything is whole-file encrypted; there is deliberately no rule for plain `*.yaml`, so `sops -e` on one fails closed. Name a Secret manifest `*.sops.yaml`.
- Secrets reach pods via Kustomize `secretGenerator` (`envs:`/`files:`) or `generators:` in an overlay's `kustomization.yaml`; Flux decrypts at apply time.
- Edit in place: `sops path/to/file.sops.env`. Add/remove a member: update `.sops.yaml`, then re-encrypt **everything** with `./.github/scripts/rekey.sh`.
- ⚠️ **`.sops.yaml` and the ciphertext must change in the same commit.** A file that is validly encrypted but missing the cluster's key breaks every Flux layer that touches it. `.github/scripts/check-sops-encryption.py` (CI) asserts every matched file carries every listed recipient — run it locally after any recipient change.

## In-repo Helm charts

Charts under `helm/` are pulled by the `helmcharts` **GitRepository** source (which points back at this repo's `main`). External charts come from `OCIRepository`/`HelmRepository` sources defined in `foundation/sources/clusters/feather-core/sources/`.

⚠️ **When you edit a chart in `helm/`, bump its `Chart.yaml` `version:`.** Flux/Helm caches by chart version; without a bump, edits to templates/values are not re-rendered onto the cluster.

## Conventions & non-obvious behaviors

- **Conventional Commits are enforced in CI** (`.github/workflows/pr-lint.yaml` + `commitlint.config.mjs`): allowed types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`, subject must start **lowercase**, header ≤100 chars. The PR title is the squash-merge subject and is linted too.
- **`flux-validate` CI** runs `.github/scripts/validate.sh` on every PR/push touching `clusters|foundation|apps|helm`. Run it locally before opening a PR.
- Overlays set `generatorOptions.disableNameSuffixHash: true`, so generated Secret/ConfigMap **names are stable**. Consequence: changing a secret's contents does **not** roll the consuming Deployment — `kubectl rollout restart` it to pick up new values.
- **Renovate** (`renovate.json`) opens PRs to bump image tags and chart versions; expect `main` to move under you. Re-fetch/rebase before pushing.
- A HelmRelease change updates the cluster ConfigMap/Deployment via a Helm upgrade; if values come from a chart-rendered ConfigMap, the new values only land after the upgrade completes — verify the ConfigMap before restarting a pod to apply them.
