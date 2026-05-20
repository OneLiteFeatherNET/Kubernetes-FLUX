#!/usr/bin/env bash
# Flux manifest validation: build every Flux Kustomization path with
# kustomize and schema-check the rendered output with kubeconform.
# Secrets are skipped (sops-encrypted payloads are not valid plaintext).
set -euo pipefail

KUSTOMIZE_VERSION="${KUSTOMIZE_VERSION:-5.7.1}"
KUBECONFORM_VERSION="${KUBECONFORM_VERSION:-0.7.0}"
KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.31.0}"

BIN_DIR="$(mktemp -d)"
trap 'rm -rf "${BIN_DIR}"' EXIT
export PATH="${BIN_DIR}:${PATH}"

echo "::group::Install tooling"
curl -fsSL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
  | tar -xz -C "${BIN_DIR}"
curl -fsSL "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" \
  | tar -xz -C "${BIN_DIR}" kubeconform
chmod +x "${BIN_DIR}/kustomize" "${BIN_DIR}/kubeconform"
kustomize version
kubeconform -v
echo "::endgroup::"

# Schema locations: upstream Kubernetes + the community CRD catalog so that
# Flux, cert-manager, Envoy Gateway, step-issuer, CNPG, etc. resolve.
SCHEMA_LOCATIONS=(
  -schema-location default
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
)

# -strict intentionally omitted: community CRD schemas (datreeio catalog)
# lag upstream and frequently flag valid fields (e.g. CNPG
# spec.affinity.topologySpreadConstraints) as additionalProperties.
KUBECONFORM_COMMON=(
  -ignore-missing-schemas
  -kubernetes-version "${KUBERNETES_VERSION}"
  -skip Secret
  -summary
  "${SCHEMA_LOCATIONS[@]}"
)

rc=0

# 1. Validate the Flux bootstrap + cluster Kustomization CRs themselves.
echo "::group::Validate Flux control-plane manifests"
if ! kubeconform "${KUBECONFORM_COMMON[@]}" \
  clusters/feather-core/flux-system/gotk-components.yaml \
  clusters/feather-core/*.yaml; then
  rc=1
fi
echo "::endgroup::"

# 2. Discover every Flux Kustomization spec.path and build it.
mapfile -t PATHS < <(
  python3 - <<'PY'
import glob, sys
try:
    import yaml
except ImportError:
    sys.stderr.write("PyYAML missing\n"); sys.exit(2)
seen = []
for f in sorted(glob.glob("clusters/feather-core/*.yaml")):
    with open(f) as fh:
        for doc in yaml.safe_load_all(fh):
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == "Kustomization" and \
               doc.get("apiVersion", "").startswith("kustomize.toolkit.fluxcd.io"):
                p = (doc.get("spec") or {}).get("path")
                if p and p not in seen:
                    seen.append(p)
print("\n".join(seen))
PY
)

for p in "${PATHS[@]}"; do
  dir="${p#./}"
  if [[ ! -d "${dir}" ]]; then
    echo "::error::Flux path not found: ${p}"
    rc=1
    continue
  fi
  echo "::group::kustomize build ${dir}"
  if ! kustomize build --load-restrictor=LoadRestrictionsNone "${dir}" \
    | kubeconform "${KUBECONFORM_COMMON[@]}"; then
    rc=1
  fi
  echo "::endgroup::"
done

if [[ "${rc}" -ne 0 ]]; then
  echo "::error::Flux manifest validation failed"
fi
exit "${rc}"
