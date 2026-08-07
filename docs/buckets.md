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

## 2026-07-14 incident: fixing the provisioner made it worse

`619fde4` (2026-07-13, "fix(rook-fr01): correct bucket StorageClass
provisioner name") fixed the immutable `provisioner` field described above.
The very next day, every OBC that had been sitting `Pending` since its
creation (some for 37+ days) finally bound — and each one **took over
ownership of the pre-existing, already-populated bucket of the same name**,
reassigning it from the app's own `CephObjectStoreUser` to a freshly minted
`obc-<ns>-<name>-<uuid>` user. 13 of 15 real buckets were silently hijacked
this way (`tempo-traces` and `olf` were the only ones spared, because their
OBCs never bound). Every app using its static user's hand-copied credentials
(the only working pattern — see above) started getting `403
AccessDeniedException` on every S3 write, discovered via Tempo traces from
BlueMap running on an external Minecraft server, of all things.

**Detection** — audit every real bucket's owner and flag anything owned by
an `obc-*` ghost user instead of its app's own `CephObjectStoreUser`:

```bash
for b in <bucket1> <bucket2> ...; do
  echo "$b -> $(kubectl exec -n rook-ceph-fr01 deploy/rook-ceph-tools -- \
    radosgw-admin bucket stats --bucket="$b" --rgw-realm=feather-s3 2>/dev/null \
    | grep '"owner"')"
done
```

**Fix** is the same `bucket link` command as above, applied per bucket.

**Why this doesn't self-heal and won't recur on its own**: once an OBC
reaches `Bound`, Rook's bucket controller doesn't re-run `Create()` on
subsequent reconciles — it only re-hijacks ownership if the OBC is deleted
and recreated (or newly created against an existing bucket name for the
first time). The already-Bound OBCs for these buckets are stable now that
ownership has been corrected. **Never delete-and-recreate one of these OBCs
while its bucket already has data** — that WILL mint a new ghost owner
again. If an OBC genuinely needs to be recreated, re-run the ownership audit
above afterwards.

**Trap for whoever fixes this next time**: at least one app (CNPG's
`cnpg-backup` / `feather-core-cluster-pg-backup`) got "fixed" the *other*
direction on 2026-07-14 — instead of re-linking the bucket back to its
static `CephObjectStoreUser`, the app's SOPS secret was repointed at the
new `obc-*` ghost user's own key pair, since at the time that ghost user
really was the verified-working owner. That fix and the `bucket link`
fix above are **mutually exclusive** — re-linking the bucket back to the
static user (as this doc recommends) without also reverting the SOPS
secret back to that static user's key pair leaves the app broken again,
just in the opposite direction. Before running `bucket link` on any
bucket, check whether the consuming app's secret was quietly repointed at
the current (ghost) owner's key first — `grep` the app's HelmRelease/
Cluster/ObjectStore manifest for which Secret it reads
(`accessKeyId.name`/`secretAccessKey.name`), decrypt that secret with
`sops -d`, and compare its `access-key-id` against
`radosgw-admin user info --uid=<ghost-uid>`. If they match, relinking the
bucket must be paired with `sops --set` on that secret to swap back to
the static user's key pair (`radosgw-admin user info --uid=<app>`), in the
same change.
