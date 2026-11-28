## 1. Code

- [ ] 1.1 Set `dags.gitSync.maxFailures: -1` in `foundation/data/clusters/feather-core/airflow/release.yaml` and confirm the key in the chart's values schema
- [ ] 1.2 Set the Dragonfly CPU request to `1` in `foundation/databases/clusters/feather-core/dragonfly/dragonfly.yaml`
- [ ] 1.3 Run `./.github/scripts/validate.sh`

## 2. Rollout and verification

- [ ] 2.1 Confirm `GITSYNC_MAX_FAILURES=-1` on the dag-processor and triggerer pods
- [ ] 2.2 Confirm the Dragonfly rollout finished with one master and two replicas in sync
- [ ] 2.3 After 24 hours, count `Stalled heartbeat` lines on the master and restarts of the two Sentry consumers

## 3. Pull request

- [ ] 3.1 Open the code pull request `fix(platform): stop avoidable airflow and sentry restarts`
