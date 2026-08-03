# Ceph RGW buckets

## The `ObjectBucketClaim`s under `infrastructure/.../rook-fr01/buckets/` don't actually provision anything

Every file in `infrastructure/clusters/feather-core/rook-fr01/buckets/` is an
`ObjectBucketClaim` (OBC). In theory Rook's bucket provisioner should turn
each of these into a bucket + a freshly generated RGW user + a
Secret/ConfigMap holding that user's credentials.

In practice, **none of them have ever reached `Bound`**. Two structural
reasons:

- The provisioner name a running rook-ceph operator watches is derived from
  the `CephCluster`'s namespace (here: `rook-ceph-fr01.ceph.rook.io/bucket`),
  not the fixed `rook-ceph.ceph.rook.io/bucket` used in
  `infrastructure/clusters/feather-core/rook-fr01/storageclasses/bucket.yaml`
  before 2026-07-13. A StorageClass's `provisioner` field is immutable, so
  fixing this required deleting and letting Flux recreate the StorageClass.
- Even with the provisioner name fixed, the OBC's generated Secret/ConfigMap
  land in the OBC's own namespace (`rook-ceph-fr01`) — not in the consuming
  app's namespace — and this repo has no cross-namespace secret sync
  (no Reflector/kubernetes-replicator). So no app could consume that Secret
  even if provisioning succeeded. On top of that, the OBC flow always mints a
  brand-new RGW user as bucket owner; it has no field to assign an existing
  `CephObjectStoreUser` as owner.

## How buckets actually get created

Every real bucket in this cluster (`harbor`, `outline`, `loki-chunks`,
`mimir-blocks`, ...) is owned by the app's own `CephObjectStoreUser` of the
same name (see `radosgw-admin bucket stats --bucket=<name> --rgw-realm=feather-s3`
→ `"owner"`), whose credentials are hand-copied into that app's SOPS secret
(e.g. `apps/.../<app>/*.sops.env`). Most storage clients (Loki, Mimir, Harbor)
auto-create their bucket on first write using those credentials — that's the
"it just worked" experience for those apps. **Tempo's S3 backend does not
auto-create its bucket** — it only ever calls `ListObjects`, and fails
outright if the bucket doesn't exist yet.

For an app like Tempo, the bucket has to be created once, out-of-band, owned
by that app's own `CephObjectStoreUser`:

```bash
# if the bucket doesn't exist yet, create it as the app's own user
# (RGW's zonegroup rejects any explicit LocationConstraint except the one
# AWS CLI omits it for — use --region=us-east-1, not the zonegroup's own
# api_name "default" or a real AWS region)
kubectl run bucket-init --rm -i --restart=Never -n <app-ns> \
  --image=amazon/aws-cli:2.17.60 \
  --overrides='{"spec":{"containers":[{"name":"bucket-init","image":"amazon/aws-cli:2.17.60","command":["aws","--endpoint-url=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80","--region=us-east-1","s3","mb","s3://<bucket>"],"envFrom":[{"secretRef":{"name":"<app>-s3"}}]}]}}'

# if the bucket already exists but is owned by the wrong user (e.g. created
# via a since-fixed OBC, owned by an auto-generated obc-<ns>-<name>-<uuid>
# user), re-link ownership instead of recreating it:
kubectl exec -n rook-ceph-fr01 deploy/rook-ceph-tools -- \
  radosgw-admin bucket link --bucket=<bucket> --uid=<app> --rgw-realm=feather-s3
```

The `ObjectBucketClaim` files stay in place as bucket-name reservations /
documentation of intent, but don't rely on them actually provisioning
anything.
