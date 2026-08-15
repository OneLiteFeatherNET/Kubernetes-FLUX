# SOPS – Secrets Management

This repository uses [SOPS](https://github.com/getsops/sops) (Secrets OPerationS) to store Kubernetes secrets encrypted inside the Git repository. Encryption is based on [age](https://github.com/FiloSottile/age) keys.

## Prerequisites

```bash
# Install SOPS (Linux)
curl -LO https://github.com/getsops/sops/releases/latest/download/sops-v3.x.x.linux.amd64
chmod +x sops-v3.x.x.linux.amd64 && sudo mv sops-v3.x.x.linux.amd64 /usr/local/bin/sops

# Alternatively via package manager
brew install sops age          # macOS
nix-env -iA nixpkgs.sops nixpkgs.age   # NixOS
```

`age` must also be installed (`age --version`). It ships `age-keygen`, which is
the only key-management tool you need — there is no keyring, no web of trust and
no key server. An age identity is a single line of text in a single file.

---

## Generate your first key (new member)

```bash
# Generate a key pair in the location sops reads by default
mkdir -p ~/.config/sops/age && chmod 700 ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

# Show your public key and send it to a maintainer
age-keygen -y ~/.config/sops/age/keys.txt
# Output: age1kfcggatwrwslka8sssdw2sy05jwntft84cw979gkvzp9wksnyy0qllsz8s
```

The **public** key is the `age1...` string — that is what goes into `.sops.yaml`
and what you share. The file `keys.txt` contains the matching private key
(`AGE-SECRET-KEY-1...`) — never share or commit it.

> `sops` reads `~/.config/sops/age/keys.txt` automatically. To use a key from
> somewhere else, point `SOPS_AGE_KEY_FILE` at it.

---

## Add a new member (maintainer)

```bash
# 1. Add their public key to the age list in .sops.yaml (comma-separated).
#    There is only one SOPS config, at the repo root. No key import step —
#    the public key in .sops.yaml is all sops needs to encrypt to them.

# 2. Re-encrypt EVERY encrypted file against the new recipient list
./scripts/rekey.sh

# 3. Verify nothing was missed — this must print nothing
./scripts/rekey.sh --list | xargs grep -L '<THEIR-PUBLIC-KEY>'

# 4. Commit
git add .sops.yaml $(./scripts/rekey.sh --list)
git commit -m "chore: add <name> as SOPS recipient"
```

---

## Remove a member

```bash
# 1. Remove their public key from the age list in .sops.yaml

# 2. Re-encrypt EVERY encrypted file — mandatory, and it must be every one.
#    A file you skip stays decryptable by the removed key forever, because
#    they already have the old ciphertext from the git history.
./scripts/rekey.sh

# 3. Verify their key is gone everywhere — this must print nothing
./scripts/rekey.sh --list | xargs grep -l '<THEIR-PUBLIC-KEY>'

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

| Name        | Purpose         | Public key                                                     | Private key lives in                     |
|-------------|-----------------|----------------------------------------------------------------|------------------------------------------|
| TheMeinerLP | human maintainer | `age1kfcggatwrwslka8sssdw2sy05jwntft84cw979gkvzp9wksnyy0qllsz8s` | `~/.config/sops/age/keys.txt` + escrow   |
| flux        | cluster decryption | `age10x4xtzptvjztg9jkxr4m5luw3mfyqwww499lgnnqx32h2xytu5nqkjwqaq` | k8s secret `flux-system/sops-age`, key `age.agekey` |
| ci          | CI pipelines    | `age1rrus5c38flq6yg6l56d0cecgzap0fhdew64n7ygz7lngjwpzws4qcnge3x` | GitHub Actions secret `SOPS_AGE_KEY`     |

> Public keys are managed in `.sops.yaml` at the repo root. There is no per-cluster SOPS config.

**Every recipient is a separate key on purpose.** Each can be rotated on its own
without touching the others, and the cluster's key never has to leave the
cluster. Keep an offline escrow copy of at least the `flux` key — lose every
copy of every key and all 73 encrypted files are permanently unreadable.

### Why CI has a key but no decrypting job

`ci` is a recipient so that a future workflow *can* be given a decryption key
without a repo-wide re-key. No workflow currently uses it: `flux-validate.yaml`
only asserts that matching files are encrypted and carry every recipient, which
needs no private key at all. Adding a decrypting job hands GitHub Actions every
credential in the cluster — weigh that before you write one.

---

## Cluster decryption (`flux-system/sops-age`)

Every Flux `Kustomization` under `clusters/feather-core/` (except
`internal-certs`) carries:

```yaml
decryption:
  provider: sops
  secretRef:
    name: sops-age
```

`kustomize-controller` picks the backend from the *key name inside the secret*:
an entry ending in `.agekey` is loaded as an age identity, one ending in `.asc`
as a PGP key. That is why one secret can hold both during a migration.

### Re-creating `sops-age` on a fresh cluster

```bash
kubectl -n flux-system create secret generic sops-age \
  --from-file=age.agekey=/path/to/flux.agekey
```

Do this **before** bootstrapping Flux, or every layer fails with a decryption error.

### Rotating the cluster key

```bash
# 1. Generate the new key
age-keygen -o flux-new.agekey

# 2. Add its public key to .sops.yaml alongside the current one, re-key, push.
#    Both keys are now valid — nothing breaks yet.
./scripts/rekey.sh

# 3. Add the new private key to the cluster secret as a SECOND entry.
#    kustomize-controller tries every identity in the secret.
kubectl -n flux-system patch secret sops-age --type=json \
  -p="[{'op':'add','path':'/data/age-new.agekey','value':'<base64 of flux-new.agekey>'}]"

# 4. Verify no layer reports a decryption error, then remove the old public key
#    from .sops.yaml, re-key again, and drop the old entry from the secret.
```

The overlap in steps 2–3 is what makes the rotation uninterrupted: at no point
is there a revision the cluster cannot decrypt.

---

## Troubleshooting

**`Error: Failed to get the data key`**
→ Your public key is not listed as a recipient in `.sops.yaml`, or sops cannot find your private key.

```bash
age-keygen -y ~/.config/sops/age/keys.txt   # your public key
grep "$(age-keygen -y ~/.config/sops/age/keys.txt)" .sops.yaml   # are you a recipient?
```

**`no identity matched any of the recipients`**
→ The private key on this machine does not match any recipient on that file.
Either the file predates you being added (re-run `./scripts/rekey.sh`), or
`SOPS_AGE_KEY_FILE` points at the wrong key.

**Private key missing on a new machine**
→ Copy `~/.config/sops/age/keys.txt` across over a secure channel and `chmod 600` it.
There is nothing to import — the file *is* the key.

**A Flux layer reports a decryption error after a merge**
→ A file was committed without the `flux` key as a recipient. Revert the merge
commit (the previous ciphertext is still decryptable), then run
`./scripts/rekey.sh` and check the CI recipient assertion locally:

```bash
python3 scripts/check-sops-encryption.py
```
