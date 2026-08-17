# Ceph RGW buckets

Zwei Wege koexistieren im Repo. Neue Apps nehmen den ersten.

## Der Weg für neue Apps: `ObjectBucketClaim` im App-Namespace

Rooks Bucket-Provisioner legt aus einer OBC den Bucket an und schreibt die
Zugangsdaten in **den Namespace der Claim** — als ConfigMap und Secret, beide
benannt wie die Claim:

| Objekt | Schlüssel |
|---|---|
| ConfigMap | `BUCKET_HOST`, `BUCKET_PORT`, `BUCKET_NAME`, `BUCKET_REGION`, `BUCKET_SUBREGION` |
| Secret | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |

Die Claim gehört deshalb **nicht** nach `rook-ceph-fr01`, sondern neben die App
(`apps/.../base-apps/<app>/bucket.yaml`). Vorbilder: `apus-bundles` in
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
    bucketOwner: <app>          # existierender CephObjectStoreUser
```

`bucketOwner` ist wichtig und nicht optional-im-Sinne-von-egal. Ohne das Feld
erzeugt der Provisioner für jede Claim einen anonymen RGW-User
`obc-<ns>-<name>-<uuid>`; mit dem Feld gehört der Bucket dem benannten
`CephObjectStoreUser` unter `infrastructure/.../rook-fr01/users/`, und **das
generierte Secret trägt dann dessen Zugangsdaten** statt eines zweiten
Schlüsselpaars. Damit bleibt der gesamte Footprint einer App unter einem
nachvollziehbaren User (Quota, `radosgw-admin bucket stats`), und es ist
zugleich die Konstellation, die nach dem Rook-1.20-Upgrade noch funktioniert —
dort brach der Zugriff für benannte User, die *nicht* Eigentümer des Buckets
waren.

`BUCKET_REGION` kommt leer zurück. Clients, die eine Region zum Signieren
brauchen (AWS SDK), bekommen sie aus ihrer eigenen Config — `us-east-1`.

### Warum das früher nicht ging

Bis 2026-07-13 erreichte keine einzige OBC `Bound`. Der Provisioner-Name, den
ein laufender rook-ceph-Operator beobachtet, leitet sich vom Namespace des
`CephCluster` ab (`rook-ceph-fr01.ceph.rook.io/bucket`), die StorageClass in
`infrastructure/.../rook-fr01/storageclasses/bucket.yaml` nannte aber das feste
`rook-ceph.ceph.rook.io/bucket`. Das Feld `provisioner` ist immutable, die
Korrektur brauchte also ein Löschen der StorageClass.

Seitdem binden die Claims. Die zweite Hürde von damals — Secret und ConfigMap
landen im Namespace der Claim und nicht bei der App — ist keine, sobald die
Claim von vornherein im App-Namespace liegt.

## Der Altbestand: Claims in `rook-ceph-fr01`

Die Dateien unter `infrastructure/clusters/feather-core/rook-fr01/buckets/`
sind Claims aus der Zeit davor. Sie binden inzwischen ebenfalls, aber ihre
ConfigMap und ihr Secret liegen in `rook-ceph-fr01` — und dieses Repo hat
keinen namespace-übergreifenden Secret-Sync (kein Reflector, kein
kubernetes-replicator). Die zugehörigen Apps lesen ihre Zugangsdaten deshalb
weiter aus einem eigenen, handbefüllten SOPS-Secret
(`apps/.../<app>/*.sops.env`), das aus dem operator-erzeugten Secret
`rook-ceph-object-user-feather-s3-<app>` kopiert wurde.

Die meisten Storage-Clients (Loki, Mimir, Harbor) legen ihren Bucket beim
ersten Schreiben selbst an — daher das „hat einfach funktioniert" bei denen.
**Tempos S3-Backend tut das nicht**: es ruft nur `ListObjects` und scheitert,
wenn der Bucket fehlt. Gleiches gilt für Vikunja, dessen S3-Backend kein
`CreateBucket` kennt.

Wer so einen Bucket von Hand braucht:

```bash
# als eigener User des Apps anlegen (RGWs Zonegroup lehnt jeden expliziten
# LocationConstraint ab außer dem, für den die AWS CLI ihn weglässt — also
# --region=us-east-1, nicht den api_name "default" der Zonegroup)
kubectl run bucket-init --rm -i --restart=Never -n <app-ns> \
  --image=amazon/aws-cli:2.17.60 \
  --overrides='{"spec":{"containers":[{"name":"bucket-init","image":"amazon/aws-cli:2.17.60","command":["aws","--endpoint-url=http://rook-ceph-rgw-feather-s3.rook-ceph-fr01.svc:80","--region=us-east-1","s3","mb","s3://<bucket>"],"envFrom":[{"secretRef":{"name":"<app>-s3"}}]}]}}'

# existiert der Bucket schon, gehört aber dem falschen User (z. B. einem
# obc-<ns>-<name>-<uuid> aus einer alten Claim), Eigentum umhängen statt
# neu anlegen:
kubectl exec -n rook-ceph-fr01 deploy/rook-ceph-tools -- \
  radosgw-admin bucket link --bucket=<bucket> --uid=<app> --rgw-realm=feather-s3
```
