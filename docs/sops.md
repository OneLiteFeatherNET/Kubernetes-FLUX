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

# 2. Add their fingerprint to the pgp list in .sops.yaml (comma-separated).
#    There is only one SOPS config, at the repo root.

# 3. Re-encrypt EVERY encrypted file against the new recipient list
./scripts/rekey.sh

# 4. Verify nothing was missed — this must print nothing
./scripts/rekey.sh --list | xargs grep -L '<THEIR-FINGERPRINT>'

# 5. Commit
git add .sops.yaml $(./scripts/rekey.sh --list)
git commit -m "chore: add <name> as SOPS recipient"
```

---

## Remove a member

```bash
# 1. Remove their fingerprint from the pgp list in .sops.yaml

# 2. Re-encrypt EVERY encrypted file — mandatory, and it must be every one.
#    A file you skip stays decryptable by the removed key forever, because
#    they already have the old ciphertext from the git history.
./scripts/rekey.sh

# 3. Verify their key is gone everywhere — this must print nothing
./scripts/rekey.sh --list | xargs grep -l '<THEIR-FINGERPRINT>'

# 4. Commit
git add .sops.yaml $(./scripts/rekey.sh --list)
git commit -m "chore: remove <name> from SOPS recipients"
```

> Removing a recipient does **not** invalidate anything they already read.
> Treat every credential in this repo as known to them and rotate it.

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

## Current recipients

| Name         | PGP Fingerprint                            |
|--------------|--------------------------------------------|
| TheMeinerLP  | `0231831CB40B8E587B7353CBA3AF727721205A62` |

> Fingerprints are managed in `.sops.yaml` at the repo root. There is no per-cluster SOPS config.

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
