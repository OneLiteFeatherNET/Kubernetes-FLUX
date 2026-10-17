# Changelog

## [0.2.0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/compare/v0.1.1...v0.2.0) (2026-10-17)


### Features

* **corvus:** add the lake s3 credentials to the ml job environment ([#443](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/443)) ([2fb56f0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/2fb56f0048d51098e0d939732b6415e9b0d66632))
* **corvus:** deploy nats and corvus ([#440](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/440)) ([8642ab6](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/8642ab6b0b1d0e4591f3e4b60b5aa7d5dd77e2dc))
* **corvus:** expose nats and the corvus api on the internal gateway ([#444](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/444)) ([814c5e5](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/814c5e5f4f7f29486db5d3e564405e04513e253c))
* **postgresql:** add cygnus_stats databases for live and dev ([#435](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/435)) ([0ec9153](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/0ec9153be4edf61052ff38e58133e6ed402dbe53))
* **reposilite:** add permanent staging environment ([#445](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/445)) ([b758388](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/b758388db14891625353b7834f347ee14c5641fc))
* **reposilite:** run the fixed ingot 1.2.0 nightly on staging ([#450](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/450)) ([439f643](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/439f643c64ce002fa3e9f2b1623d0e499a8e5b1f))
* **reposilite:** run the ingot 1.2.0 candidate nightly on staging ([#455](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/455)) ([55cc7d3](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/55cc7d333bfd3aefe37843a93007a74e8d2bfb04))
* **reposilite:** run the ingot 1.2.0 nightly on staging ([#448](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/448)) ([caa9f90](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/caa9f90a08e119fc4a07944349ee2fd5e5f4b3ed))
* **reposilite:** run the ingot 19189c7 nightly on staging ([#457](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/457)) ([b908112](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/b9081126b39ad6ae3c5f64b03b6e75c72030e4ef))
* **reposilite:** run the ingot nightly with ui-test fixes on staging ([#451](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/451)) ([1c08df4](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/1c08df4d5e9119ca89a5ae133dc7647ab720ef90))
* **reposilite:** start the staging instance ([#447](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/447)) ([68d258b](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/68d258bca28c4195f550e17eae1e062ee7f9c3d6))
* **sturnus:** roll out 0.17.0 with an in-cluster transcription endpoint ([#438](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/438)) ([5415e2f](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5415e2fe0d0dc5f7208c46ace5f868c208020c7c))
* **vulpes:** enable image automation for vulpes-generator dev ([#434](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/434)) ([4724cb0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/4724cb083beec035a54ebbfcbe9f5076f723adf7))


### Bug Fixes

* **dependency-track:** apply affinity via the chart's supported keys ([#432](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/432)) ([d5e4bc0](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/d5e4bc03632106b26b21927b0e814e20ba626512))
* **envoy-gateway:** update envoy gateway to 1.8.5 for ghsa-gmx8-fhfh-mgjx ([#412](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/412)) ([047a150](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/047a150cc72b0d3dd0fb8fe1d95cb56964946b1e))
* **grafana:** update grafana to 12.4.12 for cve-2026-27876 ([#410](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/410)) ([28c9c74](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/28c9c743babf5b6e9229b02ce69d4fa421945ee5))
* **n8n:** pin image to 2.42.6 instead of floating stable ([#409](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/409)) ([49a34b9](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/49a34b961a35d6dabfacfbd335d74ebb43b3f9bf))
* **postgresql:** pin cnpg image to 17.11-standard-trixie ([#411](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/411)) ([e2ef1f5](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/e2ef1f5abf57dd6e9e04264a85c2a1ff7d273ba8))
* **renovate:** stop automerging database and storage updates ([#416](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/416)) ([6310420](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/63104206b0ebcb88d87e47a8fc18366a654cf4b9))
* **reposilite:** fix staging database string and hold staging at zero replicas ([#446](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/446)) ([5c2cf89](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/5c2cf893ce7624dc3ebb5072b2418cb69f4e8f7f))
* **sturnus:** deploy tagged releases instead of every commit on main ([#439](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/439)) ([bcc9f6e](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/bcc9f6ed26e0bb116353dc700d6e42c1924c6808))

## [0.1.1](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/compare/v0.1.0...v0.1.1) (2026-10-11)


### Bug Fixes

* **ci:** pass the org release app secrets to release-please ([#405](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/issues/405)) ([15351c2](https://github.com/OneLiteFeatherNET/Kubernetes-FLUX/commit/15351c22206439959ce2d2957126717db6536c39))
