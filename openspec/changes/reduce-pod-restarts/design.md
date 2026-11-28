## Context

Airflow is deployed by the official chart; `dags.gitSync.maxFailures` maps to `GITSYNC_MAX_FAILURES`. Dragonfly runs as one `Dragonfly` resource with three replicas (one master), `--proactor_threads=2`, request 250m CPU, no CPU limit. Under Linux CFS the CPU request sets the cgroup weight; with no limit there is no throttling, but a low weight means little CPU time while other pods on the node are busy, for example during the nightly Spark maintenance.

## Decision drivers

- Fewer restarts without hiding real outages.
- Lowest-risk change first; no new components.

## Considered options

1. **Retry git-sync forever; raise the Dragonfly CPU request to one core (chosen).**
2. **Raise Redis socket timeouts in Sentry and Snuba.** Treats the symptom; Dragonfly would still stall for every client. Kept as a follow-up if stalls persist.
3. **More proactor threads.** Needs more memory per thread (256 MiB each) and does not address CPU weight. Rejected for now.

## Decision

Option 1.

## Consequences

- git-sync no longer signals a broken GitHub connection by restarting; a stale checkout shows only in its logs and in Airflow DAG freshness.
- Dragonfly replicas reserve one core each. If heartbeat stalls persist after rollout, the cause is not CPU weight and option 2 or a node-level investigation follows.
