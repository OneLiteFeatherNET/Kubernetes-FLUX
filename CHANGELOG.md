# Changelog

## [0.2.0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/compare/v0.1.1...v0.2.0) (2026-11-28)


### Features

* **corvus:** add s3 keys for the iceberg lake ([#671](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/671)) ([e1f6810](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/e1f68103ccd55659b5e57a947d3ce1a10a203d3e))
* **corvus:** grant corvus access to the iceberg lake ([#670](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/670)) ([7556a44](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/7556a44bbbca5c0e2beceb28680ae8493bace209))
* **corvus:** write capture data to the iceberg lake ([#680](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/680)) ([069ec65](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/069ec65a527dd5bf0be05cdd668aba57bc06ec0b))
* **data:** add a superset dashboard for packet type analysis ([#684](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/684)) ([3cab09d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/3cab09df28f2026e0f71e191462e264a345b5c32))
* **data:** add apache iceberg data lake ([#661](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/661)) ([5e0923c](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5e0923cf850884e7331ac871721652e18defcc94))
* **data:** add lake layer platform ([#708](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/708)) ([9a9ead7](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/9a9ead794c5443fb8defa3f940ec47843ff44df5))
* **data:** add superset dashboards for sessions, movement and actions ([#686](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/686)) ([374cb44](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/374cb448c4d8182cf9f28292d4336b92a873084a))
* **data:** add trino and superset for lake sql ([#672](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/672)) ([4134927](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/413492731e46f81f2463dd09a1d7be3ef1836a64))
* **data:** add trino s3 keys for the iceberg lake ([#673](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/673)) ([f318236](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/f318236eae0edf6409801265e78455134b558a91))
* **data:** expose airflow on an internal https url ([#668](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/668)) ([e369a3a](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/e369a3ab8ddeeeeacc0832b2beb803410d3ed054))
* **data:** query the lake from superset with duckdb instead of trino ([#704](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/704)) ([d11ac95](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/d11ac95e2f9ad9fe3370d387825a01b968aedbdb))
* **data:** remove trino now that superset queries the lake with duckdb ([#712](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/712)) ([39fe930](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/39fe930544f66a3f4924e54e86f2ecafccf59967))
* **data:** update airflow to 3.4.0 ([#678](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/678)) ([da4b4b0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/da4b4b0780b7d172f4b19b13e0f1b4d2fe340369))
* **dependency-track:** cut over to dependency-track 5 ([#657](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/657)) ([1fc5142](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1fc5142b93c9dff92a8e9ab5f34abaf58d4ae9b4))
* **dependency-track:** expose the api on the internal gateway ([#658](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/658)) ([af713b3](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/af713b3bad37fb455370c561005da8a0b58a6c44))
* **dependency-track:** prepare the dependency-track 5 cutover migration ([#656](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/656)) ([52c438d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/52c438d3a63de0b0aeca28d33f5529ad523b92b7))
* **dependency-track:** sync cluster and harbor sboms into dependency-track ([#643](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/643)) ([728f9a4](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/728f9a40d70b10da057bce2fcb91e07faee560ed))
* **ingot-loadtest:** add the k6-operator and the load test namespace ([#695](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/695)) ([31b5ad1](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/31b5ad1ea7d46c8a144e5b8e108aacf2335fe46a))
* **networking:** announce the gateway vip from helsinki ([#745](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/745)) ([ae42f87](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/ae42f872faea4548829633546d317dca299e10e8))
* **networking:** keep in-cluster gateway traffic in its zone ([#744](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/744)) ([9cdcc05](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/9cdcc05a48ad6f0fd19ad456d71ea7c05fb6ec8a))
* **networking:** lower the cilium mtu to 1400 for the helsinki site ([#741](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/741)) ([de3ce8b](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/de3ce8bcc3c73df90261096bf2ced357addfaec6))
* **networking:** tag helsinki routes with a site community ([#749](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/749)) ([3e067b6](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/3e067b6324aa4395490efeb96e4156f2ee19a769))
* **observability:** restore etcd and apiserver latency metrics ([#754](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/754)) ([de1f682](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/de1f682098e1c4ec83ff65c16298b276961daaf7))
* **platform:** run spegel and alloy-logs on the helsinki edge nodes ([#742](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/742)) ([32a4591](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/32a45919e5bbcf6e497eb60bbb7c8a473be408d1))
* **products:** add the grus custom resource definitions ([#728](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/728)) ([cb31bce](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/cb31bce15bd1de0d7d1b94886d986c07d8798872))
* **products:** add the grus prototype ([#721](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/721)) ([d14724e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/d14724e9d505a1501c79a3255f712e9681472866))
* **products:** enable the grus web ui in grus-dev ([#723](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/723)) ([ed06f8f](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/ed06f8f575fc18463532dd746d64c6257e65f78e))
* **products:** expose the grus dev proxies on an internal load balancer ([#733](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/733)) ([569609f](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/569609f230c762d35636fd9b95c23f3c2a0a6e5c))
* **products:** expose the grus web ui on the internal gateway ([#725](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/725)) ([16bf349](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/16bf349dbc0a7b173297bce321f03eefb5dfc5e0))
* **products:** move grus-dev to the build with private servers and cells ([#731](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/731)) ([c9e03f1](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/c9e03f1d65f5d4542de1d6eba9c2cf07c404a7c2))
* **products:** run grus v2 in grus-dev with an agent on mc01 ([#730](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/730)) ([e45a307](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/e45a307b4efe6a83ba66e86d159329f956078f28))


### Bug Fixes

* **cert-manager:** raise the cainjector memory limit to 256Mi ([#699](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/699)) ([9ce7813](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/9ce781336d0039ed8385b3ff178969059fb148ef))
* **databases:** keep vikunja on the direct postgres service ([#740](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/740)) ([9319063](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/931906358b48b653dbdfef949b3b5b3ae905d3f6))
* **databases:** raise the luckperms connection limit to 350 ([#682](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/682)) ([4ab32cd](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/4ab32cdd814ed4b7e1be82ca1817cae7a29bb0e4))
* **databases:** raise the superset connection limit to 25 ([#683](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/683)) ([72b08e4](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/72b08e4824734b3783e32466622ea6f7fdedf30a))
* **databases:** route penpot and vikunja through the transaction pooler ([#739](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/739)) ([6808a88](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/6808a884201d02b53cecedcb9c134055e99d925d))
* **databases:** size mongodb and clickhouse memory requests to their real usage ([#709](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/709)) ([719c2f5](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/719c2f52701ff420d43a27171c53d7660569f1a0))
* **data:** complete polaris catalog setup and entra role mapping ([#664](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/664)) ([1fa7a03](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1fa7a031fb613800ff6111c01229e751a80394d9))
* **data:** give polaris s3 access and narrow maintenance grants ([#665](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/665)) ([dd52e3e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/dd52e3e35435ac93d74fa54eea55b4d4fcc8720b))
* **data:** give superset dashboards five minutes before queries time out ([#715](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/715)) ([27a797d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/27a797d9423580b2f46c5e1904b2ecbd7f430026))
* **data:** give trino memory headroom outside the java heap ([#685](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/685)) ([77ec9d8](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/77ec9d8e307b35a2caea473687cd2d1265c293ba))
* **data:** grant polaris access to corvus lake objects ([#705](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/705)) ([43a7a4c](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/43a7a4c9e53285c835db65a5c9431a5c2f12388c))
* **data:** install authlib for the superset entra login ([#677](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/677)) ([564bf5a](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/564bf5a110ffb57c34f3f0d126d0fcbdc0adfc48))
* **data:** install superset drivers with uv ([#675](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/675)) ([a8e3ad3](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a8e3ad327167c0088080fb177657ff3de397ca40))
* **data:** keep polaris token keys decryptable by flux ([#662](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/662)) ([5edc30e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5edc30e3bfeb03901b1366aa1e7f129859d843f6))
* **data:** keep superset alive while dashboards query duckdb ([#707](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/707)) ([e039933](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/e0399339cf913d7c413998f4b4356c281cef1037))
* **data:** let airflow workers manage maintenance script configmaps ([#667](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/667)) ([a91f65f](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a91f65f1a07f8551b61324ec525b398253b62899))
* **data:** let lake sql list views ([#676](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/676)) ([82443db](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/82443dbd4a0b1d83cb467db0e45351684087ab66))
* **data:** let the corvus ingest principal read the capture namespace ([#681](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/681)) ([7cb1ee9](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/7cb1ee9a7bd7ed38a1a499ba4dcd150ab4e1800c))
* **data:** prefer rather than require separate nodes for trino workers ([#689](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/689)) ([cdb672e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/cdb672ed3ba2f3146ed02241ff208fb5b2827a12))
* **data:** request less memory for the trino workers ([#687](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/687)) ([80d2b6a](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/80d2b6ab8f162f6d7c4ff5b31987684c1eeb7cea))
* **data:** roll trino workers one at a time without a surge pod ([#688](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/688)) ([1ed0577](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1ed057708f8bf0e8d29fb47ab3033b885df61c18))
* **data:** rotate polaris root credentials and drop bootstrap job ([#666](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/666)) ([0b2b054](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/0b2b0542065b0449ba57e4ba0cfc6bba0bcff547))
* **data:** run superset pods with a numeric user id ([#674](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/674)) ([634e15d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/634e15de7cb52a5986821dff13dd8ad55b251927))
* **data:** unblock polaris bootstrap and airflow migrations ([#663](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/663)) ([75f78d4](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/75f78d4313fbbbafc45e73f3c70ca43c4f5acdfe))
* **dependency-track:** pause the sbom sync and run it every 6 hours ([#700](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/700)) ([207dd8d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/207dd8d5010041b3bb67042f2d69e522627a5d5f))
* **ingot-loadtest:** cap the load test bucket with an allowed quota field ([#696](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/696)) ([a7ca632](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a7ca632b123284d0c0dd9faf450f4add345e87b6))
* **ingot-loadtest:** keep the load test database schedulable ([#698](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/698)) ([2b0f82a](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/2b0f82a4550cb429112f3cc1804e59d9579938cd))
* **ingot-loadtest:** turn off the backup plugin on the load test database ([#697](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/697)) ([111c91d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/111c91d3abc1bc9fc7905e11a107344b87ebdf7f))
* **messaging:** upgrade strimzi crds with the chart ([#757](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/757)) ([01d0d8d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/01d0d8d1f3060c7312464fa7a880a1fe9ceb65cb))
* **observability:** match normalised le labels in the apiserver bucket filter ([#755](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/755)) ([56da6cd](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/56da6cda93a0b42a8aff8362fbe7c80f9dfc4684))
* **observability:** reduce discord alert noise ([#737](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/737)) ([3c18e03](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/3c18e03af31ace29f6f194b19f2371a5b5a8cad5))
* **operators:** right-size cert-manager, step-issuer and mariadb-operator ([#736](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/736)) ([1b378af](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1b378afa7ae28b6b7bf5efb5c401e974291c0714))
* **platform:** stop avoidable airflow and sentry restarts ([#759](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/759)) ([f28f32a](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/f28f32ae5bc4c2a625709e8fba8765d41a1adb6a))
* **security:** give the kyverno reports controller enough memory to stop oom loops ([#714](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/714)) ([2b7a03f](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/2b7a03f6bc2f1fa821c2b9fc678cc6895bb05b23))
* **security:** stop generating trivy sbom reports ([#752](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/752)) ([18d844e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/18d844e45ab507b20698b4d5714e883ea33405ea))
* **storage:** size the ceph csi memory requests to their real usage ([#710](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/710)) ([969e536](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/969e5360da2e75ca5775340ecb0b1eb901682a17))


### Performance Improvements

* **flux:** reconcile helmreleases every 10 minutes ([#654](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/654)) ([637a09d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/637a09d22bda9100c6f544e74b19747c361668de))
* **security:** keep one sbom per image and skip ci runner namespaces in trivy ([#711](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/711)) ([921e7ef](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/921e7ef7459368ab03926a495985b58b4d988a1a))
* **security:** reconcile security helm releases every 10 minutes ([#655](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/655)) ([9b32907](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/9b329071e8634a154b213dbf4b33bc897fbd0045))

## [0.1.1](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/compare/v0.1.0...v0.1.1) (2026-10-11)


### Bug Fixes

* **ci:** pass the org release app secrets to release-please ([#405](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/405)) ([15351c2](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/15351c22206439959ce2d2957126717db6536c39))
