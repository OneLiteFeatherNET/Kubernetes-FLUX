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

# Only the upstream Kubernetes schemas. Community CRD catalogs (datreeio)
# lag upstream and bake additionalProperties:false into every schema, so
# valid CRD fields like CNPG spec.affinity.topologySpreadConstraints fail
# regardless of -strict. -ignore-missing-schemas means CRDs are skipped
# rather than rejected; core resources are still strictly validated.
KUBECONFORM_COMMON=(
  -ignore-missing-schemas
  -kubernetes-version "${KUBERNETES_VERSION}"
  -skip Secret
  -summary
  -schema-location default
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

# Mirror the repo into a tmp dir and strip sops-encrypted patch
# references from every kustomization.yaml. Flux decrypts these at apply
# time via the cluster's sops-gpg key; CI has no key and the encrypted
# YAML is not parseable by kustomize. Stripping the patch entry lets us
# still validate the rest of the overlay (siblings, base resources).
# Encrypted secretGenerator inputs (*.sops.env) are fine: kustomize
# reads them as opaque bytes and the resulting Secrets are skipped by
# kubeconform anyway.
SANITIZED="$(mktemp -d)"
trap 'rm -rf "${BIN_DIR}" "${SANITIZED}"' EXIT
cp -a . "${SANITIZED}/repo"
SANITIZED_REPO="${SANITIZED}/repo"

python3 - "${SANITIZED_REPO}" <<'PY'
import os, sys, yaml
root = sys.argv[1]
stripped = 0
for dirpath, _dirs, files in os.walk(root):
    if "kustomization.yaml" not in files and "kustomization.yml" not in files:
        continue
    name = "kustomization.yaml" if "kustomization.yaml" in files else "kustomization.yml"
    full = os.path.join(dirpath, name)
    with open(full) as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        continue
    changed = False

    def is_sops(p):
        return isinstance(p, str) and (p.endswith(".sops.yaml") or p.endswith(".sops.yml"))

    patches = doc.get("patches")
    if isinstance(patches, list):
        kept = [e for e in patches if not (isinstance(e, dict) and is_sops(e.get("path")))]
        if len(kept) != len(patches):
            doc["patches"] = kept
            changed = True
    legacy = doc.get("patchesStrategicMerge")
    if isinstance(legacy, list):
        kept = [p for p in legacy if not is_sops(p)]
        if len(kept) != len(legacy):
            doc["patchesStrategicMerge"] = kept
            changed = True
    resources = doc.get("resources")
    if isinstance(resources, list):
        kept = [r for r in resources if not is_sops(r)]
        if len(kept) != len(resources):
            doc["resources"] = kept
            changed = True

    if changed:
        with open(full, "w") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False)
        stripped += 1
        print(f"sanitized {os.path.relpath(full, root)}", file=sys.stderr)
print(f"stripped sops patches from {stripped} kustomization.yaml file(s)", file=sys.stderr)
PY

for p in "${PATHS[@]}"; do
  dir="${SANITIZED_REPO}/${p#./}"
  if [[ ! -d "${dir}" ]]; then
    echo "::error::Flux path not found: ${p}"
    rc=1
    continue
  fi
  echo "::group::kustomize build ${p}"
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
