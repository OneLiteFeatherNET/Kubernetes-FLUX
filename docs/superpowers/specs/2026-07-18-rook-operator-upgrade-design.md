# Rook Operator Upgrade 1.18 → 1.20 — Design

## Goal & scope

Upgrade the `rook-ceph` Helm chart in `infrastructure/clusters/feather-core/rook/release.yaml`
from the current constraint `>=1.18.0 <1.19.0` to `1.20.2`, following Rook's mandatory
sequential upgrade path (Rook does not support skipping minor versions). This plan covers
**only the operator**. Ceph itself stays on the Squid line (`v19.2.5`, already pushed on
branch `fix/ceph-bump-v19.2.5` as a separate, unrelated change) — a Ceph major upgrade to
Tentacle is explicitly out of scope and would be its own future spec.

Each stage is its own pull request, merged and confirmed `Ready` on the cluster before the
next PR is opened. This is required, not just tidy process: FluxCD reconciles the *final*
tree state of `main` on every poll — it does not replay intermediate commits. If both
version bumps landed in one merged PR, Flux would instruct Helm to upgrade the release
directly from 1.18 to 1.20 in a single operation, which is the exact unsupported jump this
plan exists to avoid.

## PR 1 — Rook 1.18 → 1.19

- File touched: `infrastructure/clusters/feather-core/rook/release.yaml`
- Change: `version: ">=1.18.0 <1.19.0"` → `version: ">=1.19.5 <1.20.0"` (the `1.19.5` floor
  matches Rook's own documented prerequisite for the later 1.20 jump). Flux/Helm resolves to
  the latest matching patch at merge time (currently `1.19.7`).
- No `csi.*` value changes needed — in 1.19 CSI is still configured inside the `rook-ceph`
  chart itself, so the existing tolerations/resources/host-network settings keep working
  unchanged.
- No new Flux sources or Kustomization layers — same `rook` layer, same `HelmRepository`.
- Local validation before commit: `./scripts/validate.sh` plus a manual
  `kubectl kustomize infrastructure/clusters/feather-core/rook`.
- Post-merge rollout: reconcile once
  (`flux reconcile kustomization rook --with-source` — do not loop this), then confirm
  health before opening PR 2: `flux get kustomizations -A` shows `rook` Ready, the operator
  pod is running the new image, and `ceph status` (via the toolbox pod) reports
  `HEALTH_OK`.

## PR 2 — Rook 1.19 → 1.20 + CSI operator migration

- File touched: `infrastructure/clusters/feather-core/rook/release.yaml` — bump to
  `version: ">=1.20.0 <1.21.0"`, and remove the entire `csi:` block (host-network flag,
  driver enable flags, grpc-metrics flag, tolerations, per-container resource specs), since
  CSI is no longer configured through this chart as of 1.20.
- New files, same `rook` Flux layer (no new Kustomization/dependency-graph layer needed):
  - `infrastructure/clusters/feather-core/base-sources/ceph-csi-operator.yaml` — new
    `HelmRepository` pointing at `https://ceph.github.io/ceph-csi-operator-charts`,
    registered in `base-sources/kustomization.yaml`.
  - `infrastructure/clusters/feather-core/rook/csi-drivers-release.yaml` — new
    `HelmRelease` for the `ceph-csi-drivers` chart, added to `rook/kustomization.yaml`.
- Value migration (best-effort mapping; RBD shown, CephFS mirrors it):
  - `csi.enableRbdDriver` / `csi.enableCephfsDriver` → `drivers.rbd.enabled` /
    `drivers.cephfs.enabled`
  - `csi.rbdPluginTolerations` → `drivers.rbd.nodePlugin.tolerations` (keeps the
    `node-role.feather/storage` toleration so plugin pods still schedule on
    `fr01-str-01..03`)
  - `csi.csiRBDPluginResource` → `drivers.rbd.nodePlugin.resources`;
    `csi.csiRBDProvisionerResource` → `drivers.rbd.controllerPlugin.resources`
  - `nfs.enabled` / `nvmeof.enabled` → both `false` (unused on this cluster)

### Open question (must be resolved during implementation, not assumed here)

The `ceph-csi-drivers` chart's `resources` fields appear to be one block per pod-side
(`controllerPlugin` / `nodePlugin`), not the current per-container granularity (separate
limits for `csi-provisioner`/`csi-resizer`/`csi-attacher`/`csi-snapshotter`/
`liveness-prometheus`). Whether `cephConnections`/`clientProfiles` need manual mon
endpoints or are auto-wired by Rook against the existing `rook-ceph-fr01` `CephCluster` is
also unconfirmed. This requires reading the actual chart templates (not just its docs)
before the exact `values` are written.

## Verification & rollback

- Between PRs: `flux get kustomizations -A` (Ready), `ceph status` via toolbox
  (`HEALTH_OK`), CSI plugin pods Running on all 3 storage nodes, no PVC mount regressions on
  a sample workload.
- Rollback: revert the merge commit — chart version constraints are declarative, so
  Flux/Helm rolls the release back to the previous chart version on the next reconcile. No
  data-plane changes are involved in either stage.

## Testing

- `./scripts/validate.sh` before every commit (CI parity).
- `kubectl kustomize infrastructure/clusters/feather-core/rook`, and once written,
  `helm template` against the new `ceph-csi-drivers` chart, to catch schema mistakes before
  they reach the cluster.

## Out of scope

- Ceph major-version upgrade (Squid → Tentacle).
- Any change to the `rook-fr01` layer / `CephCluster` CR itself.
