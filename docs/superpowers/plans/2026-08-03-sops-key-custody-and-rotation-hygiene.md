# SOPS Key Custody and Rotation Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disarm the `.sops.yaml` creation rule that emits cleartext files carrying a valid-looking `sops:` block (the mechanism that published the step-ca private keys in 2025), escrow a second SOPS recipient so the repo survives loss of the single PGP key, and make credential rotation a documented, executable operation instead of something that has never been done.

**Architecture:** Four separately-mergeable PRs, ordered by rising risk. PR 1 touches only `.sops.yaml`, docs and a new script — **zero ciphertext is rewritten and Flux reads none of it**, so it cannot break the cluster. PR 2 adds an offline-escrowed `age` recipient and rewrites all 72 encrypted files in one commit — the only PR with cluster-wide blast radius. PR 3 is documentation only (the rotation runbook + the three dangling references to a doc that does not exist). PR 4 deploys `stakater/reloader` so secret edits stop being silently inert; it is optional and gated on a human decision.

**Tech Stack:** SOPS 3.13.3 (PGP + age), GnuPG, `age`/`age-keygen`, FluxCD `kustomize-controller` SOPS decryption via `flux-system/sops-gpg`, Kustomize `secretGenerator`, Helm/Flux `HelmRelease` (for reloader).

---

## Prerequisites

Before starting, confirm all of the following. Any `NO` stops the plan.

```bash
sops --version                      # expect: sops 3.13.3 (or newer 3.x)
gpg --list-secret-keys --with-colons 0231831CB40B8E587B7353CBA3AF727721205A62 | grep '^sec'
                                    # expect: a line starting `sec:u:4096:1:A3AF727721205A62:...`
command -v age-keygen               # expect: a path (needed from PR 2 on)
command -v jq python3               # expect: two paths (health gates use jq; Task 9's
                                    #   map-regeneration script uses python3)
kubectl config current-context      # expect: admin@feather-core
git status --porcelain              # run from the repo root; expect: empty (clean tree)
```

The private PGP key **must** be in the local keyring: `sops updatekeys` in PR 2 has to decrypt every file to re-encrypt it. If it is missing, PR 2 cannot be executed from this machine at all.

## Cross-theme dependencies

- **This theme is a prerequisite for theme 2 (credential rotation).** Do not begin any rotation work until PR 3 of this plan is merged: without `docs/rotation.md` the Dragonfly password rotation misses two of its six locations (see the correction below), and without PR 4 nothing restarts.
- **This theme depends on nothing.** It can start immediately.
- **PR 2 must not overlap in time with any other PR that edits an encrypted file.** `sops updatekeys` rewrites all 72; a concurrent Renovate or feature PR touching a `*.sops.env` produces a conflict on a file whose diff is pure ciphertext and therefore unmergeable by hand. Coordinate: land PR 2 on a quiet `main`.
- **Talos:** nothing in this plan touches `/mnt/projects/lab/talos-cluster`. That repo already has three `age` recipients plus a CI key held as the `SOPS_AGE_CI_KEY` GitHub secret (`talos-cluster/.sops.yaml`, `SECURITY.md:95-97`) — it is the model this plan copies, not a target.

## Global Constraints

- Conventional Commits enforced by CI (`commitlint.config.mjs`): types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`; subject starts lowercase; header ≤100 chars. The PR title is linted too.
- `./scripts/validate.sh` must pass locally before every commit.
- A change only takes effect when pushed to `main`. Rebase before pushing — Renovate moves `main`.
- Never hammer `flux reconcile` in a loop. One reconcile per stage, then verify.
- **Never print a decrypted secret value.** Every verification in this plan works on fingerprints, key names or `sha256` prefixes.
- `generatorOptions.disableNameSuffixHash: true` is set on every overlay: changing a Secret's contents does **not** roll the consuming Deployment. Until PR 4 lands, every secret change needs an explicit `kubectl rollout restart`.

---

## Corrections to the audit that this plan acts on

Verified against the live repo and cluster on 2026-08-03. Where these differ from the audit findings, **the numbers below are the correct ones.**

1. **72 encrypted files, not 73.** The audit's `*.sops.yaml` count of 3 includes `clusters/feather-core/.sops.yaml` — the config file itself, which is not encrypted. Only 2 real `*.sops.yaml` secrets exist (`infrastructure/clusters/feather-core/rook/secrets.sops.yaml`, `.../base-controllers/step-certificates/release.sops.yaml`). Any `find . -name '*.sops.*'` re-key loop would try to `updatekeys` the config file and error. `scripts/rekey.sh` in Task 2 excludes it explicitly.

2. **The Dragonfly password lives in 6 files / 9 keys, not 4 files.** The audit fingerprinted exact values and so missed the two apps that embed it inside a connection URI. Verified by substring search over all decrypted `*.env`/`*.sops.env` (values never printed):

   | File | Key(s) | Generated Secret | Namespace |
   |---|---|---|---|
   | `infrastructure/clusters/feather-core/controllers/dragonfly/dragonfly.env` | `password` | `dragonfly-auth` | `dragonfly` |
   | `apps/clusters/feathre-core/base-apps/harbor/harbor.env` | `REDIS_PASSWORD` | `harbor-secret` | `harbor` |
   | `apps/clusters/feathre-core/base-apps/n8n/n8n-redis.sops.env` | `redis-password` | `n8n-redis` | `n8n` |
   | `apps/clusters/feathre-core/base-apps/outline/outline.sops.env` | `REDIS_URL`, `REDIS_COLLABORATION_URL` (embedded in URI) | `outline-env` | `outline` |
   | `apps/clusters/feathre-core/base-apps/plane/plane.sops.env` | `REDIS_URL`, `CELERY_BROKER_URL` (embedded in URI) | `plane-app-env`, `plane-doc-store`, `plane-live-env`, `plane-silo`, `plane-rabbitmq`, `plane-opensearch`, `plane-pi-api` (all 7 generated from the same file) | `plane` |
   | `apps/clusters/feathre-core/base-apps/shlink/shlink.env` | `REDIS_SERVERS_PASSWORD`, `REDIS_SERVERS` (embedded in URI) | `shlink-secret` | `shlink` |

   A rotation done from the audit's 4-file list would leave Outline and Plane broken at their next restart.

3. **The `.*\.yaml$` creation rule governs zero existing files.** `grep -rl 'ENC\[AES256_GCM' --include='*.yaml' apps infrastructure clusters helm` returns exactly the two `*.sops.yaml` files above, and both match rule 1 (whole-file encryption), not rule 2. Deleting rule 2 changes no existing ciphertext. Confirmed empirically that deleting it makes `sops -e` on a plain `.yaml` fail closed with `error loading config: no matching creation rules found`, while `*.sops.yaml` still whole-file encrypts.

4. **`prism` and `reposilite` share one MariaDB password** (`infrastructure/clusters/feather-core/configs/mariadb-galera/passwords/prism.sops.env` and `.../reposilite.sops.env`, identical value). Two distinct DB roles, one credential. Recorded in `docs/rotation.md` in Task 9; **not** fixed by this plan.

5. **`flux get kustomizations -A` legitimately flaps.** Observed three separate snapshots minutes apart, each showing a *different* set of 3–6 layers reporting `dependency '<x>' is not ready` at the same healthy revision — that is normal reconcile churn on this dependency graph, not a fault. A "wait until every row says True" gate will hang. The health gates in this plan therefore test for *decryption* failures specifically.

---

## Decision gates

Each is called out again at the task that needs it. Do not proceed past a gate without the maintainer's answer.

- **Gate A (Task 5): what medium holds the escrow private key?** Options and recommendation in Task 5. Blocks PR 2.
- **Gate B (Task 10): deploy `stakater/reloader`, and with what scope?** Options in Task 10. Blocks PR 4 only.
- **Gate C (Task 9, informational): should the Dragonfly password be collapsed to one source?** This plan deliberately does **not** do it — see "Deliberately out of scope". The runbook records the decision as open.

---

## Deliberately out of scope

- **Rotating any credential.** This plan makes rotation *possible* and *documented*; performing it is theme 2. Nothing here changes a secret's value.
- **Collapsing the Dragonfly password to a single Secret.** The audit's suggested fix ("n8n already does this correctly via `secretKeyRef`") does not hold: n8n's `secretKeyRef` points at `n8n/n8n-redis`, which is its own private encrypted copy — it is the 3rd copy, not a reference to a shared one. A real consolidation needs (a) a cross-namespace Secret replicator (none installed: `kubectl get deploy -A | grep -iE 'reflector|kubed|replicator'` → no matches) across 6 namespaces, and (b) a way to feed Outline/Plane/shlink, which need the password *interpolated into a URI*, not as a discrete field — `secretKeyRef` cannot do that. That is a design change with its own blast radius, not a hygiene fix. Recorded as Gate C; recommended follow-up theme.
- **External Secrets Operator / Vault.** A one-maintainer, self-hosted cluster does not need a secrets platform; the fix for bus-factor-one here is an offline escrow key, which costs 20 minutes.
- **Moving the SOPS private key out of `flux-system/sops-gpg`.** This is Flux's own design (`decryption.secretRef`); there is no supported alternative. The audit's "exposure" leg of `sops-bus-factor-one` is correctly downgraded — this plan addresses the *loss* leg only.
- **Enabling GitHub secret scanning / push protection** (both currently `disabled` on the repo). Worth doing, but it is a repo-settings change, not a manifest change, and belongs with the repo-hygiene theme.
- **The `helm/` charts.** Untouched; no `Chart.yaml` version bump is needed anywhere in this plan.

## What could not be verified

- Whether an offline copy of the `0231831C…` private key already exists anywhere (paper, USB, password manager). The repo, `docs/`, `README.md` and `CLAUDE.md` say nothing. Task 8 asks the maintainer to state it and writes the answer down; the plan assumes "no" until told otherwise.
- Whether `sops updatekeys` over all 72 files completes cleanly on this machine — it needs the GPG agent to decrypt 72 times and may prompt for the passphrase. Verified on 6 representative files (`.sops.env`, `.env`, `.sops.yaml`, `.sops.json`, `.sops.crt`, `.sops.conf`), all of which re-keyed and then decrypted with an age-only key. The remaining 66 are the same formats.
- `stakater/reloader` chart `2.2.14` (app `v1.4.19`) is the newest published version as of 2026-08-03 (`helm search repo stakater/reloader --versions`). Its behaviour under `autoReloadAll: true` on this cluster is *not* verified — Task 11 Step 8 verifies it against a controlled, throwaway test before anything relies on it.

---

# PR 1 — Disarm the footgun (config + docs only, no ciphertext touched)

**Blast radius: none on the cluster.** Flux's `kustomize-controller` never reads `.sops.yaml`; it decrypts using the metadata baked into each encrypted file. Nothing in this PR changes any encrypted file, any manifest, or any Secret. If it is wrong, `git revert` fully undoes it.

---

### Task 1: Delete the field-level `.*\.yaml$` rule and the stale cluster config

**Files:**
- Modify: `.sops.yaml` (delete lines 18–21)
- Delete: `clusters/feather-core/.sops.yaml`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: a repo where `sops -e` on a plain `.yaml` fails closed, and exactly one `.sops.yaml` governs the tree — the precondition for `scripts/rekey.sh` in Task 2 and for the single recipient list in PR 2.

**Why:** with the current config, `sops --config .sops.yaml -e some-secret.yaml` exits 0 and emits every `stringData` value in cleartext under a full `sops:` block with a valid MAC and PGP recipient. Reproduced on 2026-08-03. `scripts/validate.sh` cannot catch it (`-skip Secret`), CI cannot catch it, and review sees a file that reads as encrypted. That is the exact mechanism that published the step-ca keys.

- [ ] **Step 1: Create the PR 1 branch from `main`**

```bash
git checkout main
git pull origin main
git checkout -b fix/sops-remove-field-level-yaml-rule
```

- [ ] **Step 2: Capture the "before" behaviour so the fix is provable**

Run all of this from the repo root — no `cd` anywhere, so it survives a shell whose
working directory resets between commands:

```bash
mkdir -p /tmp/sops-probe
cat > /tmp/sops-probe/probe-secret.yaml <<'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: probe
stringData:
  password: hunter2
EOF
sops --config .sops.yaml -e /tmp/sops-probe/probe-secret.yaml | grep -E '^ +password:'
```

Expected **before** the fix (this is the bug):

```
    password: hunter2
```

- [ ] **Step 3: Delete rule 2 from `.sops.yaml`**

Delete lines 18–21 of `.sops.yaml` — the entire second creation rule, including the blank line 17 that precedes it:

```yaml
  - path_regex: .*\.yaml$
    encrypted_regex: ^(ca_password|provisioner_password|intermediate_ca|root_ca|intermediate_ca_key|root_ca_key|\.dockerconfigjson|sql\.php)$
    pgp: >-
      0231831CB40B8E587B7353CBA3AF727721205A62
```

The complete file afterwards is exactly:

```yaml
# PGP recipients for SOPS encryption.
# To grant a new team member access:
#   1. Ask them to run: gpg --full-generate-key
#   2. Get their fingerprint: gpg --list-keys --fingerprint <email>
#   3. Import their public key: gpg --import <their-key.pub>
#   4. Add their fingerprint (without spaces) to the pgp field below, comma-separated
#   5. Re-encrypt every encrypted file: ./scripts/rekey.sh
#
# Secrets are ALWAYS whole-file encrypted. There is deliberately no rule for
# plain `*.yaml`: a Kubernetes Secret manifest must be named `*.sops.yaml` so
# it matches the rule below and is encrypted in full. `sops -e` on a plain
# `.yaml` fails with "no matching creation rules found" — that is intentional.
#
# Current recipients:
#   - TheMeinerLP: 0231831CB40B8E587B7353CBA3AF727721205A62

creation_rules:
  - path_regex: .*\.(env|sops\.env|sops\.json|sops\.ya?ml|sops\.conf|sops\.crt|sops\.key|dockerconfigjson|s3\.conf)$
    pgp: >-
      0231831CB40B8E587B7353CBA3AF727721205A62
```

(The `- <member>: <FINGERPRINT>` placeholder comment is removed with it — it has never been filled in and PR 2 replaces the recipient list with a real second entry.)

- [ ] **Step 4: Delete the stale cluster-level config**

```bash
git rm clusters/feather-core/.sops.yaml
```

It governs zero files (`find clusters -type f` returns only Flux Kustomization CRs, `gotk-*`, `.sops.pub.asc` and the config itself), its `path_regex` has drifted from the root's (missing `sops.json|sops.conf|sops.crt|sops.key`), and its existence is the entire reason CLAUDE.md carries a permanent "both must stay in sync" obligation. Keep `clusters/feather-core/.sops.pub.asc` — it is the public half and is harmless.

- [ ] **Step 5: Prove the footgun is gone and nothing else changed**

```bash
# a) sops now fails closed on a plain .yaml
sops --config .sops.yaml -e /tmp/sops-probe/probe-secret.yaml; echo "exit=$?"
```

Expected:

```
error loading config: no matching creation rules found
exit=1
```

```bash
# b) *.sops.yaml still whole-file encrypts
cp /tmp/sops-probe/probe-secret.yaml /tmp/sops-probe/probe.sops.yaml
sops --config .sops.yaml -e /tmp/sops-probe/probe.sops.yaml | head -2
```

Expected: both lines are `ENC[AES256_GCM,...]` — including `apiVersion:`.

```bash
# c) both real encrypted yaml files still decrypt
sops -d infrastructure/clusters/feather-core/rook/secrets.sops.yaml | head -2
sops -d infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml | head -2
```

Expected: plain `apiVersion: v1` / `kind: Secret` (rook) and `apiVersion: helm.toolkit.fluxcd.io/v2` / `kind: HelmRelease` (step-certificates). No error.

```bash
# d) no plain .yaml anywhere was relying on field-level encryption
grep -rl 'ENC\[AES256_GCM' --include='*.yaml' --include='*.yml' apps infrastructure clusters helm
```

Expected — exactly these two lines and nothing else:

```
infrastructure/clusters/feather-core/rook/secrets.sops.yaml
infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml
```

If (c) or (d) is wrong, **stop** and `git checkout .sops.yaml clusters/feather-core/.sops.yaml` — something in the tree depends on rule 2 and the plan's premise is wrong.

- [ ] **Step 6: Run full validation**

```bash
./scripts/validate.sh
```

Expected: exits `0`. (No group's `Invalid`/`Errors` count changes — this PR renders no manifests.)

- [ ] **Step 7: Commit**

```bash
rm -rf /tmp/sops-probe
git add .sops.yaml clusters/feather-core/.sops.yaml
git commit -m "fix(sops): drop field-level yaml rule that emitted cleartext secrets"
```

**Rollback:** `git revert` the commit. Nothing on the cluster is affected either way.

---

### Task 2: Add `scripts/rekey.sh` so the re-key file list cannot drift again

**Files:**
- Create: `scripts/rekey.sh` (mode `0755`)

**Interfaces:**
- Consumes: the single `.sops.yaml` from Task 1
- Produces: the one command PR 2, `docs/sops.md` and every future recipient change call. Replaces the four hand-written `find` invocations in `docs/sops.md`, which today cover 50 of 72 files on add-member and 3 of 72 on remove-member.

- [ ] **Step 1: Create the script**

Create `scripts/rekey.sh` with exactly this content — the whole file, nothing to assemble:

```bash
#!/usr/bin/env bash
# Re-encrypt every SOPS-encrypted file in this repo to the recipient set
# currently listed in .sops.yaml. Run from anywhere; it cd's to the repo root.
#
#   ./scripts/rekey.sh          re-key every encrypted file
#   ./scripts/rekey.sh --list   print the file list and exit (used by verification)
#
# Run this after ANY edit to .sops.yaml — adding a recipient, removing one,
# or rotating a key. `sops updatekeys` is a no-op on a file whose recipients
# already match, so re-running is always safe.
#
# The suffix list below mirrors the path_regex in .sops.yaml. If you add a
# suffix there, add it here. `.sops.yaml` itself is excluded: it is config,
# not ciphertext, and `sops updatekeys` on it errors.
set -euo pipefail

cd "$(dirname "$0")/.."

mapfile -t FILES < <(
  find apps infrastructure clusters -type f \
    \( -name '*.sops.env' \
    -o -name '*.sops.yaml' -o -name '*.sops.yml' \
    -o -name '*.sops.json' \
    -o -name '*.sops.conf' \
    -o -name '*.sops.crt' \
    -o -name '*.sops.key' \
    -o -name '*.dockerconfigjson' \
    -o -name '*.s3.conf' \
    -o -name '*.env' \) \
    ! -name '.sops.*' \
    | sort
)

if [[ "${#FILES[@]}" -eq 0 ]]; then
  echo "error: no encrypted files found — wrong directory?" >&2
  exit 1
fi

if [[ "${1:-}" == "--list" ]]; then
  printf '%s\n' "${FILES[@]}"
  exit 0
fi

echo "re-keying ${#FILES[@]} file(s) against $(pwd)/.sops.yaml"
for f in "${FILES[@]}"; do
  printf '  %s\n' "$f"
  sops updatekeys -y "$f"
done
echo
echo "done: ${#FILES[@]} file(s) re-keyed"
echo "now verify every file carries every recipient:"
echo "  ./scripts/rekey.sh --list | xargs grep -L <RECIPIENT>   # must print nothing"
```

Note the shebang is `bash`, not `sh` — `mapfile` is a bash builtin and the
repo's default shell (zsh) does not have it. `scripts/validate.sh` uses the
same shebang.

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/rekey.sh
```

- [ ] **Step 3: Verify the file list is complete and correct**

```bash
./scripts/rekey.sh --list | wc -l
```

Expected: `72`

```bash
./scripts/rekey.sh --list | grep -c '\.sops\.yaml$'
```

Expected: `2` — the two real encrypted manifests. Note the `! -name '.sops.*'`
predicate makes this `2` whether or not Task 1 Step 4 has run: `find -name '*.sops.yaml'`
*does* match a leading dot, so the exclusion is load-bearing. If this returns `3`,
the exclusion was dropped from the script.

```bash
# every listed file is genuinely encrypted
./scripts/rekey.sh --list | xargs grep -L 'ENC\[AES256_GCM'
```

Expected: no output. (`grep -L` prints files *without* the marker; any output is a file that is in the list but not encrypted.)

```bash
# nothing encrypted is missing from the list
comm -13 <(./scripts/rekey.sh --list) \
         <(grep -rl 'ENC\[AES256_GCM' apps infrastructure clusters | sort)
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add scripts/rekey.sh
git commit -m "feat(sops): add scripts/rekey.sh covering all 72 encrypted files"
```

**Rollback:** delete the file. It is not referenced by CI or Flux.

---

### Task 3: Rewrite the wrong half of `docs/sops.md`

**Files:**
- Modify: `docs/sops.md`

**Interfaces:**
- Consumes: `scripts/rekey.sh` from Task 2
- Produces: a document whose procedures are executable and whose content is about this cluster. Task 8 appends the key-custody section to it in PR 2.

Apply the edits **bottom-up** so earlier line numbers stay valid.

- [ ] **Step 1: Repoint the recipients note (line 244), then delete the Azure Key Vault section (lines 123–235)**

`docs/sops.md:244` still points at the file Task 1 Step 4 deleted:

```markdown
> Fingerprints are managed in `.sops.yaml` and `clusters/feather-core/.sops.yaml`.
```

It is the highest line number this task touches, so fix it **first** (bottom-up):

```bash
sed -i '244s|.*|> Fingerprints are managed in `.sops.yaml` at the repo root. There is no per-cluster SOPS config.|' docs/sops.md
sed -n '244p' docs/sops.md
```

Expected: the new sentence, with no mention of `clusters/feather-core`.

Then delete the Azure section: from line 123 (the `---` separator that precedes the section) through line 235 (the blank line after the closing fence at 234). 110 lines of Azure Entra ID / AKS workload-identity setup for a bare-metal Talos cluster; there is no `azure_keyvault` key in `.sops.yaml` and `azure_kv: []` in every encrypted file.

```bash
sed -i '123,235d' docs/sops.md
```

Verify:

```bash
grep -c -iE 'azure|keyvault|aks|entra' docs/sops.md
```

Expected: `0`

```bash
sed -n '119,126p' docs/sops.md
```

Expected: the "Reference in Kustomization" section runs straight into `---` and then `## Current recipients` — no stray separators, no orphaned blank block.

- [ ] **Step 2: Replace the "Reference in Kustomization" section**

Replace lines 112–121, which currently read:

```markdown
## Reference in Kustomization

Reference the SOPS file as a generator in `kustomization.yaml`:

```yaml
generators:
  - credentials.sops.yaml
```

Flux decrypts the file automatically via the SOPS provider configured in the cluster.
```

with:

```markdown
## Reference in a Kustomization overlay

`generators:` is **not** used anywhere in this repo. There are exactly two
patterns:

**1. `secretGenerator` — for `*.env` / `*.sops.env` / cert + key pairs.**
This is what almost every overlay does. The env file's keys become the
Secret's keys.

```yaml
generatorOptions:
  disableNameSuffixHash: true
secretGenerator:
  - name: myapp-secret
    envs:
      - myapp.sops.env
```

Or, for a file that should land under a single key (Outline does this):

```yaml
secretGenerator:
  - name: outline-env
    files:
      - .env=outline.sops.env
  - name: cf-origin-tls
    type: kubernetes.io/tls
    files:
      - tls.crt=cf-origin-tls.sops.crt
      - tls.key=cf-origin-tls.sops.key
```

**2. `resources:` — for a `*.sops.yaml` that is already a complete manifest.**

```yaml
resources:
  - secrets.sops.yaml
```

Flux decrypts either form at apply time using `flux-system/sops-gpg`.

> **`disableNameSuffixHash: true` is set on every overlay in this repo.**
> Generated Secret names are therefore stable, which means **editing a secret
> does not roll the consuming Deployment.** After changing any secret you must
> `kubectl rollout restart` the workloads yourself — see `docs/rotation.md`.
```

- [ ] **Step 3: Replace the "Add a new member" and "Remove a member" sections**

Replace lines 39–72 (both sections, from `## Add a new member (maintainer)` through the closing fence of the remove section) with:

```markdown
## Add a recipient (maintainer)

```bash
# 1. Import their public key (PGP) — or just take their age public key
gpg --import their-public-key.asc

# 2. Add the fingerprint / age recipient to .sops.yaml (repo root).
#    There is exactly ONE .sops.yaml in this repo. Do not create another.

# 3. Re-encrypt EVERY encrypted file to the new recipient set
./scripts/rekey.sh

# 4. Verify: no file may be missing the new recipient
./scripts/rekey.sh --list | xargs grep -L '<NEW_FINGERPRINT_OR_AGE_RECIPIENT>'
#    -> must print nothing. Any file listed here is NOT readable by the new
#       recipient. Most common cause: their public key was not in your keyring
#       when rekey.sh ran, in which case sops silently skips that recipient.

# 5. Commit
git add .sops.yaml $(./scripts/rekey.sh --list)
git commit -m "chore(sops): add <name> as recipient"
```

---

## Remove a recipient

Re-encryption is **mandatory**: the removed key can still decrypt every
version of every file that exists in the public git history. Removing the
recipient only protects data encrypted from that point on — treat every
secret the departing recipient could read as compromised and rotate it
(`docs/rotation.md`).

```bash
# 1. Remove their fingerprint / age recipient from .sops.yaml

# 2. Re-encrypt EVERY encrypted file
./scripts/rekey.sh

# 3. Verify: no file may still carry the removed recipient
./scripts/rekey.sh --list | xargs grep -l '<OLD_FINGERPRINT_OR_AGE_RECIPIENT>'
#    -> must print nothing.

# 4. Commit
git add .sops.yaml $(./scripts/rekey.sh --list)
git commit -m "chore(sops): remove <name> from recipients"

# 5. Rotate every credential they held. See docs/rotation.md.
```
```

- [ ] **Step 4: Verify the document**

```bash
grep -n 'find \. -name' docs/sops.md          # expect: no output
grep -n 'clusters/feather-core/.sops.yaml' docs/sops.md   # expect: no output
grep -n 'generators:' docs/sops.md            # expect: no output
grep -c 'scripts/rekey.sh' docs/sops.md       # expect: >= 6
wc -l docs/sops.md                            # expect: roughly 190-210 (was 269)
```

The second grep only comes back empty because Step 1 fixed line 244 and Step 3
replaced lines 47 and 54. If it prints a line, one of those steps was skipped —
re-run it rather than editing the grep.

- [ ] **Step 5: Commit**

```bash
git add docs/sops.md
git commit -m "docs(sops): remove azure key vault section and fix the rekey procedure"
```

**Rollback:** `git checkout HEAD -- docs/sops.md` (before commit) or `git revert` the
commit (after). Documentation only — no cluster impact either way. If the `sed`
line ranges do not match what is described (because `main` moved under you and
`docs/sops.md` changed), **stop and re-derive the line numbers** with
`grep -n '^## ' docs/sops.md` before editing; a blind `sed -i '123,235d'` on a
shifted file silently deletes the wrong 113 lines.

---

### Task 4: Correct `CLAUDE.md`, guard the escrow key in `.gitignore`, open and merge PR 1

**Files:**
- Modify: `CLAUDE.md:71-74`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Tasks 1–3
- Produces: merged PR 1. PR 2 branches from the resulting `main`.

> **Note for the executor:** `CLAUDE.md` is the instruction file agents read. Its current text describes the mechanism this PR just removed, so leaving it is worse than editing it — but confirm the wording with the maintainer before committing if there is any doubt.

- [ ] **Step 1: Fix the SOPS bullets in `CLAUDE.md`**

Replace lines 71–74, currently:

```markdown
- Recipients are listed in **two** files: `.sops.yaml` (repo root) and `clusters/feather-core/.sops.yaml`. Both must stay in sync.
- Encrypted file suffixes: `*.sops.env`, `*.sops.yaml`, `*.sops.json`, `*.sops.crt`, `*.sops.key`, `*.sops.conf` — **and plain `*.env`** (the root `.sops.yaml` regex encrypts those too). `*.yaml` files use field-level encryption (`encrypted_regex` for keys like `*_password`, `*_ca_key`).
- Secrets reach pods via Kustomize `secretGenerator` (`envs:`/`files:`) or `generators:` in an overlay's `kustomization.yaml`; Flux decrypts at apply time.
- Edit in place: `sops path/to/file.sops.env`. Add/remove a member: update both `.sops.yaml`, then re-encrypt everything with `sops updatekeys` (one per file).
```

with:

```markdown
- Recipients live in **one** file: `.sops.yaml` at the repo root. There is no per-cluster SOPS config.
- Encrypted file suffixes: `*.sops.env`, `*.sops.yaml`, `*.sops.json`, `*.sops.crt`, `*.sops.key`, `*.sops.conf`, `*.dockerconfigjson`, `*.s3.conf` — **and plain `*.env`**. All are encrypted **whole-file**; there is no field-level rule. A Kubernetes Secret manifest must therefore be named `*.sops.yaml`, never `*.yaml` — `sops -e` on a plain `.yaml` deliberately errors with "no matching creation rules found".
- Secrets reach pods via Kustomize `secretGenerator` (`envs:`/`files:`) or by listing a `*.sops.yaml` under `resources:`; Flux decrypts at apply time. `generators:` is not used in this repo.
- Edit in place: `sops path/to/file.sops.env`. Add/remove a recipient: edit `.sops.yaml`, then run `./scripts/rekey.sh` (re-keys all 72 encrypted files) and verify per `docs/sops.md`.
```

- [ ] **Step 2: Make it impossible to commit an escrow key by accident**

Append to `.gitignore`:

```gitignore

# SOPS / age private key material — never commit
*.age
*.age.key
age-key.txt
key.txt
.age/
private-key.asc
*-secret-key.asc
```

- [ ] **Step 3: Validate and commit**

```bash
./scripts/validate.sh
git add CLAUDE.md .gitignore
git commit -m "docs(sops): align claude.md with whole-file encryption and ignore key material"
```

- [ ] **Step 4: Push and open PR 1**

```bash
git fetch origin && git rebase origin/main
git push -u origin fix/sops-remove-field-level-yaml-rule
gh pr create --title "fix(sops): remove the field-level yaml rule that emitted cleartext secrets" --body "$(cat <<'EOF'
## Summary
- Deletes the `.*\.yaml$` creation rule from `.sops.yaml`. With it in place, `sops -e` on a hand-written Secret manifest exits 0 and emits every credential in cleartext under a valid-looking `sops:` block with a real MAC and PGP recipient — the mechanism that published the step-ca keys in 2025. Reproduced on 2026-08-03. Without it, `sops -e` on a plain `.yaml` fails closed.
- Deletes `clusters/feather-core/.sops.yaml`: it governs zero encrypted files, its `path_regex` had drifted from the root's, and its existence created a permanent sync obligation.
- Adds `scripts/rekey.sh`, covering all 72 encrypted files. The `find` commands in `docs/sops.md` covered 50 of 72 on add-member and 3 of 72 on remove-member.
- Removes 110 lines of Azure Key Vault / AKS workload-identity docs from `docs/sops.md` (bare-metal Talos cluster; no `azure_keyvault` anywhere), corrects the `generators:` section to the `secretGenerator`/`resources:` patterns actually used, and rewrites the add/remove-recipient procedures around `scripts/rekey.sh`.

**No encrypted file is modified by this PR. Flux does not read `.sops.yaml`.**

## Test plan
- [x] `sops --config .sops.yaml -e probe.yaml` -> `error loading config: no matching creation rules found`, exit 1
- [x] Both real `*.sops.yaml` files still `sops -d` cleanly
- [x] `grep -rl 'ENC\[AES256_GCM' --include='*.yaml'` returns only those two files
- [x] `./scripts/rekey.sh --list | wc -l` -> 72, all encrypted, none missing
- [x] `./scripts/validate.sh` passes
EOF
)"
```

Merging is a human decision — do not merge automatically.

- [ ] **Step 5: After merge, confirm the cluster is untouched**

```bash
flux reconcile kustomization flux-system --with-source
sleep 60
kubectl -n flux-system get kustomizations -o json \
  | jq -r '.items[] | select(.status.conditions[]? | (.type=="Ready" and .status=="False" and (.message|test("decrypt|sops|data key";"i")))) | .metadata.name'
```

Expected: **no output** (no layer reports a decryption failure). Do not gate on every row being `Ready=True` — this dependency graph normally shows a rotating subset of layers reporting `dependency '<x>' is not ready` at a healthy revision.

**Gate:** if any layer name is printed, revert the merge commit on `main` immediately and investigate. Since this PR changes no ciphertext, a decryption failure here would mean something else changed concurrently.

**Rollback for the whole of PR 1:**

```bash
git checkout main && git pull origin main
git revert --no-edit <merge-commit-sha>      # or -m 1 <sha> for a merge commit
git push origin main
flux reconcile kustomization flux-system --with-source
```

This restores `clusters/feather-core/.sops.yaml`, the `.*\.yaml$` rule, the old
`docs/sops.md` and the old `CLAUDE.md`. No encrypted file, no Secret and no
workload is touched in either direction, so there is nothing to restart.

---

# PR 2 — Escrow a second recipient

> ## ⚠️ THIS PR REWRITES ALL 72 ENCRYPTED FILES IN ONE COMMIT
>
> - A botched re-key surfaces as decryption failures across **every** Flux layer at once.
> - `sops updatekeys` **silently skips** a recipient whose public key is not available locally — you get exit 0 and a file the new recipient cannot read. The verification in Task 7 Step 4 is not optional.
> - It needs the `0231831C…` **private** PGP key in the local keyring (72 decrypt operations; the GPG agent may prompt).
> - Land it on a quiet `main` — a concurrent PR touching any `*.sops.env` produces a ciphertext conflict that cannot be resolved by hand.
> - The generated **private** age key must never be written into the repo, a shell history, or a scratchpad that gets committed.
>
> **No `kubectl rollout restart` is required for this PR, and none should be run.**
> The global constraint "every secret change needs an explicit rollout restart"
> applies to *plaintext* changes. Here the ciphertext is rewritten but every
> decrypted value is byte-identical (Task 7 Step 6 proves it), so Kustomize
> regenerates the same Secret contents under the same stable name, no Secret
> `resourceVersion` changes meaningfully, and nothing needs to pick anything up.
> Restarting workloads here would only add risk.

---

### Task 5: DECISION GATE — choose the escrow medium

**Files:** none (decision only)

**Interfaces:**
- Consumes: merged PR 1
- Produces: the maintainer's answer, which Task 6 executes and Task 8 documents.

The problem being solved: all 72 encrypted files are readable by exactly one key (`0231831CB40B8E587B7353CBA3AF727721205A62`, `cluster0.onelite.feather (flux secrets)`, rsa4096, no expiry). Copies exist in exactly two places — the maintainer's keyring and `flux-system/sops-gpg` (created 2025-09-28). Lose both and every DB password, S3 credential, Cloudflare token, origin TLS key and the step-ca material is permanently unreadable, and the "rebuild from git" story evaporates.

- [ ] **Step 1: Present the options to the maintainer and record the answer**

| Option | What it is | Cost | Recommendation |
|---|---|---|---|
| **A. Offline-escrowed `age` key** | `age-keygen`, public half in `.sops.yaml`, private half printed on paper / written to a USB stick and stored **physically off-site** (not in the same building as the cluster or the laptop) | ~20 min | ✅ **Recommended.** Smallest key material (one line), no GPG agent, no expiry semantics, works with `SOPS_AGE_KEY_FILE`. Mirrors what `talos-cluster` already does. |
| **B. Second `age` key held by a second person** | Same mechanism, private half on another human's machine | ~20 min + a second person | Strictly better than A *if* a second trusted maintainer exists. Ask. |
| **C. `age` key in a password manager** | Private half pasted into 1Password/Bitwarden/etc. | ~10 min | Acceptable, but it makes the password manager a new single point of failure and it is typically synced to the same laptop. Combine with A, don't substitute. |
| **D. Second PGP key** | `gpg --full-generate-key`, export the secret key | ~30 min | Works, but the private key is ~7 KB of armored text — far harder to escrow on paper, and it drags GPG agent semantics into the recovery path. Not recommended. |
| **E. Do nothing** | — | 0 | Only defensible if an offline copy of the PGP key already exists somewhere and Task 8 can name it. Ask first — the answer is currently unknown. |

**Recommendation: A, plus B if a second maintainer exists, plus C as a convenience copy.** A and C can share the same key — one keypair, two storage locations.

**Do not proceed to Task 6 until the maintainer has chosen.** Record the choice in the PR description.

---

### Task 6: Generate the escrow key and store the private half offline

**Files:** none committed. The private key must not touch the repo.

**Interfaces:**
- Consumes: Gate A's answer
- Produces: an `age` public recipient string (`age1…`) that Task 7 puts into `.sops.yaml`, and a private half stored per Gate A.

> **This task requires handling a private key. Do it interactively, at a terminal you control. Do not run it in a shared session, do not paste the private key into a chat, a commit, or a file inside the repo.**

- [ ] **Step 1: Generate the keypair outside the repo**

```bash
umask 077
age-keygen -o ~/feather-core-sops-escrow.age
```

Expected output on stderr:

```
Public key: age1................................................
```

The file contains a `# created:` comment, a `# public key:` comment, and one `AGE-SECRET-KEY-1…` line.

- [ ] **Step 2: Record the public half**

```bash
grep 'public key:' ~/feather-core-sops-escrow.age | awk '{print $4}'
```

Keep this string; Task 7 needs it. It is **public** — it is safe to commit and safe to paste.

- [ ] **Step 3: Move the private half to its escrow location(s)**

Per Gate A. Concretely, for option A:

```bash
cat ~/feather-core-sops-escrow.age
```

Transcribe or print the `AGE-SECRET-KEY-1…` line (it is a single line of uppercase Bech32 — transcribable by hand if necessary), seal it, and store it off-site. Note the date and the public key alongside it so a future recovery can confirm it is the right key.

- [ ] **Step 4: Confirm the escrowed copy actually works before deleting anything**

Reconstruct a key file *from the escrowed copy* (retype it, or read it back off the USB stick — do not just copy the original file) and test it:

```bash
printf 'AGE-SECRET-KEY-1...\n' > /tmp/escrow-test.age && chmod 600 /tmp/escrow-test.age
age-keygen -y /tmp/escrow-test.age
```

Expected: prints the **same** `age1…` public key as Step 2. If it does not, the escrowed copy is wrong — redo Step 3.

- [ ] **Step 5: Keep the working copy for now**

Leave `~/feather-core-sops-escrow.age` in place until Task 7 Step 5 has used it to prove an age-only decrypt. Delete `/tmp/escrow-test.age` now:

```bash
shred -u /tmp/escrow-test.age 2>/dev/null || rm -f /tmp/escrow-test.age
```

**Rollback:** nothing is committed and nothing on the cluster changes in this task.
To abandon it, destroy every copy of the keypair
(`shred -u ~/feather-core-sops-escrow.age /tmp/escrow-test.age`, plus the escrowed
copy) and start over. Do **not** carry a half-escrowed key into Task 7: a
recipient whose private half you cannot reproduce is worse than no second
recipient, because it looks like an escrow in `docs/sops.md`.

---

### Task 7: Add the age recipient and re-key all 72 files

**Files:**
- Modify: `.sops.yaml`
- Modify: all 72 encrypted files (ciphertext only — no plaintext value changes)

**Interfaces:**
- Consumes: the `age1…` public key from Task 6; `scripts/rekey.sh` from Task 2
- Produces: 72 files decryptable by either the PGP key or the escrow age key. Flux keeps using PGP and is unaffected.

- [ ] **Step 1: Branch from a freshly-pulled `main`**

```bash
git checkout main
git pull origin main
git status --porcelain    # must be empty
git checkout -b feat/sops-escrow-age-recipient
```

- [ ] **Step 2: Add the `age:` recipient to `.sops.yaml`**

The rule becomes (substitute the real `age1…` value from Task 6 Step 2):

```yaml
creation_rules:
  - path_regex: .*\.(env|sops\.env|sops\.json|sops\.ya?ml|sops\.conf|sops\.crt|sops\.key|dockerconfigjson|s3\.conf)$
    pgp: >-
      0231831CB40B8E587B7353CBA3AF727721205A62
    age: >-
      age1................................................
```

And update the recipient comment block at the top:

```yaml
# Current recipients:
#   - TheMeinerLP (PGP):   0231831CB40B8E587B7353CBA3AF727721205A62
#   - break-glass (age):   age1....  -- private half escrowed offline,
#                          see "Key custody" in docs/sops.md
```

`pgp:` and `age:` in the same rule are ORed — either key alone can decrypt. Verified with sops 3.13.3.

- [ ] **Step 3: Re-key everything**

```bash
./scripts/rekey.sh
```

Expected: for each of the 72 files, `sops` prints (on stderr) exactly this shape —
verified against sops 3.13.3 on 2026-08-03 by adding a throwaway age recipient to a
copy of `harbor.env`:

```
2026/08/03 15:56:54 Syncing keys for file /…/harbor.env
The following changes will be made to the file's groups:
Group 1
    0231831CB40B8E587B7353CBA3AF727721205A62
+++ age1……
2026/08/03 15:56:54 File /…/harbor.env synced with new keys
```

then `done: 72 file(s) re-keyed` from the script itself. A file that prints
`already up to date` instead means it did **not** get the new recipient — treat
that as a failure and check the `age:` value in `.sops.yaml`. This may take a few
minutes and the GPG agent may prompt.

If it aborts partway (`set -e`), **do not commit.** Fix the cause and re-run — `updatekeys` is idempotent, so a re-run picks up where it stopped.

- [ ] **Step 4: Verify recipient coverage — the step that catches the silent failure**

```bash
AGE=age1................................................   # your recipient
FP=0231831CB40B8E587B7353CBA3AF727721205A62

./scripts/rekey.sh --list | xargs grep -L "$AGE"   # must print NOTHING
./scripts/rekey.sh --list | xargs grep -L "$FP"    # must print NOTHING
```

Both must print nothing. `grep -L` lists files *lacking* the string; any output is a file the corresponding key cannot open. Also confirm the count is right:

```bash
git diff --stat -- $(./scripts/rekey.sh --list) | tail -1
```

Expected: `72 files changed, ...`

**If either grep prints anything, stop.** `git checkout -- .` and re-run Task 7 Step 3 after fixing the cause (usually: the age recipient string has a typo, or the run was interrupted).

- [ ] **Step 5: Prove the escrow key alone can decrypt — the whole point of this PR**

```bash
# Pick the highest-value file in the repo and open it with ONLY the age key.
GNUPGHOME=/nonexistent SOPS_AGE_KEY_FILE=~/feather-core-sops-escrow.age \
  sops -d infrastructure/clusters/feather-core/base-controllers/step-certificates/release.sops.yaml \
  | head -2
```

Expected: `apiVersion: helm.toolkit.fluxcd.io/v2` / `kind: HelmRelease` in plaintext. `GNUPGHOME=/nonexistent` guarantees the PGP key is not what decrypted it.

Repeat on one file of each format to cover the whole tree:

```bash
for f in \
  apps/clusters/feathre-core/base-apps/harbor/harbor.env \
  apps/clusters/feathre-core/apps/otis/harbor.onelitefeather.dev.dockerconfigjson.sops.json \
  apps/clusters/feathre-core/base-apps/bluemap/cf-origin-tls.sops.crt \
  apps/clusters/feathre-core/monitoring/loki/loki-ingress-auth.sops.conf \
  infrastructure/clusters/feather-core/configs/postgresql/roles/grafana.sops.env ; do
  GNUPGHOME=/nonexistent SOPS_AGE_KEY_FILE=~/feather-core-sops-escrow.age \
    sops -d "$f" > /dev/null 2>&1 && echo "OK   $f" || echo "FAIL $f"
done
```

Expected: five `OK` lines. Any `FAIL` blocks the PR.

- [ ] **Step 6: Confirm no plaintext changed**

```bash
git stash
for f in $(./scripts/rekey.sh --list); do sops -d "$f" | sha256sum; done > /tmp/before.txt
git stash pop
for f in $(./scripts/rekey.sh --list); do sops -d "$f" | sha256sum; done > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt && echo "PLAINTEXT UNCHANGED"
rm -f /tmp/before.txt /tmp/after.txt
```

Expected: `PLAINTEXT UNCHANGED`. This proves the re-key changed only who can read the files, not what they say. (Values are never printed — only their hashes.)

- [ ] **Step 7: Validate and commit**

```bash
./scripts/validate.sh
git add .sops.yaml $(./scripts/rekey.sh --list)
git commit -m "feat(sops): add offline-escrowed age recipient and rekey all 72 files"
```

- [ ] **Step 8: Delete the working copy of the private key**

Only now, after Step 5 proved the escrowed copy works:

```bash
shred -u ~/feather-core-sops-escrow.age 2>/dev/null || rm -f ~/feather-core-sops-escrow.age
```

Keep it if Gate A chose option C (password manager) and it has already been stored there.

**Rollback for Task 7 (nothing is merged yet — the branch is local):**

```bash
git checkout -- .          # discard the re-key and the .sops.yaml edit
git checkout main
git branch -D feat/sops-escrow-age-recipient
```

Do this **before** Step 8 deletes the working key file, otherwise you cannot
re-run Step 5's proof without retyping the escrowed copy. Nothing has reached
`main` at this point, so the cluster is unaffected. Once PR 2 is merged, the
rollback is Task 8 Step 5's revert.

> ⚠️ **Do not `git checkout -- .` after a partial `rekey.sh` run and then commit
> anyway.** A tree where some files carry the age recipient and some do not is
> the exact silent failure this PR exists to prevent; Step 4's two `grep -L`
> checks are what catch it.

---

### Task 8: Document key custody, then merge PR 2

**Files:**
- Modify: `docs/sops.md` (append a new section after `## Current recipients`)

**Interfaces:**
- Consumes: Task 7's committed re-key
- Produces: merged PR 2 and the one section a new maintainer most needs during a disaster.

- [ ] **Step 1: Update the recipients table in `docs/sops.md`**

```markdown
## Current recipients

| Name          | Type | Recipient                                   | Where the private half lives |
|---------------|------|---------------------------------------------|------------------------------|
| TheMeinerLP   | PGP  | `0231831CB40B8E587B7353CBA3AF727721205A62`  | Maintainer's GPG keyring; second copy in-cluster as `flux-system/sops-gpg` |
| break-glass   | age  | `age1....`                                   | **<FILL IN: exact escrow location and medium>** |

> Recipients are managed in `.sops.yaml` at the repo root. There is no second config file.
```

The `<FILL IN>` is not optional — an escrow nobody can find is not an escrow. Ask the maintainer for the literal wording ("sealed envelope, <location>, labelled …").

- [ ] **Step 2: Add the key-custody / disaster-recovery section**

Append immediately after the recipients table:

```markdown
---

## Key custody and disaster recovery

### How Flux decrypts

11 of the 12 layer `Kustomization` CRs in `clusters/feather-core/*.yaml` carry:

```yaml
  decryption:
    provider: sops
    secretRef:
      name: sops-gpg
```

The two that do **not** are `internal-certs.yaml` (no encrypted input) and the
root `flux-system` Kustomization in `flux-system/gotk-sync.yaml`. Confirm with
`grep -L 'provider: sops' clusters/feather-core/*.yaml` → only
`clusters/feather-core/internal-certs.yaml`.

`flux-system/sops-gpg` holds the armored **private** PGP key under the key
`sops.asc`. Flux reads nothing else — it does **not** read `.sops.yaml`;
recipients are baked into each encrypted file's own `sops:` metadata block.

### Re-creating `sops-gpg` on a fresh cluster

This is the step that makes "rebuild from git" work. Run it **before** the
first Flux reconcile of any layer:

```bash
gpg --export-secret-keys --armor 0231831CB40B8E587B7353CBA3AF727721205A62 \
  | kubectl create secret generic sops-gpg -n flux-system --from-file=sops.asc=/dev/stdin
```

Verify:

```bash
kubectl -n flux-system get secret sops-gpg \
  -o jsonpath='{.data.sops\.asc}' | base64 -d | head -1
# -> -----BEGIN PGP PRIVATE KEY BLOCK-----
```

### If the PGP key is gone

The break-glass `age` key in the recipients table above can decrypt every
encrypted file in this repo. Recovery path:

```bash
# 1. Retrieve the escrowed private key, write it to a file
printf 'AGE-SECRET-KEY-1...\n' > /tmp/escrow.age && chmod 600 /tmp/escrow.age
age-keygen -y /tmp/escrow.age    # sanity check: must print the age1... in the table

# 2. Confirm it opens the repo
SOPS_AGE_KEY_FILE=/tmp/escrow.age sops -d \
  infrastructure/clusters/feather-core/rook/secrets.sops.yaml | head -2

# 3. Generate a NEW cluster PGP key, add it to .sops.yaml, re-key, bootstrap it
gpg --full-generate-key                       # rsa4096, no expiry
# add the new fingerprint to .sops.yaml, then:
SOPS_AGE_KEY_FILE=/tmp/escrow.age ./scripts/rekey.sh
# then run the `sops-gpg` creation command above with the NEW fingerprint

# 4. Remove the old, lost fingerprint from .sops.yaml and ./scripts/rekey.sh again
# 5. Rotate every credential — the old key is unaccounted for. See docs/rotation.md.
```

### Verifying the escrow still works

Do this whenever the recipient list changes, and otherwise once a year:

```bash
GNUPGHOME=/nonexistent SOPS_AGE_KEY_FILE=<escrow key file> \
  sops -d infrastructure/clusters/feather-core/rook/secrets.sops.yaml | head -2
```

`GNUPGHOME=/nonexistent` ensures the PGP key is not what answered.
```

- [ ] **Step 3: Validate and commit**

```bash
./scripts/validate.sh
git add docs/sops.md
git commit -m "docs(sops): document key custody, sops-gpg bootstrap and break-glass recovery"
```

- [ ] **Step 4: Push and open PR 2**

```bash
git fetch origin && git rebase origin/main
git push -u origin feat/sops-escrow-age-recipient
gh pr create --title "feat(sops): add an offline-escrowed age recipient and rekey every file" --body "$(cat <<'EOF'
## Summary
- Adds a break-glass `age` recipient alongside the existing PGP key in `.sops.yaml`. Until now all 72 encrypted files were readable by exactly one key, whose only two copies are the maintainer's keyring and `flux-system/sops-gpg`.
- Re-keys all 72 encrypted files via `./scripts/rekey.sh`. **Ciphertext only — no plaintext value changes** (verified by comparing sha256 of every decrypted file before and after).
- Documents the `sops-gpg` bootstrap command, where the escrow lives, and the break-glass recovery path in `docs/sops.md`.

Flux is unaffected: it decrypts with the PGP key via `flux-system/sops-gpg`, which is unchanged and still a recipient on every file.

## Test plan
- [x] `./scripts/rekey.sh --list | xargs grep -L <age recipient>` -> empty (all 72 carry it)
- [x] `./scripts/rekey.sh --list | xargs grep -L <pgp fingerprint>` -> empty (Flux can still decrypt)
- [x] `GNUPGHOME=/nonexistent SOPS_AGE_KEY_FILE=... sops -d` succeeds on one file of every format
- [x] plaintext sha256 identical for all 72 files before/after
- [x] `./scripts/validate.sh` passes
- [ ] After merge: one reconcile, then no Flux layer reports a decryption error
EOF
)"
```

Merging is a human decision.

- [ ] **Step 5: HEALTH GATE — after merge, confirm every layer still decrypts**

```bash
flux reconcile kustomization flux-system --with-source
sleep 120
kubectl -n flux-system get kustomizations -o json \
  | jq -r '.items[] | select(.status.conditions[]? | (.type=="Ready" and .status=="False" and (.message|test("decrypt|sops|data key|cannot get keys";"i")))) | "\(.metadata.name): \(.status.conditions[]|select(.type=="Ready")|.message)"'
```

Expected: **no output.**

```bash
# And confirm the revision actually advanced past the re-key commit
flux get kustomizations -A | awk '{print $3}' | sort -u
```

Expected: the new `main@sha1:…` short hash appears (Flux picked up the commit).

```bash
# Spot-check that a real Secret still has the right shape on the cluster
kubectl -n dragonfly get secret dragonfly-auth -o jsonpath='{.data.password}' | wc -c
```

Expected: a non-zero byte count (the base64 length of the unchanged password). Do not decode and print it.

**Gate:** if the first command prints any layer, the re-key is broken. **Roll back immediately:**

```bash
git checkout main && git pull
git revert --no-edit <merge-commit-sha>
git push origin main
flux reconcile kustomization flux-system --with-source
```

Reverting restores the previous ciphertext, which the PGP key in `sops-gpg` has always been able to read — the cluster recovers on the next reconcile. Then diagnose offline.

---

# PR 3 — The rotation runbook

**Blast radius: none.** Documentation only.

---

### Task 9: Write `docs/rotation.md`

**Files:**
- Create: `docs/rotation.md`

**Interfaces:**
- Consumes: merged PR 2 (so the doc can reference the final recipient model)
- Produces: the runbook theme 2's Phase 2/3 rotations execute against. **This task is the hard dependency for theme 2.**

The data below is verified as of 2026-08-03 by decrypting every `*.env`/`*.sops.env` locally and comparing `sha256` fingerprints and substrings — values were never printed. Re-derive it with the script in the "Regenerating these maps" section before trusting it in six months.

- [ ] **Step 0: Branch from a freshly-pulled `main`**

Do not skip this — Step 4 pushes `docs/rotation-runbook`, and after PR 2 merged
you are most likely sitting on `main` or on the merged `feat/sops-escrow-age-recipient`.

```bash
git checkout main
git pull origin main
git status --porcelain    # must be empty
git checkout -b docs/rotation-runbook
```

- [ ] **Step 1: Create the file with the mechanics section**

```markdown
# Credential rotation runbook

## Why this document exists

Two properties of this repo make rotation error-prone, and both are deliberate:

1. **Every overlay sets `generatorOptions.disableNameSuffixHash: true`.**
   Generated Secret names are stable, so changing a secret's contents does
   **not** roll the consuming Deployment. The pod keeps the old value in its
   running process until something restarts it — which may be days later,
   with no correlation to your change. Every rotation below ends in an
   explicit `kubectl rollout restart`.
   *(If `stakater/reloader` is deployed, see "Reloader" at the end — it
   removes this step.)*

2. **Several credentials are stored in more than one encrypted file.**
   There is no single source of truth. Miss one copy and the app fails auth
   at its next restart. The maps below are the complete list.

## The general procedure

```bash
# 1. Edit every file that holds the value (see the maps below). The value must
#    be byte-identical in all of them.
sops <file>

# 2. Validate and commit
./scripts/validate.sh
git add <files> && git commit -m "chore(<app>): rotate <credential>"

# 3. Push, then reconcile ONCE
git push origin main
flux reconcile kustomization <layer> --with-source

# 4. Confirm the Secret on the cluster actually changed (compare byte length,
#    never print the value)
kubectl -n <ns> get secret <secret> -o jsonpath='{.data.<key>}' | wc -c

# 5. Restart every consumer (nothing rolls on its own)
kubectl -n <ns> rollout restart deploy/<name>
kubectl -n <ns> rollout status  deploy/<name> --timeout=5m
```

### Finding the consumers of any Secret

```bash
kubectl -n <ns> get deploy,sts -o json | jq -r --arg s '<secret-name>' '
  .items[]
  | select([ (.spec.template.spec.containers[].envFrom[]?.secretRef.name),
             (.spec.template.spec.containers[].env[]?.valueFrom.secretKeyRef.name),
             (.spec.template.spec.volumes[]?.secret.secretName) ] | index($s))
  | "\(.kind)/\(.metadata.name)"' | sort -u
```
```

- [ ] **Step 2: Add the per-credential maps**

```markdown
---

## Dragonfly password — 6 files, 9 keys, 6 namespaces

The single highest-risk rotation in this repo. Two apps embed the password
**inside a connection URI**, so a search for the bare value misses them.

| File | Key(s) | Secret | Namespace |
|---|---|---|---|
| `infrastructure/clusters/feather-core/controllers/dragonfly/dragonfly.env` | `password` | `dragonfly-auth` | `dragonfly` |
| `apps/clusters/feathre-core/base-apps/harbor/harbor.env` | `REDIS_PASSWORD` | `harbor-secret` | `harbor` |
| `apps/clusters/feathre-core/base-apps/n8n/n8n-redis.sops.env` | `redis-password` | `n8n-redis` | `n8n` |
| `apps/clusters/feathre-core/base-apps/outline/outline.sops.env` | `REDIS_URL`, `REDIS_COLLABORATION_URL` — **embedded in URI** | `outline-env` | `outline` |
| `apps/clusters/feathre-core/base-apps/plane/plane.sops.env` | `REDIS_URL`, `CELERY_BROKER_URL` — **embedded in URI** | `plane-app-env`, `plane-doc-store`, `plane-live-env`, `plane-silo`, `plane-rabbitmq`, `plane-opensearch`, `plane-pi-api` | `plane` |
| `apps/clusters/feathre-core/base-apps/shlink/shlink.env` | `REDIS_SERVERS_PASSWORD`, and `REDIS_SERVERS` — **embedded in URI** | `shlink-secret` | `shlink` |

**Ordering matters.** Dragonfly has no "accept both passwords" mode, so there
is an unavoidable window where clients hold the old value. Rotate in one
commit, then restart Dragonfly first and the clients immediately after:

```bash
# after the commit is pushed and reconciled:
kubectl -n dragonfly  rollout restart sts/dragonfly
kubectl -n dragonfly  rollout status  sts/dragonfly --timeout=10m

kubectl -n harbor  rollout restart deploy/harbor-core deploy/harbor-registry deploy/harbor-exporter
kubectl -n n8n     rollout restart deploy/n8n-main deploy/n8n-worker
kubectl -n outline rollout restart deploy/outline-web deploy/outline-collaboration
kubectl -n shlink  rollout restart deploy/shlink
kubectl -n plane   rollout restart deploy/plane-api-wl deploy/plane-worker-wl \
                                   deploy/plane-beat-worker-wl deploy/plane-live-wl \
                                   deploy/plane-silo-wl deploy/plane-space-wl \
                                   deploy/plane-web-wl deploy/plane-admin-wl
```

Consumer lists verified live on 2026-08-03. Re-derive with the jq query above
before executing — Plane in particular generates 7 Secrets from one env file.

> **Open design question:** these six copies should arguably be one Secret.
> Collapsing them needs a cross-namespace Secret replicator (none installed)
> *and* a way to interpolate the password into a URI for Outline/Plane/shlink,
> which `secretKeyRef` cannot do. Not done; tracked as a follow-up.

---

## PostgreSQL role passwords — 2 files each

The CNPG role definition and the app's own copy must move together. The
operator applies its side within a reconcile; the app keeps the old value in
its running process until restarted.

| Role | Infrastructure file (key `password`) | App file | App key |
|---|---|---|---|
| `otis` | `infrastructure/clusters/feather-core/configs/postgresql/roles/otis.sops.env` | `apps/clusters/feathre-core/apps/otis/otis.sops.env` | `DB_PASS` |
| `otis-dev` | `.../roles/otis-dev.sops.env` | `apps/clusters/feathre-core/apps/otis-dev/otis-dev.sops.env` | `DB_PASS` (also `DB_USER` is duplicated) |
| `vulpes` | `.../roles/vulpes.sops.env` | `apps/clusters/feathre-core/apps/vulpes-backend/vulpes.sops.env` | `DB_PASS` |
| `vulpes-dev` | `.../roles/vulpes-dev.sops.env` | `apps/clusters/feathre-core/apps/vulpes-backend-dev/vulpes-dev.sops.env` | `DB_PASS` (also `DB_USER`) |
| `grafana` | `.../roles/grafana.sops.env` | `apps/clusters/feathre-core/base-apps/grafana/grafana-postgresql.sops.env` | `password` |
| `dependency-track` | `.../roles/dependency-track.sops.env` | `apps/clusters/feathre-core/base-apps/dependency-track/dtrack.env` | `db-password` (also `db-username`) |
| `harbor` | `.../roles/harbor.sops.env` | `apps/clusters/feathre-core/base-apps/harbor/harbor.env` | `password` |
| `n8n` | `.../roles/n8n.sops.env` | `apps/clusters/feathre-core/base-apps/n8n/n8n-db.sops.env` | `password` |

Roles with **no** duplicated app-side copy (the app reads the CNPG-published
secret directly, or the password is embedded in a URI not yet mapped):
`backstage`, `outline`, `plane`. Verify with the script in "Regenerating these
maps" before rotating those.

---

## MariaDB role passwords — 2 files each

| Role | Infrastructure file (key `password`) | App file | App key |
|---|---|---|---|
| `leantime` | `infrastructure/clusters/feather-core/configs/mariadb-galera/passwords/leantime.sops.env` | `apps/clusters/feathre-core/base-apps/leantime/leantime.sops.env` | `LEAN_DB_PASSWORD` |
| `shlink` | `.../passwords/shlink.sops.env` | `apps/clusters/feathre-core/base-apps/shlink/shlink.env` | `DB_PASSWORD` |
| `uptime-kuma` | `.../passwords/uptime-kuma.sops.env` | `apps/clusters/feathre-core/base-apps/uptime-kuma/uptime-kuma.sops.env` | `mariadb-password` |

> ⚠️ **`prism` and `reposilite` currently share one password.**
> `.../passwords/prism.sops.env` and `.../passwords/reposilite.sops.env` hold
> the identical value for two distinct DB roles. Rotating one without the
> other is correct and desirable — do them separately and give each its own
> new value. This is not fixed by design; it happened by copy-paste.

The remaining MariaDB roles (`cloudnet`, `coreprotect`, `discordbot`,
`luckperms`, `monitoring`, `playerkits`, `plotsquared`, `stardust`) are
consumed by game servers outside this cluster — rotating them requires
coordinating with those servers' configs, which are not in this repo.

---

## Non-credential duplicates (informational)

These are duplicated but are not secrets; no rotation needed. Listed so a
future fingerprint scan does not re-flag them:

- S3 endpoint URL, identical in `leantime.sops.env` (`LEAN_S3_END_POINT`),
  `outline.sops.env` (`AWS_S3_UPLOAD_BUCKET_URL`), `plane.sops.env`
  (`AWS_S3_ENDPOINT_URL`).
- `AWS_REGION`, identical in `outline.sops.env` and `plane.sops.env`.

---

## Regenerating these maps

Run this from the repo root whenever you suspect the maps have drifted. It
prints only sha256 prefixes — never a secret value.

```bash
python3 - <<'PY'
import subprocess, hashlib, collections
files = subprocess.run(
    ["bash","-c","find apps infrastructure -type f \\( -name '*.sops.env' -o -name '*.env' \\)"],
    capture_output=True, text=True).stdout.split()
idx = collections.defaultdict(list)
for f in sorted(files):
    o = subprocess.run(["sops","-d",f], capture_output=True, text=True)
    if o.returncode: continue
    for line in o.stdout.splitlines():
        if "=" not in line or line.startswith("#"): continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if len(v) < 8: continue
        idx[hashlib.sha256(v.encode()).hexdigest()[:12]].append((f, k))
for h, occ in sorted(idx.items(), key=lambda x: -len(x[1])):
    if len({f for f, _ in occ}) < 2: continue
    print(f"\n[{h}] {len(occ)} occurrences in {len({f for f,_ in occ})} files")
    for f, k in occ: print(f"    {k:34s} {f}")
PY
```

**This only finds exact-value duplicates.** Passwords embedded inside a URI
(Outline, Plane, shlink) will not show up. To find those, take the known
password and substring-search the decrypted files — that is how the Dragonfly
map above was completed after the exact-value scan missed two apps.

---

## Reloader

If `stakater/reloader` is deployed (namespace `reloader`), the
`kubectl rollout restart` steps above become unnecessary: reloader watches
Secret contents and restarts the referencing workloads itself. Confirm it is
running before relying on it:

```bash
kubectl -n reloader get deploy reloader-reloader
```

If that returns `NotFound`, every rotation still needs its manual restarts.
```

- [ ] **Step 3: Fix the three dangling references to a non-existent doc**

`docs/dragonfly-redis-cutover.md` does not exist (`ls docs/` → `buckets.md`, `dragonfly-redis-allocations.md`, `incidents`, `sops.md`, `superpowers`). Three manifests point at it. Update the comment in each — content and behaviour are unchanged:

- `apps/clusters/feathre-core/base-apps/n8n/release.yaml:44` — change `See docs/dragonfly-redis-cutover.md.` to `See docs/dragonfly-redis-allocations.md (DB map) and docs/rotation.md (password rotation).`
- `apps/clusters/feathre-core/base-apps/shlink/release.yaml:122` — same replacement.
- `apps/clusters/feathre-core/base-apps/harbor/release.yaml:1057` — change `see docs/dragonfly-redis-cutover.md.` to `see docs/dragonfly-redis-allocations.md.`

The three `docs/superpowers/` specs that also reference it are historical records — leave them alone.

Verify:

```bash
grep -rn 'dragonfly-redis-cutover' --include='*.yaml' apps infrastructure
```

Expected: no output.

- [ ] **Step 4: Validate, commit, push, open PR 3**

```bash
./scripts/validate.sh
git add docs/rotation.md \
        apps/clusters/feathre-core/base-apps/n8n/release.yaml \
        apps/clusters/feathre-core/base-apps/shlink/release.yaml \
        apps/clusters/feathre-core/base-apps/harbor/release.yaml
git commit -m "docs: add credential rotation runbook and fix dangling doc references"
git fetch origin && git rebase origin/main
git push -u origin docs/rotation-runbook
gh pr create --title "docs: add the credential rotation runbook" --body "$(cat <<'EOF'
## Summary
- Adds `docs/rotation.md`: per credential, which encrypted files hold it and which workloads need a manual `kubectl rollout restart` (nothing rolls on its own — every overlay sets `disableNameSuffixHash: true`).
- Corrects the Dragonfly password map: it lives in **6 files / 9 keys**, not 4. Outline and Plane embed it inside a connection URI, so an exact-value scan misses them. A rotation done from the old list would have broken both.
- Flags that the `prism` and `reposilite` MariaDB roles currently share one password.
- Repoints the three manifest comments that reference the non-existent `docs/dragonfly-redis-cutover.md`.

Documentation and comments only — no rendered manifest changes.

## Test plan
- [x] `./scripts/validate.sh` passes
- [x] `grep -rn 'dragonfly-redis-cutover' --include='*.yaml' apps infrastructure` -> empty
- [x] every consumer list re-derived live from the cluster
EOF
)"
```

The three `release.yaml` edits are **comment-only**. `kustomize build` strips YAML
comments, so the rendered manifests are byte-identical and no `HelmRelease` is
upgraded and no pod is restarted. Confirm before merging:

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps > /tmp/after-render.yaml
git stash && kubectl kustomize apps/clusters/feathre-core/base-apps > /tmp/before-render.yaml && git stash pop
diff /tmp/before-render.yaml /tmp/after-render.yaml && echo "RENDER UNCHANGED"
rm -f /tmp/before-render.yaml /tmp/after-render.yaml
```

Expected: `RENDER UNCHANGED`.

**Rollback:** `git revert --no-edit <merge-commit-sha> && git push origin main`.
Documentation and comments only; nothing on the cluster depends on either.

**Gate for theme 2:** do not begin any credential rotation until this PR is merged.

---

# PR 4 — Reloader (optional)

> ## ⚠️ THIS PR CHANGES CLUSTER BEHAVIOUR
>
> After it lands, editing any Secret or ConfigMap **restarts the workloads that
> reference it**. That is the point — but it turns every previously-inert secret
> edit into a rollout. **Land it during a window when you can watch the cluster**,
> not at the end of a session.
>
> **It also adds a resource to a `wait: true` layer near the root of the dependency
> graph.** `clusters/feather-core/base-controllers.yaml` has `wait: true` and
> `timeout: 15m0s`, and `controllers` + `base-configs` depend on it — which means
> `rook`, `rook-fr01`, `configs`, `base-apps`, `apps` and `monitoring` depend on it
> transitively. If the `reloader` HelmRelease never becomes Ready (bad chart
> version, unreachable `HelmRepository`, a values key the chart rejects), the
> `base-controllers` layer stalls and **every downstream layer stops reconciling**
> until it is reverted. Nothing already running breaks, but no further GitOps
> change lands anywhere in the cluster. This is why Task 11 Step 5's local render
> and `./scripts/validate.sh` are mandatory before pushing, and why Step 7 checks
> the whole graph, not just the reloader Deployment.

---

### Task 10: DECISION GATE — deploy reloader, and with what scope?

**Files:** none (decision only)

- [ ] **Step 1: Present the options and record the answer**

| Option | Config | Effect | Recommendation |
|---|---|---|---|
| **A. `autoReloadAll: true`, secrets only** | `reloader.autoReloadAll: true`, `reloader.ignoreConfigMaps: true`, `reloadStrategy: annotations` | Every workload referencing a changed **Secret** restarts. No per-Deployment annotation needed — which matters here, because almost every consuming Deployment is rendered by a third-party Helm chart and cannot be annotated without a values hack or a post-render patch. | ✅ **Recommended.** |
| **B. `autoReloadAll: true`, secrets + configmaps** | as A but `ignoreConfigMaps: false` | Also restarts on ConfigMap changes. Wider blast radius, and this cluster has many operator-managed ConfigMaps. | Only if you want it; start with A. |
| **C. Opt-in annotations only** | `autoReloadAll: false`; annotate each Deployment with `reloader.stakater.com/auto: "true"` | Precise, but the Deployments are chart-rendered — each annotation needs a chart-specific `podAnnotations` value or a Kustomize patch, across harbor/n8n/outline/plane/shlink. High effort, easy to miss one, and missing one silently restores the exact bug. | Not recommended. |
| **D. Don't deploy it** | — | Rotation stays a manual multi-step ritual documented in `docs/rotation.md`. | Acceptable — PR 3 makes rotation *possible*. This PR makes it *safe*. |

Known side effects of A, checked against this cluster: reloader will also
restart workloads when cert-manager renews a TLS secret they mount. Of the 20
`Certificate` resources, 18 live in `envoy` (read by the Gateway, not mounted
by a pod) and 2 are operator webhook certs (`cnpg-system`,
`mariadb-operator`) whose operator restart is harmless. No restart storm is
expected.

**Do not proceed to Task 11 without an explicit answer. If the answer is D, this plan ends here.**

---

### Task 11: Deploy reloader

**Files:**
- Create: `infrastructure/clusters/feather-core/base-sources/stakater.yml`
- Modify: `infrastructure/clusters/feather-core/base-sources/kustomization.yaml`
- Create: `infrastructure/base/controllers/reloader/kustomization.yaml`
- Create: `infrastructure/base/controllers/reloader/namespace.yaml`
- Create: `infrastructure/base/controllers/reloader/release.yaml`
- Modify: `infrastructure/clusters/feather-core/base-controllers/kustomization.yaml`

**Interfaces:**
- Consumes: Gate B's answer
- Produces: a `reloader` Deployment in namespace `reloader` watching Secrets cluster-wide.

Placed in `base-controllers` (not `controllers`) to match `descheduler`, `spegel` and `dragonfly-operator` — cluster-wide controllers with no dependency on `rook` or `configs`.

- [ ] **Step 1: Branch**

```bash
git checkout main && git pull origin main
git checkout -b feat/reloader
```

- [ ] **Step 2: Add the HelmRepository source**

Create `infrastructure/clusters/feather-core/base-sources/stakater.yml`:

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: stakater
  namespace: flux-system
spec:
  interval: 5m
  url: https://stakater.github.io/stakater-charts
```

Register it in `infrastructure/clusters/feather-core/base-sources/kustomization.yaml` by appending to the `resources:` list:

```yaml
  - plane.yml
  - stakater.yml
```

- [ ] **Step 3: Create the base overlay**

`infrastructure/base/controllers/reloader/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: reloader
```

`infrastructure/base/controllers/reloader/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: reloader
resources:
  - namespace.yaml
  - release.yaml
```

`infrastructure/base/controllers/reloader/release.yaml` (option A from Gate B):

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: reloader
  namespace: reloader
spec:
  releaseName: reloader
  chart:
    spec:
      chart: reloader
      version: "2.2.14"
      sourceRef:
        kind: HelmRepository
        name: stakater
        namespace: flux-system
  install:
    remediation:
      retries: 3
  upgrade:
    remediation:
      retries: 3
  interval: 1m0s
  timeout: 5m
  values:
    reloader:
      # Restart every workload that references a changed Secret, without
      # needing a per-Deployment annotation. Almost every consumer here is
      # rendered by a third-party Helm chart and cannot be annotated cleanly.
      autoReloadAll: true
      # Secrets only. ConfigMap churn on this cluster is mostly operator-owned.
      ignoreConfigMaps: true
      ignoreSecrets: false
      watchGlobally: true
      # Patch a pod-template annotation instead of injecting a dummy env var,
      # so Helm/Flux drift correction does not fight the restart mechanism.
      reloadStrategy: annotations
      deployment:
        resources:
          requests:
            cpu: 10m
            memory: 64Mi
          limits:
            memory: 128Mi
```

- [ ] **Step 4: Register it in the layer**

Append to `infrastructure/clusters/feather-core/base-controllers/kustomization.yaml`'s `resources:` list:

```yaml
  - ../../../../infrastructure/base/controllers/dragonfly-operator
  - ../../../../infrastructure/base/controllers/reloader
```

- [ ] **Step 5: Render and validate**

```bash
kubectl kustomize infrastructure/clusters/feather-core/base-controllers | grep -A4 'name: reloader'
kubectl kustomize infrastructure/clusters/feather-core/base-sources | grep -A4 'name: stakater'
./scripts/validate.sh
```

Expected: a `HelmRelease` named `reloader` in namespace `reloader` with `version: 2.2.14`; a `HelmRepository` named `stakater` pointing at `https://stakater.github.io/stakater-charts`; `validate.sh` exits `0`.

- [ ] **Step 6: Commit, push, open PR 4**

```bash
git add infrastructure/clusters/feather-core/base-sources/stakater.yml \
        infrastructure/clusters/feather-core/base-sources/kustomization.yaml \
        infrastructure/base/controllers/reloader \
        infrastructure/clusters/feather-core/base-controllers/kustomization.yaml
git commit -m "feat(reloader): deploy stakater reloader to restart workloads on secret change"
git fetch origin && git rebase origin/main
git push -u origin feat/reloader
gh pr create --title "feat(reloader): restart workloads automatically on secret change" --body "$(cat <<'EOF'
## Summary
- Adds the `stakater` HelmRepository and a `reloader` HelmRelease (chart 2.2.14) in `base-controllers`.
- Configured `autoReloadAll: true` / `ignoreConfigMaps: true` / `reloadStrategy: annotations` — no per-Deployment annotation is needed, which matters because almost every Secret-consuming Deployment here is rendered by a third-party Helm chart.
- Removes the whole class of silent-stale-secret incidents caused by `disableNameSuffixHash: true` (Secret contents change, name does not, nothing rolls).

⚠️ **Behaviour change:** after this merges, editing any Secret restarts the workloads that reference it. Merge when someone can watch the cluster.

## Test plan
- [x] `./scripts/validate.sh` passes
- [ ] After merge: reloader Deployment Ready, then a controlled no-op secret edit restarts exactly the expected workloads
EOF
)"
```

- [ ] **Step 7: After merge, reconcile once and verify it is running**

```bash
flux reconcile kustomization base-controllers --with-source
sleep 90
kubectl -n reloader get deploy
kubectl -n reloader logs deploy/reloader-reloader --tail=20
```

Expected: one Deployment named `reloader-reloader`, `1/1` Ready (the chart's
fullname is `<release>-<chart>`); logs show it starting and watching (no
`forbidden` / RBAC errors).

Then confirm the layer itself went Ready and did not stall its dependents:

```bash
kubectl -n flux-system get kustomization base-controllers \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{" "}{.status.conditions[?(@.type=="Ready")].message}{"\n"}'
kubectl -n flux-system get helmrelease reloader -n reloader \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{" "}{.status.conditions[?(@.type=="Ready")].message}{"\n"}' 2>/dev/null \
  || kubectl -n reloader get helmrelease reloader -o wide
```

Expected: `True Applied revision: main@sha1:<the merge commit>` for the layer, and
a Ready `HelmRelease`.

**If `base-controllers` is still `False` after ~15 minutes** (its `timeout` is
`15m0s`), do not wait it out — every downstream layer is blocked. Revert now
using the command in the Step 8 gate below, then diagnose offline with
`kubectl -n reloader describe helmrelease reloader`.

- [ ] **Step 8: Prove it works with a controlled, harmless test**

Do **not** test by rotating a real credential. Use a throwaway Secret and Deployment:

```bash
kubectl create ns reloader-probe
kubectl -n reloader-probe create secret generic probe --from-literal=v=1
kubectl -n reloader-probe create deployment probe --image=registry.k8s.io/pause:3.9
kubectl -n reloader-probe set env deploy/probe --from=secret/probe

kubectl -n reloader-probe get deploy probe -o jsonpath='{.metadata.generation}{"\n"}'   # note this number
kubectl -n reloader-probe create secret generic probe --from-literal=v=2 \
  --dry-run=client -o yaml | kubectl apply -f -
sleep 30
kubectl -n reloader-probe get deploy probe -o jsonpath='{.metadata.generation}{"\n"}'
```

Expected: the generation **increased** — reloader patched the pod template. Confirm the mechanism:

```bash
kubectl -n reloader-probe get deploy probe -o jsonpath='{.spec.template.metadata.annotations}' | tr ',' '\n' | grep -i reloader
```

Expected: a `reloader.stakater.com/last-reloaded-from` annotation.

Clean up:

```bash
kubectl delete ns reloader-probe
```

**Gate:** if the generation did not change, reloader is not doing its job — `docs/rotation.md`'s manual restart steps remain mandatory. Either fix the config or revert:

```bash
git revert --no-edit <merge-commit-sha> && git push origin main
flux reconcile kustomization base-controllers --with-source
```

Reverting removes the reloader Deployment and namespace; no workload state depends on it.

- [ ] **Step 9: Update `docs/rotation.md` to reflect that reloader is live**

Only do this after Step 8 passed. This is a **separate follow-up PR** — the
`feat/reloader` branch is already merged, so committing onto it goes nowhere.

In the "Reloader" section, change the conditional wording to state that
reloader **is** deployed as of `<merge date>`, and mark the per-credential
`kubectl rollout restart` blocks as "verification only — reloader performs the
restart; use these to confirm or to force it".

```bash
git checkout main && git pull origin main
git checkout -b docs/reloader-is-live
# edit docs/rotation.md
./scripts/validate.sh
git add docs/rotation.md
git commit -m "docs: note that reloader is deployed and handles restarts"
git fetch origin && git rebase origin/main
git push -u origin docs/reloader-is-live
gh pr create --title "docs: note that reloader is deployed and handles restarts" \
  --body "Follow-up to the reloader rollout: docs/rotation.md's manual restart steps are now verification-only."
```

Verify: `grep -n 'If \`stakater/reloader\` is deployed' docs/rotation.md` → no output
(the conditional phrasing is gone).

**Rollback:** `git revert` the commit. Documentation only.

---

## Final state

After all four PRs:

- `sops -e` on a plain `.yaml` fails closed. There is one `.sops.yaml`, and it whole-file encrypts.
- All 72 encrypted files are readable by the cluster PGP key **and** an offline-escrowed age key whose location is written down.
- `./scripts/rekey.sh` re-keys all 72 in one command; the file list cannot drift from `.sops.yaml` without someone editing the script.
- `docs/sops.md` describes this cluster, documents the `sops-gpg` bootstrap, and has a break-glass recovery path.
- `docs/rotation.md` maps every duplicated credential to its files, its Secrets and its consumers — including the two the audit missed.
- Secret edits are no longer silently inert.
