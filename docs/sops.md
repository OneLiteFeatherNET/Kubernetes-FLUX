# SOPS – Secrets Management

This repository uses [SOPS](https://github.com/getsops/sops) (Secrets OPerationS) to store Kubernetes secrets encrypted inside the Git repository. Encryption is based on PGP keys.

## Prerequisites

```bash
# Install SOPS (Linux)
curl -LO https://github.com/getsops/sops/releases/latest/download/sops-v3.x.x.linux.amd64
chmod +x sops-v3.x.x.linux.amd64 && sudo mv sops-v3.x.x.linux.amd64 /usr/local/bin/sops

# Alternatively via package manager
brew install sops          # macOS
nix-env -iA nixpkgs.sops   # NixOS
```

GPG must also be installed (`gpg --version`).

---

## Generate your first key (new member)

```bash
# Generate a key pair
gpg --full-generate-key
# Recommended: RSA 4096 bit, no expiry for cluster keys

# Show your fingerprint
gpg --list-keys --fingerprint <your-email>
# Output: 0231 831C B40B 8E58 7B73  53CB A3AF 7277 2120 5A62
# → Fingerprint without spaces: 0231831CB40B8E587B7353CBA3AF727721205A62

# Export your public key and send it to a maintainer
gpg --armor --export <your-email> > my-public-key.asc
```

---

## Add a new member (maintainer)

```bash
# 1. Import the member's public key
gpg --import their-public-key.asc

# 2. Add their fingerprint to both .sops.yaml files (comma-separated):
#      .sops.yaml
#      clusters/feather-core/.sops.yaml

# 3. Re-encrypt all encrypted files with the updated recipient list
find . -name "*.sops.yaml" | xargs -I {} sops updatekeys {}
find . -name "*.sops.env"  | xargs -I {} sops updatekeys {}

# 4. Commit the changes
git add .sops.yaml clusters/feather-core/.sops.yaml
git add $(find . -name "*.sops.*")
git commit -m "chore: add <name> as SOPS recipient"
```

---

## Remove a member

```bash
# 1. Remove their fingerprint from both .sops.yaml files

# 2. Re-encrypt all secrets — mandatory!
#    (the removed key can still decrypt as long as it has access to the old ciphertext)
find . -name "*.sops.yaml" | xargs -I {} sops updatekeys {}

# 3. Commit
git commit -am "chore: remove <name> from SOPS recipients"
```

---

## Edit secrets

```bash
# Open a file decrypted in your editor (re-encrypted automatically on save)
sops infrastructure/clusters/feather-core/rook/secrets.sops.yaml

# Print a single value
sops --decrypt --extract '["data"]["userKey"]' infrastructure/clusters/feather-core/rook/secrets.sops.yaml

# Create a new encrypted file (automatically picks the matching rule from .sops.yaml)
sops infrastructure/clusters/feather-core/myapp/new-secret.sops.yaml
```

---

## Create a new secret file

```bash
# 1. Open a new file with the correct suffix (*.sops.yaml)
sops infrastructure/clusters/feather-core/myapp/credentials.sops.yaml

# SOPS opens your editor. Write plain YAML, e.g.:
# apiVersion: v1
# kind: Secret
# metadata:
#   name: myapp-credentials
#   namespace: myapp
# type: Opaque
# data:
#   password: supersecret

# The file is automatically encrypted on save.
```

---

## Reference in Kustomization

Reference the SOPS file as a generator in `kustomization.yaml`:

```yaml
generators:
  - credentials.sops.yaml
```

Flux decrypts the file automatically via the SOPS provider configured in the cluster.

---

## Azure Key Vault (Entra ID)

Instead of PGP keys, SOPS can use an **Azure Key Vault** key as the Key Encryption Key (KEK). This removes the need to distribute private keys to team members — access is controlled entirely via Azure Entra ID.

### How authentication works

SOPS uses Azure's default credential chain and tries the following in order:

| Method | When to use |
|---|---|
| **Workload Identity** | AKS clusters (recommended for production) |
| **Managed Identity** | Azure VMs / non-AKS compute |
| **Service Principal** | CI/CD pipelines, local dev |
| **Azure CLI** | Local development (`az login`) |

### 1. Create the Key Vault and key

```bash
# Create a Key Vault
az keyvault create --name <vault-name> --resource-group <rg> --location <region>

# Create an RSA key used for SOPS encryption
az keyvault key create --vault-name <vault-name> --name sops-key --kty RSA --size 4096

# Get the key identifier (used in .sops.yaml)
az keyvault key show --vault-name <vault-name> --name sops-key --query key.kid -o tsv
# → https://<vault-name>.vault.azure.net/keys/sops-key/<version>
```

### 2. Grant access via Entra ID

```bash
# Assign the "Key Vault Crypto User" role to a user, group, or managed identity
az role assignment create \
  --role "Key Vault Crypto User" \
  --assignee <object-id-or-email> \
  --scope $(az keyvault show --name <vault-name> --query id -o tsv)
```

### 3. Configure .sops.yaml

Add the `azure_keyvault` field to your creation rules. PGP and Azure KV can be combined so that both can decrypt:

```yaml
creation_rules:
  - path_regex: .*\.sops\.ya?ml$
    azure_keyvault: https://<vault-name>.vault.azure.net/keys/sops-key/
    # Optional: also keep PGP for local dev without Azure CLI
    pgp: 0231831CB40B8E587B7353CBA3AF727721205A62
```

Omit the key version at the end of the URL so SOPS always uses the latest version.

### 4. Authenticate locally

```bash
# Easiest for local development — SOPS picks this up automatically
az login
```

For a Service Principal (e.g. CI/CD):

```bash
export AZURE_TENANT_ID="<tenant-uuid>"
export AZURE_CLIENT_ID="<app-id>"
export AZURE_CLIENT_SECRET="<password>"
```

### 5. Workload Identity for Flux on AKS

The `kustomize-controller` needs permission to unwrap the DEK at reconcile time.

```bash
# Enable OIDC issuer and workload identity on the AKS cluster (if not already done)
az aks update --name <cluster> --resource-group <rg> \
  --enable-oidc-issuer --enable-workload-identity

# Create a managed identity for the controller
az identity create --name flux-sops --resource-group <rg>

# Grant it the Crypto User role on the Key Vault
az role assignment create \
  --role "Key Vault Crypto User" \
  --assignee $(az identity show --name flux-sops --resource-group <rg> --query principalId -o tsv) \
  --scope $(az keyvault show --name <vault-name> --query id -o tsv)

# Create a federated credential linking the K8s ServiceAccount to the managed identity
az identity federated-credential create \
  --name flux-kustomize-controller \
  --identity-name flux-sops \
  --resource-group <rg> \
  --issuer $(az aks show --name <cluster> --resource-group <rg> --query oidcIssuerProfile.issuerUrl -o tsv) \
  --subject system:serviceaccount:flux-system:kustomize-controller \
  --audience api://AzureADTokenExchange
```

Then patch the `kustomize-controller` ServiceAccount in your Flux bootstrap config:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kustomize-controller
  namespace: flux-system
  annotations:
    azure.workload.identity/client-id: <managed-identity-client-id>
    azure.workload.identity/tenant-id: <tenant-id>
  labels:
    azure.workload.identity/use: "true"
```

---

## Current recipients

| Name         | PGP Fingerprint                            |
|--------------|--------------------------------------------|
| TheMeinerLP  | `0231831CB40B8E587B7353CBA3AF727721205A62` |

> Fingerprints are managed in `.sops.yaml` and `clusters/feather-core/.sops.yaml`.

---

## Troubleshooting

**`Error: Failed to get the data key`**
→ Your PGP key is not listed as a recipient, or you have not imported your private key.

```bash
gpg --list-secret-keys   # Check whether your private key is present
```

**`gpg: decryption failed: No secret key`**
→ The private key is missing on this machine. Export it from another device and import it here:

```bash
# Export (on the device that has the key)
gpg --armor --export-secret-keys <email> > private-key.asc

# Import (on the new device) — NEVER commit this file!
gpg --import private-key.asc
```

**File was not re-encrypted after `updatekeys`**
→ Make sure all recipients' public keys are present in your local GPG keyring before running `updatekeys`.
