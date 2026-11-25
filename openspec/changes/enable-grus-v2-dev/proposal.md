## Why

Grus v2 can run game servers on machines outside Kubernetes through an agent, keep its templates in S3, and read its pools from custom resources. To prove that on `feather-core`, `grus-dev` has to run the v2 build, and the first external machine, `mc01`, has to reach the controller and be reachable from the Velocity proxy pods.

BLUF: this change moves `grus-dev` to the Grus v2 build and adds what v2 needs around it: an S3 bucket for templates, the CRD watch, an internal API hostname `grus-dev-api.apps.onelite.feather` that exposes only the agent paths, and two network paths (gateway to controller, proxy to `mc01`). The agent on `mc01` and the CloudNet installation there are not touched by this repository. **Depends on `add-grus-crds`** (the CRDs must exist before the controller watches them).

## Current state

- `grus-dev` runs the prototype: controller, Velocity proxy, Minestom lobbies, web UI. Images and the GitRepository `grus` are pinned to the prototype commit `4839dd3`. Templates are stored on the controller's volume.
- The web UI is on the internal gateway at `grus-dev.apps.onelite.feather` and reaches only `grus-web`. The controller API (port 8080) is open only inside the namespace.
- `products/grus/clusters/feather-core/grus-dev/networkpolicy.yaml` is default deny plus a few allows. The comment there says the policies are audit only until enforcement is decided cluster-wide. Reachability from the proxy pod to `mc01` was verified on 2026-11-25 (TCP to port 22 over IPv4 and IPv6).
- `grus-dev-api.apps.onelite.feather` already resolves to the gateway IP `10.200.90.1` through the internal wildcard `*.apps.onelite.feather` (checked with `getent hosts`). No DNS change is needed.
- The Rook object store `feather-s3` serves S3 in namespace `rook-ceph-fr01` (Service `rook-ceph-rgw-feather-s3`, port 80). Claims without `bucketOwner` exist: `ingot-loadtest-s3` and `feather-core-cluster-pg-backup`.

## What Changes

- `foundation/sources/clusters/feather-core/sources/grus.yaml`: `ref.commit` to the Grus v2 merge commit (placeholder `<v2-merge-commit>`, filled in the code pull request; skipped if `add-grus-crds` already moved it there).
- `products/grus/clusters/feather-core/grus-dev/release.yaml`: image tags (controller, web) to the v2 tag (placeholder `<v2-tag>`); values `templates.backend: s3`, `templates.s3.existingSecret` and `existingConfigMap: grus-templates-dev`, endpoint from the ConfigMap (host and port, plain http inside the cluster); `crds.watch: true`; `GRUS_AGENT_TOKENS` from the Secret `grus-dev-secrets`.
- `products/grus/clusters/feather-core/grus-dev/templates-bucket.yaml` (new): ObjectBucketClaim `grus-templates-dev`, `storageClassName: ceph-bucket-fr01`, no `bucketOwner`.
- `products/grus/clusters/feather-core/grus-dev/grus-dev-secrets.sops.env`: add `GRUS_AGENT_TOKENS` (`mc01=<sha256 hex of the agent token>`). Only the hash is stored.
- `products/grus/clusters/feather-core/grus-dev/httproute.yaml`: add HTTPRoute `grus-dev-api` (hostname `grus-dev-api.apps.onelite.feather`, parent `eg` / `envoy` / `https`, backend Service `grus` port 8080) with an allow list of paths only (see below).
- `foundation/certificates/clusters/feather-core/internal-certs/internal-certificates.yaml`: Certificate `grus-dev-api-apps-onelite-feather` in namespace `envoy`, issuer `step-ca-issuer`, like `grus-dev`.
- `foundation/networking/clusters/feather-core/gateway/gateway.yaml`: add the new secret to the https listener `certificateRefs`. Nothing else changes.
- `products/grus/clusters/feather-core/grus-dev/networkpolicy.yaml`: three additions: ingress from the envoy proxy pods to the controller on TCP 8080, egress from the controller to the RGW pods (TCP 80), egress from the proxy pods to `10.200.2.35/32` on TCP 30000-30100.
- `products/grus/clusters/feather-core/grus-dev/kustomization.yaml`: list the new files.

Routed paths on `grus-dev-api` (everything else returns 404 at the gateway):

| Match | Purpose | Timeouts |
|---|---|---|
| PathPrefix `/v1/agents` | Agent register, long-poll `desired`, `observed` | request and backend 60s (the long-poll lasts 25s) |
| PathPrefix `/v1/self` | Instances report readiness and player counts with their per-instance token | default |
| Method GET + PathPrefix `/v1/templates` | Template archive download only | default |
| Exact `/healthz` | Health check | default |

**Commit type and scope:** `feat(products)`. The code pull request is titled `feat(products): run grus v2 in grus-dev with an agent on mc01`. Because the change also edits two shared files (certificates, gateway) they stay in that one pull request, like `expose-grus-web-ui-internally`.

## Context outside this repository (not tasks here)

On `mc01` the Grus agent runs as user `cloud` in `~/grus`, next to CloudNet. CloudNet is not changed. The operator places the plain agent token in a file on `mc01` and installs the agent unit there. The plain token never enters Git.

## Out of scope and non-goals

- Anycast for the Velocity proxies: design only, in the grus repository.
- Any change on `mc01` or to CloudNet by this repository.
- Pools, environments and groups of the demo in Git (see `design.md`, decision 6). They are created through the API or the CLI at runtime.
- Production, public hostnames, Cloudflare, LoadBalancer Services.
- A foundation change for the S3 user (no `bucketOwner`, no `CephObjectStoreUser`).
- Enforcement of NetworkPolicies cluster-wide.

## Impact

- Capability `grus-v2-dev` (see `specs/grus-v2-dev/spec.md`).
- About ten files change in the code pull request, two of them shared (certificates, gateway).
- The Role of the controller grows by get/list/watch on three kinds and get/update/patch on `pools/status`, in `grus-dev` only. The chart creates it; there is no ClusterRole.
- A new internal hostname exposes the agent API (not the admin API) to the internal network, protected by machine tokens. This is a new attack surface on the internal network; see `design.md`.
- Ceph gets one more bucket and one more RGW user (created by Rook).

## Rollback

1. Revert the code pull request. Flux removes the route, the policies, the certificate and the release values. The bucket claim has `reclaimPolicy` of the StorageClass; check it before reverting if the templates are still needed (task 1.6).
2. The agent on `mc01` keeps running its instances and retries until the controller is back; stop it there if it is no longer wanted.
3. The CRDs stay (see `add-grus-crds`).

## Verification after merge

Recorded in the code pull request: the release is Ready at the v2 revision, the bucket claim is Bound and the Secret and ConfigMap exist, the controller lists the CRD kinds without a permission error, `https://grus-dev-api.apps.onelite.feather/healthz` answers 200 from the internal network, `/v1/pools` on that hostname answers 404, the agent on `mc01` registers and a pool with `placement.driver: agent` starts an instance there, and a proxy pod reaches that instance's port.
