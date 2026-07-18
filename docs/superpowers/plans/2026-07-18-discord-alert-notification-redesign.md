# Discord Alert Notification Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Grafana's `discord.title`/`discord.message` notification templates so a group of
simultaneously-firing instances of the same alert rule renders as one compact message (shared
Rule/Dashboard links once, an instance-count badge in the title, a bullet list of instances) instead
of repeating the full block per instance.

**Architecture:** Single-file edit inside the existing Helm-values-embedded Grafana alerting
provisioning block. No new files, no new components — this replaces the `template:` value of the
`discord.title` and `discord.message` entries under
`spec.values.alerting.templates.yaml.templates` in
`apps/clusters/feathre-core/base-apps/grafana/release.yaml`.

**Tech Stack:** Grafana unified alerting notification templates (Go `text/template`, Alertmanager
template funcs), YAML, Helm values (with the repo's established `{{ \`{{ ... }}\` }}` escaping
convention to keep literal `{{`/`}}` out of Helm's own `tpl` pass).

## Global Constraints

- Every literal Go-template action (`{{ ... }}`) in the new template text MUST be wrapped exactly
  as `{{ \`{{ ... }}\` }}` — the same escaping convention already used by every other value under
  `spec.values.alerting.*` in this file (see `contactpoints.yaml`/`policies.yaml`/`rules.yaml` in
  the same block for the established pattern). Do not introduce a different escaping style.
- The regex literal inside the `reReplaceAll` call MUST contain **two** literal backslash
  characters before each `.` — i.e. write it as `\\.` (two backslash bytes), not `\.` (one). This
  was verified live against Grafana's template-test API during planning: a single backslash
  produces `invalid syntax` (Go double-quoted string literals don't recognize `\.` as a valid
  escape), and the file passes through no other layer that reduces the backslash count (Helm's
  backtick raw-string wrapper and YAML's literal/single-quoted scalar styles both pass backslashes
  through unchanged) — so whatever is typed in the file is exactly what Grafana's Go template
  parser sees.
- No `Chart.yaml` version bump needed — `grafana` is an external chart (from the `grafana-labs`
  `HelmRepository`), not an in-repo chart under `helm/`.
- Do not touch `contactpoints.yaml`, `policies.yaml`, or `rules.yaml` in this same file — out of
  scope per the spec.
- Preserve the exact 14-space content indentation the `discord.message` block-literal
  (`template: |`) already uses, and the single-quoted style for `discord.title`'s one-line
  `template: '...'`.
- Commit messages follow Conventional Commits (repo CI lints them): `feat(monitoring): ...`.

---

### Task 1: Replace the Discord notification templates

**Files:**
- Modify: `apps/clusters/feathre-core/base-apps/grafana/release.yaml:27585-27601`

**Interfaces:**
- Consumes: nothing from other tasks — this is the only task in this plan.
- Produces: the new `discord.title` and `discord.message` template text, already fully validated
  live against Grafana's template-test API during design (see the spec's "Verification performed
  during design" section) — this task's job is a faithful, byte-exact transcription into the
  YAML file's escaping convention, not new template design.

- [ ] **Step 1: Confirm the current file content matches exactly before editing**

Run: `sed -n '27585,27601p' apps/clusters/feathre-core/base-apps/grafana/release.yaml`

Expected output (if this doesn't match byte-for-byte, STOP — something else has changed this
block since this plan was written; do not proceed with the Edit below without re-deriving the
anchor):

```
          - name: discord.title
            template: '{{ `{{ define "discord.title" }}` }}{{ `{{ if eq .Status "firing" }}` }}🔴 FIRING{{ `{{ else }}` }}✅ RESOLVED{{ `{{ end }}` }} · {{ `{{ .CommonLabels.alertname }}` }}{{ `{{ end }}` }}'
          - name: discord.message
            template: |
              {{ `{{ define "discord.message" }}` }}
              {{ `{{- range .Alerts }}` }}
              {{ `{{ .Annotations.summary }}` }}
              {{ `{{- if .Labels.severity }}` }}
              severity: `{{ `{{ .Labels.severity }}` }}`
              {{ `{{- end }}` }}
              🔗 Rule: <{{ `{{ .GeneratorURL }}` }}>
              {{ `{{- if .Annotations.dashboard_url }}` }}
              📊 Dashboard: <{{ `{{ .Annotations.dashboard_url }}` }}>
              {{ `{{- end }}` }}

              {{ `{{ end }}` }}
              {{ `{{- end }}` }}
```

- [ ] **Step 2: Replace the block**

Using the Edit tool, replace this exact text (the block from Step 1):

```yaml
          - name: discord.title
            template: '{{ `{{ define "discord.title" }}` }}{{ `{{ if eq .Status "firing" }}` }}🔴 FIRING{{ `{{ else }}` }}✅ RESOLVED{{ `{{ end }}` }} · {{ `{{ .CommonLabels.alertname }}` }}{{ `{{ end }}` }}'
          - name: discord.message
            template: |
              {{ `{{ define "discord.message" }}` }}
              {{ `{{- range .Alerts }}` }}
              {{ `{{ .Annotations.summary }}` }}
              {{ `{{- if .Labels.severity }}` }}
              severity: `{{ `{{ .Labels.severity }}` }}`
              {{ `{{- end }}` }}
              🔗 Rule: <{{ `{{ .GeneratorURL }}` }}>
              {{ `{{- if .Annotations.dashboard_url }}` }}
              📊 Dashboard: <{{ `{{ .Annotations.dashboard_url }}` }}>
              {{ `{{- end }}` }}

              {{ `{{ end }}` }}
              {{ `{{- end }}` }}
```

with:

```yaml
          - name: discord.title
            template: '{{ `{{ define "discord.title" }}` }}{{ `{{ if eq .Status "firing" }}` }}🔴 FIRING{{ `{{ if gt (len .Alerts.Firing) 1 }}` }} ({{ `{{ len .Alerts.Firing }}` }}x){{ `{{ end }}` }}{{ `{{ else }}` }}✅ RESOLVED{{ `{{ if gt (len .Alerts.Resolved) 1 }}` }} ({{ `{{ len .Alerts.Resolved }}` }}x){{ `{{ end }}` }}{{ `{{ end }}` }} · {{ `{{ .CommonLabels.alertname }}` }}{{ `{{ end }}` }}'
          - name: discord.message
            template: |
              {{ `{{ define "discord.message" }}` }}{{ `{{ if .CommonLabels.severity }}` }}severity: `{{ `{{ .CommonLabels.severity }}` }}`
              {{ `{{ end }}` }}🔗 Rule: <{{ `{{ (index .Alerts 0).GeneratorURL }}` }}>
              {{ `{{ if .CommonAnnotations.dashboard_url }}` }}📊 Dashboard: <{{ `{{ .CommonAnnotations.dashboard_url }}` }}>
              {{ `{{ end }}` }}

              {{ `{{ if gt (len .Alerts.Firing) 0 }}` }}Firing ({{ `{{ len .Alerts.Firing }}` }}):
              {{ `{{ range .Alerts.Firing }}` }}• {{ `{{ reReplaceAll "^(.*?\\.) Check:.*$" "$1" .Annotations.summary }}` }}
              {{ `{{ end }}` }}{{ `{{ end }}` }}{{ `{{ if gt (len .Alerts.Resolved) 0 }}` }}Resolved ({{ `{{ len .Alerts.Resolved }}` }}):
              {{ `{{ range .Alerts.Resolved }}` }}• {{ `{{ reReplaceAll "^(.*?\\.) Check:.*$" "$1" .Annotations.summary }}` }}
              {{ `{{ end }}` }}{{ `{{ end }}` }}{{ `{{ end }}` }}
```

- [ ] **Step 3: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load_all(open('apps/clusters/feathre-core/base-apps/grafana/release.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Validate the Kustomize build renders both templates**

Run: `kubectl kustomize apps/clusters/feathre-core/base-apps/grafana 2>&1 | grep -c "name: discord.title\|name: discord.message"`
Expected: `2`

- [ ] **Step 5: Extract the two template strings exactly as Kustomize rendered them**

The escaping round-trip (YAML parse → Helm `tpl` unwrap, simulated here by just reading the
rendered Kustomize output, which shows the *values as Kustomize/YAML sees them* — the Helm `tpl`
unwrap itself only happens live inside the cluster's Helm release, so Step 6 tests the literal
template TEXT for Go-template correctness directly, which is the part this plan controls and can
verify without a live cluster) — run:

```bash
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana 2>&1 | grep -A1 "name: discord.title" | tail -1
kubectl kustomize apps/clusters/feathre-core/base-apps/grafana 2>&1 | grep -A20 "name: discord.message"
```

Confirm the output contains the literal text `reReplaceAll` followed by a pattern with **two**
backslash characters before each `.` (i.e. `\\.`  appears twice in the output, once per
Firing/Resolved bullet line) — if you see only one backslash per occurrence, Step 2's Edit lost a
backslash somewhere and must be redone; do not proceed to Step 6 until this is confirmed.

- [ ] **Step 6: Re-verify the exact template text against Grafana's live template-test API**

This is the critical check: it confirms the template text as written in the file is valid Go
template syntax that Grafana will actually accept and render correctly — not just valid YAML.

Using the `mcp__grafana__grafana_api_request` tool (`POST
/api/alertmanager/grafana/config/api/v1/templates/test`), submit the **unescaped** template text
(i.e. the literal Go-template source with the `{{ \`...\` }}` wrapper removed — this is what the
text becomes after Helm's `tpl` pass unwraps it; you already have this exact text from Step 2's
"with:" block, just without the `{{ \`` / `\` }}` wrapper) — this is the same template text and
same test payload already verified once during planning; re-running it here confirms nothing was
lost in transcription into the file, not re-deriving new results.

Title test — run with `endpoint: /api/alertmanager/grafana/config/api/v1/templates/test`, `method: POST`, `body`:
```json
{"name":"discord.title","template":"{{ define \"discord.title\" }}{{ if eq .Status \"firing\" }}🔴 FIRING{{ if gt (len .Alerts.Firing) 1 }} ({{ len .Alerts.Firing }}x){{ end }}{{ else }}✅ RESOLVED{{ if gt (len .Alerts.Resolved) 1 }} ({{ len .Alerts.Resolved }}x){{ end }}{{ end }} · {{ .CommonLabels.alertname }}{{ end }}","alerts":[{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"base-apps","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization base-apps (namespace flux-system) is not Ready. Check: flux get kustomization base-apps -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"},{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"configs","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization configs (namespace flux-system) is not Ready. Check: flux get kustomization configs -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"},{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"monitoring","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization monitoring (namespace flux-system) is not Ready. Check: flux get kustomization monitoring -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"},{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"rook","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization rook (namespace flux-system) is not Ready. Check: flux get kustomization rook -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"},{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"rook-fr01","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization rook-fr01 (namespace flux-system) is not Ready. Check: flux get kustomization rook-fr01 -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"}]}
```
Expected `results[0].text`: `🔴 FIRING (5x) · flux-core-layer-not-ready`

Message test — same endpoint/method, `body` (note: JSON-encoding the regex's two literal
backslashes requires four backslash characters in this JSON string, per standard JSON string
escaping — this is a JSON-encoding detail of the API call, not a change to the two-backslash rule
for the file itself):
```json
{"name":"discord.message","template":"{{ define \"discord.message\" }}{{ if .CommonLabels.severity }}severity: `{{ .CommonLabels.severity }}`\n{{ end }}🔗 Rule: <{{ (index .Alerts 0).GeneratorURL }}>\n{{ if .CommonAnnotations.dashboard_url }}📊 Dashboard: <{{ .CommonAnnotations.dashboard_url }}>\n{{ end }}\n{{ if gt (len .Alerts.Firing) 0 }}Firing ({{ len .Alerts.Firing }}):\n{{ range .Alerts.Firing }}• {{ reReplaceAll \"^(.*?\\\\.) Check:.*$\" \"$1\" .Annotations.summary }}\n{{ end }}{{ end }}{{ if gt (len .Alerts.Resolved) 0 }}Resolved ({{ len .Alerts.Resolved }}):\n{{ range .Alerts.Resolved }}• {{ reReplaceAll \"^(.*?\\\\.) Check:.*$\" \"$1\" .Annotations.summary }}\n{{ end }}{{ end }}{{ end }}","alerts":[{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"base-apps","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization base-apps (namespace flux-system) is not Ready. Check: flux get kustomization base-apps -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"},{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"configs","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization configs (namespace flux-system) is not Ready. Check: flux get kustomization configs -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"},{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"monitoring","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization monitoring (namespace flux-system) is not Ready. Check: flux get kustomization monitoring -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"},{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"rook","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization rook (namespace flux-system) is not Ready. Check: flux get kustomization rook -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"},{"status":"firing","labels":{"alertname":"flux-core-layer-not-ready","name":"rook-fr01","exported_namespace":"flux-system","severity":"critical"},"annotations":{"summary":"Flux Kustomization rook-fr01 (namespace flux-system) is not Ready. Check: flux get kustomization rook-fr01 -n flux-system","dashboard_url":"https://grafana.apps.onelite.feather/d/flux-control-plane"},"generatorURL":"https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1"}]}
```
Expected `results[0].text`:
```
severity: `critical`
🔗 Rule: <https://grafana.apps.onelite.feather/alerting/grafana/flux-core-layer-not-ready/view?orgId=1>
📊 Dashboard: <https://grafana.apps.onelite.feather/d/flux-control-plane>

Firing (5):
• Flux Kustomization base-apps (namespace flux-system) is not Ready.
• Flux Kustomization configs (namespace flux-system) is not Ready.
• Flux Kustomization monitoring (namespace flux-system) is not Ready.
• Flux Kustomization rook (namespace flux-system) is not Ready.
• Flux Kustomization rook-fr01 (namespace flux-system) is not Ready.
```

If either result doesn't match exactly, STOP — do not commit. The transcription into the file
introduced a discrepancy from the verified design; diff the extracted text from Step 5 against the
literal blocks in Step 2 character-by-character to find it.

- [ ] **Step 7: Run the full repo validation**

Run: `./scripts/validate.sh 2>&1 | grep -E "Invalid: [1-9]|Errors: [1-9]"`
Expected: no output (empty = all layers clean)

- [ ] **Step 8: Commit**

```bash
git add apps/clusters/feathre-core/base-apps/grafana/release.yaml
git commit -m "$(cat <<'EOF'
feat(monitoring): compact Discord notifications for grouped alerts

A group of simultaneously-firing instances of the same rule (e.g. 5
Flux Kustomizations not-Ready at once) repeated the full message block
- including the identical Rule/Dashboard links - once per instance,
turning a 5-instance group into a ~40-line wall of text. The title now
carries an instance-count badge and the message prints shared info
(severity, Rule link, Dashboard link) once, followed by a compact
bullet per instance with the Check:-command clause stripped (it stays
reachable via the Rule link).
EOF
)"
```

## After execution

Nothing to reconcile on the live cluster from this change alone beyond the normal push — Discord
notification templates only affect how already-firing/resolving alerts are *rendered*, not
whether they fire. The next time any alert rule transitions state after this merges and Flux
reconciles, its Discord message will use the new format.
