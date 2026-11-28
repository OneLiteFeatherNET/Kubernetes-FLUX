## Why

Stop two avoidable restart loops. The git-sync sidecars of the Airflow DAG processor and triggerer have restarted about 330 times each, and the Sentry consumers `process-spans` and `snuba-subscription-consumer-eap-items` 34 and 25 times.

- git-sync runs with `GITSYNC_MAX_FAILURES=0`, the chart default: the first failed fetch from GitHub ends the container. Each restart on 2026-11-27 and 2026-11-28 followed one `Connection reset by peer` or connect timeout, during short cluster-wide network outages. git-sync keeps the last good checkout, so retrying is safe.
- Both Sentry consumers fail with `redis.exceptions.TimeoutError: Timeout reading from socket` against the shared Dragonfly. The Dragonfly master logs about 250 `Stalled heartbeat() fiber for 1–4 seconds` per day, spread evenly over the day, while it uses about 0.07 CPU cores and 23 MiB. Its slowlog shows trivial commands, such as `SMEMBERS` on a two-element set, taking 819 ms: the thread waits, not the command. The pods request 250m CPU for two proactor threads, which gives them a low CPU weight whenever the node is contended.

## What Changes

- `foundation/data/clusters/feather-core/airflow/release.yaml`: `dags.gitSync.maxFailures: -1`, retry forever.
- `foundation/databases/clusters/feather-core/dragonfly/dragonfly.yaml`: CPU request from `250m` to `1`. No CPU limit, unchanged.
- **Commit type and scope:** the implementation ships as `fix(platform)`.
- **Rollback:** revert the code PR.

## Out of scope

- The cluster-wide network outages themselves (memberlist dial timeouts at 11:12, 13:04, 13:15 and 16:25 on 2026-11-28). They are investigated separately.
- The grus-web probe timeouts; the probes live in the grus chart, in the grus repository.
- The cloudflare-tunnel-ingress-controller lease losses; the binary exposes no lease settings.

## Impact

- Capabilities `airflow` and `dragonfly` (see `specs/`).
- Changed files: the two manifests above.
- Dragonfly pods restart once with the new request (rolling, one replica at a time by the operator). Sentry, Harbor, Outline and n8n see one master failover.
- CPU requests on the workers grow by 2.25 cores in total (3 replicas).
- Expected effect: no git-sync restarts on transient GitHub errors; Dragonfly heartbeat stalls and Sentry Redis timeouts drop. Measured in the tasks, not assumed.
