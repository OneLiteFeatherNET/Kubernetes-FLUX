# CI as a Merge Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `flux-validate` and `pr-lint` from advisory post-hoc checks into a real merge gate, and make the check that gates actually validate what this repo is made of — CRDs, in-repo Helm charts, chart version bumps, and SOPS encryption.

**Architecture:** Five separately-mergeable PRs, ordered by risk. PR 1 is pure CI hygiene (tool checksums, `KUBERNETES_VERSION` 1.31.0 → 1.36.1, an empty-`PATHS` guard, SHA-pinned actions, `concurrency`/`timeout-minutes`) **and removes the `pull_request` `paths:` filter**, which both unblocks required checks later and makes PRs 2-4 trigger the workflow at all. PR 2 commits a CRD schema bundle generated from the live cluster and turns on `-strict`, converting ~220 previously-skipped objects per run into validated ones. PR 3 adds Helm chart rendering plus the `Chart.yaml` version-bump gate. PR 4 adds the SOPS plaintext-secret guard. PR 5 flips GitHub ruleset 4266694 — the only step that changes anybody's workflow.

**Tech Stack:** GitHub Actions, GitHub Repository Rulesets API, `bash`/`python3`, `kustomize` 5.7.1, `kubeconform` 0.7.0, `helm` 4.2.2, SOPS creation rules.

---

## Global Constraints

- Conventional Commits enforced by CI (`commitlint.config.mjs`): types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`; subject starts lowercase; header ≤100 chars. The PR title is linted too.
- `./scripts/validate.sh` must pass locally before every commit.
- **No manifest in this plan reaches the cluster.** Every change is under `.github/`, `scripts/`, `.schemas/`, `.gitignore`, `renovate.json`, or `helm/*/ci/`. There is one exception worth naming: PR 3 adds files *inside* the chart directories that the `helmcharts` GitRepository packages — see Task 6's cluster verification step for why that is inert and how to confirm it. That verification step is itself a cluster **write** (`flux reconcile` annotates the GitRepository), so it needs a working kubeconfig with write access, not just read.
- Renovate moves `main` under you — `git pull --rebase origin main` immediately before every push.
- Do not hammer `flux reconcile`. Only Task 6 touches the cluster's view at all, and it needs one `flux reconcile source git helmcharts` at most.
- Every command in this plan is run from the repo root.

---

## Prerequisites

- `gh` authenticated as a user with **admin** on `OneLiteFeatherNET/Kubernetes-FLUX`. Verified today: `gh api repos/OneLiteFeatherNET/Kubernetes-FLUX --jq .permissions` → `{"admin":true,...}`, and `gh api orgs/OneLiteFeatherNET/memberships/TheMeinerLP --jq .role` → `admin`. Token scopes `gist, project, read:org, repo, workflow` are sufficient for the ruleset PUT in Task 8.
- `kubectl` with context `admin@feather-core` for **Task 3** (read-only, `kubectl get crd`) and **Task 6 Step 6** (`kubectl get helmrelease` plus one `flux reconcile source git helmcharts`, which writes a reconcile annotation). Tasks 1, 2, 4, 5, 7 are fully offline; Tasks 8 and 9 need only `gh`.
- `flux` CLI on `PATH` for Task 6 Step 6.
- **Open Renovate PRs at the time of writing:** #52, #88, #89 change `helm/*/values.yaml` image tags without a `Chart.yaml` bump, and #87 edits `.github/workflows/flux-validate.yaml`. Read Decision Gate D and the note in Task 2 Step 1 before you start — both affect what you can merge afterwards.
- `jq` (present at `/usr/bin/jq`), `python3` with PyYAML, `curl`, `sha256sum`, `git`.
- `helm` is only needed locally if you want to run `./scripts/validate-helm.sh` outside CI — the script downloads its own pinned copy.

## Cross-theme dependencies

| Relationship | Detail |
|---|---|
| **Runs after** `ceph-capacity-reclamation-and-retention` | That theme is the P0 capacity cliff. Do not put a merge gate in front of an operator who is fighting a filling Ceph cluster. PRs 1–4 of this plan are inert and can land any time; **PR 5 (the actual gate) must wait until the capacity work is closed out.** |
| **Coordinates with** `flux-release-control-and-convergence` (`docs/superpowers/plans/2026-08-03-flux-release-control-and-convergence.md`) | That plan edits `renovate.json` (Flux manager file patterns, packageRules) and deletes `helm/metabase` plus 12 unreferenced base dirs. This plan also edits `renovate.json` (one line, Task 2). **Whichever lands second rebases.** `scripts/validate-helm.sh` discovers charts with `find helm -mindepth 2 -maxdepth 2 -name Chart.yaml`, so deleting `helm/metabase` needs no change here. |
| **Coordinates with** `sops-key-custody-and-rotation-hygiene` (`docs/superpowers/plans/2026-08-03-sops-key-custody-and-rotation-hygiene.md`) | That plan deletes `clusters/feather-core/.sops.yaml` and may delete the field-level `.*\.yaml$` creation rule from `.sops.yaml`. `scripts/check-sops-encryption.py` (Task 7) is written to survive both: it only considers creation rules **without** an `encrypted_regex`, and its allowlist entry for `clusters/feather-core/.sops.yaml` becomes harmlessly dead if that file is removed. **After any `.sops.yaml` edit in either plan, re-run `python3 scripts/check-sops-encryption.py`.** |
| **Blocks nothing** | No other theme depends on this one landing. |

---

## Decision gates

These need a human. Do not silently pick.

### Gate A — Task 8: does the merge gate keep the existing bypass actors?

Ruleset 4266694 currently has `bypass_actors: [{OrganizationAdmin, always}, {RepositoryRole 2, always}]` and the API reports `current_user_can_bypass: "always"` for TheMeinerLP, who is an org admin. **A `pull_request` rule with an `always` bypass for org admins does not apply to the one person who does all the pushing.** Adding the rules while keeping those actors changes nothing technically.

| Option | Effect | Break-glass |
|---|---|---|
| **A1 — keep both bypass actors as-is** | Gate is advisory-by-convention. PRs get checked; `git push origin main` still works exactly as today. | Nothing to do — the bypass *is* the break-glass. |
| **A2 — remove both bypass actors (recommended)** | Real gate: direct push to `main` is rejected for everyone, PRs cannot merge red. | `gh api -X PUT repos/OneLiteFeatherNET/Kubernetes-FLUX/rulesets/4266694 --input <(jq '.enforcement="disabled"' /tmp/ruleset-4266694.backup.json)` — one command, reversible, and it lands in the org audit log, which an invisible bypass does not. |
| **A3 — keep bypass, `enforcement: "evaluate"`** | Dry-run mode; results reported, nothing blocked. Useful for one week if A2 feels too abrupt. | N/A |

**Recommendation: A2**, with the disable command written into `CLAUDE.md` (Task 9) so it is findable at 3am. A1 is a legitimate choice if the risk of being locked out of an emergency push outweighs the gate — but be honest that A1 buys almost nothing over today.

### Gate B — Task 8: which check contexts are required?

Verified contexts reported today on PR head commits (`gh api repos/.../commits/312cf776.../check-runs`):

| Context | App | Present since |
|---|---|---|
| `kustomize build + kubeconform` | github-actions (15368) | today |
| `Conventional PR title` | github-actions (15368) | today |
| `Conventional commits` | github-actions (15368) | today |
| `GitGuardian Security Checks` | GitGuardian (46505) | today, PRs only |
| `helm lint + template` | github-actions (15368) | **PR 3 of this plan** |
| `chart version bump` | github-actions (15368) | **PR 3 of this plan** |
| `sops encryption` | github-actions (15368) | **PR 4 of this plan** |

⚠️ Before requiring `chart version bump`, resolve **Decision Gate D** — as required, it makes every Renovate chart-image PR unmergeable.

**Recommendation:** require the six github-actions contexts. Leave `GitGuardian Security Checks` **not required** — it is a third-party app whose outage would block all merges, and Task 7's `sops encryption` check covers this repo's actual exposure shape (a creation-rule-matched file committed in the clear) more precisely than a generic scanner. Requiring it is a defensible alternative; decide explicitly.

### Gate C — Task 3: how does the CRD schema bundle get into CI?

The bundle is 2.2 MB across 132 files.

| Option | Trade-off |
|---|---|
| **Commit `.schemas/` to the repo (recommended)** | CI stays offline and hermetic; no kubeconfig in CI (the cluster is not reachable from GitHub runners anyway). Cost: 2.2 MB in the tree and a regeneration commit whenever an operator upgrade changes a CRD. Output is byte-deterministic (verified: two consecutive generations `diff -r` clean), so no-op regenerations produce empty diffs. |
| Generate in CI from the live cluster | Impossible — GitHub-hosted runners cannot reach `feather-core`. Would require a self-hosted runner. |
| Use the `datreeio/CRDs-catalog` | Already rejected in `scripts/validate.sh:25-29` for good reason (`additionalProperties:false` baked in, lags upstream). Do not revisit. |

**Recommendation: commit the bundle.** If the 2.2 MB is unacceptable, the fallback is to keep `-ignore-missing-schemas` without a bundle (i.e. skip PR 2 entirely) and accept that ~57% of the repo stays unvalidated. There is no cheap middle option: restricting the bundle to groups used in the repo saves only 876 KB of 7.1 MB pre-strip and adds a maintenance trap.

### Gate D — Task 6 + Task 8: the `chart version bump` check blocks every Renovate chart-image PR

⚠️ **This is the one place where this plan can wedge the repo, and it is not hypothetical.** Renovate's `helm-values` manager bumps the image tag inside `helm/<chart>/values.yaml` and touches nothing else. Verified against real history and against the open queue with the exact script from Task 6:

```
$ ./scripts/check-chart-version.sh 37eb3a1^ 37eb3a1   # PR #88, outline 1.9.2
::error file=helm/outline/Chart.yaml::helm/outline: templates/ or values.yaml changed but Chart.yaml version is still 0.5.1
exit=1
$ ./scripts/check-chart-version.sh 9b25200^ 9b25200   # PR #89, micronaut OTel agent 2.30.0  -> exit=1
$ ./scripts/check-chart-version.sh d6cbf85^ d6cbf85   # PR #52, leantime 3.9.8               -> exit=1
```

Three PRs are open in that shape **right now** (#52, #88, #89). Renovate cannot bump `Chart.yaml` itself, so once `chart version bump` is a required check and `bypass_actors` is empty (A2), those PRs and every future one like them are permanently unmergeable and the image updates stop flowing. The check is not wrong — an unbumped `values.yaml` image change genuinely does not reach the cluster — but the plan must say what the operator does about it.

| Option | Effect | Cost |
|---|---|---|
| **D1 — add the Chart.yaml bump to the Renovate branch by hand (recommended)** | Correct: the image change actually lands on the cluster. Workflow: `gh pr checkout <n>`, edit `helm/<chart>/Chart.yaml` patch version, `git commit -m "chore: bump <chart> chart for the image update" && git push`. Check goes green. | One manual commit per chart-image PR. ~1 per fortnight at the current rate. |
| **D2 — require `chart version bump` only after `flux-release-control-and-convergence` has switched the in-repo charts to `reconcileStrategy: Revision`** | Removes the whole failure class; the check then only guards a convention, not correctness, and a Renovate PR that skips the bump is harmless. | Sequences PR 5 behind another theme. |
| **D3 — leave `chart version bump` reporting but NOT required** | Renovate keeps flowing; the check is advisory, which is what this plan is trying to stop being. | Weakest. |
| D4 — exempt image-tag-only `values.yaml` diffs from the check | Reintroduces the exact footgun for the case that has already bitten (`5ecea51`, `e7466ed`). Do not. | — |

**Recommendation: D1**, and write the two-command fixup into `CLAUDE.md` in Task 9 so the next person is not stuck. If the maintainer will not accept a manual commit per Renovate chart PR, pick **D3** for `chart version bump` only (keep the other five contexts required) and revisit after D2 lands.

**Resolve this gate before Task 6 Step 5 (opening PR 3), not at Task 8** — under D3 the job still gets added, it just is not listed in Task 8 Step 3's `required_status_checks`.

---

## What this plan deliberately does NOT do

- **`flux-local test` / `flux-local diff`.** The audit suggests it and it would catch a real class of error (HelmRelease values resolved against the actual chart). It is deferred because it needs network access to every `HelmRepository`/`OCIRepository` at CI time, pushes the job from ~30s to minutes, and would start red against ~18 floating chart ranges that `flux-release-control-and-convergence` is about to pin. Revisit **after** that theme lands, when chart resolution is deterministic.
- **`reconcileStrategy: Revision` on the git-sourced HelmCharts.** The audit's verify pass correctly identified this as the structural root cause of the `Chart.yaml` footgun (`grep -rn reconcileStrategy apps infrastructure` → 0 matches). It is a cluster-manifest change to 7 HelmReleases and belongs to `flux-release-control-and-convergence`, which owns `flux-gitops/inrepo-charts-chartversion-strategy`. Task 6's CI check is the belt; that is the braces. Do not do it here.
- **Dropping `-ignore-missing-schemas`.** Verified impossible today: removing it fails with 42 `could not find schema for CustomResourceDefinition` errors, because kubeconform's default catalog ships no `CustomResourceDefinition` schema at *any* Kubernetes version (reproduced at both 1.31.0 and 1.36.1 on a single CRD extracted from `gotk-components.yaml`). The audit's recommendation to drop it is not actionable. The flag stays, with the reason written into the code comment.
- **Adding `paths:` to the `push: branches: [main]` trigger.** The audit suggests it once the PR gate exists. Left alone on purpose: the post-merge run on `main` is the last line of defence on the bypass path, and it costs 30 seconds.
- **Pre-commit hooks.** A local hook is unenforceable on a repo whose owner can bypass it, and duplicates the CI checks. The CI check is the guard.
- **Closing the sanitizer's blind spot** (`ci-pipelines/kubeconform-blind-spots` sub-point 4). `infrastructure/clusters/feather-core/rook/kustomization.yaml:5` lists `secrets.sops.yaml` as a **resource** and `.../step-certificates/kustomization.yaml:6` lists one as a **patch**; `scripts/validate.sh:85-128` strips both, so those two overlays are validated with a hole in them. Closing it needs either a decryption key in CI (rejected — the whole point of SOPS is that CI does not have one) or a stub-substitution pass in the sanitizer, which is a materially bigger change than anything else here. PR 2's `-strict` + bundle narrows the hole to those two overlays' encrypted objects only. Left for a follow-up.
- **Checking `clusters/feather-core/.sops.yaml`'s creation rules.** `scripts/check-sops-encryption.py` reads only the root `.sops.yaml`, whose whole-file rule is a strict superset of the cluster-local one (the drift the audit found is that the cluster-local file is *missing* suffixes). So the check is never weaker for reading only the root file, and `sops-key-custody-and-rotation-hygiene` is about to delete the cluster-local file anyway. Do not duplicate the rule-drift check here.
- **Enabling GitHub secret scanning / push protection.** `gh api repos/... --jq .security_and_analysis` reports all three disabled. Enabling them is a two-click settings change with real value, but it is repository-settings work owned by `crown-jewel-rotation-leaked-pki-and-credentials`, not a CI change.
- **Any change to what CI validates *semantically*** — no policy engine (Kyverno/Conftest), no image-provenance admission. Those are separate themes.

---

### Task 1: Extract shared CI tooling, checksum the downloads, bump `KUBERNETES_VERSION` (PR 1, commit 1)

**Files:**
- Create: `scripts/lib/ci-tools.sh`
- Modify: `scripts/validate.sh` (lines 7-36, and after the `mapfile` at line 70)

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `scripts/lib/ci-tools.sh` exporting `KUBERNETES_VERSION`, `KUBECONFORM_COMMON`, `install_kustomize`, `install_kubeconform`, `install_helm` — consumed by Task 4 (`-schema-location`), Task 5 (`validate-helm.sh`) and Task 6.

- [ ] **Step 1: Create the PR 1 branch**

```bash
git checkout main
git pull --rebase origin main
git checkout -b ci/validate-sh-hardening
```

- [ ] **Step 2: Create `scripts/lib/ci-tools.sh`**

The two sha256 values below were verified against the upstream published checksum files today (`.../kustomize%2Fv5.7.1/checksums.txt` and `.../kubeconform/releases/download/v0.7.0/CHECKSUMS`), not just computed from a download. The helm entry is unused until Task 5 but belongs in the same file.

```bash
mkdir -p scripts/lib
```

Create `scripts/lib/ci-tools.sh`:

```bash
#!/usr/bin/env bash
# Shared CI tooling for scripts/validate.sh and scripts/validate-helm.sh.
# Pinned versions + sha256 checksums (from each release's published
# checksums file). KUBERNETES_VERSION is the single source of truth for the
# API version every check validates against; keep it equal to the live
# cluster (`kubectl version -o json | jq -r .serverVersion.gitVersion`).

KUSTOMIZE_VERSION="${KUSTOMIZE_VERSION:-5.7.1}"
KUSTOMIZE_SHA256="ea375e7372f9aa029129d4b2d16c66b7750b7f1213c4f66f910d981c895818d8"
KUBECONFORM_VERSION="${KUBECONFORM_VERSION:-0.7.0}"
KUBECONFORM_SHA256="c31518ddd122663b3f3aa874cfe8178cb0988de944f29c74a0b9260920d115d3"
HELM_VERSION="${HELM_VERSION:-4.2.2}"
HELM_SHA256="9adafecab4d406853bba163a70e9f104f47dbbf65ce24b7653bae7e36150bcb6"
KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.36.1}"

# Only the upstream Kubernetes schemas for now. Community CRD catalogs
# (datreeio) lag upstream and bake additionalProperties:false into every
# schema, so valid CRD fields like CNPG spec.affinity.topologySpreadConstraints
# fail regardless of -strict. -ignore-missing-schemas means CRDs are skipped
# rather than rejected.
KUBECONFORM_COMMON=(
  -ignore-missing-schemas
  -kubernetes-version "${KUBERNETES_VERSION}"
  -skip Secret
  -summary
  -schema-location default
)

# fetch_tool <url> <sha256> <dest-dir> [tar member ...]
fetch_tool() {
  local url="$1" sha="$2" dir="$3"; shift 3
  local tmp; tmp="$(mktemp)"
  curl -fsSL -o "${tmp}" "${url}"
  echo "${sha}  ${tmp}" | sha256sum -c - >/dev/null
  tar -xzf "${tmp}" -C "${dir}" "$@"
  rm -f "${tmp}"
}

install_kustomize() {
  fetch_tool "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
    "${KUSTOMIZE_SHA256}" "$1" kustomize
  chmod +x "$1/kustomize"
}

install_kubeconform() {
  fetch_tool "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" \
    "${KUBECONFORM_SHA256}" "$1" kubeconform
  chmod +x "$1/kubeconform"
}

install_helm() {
  fetch_tool "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" \
    "${HELM_SHA256}" "$1" linux-amd64/helm
  mv "$1/linux-amd64/helm" "$1/helm"
  rmdir "$1/linux-amd64"
  chmod +x "$1/helm"
}
```

- [ ] **Step 3: Rewrite the head of `scripts/validate.sh`**

Replace lines 7-36 (from `KUSTOMIZE_VERSION="${KUSTOMIZE_VERSION:-5.7.1}"` through the closing `)` of the `KUBECONFORM_COMMON=(` block, inclusive of the blank line after it) with:

```bash
source "$(dirname "$0")/lib/ci-tools.sh"

# Minimum number of Flux Kustomization spec.path values discovery must find.
# The repo has 12 layers; the floor catches a partial or empty discovery
# instead of silently validating nothing.
MIN_FLUX_PATHS="${MIN_FLUX_PATHS:-10}"

BIN_DIR="$(mktemp -d)"
trap 'rm -rf "${BIN_DIR}"' EXIT
export PATH="${BIN_DIR}:${PATH}"

echo "::group::Install tooling"
install_kustomize "${BIN_DIR}"
install_kubeconform "${BIN_DIR}"
kustomize version
kubeconform -v
echo "::endgroup::"
```

The `rc=0` line and everything below it is unchanged. Net effect: `KUSTOMIZE_VERSION`/`KUBECONFORM_VERSION`/`KUBERNETES_VERSION`, the two unverified `curl … | tar -xz` pipes, and the `KUBECONFORM_COMMON` array all move into the sourced library; `KUBERNETES_VERSION` goes from `1.31.0` to `1.36.1` in the process.

- [ ] **Step 4: Add the empty-`PATHS` guard**

Immediately after the closing `)` of the `mapfile -t PATHS < <( … )` block (currently `scripts/validate.sh:70`) and before the `# Mirror the repo into a tmp dir` comment, insert:

```bash

if [[ ${#PATHS[@]} -eq 0 ]]; then
  echo "::error::no Flux Kustomization paths discovered in clusters/feather-core/*.yaml"
  exit 1
fi
if [[ ${#PATHS[@]} -lt ${MIN_FLUX_PATHS} ]]; then
  echo "::error::discovered only ${#PATHS[@]} Flux path(s), expected at least ${MIN_FLUX_PATHS}"
  exit 1
fi
echo "discovered ${#PATHS[@]} Flux Kustomization path(s)"
```

- [ ] **Step 5: Verify the guard fires**

```bash
MIN_FLUX_PATHS=99 ./scripts/validate.sh 2>&1 | grep -E "discovered only|::error"; echo "exit=${PIPESTATUS[0]}"
```

Expected:

```
::error::discovered only 12 Flux path(s), expected at least 99
exit=1
```

Do **not** verify this with `>/dev/null 2>&1; echo $?` — a failed tool download also exits 1 and would read as a pass. The grep must show the guard's own message. If it prints `exit=0`, the guard is in the wrong place — it must be after the `mapfile`, not inside the heredoc.

- [ ] **Step 6: Run full validation at the new Kubernetes version**

```bash
./scripts/validate.sh 2>&1 | grep -E "discovered|Summary"
```

Expected (verified today against this exact tree — the 1.31.0 → 1.36.1 bump is a no-op for correctness, every schema resolves):

```
Summary: 44 resources found in 13 files - Valid: 21, Invalid: 0, Errors: 0, Skipped: 23
discovered 12 Flux Kustomization path(s)
Summary: 16 resources found parsing stdin - Valid: 4, Invalid: 0, Errors: 0, Skipped: 12
… 11 more stdin summaries, all Invalid: 0, Errors: 0 …
```

Script exits `0`. **Rollback if any group reports `Invalid` or `Errors`:** set `KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.31.0}"` in `scripts/lib/ci-tools.sh` and re-run; if that clears it, the bump surfaced a real API incompatibility — record which object and fix the manifest rather than reverting the bump permanently.

- [ ] **Step 7: Commit**

```bash
git add scripts/lib/ci-tools.sh scripts/validate.sh
git commit -m "ci: checksum ci tooling, guard empty path discovery, target k8s 1.36.1"
```

---

### Task 2: Workflow hygiene — drop the `paths:` filter, SHA pins, credentials, concurrency, timeouts (PR 1, commit 2)

**Files:**
- Modify: `.github/workflows/flux-validate.yaml`
- Modify: `.github/workflows/pr-lint.yaml`
- Modify: `renovate.json`

**Interfaces:**
- Consumes: nothing from Task 1 (independent edit, same PR)
- Produces: SHA-pinned actions that Renovate keeps current; `concurrency`/`timeout-minutes` on both workflows; an unfiltered `pull_request` trigger that Tasks 3-7 and Task 8's required checks both depend on

All five SHAs below were resolved today via `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`.

⚠️ **Renovate PR #87 (`chore(deps): update actions/setup-python action to v7`) is open and edits this same file.** Close it (`gh pr close 87 --comment "superseded by the SHA pin in <this PR>"`) or expect a conflict. The pin below is already v7.0.0 for that reason.

- [ ] **Step 1: Drop the `paths:` filter, pin actions, harden `flux-validate.yaml`**

Replace **lines 5-15** of `.github/workflows/flux-validate.yaml` (the whole `on:` block, from `on:` through `branches: [main]`) with:

```yaml
# No `paths:` filter on pull_request: these jobs become required status
# checks in stage 5, and a filtered-out workflow never reports a result,
# which leaves docs-only PRs blocked on "Expected — waiting for status".
# It also means every stage of this plan is exercised on its own PR — the
# old filter did not list scripts/lib/** or .schemas/**, so PR 2 would not
# have triggered this workflow at all. The full run is ~30s.
on:
  pull_request:
  push:
    branches: [main]
```

Then add a `concurrency:` block after the `permissions:` block and rewrite the `steps:` of the `validate` job:

```yaml
permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  validate:
    name: kustomize build + kubeconform
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: '3.x'
      - name: Install PyYAML
        run: pip install --quiet pyyaml
      - name: Validate Flux manifests
        run: ./scripts/validate.sh
```

`cancel-in-progress` is deliberately false on `push` — a cancelled post-merge run on `main` would leave the last line of defence unreported.

**Why the `paths:` removal is in PR 1 and not PR 4:** with the old filter, PR 2 (which touches only `scripts/gen-crd-schemas.py`, `scripts/lib/ci-tools.sh` and `.schemas/**` — none of them listed) would not run `flux-validate` at all, so the riskiest change in this plan (`-strict`) would land on `main` never having been checked on a PR. Removing the filter first makes every later stage self-verifying.

- [ ] **Step 2: Harden `pr-lint.yaml`**

Add the same `concurrency:` block after `permissions:` (lines 9-11), then pin and harden both jobs:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  pr-title:
    name: Conventional PR title
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: amannn/action-semantic-pull-request@48f256284bd46cdaab1048c3721360e808335d50 # v6.1.1
```

(everything under that step's `env:`/`with:` is unchanged)

```yaml
  commits:
    name: Conventional commits
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0
        with:
          node-version: 24
```

(the `Install commitlint` and `Lint commits in range` steps are unchanged — commitlint reads history from the working tree, it never uses the token, so `persist-credentials: false` is safe)

- [ ] **Step 3: Keep the pins current via Renovate**

In `renovate.json`, add the canonical digest-pinning preset:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    "config:recommended",
    "helpers:pinGitHubActionDigests"
  ]
}
```

⚠️ `flux-release-control-and-convergence` also edits this file. If that plan has already landed, add only the `"helpers:pinGitHubActionDigests"` array entry to whatever `extends` list exists — do not overwrite the file.

- [ ] **Step 4: Verify the workflow files still parse**

```bash
python3 -c "import yaml,sys; [yaml.safe_load(open(f)) for f in sys.argv[1:]]; print('yaml ok')" \
  .github/workflows/flux-validate.yaml .github/workflows/pr-lint.yaml
python3 -c "import json; json.load(open('renovate.json')); print('json ok')"
```

Expected: `yaml ok` then `json ok`. Also confirm the `paths:` filter is gone:

```bash
python3 -c "
import yaml; d=yaml.safe_load(open('.github/workflows/flux-validate.yaml'))
print('paths filter:', (d[True]['pull_request'] or {}).get('paths', 'NONE'))"
```

Expected: `paths filter: NONE`. (`d[True]` is not a typo — PyYAML parses the bare key `on:` as the boolean `True`.)

**Rollback for this task:** `git checkout main -- .github/workflows/flux-validate.yaml .github/workflows/pr-lint.yaml renovate.json`. Nothing here has landed on the cluster or on `main` yet.

- [ ] **Step 5: Commit, push, open PR 1**

```bash
git add .github/workflows/flux-validate.yaml .github/workflows/pr-lint.yaml renovate.json
git commit -m "ci: pin actions to shas, drop paths filter, add concurrency and timeouts"
git pull --rebase origin main
git push -u origin ci/validate-sh-hardening
gh pr create --title "ci: harden validate.sh and both workflows" --body "$(cat <<'EOF'
## Summary
- Stage 1 of docs/superpowers/plans/2026-08-03-ci-as-a-merge-gate.md
- scripts/lib/ci-tools.sh: pinned versions + sha256-verified downloads, one KUBERNETES_VERSION variable (1.31.0 -> 1.36.1, matching live v1.36.1)
- scripts/validate.sh: fail loudly if Flux path discovery yields fewer than MIN_FLUX_PATHS (10) paths instead of exiting 0 having built nothing
- Workflows: SHA-pinned actions, persist-credentials:false, concurrency + timeout-minutes
- flux-validate: dropped the pull_request `paths:` filter so every PR (including the later stages of this plan, which touch scripts/lib and .schemas) is actually checked

## Test plan
- [x] ./scripts/validate.sh exits 0 with 12 paths discovered, all groups Invalid: 0, Errors: 0
- [x] MIN_FLUX_PATHS=99 ./scripts/validate.sh exits 1
- [ ] flux-validate green on this PR
EOF
)"
```

Merging is an operator decision — do not merge automatically.

---

### Task 3: Generate and commit the CRD schema bundle (PR 2, commit 1)

⚠️ **This task requires cluster access** (`kubectl` against `admin@feather-core`). It is read-only (`kubectl get crd`). No other task in this plan needs a kubeconfig.

**Files:**
- Create: `scripts/gen-crd-schemas.py`
- Create: `.schemas/` (132 JSON files, ~2.2 MB, across 23 group directories)
- Create: `.schemas/README.md`

**Interfaces:**
- Consumes: merged PR 1 (`scripts/lib/ci-tools.sh` must exist)
- Produces: `.schemas/<group>/<kind>_<version>.json` laid out for the `-schema-location` template Task 4 adds

**Gate:** PR 1 must be merged and green before starting. Confirm with `gh run list --workflow flux-validate.yaml --branch main --limit 1` → the top row shows `completed  success`.

- [ ] **Step 1: Create the PR 2 branch**

```bash
git checkout main
git pull --rebase origin main
git checkout -b ci/crd-schema-bundle
```

- [ ] **Step 2: Create `scripts/gen-crd-schemas.py`**

```python
#!/usr/bin/env python3
"""Regenerate .schemas/ — a kubeconform schema bundle built from the live cluster's CRDs.

Usage (from the repo root, with a kubeconfig pointing at feather-core):

    kubectl get crd -o json | python3 scripts/gen-crd-schemas.py .schemas

Layout matches the -schema-location template in scripts/lib/ci-tools.sh:
    .schemas/<group>/<kind-lowercase>_<version>.json

`description` fields are stripped (they are ~70% of the bytes and play no
part in validation) and the JSON is written compact and key-sorted so
regenerating against an unchanged cluster produces a byte-identical tree.
"""
import json
import os
import shutil
import sys


def strip_descriptions(node):
    if isinstance(node, dict):
        return {k: strip_descriptions(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [strip_descriptions(v) for v in node]
    return node


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: kubectl get crd -o json | gen-crd-schemas.py <out-dir>\n")
        return 2
    out_dir = sys.argv[1]
    crds = json.load(sys.stdin).get("items", [])
    if not crds:
        sys.stderr.write("error: no CRDs on stdin — is the kubeconfig pointing at feather-core?\n")
        return 1

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    written = 0
    for crd in crds:
        spec = crd["spec"]
        group = spec["group"]
        kind = spec["names"]["kind"].lower()
        for version in spec.get("versions", []):
            schema = (version.get("schema") or {}).get("openAPIV3Schema")
            if not schema:
                continue
            schema = strip_descriptions(schema)
            schema["$schema"] = "http://json-schema.org/draft-07/schema#"
            group_dir = os.path.join(out_dir, group)
            os.makedirs(group_dir, exist_ok=True)
            path = os.path.join(group_dir, f"{kind}_{version['name']}.json")
            with open(path, "w") as fh:
                json.dump(schema, fh, separators=(",", ":"), sort_keys=True)
                fh.write("\n")
            written += 1
    sys.stderr.write(f"wrote {written} CRD schema(s) from {len(crds)} CRD(s) to {out_dir}/\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`shutil.rmtree(out_dir)` means a CRD removed from the cluster disappears from the bundle. That is intentional — the bundle mirrors the cluster. It also means **do not point the script at a directory that contains anything else.**

- [ ] **Step 3: Generate the bundle**

```bash
kubectl config current-context   # expect: admin@feather-core
kubectl get crd -o json | python3 scripts/gen-crd-schemas.py .schemas
```

Expected stderr (verified today):

```
wrote 132 CRD schema(s) from 117 CRD(s) to .schemas/
```

If the count differs materially from 132/117, the cluster has changed since this plan was written — that is fine, record the new numbers, but sanity-check that all expected groups are present:

```bash
ls .schemas
```

Expected to include at minimum: `helm.toolkit.fluxcd.io`, `kustomize.toolkit.fluxcd.io`, `source.toolkit.fluxcd.io`, `ceph.rook.io`, `postgresql.cnpg.io`, `k8s.mariadb.com`, `cert-manager.io`, `gateway.networking.k8s.io`, `monitoring.coreos.com`, `objectbucket.io`, `metallb.io`, `dragonflydb.io`.

- [ ] **Step 4: Confirm the output is deterministic**

```bash
cp -a .schemas /tmp/schemas-first
kubectl get crd -o json | python3 scripts/gen-crd-schemas.py .schemas
diff -r /tmp/schemas-first .schemas && echo DETERMINISTIC
rm -rf /tmp/schemas-first
```

Expected: `DETERMINISTIC`. If it prints differences, something in the generator is unordered — do not commit a bundle that churns on every regeneration.

- [ ] **Step 5: Write `.schemas/README.md`**

```markdown
# CRD schema bundle

kubeconform schemas for every CustomResourceDefinition installed on
`feather-core`, consumed by `scripts/validate.sh` and
`scripts/validate-helm.sh` via the `-schema-location` template in
`scripts/lib/ci-tools.sh`.

Generated — do not hand-edit. Regenerate after any operator upgrade that
ships new or changed CRDs (Rook, CNPG, mariadb-operator, cert-manager,
Flux, prometheus-operator, Gateway API):

    kubectl get crd -o json | python3 scripts/gen-crd-schemas.py .schemas
    ./scripts/validate.sh

`description` fields are stripped and JSON is written compact and
key-sorted, so a regeneration against an unchanged cluster produces an
empty diff.

Not covered: `CustomResourceDefinition` itself. kubeconform's default
catalog ships no schema for it at any Kubernetes version, which is why
`-ignore-missing-schemas` stays in `KUBECONFORM_COMMON`.
```

- [ ] **Step 6: Commit the bundle separately from the wiring**

```bash
git add scripts/gen-crd-schemas.py .schemas
git commit -m "ci: add crd schema bundle generated from the live cluster"
```

Keeping the 2.2 MB bundle in its own commit means Task 4's wiring can be reviewed as a two-line diff.

---

### Task 4: Wire the bundle in and enable `-strict` (PR 2, commit 2)

**Files:**
- Modify: `scripts/lib/ci-tools.sh` (the comment block and `KUBECONFORM_COMMON`)

**Interfaces:**
- Consumes: `.schemas/` from Task 3
- Produces: a `KUBECONFORM_COMMON` that validates CRD-typed objects, used by Task 5's helm job as well

**Loud warning:** this is the step the audit predicted would need a fix-up pass. It did not, on this tree, today — both `-strict` alone and `-strict` + bundle were run end-to-end and came back `Invalid: 0, Errors: 0` across all 13 groups. But that is a statement about the tree as of `ac16018`. If `main` has moved, expect to fix real manifest errors here, not to loosen the flags.

- [ ] **Step 1: Replace the schema comment and `KUBECONFORM_COMMON` in `scripts/lib/ci-tools.sh`**

Replace the comment block and array added in Task 1 Step 2 with:

```bash
# Upstream core schemas first, then the CRD bundle generated from the live
# cluster by scripts/gen-crd-schemas.py. Community CRD catalogs (datreeio)
# were rejected: they lag upstream and bake additionalProperties:false into
# every schema, so valid fields like CNPG spec.affinity.topologySpreadConstraints
# fail regardless of -strict. Schemas taken straight from this cluster's own
# CRDs have neither problem.
#
# -ignore-missing-schemas stays: kubeconform's default catalog ships no
# CustomResourceDefinition schema at any Kubernetes version, so the CRD
# objects in gotk-components.yaml and the operator CRD bundles would
# otherwise hard-fail with "could not find schema".
KUBECONFORM_COMMON=(
  -ignore-missing-schemas
  -strict
  -kubernetes-version "${KUBERNETES_VERSION}"
  -skip Secret
  -summary
  -schema-location default
  -schema-location "${SCHEMA_DIR}/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
)
```

- [ ] **Step 2: Define `SCHEMA_DIR` above the array**

Insert immediately after the `KUBERNETES_VERSION=` line and before the comment block:

```bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCHEMA_DIR="${REPO_ROOT}/.schemas"
```

`BASH_SOURCE` (not `$0`) is required — the file is sourced, so `$0` is the caller's path.

- [ ] **Step 3: Prove the bundle actually validates something**

```bash
cat > /tmp/typo-hr.yaml <<'EOF'
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: t
  namespace: d
spec:
  interval: 1m
  chart:
    spec:
      chart: foo
      sourceRef:
        kind: HelmRepository
        nmae: bar
EOF
source scripts/lib/ci-tools.sh
BIN=$(mktemp -d); install_kubeconform "$BIN"
"$BIN/kubeconform" "${KUBECONFORM_COMMON[@]}" /tmp/typo-hr.yaml; echo "exit=$?"
rm -rf "$BIN" /tmp/typo-hr.yaml
```

Expected (verified):

```
… HelmRelease t is invalid: … at '/spec/chart/spec/sourceRef': missing property 'name'
Summary: 1 resource found in 1 file - Valid: 0, Invalid: 1, Errors: 0, Skipped: 0
exit=1
```

If this reports `Valid: 1`, the `-schema-location` template is not resolving — check that `SCHEMA_DIR` is absolute and that `.schemas/helm.toolkit.fluxcd.io/helmrelease_v2.json` exists.

- [ ] **Step 4: Run full validation**

```bash
./scripts/validate.sh 2>&1 | grep -E "discovered|Summary"
```

Expected (verified today, exit `0`) — note the `Valid:` counts roughly double and `Skipped:` collapses:

```
Summary: 44 resources found in 13 files - Valid: 33, Invalid: 0, Errors: 0, Skipped: 11
discovered 12 Flux Kustomization path(s)
Summary: 16 resources found parsing stdin - Valid: 8, Invalid: 0, Errors: 0, Skipped: 8
Summary: 83 resources found parsing stdin - Valid: 56, Invalid: 0, Errors: 0, Skipped: 27
Summary: 19 resources found parsing stdin - Valid: 18, Invalid: 0, Errors: 0, Skipped: 1
Summary: 97 resources found parsing stdin - Valid: 63, Invalid: 0, Errors: 0, Skipped: 34
Summary: 23 resources found parsing stdin - Valid: 23, Invalid: 0, Errors: 0, Skipped: 0
Summary: 98 resources found parsing stdin - Valid: 72, Invalid: 0, Errors: 0, Skipped: 26
Summary: 11 resources found parsing stdin - Valid: 9, Invalid: 0, Errors: 0, Skipped: 2
Summary: 14 resources found parsing stdin - Valid: 14, Invalid: 0, Errors: 0, Skipped: 0
Summary: 9 resources found parsing stdin - Valid: 6, Invalid: 0, Errors: 0, Skipped: 3
Summary: 2 resources found parsing stdin - Valid: 2, Invalid: 0, Errors: 0, Skipped: 0
Summary: 63 resources found parsing stdin - Valid: 63, Invalid: 0, Errors: 0, Skipped: 0
Summary: 5 resources found parsing stdin - Valid: 5, Invalid: 0, Errors: 0, Skipped: 0
```

Across the run that is **153 → 372 validated objects** and **331 → 112 skipped** (the 112 remainder is `-skip Secret` plus the 42 `CustomResourceDefinition` objects).

**If a group reports `Invalid`:** read the message; it names the exact JSON pointer. Fix the manifest. If — and only if — the CRD's own OpenAPI schema is provably stricter than the apiserver (test by `kubectl apply --dry-run=server -f` on the rendered object, which must succeed while kubeconform fails), then that specific object may be excluded, but record it in `.schemas/README.md`. **Do not remove `-strict` or the second `-schema-location` to make a red run green.** Rollback for the whole task is `git revert` of this commit only — Task 3's bundle is inert without it.

- [ ] **Step 5: Commit, push, open PR 2**

```bash
git add scripts/lib/ci-tools.sh
git commit -m "ci: validate crd-typed objects strictly against the schema bundle"
git pull --rebase origin main
git push -u origin ci/crd-schema-bundle
gh pr create --title "ci: validate crds against a schema bundle from the cluster" --body "$(cat <<'EOF'
## Summary
- Stage 2 of docs/superpowers/plans/2026-08-03-ci-as-a-merge-gate.md
- Adds scripts/gen-crd-schemas.py and .schemas/ (132 schemas from 117 live CRDs, 2.2 MB, descriptions stripped, deterministic)
- Adds -strict and a second -schema-location so HelmReleases, Flux Kustomizations, CephClusters, CNPG Clusters, MariaDB CRs and Gateway API objects are validated instead of skipped

## Test plan
- [x] ./scripts/validate.sh exits 0; validated objects 153 -> 372, skipped 331 -> 112
- [x] A HelmRelease with `nmae:` instead of `name:` under sourceRef is now rejected
- [x] Two consecutive regenerations produce a byte-identical .schemas/ tree
- [ ] flux-validate green on this PR
EOF
)"
```

---

### Task 5: Render and validate the in-repo Helm charts (PR 3, commit 1)

**Files:**
- Create: `scripts/validate-helm.sh`
- Create: `helm/leantime/ci/full-values.yaml`, `helm/metabase/ci/full-values.yaml`, `helm/micronaut/ci/full-values.yaml`, `helm/outline/ci/full-values.yaml`, `helm/shlink/ci/full-values.yaml`
- Modify: `helm/*/.helmignore` (5 files)
- Modify: `.github/workflows/flux-validate.yaml` (new `helm` job)

**Interfaces:**
- Consumes: `scripts/lib/ci-tools.sh` (`install_helm`, `KUBECONFORM_COMMON`, `KUBERNETES_VERSION`) from Tasks 1 and 4
- Produces: check context `helm lint + template`, required in Task 8

**Gate:** PR 2 must be merged and green. `gh run list --workflow flux-validate.yaml --branch main --limit 1` → `completed  success`.

- [ ] **Step 1: Create the PR 3 branch**

```bash
git checkout main
git pull --rebase origin main
git checkout -b ci/helm-chart-validation
```

- [ ] **Step 2: Create `scripts/validate-helm.sh`**

```bash
#!/usr/bin/env bash
# Lint and render every in-repo Helm chart, then schema-check the rendered
# output with the same kubeconform settings scripts/validate.sh uses.
# Each chart is rendered twice: with its default values, and once per
# helm/<chart>/ci/*-values.yaml fixture (the `ci/` convention helm lint and
# chart-testing already understand) so conditional templates are exercised.
set -euo pipefail

source "$(dirname "$0")/lib/ci-tools.sh"

BIN_DIR="$(mktemp -d)"
trap 'rm -rf "${BIN_DIR}"' EXIT
export PATH="${BIN_DIR}:${PATH}"

echo "::group::Install tooling"
install_helm "${BIN_DIR}"
install_kubeconform "${BIN_DIR}"
helm version --short
kubeconform -v
echo "::endgroup::"

mapfile -t CHARTS < <(find helm -mindepth 2 -maxdepth 2 -name Chart.yaml -printf '%h\n' | sort)
if [[ ${#CHARTS[@]} -eq 0 ]]; then
  echo "::error::no charts found under helm/"
  exit 1
fi

rc=0
for chart in "${CHARTS[@]}"; do
  name="$(basename "${chart}")"

  echo "::group::helm lint ${name}"
  if ! helm lint "./${chart}"; then rc=1; fi
  echo "::endgroup::"

  values_files=("")
  while IFS= read -r f; do values_files+=("${f}"); done \
    < <(find "${chart}/ci" -maxdepth 1 -name '*-values.yaml' 2>/dev/null | sort)

  for vf in "${values_files[@]}"; do
    label="${vf:-<default values>}"
    echo "::group::helm template ${name} ${label}"
    args=(template "${name}" "./${chart}" --kube-version "${KUBERNETES_VERSION}")
    [[ -n "${vf}" ]] && args+=(-f "${vf}")
    if ! helm "${args[@]}" | kubeconform "${KUBECONFORM_COMMON[@]}"; then rc=1; fi
    echo "::endgroup::"
  done
done

if [[ "${rc}" -ne 0 ]]; then
  echo "::error::Helm chart validation failed"
fi
exit "${rc}"
```

```bash
chmod +x scripts/validate-helm.sh
```

The `./` prefix on the chart path is defensive, not load-bearing: helm 3 parses a bare `helm/micronaut` as repo `helm` / chart `micronaut` and fails with `Error: repo helm not found`; helm 4.2.2 (the pinned version) resolves it as a directory and works either way. Keep the `./` so the script does not depend on which of the two is on `PATH`.

- [ ] **Step 3: Create the five `ci/` fixtures**

Each fixture turns on exactly the conditional branches that chart's own default values leave off, so `httproute.yaml`, `servicemonitor.yaml`, `pdb.yaml`, `hpa.yaml` and `rbac.yaml` are actually rendered. Keep them this minimal — they are branch switches, not a copy of production values.

The four flat charts (leantime, metabase, micronaut, shlink) gate on top-level keys (`.Values.autoscaling.enabled`, `.Values.podDisruptionBudget.enabled`, …). **Outline is different**: it has no top-level `autoscaling:` or `podDisruptionBudget:` key at all — `templates/hpa.yaml` and `templates/pdb.yaml` range over `.Values.components` and gate on `$component.autoscaling.enabled` / `$component.pdb.enabled`. Its fixture must therefore set those *per component*, and `ingress.yaml` is intentionally left off everywhere (it is dead `networking.k8s.io/v1beta1`-guarded code; the repo uses HTTPRoute).

`helm/micronaut/ci/full-values.yaml`:

```yaml
# Exercises every conditional template in this chart. Not production values —
# see apps/clusters/feathre-core/apps/otis/release.yaml for those.
podDisruptionBudget:
  enabled: true
  minAvailable: 1
rbac:
  create: true
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
httpRoute:
  enabled: true
autoscaling:
  enabled: true
tracing:
  enabled: true
```

`helm/leantime/ci/full-values.yaml` and `helm/shlink/ci/full-values.yaml` (identical content):

```yaml
# Exercises every conditional template in this chart.
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
httpRoute:
  enabled: true
autoscaling:
  enabled: true
```

`helm/outline/ci/full-values.yaml` — note the per-component `autoscaling`/`pdb`; a top-level `autoscaling: {enabled: true}` here would be silently ignored and `templates/hpa.yaml` would never render:

```yaml
# Exercises every conditional template in this chart.
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
httpRoute:
  enabled: true
components:
  web:
    autoscaling:
      enabled: true
      minReplicas: 2
      maxReplicas: 4
      targetCPUUtilizationPercentage: 80
    pdb:
      enabled: true
      minAvailable: 1
```

`helm/metabase/ci/full-values.yaml`:

```yaml
# Exercises every conditional template in this chart.
httpRoute:
  enabled: true
autoscaling:
  enabled: true
```

⚠️ If `flux-release-control-and-convergence` has already deleted `helm/metabase`, skip that fixture — the chart loop is driven by `find`, not a hardcoded list.

- [ ] **Step 4: Keep the fixtures out of the packaged chart**

Append to each of `helm/leantime/.helmignore`, `helm/metabase/.helmignore`, `helm/micronaut/.helmignore`, `helm/outline/.helmignore`, `helm/shlink/.helmignore`:

```
# CI-only render fixtures
ci/
```

- [ ] **Step 5: Run it locally**

```bash
./scripts/validate-helm.sh 2>&1 | grep -E "helm template|Summary|chart\(s\) failed"
```

Expected (verified today against these exact fixtures, with `-strict` and the `.schemas/` bundle from PR 2 — 10 renders, all `Invalid: 0, Errors: 0, Skipped: 0`). `1 chart(s) linted, 0 chart(s) failed` appears **once per chart**, i.e. five times, interleaved with the render groups:

```
1 chart(s) linted, 0 chart(s) failed
::group::helm template leantime <default values>
Summary: 4 resources found parsing stdin - Valid: 4, Invalid: 0, Errors: 0, Skipped: 0
::group::helm template leantime helm/leantime/ci/full-values.yaml
Summary: 7 resources found parsing stdin - Valid: 7, Invalid: 0, Errors: 0, Skipped: 0
… metabase 4 / 6, micronaut 5 / 11, outline 8 / 11, shlink 4 / 7 …
```

Per-chart default → fixture resource counts, all verified: leantime 4→7, metabase 4→6, micronaut 5→11, outline 8→11, shlink 4→7. The fixture render must produce **more** resources than the default render for every chart; if it does not, the fixture is not switching the branch on. `helm lint` emits `[INFO] Chart.yaml: icon is recommended` for all five charts — informational, does not fail the lint.

**Rollback for this task:** the whole thing is new files plus one new workflow job — `git checkout main -- .github/workflows/flux-validate.yaml && rm -rf scripts/validate-helm.sh helm/*/ci` and drop the `.helmignore` lines. Nothing has been merged or applied at this point.

- [ ] **Step 6: Add the `helm` job to `.github/workflows/flux-validate.yaml`**

Append after the `validate` job:

```yaml
  helm:
    name: helm lint + template
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - name: Lint and render in-repo Helm charts
        run: ./scripts/validate-helm.sh
```

- [ ] **Step 7: Commit**

```bash
git add scripts/validate-helm.sh helm/*/ci helm/*/.helmignore .github/workflows/flux-validate.yaml
git commit -m "ci: lint and render the in-repo helm charts"
```

---

### Task 6: Enforce the `Chart.yaml` version bump (PR 3, commit 2)

**Files:**
- Create: `scripts/check-chart-version.sh`
- Modify: `.github/workflows/flux-validate.yaml` (new `chart-version` job)

**Interfaces:**
- Consumes: nothing from Task 5 (independent, same PR)
- Produces: check context `chart version bump`, required in Task 8

This is the highest-value missing check in the repo. It has already cost two remediation commits (`2e63b34` after `07fa450`; `69071b1` after `1ea6c65`/`9900b52`/`d685cfb`).

⚠️ **Resolve Decision Gate D before opening PR 3.** This check fires on every Renovate `helm-values` image-tag PR (verified against open PRs #52, #88, #89). Adding the job is safe — it only reports. Making it *required* in Task 8 without a plan for those PRs stops Renovate image updates dead. Nothing in this task is destructive; the decision only binds Task 8 Step 3's context list and Task 9's `CLAUDE.md` text.

- [ ] **Step 1: Create `scripts/check-chart-version.sh`**

```bash
#!/usr/bin/env bash
# For every helm/<chart> whose templates/ or values.yaml changed between two
# refs, assert Chart.yaml's version: also changed. Flux's HelmChart uses the
# default reconcileStrategy: ChartVersion, so an unbumped chart edit lands on
# main, reports Ready, and is simply not on the cluster.
set -euo pipefail

BASE="${1:?usage: check-chart-version.sh <base-ref> [head-ref]}"
HEAD_REF="${2:-HEAD}"

rc=0
for chart_dir in helm/*/; do
  chart="$(basename "${chart_dir}")"
  if git diff --quiet "${BASE}...${HEAD_REF}" -- "${chart_dir}templates" "${chart_dir}values.yaml"; then
    continue
  fi
  old="$(git show "${BASE}:${chart_dir}Chart.yaml" 2>/dev/null | sed -n 's/^version:[[:space:]]*//p' || true)"
  new="$(git show "${HEAD_REF}:${chart_dir}Chart.yaml" | sed -n 's/^version:[[:space:]]*//p')"
  if [[ "${old}" == "${new}" ]]; then
    echo "::error file=${chart_dir}Chart.yaml::helm/${chart}: templates/ or values.yaml changed but Chart.yaml version is still ${new} — Flux caches by chart version, bump it"
    rc=1
  else
    echo "helm/${chart}: version ${old:-<new chart>} -> ${new}"
  fi
done

[[ ${rc} -eq 0 ]] && echo "chart-version check: ok"
exit ${rc}
```

```bash
chmod +x scripts/check-chart-version.sh
```

Only `templates/` and `values.yaml` trigger the check, so editing `NOTES.txt`, `.helmignore` or the `ci/` fixtures added in Task 5 does not demand a version bump.

- [ ] **Step 2: Verify it reproduces the two historical failures**

```bash
./scripts/check-chart-version.sh 07fa450^ 07fa450; echo "exit=$?"
./scripts/check-chart-version.sh 2e63b34^ 2e63b34; echo "exit=$?"
./scripts/check-chart-version.sh HEAD~5 HEAD;      echo "exit=$?"
```

Expected (verified today):

```
::error file=helm/micronaut/Chart.yaml::helm/micronaut: templates/ or values.yaml changed but Chart.yaml version is still 0.4.0 — Flux caches by chart version, bump it
exit=1
chart-version check: ok
exit=0
chart-version check: ok
exit=0
```

If the first invocation exits `0`, the three-dot diff range is wrong — do not weaken the check to make it quiet.

- [ ] **Step 3: Add the `chart-version` job to `.github/workflows/flux-validate.yaml`**

Append after the `helm` job:

```yaml
  chart-version:
    name: chart version bump
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - name: Assert Chart.yaml version bumped
        run: ./scripts/check-chart-version.sh "${{ github.event.pull_request.base.sha }}" "${{ github.event.pull_request.head.sha }}"
```

`fetch-depth: 0` is required for both SHAs to be present — same pattern the `commits` job in `pr-lint.yaml` already uses.

- [ ] **Step 4: Run the whole local suite**

```bash
./scripts/validate.sh >/dev/null && echo "validate ok"
./scripts/validate-helm.sh >/dev/null && echo "validate-helm ok"
```

Expected: both lines print.

- [ ] **Step 5: Commit, push, open PR 3**

```bash
git add scripts/check-chart-version.sh .github/workflows/flux-validate.yaml
git commit -m "ci: fail prs that change a chart without bumping chart.yaml"
git pull --rebase origin main
git push -u origin ci/helm-chart-validation
gh pr create --title "ci: render helm charts and enforce chart version bumps" --body "$(cat <<'EOF'
## Summary
- Stage 3 of docs/superpowers/plans/2026-08-03-ci-as-a-merge-gate.md
- scripts/validate-helm.sh: helm lint + helm template (default values and a ci/full-values.yaml fixture per chart) piped into the same kubeconform invocation as the Flux manifests
- scripts/check-chart-version.sh: a PR whose helm/<chart>/templates or values.yaml changed must also change that chart's Chart.yaml version

## Test plan
- [x] ./scripts/validate-helm.sh exits 0; 10 renders across 5 charts, all Invalid: 0, Errors: 0
- [x] check-chart-version.sh flags 07fa450 (the commit that needed remediation 2e63b34) and passes on 2e63b34
- [ ] flux-validate green on this PR (validate, helm, chart-version)
EOF
)"
```

- [ ] **Step 6: After merging PR 3 — confirm the charts on the cluster did not move**

⚠️ **This step needs cluster access**, and `flux reconcile source git helmcharts` is a cluster **write** (it stamps a reconcile annotation on the GitRepository). It is the only cluster write in this plan. Run it exactly once — do not loop.

This is the one PR that adds files inside directories the `helmcharts` GitRepository packages. Expected result: nothing happens, because all seven `./helm/*` HelmReleases use the default `reconcileStrategy: ChartVersion` and no `Chart.yaml` version changed.

```bash
flux reconcile source git helmcharts
kubectl get helmrelease -A -o json | python3 -c "
import json,sys
for i in json.load(sys.stdin)['items']:
    c = i['spec'].get('chart',{}).get('spec',{}).get('chart','')
    if c.startswith('./helm'):
        print(i['metadata']['namespace'], i['metadata']['name'], c,
              i.get('status',{}).get('history',[{}])[0].get('chartVersion'))
"
```

Expected — unchanged from before the merge:

```
leantime leantime ./helm/leantime 0.2.0
otis-dev otis-dev ./helm/micronaut 0.5.2
otis otis ./helm/micronaut 0.5.2
outline outline ./helm/outline 0.5.1
shlink shlink ./helm/shlink 0.5.0
vulpes-dev vulpes-backend-dev ./helm/micronaut 0.5.2
vulpes vulpes-backend ./helm/micronaut 0.5.2
```

**Gate:** if any `chartVersion` changed or any release went `Ready=False`, `git revert` the PR 3 merge commit and investigate before continuing — but this should not happen, and the `ci/` entries added to `.helmignore` are the belt for it.

---

### Task 7: Fail the build on an unencrypted SOPS-matched file (PR 4)

**Files:**
- Create: `scripts/check-sops-encryption.py`
- Modify: `.gitignore`
- Modify: `.github/workflows/flux-validate.yaml` (new `sops-encryption` job; remove the `paths:` filter from the `pull_request` trigger)

**Interfaces:**
- Consumes: `.sops.yaml` creation rules (read at runtime, never hardcoded)
- Produces: check context `sops encryption`, required in Task 8; and an always-reporting `flux-validate` workflow, which Task 8 depends on

- [ ] **Step 1: Create the PR 4 branch**

```bash
git checkout main
git pull --rebase origin main
git checkout -b ci/sops-encryption-check
```

- [ ] **Step 2: Create `scripts/check-sops-encryption.py`**

```python
#!/usr/bin/env python3
"""Assert every git-tracked file matched by a whole-file .sops.yaml creation_rule is encrypted.

The file list is derived from .sops.yaml at runtime — never a hardcoded glob —
so it cannot drift from the rules. Only rules WITHOUT an `encrypted_regex` are
considered: those encrypt the whole file. Field-level rules (the `.*\\.yaml$`
rule) legitimately match plaintext manifests that happen to contain no
encryptable field, so they are out of scope for this check.
"""
import re
import subprocess
import sys

import yaml

# The two .sops.yaml files self-match the `sops\.ya?ml$` alternative in the
# whole-file rule but are configuration, not secrets. `.sops.pub.asc` does NOT
# match any creation rule (verified) and needs no entry here.
ALLOWLIST = {".sops.yaml", "clusters/feather-core/.sops.yaml"}

rules = yaml.safe_load(open(".sops.yaml"))["creation_rules"]
patterns = [re.compile(r["path_regex"]) for r in rules
            if r.get("path_regex") and not r.get("encrypted_regex")]
if not patterns:
    sys.exit("::error::no whole-file creation_rules found in .sops.yaml")

files = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True,
                       check=True).stdout.split("\0")
bad, checked = [], 0
for f in files:
    if not f or f in ALLOWLIST or not any(p.search(f) for p in patterns):
        continue
    checked += 1
    blob = open(f, "rb").read()
    if b"ENC[AES256_GCM" not in blob and b"sops_version" not in blob and b"sops:" not in blob:
        bad.append(f)

print(f"sops-encryption: checked {checked} matched file(s)", file=sys.stderr)
for f in bad:
    print(f"::error file={f}::matched a .sops.yaml creation_rule but contains no sops metadata")
sys.exit(1 if bad else 0)
```

- [ ] **Step 3: Verify it passes on the current tree**

```bash
python3 scripts/check-sops-encryption.py; echo "exit=$?"
```

Expected (verified today):

```
sops-encryption: checked 72 matched file(s)
exit=0
```

If `checked` is far below 72, the rule parsing is broken — a check that examines nothing is exactly the failure mode this plan is trying to remove. Investigate before continuing.

- [ ] **Step 4: Verify it fails on a plaintext secret**

```bash
mkdir -p /tmp/sopsneg && cd /tmp/sopsneg && git init -q .
cp "${OLDPWD}/.sops.yaml" .
mkdir -p apps && printf 'password=hunter2\n' > apps/leak.env
git add -A
python3 "${OLDPWD}/scripts/check-sops-encryption.py"; echo "exit=$?"
cd "${OLDPWD}" && rm -rf /tmp/sopsneg
```

Expected (verified):

```
sops-encryption: checked 1 matched file(s)
::error file=apps/leak.env::matched a .sops.yaml creation_rule but contains no sops metadata
exit=1
```

- [ ] **Step 5: Extend `.gitignore`**

Append to `.gitignore`:

```
# Decrypted / exported secret material — never commit (see docs/sops.md)
private-key.asc
*.dec
*.plain
```

`*.asc` is deliberately **not** blanket-ignored: `clusters/feather-core/.sops.pub.asc` is a tracked public key and a blanket rule plus a negation is more fragile than naming the one file the docs tell people to create.

- [ ] **Step 6: Confirm the `paths:` filter is already gone**

⚠️ **This is a prerequisite for Task 8, not an optimisation.** A required status check whose workflow is filtered out by `paths:` never reports, and GitHub leaves the PR stuck on *"Expected — Waiting for status to be reported"* forever. A docs-only PR would be unmergeable.

The removal was done in **Task 2 Step 1 (PR 1)** so that PRs 2-4 trigger the workflow themselves. Verify it stuck:

```bash
python3 -c "
import yaml; d=yaml.safe_load(open('.github/workflows/flux-validate.yaml'))
print('paths filter:', (d[True]['pull_request'] or {}).get('paths', 'NONE'))"
```

Expected: `paths filter: NONE`. If it prints a list, PR 1 was merged without that hunk (or Renovate PR #87 clobbered it) — apply the `on:` block from Task 2 Step 1 here before continuing, and add `.github/workflows/flux-validate.yaml` to this PR's `git add`.

- [ ] **Step 7: Add the `sops-encryption` job**

Append after the `chart-version` job:

```yaml
  sops-encryption:
    name: sops encryption
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: '3.x'
      - name: Install PyYAML
        run: pip install --quiet pyyaml
      - name: Assert sops-matched files are encrypted
        run: python3 scripts/check-sops-encryption.py
```

(Keep this SHA identical to the one in the `validate` job — Renovate bumps both together only if they match.)

**Rollback for this task:** `git revert` the PR 4 merge commit. If the check false-positives on a legitimately-plaintext file that matches a creation rule, add that exact path to `ALLOWLIST` in `scripts/check-sops-encryption.py` with a comment saying why — **never** loosen the regex derivation, which is the whole point of the check.

- [ ] **Step 8: Commit, push, open PR 4**

```bash
git add scripts/check-sops-encryption.py .gitignore .github/workflows/flux-validate.yaml
git commit -m "ci: fail on plaintext files matched by a sops creation rule"
git pull --rebase origin main
git push -u origin ci/sops-encryption-check
gh pr create --title "ci: guard against committing plaintext secrets" --body "$(cat <<'EOF'
## Summary
- Stage 4 of docs/superpowers/plans/2026-08-03-ci-as-a-merge-gate.md
- scripts/check-sops-encryption.py derives its file list from .sops.yaml's whole-file creation rules and fails if any tracked match lacks sops metadata (72 files matched today, all encrypted)
- Adds the `sops encryption` job to flux-validate (the pull_request `paths:` filter was already removed in stage 1, so it reports on every PR)
- .gitignore: private-key.asc, *.dec, *.plain

## Test plan
- [x] python3 scripts/check-sops-encryption.py -> checked 72, exit 0
- [x] A plaintext apps/leak.env in a scratch repo is rejected with exit 1
- [ ] flux-validate green on this PR (validate, helm, chart-version, sops-encryption)
EOF
)"
```

**Gate before Task 8:** after merging, open a throwaway docs-only PR and confirm **all four** `flux-validate` jobs plus both `pr-lint` jobs report. Do not flip the ruleset until you have seen every one of the six contexts report on a PR that touches no manifests.

```bash
gh pr checks <throwaway-pr-number>
```

Expected: six rows with app `github-actions`, all `pass`. Close the throwaway PR.

---

### Task 8: Require a PR and passing checks on `main` (PR 5)

⚠️ **This task changes repository settings, not files, and it changes how the maintainer works.** It requires an admin token. It is also the only step in this plan that cannot be reverted by `git revert` — the rollback is another API call, so **take the backup in Step 1 or you cannot roll back.**

⚠️ **Do not run this while any incident or capacity remediation is in flight.** See "Cross-theme dependencies".

**Files:** none (GitHub API only)

**Interfaces:**
- Consumes: merged PRs 1-4, and the six reporting check contexts confirmed in Task 7's gate
- Produces: ruleset 4266694 with `pull_request` + `required_status_checks` rules

- [ ] **Step 1: Back up the current ruleset — do this first**

```bash
gh api repos/OneLiteFeatherNET/Kubernetes-FLUX/rulesets/4266694 \
  > /tmp/ruleset-4266694.backup.json
jq '.rules' /tmp/ruleset-4266694.backup.json
```

Expected: `[{"type":"deletion"},{"type":"non_fast_forward"}]`. Keep this file until Task 9 is complete.

- [ ] **Step 2: Resolve Decision Gates A, B and D**

Write the answers down before proceeding. The commands below implement **A2 (remove bypass actors) + B (six github-actions contexts) + D1 (hand-bump Chart.yaml on Renovate chart PRs)**.

- For **A1**, change the `bypass_actors:` line in Step 3 from `[]` to `.bypass_actors`. Note that the API returns `"actor_id": null` for the `OrganizationAdmin` entry; if the PUT is rejected with a validation error on that field, drop the null key: `bypass_actors: [.bypass_actors[] | with_entries(select(.value != null))]`.
- For **D3**, delete the `{"context": "chart version bump", …}` line from the `required_status_checks` array in Step 3 — the job still runs and reports, it just does not block. Five contexts then, not six; adjust Step 5's expected output and Task 9's `CLAUDE.md` text accordingly.

- [ ] **Step 3: Build the new ruleset body**

```bash
jq '{
  name, target, enforcement, conditions,
  bypass_actors: [],
  rules: (.rules + [
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "allowed_merge_methods": ["merge", "squash", "rebase"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          {"context": "kustomize build + kubeconform", "integration_id": 15368},
          {"context": "helm lint + template",          "integration_id": 15368},
          {"context": "chart version bump",            "integration_id": 15368},
          {"context": "sops encryption",               "integration_id": 15368},
          {"context": "Conventional PR title",         "integration_id": 15368},
          {"context": "Conventional commits",          "integration_id": 15368}
        ]
      }
    }
  ])
}' /tmp/ruleset-4266694.backup.json > /tmp/ruleset-4266694.new.json
jq '.rules | map(.type)' /tmp/ruleset-4266694.new.json
```

Expected: `["deletion","non_fast_forward","pull_request","required_status_checks"]`.

`integration_id: 15368` is the GitHub Actions app — confirmed today from `gh api repos/.../commits/ac16018/check-runs --jq '.check_runs[].app.id'`. `required_approving_review_count: 0` is deliberate: on a single-maintainer repo the passing check is the gate, not a rubber-stamp review. `strict_required_status_checks_policy: false` means a PR does not have to be rebased onto the latest `main` before merging — with Renovate moving `main` constantly, `true` would cause a re-run treadmill.

- [ ] **Step 4: Apply it**

```bash
gh api -X PUT repos/OneLiteFeatherNET/Kubernetes-FLUX/rulesets/4266694 \
  --input /tmp/ruleset-4266694.new.json
```

- [ ] **Step 5: Confirm it took**

```bash
gh api repos/OneLiteFeatherNET/Kubernetes-FLUX/rulesets/4266694 \
  --jq '{rules: [.rules[].type], bypass: .bypass_actors, can_bypass: .current_user_can_bypass}'
```

Expected under A2:

```json
{"rules":["deletion","non_fast_forward","pull_request","required_status_checks"],"bypass":[],"can_bypass":"never"}
```

`can_bypass: "never"` is the proof that the gate is real. If it still says `"always"`, the bypass actors were not cleared and you have chosen A1 by accident.

**Rollback (any failure, or the gate proves unworkable):**

```bash
gh api -X PUT repos/OneLiteFeatherNET/Kubernetes-FLUX/rulesets/4266694 \
  --input <(jq '{name, target, enforcement, conditions, bypass_actors, rules}' /tmp/ruleset-4266694.backup.json)
```

---

### Task 9: Prove the gate works, then document the break-glass

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the ruleset from Task 8
- Produces: a verified gate and a findable break-glass procedure

- [ ] **Step 1: Prove a direct push is rejected**

⚠️ This creates a throwaway commit on local `main` and then hard-resets. **Run `git status --porcelain` first and confirm it is empty** — `git commit -am` would sweep up unrelated modifications to tracked files, and the `git reset --hard` below would then discard them irrecoverably.

```bash
git checkout main && git pull --rebase origin main
git status --porcelain   # must print nothing
echo "" >> CLAUDE.md && git commit -am "chore: gate smoke test"
git push origin main
```

Expected under A2: the push is **rejected** with `GH013: Repository rule violations found` / `Changes must be made through a pull request`. Then:

```bash
git reset --hard origin/main
```

Under A1 the push succeeds — that is the expected (and disappointing) result of that choice, and confirms A1's limits rather than a bug.

- [ ] **Step 2: Prove a PR with a red check cannot merge**

Open a scratch branch with a deliberately broken manifest. **Use a field-level typo, not a `kind:` typo.** With `-ignore-missing-schemas`, a misspelled kind resolves to no schema and is *skipped*, not rejected — verified:

```
$ kubeconform … <a manifest with `kind: HelmReleas`>
Summary: 1 resource found in 1 file - Valid: 0, Invalid: 0, Errors: 0, Skipped: 1   # exit 0
```

so that test would come back green and "prove" nothing. Use instead the same typo Task 4 Step 3 validated: in any `release.yaml` under `apps/base/`, rename `spec.chart.spec.sourceRef.name` to `nmae`. Push it, open a PR, and confirm:

```bash
gh pr checks <pr-number>
gh pr merge <pr-number> --squash --dry-run 2>&1 | head -3
```

Expected: `kustomize build + kubeconform` shows `fail` with `at '/spec/chart/spec/sourceRef': missing property 'name'` in the job log, and the merge is refused with a message about required checks / rule violations. Close the PR **without merging** and delete the branch:

```bash
gh pr close <pr-number> --delete-branch
```

If the check shows `pass`, the `-strict` + `.schemas/` wiring from PR 2 is not in effect on `main` — stop and re-run Task 4 Step 3 locally before trusting the gate.

- [ ] **Step 3: Document the workflow change in `CLAUDE.md`**

In the "Conventions & non-obvious behaviors" section, replace the first bullet (currently `- **Conventional Commits are enforced in CI** …`) with a bullet group:

```markdown
- **`main` is gated — every change goes through a PR.** Ruleset 4266694 requires a pull request plus six passing checks: `kustomize build + kubeconform`, `helm lint + template`, `chart version bump`, `sops encryption`, `Conventional PR title`, `Conventional commits`. `required_approving_review_count` is 0 — the passing check is the gate, not a reviewer. Direct `git push origin main` is rejected.
- **Break-glass (real incident, cannot wait for CI):**
  ```bash
  gh api repos/OneLiteFeatherNET/Kubernetes-FLUX/rulesets/4266694 > /tmp/ruleset.json
  gh api -X PUT repos/OneLiteFeatherNET/Kubernetes-FLUX/rulesets/4266694 \
    --input <(jq '{name,target,conditions,bypass_actors,rules} + {enforcement:"disabled"}' /tmp/ruleset.json)
  # … push the fix …
  gh api -X PUT repos/OneLiteFeatherNET/Kubernetes-FLUX/rulesets/4266694 \
    --input <(jq '{name,target,conditions,bypass_actors,rules,enforcement}' /tmp/ruleset.json)
  ```
  Disabling and re-enabling is auditable; a standing bypass actor is not.
- **Renovate PRs that bump an image tag inside `helm/<chart>/values.yaml` will fail `chart version bump`** — Renovate cannot bump `Chart.yaml`, and without the bump the new tag never reaches the cluster. Fix it on the Renovate branch, do not merge past it:
  ```bash
  gh pr checkout <n>
  # bump the patch version in helm/<chart>/Chart.yaml
  git commit -am "chore: bump <chart> chart for the image update" && git push
  ```
- **Conventional Commits are enforced in CI** (`.github/workflows/pr-lint.yaml` + `commitlint.config.mjs`): allowed types `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`, subject must start **lowercase**, header ≤100 chars. The PR title is the squash-merge subject and is linted too.
- **`./scripts/validate.sh` validates CRDs too.** It builds every Flux path with kustomize and schema-checks the output with kubeconform `-strict`, against upstream core schemas plus `.schemas/` — a bundle generated from this cluster's own CRDs by `scripts/gen-crd-schemas.py`. Regenerate it (`kubectl get crd -o json | python3 scripts/gen-crd-schemas.py .schemas`) after any operator upgrade that ships new CRDs. `./scripts/validate-helm.sh` does the same for the charts under `helm/`.
```

Also update the "Common commands" block: under the existing `./scripts/validate.sh` entry, add

```bash
# Lint + render the in-repo Helm charts and schema-check the output.
./scripts/validate-helm.sh
```

and change the stale comment `# Pins kustomize 5.7.1 / kubeconform 0.7.0 / k8s 1.31; skips Secrets; strips SOPS patches.` to `# Pins kustomize 5.7.1 / kubeconform 0.7.0 / k8s 1.36.1; skips Secrets; strips SOPS patches.`

Finally, extend the "When you edit a chart in `helm/`, bump its `Chart.yaml` `version:`" warning with: `CI enforces this — see \`scripts/check-chart-version.sh\`.`

- [ ] **Step 4: Ship it through the gate you just built**

```bash
git checkout -b docs/ci-merge-gate
git add CLAUDE.md
git commit -m "docs: describe the ci merge gate and break-glass"
git pull --rebase origin main
git push -u origin docs/ci-merge-gate
gh pr create --title "docs: describe the ci merge gate and break-glass" --body "Stage 5 of docs/superpowers/plans/2026-08-03-ci-as-a-merge-gate.md — documents ruleset 4266694, the six required contexts, the break-glass procedure, and the two new validation scripts."
```

**Final gate:** this PR is the first one that must pass through the gate end-to-end. If it merges cleanly with six green checks and no direct push, the theme is complete.

---

## What could not be verified

- **The behaviour of `azure/setup-helm` and of `helm` 3.x** — sidestepped entirely: `scripts/validate-helm.sh` downloads a checksum-pinned `helm` 4.2.2, which is the exact version every render in this plan was verified with. Whether the charts also render identically under helm 3.x was not tested and is not relied on.
- **Whether GitHub reports a `chart-version` job skipped by `if: github.event_name == 'pull_request'` as satisfying a required check on `push` events.** Required status checks only apply to pull requests, where the condition is always true, so this should never arise — but it was not empirically confirmed.
- **The exact wording of the push-rejection message in Task 9 Step 1** (`GH013 …`). The mechanism is certain; the string may differ by `gh`/git version.
- **That `-strict` + the CRD bundle stays green as `main` moves.** It was verified green against the tree at `ac16018` (12 Flux paths, 372 validated objects, 0 invalid). Manifests merged between now and execution are not covered by that result.
- **`RepositoryRole` actor id `2` in the existing `bypass_actors`** — the numeric id was read from the API but not resolved to a role name. Under recommendation A2 both actors are removed, so it does not matter; under A1 it is preserved verbatim. The `OrganizationAdmin` entry comes back from the API with `"actor_id": null`; whether GitHub accepts that on the way back in was not tested (see Task 8 Step 2 for the workaround).
- **Whether GitHub accepts a `PUT` body assembled by the Step 3 `jq` filter at all.** The body was built and inspected, but no `PUT` was issued — this review is read-only. Step 4 is the first write; the backup from Step 1 is what makes that safe.
- **`actions/setup-python@v7.0.0` behaviour on this workflow.** The SHA `5fda3b95a4ea91299a34e894583c3862153e4b97` was resolved from the tag and is what Renovate PR #87 proposes, but v7 has not been run against `pip install --quiet pyyaml` here. If the `validate` job fails at setup, fall back to `ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0` and let Renovate re-propose the major bump on its own PR.

---

## Verification log (this tree, `ac16018`)

Everything below was re-run end-to-end and reproduced exactly as written above:

| Claim | Result |
|---|---|
| kustomize/kubeconform/helm sha256 pins | all three match the upstream published checksum files |
| `actions/checkout` v7.0.1, `setup-node` v7.0.0, `action-semantic-pull-request` v6.1.1 SHAs | match `gh api .../git/ref/tags/<tag>` |
| live server version | `v1.36.1` |
| CRD bundle | 132 schemas from 117 CRDs, 2.2 MB, 23 group dirs |
| Task 1 Step 6 control-plane summary at 1.36.1 | `44 resources … Valid: 21, Skipped: 23` — byte-identical to the 1.31.0 result |
| Task 4 Step 4 full run with `-strict` + bundle | all 13 summaries reproduced line for line; 372 valid, 112 skipped, 0 invalid |
| Task 4 Step 3 `nmae:` negative test | `Invalid: 1`, exit 1, message as quoted |
| Task 5 renders (`-strict` + bundle) | leantime 4→7, metabase 4→6, micronaut 5→11, outline 8→**11** (fixture corrected), shlink 4→7, all `Invalid: 0` |
| Task 6 Step 2 historical replay | `07fa450` → exit 1 naming micronaut 0.4.0; `2e63b34` and `HEAD~5..HEAD` → exit 0 |
| Task 7 Step 3 / Step 4 | `checked 72`, exit 0 on this tree; scratch-repo `apps/leak.env` → exit 1 |
| ruleset 4266694 | `rules: ["deletion","non_fast_forward"]`, both bypass actors present, `current_user_can_bypass: "always"` |
