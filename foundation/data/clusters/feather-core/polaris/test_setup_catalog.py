"""Unit tests for setup-catalog.py: grant matrix, principals and the erasure_requests schema.

Stdlib only, no network: the module is loaded with placeholder environment values and its
main() is never called. Run with:
    python3 -m unittest discover -v -s foundation/data/clusters/feather-core/polaris -p 'test_*.py'
"""
import importlib.util
import os
import pathlib
import unittest
from unittest import mock

MODULE_PATH = pathlib.Path(__file__).with_name("setup-catalog.py")
PLACEHOLDER_ENV = {
    "POLARIS_URL": "http://polaris.invalid",
    "POLARIS_HEALTH_URL": "http://polaris.invalid/q/health/ready",
    "ROOT_CLIENT_ID": "test-root",
    "ROOT_CLIENT_SECRET": "test-secret",
    "BUCKET_NAME": "test-bucket",
    "S3_ENDPOINT": "http://s3.invalid",
}

PIPELINE_CLIENT = "872fb660-791b-482e-83ce-33252b9cca47"  # Polaris Lake Pipeline
ERASURE_CLIENT = "2579bb7f-518a-4d99-8bbc-a72dc8bde15f"  # Polaris Lake Erasure
INGEST_CLIENT = "41380977-879c-4c49-8e0d-fb1047f1bb7a"  # Polaris Corvus Ingest

WRITE_PRIVILEGES = {
    "TABLE_CREATE", "TABLE_WRITE_DATA", "TABLE_WRITE_PROPERTIES", "TABLE_DROP",
    "NAMESPACE_CREATE", "NAMESPACE_DROP", "NAMESPACE_WRITE_PROPERTIES",
    "CATALOG_MANAGE_CONTENT", "CATALOG_MANAGE_METADATA",
}


def load_setup_module():
    with mock.patch.dict(os.environ, PLACEHOLDER_ENV):
        spec = importlib.util.spec_from_file_location("setup_catalog", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def grant_keys(grants):
    """(type, namespace, table, privilege) per grant; table is "" for namespace and catalog grants."""
    keys = set()
    for grant in grants:
        namespace = ".".join(grant.get("namespace", [])) if grant["type"] != "catalog" else ""
        keys.add((grant["type"], namespace, grant.get("tableName", ""), grant["privilege"]))
    return keys


def expected(*entries):
    """Build grant keys from (type, namespace, table, [privileges]) entries."""
    keys = set()
    for kind, namespace, table, privileges in entries:
        for privilege in privileges:
            keys.add((kind, namespace, table, privilege))
    return keys


CAPTURE_READ = ("TABLE_READ_DATA", "TABLE_READ_PROPERTIES")
LAKE_NAMESPACES = ("refined", "features", "labels", "ref", "quality")


class SetupCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_setup_module()

    def test_pipeline_role_has_exactly_the_decision_4_grants(self):
        expected_keys = expected(
            *[("table", "capture", table, CAPTURE_READ)
              for table in ("raw", "events", "sessions", "segments", "session_ends")],
            ("namespace", "capture", "", ("NAMESPACE_READ_PROPERTIES", "TABLE_LIST")),
            *[("namespace", ns, "", (
                "TABLE_CREATE", "TABLE_READ_DATA", "TABLE_WRITE_DATA", "TABLE_WRITE_PROPERTIES",
                "TABLE_LIST", "NAMESPACE_LIST", "NAMESPACE_READ_PROPERTIES"))
              for ns in LAKE_NAMESPACES],
        )
        self.assertEqual(grant_keys(self.module.GRANTS["pipeline"]), expected_keys,
                         "pipeline grants differ from design decision 4")

    def test_erasure_role_has_exactly_the_decision_4_grants(self):
        expected_keys = expected(
            *[("table", "capture", table, CAPTURE_READ)
              for table in ("raw", "events", "sessions", "segments", "session_ends")],
            ("namespace", "capture", "", ("NAMESPACE_READ_PROPERTIES", "TABLE_LIST")),
            *[("namespace", ns, "", (
                "TABLE_READ_DATA", "TABLE_WRITE_DATA", "TABLE_WRITE_PROPERTIES",
                "TABLE_LIST", "NAMESPACE_LIST", "NAMESPACE_READ_PROPERTIES"))
              for ns in ("refined", "features", "labels", "quality")],
        )
        self.assertEqual(grant_keys(self.module.GRANTS["erasure"]), expected_keys,
                         "erasure grants differ from design decision 4")

    def test_pipeline_and_erasure_have_no_write_privilege_on_capture(self):
        for role in ("pipeline", "erasure"):
            for grant in self.module.GRANTS[role]:
                if grant["type"] == "catalog":
                    self.fail(f"{role} must not have a catalog-wide grant: {grant}")
                in_capture = "capture" in grant.get("namespace", [])
                if in_capture:
                    self.assertNotIn(grant["privilege"], WRITE_PRIVILEGES,
                                     f"{role} has a write privilege on capture: {grant}")

    def test_no_new_role_has_a_drop_privilege(self):
        for role in ("pipeline", "erasure"):
            privileges = {grant["privilege"] for grant in self.module.GRANTS[role]}
            self.assertNotIn("TABLE_DROP", privileges, f"{role} must not drop tables")

    def test_lake_sql_has_no_write_privilege(self):
        privileges = {grant["privilege"] for grant in self.module.GRANTS["sql"]}
        self.assertFalse(privileges & WRITE_PRIVILEGES, f"sql role writes: {privileges}")

    def test_ingest_gets_table_write_on_erasure_requests_only(self):
        added = grant_keys(self.module.GRANTS["ingest"]) - expected(
            ("namespace", "capture", "", (
                "TABLE_CREATE", "TABLE_WRITE_DATA", "TABLE_WRITE_PROPERTIES", "TABLE_LIST",
                "NAMESPACE_LIST", "NAMESPACE_READ_PROPERTIES")),
        )
        self.assertEqual(added, expected(
            ("table", "quality", "erasure_requests", ("TABLE_WRITE_DATA", "TABLE_READ_PROPERTIES")),
        ), "ingest must gain exactly table-level write on quality.erasure_requests")

    def test_lake_principals_are_mapped_to_their_roles(self):
        self.assertEqual(self.module.PRINCIPALS[PIPELINE_CLIENT], "lake_pipeline")
        self.assertEqual(self.module.PRINCIPALS[ERASURE_CLIENT], "lake_erasure")
        self.assertEqual(self.module.PRINCIPALS[INGEST_CLIENT], "corvus_ingest")

    def test_lake_principal_roles_are_assigned_to_their_catalog_roles(self):
        self.assertEqual(self.module.ASSIGNMENTS["lake_pipeline"], "pipeline")
        self.assertEqual(self.module.ASSIGNMENTS["lake_erasure"], "erasure")

    def test_erasure_requests_table_matches_lake_dags_contract(self):
        table = self.module.ERASURE_REQUESTS_TABLE
        self.assertEqual((table["namespace"], table["name"]), ("quality", "erasure_requests"))
        fields = [(f["name"], f["type"]) for f in table["schema"]["fields"]]
        self.assertEqual(fields, [
            ("request_id", "string"),
            ("player_key", "fixed[32]"),
            ("requested_at", "timestamptz"),
            ("capture_erased_at", "timestamptz"),
            ("capture_raw_snapshot_id", "long"),
            ("capture_events_snapshot_id", "long"),
            ("status", "string"),
            ("failure_reason", "string"),
            ("lake_run_id", "string"),
            ("completed_at", "timestamptz"),
        ])

    def test_erasure_requests_field_ids_are_unique(self):
        ids = [f["id"] for f in self.module.ERASURE_REQUESTS_TABLE["schema"]["fields"]]
        self.assertEqual(len(ids), len(set(ids)), "Iceberg field ids must be unique")


if __name__ == "__main__":
    unittest.main()
