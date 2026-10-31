# Changelog

## [0.2.0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/compare/v0.1.1...v0.2.0) (2026-10-31)


### Features

* **blackhole:** deploy blackhole to feather-core ([#479](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/479)) ([0b5c9f2](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/0b5c9f2aaa68b22878832c8e2fa954ce07db4795))
* **bluemap:** update to v5.28 and configure maps for rendering ([1572f77](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1572f775452f6dd236d4abbb0957e4c60bb2f9fc))
* **corvus:** add the lake s3 credentials to the ml job environment ([#443](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/443)) ([2fb56f0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/2fb56f0048d51098e0d939732b6415e9b0d66632))
* **corvus:** deploy nats and corvus ([#440](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/440)) ([8642ab6](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/8642ab6b0b1d0e4591f3e4b60b5aa7d5dd77e2dc))
* **corvus:** expose nats and the corvus api on the internal gateway ([#444](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/444)) ([814c5e5](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/814c5e5f4f7f29486db5d3e564405e04513e253c))
* **databases:** add skygridng database and role ([#488](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/488)) ([f818f1d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/f818f1d6dfd1518fbf91d32d9ced7f1787d5d6c0))
* **grafana:** add minecraft proxy dashboard ([#502](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/502)) ([2a8d22d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/2a8d22d505364b1347c8e44d26f64965907d0cac))
* **grafana:** add quelea dashboard ([#491](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/491)) ([1f64153](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1f64153af705c0e7ab954c8c21eca5410144b9eb))
* **grafana:** track kubelet stalls per node with dashboard and alert ([#497](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/497)) ([5fcb2b8](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5fcb2b8294bf4f44c73f20d402e452ff9ab9feea))
* **platform:** add pod disruption budgets for multi-replica workloads ([#475](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/475)) ([1274521](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1274521095323ae1795cbb742e673079a440603f))
* **platform:** make feather-standard the global default priority class ([#474](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/474)) ([5cf8db6](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5cf8db6b34326ff4708e324e93059e4e13818dea))
* **postgresql:** add cygnus_stats databases for live and dev ([#435](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/435)) ([0ec9153](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/0ec9153be4edf61052ff38e58133e6ed402dbe53))
* **quelea:** deploy quelea 0.4.0 with entra id, otlp metrics, sentry and a rate limit ([#494](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/494)) ([227e1a5](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/227e1a526b54229dee33378031c2eab8a74f97de))
* **quelea:** deploy quelea 0.5.0 ([#501](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/501)) ([84353f4](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/84353f4217cf58595a25d858f943e9426d3d5507))
* **quelea:** deploy quelea to feather-core ([#489](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/489)) ([c1fa7e5](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/c1fa7e57c19977f7bc579d98f6b3953e1dceb78a))
* **reposilite:** add permanent staging environment ([#445](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/445)) ([b758388](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/b758388db14891625353b7834f347ee14c5641fc))
* **reposilite:** run the fixed ingot 1.2.0 nightly on staging ([#450](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/450)) ([439f643](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/439f643c64ce002fa3e9f2b1623d0e499a8e5b1f))
* **reposilite:** run the ingot 1.2.0 candidate nightly on staging ([#455](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/455)) ([55cc7d3](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/55cc7d333bfd3aefe37843a93007a74e8d2bfb04))
* **reposilite:** run the ingot 1.2.0 nightly on staging ([#448](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/448)) ([caa9f90](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/caa9f90a08e119fc4a07944349ee2fd5e5f4b3ed))
* **reposilite:** run the ingot 19189c7 nightly on staging ([#457](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/457)) ([b908112](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/b9081126b39ad6ae3c5f64b03b6e75c72030e4ef))
* **reposilite:** run the ingot nightly with ui-test fixes on staging ([#451](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/451)) ([1c08df4](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1c08df4d5e9119ca89a5ae133dc7647ab720ef90))
* **reposilite:** start the staging instance ([#447](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/447)) ([68d258b](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/68d258bca28c4195f550e17eae1e062ee7f9c3d6))
* **rook:** enable discovery daemon with 5-minute interval ([83b532b](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/83b532b7fe97d0ceda25da574fec9b8a535b2caf))
* **storage:** grant team members s3 access to cloudnet templates ([#490](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/490)) ([a5e591b](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a5e591bd15890073b39427a6bae6aec7d8e8e701))
* **sturnus:** roll out 0.17.0 with an in-cluster transcription endpoint ([#438](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/438)) ([5415e2f](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5415e2fe0d0dc5f7208c46ace5f868c208020c7c))
* **vulpes:** enable image automation for vulpes-generator dev ([#434](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/434)) ([4724cb0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/4724cb083beec035a54ebbfcbe9f5076f723adf7))


### Bug Fixes

* **blackhole:** allow appeals from the first day ([#507](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/507)) ([f08ab04](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/f08ab049272d8f5780548de11da546b670a7045a))
* **blackhole:** baseline flyway past the cnpg pooler function ([#499](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/499)) ([b6f48a9](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/b6f48a92cb5f6a5c7284012d19596c6b4e02292e))
* **blackhole:** serve the external api through the chart's nginx allow-list ([#498](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/498)) ([30d50a7](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/30d50a7da6014645744bc1e907621eac2ebfdddd))
* **databases:** add the analytics reader role for blackhole ([#500](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/500)) ([9b6117f](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/9b6117f37704598c06c6dade252e50cadb6fd193))
* **databases:** drop immutable exporter priority class from mariadb and maxscale ([#477](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/477)) ([94698f8](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/94698f851149d1e6b21a9304f73e83f5e942f4d8))
* **dependency-track:** apply affinity via the chart's supported keys ([#432](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/432)) ([d5e4bc0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/d5e4bc03632106b26b21927b0e814e20ba626512))
* **envoy-gateway:** update envoy gateway to 1.8.5 for ghsa-gmx8-fhfh-mgjx ([#412](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/412)) ([047a150](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/047a150cc72b0d3dd0fb8fe1d95cb56964946b1e))
* **foundation:** bump images with critical cves ([#466](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/466)) ([a35aeec](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a35aeeca46f09678ab0f4c2d85d4a3a65c8276ba))
* **grafana:** aggregate proxy backend panels per server ([#505](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/505)) ([6208435](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/6208435744eaa2e8e5d5d1fb85c379bc1567ea05))
* **grafana:** update grafana to 12.4.12 for cve-2026-27876 ([#410](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/410)) ([28c9c74](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/28c9c743babf5b6e9229b02ce69d4fa421945ee5))
* **n8n:** pin image to 2.42.6 instead of floating stable ([#409](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/409)) ([49a34b9](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/49a34b961a35d6dabfacfbd335d74ebb43b3f9bf))
* **observability:** bump images with critical cves ([#467](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/467)) ([4b6353d](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/4b6353d0af1b3db77712662d48825949c12d169e))
* **platform:** assign priority classes to unclassified workloads ([#476](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/476)) ([fa8bcd0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/fa8bcd033c1d079c543a9720d8140f9256a31559))
* **postgresql:** pin cnpg image to 17.11-standard-trixie ([#411](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/411)) ([e2ef1f5](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/e2ef1f5abf57dd6e9e04264a85c2a1ff7d273ba8))
* **quelea:** issue an internal tls certificate for quelea.apps.onelite.feather ([#493](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/493)) ([a0836eb](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/a0836eb758203154106929cce733d12188b28412))
* **renovate:** stop automerging database and storage updates ([#416](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/416)) ([6310420](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/63104206b0ebcb88d87e47a8fc18366a654cf4b9))
* **reposilite:** fix staging database string and hold staging at zero replicas ([#446](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/446)) ([5c2cf89](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5c2cf893ce7624dc3ebb5072b2418cb69f4e8f7f))
* **reposilite:** query maven central first and scope proxy upstreams by group ([#495](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/495)) ([0a3b986](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/0a3b98600acf1c584993709280d63791e69afcd4))
* **sturnus:** deploy tagged releases instead of every commit on main ([#439](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/439)) ([bcc9f6e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/bcc9f6ed26e0bb116353dc700d6e42c1924c6808))

## [0.1.1](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/compare/v0.1.0...v0.1.1) (2026-10-11)


### Bug Fixes

* **ci:** pass the org release app secrets to release-please ([#405](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/405)) ([15351c2](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/15351c22206439959ce2d2957126717db6536c39))
