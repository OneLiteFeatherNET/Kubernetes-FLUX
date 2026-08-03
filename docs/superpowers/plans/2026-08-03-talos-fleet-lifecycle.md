# Talos Fleet Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 10-node `feather-core` Talos fleet reproducible and upgradable: commit the Image Factory schematic source and a CI assertion for it, pin the two unpinned control-plane `extraManifests` URLs, roll every node from Talos v1.13.4 to v1.13.7 and Kubernetes v1.36.1 to v1.36.2, replace stock kubelet reservations with a role-specific reservation + eviction tier, and clean up the stale render artifacts and lifecycle gaps in `talos.sh`.

**Architecture:** Five separately-merged PRs, ordered by blast radius. PR 1 touches no node at all (repo/CI/tooling only). PR 2 changes only `cluster.extraManifests` on the three control planes via `apply-config --mode=no-reboot`. PR 3 is the fleet reboot: installer pin bump to v1.13.7, drop the deprecated `machine.install.extensions` block, roll control planes → storage → workers one node at a time, then `upgrade-k8s --to v1.36.2`. PR 4 adds the kubelet reservation/eviction tier and applies it node by node with no reboot. PR 5 codifies the commands that were actually run in PR 3/4 into `talos.sh` wrappers — deliberately last, so the wrappers encode proven behaviour instead of guesses.

**Tech Stack:** Talos Linux v1.13.x, `talosctl` v1.13.7, Image Factory (`factory.talos.dev`), SecureBoot installer image, layered-YAML machine configs rendered by `./talos.sh`, GitHub Actions (`.github/workflows/talos.yml`), Kubernetes v1.36.x, Rook/Ceph, CloudNativePG.

---

## ⚠️ WHICH REPO EACH CHANGE GOES INTO

**Almost every file edit in this plan is in a DIFFERENT repository from the one this plan file lives in.**

| Repo | Path | What it holds | Which tasks |
|---|---|---|---|
| **Talos repo** | `/mnt/projects/lab/talos-cluster` (remote `TheMeinerLP/FeatherCore`) | `talos.sh`, `Makefile`, `README.md`, `.github/workflows/talos.yml`, all machine-config layers under `clusters/feather-core/talos/` | **Tasks 1–15 — all file edits** |
| **GitOps repo** | this repo (`onelitefeather/Kubernetes-FLUX`) | Flux manifests | **only this plan document**; and Task 14, which is *gated on* a change another theme makes here |

Every `git`/`./talos.sh`/`talosctl` command in this plan is run from `/mnt/projects/lab/talos-cluster` unless stated otherwise. `./scripts/validate.sh` does **not** exist in the Talos repo — its equivalent is `./talos.sh build`.

---

## Corrections to the audit — read before executing

The audit's recommendations contain three statements that were re-verified against the live cluster and the installed tooling and are **wrong**. Do not follow them.

1. **`talosctl upgrade --preserve` does not exist.** `talosctl v1.13.7 upgrade --help` has no `--preserve` flag; the flag was retired for Talos 1.8+ (`siderolabs/talos@683153a33 docs: remove the last mentions of preserve flag for Talos 1.8+`) and survives only inside the deprecated `--legacy` path. Modern `talosctl upgrade` preserves `EPHEMERAL` by default. Passing `--preserve` will make the command fail with an unknown-flag error, not silently destroy etcd. **Every upgrade command in this plan omits it.**
2. **The schematic is not lost and does not need regenerating.** `curl https://factory.talos.dev/schematics/2c97492b…` returns the source YAML today, and re-POSTing that YAML returns the identical ID (verified 2026-08-03). Image Factory paths are `<schematic-id>:<talos-version>`, so bumping to v1.13.7 is a **tag-only edit** — the schematic ID stays byte-identical and the extension set cannot silently change. Committing `schematic.yaml` is about documentation and a CI assertion, not about recovering something at risk.
3. **The OOM killer cannot pick the kubelet.** Talos runs kubelet with an explicit protective `oom_score_adj`. The real exposure is Ceph OSD pods, which are `Burstable`. The reservation work in PR 4 is still correct; the justification is narrower than the audit states.

Additionally: the audit's "CI can validate a config the nodes would reject" claim is false — `v1.13.3` and `v1.13.4` share the same `v1alpha1` schema. Bumping `TALOSCTL_VERSION` (Task 3) is tidiness, not a correctness fix. Do not oversell it in the PR description.

---

## Prerequisites

- [ ] **Console access to all 10 KVM guests** (Proxmox/libvirt console, not SSH/talosctl) before starting PR 3. A SecureBoot UKI that fails to boot is only recoverable from the console.
- [ ] **`talosctl` v1.13.7 client** — `talosctl version --client` → `Tag: v1.13.7`. Verified present at `/usr/bin/talosctl` on 2026-08-03.
- [ ] **The generated talosconfig** at `clusters/feather-core/generated/talosconfig` (endpoints `192.168.15.10,.11,.12`). `~/.talos/config` is **empty** on this workstation (`context: ""`), so **every** `talosctl` command must pass `--talosconfig clusters/feather-core/generated/talosconfig` or export `TALOSCONFIG`. Export it once:
  ```bash
  cd /mnt/projects/lab/talos-cluster
  export TALOSCONFIG=$PWD/clusters/feather-core/generated/talosconfig
  talosctl -n 192.168.15.10 version   # → Server Tag: v1.13.4
  ```
  ⚠️ **`generated/talosconfig` is a build output and `./talos.sh clean` deletes it.** It is regenerated by `./talos.sh gen-talosconfig` (which needs `base/talosconfig` from `gen-base`) — **not** by `./talos.sh build`. Anywhere this plan runs `clean`, the recovery is always `gen-base && gen-talosconfig && build`. Losing it mid-window means you cannot reach any node.
- [ ] **`.age/key.txt` present** — required for `./talos.sh gen-base` / `render-all`. Verified present.
- [ ] **`kubectl cnpg` plugin** — `kubectl cnpg version` → `Version:1.29.0`. Needed in Task 11 to move the Postgres primary off a node before draining it.
- [ ] **The Talos repo working tree is dirty right now** (`clusters/feather-core/talos/secrets/secrets.sops.yaml` modified, `toolbox.yaml` untracked). Resolve or stash this *before* Task 1 — do not sweep an uncommitted secrets change into an unrelated PR:
  ```bash
  cd /mnt/projects/lab/talos-cluster && git status --porcelain && git diff --stat
  ```

## Cross-theme dependencies

| Theme | Relationship |
|---|---|
| `offsite-backups-and-disaster-recovery` | **HARD BLOCKER for PR 3.** There is currently no etcd snapshot anywhere. That theme adds `talos.sh snapshot`. Task 7 will not start until an etcd snapshot exists off-cluster. If that theme has not landed, Task 7 takes a one-off snapshot by hand — but the recurring snapshot is theirs to build, not this plan's. |
| `lan-exposure-and-unmanaged-sniffer` | **Gates Task 14 only.** That theme adopts `kubelet-serving-cert-approver` into the GitOps repo as `infrastructure/base/controllers/kubelet-serving-cert-approver/`. Task 14 (removing it from `extraManifests`) may only run once that Flux copy is `Ready`. PR 2 pins the URL as the interim fix and is independent. That theme also deletes the `kubeshark-debug` DaemonSet, which alone accounts for 10 GiB of the 161 % memory-limit overcommit on the storage nodes — nice to have before PR 4, but not required (PR 4's headroom maths uses *requests*, not limits). |
| `flux-release-control-and-convergence` | No ordering constraint. Mentioned only because metrics-server/cert-approver would eventually land under Renovate's scope there. |

## Global constraints

- The Talos repo uses conventional-commit style **by convention, not CI enforcement** (`CONTRIBUTING.md:55-58`). Match it anyway: `feat:`, `fix:`, `chore:`, `docs:`, lowercase subject.
- `./talos.sh build` (render-all + `talosctl validate` on all 10 configs) must pass before every commit. That is what `.github/workflows/talos.yml` runs.
- `base/` and `generated/` are git-ignored build outputs (`.gitignore`). **Never commit a rendered machineconfig** — they contain `machine.ca.key` and `machine.token` in plaintext.
- **One node at a time. Never two control planes.** Three etcd members tolerate exactly one down.
- Between node operations, wait for the verification command to pass. Do not batch.
- After any `apply-config`/`upgrade`, check `kubectl get nodes` for `SchedulingDisabled` and `kubectl uncordon <node>` if the node came back cordoned.

## Deliberately NOT in scope

- **Rotating the leaked Talos/Kubernetes PKI.** Task 3 shreds four stale plaintext machineconfigs, which removes *copies*. The CA key and machine token in them are still the live cluster's and are still in `secrets.sops.yaml`. Rotation is `crown-jewel-rotation-leaked-pki-and-credentials`. Do not let Task 3 create the impression the leak is handled.
- **Right-sizing Ceph/Rook resource limits.** The 161 %/194 % limit overcommit on `fr01-str-01..03` is a Rook values problem in the GitOps repo and belongs to the workload theme. PR 4 only gives the kubelet room to act; it does not fix the overcommit.
- **Deleting kubeshark.** Belongs to `lan-exposure-and-unmanaged-sniffer`. Do not remove it as a side effect here.
- **Moving `metrics-server` under Flux.** PR 2 pins it and stops there. Migration is a GitOps-repo change with its own review; pinning to the exact version already running is a zero-delta improvement available today.
- **Reservations for the `general`, `small` and `ingress` roles.** No node uses those roles (`nodes/fr01/` has only `controlplane/`, `storage/`, `xl/`). Adding untested reservations to unused role files creates a trap. Task 12 adds a comment to each pointing at the pattern instead.
- **`rotate-ca`, node-removal automation beyond a documented wrapper, and a full `docs/OPERATIONS.md` runbook.** PR 5 adds `apply`/`upgrade`/`remove-node` wrappers and a short ops section; anything larger is separate work.
- **Kubernetes minor upgrades (1.36 → 1.37).** Only the 1.36.1 → 1.36.2 patch that Talos v1.13.5+ defaults to.

## Decision gates

These need a human answer. Do not pick silently.

- **DG-1 (Task 5) — which `kubelet-serving-cert-approver` version to pin.** Live today: image `ghcr.io/alex1989hu/kubelet-serving-cert-approver:main` (a mutable third-party tag). Latest release is `v0.11.0` (2026-05-21), whose manifest pins image `:0.11.0`. **Recommendation: v0.11.0.** Pinning is the whole point; `:main` is unreviewable. Cost: the Deployment rolls (single replica, ~15 s gap in CSR approval — harmless, unapproved CSRs stay Pending and get approved when it returns).
- **DG-2 (Task 5) — which `metrics-server` version to pin.** Live today: `v0.8.1` (whatever `latest` resolved to). Latest release is `v0.9.0` (2026-07-13). **Recommendation: pin `v0.8.1`** — byte-identical to what is running, so the pin is a true no-op and the change is provably safe. Bump to v0.9.0 as a separate, reviewable PR later.
- **DG-3 (Task 7) — maintenance window.** PR 3 reboots all 10 nodes. Expect ~5–10 min per node including drain, so **90–120 minutes wall-clock** if done in one sitting. Options: (a) one window, all 10 — fastest, one context, recommended if the window exists; (b) three windows: control planes, then storage, then workers — lower risk per window, but the fleet sits on mixed versions between them (supported, but drift you must remember). **Recommendation: (a) one window with console access, or (b) split at the storage boundary.** Not a free choice — say which before starting.
- **DG-4 (Task 10) — take the Kubernetes 1.36.2 bump now or defer.** Talos v1.13.5+ defaults Kubernetes to v1.36.2; leaving nodes on 1.36.1 is supported but is drift you re-discover next upgrade. **Recommendation: take it, in the same window, immediately after the last node reports v1.13.7.**
- **DG-5 (Task 11) — how to clear the three blocking PodDisruptionBudgets.** `cnpg-system/feather-core-cluster-pg-primary`, `harbor/harbor-registry` and `n8n/n8n-main` all report `ALLOWED DISRUPTIONS = 0`, so `talosctl upgrade --drain` will stall on whichever node hosts them. Options: (a) delete the pod immediately before draining — `kubectl delete pod` is not an eviction, so the PDB does not apply, and the ReplicaSet reschedules it elsewhere; brief single-replica downtime for Harbor registry and n8n either way; (b) temporarily relax the PDBs — they are Flux-owned, so Flux will fight you mid-window. **Recommendation: (a), plus a proper CNPG switchover (`kubectl cnpg promote`) for Postgres rather than a pod delete.**

---

# PR 1 — Reproducibility and tooling hygiene (no node is touched)

Everything in PR 1 is a repo/CI/tooling change plus one local-filesystem cleanup. Nothing reaches a node. Rollback for the whole PR is `git revert`.

### Task 1: Verify the schematic round-trip and commit `schematic.yaml`

**Repo:** Talos repo. **Files:** Create `clusters/feather-core/talos/schematic.yaml`.

**Interfaces:**
- Consumes: nothing.
- Produces: the committed schematic source that Task 2's `talos.sh schematic` command asserts against.

- [ ] **Step 1: Branch**

```bash
cd /mnt/projects/lab/talos-cluster
git checkout main && git pull origin main
git checkout -b chore/talos-schematic-and-tooling
```

- [ ] **Step 2: Fetch the schematic that the pinned ID actually resolves to**

```bash
curl -fsSL https://factory.talos.dev/schematics/2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896
```

Expected output, exactly:

```yaml
customization:
    systemExtensions:
        officialExtensions:
            - siderolabs/cloudflared
            - siderolabs/qemu-guest-agent
```

If the output differs from this, **stop**. It means the pinned ID encodes something this plan does not know about; investigate before writing any file. (Cross-check: every node carries `extensions.talos.dev/cloudflared=2026.5.2` and `extensions.talos.dev/qemu-guest-agent=11.0.1` and annotation `extensions.talos.dev/schematic: 2c97492b…` — `kubectl get nodes -o json | grep -c 2c97492b` → `10`.)

- [ ] **Step 3: Prove the round-trip before committing it**

```bash
printf 'customization:\n    systemExtensions:\n        officialExtensions:\n            - siderolabs/cloudflared\n            - siderolabs/qemu-guest-agent\n' \
  | curl -sS -X POST --data-binary @- https://factory.talos.dev/schematics
```

Expected output:

```json
{"id":"2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896","schematic":"customization:\n    systemExtensions:\n        officialExtensions:\n            - siderolabs/cloudflared\n            - siderolabs/qemu-guest-agent\n"}
```

The `id` must equal the one pinned in `patches/common/installer-secureboot.yaml:3`. If it does not, stop and investigate — do **not** edit the pin to match a freshly generated ID.

- [ ] **Step 4: Create `clusters/feather-core/talos/schematic.yaml`**

```yaml
# Image Factory schematic source for this cluster's SecureBoot installer.
#
# POSTing the `customization:` block below to https://factory.talos.dev/schematics
# returns the content-addressed ID pinned in patches/common/installer-secureboot.yaml:
#   2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896
#
# The installer image is <id>:<talos-version>, so a Talos upgrade is a TAG-ONLY edit
# in installer-secureboot.yaml — the ID never changes unless this file changes.
# Verify both are still in sync with:  ./talos.sh schematic
#
# Extensions and what they are load-bearing for:
#   siderolabs/cloudflared        — the tunnel that fronts this cluster; also the
#                                   127.0.0.1 cert-SAN path in patches/common/cert-sans.yaml:6
#                                   that `talosctl` uses via `cloudflared access tcp`.
#   siderolabs/qemu-guest-agent   — graceful guest shutdown / host-side quiesce on all
#                                   10 KVM guests.
#
# NOTE: this file is NOT a machineconfig patch. `talos.sh` only globs
# patches/{common,cluster,cri,extensions}/*.yaml, so it is never merged into a node config.
customization:
    systemExtensions:
        officialExtensions:
            - siderolabs/cloudflared
            - siderolabs/qemu-guest-agent
```

The `customization:` block must list exactly the two extensions Step 2 returned. The comment header is safe: Image Factory parses the body as YAML and re-serialises it before hashing, so leading `#` comments do **not** change the returned ID — verified 2026-08-03 by POSTing this exact file (comments included) and getting `2c97492bf124…` back. Do not, however, add or reorder extensions: that genuinely changes the ID and would make `./talos.sh schematic` fail.

**Verification:** `./talos.sh schematic` in Task 2 Step 6 is the check for this file. Until Task 2 lands, verify by hand:

```bash
curl -fsS -X POST --data-binary @clusters/feather-core/talos/schematic.yaml \
  https://factory.talos.dev/schematics
```

Expected: `{"id":"2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896", ...}`.

**Rollback:** `rm clusters/feather-core/talos/schematic.yaml`. The file is new and nothing consumes it until Task 2.

---

### Task 2: Add `talos.sh schematic` and `talos.sh clean`, and render with `umask 077`

**Repo:** Talos repo. **Files:** Modify `talos.sh`, `Makefile`.

**Interfaces:**
- Consumes: `clusters/feather-core/talos/schematic.yaml` from Task 1.
- Produces: `./talos.sh schematic` (the CI assertion Task 3 wires up) and `./talos.sh clean` (referenced by Task 3's cleanup and PR 5's docs).

- [ ] **Step 1: Add the two commands**

In `talos.sh`, insert the following **after** `cmd_flux-key()` ends (currently line 163, the `}` before `cmd_gen-base()`):

```bash
cmd_schematic() {
  need curl; need python3
  local src="${CLUSTER_DIR}/talos/schematic.yaml"
  local pin="${PATCHES_DIR}/common/installer-secureboot.yaml"
  [ -f "$src" ] || die "Missing $src"
  [ -f "$pin" ] || die "Missing $pin"
  local got want tag
  got="$(curl -fsS -X POST --data-binary @"$src" https://factory.talos.dev/schematics \
         | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"
  want="$(sed -n 's#.*/\([0-9a-f]\{64\}\):v.*#\1#p' "$pin")"
  tag="$(sed -n 's#.*:\(v[0-9][0-9.]*\)[[:space:]]*$#\1#p' "$pin")"
  [ -n "$want" ] || die "Could not parse a schematic ID out of $pin"
  if [ "$got" = "$want" ]; then
    info "schematic OK: ${got} (installer tag ${tag:-unknown})"
  else
    die "schematic MISMATCH
  schematic.yaml            -> ${got}
  installer-secureboot.yaml -> ${want}
Investigate before changing either file. A mismatch means the running image contains
something not described by schematic.yaml — do NOT 'fix' it by editing the pin."
  fi
}

cmd_clean() {
  [ -f .age/key.txt ] || [ -n "${SOPS_AGE_KEY:-}" ] \
    || die "Refusing to clean: no .age/key.txt and no SOPS_AGE_KEY — you could not re-run gen-base afterwards"
  local d
  for d in "${CLUSTER_DIR}/generated" "$BASE_DIR"; do
    [ -d "$d" ] || continue
    find "$d" -type f -exec shred -uz {} + 2>/dev/null || true
    rm -rf "$d"
    info "removed $d"
  done
  info "regenerate with: ./talos.sh gen-base && ./talos.sh gen-talosconfig && ./talos.sh build"
}
```

`shred` before `rm` because both directories hold `machine.ca.key`, `machine.token` and the decrypted Flux deploy key in plaintext.

> ⚠️ **`clean` also destroys `clusters/feather-core/generated/talosconfig`.** That is the *only* talosconfig on this workstation (`~/.talos/config` is empty — see Prerequisites), so after any `clean` you have no way to reach a node until you regenerate it. `gen-base` writes `base/talosconfig`; **`gen-talosconfig` is what writes `generated/talosconfig`**, and `build` does **not** call it. The recovery sequence is therefore always three commands:
>
> ```bash
> ./talos.sh gen-base && ./talos.sh gen-talosconfig && ./talos.sh build
> ```
>
> Every `clean` in this plan (Task 3 Step 4, Task 10 Step 7) uses that exact sequence. Do not drop `gen-talosconfig`.

- [ ] **Step 2: Render with a private umask**

Add `umask 077` as the first statement of the function body in three places:

- `cmd_gen-base()` — after `need sops; need talosctl; need python3` (currently line 166)
- `cmd_gen-talosconfig()` — after `need python3` (currently line 185)
- `cmd_render-node()` — after `need talosctl` (currently line 230)

Example for `cmd_render-node`:

```bash
cmd_render-node() {
  need talosctl
  umask 077   # rendered machineconfigs carry machine.ca.key / machine.token in plaintext
  [ -n "${NODE:-}" ] || die "Usage: ./talos.sh render-node NODE=<name>"
```

- [ ] **Step 3: Document them in `cmd_help`**

In `cmd_help()`, add a `schematic` line to the `Build:` block and a `clean` line after `build`:

```
Build:
  gen-base                   Run talosctl gen config into base/
  schematic                  POST talos/schematic.yaml to Image Factory and assert the ID
                             matches patches/common/installer-secureboot.yaml
  new-node NAME= SITE= ROLE= IP= [CIDR=24] [IFACE=ens18]   Scaffold a node override file
  render-node NODE=<name>    Merge layers -> generated machineconfig for one node
  render-all                 Render machineconfigs for every node under nodes/
  validate                   talosctl-validate every rendered machineconfig
  build                      render-all + validate
  clean                      Shred and remove generated/ and base/ (this DELETES
                             generated/talosconfig; rebuild with:
                             gen-base && gen-talosconfig && build)
```

- [ ] **Step 4: Register them for dispatch and completion**

`talos.sh:303-305` — add `schematic clean` to `COMMANDS`:

```bash
COMMANDS="help age-keygen sops-config sops-updatekeys sops-encrypt sops-decrypt \
sops-edit flux-key schematic gen-base gen-talosconfig new-node render-node render-all \
validate build clean completion"
```

`talos.sh:429-431` — add them to the dispatch case:

```bash
    age-keygen|sops-config|sops-updatekeys|sops-encrypt|sops-decrypt|sops-edit|\
    flux-key|schematic|gen-base|gen-talosconfig|new-node|render-node|render-all|validate|build|clean)
      "cmd_${command}" ;;
```

- [ ] **Step 5: Add them to the `Makefile` shim**

In `Makefile`, add `schematic` and `clean` to **both** the `.PHONY` list and the target list (they are two separate places in the same file):

```make
.PHONY: help age-keygen sops-config sops-encrypt sops-updatekeys sops-decrypt \
        sops-edit flux-key schematic gen-base gen-talosconfig new-node render-node \
        render-all validate build clean

help age-keygen sops-config sops-encrypt sops-updatekeys sops-decrypt sops-edit \
flux-key schematic gen-base gen-talosconfig new-node render-node render-all validate build clean:
```

- [ ] **Step 6: Verify**

```bash
cd /mnt/projects/lab/talos-cluster
bash -n talos.sh && echo "SYNTAX OK"
./talos.sh schematic
./talos.sh help 2>&1 | grep -E 'schematic|clean'
./talos.sh __complete "" "" | grep -cE '^(schematic|clean)$'
```

Expected:
```
SYNTAX OK
schematic OK: 2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896 (installer tag v1.13.4)
```
plus the two help lines, and `2` from the completion check.

**Rollback:** `git checkout -- talos.sh Makefile`. Nothing outside the working tree changed.

---

### Task 3: Shred the stale renders, bump CI's talosctl, wire the schematic check into CI, fix the README claim

**Repo:** Talos repo. **Files:** Modify `.github/workflows/talos.yml`, `README.md`. **Filesystem:** delete `clusters/feather-core/generated/machineconfigs-correct/`.

**Interfaces:**
- Consumes: `./talos.sh schematic` and `./talos.sh clean` from Task 2.
- Produces: a PR-ready branch for Task 4.

> ⚠️ **Step 1 is destructive and irreversible.** It shreds four files for nodes that no longer exist. Read the justification before running it.

- [ ] **Step 1: Shred `generated/machineconfigs-correct/`**

These four files (`fr01-wrk-g-01.yaml`, `fr01-wrk-g-02.yaml`, `fr01-wrk-xl-01.yaml`, `fr01-wrk-xl-02.yaml`, dated Feb/Mar 2026) are git-ignored render artifacts for nodes decommissioned in `cd70920`. They contain `machine.ca.key` and `machine.token` in plaintext — **still the live cluster's**, not historical. Nothing unique is lost: those credentials also live (encrypted) in `clusters/feather-core/talos/secrets/secrets.sops.yaml`, and the two `wrk-g` nodes are gone.

```bash
cd /mnt/projects/lab/talos-cluster
ls -la clusters/feather-core/generated/machineconfigs-correct/          # confirm the 4 files
kubectl get nodes | grep -c 'wrk-g'                                     # must be 0
shred -uz clusters/feather-core/generated/machineconfigs-correct/*.yaml
rmdir clusters/feather-core/generated/machineconfigs-correct
ls clusters/feather-core/generated/                                     # → machineconfigs  talosconfig
```

Expected: the second command prints `0`; the final `ls` no longer lists `machineconfigs-correct`.

**This does not fix the PKI leak.** See "Deliberately NOT in scope".

- [ ] **Step 2: Bump `TALOSCTL_VERSION` and add the schematic check to CI**

In `.github/workflows/talos.yml`, change:

```yaml
env:
  TALOSCTL_VERSION: v1.13.3
```

to:

```yaml
env:
  TALOSCTL_VERSION: v1.13.7
```

and change the final step from:

```yaml
      - name: Render and validate all nodes
        run: ./talos.sh gen-base && ./talos.sh build
```

to:

```yaml
      - name: Assert the committed schematic matches the pinned installer image
        run: ./talos.sh schematic

      - name: Render and validate all nodes
        run: ./talos.sh gen-base && ./talos.sh build
```

The schematic step runs before the render so a mismatch fails fast and cheap. It needs outbound HTTPS to `factory.talos.dev`, which the GitHub-hosted runner has.

The workflow's `paths:` filters already cover `clusters/feather-core/talos/**` and `talos.sh`, so both `schematic.yaml` and the new commands trigger CI without touching the filters.

- [ ] **Step 3: Fix the overstated README claim**

`README.md:48` currently reads:

```markdown
- **One entrypoint** — `./talos.sh help` shows the entire operational surface.
```

Replace with:

```markdown
- **One entrypoint for build & secrets** — `./talos.sh help` covers rendering, validation,
  the schematic check and SOPS. Day-2 node operations (`apply-config`, `upgrade`,
  `upgrade-k8s`, node removal) are still raw `talosctl` — see [Deploy](#deploy).
```

(PR 5 revisits this line once the wrappers exist.)

- [ ] **Step 4: Verify the whole PR renders**

```bash
cd /mnt/projects/lab/talos-cluster
./talos.sh clean
./talos.sh gen-base && ./talos.sh gen-talosconfig && ./talos.sh build
./talos.sh schematic
ls -l clusters/feather-core/generated/machineconfigs/ | head -3
ls -l clusters/feather-core/generated/talosconfig
git status --porcelain
```

⚠️ **`gen-talosconfig` is not optional here.** `clean` shredded `generated/talosconfig`, and `build` does not recreate it. Skip it and every `talosctl` command from Task 6 onwards fails with an empty context.

Expected: `build` prints `VALID   <node>.yaml` ten times and exits 0; `schematic` prints `schematic OK: 2c97492b…`; `gen-talosconfig` prints `gen-talosconfig -> clusters/feather-core/generated/talosconfig  endpoints=['192.168.15.10', '192.168.15.11', '192.168.15.12']` and the file exists at mode `-rw-------`; the rendered files are mode `-rw-------` (this is the `umask 077` change taking effect — the previous renders were `-rw-r--r--`); `git status --porcelain` shows only `talos.sh`, `Makefile`, `README.md`, `.github/workflows/talos.yml` and the new `clusters/feather-core/talos/schematic.yaml`, and **no** file under `generated/`.

Confirm the regenerated talosconfig actually reaches a node before you consider PR 1 done:

```bash
export TALOSCONFIG=$PWD/clusters/feather-core/generated/talosconfig
talosctl -n 192.168.15.10 version | grep -A2 Server
```

Expected: `Tag: v1.13.4`. If this fails, **stop** — do not start PR 2/3 without a working talosconfig.

**Rollback:** `git checkout -- .` for the tracked files. The shredded directory is not recoverable — that is intentional and the justification is in Step 1.

---

### Task 4: Commit and open PR 1

**Repo:** Talos repo. **Files:** none.

- [ ] **Step 1: Commit**

```bash
cd /mnt/projects/lab/talos-cluster
git add clusters/feather-core/talos/schematic.yaml talos.sh Makefile README.md .github/workflows/talos.yml
git commit -m "chore(talos): commit schematic source, add schematic/clean commands, tighten render perms"
```

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin chore/talos-schematic-and-tooling
gh pr create --title "chore(talos): commit schematic source and add schematic/clean commands" --body "$(cat <<'EOF'
## Summary
- Commits clusters/feather-core/talos/schematic.yaml — the exact customization YAML that
  Image Factory resolves to the ID pinned in patches/common/installer-secureboot.yaml.
- Adds `./talos.sh schematic`, which POSTs it and asserts the returned ID equals the pin,
  and wires it into CI as a fast pre-render gate.
- Adds `./talos.sh clean` (shred + rm of generated/ and base/) and renders with umask 077;
  both directories hold machine.ca.key and machine.token in plaintext.
- Bumps CI TALOSCTL_VERSION v1.13.3 -> v1.13.7 to match the operator client. Note: v1.13.3
  and v1.13.4 share the same v1alpha1 schema, so this is tidiness, not a correctness fix.
- Corrects README.md's claim that `talos.sh help` shows "the entire operational surface".

Also done out-of-band (git-ignored, so not in this diff): shredded
clusters/feather-core/generated/machineconfigs-correct/ — four stale renders for the
wrk-g nodes decommissioned in cd70920, carrying the live machine CA key and token in
cleartext. This does NOT rotate that PKI; rotation is tracked separately.

No node is touched by this PR.

## Test plan
- [x] ./talos.sh schematic -> schematic OK: 2c97492b...
- [x] ./talos.sh clean && ./talos.sh gen-base && ./talos.sh gen-talosconfig && ./talos.sh build -> 10x VALID
- [x] generated/talosconfig regenerated and reaches a node (talosctl -n 192.168.15.10 version)
- [x] rendered configs are mode 0600
EOF
)"
```

Merging is a human decision — do not merge automatically.

---

# PR 2 — Pin the control-plane `extraManifests`

Changes `cluster.extraManifests` on the three control planes only. Applied with `--mode=no-reboot`; no node reboots.

### Task 5: Pin both URLs to tags

**Repo:** Talos repo. **Files:** Modify `clusters/feather-core/talos/defaults/roles/controlplane.yaml:16-18`.

**Interfaces:**
- Consumes: DG-1 and DG-2 answers.
- Produces: a rendered control-plane config with pinned manifest URLs, for Task 6 to apply.

- [ ] **Step 1: Record the current live state (this is your rollback reference)**

```bash
kubectl -n kubelet-serving-cert-approver get deploy kubelet-serving-cert-approver \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl -n kube-system get deploy metrics-server \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl top nodes | head -3
kubectl -n kube-system logs deploy/metrics-server --tail=1
```

Expected as of 2026-08-03: `ghcr.io/alex1989hu/kubelet-serving-cert-approver:main`, `registry.k8s.io/metrics-server/metrics-server:v0.8.1`, `kubectl top nodes` returns numbers for all 10 nodes.

- [ ] **Step 2: Confirm both tagged URLs resolve before committing them**

```bash
curl -fsSL https://raw.githubusercontent.com/alex1989hu/kubelet-serving-cert-approver/v0.11.0/deploy/standalone-install.yaml | grep 'image:'
curl -fsSL -o /dev/null -w '%{http_code}\n' https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.1/components.yaml
```

Expected:
```
        image: ghcr.io/alex1989hu/kubelet-serving-cert-approver:0.11.0
200
```

If either fails, stop — do not apply a config referencing a URL that 404s; the Talos manifest-sync controller will log errors on every reconcile.

- [ ] **Step 3: Edit `defaults/roles/controlplane.yaml`**

Lines 16-18 currently read:

```yaml
  extraManifests:
    - https://raw.githubusercontent.com/alex1989hu/kubelet-serving-cert-approver/main/deploy/standalone-install.yaml
    - https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

Replace with (versions per DG-1/DG-2):

```yaml
  # Pinned to tags on purpose: these are applied by the Talos controller-manager with
  # control-plane privileges, outside Flux and outside Renovate's view. `main`/`latest`
  # meant two renders of the same commit could install different software.
  # Bumping either is a reviewable one-line change here.
  extraManifests:
    - https://raw.githubusercontent.com/alex1989hu/kubelet-serving-cert-approver/v0.11.0/deploy/standalone-install.yaml
    - https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.1/components.yaml
```

- [ ] **Step 4: Render and verify**

```bash
cd /mnt/projects/lab/talos-cluster
./talos.sh build
grep -A3 extraManifests clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
grep -c extraManifests clusters/feather-core/generated/machineconfigs/fr01-wrk-xl-01.yaml
```

Expected: `build` prints 10× `VALID`; the control-plane render shows both pinned URLs; the worker render returns `0` (control-plane-only setting, as before).

- [ ] **Step 5: Commit**

```bash
git checkout main && git pull origin main
git checkout -b fix/talos-pin-extramanifests
# re-apply the edit if you branched after editing
git add clusters/feather-core/talos/defaults/roles/controlplane.yaml
git commit -m "fix(talos): pin control-plane extraManifests to tags"
git push -u origin fix/talos-pin-extramanifests
```

**Rollback:** nothing has reached a node yet — this task only edits a file and renders. `git checkout -- clusters/feather-core/talos/defaults/roles/controlplane.yaml && ./talos.sh build`, or close the PR. The cluster is unchanged until Task 6.

---

### Task 6: Apply to the three control planes and verify

**Repo:** Talos repo. **Files:** none (operational).

**Interfaces:**
- Consumes: merged PR 2 and the rendered configs from Task 5.
- Produces: control planes fetching pinned manifests — the clean baseline PR 3 upgrades from.

- [ ] **Step 1: Dry-run against one node first**

```bash
cd /mnt/projects/lab/talos-cluster
export TALOSCONFIG=$PWD/clusters/feather-core/generated/talosconfig
talosctl -n 192.168.15.10 -e 192.168.15.11 apply-config --mode=no-reboot --dry-run \
  -f clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
```

Expected: a diff showing **only** the two `extraManifests` URLs changing, and no statement that a reboot is required. If the diff contains anything else, stop — your render is stale or carries an unrelated change.

- [ ] **Step 2: Apply, one control plane at a time**

Always target a node via a *different* endpoint so the connection is not the node you are changing:

```bash
talosctl -n 192.168.15.10 -e 192.168.15.11 apply-config --mode=no-reboot -f clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
talosctl -n 192.168.15.11 -e 192.168.15.10 apply-config --mode=no-reboot -f clusters/feather-core/generated/machineconfigs/fr01-cp-02.yaml
talosctl -n 192.168.15.12 -e 192.168.15.10 apply-config --mode=no-reboot -f clusters/feather-core/generated/machineconfigs/fr01-cp-03.yaml
```

- [ ] **Step 3: Verify the manifests actually re-synced**

```bash
sleep 60
kubectl -n kubelet-serving-cert-approver get deploy kubelet-serving-cert-approver \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl -n kubelet-serving-cert-approver get pods
kubectl -n kube-system get deploy metrics-server -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

Expected: cert-approver image is now `ghcr.io/alex1989hu/kubelet-serving-cert-approver:0.11.0` with a `Running` `1/1` pod; metrics-server is unchanged at `registry.k8s.io/metrics-server/metrics-server:v0.8.1` (the pin is byte-identical to what it was already serving, so no rollout).

- [ ] **Step 4: Verify kubelet serving certs still work end to end**

```bash
kubectl top nodes
kubectl logs -n kube-system -l k8s-app=kube-proxy --tail=1 --max-log-requests=10 >/dev/null && echo "LOGS OK"
kubectl -n kubelet-serving-cert-approver logs deploy/kubelet-serving-cert-approver --tail=20
kubectl get csr
```

Expected: `kubectl top nodes` returns metrics for all 10 nodes and `LOGS OK` prints — both require the apiserver to trust each kubelet's serving cert, which only holds if the approver has been doing its job. The approver log shows it running with no errors. `kubectl get csr` returning `No resources found` is **normal** — approved CSRs are garbage-collected, and it was already empty before this change; it is *not* evidence of failure. A true end-to-end approval only happens at the next serving-cert rotation.

**Gate:** all four checks must pass before PR 3 starts. **Rollback:** revert the PR 2 merge commit, `./talos.sh build`, and re-apply the three control-plane configs with the same `--mode=no-reboot` commands. The cert-approver Deployment rolls back to `:main` within ~60 s of the manifest sync.

---

# PR 3 — Talos v1.13.4 → v1.13.7 and Kubernetes v1.36.1 → v1.36.2

> ⚠️ **THIS PR REBOOTS ALL TEN NODES.** Have console access to every KVM guest. A SecureBoot UKI that fails to boot is only recoverable from the console. Answer DG-3 before starting. This is the only irreversible-in-practice step in the plan: Talos supports downgrades within a minor, but a downgrade is a second reboot of an already-degraded node and is not a fast rollback.

**Hard prerequisite: an etcd snapshot must exist off-cluster (see Cross-theme dependencies).**

> **Expect Flux to look broken during this PR, and leave it alone.** Draining and rebooting a node moves pods; most GitOps-repo Flux layers use `wait: true`, so while a Deployment is rescheduling its layer reports `Reconciling`/`not ready` and every dependent layer reports "dependency not ready". This is transient and self-clearing. **Do not run `flux reconcile` to "fix" it** — forcing a layer mid-flight flips it back to `Reconciling` and makes every dependent report not-ready, i.e. you create the churn you are trying to clear. Check Flux once, at the PR 3 gate, after the last node is back `Ready`.

### Task 7: Pre-flight — snapshot, health baseline, and the version bump commit

**Repo:** Talos repo. **Files:** Modify `clusters/feather-core/talos/patches/common/installer-secureboot.yaml:3`; move `clusters/feather-core/talos/patches/extensions/cloudflared.yaml`.

**Interfaces:**
- Consumes: PR 1 and PR 2 merged and verified.
- Produces: an etcd snapshot, a recorded health baseline, and the branch Task 8 rolls out.

- [ ] **Step 1: Take an etcd snapshot and move it off-cluster**

If `offsite-backups-and-disaster-recovery` has landed, use its command. Otherwise, by hand:

```bash
cd /mnt/projects/lab/talos-cluster
export TALOSCONFIG=$PWD/clusters/feather-core/generated/talosconfig
mkdir -p ~/talos-snapshots && chmod 700 ~/talos-snapshots
talosctl -n 192.168.15.10 etcd snapshot ~/talos-snapshots/etcd-$(date +%Y%m%d-%H%M).db
ls -l ~/talos-snapshots/
```

Expected: a `.db` file of non-trivial size. **This file contains every Kubernetes Secret.** Encrypt it and copy it off this workstation before proceeding. Do not skip this because "the upgrade is safe" — it is the only thing standing between a bad control-plane reboot and a rebuild.

- [ ] **Step 2: Record the health baseline**

```bash
kubectl get nodes -o wide
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
talosctl -n 192.168.15.10,192.168.15.11,192.168.15.12 service etcd
talosctl -n 192.168.15.10 etcd members
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph -s
flux get kustomizations -A     # run from the GitOps repo checkout
```

Expected baseline as of 2026-08-03: 10 nodes `Ready` on `Talos (v1.13.4)` / kubelet `v1.36.1` / `containerd://2.2.4`; etcd `Running`/`OK` on all three with three members; Ceph `HEALTH_OK`, `12 osds: 12 up, 12 in`, `377 pgs` all `active+clean`. **Save this output.** If anything is already unhealthy, fix it first — do not upgrade into a degraded cluster.

- [ ] **Step 3: Branch and bump the installer tag**

```bash
git checkout main && git pull origin main
git checkout -b feat/talos-1-13-7
```

`clusters/feather-core/talos/patches/common/installer-secureboot.yaml` line 3, change:

```yaml
    image: factory.talos.dev/metal-installer-secureboot/2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896:v1.13.4
```

to:

```yaml
    image: factory.talos.dev/metal-installer-secureboot/2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896:v1.13.7
```

**The 64-hex schematic ID does not change.** Only the tag after the colon. If you find yourself editing the ID, stop and re-read Task 1.

- [ ] **Step 4: Retire the dead `machine.install.extensions` patch**

`patches/extensions/cloudflared.yaml` sets `machine.install.extensions`, which Talos marks `// Deprecated: Use custom InstallImage instead.` It renders into all 10 machineconfigs and installed nothing — cloudflared comes from the Image Factory schematic. `talosctl validate` already warns about it:

```
WARNING: .machine.install.extensions is deprecated, please see https://docs.siderolabs.com/talos/latest/platform-specific-installations/boot-assets
```

Disable it using the repo's own documented mechanism (rename away from `*.yaml`, per `docs/ADOPTING.md`) rather than deleting it, so the pointer to the schematic survives:

```bash
git mv clusters/feather-core/talos/patches/extensions/cloudflared.yaml \
       clusters/feather-core/talos/patches/extensions/cloudflared.yaml.disabled
```

Then replace the whole contents of `clusters/feather-core/talos/patches/extensions/cloudflared.yaml.disabled` with:

```yaml
# DISABLED — machine.install.extensions is deprecated and was always a no-op here.
# `talosctl validate` warns on it; Talos marks it "Deprecated: Use custom InstallImage
# instead". cloudflared is installed by the Image Factory schematic, not by this file:
#   source:  clusters/feather-core/talos/schematic.yaml
#   pinned:  clusters/feather-core/talos/patches/common/installer-secureboot.yaml
#   verify:  ./talos.sh schematic
# Adding an extension here has no effect and produces no error. Add it to schematic.yaml,
# POST it, and update the pinned ID + tag instead.
#
# machine:
#   install:
#     extensions:
#       - image: ghcr.io/siderolabs/cloudflared
```

- [ ] **Step 5: Render and verify the diff is exactly what you expect**

```bash
cd /mnt/projects/lab/talos-cluster
./talos.sh schematic
./talos.sh build 2>&1 | tail -12
# `./talos.sh build` runs `talosctl validate ... >/dev/null 2>&1` on the success path
# (talos.sh:282), so it SWALLOWS warnings — it never showed the deprecation warning and
# never will. Call validate directly to see it, otherwise this check proves nothing.
talosctl validate -c clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml -m metal
grep -c 'extensions:' clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
grep -A6 'install:' clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
```

Expected: `schematic OK: 2c97492b… (installer tag v1.13.7)`; 10× `VALID` from `build`; the direct `talosctl validate` prints **only** `… is valid for metal mode` with **no** `WARNING: .machine.install.extensions is deprecated` line (that warning is present today — confirmed on the current render on 2026-08-03, so its disappearance is a real signal); `grep -c 'extensions:'` returns `0`; the install block now reads

```yaml
    install:
        disk: /dev/sda
        image: factory.talos.dev/metal-installer-secureboot/2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896:v1.13.7
        wipe: false
        grubUseUKICmdline: true
```

with **no** `extensions:` key.

- [ ] **Step 6: Commit and open the PR**

```bash
git add clusters/feather-core/talos/patches/common/installer-secureboot.yaml \
        clusters/feather-core/talos/patches/extensions/
git commit -m "feat(talos): bump installer to v1.13.7 and retire the deprecated install.extensions patch"
git push -u origin feat/talos-1-13-7
gh pr create --title "feat(talos): bump installer to v1.13.7" --body "$(cat <<'EOF'
## Summary
- installer-secureboot.yaml: tag v1.13.4 -> v1.13.7. The schematic ID is unchanged
  (verified by ./talos.sh schematic), so the extension set is provably identical.
- Retires patches/extensions/cloudflared.yaml -> .yaml.disabled. machine.install.extensions
  is deprecated and was always a no-op; cloudflared comes from the Image Factory schematic.
  This also silences the talosctl validate deprecation warning.

Picks up: containerd 2.2.5 (three critical + one high CVE) and 2.2.6, runc 1.4.3,
kernel 6.18.34 -> 6.18.39, "kubelet stuck restarting" (v1.13.6), "do not block volume
lifecycle teardown on failed user volumes" (v1.13.7), "bump number of open files for etcd".

NOT claimed: this is not a known fix for the containerd 2.2.4 shim task.Delete hang on this
cluster. 2.2.5 is security-only and none of 2.2.6's teardown fixes match that signature.

## Test plan
- [x] ./talos.sh schematic -> OK at tag v1.13.7
- [x] ./talos.sh build -> 10x VALID, deprecation warning gone
- [ ] Rolling upgrade per docs/superpowers/plans/2026-08-03-talos-fleet-lifecycle.md Tasks 8-10
EOF
)"
```

**Gate:** do not merge until the etcd snapshot from Step 1 is confirmed off-cluster and the DG-3 window is agreed.

**Rollback:** this task touches no node — it edits two files and takes a snapshot. `git checkout -- clusters/feather-core/talos/patches/` (which also restores `cloudflared.yaml` from the `git mv`), `./talos.sh build`, close the PR. Verify with `git status --porcelain` → empty and `./talos.sh schematic` → `installer tag v1.13.4`.

---

### Task 8: Upgrade the three control planes, one at a time

**Repo:** Talos repo. **Files:** none (operational).

**Interfaces:**
- Consumes: merged PR 3 and the fresh renders from Task 7.
- Produces: three control planes on v1.13.7 with a healthy 3-member etcd.

> ⚠️ **Never run two of these concurrently.** Three etcd members tolerate exactly one down. Each command reboots the node.

Set up once:

```bash
cd /mnt/projects/lab/talos-cluster
git checkout main && git pull origin main
./talos.sh gen-base         # required if base/ was cleaned in Task 3; harmless otherwise
./talos.sh build            # renders now carry the v1.13.7 pin
./talos.sh schematic        # must print: schematic OK: 2c97492b... (installer tag v1.13.7)
export TALOSCONFIG=$PWD/clusters/feather-core/generated/talosconfig
export IMG=factory.talos.dev/metal-installer-secureboot/2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896:v1.13.7
talosctl -n 192.168.15.10 version | grep -A2 Server   # sanity: talosconfig works, Server Tag: v1.13.4
```

⚠️ **`TALOSCONFIG` and `IMG` are shell-local.** Tasks 9 and 10 reuse both. If you open a new terminal, or resume the window the next day, re-run the two `export` lines before continuing — otherwise `talosctl ... upgrade --image ""` or an empty-context error is what you get, mid-window, with a node cordoned.

- [ ] **Step 1: Upgrade `fr01-cp-01` (192.168.15.10)**

```bash
talosctl -n 192.168.15.10 -e 192.168.15.11 upgrade \
  --image "$IMG" --drain --drain-timeout 10m --wait
```

Note there is **no `--preserve`** — see "Corrections to the audit". `--drain` and `--wait` default to true; they are explicit here so the intent is readable in shell history.

Control-plane nodes carry the `node-role.kubernetes.io/control-plane` taint and host only static pods, DaemonSets and the single-replica `rook-ceph-operator` (no PDB), so the drain is uneventful.

- [ ] **Step 2: Verify before touching the next one**

```bash
talosctl -n 192.168.15.10 version | grep -A2 Server
kubectl get node fr01-cp-01 -o wide
talosctl -n 192.168.15.10,192.168.15.11,192.168.15.12 service etcd
talosctl -n 192.168.15.11 etcd members
kubectl get nodes | grep SchedulingDisabled
```

Expected: server `Tag: v1.13.7`; the node `Ready` with `Talos (v1.13.7)` and `containerd://2.2.6`; etcd `Running`/`OK` on all three; exactly three members; the last command returns nothing. If the node is still `SchedulingDisabled`, run `kubectl uncordon fr01-cp-01`.

- [ ] **Step 3: Repeat for `fr01-cp-02`, then `fr01-cp-03`**

```bash
talosctl -n 192.168.15.11 -e 192.168.15.10 upgrade --image "$IMG" --drain --drain-timeout 10m --wait
# verify (Step 2, substituting fr01-cp-02 / 192.168.15.11), then:
talosctl -n 192.168.15.12 -e 192.168.15.10 upgrade --image "$IMG" --drain --drain-timeout 10m --wait
```

- [ ] **Step 4: Control-plane gate**

```bash
kubectl get nodes -o wide | grep cp-
talosctl -n 192.168.15.10 etcd members
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph -s | head -5
```

Expected: all three control planes `Ready` on `Talos (v1.13.7)`; three etcd members; Ceph still `HEALTH_OK`.

**If a node does not come back:** use the KVM console. A SecureBoot UKI boot failure is the scenario console access exists for. Do **not** proceed to the next node. Rollback for a single node is `talosctl -n <ip> upgrade --image <same-schematic>:v1.13.4 --wait` from a healthy control plane; the etcd member's data survives because `EPHEMERAL` is preserved by default.

---

### Task 9: Upgrade the three storage nodes, one at a time

**Repo:** Talos repo. **Files:** none (operational).

**Interfaces:**
- Consumes: a healthy control plane at v1.13.7 from Task 8.
- Produces: storage nodes on v1.13.7 with Ceph `HEALTH_OK`.

Each storage node hosts 4 OSDs (`fr01-str-01` → osd.0/3/6/9, `fr01-str-02` → osd.2/5/8/11, `fr01-str-03` → osd.1/4/7/10). The CRUSH failure domain is `host` with `size=3`, so exactly one host may be down. `rook-ceph-osd` PDB allows 1 disruption — the drain will work, but it is slow.

- [ ] **Step 1: For each of `fr01-str-01` (.7), `fr01-str-02` (.8), `fr01-str-03` (.9), in order**

```bash
# 1. suppress rebalancing for the reboot window
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph osd set noout
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph -s | head -5

# 2. upgrade
talosctl -n 192.168.15.7 -e 192.168.15.10 upgrade \
  --image "$IMG" --drain --drain-timeout 15m --wait

# 3. verify the node
talosctl -n 192.168.15.7 version | grep -A2 Server
kubectl get node fr01-str-01 -o wide
kubectl get nodes | grep SchedulingDisabled     # uncordon if listed

# 4. wait for all 12 OSDs back up, THEN clear noout
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph osd tree
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph osd unset noout
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph -s
```

`--drain-timeout 15m` (not 10m) because OSD eviction is serialised by the `rook-ceph-osd` PDB.

- [ ] **Step 2: Per-node gate — do not start the next storage node until this passes**

Expected from step 4 above: `ceph osd tree` shows all 12 OSDs `up`; `ceph -s` shows `health: HEALTH_OK`, `osd: 12 osds: 12 up, 12 in`, and `377 pgs` back to `active+clean` (a transient `active+clean+scrubbing` or a brief `degraded`/`backfilling` while PGs re-peer is expected; wait for it to clear).

**If Ceph does not return to HEALTH_OK, stop.** Do not upgrade a second storage node on a degraded cluster — that is the one path in this plan that can cause I/O stalls for all 37 PVCs.

> ⚠️ **`noout` is cluster-wide and sticky.** If you abort mid-node — the upgrade hangs, the node does not come back, you go to bed — `noout` stays set and Ceph will not self-heal a genuinely lost OSD. Whatever happens, before you stop working:
>
> ```bash
> kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph osd unset noout
> kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph -s | grep -i noout || echo "noout cleared"
> ```
>
> Expected: `noout cleared` (the `ceph -s` health block no longer mentions `noout flag(s) set`).

**Rollback (per node):** `talosctl -n <ip> -e 192.168.15.10 upgrade --image factory.talos.dev/metal-installer-secureboot/2c97492bf124203fa1190e81e7d6197961338d996b0ffcca8caba253c0c21896:v1.13.4 --drain --drain-timeout 15m --wait` — a second reboot of the same node, so only do it if the node is actually broken at v1.13.7, not as routine. Clear `noout` afterwards and wait for `HEALTH_OK` + `12 up, 12 in` before touching anything else. Downgrading is not a fast rollback; if a storage node is wedged, the KVM console is the first stop, not another upgrade command.

---

### Task 10: Upgrade the four workers, then Kubernetes to v1.36.2

**Repo:** Talos repo. **Files:** none (operational).

**Interfaces:**
- Consumes: healthy control planes and storage nodes at v1.13.7.
- Produces: the whole fleet on Talos v1.13.7 / Kubernetes v1.36.2.

> ⚠️ Workers host every stateful workload. Three PDBs currently report `ALLOWED DISRUPTIONS = 0` and **will stall `--drain`**. See DG-5.

- [ ] **Step 1: Before each worker, check what is on it**

```bash
NODE=fr01-wrk-xl-01
kubectl get pods -A --field-selector spec.nodeName=$NODE -o wide
kubectl get pdb -A -o json | python3 -c "
import sys,json
for p in json.load(sys.stdin)['items']:
    s=p.get('status',{})
    if s.get('disruptionsAllowed',0)==0 and s.get('expectedPods',0)>0:
        print('BLOCKING:', p['metadata']['namespace'], p['metadata']['name'])
"
kubectl -n cnpg-system get cluster feather-core-cluster-pg -o jsonpath='{.status.currentPrimary}{"\n"}'
```

As of 2026-08-03 the blockers are `cnpg-system/feather-core-cluster-pg-primary`, `harbor/harbor-registry` and `n8n/n8n-main`; `harbor-registry` and `n8n-main` both sit on **`fr01-wrk-xl-02`** and the CNPG primary is `feather-core-cluster-pg-2` on **`fr01-wrk-xl-03`**. **Re-check at execution time — pods move.** (`outline/outline-websockets` and `outline/outline-worker` also show 0 allowed, but have `expectedPods: 0` — no pods exist, so they cannot block anything. Ignore them.)

- [ ] **Step 2: Clear the blockers for this node (DG-5 option (a))**

If the CNPG primary is on the target node, switch over properly — do not delete a Postgres primary pod:

```bash
kubectl cnpg promote -n cnpg-system feather-core-cluster-pg feather-core-cluster-pg-1   # any instance NOT on the target node
kubectl -n cnpg-system get cluster feather-core-cluster-pg -o jsonpath='{.status.currentPrimary}{"\n"}'
kubectl -n cnpg-system get pods -o wide | grep feather-core-cluster-pg-
```

Expected: `currentPrimary` is now an instance on a different node, and all three instances are `2/2 Running`.

For `harbor-registry` / `n8n-main` on the target node, delete the pod (not an eviction, so the PDB does not apply) and let the ReplicaSet reschedule it.

⚠️ **Cordon the node FIRST.** If you delete the pod while the node is still schedulable, the ReplicaSet is free to put the replacement straight back onto the node you are about to upgrade — you take the outage and still stall the drain. `talosctl upgrade --drain` cordons again later; cordoning twice is harmless.

```bash
NODE=fr01-wrk-xl-02                      # the node you are about to upgrade
kubectl cordon $NODE
kubectl -n harbor delete pod -l app=harbor,component=registry --field-selector spec.nodeName=$NODE
kubectl -n n8n   delete pod -l <the n8n-main selector from Step 1> --field-selector spec.nodeName=$NODE
kubectl get pods -A -o wide | grep -E 'harbor-registry|n8n-main'
```

Expected: both are `Running` on a node **other than** `$NODE`. Each has one replica, so this is a real ~30–60 s outage for the Harbor registry and n8n — unavoidable either way, but do it deliberately rather than discovering it as a stalled drain.

If a pod comes back `Pending` instead of `Running` on another node, **do not start the upgrade** — you have created an outage without clearing the blocker. `kubectl uncordon $NODE`, investigate why it will not schedule elsewhere, and retry.

- [ ] **Step 3: Upgrade the worker**

```bash
talosctl -n 192.168.15.5 -e 192.168.15.10 upgrade --image "$IMG" --drain --drain-timeout 15m --wait
```

Node → IP: `fr01-wrk-xl-01` 192.168.15.5, `fr01-wrk-xl-02` 192.168.15.6, `fr01-wrk-xl-03` 192.168.15.15, `fr01-wrk-xl-04` 192.168.15.16.

- [ ] **Step 4: Per-worker verification**

```bash
kubectl get node <NODE> -o wide
kubectl get nodes | grep SchedulingDisabled            # uncordon if listed
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph -s | head -3
```

Expected: the node is `Ready` on `Talos (v1.13.7)` / `containerd://2.2.6`; no pods stuck outside `Running`/`Succeeded` beyond pre-existing ones from your Task 7 baseline; Ceph `HEALTH_OK`.

Repeat Steps 1–4 for the remaining three workers, one at a time.

- [ ] **Step 5: Fleet gate**

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,OS:.status.nodeInfo.osImage,KUBELET:.status.nodeInfo.kubeletVersion,CRI:.status.nodeInfo.containerRuntimeVersion'
kubectl get nodes -o json | python3 -c "
import sys,json
ids={n['metadata']['annotations'].get('extensions.talos.dev/schematic') for n in json.load(sys.stdin)['items']}
print('schematic ids:', ids)"
```

Expected: all 10 nodes `Talos (v1.13.7)` / `v1.36.1` / `containerd://2.2.6`, and **one** schematic ID, still `2c97492b…` — the proof that no extension was silently dropped. Also confirm the extension labels survived:

```bash
kubectl get nodes -o json | python3 -c "
import sys,json
for n in json.load(sys.stdin)['items']:
    print(n['metadata']['name'], {k:v for k,v in n['metadata']['labels'].items() if k.startswith('extensions.talos.dev')})"
```

Expected: `cloudflared` and `qemu-guest-agent` present on all 10 (versions may have advanced with the Talos release — that is fine; *absence* is the failure signal).

- [ ] **Step 6: Kubernetes v1.36.1 → v1.36.2 (DG-4)**

```bash
talosctl -n 192.168.15.10 upgrade-k8s --to v1.36.2 --dry-run
```

Read the plan it prints. Then:

```bash
talosctl -n 192.168.15.10 upgrade-k8s --to v1.36.2
kubectl get nodes -o custom-columns='NAME:.metadata.name,KUBELET:.status.nodeInfo.kubeletVersion'
kubectl -n kube-system get pods -l tier=control-plane -o wide
```

Expected: all 10 kubelets on `v1.36.2`; apiserver/controller-manager/scheduler static pods `Running` on all three control planes.

- [ ] **Step 7: Keep `talos.sh` in sync with the cluster's Kubernetes version**

`upgrade-k8s` edits the machineconfig on the nodes directly; the repo's `gen-base` default would otherwise re-render at 1.36.1 and drift. In `talos.sh:24`, change:

```bash
KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.36.1}"
```

to:

```bash
KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.36.2}"
```

Then:

```bash
cd /mnt/projects/lab/talos-cluster
# clean shreds generated/talosconfig — gen-talosconfig is what puts it back, NOT build.
./talos.sh clean && ./talos.sh gen-base && ./talos.sh gen-talosconfig && ./talos.sh build
export TALOSCONFIG=$PWD/clusters/feather-core/generated/talosconfig
talosctl -n 192.168.15.10 version | grep -A2 Server    # must print Tag: v1.13.7 — PR 4 needs this
grep 'kubelet:v' clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
git checkout -b chore/talos-k8s-1-36-2
git add talos.sh
git commit -m "chore(talos): track kubernetes v1.36.2 in gen-base default"
git push -u origin chore/talos-k8s-1-36-2
gh pr create --title "chore(talos): track kubernetes v1.36.2 in gen-base default" \
  --body "Follows the talosctl upgrade-k8s --to v1.36.2 run; keeps rendered configs matching the cluster."
```

Expected: `image: ghcr.io/siderolabs/kubelet:v1.36.2` in the render; 10× `VALID`.

**PR 3 gate:** the fleet is done when Step 5 and Step 6 both pass and `flux get kustomizations -A` (from the GitOps repo) shows every layer `READY=True`. **Rollback:** per-node, `talosctl -n <ip> upgrade --image <same-schematic>:v1.13.4 --wait`, one node at a time, same order. Revert the PR to keep git honest.

---

# PR 4 — Kubelet reservations and eviction tier

Config-only change applied with `--mode=no-reboot`. The kubelet restarts on each node (node briefly `NotReady`; running pods are unaffected). Reversible by re-applying the previous render.

**Measured baseline (2026-08-03).** `enforceNodeAllocatable: ["pods"]`, `kubeReserved: null`, no `systemReservedCgroup`/`kubeReservedCgroup`, `evictionSoft: null`, `evictionMinimumReclaim: null`, `evictionHard.memory.available: 100Mi` on every node. `systemReserved.memory` is `384Mi` on workers/storage and `512Mi` on control planes (Talos default differs by role — do not assume 384Mi everywhere). Allocatable is exactly `capacity − systemReserved − evictionHard`, confirmed on all three roles.

| Role | CPU | Memory capacity | Allocatable now | Requests now | New systemReserved | New kubeReserved | evictionHard | **New allocatable** | Requests as % of new |
|---|---|---|---|---|---|---|---|---|---|
| controlplane ×3 | 4 | 11920 Mi | 11308 Mi | 1206 / 1148 / 1148 Mi | 2Gi / 500m | 1Gi / 500m | 500Mi | **8348 Mi** | 14 % |
| storage ×3 | 8 | 32054 Mi | 31570 Mi | 21434 / **22006** / 21434 Mi | 1Gi / 500m | 1Gi / 500m | 500Mi | **29506 Mi** | 73 / **75** / 73 % |
| xl ×4 | 32 | 64350 Mi | 63866 Mi | 26912 / 24751 / **26145** / 22581 Mi | 2Gi / 1000m | 1Gi / 500m | 500Mi | **60778 Mi** | 44 / 41 / 43 / 37 % |

**No node loses enough allocatable to make a currently-scheduled pod not fit.** The tightest node in the fleet is **`fr01-str-02` at 22006 Mi = 75 % of the new 29506 Mi** (re-measured live 2026-08-03), leaving ~7.3 GiB of request headroom — *not* `fr01-str-01`. CPU is never the constraint: the new storage allocatable is 7000m against 2485m of requests. (The 161 % *limit* overcommit on storage is a separate problem — limits do not affect scheduling.)

Re-measure before applying, because pods move:

```bash
kubectl get pods -A -o json | python3 -c "
import sys,json,collections
def mem(s):
    s=str(s or 0)
    for u,m in [('Ki',1/1024),('Mi',1),('Gi',1024)]:
        if s.endswith(u): return float(s[:-len(u)])*m
    return float(s)/1048576
r=collections.defaultdict(float)
for p in json.load(sys.stdin)['items']:
    if p['status'].get('phase') in ('Succeeded','Failed') or not p['spec'].get('nodeName'): continue
    for c in p['spec']['containers']:
        r[p['spec']['nodeName']]+=mem(c.get('resources',{}).get('requests',{}).get('memory'))
for n in sorted(r): print(n, round(r[n]), 'Mi')"
```

Expected: no storage node above ~29506 Mi, no xl node above ~60778 Mi, no control plane above ~8348 Mi. If one is, **stop** — that node would start evicting/refusing pods the moment the reservation lands, and PR 4 must not be applied to it until the workload theme right-sizes it.

### Task 11: Add the common eviction tier and role-specific reservations

**Repo:** Talos repo. **Files:** Create `clusters/feather-core/talos/patches/common/kubelet-reservations.yaml`; modify `defaults/roles/controlplane.yaml`, `defaults/roles/storage.yaml`, `defaults/roles/xl.yaml`; comment-only edits to `defaults/roles/general.yaml`, `small.yaml`, `ingress.yaml`.

**Interfaces:**
- Consumes: the fleet at v1.13.7 from PR 3.
- Produces: rendered configs carrying `machine.kubelet.extraConfig`, for Task 12 to apply.

- [ ] **Step 1: Create the common eviction patch**

`clusters/feather-core/talos/patches/common/kubelet-reservations.yaml`:

```yaml
# Kubelet eviction tier (all nodes). Role-specific systemReserved/kubeReserved live in
# defaults/roles/<role>.yaml and merge into this same extraConfig map.
#
# Stock Talos ships evictionHard.memory.available=100Mi with no soft tier, i.e. on a 64 GB
# node memory pressure only fires at 99.85% utilisation — far too late for the kubelet to
# evict anything, so the kernel OOM killer wins the race and picks by oom_score_adj. Ceph
# OSD pods are Burstable, so a burst-y pod can get an OSD killed. (The kubelet itself is
# safe: Talos gives it a protective oom_score_adj.)
#
# nodefs/imagefs thresholds are repeated verbatim from the Talos defaults: extraConfig
# REPLACES a map wholesale rather than merging into it, so omitting them would drop them.
machine:
  kubelet:
    extraConfig:
      evictionHard:
        memory.available: 500Mi
        nodefs.available: 10%
        nodefs.inodesFree: 5%
        imagefs.available: 15%
        imagefs.inodesFree: 5%
      evictionSoft:
        memory.available: 1Gi
        nodefs.available: 15%
      evictionSoftGracePeriod:
        memory.available: 2m
        nodefs.available: 2m
      evictionMinimumReclaim:
        memory.available: 500Mi
      evictionMaxPodGracePeriod: 60
```

- [ ] **Step 2: Add reservations to `defaults/roles/controlplane.yaml`**

Append to the existing `machine:` block (it currently only has `nodeLabels:`). Full resulting `machine:` section:

```yaml
machine:
  nodeLabels:
    node.kubernetes.io/exclude-from-external-load-balancers: ""
  kubelet:
    # etcd runs as a Talos system service, OUTSIDE the kubepods cgroup, so it is charged
    # to systemReserved. Stock Talos reserves 512Mi on control planes; these nodes sit at
    # ~4 GiB used with only 1206Mi of pod requests, so 2Gi/1Gi is comfortable headroom.
    # New allocatable: 11920Mi - 2048 - 1024 - 500 = 8348Mi (requests 1206Mi = 14%).
    extraConfig:
      systemReserved:
        cpu: 500m
        memory: 2Gi
        ephemeral-storage: 2Gi
        pid: "100"
      kubeReserved:
        cpu: 500m
        memory: 1Gi
        ephemeral-storage: 1Gi
        pid: "100"
```

- [ ] **Step 3: Add reservations to `defaults/roles/storage.yaml`**

Append a `kubelet:` block to the existing `machine:` section (keep `install`, `nodeLabels`, `nodeTaints` exactly as they are):

```yaml
  kubelet:
    # 32 GB / 8 CPU, 4 OSDs. Smaller systemReserved than xl because there is less to
    # reserve for and less headroom to give away.
    # New allocatable: 32054Mi - 1024 - 1024 - 500 = 29506Mi (requests 21434Mi = 73%).
    extraConfig:
      systemReserved:
        cpu: 500m
        memory: 1Gi
        ephemeral-storage: 2Gi
        pid: "100"
      kubeReserved:
        cpu: 500m
        memory: 1Gi
        ephemeral-storage: 1Gi
        pid: "100"
```

- [ ] **Step 4: Add reservations to `defaults/roles/xl.yaml`**

Append a `kubelet:` block to the existing `machine:` section:

```yaml
  kubelet:
    # 64 GB / 32 CPU, ~56 pods per node.
    # New allocatable: 64350Mi - 2048 - 1024 - 500 = 60778Mi (requests 26912Mi = 44%).
    extraConfig:
      systemReserved:
        cpu: 1000m
        memory: 2Gi
        ephemeral-storage: 2Gi
        pid: "100"
      kubeReserved:
        cpu: 500m
        memory: 1Gi
        ephemeral-storage: 1Gi
        pid: "100"
```

- [ ] **Step 5: Leave a pointer in the three unused role files**

`defaults/roles/general.yaml`, `small.yaml` and `ingress.yaml` have no nodes today. Do not invent reservations for hardware that does not exist. Add this comment at the top of each, below the existing header comment:

```yaml
# No node currently uses this role. When one is created, add a machine.kubelet.extraConfig
# block with systemReserved/kubeReserved sized to that hardware — see defaults/roles/xl.yaml
# and defaults/roles/storage.yaml. The eviction tier in
# patches/common/kubelet-reservations.yaml applies to every node regardless.
```

- [ ] **Step 6: Render and verify the merge worked**

The critical risk is that a later patch replaces `extraConfig` instead of merging into it. Check explicitly:

```bash
cd /mnt/projects/lab/talos-cluster
./talos.sh build 2>&1 | tail -12
for n in fr01-cp-01 fr01-str-01 fr01-wrk-xl-01; do
  echo "##### $n"
  python3 -c "
import yaml,sys
d=[x for x in yaml.safe_load_all(open('clusters/feather-core/generated/machineconfigs/$n.yaml')) if x and 'machine' in x][0]
print(yaml.dump(d['machine']['kubelet']['extraConfig'], default_flow_style=False))"
done
```

Expected for each node: **both** the eviction keys (from the common patch) **and** the role's `systemReserved`/`kubeReserved` present in one map, with the role's numbers from the table above. If `evictionHard` is missing on a node that has `systemReserved`, the merge replaced rather than merged — stop and split the settings into a single per-role patch instead.

(This was pre-verified on 2026-08-03: `talosctl machineconfig patch` deep-merges two patches that both set `machine.kubelet.extraConfig`, and `talosctl validate -m metal` accepts the full block including `systemReserved`, `kubeReserved`, `evictionSoft`, `evictionSoftGracePeriod`, `evictionMinimumReclaim` and `evictionMaxPodGracePeriod`. The check above is a guard against patch-order regressions, not an open question.)

**Rollback:** no node has been touched by this task — it is file edits plus a render. `git checkout -- clusters/feather-core/talos/ && rm -f clusters/feather-core/talos/patches/common/kubelet-reservations.yaml && ./talos.sh build`. Verify with `grep -rc extraConfig clusters/feather-core/generated/machineconfigs/fr01-str-02.yaml` → `0`.

- [ ] **Step 7: Commit and open the PR**

```bash
git checkout main && git pull origin main
git checkout -b feat/talos-kubelet-reservations
# (re-apply the edits if you branched afterwards)
git add clusters/feather-core/talos/patches/common/kubelet-reservations.yaml \
        clusters/feather-core/talos/defaults/roles/
git commit -m "feat(talos): add role-specific kubelet reservations and an eviction tier"
git push -u origin feat/talos-kubelet-reservations
gh pr create --title "feat(talos): add role-specific kubelet reservations and an eviction tier" --body "$(cat <<'EOF'
## Summary
- New patches/common/kubelet-reservations.yaml: evictionHard memory.available 100Mi -> 500Mi,
  plus an evictionSoft tier (1Gi / 2m grace), evictionMinimumReclaim 500Mi and
  evictionMaxPodGracePeriod 60. nodefs/imagefs thresholds repeated verbatim because
  extraConfig replaces a map wholesale.
- Role-specific systemReserved/kubeReserved in defaults/roles/{controlplane,storage,xl}.yaml.
  Stock Talos gives 384Mi (512Mi on CP) systemReserved and no kubeReserved at all.

Headroom checked against live requests before writing: worst case is the storage nodes at
73% of the new allocatable (21434Mi of 29506Mi). No currently-scheduled pod stops fitting.

Applied node by node with --mode=no-reboot; the kubelet restarts, running pods do not.

## Test plan
- [x] ./talos.sh build -> 10x VALID
- [x] rendered extraConfig on cp/storage/xl contains BOTH the eviction tier and the role reservations
- [ ] apply-config --dry-run per node, then apply, then confirm allocatable via kubectl describe node
EOF
)"
```

---

### Task 12: Apply the reservations node by node

**Repo:** Talos repo. **Files:** none (operational).

**Interfaces:**
- Consumes: merged PR 4.
- Produces: the whole fleet running with reservations and a soft-eviction tier.

- [ ] **Step 1: Dry-run on the tightest node first**

Start with **`fr01-str-02` (192.168.15.8)** — it is the tightest node in the fleet (22006 Mi of requests against the new 29506 Mi allocatable, 75 %), so if anything is going to be a problem it shows up here. Re-run the per-node request measurement at the top of PR 4 first and start with whichever node is actually tightest today.

```bash
cd /mnt/projects/lab/talos-cluster
git checkout main && git pull origin main
./talos.sh gen-base && ./talos.sh build        # gen-base is a no-op if base/ survives; safe either way
export TALOSCONFIG=$PWD/clusters/feather-core/generated/talosconfig
talosctl -n 192.168.15.8 -e 192.168.15.10 apply-config --mode=no-reboot --dry-run \
  -f clusters/feather-core/generated/machineconfigs/fr01-str-02.yaml
```

> ⚠️ **The diff will contain more than the kubelet change, and that is expected — do not treat it as "the render carries an unrelated change".** Tasks 8–10 upgraded the nodes with `talosctl upgrade --image`, which never applies a rendered machineconfig. So this is the **first** apply that carries PR 3's file edits, and relative to what the node last had applied the diff can legitimately show all three of:
>
> 1. `machine.kubelet.extraConfig` — added (this PR).
> 2. `machine.install.extensions` — removed (PR 3 Task 7 Step 4).
> 3. `machine.install.image` — `:v1.13.4` → `:v1.13.7` (PR 3 Task 7 Step 3), *unless* the upgrade already rewrote it on the node.
>
> Anything **outside** that list means your render is stale — stop and re-check.
>
> `machine.install.*` only takes effect on the next install, so none of the three should require a reboot. **If the dry-run does report that a reboot is required, stop** — applying it would reboot a Ceph storage node outside the maintenance window. In that case, split the change: apply a config containing only the kubelet delta, or defer the whole of PR 4 into a window where a reboot is acceptable. Do not "just apply it and see".

Expected: the diff is a subset of the three items above, and no reboot is required.

- [ ] **Step 2: Apply to `fr01-str-02` and verify allocatable moved as predicted**

Note the record-before-you-change line: capture the pre-change allocatable so the rollback in this step has something to compare against.

```bash
kubectl get node fr01-str-02 -o jsonpath='{.status.allocatable.memory}{"  "}{.status.allocatable.cpu}{"\n"}'   # baseline: 32328296Ki  8
talosctl -n 192.168.15.8 -e 192.168.15.10 apply-config --mode=no-reboot \
  -f clusters/feather-core/generated/machineconfigs/fr01-str-02.yaml
sleep 45
kubectl get node fr01-str-02 -o jsonpath='{.status.allocatable.memory}{"  "}{.status.allocatable.cpu}{"\n"}'
kubectl get --raw /api/v1/nodes/fr01-str-02/proxy/configz | python3 -c "
import sys,json
c=json.load(sys.stdin)['kubeletconfig']
for k in ['systemReserved','kubeReserved','evictionHard','evictionSoft','evictionSoftGracePeriod','evictionMinimumReclaim']:
    print(k,'=',c.get(k))"
kubectl get pods -A --field-selector spec.nodeName=fr01-str-02 --no-headers | grep -c Pending
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph -s | head -3
```

Expected: allocatable memory ≈ `30214144Ki` (29506 Mi) and cpu `7000m` (baseline was `32328296Ki` / `8`); `systemReserved` and `kubeReserved` both populated with the storage-role numbers; `evictionSoft` no longer `None`; **`0` Pending pods**; Ceph `HEALTH_OK`. (`grep -c` returns `0` and exit status 1 when nothing matches — that is the pass case, not an error.)

**Gate:** if any pod on that node goes `Pending` with `Insufficient memory`, stop and roll back this node immediately. Take the pre-PR-4 commit from `git log` *before* you need it:

```bash
PRE_PR4=$(git rev-parse main~1)     # the commit before the reservations merge — VERIFY with: git show --stat $PRE_PR4
git checkout "$PRE_PR4" -- clusters/feather-core/talos/
./talos.sh build
talosctl -n 192.168.15.8 -e 192.168.15.10 apply-config --mode=no-reboot \
  -f clusters/feather-core/generated/machineconfigs/fr01-str-02.yaml
git checkout main -- clusters/feather-core/talos/
./talos.sh build                    # restore the renders to match main before touching another node
kubectl get node fr01-str-02 -o jsonpath='{.status.allocatable.memory}{"\n"}'   # back to 32328296Ki
```

The node's kubelet restarts back onto the old reservations within ~45 s and the Pending pod schedules again. Do **not** proceed to Step 3 on a partial rollback — leave the fleet consistent.

- [ ] **Step 3: Roll the remaining nine nodes, one at a time**

Order: `fr01-str-01` (.7) → `fr01-str-03` (.9) → `fr01-wrk-xl-01` (.5) → `-02` (.6) → `-03` (.15) → `-04` (.16) → `fr01-cp-01` (.10) → `fr01-cp-02` (.11) → `fr01-cp-03` (.12).

Control planes last: they have the most headroom (14 % utilisation of the new allocatable) and are the least likely to surprise you, so there is no value in taking the etcd-adjacent risk first.

```bash
talosctl -n <ip> -e <different-cp-ip> apply-config --mode=no-reboot \
  -f clusters/feather-core/generated/machineconfigs/<node>.yaml
sleep 45
kubectl get node <node> -o jsonpath='{.status.allocatable.memory}{"  "}{.status.allocatable.cpu}{"\n"}'
kubectl get pods -A --field-selector spec.nodeName=<node> | grep -c Pending
```

Expected per node: allocatable matches the table; `0` Pending. A brief `NotReady` while the kubelet restarts is expected — wait for `Ready` before the next node.

- [ ] **Step 4: Fleet gate**

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,ALLOC_MEM:.status.allocatable.memory,ALLOC_CPU:.status.allocatable.cpu'
kubectl get pods -A --field-selector=status.phase=Pending
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph -s | head -3
for n in $(kubectl get nodes -o name | cut -d/ -f2); do
  echo -n "$n "
  kubectl get --raw /api/v1/nodes/$n/proxy/configz \
    | python3 -c "import sys,json;c=json.load(sys.stdin)['kubeletconfig'];print(c['evictionHard']['memory.available'], c.get('evictionSoft'))"
done
```

Expected: allocatable matches the table for all three roles; no `Pending` pods; Ceph `HEALTH_OK`; every node reports `500Mi` and a non-null `evictionSoft`.

---

# PR 5 — Codify the operations that were just performed

Written **after** PR 3 and PR 4 so the wrappers encode commands that were actually run and verified, not guesses. Writing an untested `upgrade` wrapper *before* the fleet rollout and then using it for the riskiest operation in this plan would be exactly backwards.

### Task 13: Add `apply`, `upgrade` and `remove-node` wrappers to `talos.sh`

**Repo:** Talos repo. **Files:** Modify `talos.sh`, `Makefile`, `README.md`.

**Interfaces:**
- Consumes: the exact command forms proven in Tasks 6, 8, 9, 10 and 12.
- Produces: `./talos.sh apply|upgrade|remove-node` with `--dry-run`-by-default guardrails.

- [ ] **Step 1: Add the commands**

Insert after `cmd_build()` in `talos.sh`. Key design points, each derived from something this plan hit:

- `DRY_RUN` defaults to `1` — every wrapper prints what it would do and requires `CONFIRM=yes` to act.
- `upgrade` refuses to run against a control plane if any etcd member is unhealthy, and refuses two control planes in one invocation.
- The installer image is read from `patches/common/installer-secureboot.yaml`, never typed by hand.
- No `--preserve` anywhere — the flag does not exist in talosctl ≥1.8.

```bash
# Resolve a node name -> mgmt IP + role, from the node override files.
node_facts() {
  local name="$1" f
  f="$(find "$NODES_DIR" -type f -name "${name}.yaml" | head -1)"
  [ -n "$f" ] || die "Unknown node: $name"
  local role ip
  role="$(echo "${f#"$NODES_DIR"/}" | cut -d/ -f2)"
  ip="$(python3 -c "
import yaml,sys
d=yaml.safe_load(open('$f'))
print([i for i in d['machine']['network']['interfaces'] if i.get('interface')=='${MGMT_IFACE}'][0]['addresses'][0].split('/')[0])")"
  echo "$ip $role"
}

# All control-plane mgmt IPs, one per line.
all_cp_ips() {
  local f
  while IFS= read -r f; do
    python3 -c "
import yaml
d=yaml.safe_load(open('$f'))
print([i for i in d['machine']['network']['interfaces'] if i.get('interface')=='${MGMT_IFACE}'][0]['addresses'][0].split('/')[0])"
  done < <(find "$NODES_DIR" -path '*/controlplane/*.yaml' | sort)
}

# Pick a control-plane endpoint that is NOT the target node.
other_cp_endpoint() {
  local target="$1" f ip
  while IFS= read -r f; do
    ip="$(python3 -c "
import yaml
d=yaml.safe_load(open('$f'))
print([i for i in d['machine']['network']['interfaces'] if i.get('interface')=='${MGMT_IFACE}'][0]['addresses'][0].split('/')[0])")"
    [ "$ip" != "$target" ] && { echo "$ip"; return 0; }
  done < <(find "$NODES_DIR" -path '*/controlplane/*.yaml' | sort)
  die "No control-plane endpoint other than $target"
}

cmd_apply() {
  need talosctl; need python3
  [ -n "${NODE:-}" ] || die "Usage: ./talos.sh apply NODE=<name> [MODE=no-reboot] [CONFIRM=yes]"
  local ip role ep cfg mode
  read -r ip role < <(node_facts "$NODE")
  ep="$(other_cp_endpoint "$ip")"
  cfg="${GENERATED}/${NODE}.yaml"
  [ -f "$cfg" ] || die "Missing $cfg (run: ./talos.sh render-node NODE=${NODE})"
  mode="${MODE:-no-reboot}"
  if [ "${CONFIRM:-}" != "yes" ]; then
    info "DRY RUN (set CONFIRM=yes to apply): ${NODE} (${ip}, role=${role}) mode=${mode} via ${ep}"
    talosctl -n "$ip" -e "$ep" apply-config --mode="$mode" --dry-run -f "$cfg"
    return 0
  fi
  talosctl -n "$ip" -e "$ep" apply-config --mode="$mode" -f "$cfg"
}

cmd_upgrade() {
  need talosctl; need python3
  [ -n "${NODE:-}" ] || die "Usage: ./talos.sh upgrade NODE=<name> [CONFIRM=yes]"
  case "${NODE}" in *,*) die "One node at a time. Never two control planes." ;; esac
  local ip role ep img
  read -r ip role < <(node_facts "$NODE")
  ep="$(other_cp_endpoint "$ip")"
  img="$(sed -n 's#.*image:[[:space:]]*\(factory\.talos\.dev/.*\)#\1#p' \
         "${PATCHES_DIR}/common/installer-secureboot.yaml")"
  [ -n "$img" ] || die "Could not read the installer image from installer-secureboot.yaml"
  cmd_schematic   # never upgrade with a pin that does not match the committed schematic
  if [ "$role" = controlplane ]; then
    # `talosctl service etcd` exits 0 whenever it can REACH the node, healthy or not, and
    # `etcd members` has no health column at all (verified 2026-08-03: its columns are
    # NODE/ID/HOSTNAME/PEER URLS/CLIENT URLS/LEARNER). The only machine-readable health
    # signal is the HEALTH column of `talosctl services`. Require ALL control planes
    # Running+OK before taking one of three members down.
    local cp_ips ok total
    cp_ips="$(all_cp_ips | paste -sd, -)"
    ok="$(talosctl -e "$ep" -n "$cp_ips" services 2>/dev/null \
          | awk '$2=="etcd" && $3=="Running" && $4=="OK" {c++} END {print c+0}')"
    total="$(all_cp_ips | wc -l)"
    [ "$ok" = "$total" ] \
      || die "etcd healthy on ${ok}/${total} control planes — refusing to upgrade a control plane.
Check: talosctl -e ${ep} -n ${cp_ips} services | grep etcd"
    info "control plane: ${total} etcd members tolerate exactly ONE down. Confirm no other CP upgrade is in flight."
  fi
  if [ "${CONFIRM:-}" != "yes" ]; then
    info "DRY RUN (set CONFIRM=yes to run):"
    info "  talosctl -n ${ip} -e ${ep} upgrade --image ${img} --drain --drain-timeout 15m --wait"
    info "  (no --preserve: the flag does not exist in talosctl >=1.8; EPHEMERAL is preserved by default)"
    return 0
  fi
  talosctl -n "$ip" -e "$ep" upgrade --image "$img" --drain --drain-timeout 15m --wait
}

cmd_remove-node() {
  need talosctl; need kubectl
  [ -n "${NODE:-}" ] || die "Usage: ./talos.sh remove-node NODE=<name> CONFIRM=yes"
  local ip role ep
  read -r ip role < <(node_facts "$NODE")
  ep="$(other_cp_endpoint "$ip")"
  cat >&2 <<EOF
remove-node ${NODE} (${ip}, role=${role}) will:
  1. kubectl cordon ${NODE}
  2. kubectl drain ${NODE} --ignore-daemonsets --delete-emptydir-data
  3. talosctl -n ${ip} -e ${ep} reset --graceful --reboot   # WIPES THE NODE
  4. kubectl delete node ${NODE}
  5. rm the node file, then edit patches/common/cert-sans.yaml and re-run ./talos.sh build
Blocking PodDisruptionBudgets will stall step 2 — clear them first (kubectl delete pod is
not an eviction; use kubectl cnpg promote for a Postgres primary).
EOF
  [ "${CONFIRM:-}" = "yes" ] || { info "DRY RUN — set CONFIRM=yes to execute."; return 0; }
  kubectl cordon "$NODE"
  kubectl drain "$NODE" --ignore-daemonsets --delete-emptydir-data
  talosctl -n "$ip" -e "$ep" reset --graceful --reboot
  kubectl delete node "$NODE"
  info "Now: rm $(find "$NODES_DIR" -type f -name "${NODE}.yaml"), drop ${NODE} from patches/common/cert-sans.yaml, ./talos.sh build, and re-apply the control planes."
}
```

- [ ] **Step 2: Register them**

Add `apply upgrade remove-node` to `COMMANDS`, to the dispatch `case`, to `cmd_help` under a new `Operate (guarded — DRY RUN unless CONFIRM=yes):` heading, to `command_keys()` (`apply) echo "NODE MODE CONFIRM" ;;`, `upgrade|remove-node) echo "NODE CONFIRM" ;;`), and to both lists in the `Makefile` — the same five places Task 2 used.

⚠️ **There is a sixth place in the `Makefile`, and missing it is a silent footgun.** `Makefile:15-16` forwards only an explicit allow-list of variables:

```make
	@./talos.sh $@ $(foreach v,CLUSTER CLUSTER_ENDPOINT KUBERNETES_VERSION MGMT_IFACE \
	  EXTRA_RECIPIENTS FILE NAME SITE ROLE IP CIDR IFACE NODE,$(if $($(v)),$(v)=$($(v))))
```

`MODE` and `CONFIRM` are not in that list, so `make upgrade NODE=x CONFIRM=yes` would **drop `CONFIRM` and silently dry-run** — the operator sees a plan, believes the node was upgraded, and moves on. Add both:

```make
	@./talos.sh $@ $(foreach v,CLUSTER CLUSTER_ENDPOINT KUBERNETES_VERSION MGMT_IFACE \
	  EXTRA_RECIPIENTS FILE NAME SITE ROLE IP CIDR IFACE NODE MODE CONFIRM,$(if $($(v)),$(v)=$($(v))))
```

Verify with `make upgrade NODE=fr01-wrk-xl-04` (must dry-run) and confirm `CONFIRM=yes` reaches the script by checking the printed command, not by running it against a node.

Also extend the `NODE)` branch of `cmd___complete` — it already completes node names, so `apply`/`upgrade`/`remove-node` get completion for free once they are in `command_keys`.

- [ ] **Step 3: Verify without touching a node**

```bash
cd /mnt/projects/lab/talos-cluster
export TALOSCONFIG=$PWD/clusters/feather-core/generated/talosconfig   # REQUIRED: ~/.talos/config is empty
bash -n talos.sh && echo "SYNTAX OK"
./talos.sh apply NODE=fr01-wrk-xl-04
./talos.sh upgrade NODE=fr01-wrk-xl-04
./talos.sh remove-node NODE=fr01-wrk-xl-04
./talos.sh upgrade NODE=fr01-cp-01,fr01-cp-02 ; echo "exit=$?"
./talos.sh upgrade NODE=fr01-cp-01                                   # exercises the etcd health gate
```

Without the `TALOSCONFIG` export, `apply` and the control-plane `upgrade` fail with an empty-context error rather than dry-running — that is a missing environment variable, not a bug in the wrapper.

Expected: `SYNTAX OK`; `apply` prints a real `--dry-run` diff for 192.168.15.16 via a control-plane endpoint that is not itself; `upgrade NODE=fr01-wrk-xl-04` prints the schematic check plus the exact command it *would* run, with the v1.13.7 image read out of the pin file, and does not run it; `remove-node` prints the five-step plan and exits without acting; the comma form dies with `One node at a time. Never two control planes.` and a non-zero exit; `upgrade NODE=fr01-cp-01` additionally prints `control plane: 3 etcd members tolerate exactly ONE down` (proving the health gate ran and found 3/3 healthy) before its dry-run line.

**Rollback:** `git checkout -- talos.sh Makefile README.md`. Every wrapper is dry-run-by-default, so nothing reached a node during this task.

- [ ] **Step 4: Update the README**

Replace the `## Deploy` section's raw `talosctl apply-config` snippet with the wrapper, keeping the raw form as the documented escape hatch, and revise line 48 again now that the claim is closer to true:

```markdown
- **One entrypoint** — `./talos.sh help` covers rendering, validation, SOPS, the schematic
  check, and guarded day-2 operations (`apply`, `upgrade`, `remove-node`), which dry-run by
  default and require `CONFIRM=yes`.
```

- [ ] **Step 5: Commit and open the PR**

```bash
git checkout main && git pull origin main
git checkout -b feat/talos-operate-wrappers
git add talos.sh Makefile README.md
git commit -m "feat(talos): add guarded apply/upgrade/remove-node wrappers"
git push -u origin feat/talos-operate-wrappers
gh pr create --title "feat(talos): add guarded apply/upgrade/remove-node wrappers" --body "$(cat <<'EOF'
## Summary
Codifies the commands proven during the v1.13.7 rollout and the kubelet-reservation rollout
(docs/superpowers/plans/2026-08-03-talos-fleet-lifecycle.md, Tasks 6/8/9/10/12).

- Every wrapper DRY RUNS unless CONFIRM=yes.
- `upgrade` reads the installer image from installer-secureboot.yaml, runs ./talos.sh
  schematic first, refuses a comma-separated node list, and warns on control planes.
- Endpoints are always a control plane OTHER than the target node.
- No --preserve anywhere: the flag does not exist in talosctl >=1.8 and would fail.
- `remove-node` documents the PDB traps that stalled drains during the rollout.

## Test plan
- [x] bash -n talos.sh
- [x] apply/upgrade/remove-node all dry-run correctly against fr01-wrk-xl-04
- [x] `upgrade NODE=fr01-cp-01,fr01-cp-02` refuses with a non-zero exit
EOF
)"
```

---

# Optional follow-up (blocked on another theme)

### Task 14: Drop `kubelet-serving-cert-approver` from `extraManifests` once Flux owns it

**Repo:** Talos repo (edit) — but **gated on a GitOps-repo change owned by `lan-exposure-and-unmanaged-sniffer`.**

> Do **not** start this task until that theme's `infrastructure/base/controllers/kubelet-serving-cert-approver/` is deployed and `Ready`. Removing it from `extraManifests` while nothing else provides it silently stops kubelet serving-cert approval; `kubectl logs`, `kubectl exec` and metrics scraping rot on the next cert rotation, days later, with no obvious cause.

- [ ] **Step 1: Confirm the Flux-managed copy is live and owns the workload**

```bash
flux get kustomizations -A | grep -i controllers
kubectl -n kubelet-serving-cert-approver get deploy kubelet-serving-cert-approver \
  -o jsonpath='{.metadata.labels}{"\n"}'
kubectl -n kubelet-serving-cert-approver get pods
```

Expected: the Flux layer `READY=True`, the Deployment carrying Flux/Kustomize labels (`kustomize.toolkit.fluxcd.io/name`), and a `Running` pod.

- [ ] **Step 2: Remove only that line from `defaults/roles/controlplane.yaml`**

```yaml
  extraManifests:
    - https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.1/components.yaml
```

Leave metrics-server in place — it is not in that theme's scope.

- [ ] **Step 3: Apply and verify approval still happens**

```bash
cd /mnt/projects/lab/talos-cluster && ./talos.sh build
export TALOSCONFIG=$PWD/clusters/feather-core/generated/talosconfig

# node -> IP, and always drive the apply through a DIFFERENT control plane.
# (Do not derive the filename from the last IP octet — .10/.11/.12 map to
#  fr01-cp-01/02/03, not fr01-cp-010/011/012.)
talosctl -n 192.168.15.10 -e 192.168.15.11 apply-config --mode=no-reboot --dry-run \
  -f clusters/feather-core/generated/machineconfigs/fr01-cp-01.yaml
talosctl -n 192.168.15.11 -e 192.168.15.10 apply-config --mode=no-reboot --dry-run \
  -f clusters/feather-core/generated/machineconfigs/fr01-cp-02.yaml
talosctl -n 192.168.15.12 -e 192.168.15.10 apply-config --mode=no-reboot --dry-run \
  -f clusters/feather-core/generated/machineconfigs/fr01-cp-03.yaml
# inspect each diff — it must show ONLY the removed cert-approver URL — then re-run
# the same three commands without --dry-run, ONE node at a time, verifying between each.
sleep 120
kubectl -n kubelet-serving-cert-approver get pods
kubectl top nodes
kubectl get csr
```

Expected: the Deployment is still `Running` (now owned solely by Flux — Talos no longer re-applies it and, critically, no longer *prunes* it), `kubectl top nodes` returns metrics for all 10 nodes. As in Task 6, an empty `kubectl get csr` is normal, not a failure signal.

**Rollback:** restore the removed URL line, `./talos.sh build`, re-apply the three control-plane configs. Talos re-applies the manifest within one sync cycle.

---

## What could not be verified

- **Whether v1.13.7 fixes the containerd shim `task.Delete` hang** recorded on this cluster. containerd 2.2.5 is security-only; 2.2.6's teardown fixes (NRI `GetIPs` nil-deref, rejecting `CreateContainer` against a non-running sandbox, sandbox shutdown on `RunPodSandbox` hook failure) are adjacent but none matches that signature, and none of the upstream issues cited in the incident notes is listed as fixed. Take the upgrade on CVE and kubelet/volume-teardown grounds; treat any shim-hang improvement as a bonus, and do not close that incident on the strength of this plan.
- **Whether the SecureBoot UKI for `<schematic>:v1.13.7` boots on this hardware.** It cannot be tested without booting it. This is why Task 8 starts with one control plane, verifies, and stops — and why console access is a prerequisite. The schematic ID is proven identical, which removes the extension-drift risk but not the boot risk.
- **Where the SecureBoot signing keys for this schematic live.** `grep -rn 'secureboot\|SecureBoot\|\.pem\|signing' clusters/feather-core/talos/ docs/` turns up nothing beyond the installer pin, and `docs/ADOPTING.md:111-116` only points at `factory.talos.dev`. Image Factory's own keys are used for the public `metal-installer-secureboot` images, so no local key is needed for this upgrade — but if the cluster ever moves to custom-signed UKIs, that is undocumented and should be written down.
- **End-to-end kubelet serving-cert approval.** `kubectl get csr` is empty on this cluster (approved CSRs are garbage-collected), so approval can only be observed at the next rotation. Tasks 6 and 14 verify the *consequences* (`kubectl top nodes`, `kubectl logs`) instead, which is weaker but non-destructive.
- **Whether `talosctl apply-config --mode=no-reboot` accepts a `machine.install.image` change.** The plan avoids needing to know: the installer pin reaches the nodes through PR 3's `upgrade --image` and PR 4's post-upgrade renders, and every apply step begins with `--dry-run`, which reports the reboot requirement before anything happens.
- **Pod placement at execution time.** The blocking-PDB analysis (Harbor registry and n8n on `fr01-wrk-xl-02`, CNPG primary `feather-core-cluster-pg-2` on `fr01-wrk-xl-03`) is a 2026-08-03 snapshot. Task 10 Step 1 re-derives it rather than trusting it.
