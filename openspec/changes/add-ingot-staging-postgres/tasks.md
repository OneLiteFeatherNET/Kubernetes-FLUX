## 1. Preflight

- [ ] 1.1 Confirm the names `ingot_staging` (role and database) are unused on `feather-core-cluster-pg`
- [ ] 1.2 Confirm the CNPG connection budget leaves room for a role with limit 10 (`max_connections: 200`)
- [ ] 1.3 Confirm the existing role secrets in the same overlay use the basic-auth layout and the SOPS rule of `.sops.yaml`

## 2. Database objects

- [ ] 2.1 Add the managed role `ingot_staging` (`login: true`, `connectionLimit: 10`, `passwordSecret: role-ingot-staging`) to `foundation/databases/clusters/feather-core/postgresql/cluster.yaml`
- [ ] 2.2 Add the database object `ingot-staging` (`spec.name: ingot_staging`, owner `ingot_staging`, `databaseReclaimPolicy: retain`) under the postgresql overlay
- [ ] 2.3 Register the database object and the role secret in the postgresql `kustomization.yaml`

## 3. Role secret

- [ ] 3.1 Generate the password locally, write the manifest `role-ingot-staging` (basic-auth, keys `username` and `password`) to a temporary file inside the clone, and encrypt it in place with SOPS. Never print or commit the plaintext
- [ ] 3.2 Verify the encrypted file holds `ENC[` values and `sops:` metadata

## 4. Validation

- [ ] 4.1 Run `./.github/scripts/validate.sh` and `kubectl kustomize` on the postgresql overlay
- [ ] 4.2 Run `.github/scripts/check-sops-encryption.py` on the new file

## 5. Code pull request

- [ ] 5.1 Open the code pull request under the title `feat(databases): add the ingot staging database on postgresql`; no files under `openspec/` in it
