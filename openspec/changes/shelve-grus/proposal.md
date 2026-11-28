## Why

Put the grus prototype on ice. The maintainer decided on 2026-11-28 to stop running it for now. grus-dev still runs its controller and web UI in namespace `grus-dev`, holds a load-balancer IP and an internal gateway route, and its web pod restarts regularly on probe timeouts. Running it costs resources and alert noise without a user.

## What Changes

- Remove the `grus-dev-crds` and `grus-dev` entries from `products/layers/feather-core/dev/kustomization.yaml`. Flux prunes the HelmRelease, namespace `grus-dev`, its route, load balancer and network policy, and the three grus CRDs with their custom resources.
- Remove the Flux source `foundation/sources/clusters/feather-core/sources/grus.yaml` and its entry in that directory's `kustomization.yaml`.
- Remove the two grus-dev Certificates `grus-dev-apps-onelite-feather` and `grus-dev-api-apps-onelite-feather` from `foundation/certificates/clusters/feather-core/internal-certs/internal-certificates.yaml`, and their two `certificateRefs` (secrets `grus-dev-apps-onelite-feather-tls` and `grus-dev-api-apps-onelite-feather-tls`) from the listeners in `foundation/networking/clusters/feather-core/gateway/gateway.yaml`. The gateway change lands in the same commit as the certificate change, so no listener points at a removed secret.
- Keep `products/grus/` unchanged in the repository so the prototype can be restored.
- The template bucket `grus-templates-dev` is kept: its storage class `ceph-bucket-fr01` has `reclaimPolicy: Retain`, so the bucket and its objects stay in Ceph when the claim is pruned.
- **Commit type and scope:** the implementation ships as `chore(products)`.
- **Rollback / thaw:** restore the two layer entries and the source in one commit; the bucket is re-bound by name.

## Out of scope

- The grus source repository and its chart (probe timeouts stay unfixed until the project resumes).
- Deleting the template bucket's data.

## Impact

- Capability `grus` (see `specs/grus/spec.md`).
- Changed files: the products dev layer kustomization, the sources kustomization and the removed source file `grus.yaml` (foundation-sources), `internal-certificates.yaml` (foundation-certificates), and `gateway.yaml` (foundation-networking).
- Namespace `grus-dev` and the grus CRDs disappear from the cluster; the internal gateway route and the load-balancer IP are released; the two grus-dev certificates and their gateway listener refs are removed.
