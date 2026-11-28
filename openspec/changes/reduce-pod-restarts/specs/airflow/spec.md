## ADDED Requirements

### Requirement: git-sync survives transient fetch failures

WHEN a git-sync fetch of the DAG repository fails, THE git-sync sidecar SHALL keep running and retry, serving the last good checkout.

#### Scenario: GitHub resets one connection

- **GIVEN** `dags.gitSync.maxFailures` is `-1`
- **WHEN** one fetch fails with `Connection reset by peer`
- **THEN** the git-sync container is not restarted and the next fetch succeeds
