## 1. Preflight

- [ ] 1.1 Confirm the `add-grus-crds` code pull request is merged and the three CRDs are Established
- [ ] 1.2 Confirm the Grus v2 images exist in Harbor (controller and web) and record the tag and the merge commit; replace the placeholders `<v2-tag>` and `<v2-merge-commit>`
- [ ] 1.3 Render the chart at that commit with the new values (`helm template`) and confirm the Role, the `GRUS_AGENT_TOKENS` env and the S3 settings appear; record what the controller does when the CRDs are missing
- [ ] 1.4 Confirm `grus-dev-api.apps.onelite.feather` resolves to `10.200.90.1` (`getent hosts`) and the envoy proxy pods carry `app.kubernetes.io/name=envoy` and `app.kubernetes.io/component=proxy`
- [ ] 1.5 Confirm the RGW pods carry `app=rook-ceph-rgw` in `rook-ceph-fr01` and listen on port 80
- [ ] 1.6 Record the reclaim policy of StorageClass `ceph-bucket-fr01`
- [ ] 1.7 Confirm the proxy pods carry `grus.olf/role=proxy` and `mc01` is `10.200.2.35` on the path the proxy uses; repeat the TCP test from a proxy pod to a port in 30000-30100 once the agent runs
- [ ] 1.8 Agree with the operator that the plain agent token is generated outside Git and the SHA-256 is handed over for the secret

## 2. Source, release and secret

- [ ] 2.1 Set `ref.commit` in `foundation/sources/clusters/feather-core/sources/grus.yaml` to the v2 merge commit (skip if already set by `add-grus-crds`)
- [ ] 2.2 In `products/grus/clusters/feather-core/grus-dev/release.yaml` set the controller and web image tags to the v2 tag and update the comments
- [ ] 2.3 Add `templates.backend: s3`, `templates.s3.existingSecret` and `existingConfigMap: grus-templates-dev` and the endpoint from the ConfigMap, `crds.watch: true`, and the `GRUS_AGENT_TOKENS` reference to `grus-dev-secrets`
- [ ] 2.4 Add `GRUS_AGENT_TOKENS=mc01=<sha256>` to `grus-dev-secrets.sops.env` with `sops`; commit no plain value

## 3. Bucket, certificate and gateway

- [ ] 3.1 Create `products/grus/clusters/feather-core/grus-dev/templates-bucket.yaml` with ObjectBucketClaim `grus-templates-dev` (`ceph-bucket-fr01`, no `bucketOwner`)
- [ ] 3.2 Add Certificate `grus-dev-api-apps-onelite-feather` to `foundation/certificates/clusters/feather-core/internal-certs/internal-certificates.yaml`
- [ ] 3.3 Add `grus-dev-api-apps-onelite-feather-tls` to the https listener `certificateRefs` in `foundation/networking/clusters/feather-core/gateway/gateway.yaml`; change nothing else

## 4. Route and policies

- [ ] 4.1 Add HTTPRoute `grus-dev-api` to `products/grus/clusters/feather-core/grus-dev/httproute.yaml` with the four allowed matches and 60s timeouts on `/v1/agents`
- [ ] 4.2 Add the three NetworkPolicies (gateway to controller 8080, controller to RGW 80, proxy to `10.200.2.35/32` 30000-30100) to `networkpolicy.yaml`
- [ ] 4.3 List new files in `products/grus/clusters/feather-core/grus-dev/kustomization.yaml`

## 5. Validation

- [ ] 5.1 Run `./.github/scripts/validate.sh`
- [ ] 5.2 Render `kubectl kustomize products/grus/clusters/feather-core/grus-dev` and confirm no `Pool`, `Group` or `Environment`, no LoadBalancer Service and no plaintext token
- [ ] 5.3 Run `openspec validate enable-grus-v2-dev --strict`

## 6. Rollout verification (after the code pull request is merged)

- [ ] 6.1 `flux get helmreleases -n grus-dev` is Ready at the v2 revision; `kubectl -n grus-dev get obc,secret,cm grus-templates-dev` shows Bound
- [ ] 6.2 `curl https://grus-dev-api.apps.onelite.feather/healthz` answers 200 and `/v1/pools` answers 404 from the internal network
- [ ] 6.3 The operator starts the agent on `mc01`; `PUT /v1/agents/mc01` succeeds and the host shows in the UI
- [ ] 6.4 A `desired` long-poll stays open past 15 s through the gateway
- [ ] 6.5 Create a demo pool with `placement.driver: agent` through the API; an instance starts on `mc01` and the proxy reaches it
- [ ] 6.6 Template upload and archive download work through S3; a `PUT` to `/v1/templates` on the API hostname answers 404
- [ ] 6.7 Apply one throw-away Pool CR, confirm the controller picks it up (source `kubernetes`), delete it

## 7. Code pull request

- [ ] 7.1 Open the code pull request under the title `feat(products): run grus v2 in grus-dev with an agent on mc01`; no files under `openspec/` in it
