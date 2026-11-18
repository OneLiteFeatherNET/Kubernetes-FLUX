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

CATALOG = "corvus"
NAMESPACE = "capture"
MGMT = f"{POLARIS}/api/management/v1"
CATALOG_API = f"{POLARIS}/api/catalog/v1"

# principal role -> catalog role
ASSIGNMENTS = {
    "corvus_ingest": "ingest",
    "corvus_ml": "ml",
    "lake_maintenance": "maintenance",
    # Lets the root principal create the namespace through the Iceberg REST API.
    "service_admin": "admin",
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
    "maintenance": [{"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"}],
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
            with urllib.request.urlopen(f"{POLARIS}/q/health/ready", timeout=5) as r:
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

    for catalog_role, grants in GRANTS.items():
        for grant in grants:
            call("PUT", f"{MGMT}/catalogs/{CATALOG}/catalog-roles/{catalog_role}/grants",
                 token=token, body={"grant": grant})

    for principal_role, catalog_role in ASSIGNMENTS.items():
        call("PUT", f"{MGMT}/principal-roles/{principal_role}/catalog-roles/{CATALOG}",
             token=token, body={"catalogRole": {"name": catalog_role}})

    call("POST", f"{CATALOG_API}/{CATALOG}/namespaces", token=token,
         body={"namespace": [NAMESPACE]})
    print("polaris setup complete", flush=True)


if __name__ == "__main__":
    sys.exit(main())
