# 2026-07-18: MariaDB 12.3.2 upgrade (complete) + cluster-wide RGW AccessDenied incident (ongoing)

**Status as of 2026-07-18 ~14:00 UTC:**
- MariaDB Galera major upgrade — **DONE, cluster fully healthy.**
- Rook Ceph RGW (S3) AccessDenied incident — **ROOT-CAUSED, NOT YET FIXED.** Blocks MariaDB backups and several unrelated services. Awaiting a decision on remediation.

This doc exists because the two are entangled: the RGW incident was discovered *while validating* the MariaDB upgrade (the post-upgrade backup check failed), but its actual cause is a separate, unrelated change (a Rook operator upgrade). Keeping both in one place so the MariaDB status doesn't get lost inside the bigger incident.

---

## Part 1: MariaDB Galera upgrade — 11.8.5 → 11.8.8 → 12.3.2

### What changed

| Step | Commit | Result |
|---|---|---|
| Patch bump 11.8.5 → 11.8.8 | `7bfc054` | Rolled clean, same day |
| Major bump 11.8.8 → 12.3.2 | `15f9da7` (rebased to `15f9da7` after an unrelated commit landed on `main`) | Rolled clean |
| PhysicalBackup memory limit raise (512Mi → requests 1Gi/limits 4Gi) | `25ae9a2` | Required because `mariadb-backup` under 12.3.2 needs more memory than 11.8.8's did — the old limit OOMKilled the first post-upgrade backup attempt |

Plan used: `docs/superpowers/plans/2026-07-18-mariadb-12.3-major-upgrade.md`, executed via `superpowers:subagent-driven-development` with a human checkpoint before the production push.

### How the rollout went

- `mariadb-operator`'s default `updateStrategy: ReplicasFirstPrimaryLast` rolled one Galera pod at a time, primary last — this is the built-in canary behavior, no extra tooling needed.
- The rollout was much faster than the plan's 10-minute abort budget assumed: all 3 pods restarted and resynced within ~100 seconds via Galera **IST** (incremental state transfer, not a full SST snapshot) — light write load meant the diverge window was tiny.
- `mariadb-upgrade` (formerly `mysql_upgrade`) did **not** run automatically — the entrypoint logged `MariaDB upgrade ... skipped due to $MARIADB_AUTO_UPGRADE setting`, and `mariadb-galera-1`'s log even showed the concrete symptom (`Column count of mysql.proc is wrong. Expected 22, found 21 ... Please use mariadb-upgrade to fix this error`). Ran it manually on all 3 pods — clean (`Phase 8/8`, rc 0), cluster unaffected.
  - **Open follow-up, not yet done:** decide whether to set `$MARIADB_AUTO_UPGRADE` (or find the operator's intended mechanism) so future major bumps don't need this manual step again.

### Current confirmed-healthy state (last checked 2026-07-18 ~13:55 UTC)

```
kubectl get mariadb mariadb-galera -n mariadb-galera
NAME             READY   STATUS    PRIMARY            UPDATES                    AGE
mariadb-galera   True    Running   mariadb-galera-1   ReplicasFirstPrimaryLast   6d2h

Conditions: Ready=True GaleraInitialized=True Updated=True GaleraReady=True GaleraConfigured=True
```

All 3 pods (`mariadb-galera-0/1/2`):
- Image: `mariadb:12.3.2`
- `SELECT VERSION()` → `12.3.2-MariaDB-ubu2404-log`
- `wsrep_cluster_size` = 3, `wsrep_local_state_comment` = `Synced`, `wsrep_ready` = `ON`

MaxScale (`maxscale-galera`): `Ready=True`, primary `mariadb-galera-1`.

App connectivity spot-checked (shlink, leantime): no DB errors. Metrics exporter (`mariadb-galera-metrics`): healthy, 0 restarts, `mysql_up=1` for all 3 targets in Prometheus/Mimir.

**The only thing NOT working is backups** — and that's the RGW incident below, not a MariaDB problem. The Galera cluster, MaxScale, and every app using it are fully healthy on 12.3.2.

### Pre-upgrade restore point (in case a rollback of the *data* is ever needed)

Last known-good backup taken **before** the upgrade, still on MariaDB 11.8.8: `mariadb-galera-backup-20260718060000`, completed `2026-07-18T07:03:08Z`, `Success`, `1/1`. No successful backup exists yet on 12.3.2 (blocked by the RGW incident — see Part 2).

---

## Part 2: Cluster-wide Rook Ceph RGW `403 AccessDenied` incident

### Impact

Every app that authenticates as a **named `CephObjectStoreUser` that does not own its own bucket** (the "shared user + separately OBC-owned bucket" pattern used throughout this repo) started getting `403 AccessDenied` on all S3 operations. Confirmed affected via RGW access logs:

- `mariadb-galera-backup` (this doc's trigger)
- `feather-core-cluster-pg-backup` — **CNPG's own Postgres WAL archiving.** This is the most urgent one if the incident runs long — WAL archive failures can eventually cause disk pressure on the Postgres cluster.
- Loki (chunks + index), Mimir (alertmanager), Tempo (traces), Reposilite, BlueMap

**Not affected:** Ceph itself (`ceph health detail` → `HEALTH_OK` throughout), RGW daemon pods (0 restarts, stable), the MariaDB Galera cluster itself, any bucket's own auto-generated OBC-owner credentials (see differential test below).

### Root cause

**Trigger:** a separate, unrelated, deliberately-planned infrastructure change — a Rook Ceph **operator** upgrade — executed by another session today, via its own plan (`docs/superpowers/plans/2026-07-18-rook-operator-upgrade.md` on `main`, not yet present in this worktree branch as of `25ae9a2`):

| Time (UTC) | Event |
|---|---|
| ~11:41 | PR #74 (`6916749`) merged — includes the operator-upgrade design/plan docs and an initial `ceph image → v19.2.5` bump |
| 12:13:40 | First observed `403` in RGW access logs (Postgres WAL backup) |
| 12:21:45 | `rook-ceph-operator` Deployment rolls to `v1.19.7` (from `v1.18.11`) — new ReplicaSet `rook-ceph-operator-5c6946f787` |
| ~12:13–13:19 | `403` storm spreads across all affected buckets (timing varies per RGW daemon — each re-syncs independently) |
| 13:02:49 | Operator rolls again, to `v1.20.2` — new ReplicaSet `rook-ceph-operator-5485c595b9` (currently running). This pod's bundled Ceph client reports version **20.2.2 "tentacle"**, while the actual Ceph cluster is still on **19.2.3 "squid"** (`ceph versions` confirmed) |
| ~13:02–13:34 | New operator pod stuck in an init loop: `failed to create or retrieve rgw admin ops user ... skipping reconcile since operator is still initializing`, repeating for every managed bucket/user |
| by ~13:44 | Operator init loop cleared on its own (no more "still initializing" log lines) — but the `403`s **did not** self-heal |

**Mechanism, confirmed empirically (all read-only checks):**

1. Ceph's own user/bucket database is intact — `radosgw-admin user list`/`bucket list` only appeared empty because the CLI defaults to the wrong of two zones (`default` vs the real `feather-s3`); scoped correctly (`--rgw-realm=feather-s3`), all users and buckets are present and correctly configured (`mariadb` user: not suspended, correct keys, `caps: buckets=*, users=*`).
2. A hand-rolled SigV4 request against `mariadb-galera-backup` using the `mariadb` user's real credentials returns a genuine `<Error><Code>AccessDenied</Code>` — i.e. the signature is valid (it's not a credentials/auth problem), the server is explicitly denying the *authorization*.
3. **Decisive differential test:** the exact same bucket, queried with the bucket's own auto-generated OBC-owner credentials (`rook-ceph-fr01/mariadb-galera-backup` secret) → `200 OK`, lists objects fine. Queried with the `mariadb` user's credentials (same bucket, same moment) → `403 AccessDenied`. This proves RGW and the bucket data are completely healthy — only the **named, non-owner user's access** is broken.
4. The bucket's ACL (`radosgw-admin metadata get bucket.instance:...`) lists **only the OBC-owner** as a grantee — no separate grant for `mariadb`. This repo's `ObjectBucketClaim` for `mariadb-galera-backup` (and the equivalent for every other affected bucket) never set `spec.additionalConfig.bucketOwner`, so bucket ownership always defaulted to the auto-generated OBC user; the named apps' access must therefore have relied on some other mechanism that the Rook 1.18→1.20 upgrade changed or broke — most likely how the operator (re)provisions/links `CephObjectStoreUser` access against `ObjectBucketClaim`-owned buckets, though the exact upstream Rook change responsible has not been pinned down (would need diffing Rook's source between 1.18.11 and 1.20.2; not done as part of this investigation).
5. `radosgw-admin user info --uid=mariadb` shows no `system`/`admin` bypass flag — ruling out "it used to be a Ceph system user and that flag got cleared" as the mechanism.
6. `ceph config log` shows no relevant `rgw_*` config changes in the incident window — ruling out a live RGW daemon config change as the direct trigger. (It does show `osd_recovery_max_active` and `rbd_default_map_options` being toggled on/off repeatedly around the same time, which looks like operator-driven flapping/instability during the upgrade — not chased further.)

**Not yet done / open:**
- The precise Rook code path responsible (which controller, what changed between 1.18 and 1.20) is not identified — only the empirical *symptom* and *trigger window* are confirmed.
- The upstream migration plan's own verification gate (`docs/superpowers/plans/2026-07-18-rook-operator-upgrade.md`, Task 2 Step 5) only checked `ceph status` / Kustomization readiness — it never exercised actual S3 data-path access for a non-owner named user, which is why this regression wasn't caught before merging.
- No remediation attempted yet (see below) — this doc stops at root-cause per the explicit instruction to investigate, not fix, without a separate go-ahead.

### Evidence commands (for reproduction / re-verification)

```bash
# Ceph/RGW health baseline
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph health detail
kubectl get pods -n rook-ceph-fr01 -l app=rook-ceph-rgw -o wide   # 0 restarts throughout

# Operator version history (the actual trigger)
kubectl get rs -n rook-ceph -l app=rook-ceph-operator \
  -o custom-columns=NAME:.metadata.name,IMAGE:'.spec.template.spec.containers[0].image',CREATED:.metadata.creationTimestamp \
  --sort-by=.metadata.creationTimestamp

# Ceph client/cluster version skew
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- ceph versions   # cluster: 19.2.3 squid
kubectl logs -n rook-ceph -l app=rook-ceph-operator | grep "base ceph version"  # operator: 20.2.2 tentacle

# Correct zone scoping for radosgw-admin (there are two zones: "feather-s3" [real] and "default" [unused/empty])
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin zone list
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin user info --uid=mariadb --rgw-realm=feather-s3
kubectl -n rook-ceph-fr01 exec deploy/rook-ceph-tools -- radosgw-admin bucket stats --bucket=mariadb-galera-backup --rgw-realm=feather-s3

# Differential SigV4 test (owner key works, named-user key doesn't) — script left at /tmp/sigv4_test.py
# on the rook-ceph-tools pod for re-runs; not committed to the repo (throwaway diagnostic tool).
```

### Recommended next steps (not yet executed — needs a decision)

1. **Immediate mitigation options** (pick one, needs explicit sign-off before acting — this touches shared production storage):
   - Roll `rook-ceph-operator` back to `v1.18.11` and see if access is restored (would confirm the operator version as the direct cause, and unblock everyone immediately) — but this reverts a deliberate, already-"verified"-and-merged upgrade (PRs #73/#74/#75), so should be coordinated with whoever ran that migration.
   - Or: find and set `spec.additionalConfig.bucketOwner` on the affected `ObjectBucketClaim`s to make the named user (`mariadb`, `loki`, etc.) the actual bucket owner — sidesteps whatever broke the non-owner grant path, but changes bucket ownership semantics for every affected app and needs testing.
   - Or: escalate to Rook upstream (open/search a GitHub issue) if this turns out to be a genuine 1.19/1.20 regression, and wait for a fix.
2. Once RGW access is restored, re-trigger `mariadb-galera-backup`'s on-demand backup (`kubectl patch physicalbackup mariadb-galera-backup -n mariadb-galera --type merge -p '{"spec":{"schedule":{"onDemand":"<new-value>"}}}'`) and confirm success before considering the MariaDB upgrade fully closed out.
3. Check whether CNPG's Postgres WAL archive backlog needs attention once access is restored (verify no disk-pressure alarm fired during the outage window).
4. Consider adding an S3 data-path smoke test (PUT+GET+DELETE as a representative non-owner named user) to future Rook/Ceph upgrade plans' verification gates, given this is exactly what slipped through this time.
