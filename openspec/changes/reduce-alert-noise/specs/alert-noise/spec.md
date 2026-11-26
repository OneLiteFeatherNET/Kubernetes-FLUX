## ADDED Requirements

### Requirement: Transient Flux timeouts stay out of Discord

WHEN a Flux object reports a transient timeout or status-update error, the Flux notification SHALL NOT be sent to Discord.

#### Scenario: Status-update timeout is excluded

- **GIVEN** a Kustomization reports "failed to update status: timed out waiting for the condition"
- **WHEN** the Flux notification controller evaluates the Alert `cluster-errors`
- **THEN** no message is sent to Discord

#### Scenario: Build error still reaches Discord

- **GIVEN** a Kustomization reports a build error
- **WHEN** the Flux notification controller evaluates the Alert `cluster-errors`
- **THEN** the message is sent to Discord immediately

### Requirement: One alert for a stalled Flux object

WHEN a Flux Kustomization or HelmRelease has not been Ready for 15 minutes, Grafana SHALL send one alert.

#### Scenario: Stalled layer fires once

- **GIVEN** a Kustomization is not Ready for 15 minutes
- **WHEN** the Grafana rule "Flux core layer not ready" evaluates
- **THEN** one firing notification is sent for that Kustomization
- **AND** when it becomes Ready again, one resolved notification is sent

### Requirement: Hysteresis for the CNPG connection ratio

WHILE the CNPG connection ratio stays between 70% and 80% after firing, the alert "CNPG Postgres connection saturation high" SHALL remain firing.

#### Scenario: Ratio falls into the hysteresis band

- **GIVEN** the alert fired at 81% connection saturation
- **WHEN** the ratio drops to 75%
- **THEN** the alert stays firing

#### Scenario: Ratio drops below the recovery threshold

- **GIVEN** the alert is firing
- **WHEN** the ratio drops below 70%
- **THEN** the alert resolves

### Requirement: Single Otis restart is not an alert

IF Otis restarts once within one hour, THEN no alert "Otis container restarting" SHALL be sent.

#### Scenario: One restart in an hour

- **GIVEN** an Otis pod restarts once
- **WHEN** the rule "Otis container restarting" evaluates
- **THEN** the alert stays in Normal state

#### Scenario: Two restarts in an hour

- **GIVEN** an Otis pod restarts twice within one hour
- **WHEN** the rule "Otis container restarting" evaluates
- **THEN** the alert fires

### Requirement: Short dips do not fire

WHEN a dip in Otis health checks, Minecraft TPS or Ceph RGW failed requests lasts less than the rule's `for` duration, the corresponding alert SHALL NOT fire.

#### Scenario: Health check dip shorter than 10 minutes

- **GIVEN** Otis health checks fail for 6 minutes
- **WHEN** the rule "Otis health check failing" evaluates
- **THEN** the alert does not fire

### Requirement: Alerts stay firing briefly after recovery

WHEN the condition of "Ceph RGW failed request rate elevated" or "Minecraft TPS low" clears, the alert SHALL remain firing for the rule's `keepFiringFor` duration before it resolves.

#### Scenario: Brief recovery does not resolve

- **GIVEN** "Minecraft TPS low" is firing
- **WHEN** TPS recovers for 3 minutes and then drops again
- **THEN** the alert never sends a resolved notification in between
