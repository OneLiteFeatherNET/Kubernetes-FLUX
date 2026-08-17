# Ceph RGW buckets

Two approaches coexist in this repo. New apps take the first one.

## For new apps: an `ObjectBucketClaim` in the app's namespace

Rook's bucket provisioner turns a claim into a bucket and writes the access
details into **the namespace of the claim** — a ConfigMap and a Secret, both
named after the claim:

| Object | Keys |
|---|---|
| ConfigMap | `BUCKET_HOST`, `BUCKET_PORT`, `BUCKET_NAME`, `BUCKET_REGION`, `BUCKET_SUBREGION` |
| Secret | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |

So the claim does **not** belong in `rook-ceph-fr01`; it belongs next to the app
(`apps/.../base-apps/<app>/bucket.yaml`). Existing examples: `apus-bundles` in
`apus-system`, `vikunja-bucket` in `vikunja`.

```yaml
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: <app>-bucket
  namespace: <app>
spec:
  bucketName: <app>
  storageClassName: ceph-bucket-fr01
  additionalConfig:
    bucketOwner: <app>          # an existing CephObjectStoreUser
```

`bucketOwner` matters and is not optional-in-the-sense-of-irrelevant. Without
it the provisioner mints an anonymous RGW user `obc-<ns>-<name>-<uuid>` per
claim; with it the bucket belongs to the named `CephObjectStoreUser` under
`infrastructure/.../rook-fr01/users/`, and **the generated Secret then carries
that user's credentials** instead of a second key pair. That keeps an app's
whole footprint under one traceable user (quota, `radosgw-admin bucket stats`),
and it is also the arrangement that still works after the Rook 1.20 upgrade,
which broke access for named users that were *not* the bucket's owner.

`BUCKET_REGION` comes back empty. Clients that need a region to sign with (the
AWS SDK) take it from their own config — `us-east-1`.

### Why this did not work before

Until 2026-07-13 not a single OBC reached `Bound`. The provisioner name a
running rook-ceph operator watches is derived from the `CephCluster`'s
namespace (`rook-ceph-fr01.ceph.rook.io/bucket`), but the StorageClass in
`infrastructure/.../rook-fr01/storageclasses/bucket.yaml` named the fixed
`rook-ceph.ceph.rook.io/bucket`. A StorageClass's `provisioner` field is
immutable, so the fix required deleting and recreating it.

Claims have bound ever since. The second obstacle from back then — Secret and
ConfigMap landing in the claim's namespace rather than with the app — stops
being one as soon as the claim sits in the app's namespace to begin with.

## The legacy path: claims in `rook-ceph-fr01`

The files under `infrastructure/clusters/feather-core/rook-fr01/buckets/` are
claims from before that fix. They bind now too, but their ConfigMap and Secret
live in `rook-ceph-fr01` — and this repo has no cross-namespace secret sync (no
Reflector, no kubernetes-replicator). The corresponding apps therefore still
read their credentials from a hand-filled SOPS secret
(`apps/.../<app>/*.sops.env`) copied out of the operator-minted Secret
`rook-ceph-object-user-feather-s3-<app>`.

Most storage clients (Loki, Mimir, Harbor) create their bucket on first write —
that is the "it just worked" experience for those. **Tempo's S3 backend does
not**: it only ever calls `ListObjects` and fails outright if the bucket does
not exist. The same goes for Vikunja, whose S3 backend has no `CreateBucket`.

To create such a bucket by hand:

```bash
# create it as the app's own user (RGW's zonegroup rejects any explicit
# LocationConstraint except the one the AWS CLI omits it for — use
# --region=us-east-1, not the zonegroup's own api_name "default")
kubectl run bucket-init --rm -i --restart=Never -n <app-ns> \
  --image=amazon/aws-cli:2.17.60 \
  --overrides='{"spec":{"containers":[{"name":"bucket-init","image":"amazon/aws-cli:2.17.60","command":["aws","--endpoint-url=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80","--region=us-east-1","s3","mb","s3://<bucket>"],"envFrom":[{"secretRef":{"name":"<app>-s3"}}]}]}}'

# if the bucket already exists but is owned by the wrong user (e.g. an
# obc-<ns>-<name>-<uuid> from an old claim), re-link ownership instead of
# recreating it:
kubectl exec -n rook-ceph-fr01 deploy/rook-ceph-tools -- \
  radosgw-admin bucket link --bucket=<bucket> --uid=<app> --rgw-realm=feather-s3
```
