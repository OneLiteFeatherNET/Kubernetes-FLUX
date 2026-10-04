#!/usr/bin/env python3
"""Assert every in-repo Helm chart touched by a change also bumps its Chart.yaml version.

Flux keys the HelmChart artifact by the chart's `version:`. Edit a chart's
templates or values without moving that version and the source-controller
produces a byte-identical artifact revision, so the HelmRelease sees nothing to
do: no upgrade runs, and the release keeps reporting Ready with the old chart.
There is no error anywhere in Flux, the CLI, or the cluster.

That is how three merged Renovate PRs (#88, #245, #255) left Outline running
1.9.1 while helm/outline/values.yaml said 1.10.1. renovate.json now carries a
`bumpVersions` rule so Renovate moves the version itself, but that feature is
flagged experimental upstream and does nothing for a chart edited by hand. This
check is the part that fails loudly either way.

Compares against the merge base, so it sees the same set of changes a reviewer
does. Charts added or deleted in the change are skipped: there is no previous
version to compare against, and a deleted chart needs no bump.
"""
import re
import subprocess
import sys

CHART_VERSION = re.compile(r"(?:^|\n)version:\s*(\S+)")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def version_at(ref: str, path: str) -> str | None:
    """The chart version at `ref`, or None when the file does not exist there."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    match = CHART_VERSION.search(result.stdout)
    return match.group(1) if match else None


base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
merge_base = git("merge-base", base, "HEAD").strip()

changed = git("diff", "--name-only", merge_base, "HEAD").splitlines()
charts = sorted({p.split("/")[1] for p in changed if p.startswith("helm/") and "/" in p[5:]})

stale = []
for chart in charts:
    manifest = f"helm/{chart}/Chart.yaml"
    before, after = version_at(merge_base, manifest), version_at("HEAD", manifest)
    # A new or removed chart has nothing to compare against.
    if before is None or after is None:
        continue
    if before == after:
        stale.append((chart, before))

if stale:
    print("Chart changed without a version bump — Flux will not re-render it:\n")
    for chart, version in stale:
        print(f"  helm/{chart}/Chart.yaml stays at {version}")
    print("\nBump `version:` in each Chart.yaml above. Without it the change is")
    print("applied to git and silently ignored by the cluster.")
    sys.exit(1)

if charts:
    print(f"Chart version bumped for: {', '.join(charts)}")
else:
    print("No in-repo chart touched.")
