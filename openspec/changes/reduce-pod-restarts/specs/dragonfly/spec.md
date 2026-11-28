## ADDED Requirements

### Requirement: Dragonfly gets enough CPU weight

THE Dragonfly pods SHALL request one CPU core each.

#### Scenario: Heartbeat stalls drop

- **GIVEN** the Dragonfly pods run with a CPU request of `1`
- **WHEN** the master log is read for 24 hours
- **THEN** it shows clearly fewer than 250 `Stalled heartbeat` lines, and the Sentry consumers show no new `TimeoutError` restarts
