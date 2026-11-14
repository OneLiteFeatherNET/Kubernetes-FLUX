## ADDED Requirements

### Requirement: Reads stay available during the migration

WHILE the migration is being rehearsed or prepared, THE public Dependency-Track service SHALL continue to serve read requests from the v4 deployment.

#### Scenario: Read requests succeed during rehearsal

- **GIVEN** the v4 deployment serves `dependency-track.onelitefeather.dev`
- **WHEN** the copy, migrator and verification steps run against `dependency-track-v4-copy` and `dependency-track-v5`
- **THEN** a `GET /api/v1/project` on the public hostname returns success throughout the rehearsal

### Requirement: The v4 database is never written to

THE migration SHALL read the v4 data only from the copy database `dependency-track-v4-copy` and SHALL NOT write to the v4 database `dependency-track`.

#### Scenario: v4 database is unchanged after a full rehearsal

- **GIVEN** the row counts and last-updated values of the v4 database recorded before the rehearsal
- **WHEN** the copy, bootstrap, migrator run, verification and cleanup have completed
- **THEN** the v4 database has the same row counts and last-updated values as before

### Requirement: The v5 target database is empty and exclusively owned

BEFORE the migrator bootstrap runs, THE target database `dependency-track-v5` SHALL be empty, and its owner SHALL be the role `dependency-track` that the v5 apiserver also uses.

#### Scenario: Bootstrap refuses a target that holds data

- **GIVEN** the target database `dependency-track-v5` already contains user data
- **WHEN** the migrator `bootstrap` is run against it
- **THEN** the migrator refuses to proceed and no data is changed

#### Scenario: Owner matches the apiserver

- **GIVEN** the migrator bootstrap has completed
- **WHEN** the owner of the schema objects is inspected
- **THEN** the owner is the role `dependency-track`

### Requirement: The key-encryption key exists before the first start

BEFORE the v5 apiserver starts for the first time, THE key-encryption key SHALL exist as a keyset Secret that the release references by name, and THE key SHALL NOT be set inline in a manifest.

#### Scenario: Apiserver waits for a missing key

- **GIVEN** the KEK Secret referenced by `secretManagement.database.kek.existingSecret` does not exist
- **WHEN** the v5 release is applied
- **THEN** the apiserver pod does not start

#### Scenario: Key is not in plain text in Git

- **GIVEN** the rendered v5 release
- **WHEN** the manifests are searched for the KEK value
- **THEN** the KEK value is found only in a SOPS-encrypted Secret

### Requirement: No legacy alpine properties

THE v5 apiserver environment SHALL NOT contain any variable whose name starts with `ALPINE_`, because Dependency-Track 5.2.1 refuses to start with such a property.

#### Scenario: Rendered manifest has no legacy property

- **GIVEN** the rendered manifests of the `dependency-track-v5` release
- **WHEN** environment variable names are listed
- **THEN** no name starts with `ALPINE_`

### Requirement: A full rehearsal precedes the cutover

BEFORE the cutover window opens, THE migration SHALL complete a full rehearsal on a fresh copy of the v4 database, with the duration of each step recorded.

#### Scenario: Cutover is blocked without a rehearsal record

- **GIVEN** no rehearsal record exists with the step durations
- **WHEN** the cutover is scheduled
- **THEN** the cutover does not start

### Requirement: The verification checklist gates the cutover

BEFORE the public hostname moves to v5, THE verification checklist SHALL pass, and each difference in row counts SHALL be explained by the migrator's own verification notes.

#### Scenario: Unexplained row difference blocks the cutover

- **GIVEN** the v5 row count of projects is lower than the v4 count and the migrator notes do not explain the difference
- **WHEN** the checklist is evaluated
- **THEN** the cutover does not proceed

#### Scenario: Legacy API key still uploads

- **GIVEN** an API key migrated from v4
- **WHEN** it is used to `PUT /api/v1/bom` with a test BOM on the test hostname
- **THEN** the project receives the analysis and the request succeeds

#### Scenario: OIDC login works on the test hostname

- **GIVEN** a maintainer in the Entra group for Dependency-Track
- **WHEN** the maintainer signs in on `dependency-track-next.onelitefeather.dev`
- **THEN** the session is established with the teams from the group claim

### Requirement: Automatic permission grants are reviewed

AFTER migration, THE operators SHALL review every permission that the migration granted automatically, and each grant SHALL be accepted or removed before the cutover.

#### Scenario: Automatic grant is removed

- **GIVEN** a team that held `ACCESS_MANAGEMENT` in v4 received `PORTFOLIO_ACCESS_CONTROL_BYPASS` in v5
- **WHEN** the review concludes that the team should not hold the bypass
- **THEN** the permission is removed from that team in v5 before cutover

### Requirement: Rollback is an ingress switch

WHEN a cutover must be rolled back, THE public hostname SHALL return to the v4 deployment by reverting the cutover change, and THE v4 deployment SHALL remain running until retirement.

#### Scenario: Reverting the cutover restores v4

- **GIVEN** the cutover change has been merged and the public hostname serves v5
- **WHEN** the cutover change is reverted
- **THEN** the public hostname serves v4 again, and the v4 pods were never stopped

### Requirement: Notification rules and integration credentials are restored

BEFORE the write freeze is lifted after cutover, THE operators SHALL re-enable each reviewed notification rule and SHALL re-enter each repository, analyzer and vulnerability-source credential that the migration removed.

#### Scenario: Freeze is lifted only after restoration

- **GIVEN** the cutover has moved the public hostname to v5
- **WHEN** the write freeze is to be lifted
- **THEN** every migrated notification rule has been reviewed and every removed credential has been re-entered

### Requirement: v4 is retired only after a stability window

THE v4 release, the v4 database and the copy database SHALL NOT be removed before 14 days of stable operation on v5 after cutover and a verified final backup of the v4 database.

#### Scenario: Retirement before the window ends is refused

- **GIVEN** cutover happened 5 days ago
- **WHEN** a retirement change is proposed
- **THEN** the change is not merged until the 14-day window has ended

#### Scenario: Final backup is restorable

- **GIVEN** a final `pg_dump` of the v4 database stored in the S3 backups
- **WHEN** it is restored into a scratch database
- **THEN** the restore completes without errors
