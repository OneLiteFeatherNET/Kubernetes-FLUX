# Changelog

## [0.2.0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/compare/v0.1.1...v0.2.0) (2026-11-21)


### Features

* **backup:** add scheduled etcd snapshots to in-cluster and external storage ([#623](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/623)) ([d0c910f](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/d0c910fbb4106442e6d00bbec2e63eacecba0c7e))
* **backup:** add velero backups with rbd csi snapshots ([#625](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/625)) ([dc75d41](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/dc75d4192b4e23cb9046a3c1fe6ed3c6f129f0de))
* **backup:** enable daily etcd snapshots ([#634](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/634)) ([e23094e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/e23094ebc88b0429ce5733bd688c821da17e4b91))
* **backup:** run velero backup schedules ([#645](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/645)) ([53d1392](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/53d13928a1b13e2d497fe28335437ef6fe39fa03))
* **corvus:** add s3 keys for the iceberg lake ([#671](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/671)) ([e1f6810](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/e1f68103ccd55659b5e57a947d3ce1a10a203d3e))
* **corvus:** grant corvus access to the iceberg lake ([#670](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/670)) ([7556a44](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/7556a44bbbca5c0e2beceb28680ae8493bace209))
* **corvus:** write capture data to the iceberg lake ([#680](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/680)) ([069ec65](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/069ec65a527dd5bf0be05cdd668aba57bc06ec0b))
* **data:** add a superset dashboard for packet type analysis ([#684](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/684)) ([3cab09d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/3cab09df28f2026e0f71e191462e264a345b5c32))
* **data:** add apache iceberg data lake ([#661](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/661)) ([5e0923c](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5e0923cf850884e7331ac871721652e18defcc94))
* **data:** add superset dashboards for sessions, movement and actions ([#686](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/686)) ([374cb44](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/374cb448c4d8182cf9f28292d4336b92a873084a))
* **data:** add trino and superset for lake sql ([#672](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/672)) ([4134927](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/413492731e46f81f2463dd09a1d7be3ef1836a64))
* **data:** add trino s3 keys for the iceberg lake ([#673](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/673)) ([f318236](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/f318236eae0edf6409801265e78455134b558a91))
* **databases:** add the ingot staging database on postgresql ([#631](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/631)) ([70718c6](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/70718c6bf1f5155804915aeef2232f339d93bdf6))
* **data:** expose airflow on an internal https url ([#668](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/668)) ([e369a3a](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/e369a3ab8ddeeeeacc0832b2beb803410d3ed054))
* **data:** update airflow to 3.4.0 ([#678](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/678)) ([da4b4b0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/da4b4b0780b7d172f4b19b13e0f1b4d2fe340369))
* **dependency-track:** cut over to dependency-track 5 ([#657](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/657)) ([1fc5142](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1fc5142b93c9dff92a8e9ab5f34abaf58d4ae9b4))
* **dependency-track:** expose the api on the internal gateway ([#658](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/658)) ([af713b3](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/af713b3bad37fb455370c561005da8a0b58a6c44))
* **dependency-track:** migrate to dependency-track 5 ([#644](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/644)) ([164f163](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/164f163c26296c1bcdc8e4c84d3ac96cec95d3f0))
* **dependency-track:** prepare the dependency-track 5 cutover migration ([#656](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/656)) ([52c438d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/52c438d3a63de0b0aeca28d33f5529ad523b92b7))
* **dependency-track:** sync cluster and harbor sboms into dependency-track ([#643](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/643)) ([728f9a4](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/728f9a40d70b10da057bce2fcb91e07faee560ed))
* **development:** prepare isuku runners for kubernetes container mode ([#612](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/612)) ([1d00428](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1d00428b25a7011296742de33d504f7b93eef346))
* **grafana:** add auto-scaled units to network throughput dashboard ([#616](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/616)) ([a6fb022](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a6fb022990efa61d9cbf3446ccd182bd56c3ace3))
* **grafana:** add minecraft player sessions dashboard ([#629](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/629)) ([10e3f14](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/10e3f14840b5d160c461c1019e7c3b8bc14c20be))
* **grafana:** add network throughput units dashboard ([#615](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/615)) ([58d97dd](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/58d97dd57f3bb239c8c66d79d50cd1804709388b))
* **observability:** cover all nodes with logs, add talos log intake and cert and etcd alerts ([#624](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/624)) ([7c5a940](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/7c5a9408ce49acfd0ae57a8d90b30de49420044f))
* **platform:** add disruption budgets for cilium operator and coredns ([#621](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/621)) ([bbead30](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/bbead307ea327dd1416b1715f095772d9d6fdc27))
* **security:** add kyverno in audit mode with starter policies ([#626](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/626)) ([3d50770](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/3d50770b624f224e9c32a139e7e6bcfda2bbaa1d))
* **security:** add weekly cis kube-bench scan with talos skip list ([#622](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/622)) ([8072a9e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/8072a9e4411bd5e222dc0eabaaada891f9fa0737))
* **security:** run the weekly kube-bench scan on schedule ([#639](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/639)) ([9117412](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/9117412ba7b9e60da6df561ee5bd309d51407a1e))
* **storage:** add velero snapshot class for cephfs volumes ([#638](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/638)) ([a5882a1](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a5882a129c9b6e53a1e88f86ca7659f28d277a32))


### Bug Fixes

* **backup:** target a control-plane node for etcd snapshots ([#630](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/630)) ([b022f42](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/b022f42da2ec7f625526f2536e6c421cdcb3aac4))
* **databases:** raise the luckperms connection limit to 350 ([#682](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/682)) ([4ab32cd](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/4ab32cdd814ed4b7e1be82ca1817cae7a29bb0e4))
* **databases:** raise the superset connection limit to 25 ([#683](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/683)) ([72b08e4](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/72b08e4824734b3783e32466622ea6f7fdedf30a))
* **data:** complete polaris catalog setup and entra role mapping ([#664](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/664)) ([1fa7a03](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1fa7a031fb613800ff6111c01229e751a80394d9))
* **data:** give polaris s3 access and narrow maintenance grants ([#665](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/665)) ([dd52e3e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/dd52e3e35435ac93d74fa54eea55b4d4fcc8720b))
* **data:** give trino memory headroom outside the java heap ([#685](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/685)) ([77ec9d8](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/77ec9d8e307b35a2caea473687cd2d1265c293ba))
* **data:** install authlib for the superset entra login ([#677](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/677)) ([564bf5a](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/564bf5a110ffb57c34f3f0d126d0fcbdc0adfc48))
* **data:** install superset drivers with uv ([#675](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/675)) ([a8e3ad3](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a8e3ad327167c0088080fb177657ff3de397ca40))
* **data:** keep polaris token keys decryptable by flux ([#662](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/662)) ([5edc30e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5edc30e3bfeb03901b1366aa1e7f129859d843f6))
* **data:** let airflow workers manage maintenance script configmaps ([#667](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/667)) ([a91f65f](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a91f65f1a07f8551b61324ec525b398253b62899))
* **data:** let lake sql list views ([#676](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/676)) ([82443db](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/82443dbd4a0b1d83cb467db0e45351684087ab66))
* **data:** let the corvus ingest principal read the capture namespace ([#681](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/681)) ([7cb1ee9](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/7cb1ee9a7bd7ed38a1a499ba4dcd150ab4e1800c))
* **data:** prefer rather than require separate nodes for trino workers ([#689](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/689)) ([cdb672e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/cdb672ed3ba2f3146ed02241ff208fb5b2827a12))
* **data:** request less memory for the trino workers ([#687](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/687)) ([80d2b6a](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/80d2b6ab8f162f6d7c4ff5b31987684c1eeb7cea))
* **data:** roll trino workers one at a time without a surge pod ([#688](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/688)) ([1ed0577](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1ed057708f8bf0e8d29fb47ab3033b885df61c18))
* **data:** rotate polaris root credentials and drop bootstrap job ([#666](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/666)) ([0b2b054](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/0b2b0542065b0449ba57e4ba0cfc6bba0bcff547))
* **data:** run superset pods with a numeric user id ([#674](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/674)) ([634e15d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/634e15de7cb52a5986821dff13dd8ad55b251927))
* **data:** unblock polaris bootstrap and airflow migrations ([#663](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/663)) ([75f78d4](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/75f78d4313fbbbafc45e73f3c70ca43c4f5acdfe))
* **observability:** allow experimental tcplog receiver in talos log intake ([#637](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/637)) ([daa38bb](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/daa38bb517ad85eaf9406ac1a93ed2ca046aff96))
* **observability:** parse talos log envelope and drop auditd flood ([#640](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/640)) ([ce6a2b0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/ce6a2b0988c2f4693825390c34d765847220522d))
* **observability:** shorten talos log receiver port name ([#633](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/633)) ([1ed78da](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1ed78da3f18a4f93aa38f90029a051be67d3e8fd))
* **postgresql:** detect dead client connections within two minutes ([#528](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/528)) ([a82aff3](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a82aff3203feb5f8cb26cf6e79736a1593154d2f))
* **security:** migrate kyverno starter policies to cel validating policies ([#632](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/632)) ([641d9ef](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/641d9efe63546a39f38be29ce29412753e78b154))
* **storage:** allow team members to read cloudnet-templates versioning status ([#492](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/492)) ([31c4dd8](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/31c4dd88bdf927abcbcc7fd4680eb0a035f8498a))
* **storage:** enable volume snapshots for the rbd csi driver ([#636](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/636)) ([2928295](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/292829530c2d014c5e303272f596b35f6fdb4e69))


### Performance Improvements

* **flux:** reconcile helmreleases every 10 minutes ([#654](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/654)) ([637a09d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/637a09d22bda9100c6f544e74b19747c361668de))
* **grafana:** use $__rate_interval for short rate windows in dashboards ([#617](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/617)) ([5368327](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/536832779055bae6fabad2efdfbd886422182685))
* **security:** reconcile security helm releases every 10 minutes ([#655](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/655)) ([9b32907](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/9b329071e8634a154b213dbf4b33bc897fbd0045))

## [0.1.1](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/compare/v0.1.0...v0.1.1) (2026-10-11)


### Bug Fixes

* **ci:** pass the org release app secrets to release-please ([#405](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/405)) ([15351c2](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/15351c22206439959ce2d2957126717db6536c39))
