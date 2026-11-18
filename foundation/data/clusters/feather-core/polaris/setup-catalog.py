"""Idempotent Polaris setup: catalog corvus, namespace capture, principal and catalog roles, grants.

Runs as the bootstrap root principal (internal token, requires authentication.type mixed).
Every create treats HTTP 409 (already exists) as success, so the Job can re-run safely.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

POLARIS = os.environ["POLARIS_URL"].rstrip("/")
CLIENT_ID = os.environ["ROOT_CLIENT_ID"]
CLIENT_SECRET = os.environ["ROOT_CLIENT_SECRET"]
BUCKET = os.environ["BUCKET_NAME"]
S3_ENDPOINT = os.environ["S3_ENDPOINT"]
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
# Quarkus health lives on the management port (8182), not on the API port.
HEALTH_URL = os.environ["POLARIS_HEALTH_URL"]

CATALOG = "corvus"
NAMESPACE = "capture"
MGMT = f"{POLARIS}/api/management/v1"
CATALOG_API = f"{POLARIS}/api/catalog/v1"

# principal role -> catalog role
ASSIGNMENTS = {
    "corvus_ingest": "ingest",
    "corvus_ml": "ml",
    "lake_maintenance": "maintenance",
    # Read-only SQL access through Trino (Superset). Read grants on catalog corvus only.
    "lake_sql": "sql",
    # Lets the root principal create the namespace through the Iceberg REST API.
    "service_admin": "admin",
}

# Entra ID client app id (token claim azp) -> principal role. Polaris resolves the token's
# principal by name, so every client must exist as a Polaris principal.
PRINCIPALS = {
    "41380977-879c-4c49-8e0d-fb1047f1bb7a": "corvus_ingest",     # Polaris Corvus Ingest
    "cea8487b-adef-45ef-917e-e4506cc38922": "corvus_ml",         # Polaris Corvus ML
    "1f752161-5002-468d-a39b-12c46f6b874d": "lake_maintenance",  # Polaris Lake Maintenance
    "8832de30-7197-40ed-a2bc-68c88bc770cb": "lake_sql",          # Polaris Trino
}

# catalog role -> grants (namespace capture, or the whole catalog)
GRANTS = {
    "ingest": [
        {"type": "namespace", "namespace": [NAMESPACE], "privilege": p}
        for p in ("TABLE_CREATE", "TABLE_WRITE_DATA", "TABLE_LIST", "NAMESPACE_LIST")
    ],
    "ml": [
        {"type": "namespace", "namespace": [NAMESPACE], "privilege": p}
        for p in ("TABLE_READ_DATA", "TABLE_LIST", "NAMESPACE_LIST")
    ],
    # Table maintenance (delete, rewrite, expire, orphan cleanup) needs read and write on
    # existing tables only; no table or namespace creation (spec: maintenance cannot create).
    "maintenance": [
        {"type": "catalog", "privilege": p}
        for p in ("TABLE_READ_DATA", "TABLE_WRITE_DATA", "TABLE_LIST", "NAMESPACE_LIST")
    ],
    # Trino (Superset SQL) reads only: no write privilege anywhere in this role.
    "sql": [
        {"type": "catalog", "privilege": p}
        for p in ("TABLE_LIST", "NAMESPACE_LIST", "TABLE_READ_PROPERTIES", "TABLE_READ_DATA")
    ],
    "admin": [{"type": "catalog", "privilege": "CATALOG_MANAGE_METADATA"}],
}


def call(method, url, body=None, token=None, form=None, ok=(200, 201, 204, 409)):
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    if status not in ok:
        raise SystemExit(f"{method} {url} -> HTTP {status}: {raw[:300]!r}")
    print(f"{method} {url} -> {status}", flush=True)
    return json.loads(raw) if raw else {}


def wait_for_polaris():
    for _ in range(60):
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(5)
    raise SystemExit("Polaris did not become ready")


def main():
    wait_for_polaris()
    token = call(
        "POST",
        f"{CATALOG_API}/oauth/tokens",
        form={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "PRINCIPAL_ROLE:ALL",
        },
        ok=(200,),
    )["access_token"]

    call("POST", f"{MGMT}/catalogs", token=token, body={"catalog": {
        "name": CATALOG,
        "type": "INTERNAL",
        "properties": {"default-base-location": f"s3://{BUCKET}/"},
        "storageConfigInfo": {
            "storageType": "S3",
            "allowedLocations": [f"s3://{BUCKET}/"],
            "region": S3_REGION,
            "endpoint": S3_ENDPOINT,
            "pathStyleAccess": True,
            # RGW has no STS (design decision 3). Polaris uses the static keys from its own Secret.
            "stsUnavailable": True,
        },
    }})

    for principal_role in ASSIGNMENTS:
        call("POST", f"{MGMT}/principal-roles", token=token,
             body={"principalRole": {"name": principal_role}})

    for catalog_role in GRANTS:
        call("POST", f"{MGMT}/catalogs/{CATALOG}/catalog-roles", token=token,
             body={"catalogRole": {"name": catalog_role}})

    def grant_and_assign(catalog_role):
        for grant in GRANTS[catalog_role]:
            call("PUT", f"{MGMT}/catalogs/{CATALOG}/catalog-roles/{catalog_role}/grants",
                 token=token, body={"grant": grant})
        for principal_role, assigned in ASSIGNMENTS.items():
            if assigned == catalog_role:
                call("PUT", f"{MGMT}/principal-roles/{principal_role}/catalog-roles/{CATALOG}",
                     token=token, body={"catalogRole": {"name": catalog_role}})

    # Catalog-level grants first: the root needs "admin" to create the namespace, and
    # namespace-level grants fail with 404 until the namespace exists.
    grant_and_assign("admin")
    grant_and_assign("maintenance")
    grant_and_assign("sql")
    call("POST", f"{CATALOG_API}/{CATALOG}/namespaces", token=token,
         body={"namespace": [NAMESPACE]})
    grant_and_assign("ingest")
    grant_and_assign("ml")
    for principal, principal_role in PRINCIPALS.items():
        # The returned internal credentials are not used; clients authenticate with Entra ID.
        call("POST", f"{MGMT}/principals", token=token,
             body={"principal": {"name": principal}, "credentialRotationRequired": False})
        call("PUT", f"{MGMT}/principals/{principal}/principal-roles", token=token,
             body={"principalRole": {"name": principal_role}})

    print("polaris setup complete", flush=True)


if __name__ == "__main__":
    sys.exit(main())
