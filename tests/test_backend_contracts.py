import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services"
EXPECTED_SERVICES = {
    "academics_service",
    "activities_service",
    "attendance_service",
    "auth_service",
    "barter_service",
    "communication_service",
    "leave_service",
    "marks_service",
    "notifications_service",
    "people_service",
    "reports_service",
    "schools_service",
    "timetable_service",
    "transport_service",
    "website_service",
}


class BackendContractTests(unittest.TestCase):
    def test_entire_backend_python_source_compiles(self):
        actual_services = {
            path.name for path in SERVICE_ROOT.iterdir() if path.is_dir()
        }
        self.assertEqual(EXPECTED_SERVICES, actual_services)

        python_files = [
            *ROOT.glob("gateway/*.py"),
            *ROOT.glob("common/*.py"),
            *SERVICE_ROOT.glob("*/*.py"),
        ]
        self.assertGreater(len(python_files), 50)
        for source_file in python_files:
            with self.subTest(file=source_file.relative_to(ROOT)):
                source = source_file.read_text(encoding="utf-8")
                compile(source, str(source_file), "exec")

        for service_name in sorted(EXPECTED_SERVICES):
            service_dir = SERVICE_ROOT / service_name
            main_source = (service_dir / "main.py").read_text(encoding="utf-8")
            self.assertIn("FastAPI(", main_source)
            self.assertIn('def health(', main_source)

    def test_every_service_has_runtime_and_router_contracts(self):
        for service_name in sorted(EXPECTED_SERVICES):
            service_dir = SERVICE_ROOT / service_name
            with self.subTest(service=service_name):
                self.assertTrue((service_dir / "Dockerfile").is_file())
                self.assertTrue((service_dir / "requirements.txt").is_file())

                router_path = service_dir / "router.py"
                router_source = router_path.read_text(encoding="utf-8")
                router_tree = ast.parse(router_source)
                self.assertIn("APIRouter", router_source)
                route_count = sum(
                    1
                    for node in ast.walk(router_tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "router"
                    and node.func.attr in {
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                        "api_route",
                    }
                )
                self.assertGreater(
                    route_count,
                    0,
                    f"{service_name} router has no HTTP endpoints",
                )

    def test_shared_models_define_database_tables(self):
        source = (ROOT / "common" / "models.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        table_names = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and any(
                        isinstance(target, ast.Name)
                        and target.id == "__tablename__"
                        for target in statement.targets
                    )
                    and isinstance(statement.value, ast.Constant)
                ):
                    table_names.append(statement.value.value)
        self.assertGreaterEqual(len(table_names), 25)
        self.assertEqual(len(table_names), len(set(table_names)))
        for table_name in table_names:
            with self.subTest(table=table_name):
                self.assertRegex(table_name, r"^[a-z][a-z0-9_]+$")

    def test_gateway_routes_cover_all_services(self):
        source = (ROOT / "gateway" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        route_map = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "ROUTE_MAP":
                        route_map = ast.literal_eval(node.value)
        self.assertIsNotNone(route_map)
        expected_keys = {
            "auth",
            "schools",
            "classes",
            "subjects",
            "periods",
            "holidays",
            "teachers",
            "staff",
            "students",
            "timetable",
            "routes",
            "broadcasts",
            "media",
            "website",
            "public",
        }
        self.assertTrue(expected_keys.issubset(route_map))

    def test_public_website_lookup_supports_school_name_slug(self):
        router = (
            ROOT / "services" / "website_service" / "router.py"
        ).read_text(encoding="utf-8")
        repository = (
            ROOT / "services" / "website_service" / "repository.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"/by-name/{school_name}"', router)
        self.assertIn("find_settings_by_slug", router)
        self.assertIn("def find_settings_by_slug", repository)
        self.assertIn("regexp_replace", repository)
        self.assertIn("is_active.is_(True)", repository)

    def test_website_settings_start_unpublished_until_go_live(self):
        model = (ROOT / "common" / "models.py").read_text(encoding="utf-8")
        settings_block = model.split("class WebsiteSettings(Base):", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('default=False, server_default="false"', settings_block)

    def test_website_read_schema_exposes_live_state_and_admin_can_edit_pre_live(self):
        schema = (
            ROOT / "services" / "website_service" / "schemas.py"
        ).read_text(encoding="utf-8")
        router = (
            ROOT / "services" / "website_service" / "router.py"
        ).read_text(encoding="utf-8")
        repository = (
            ROOT / "services" / "website_service" / "repository.py"
        ).read_text(encoding="utf-8")
        self.assertIn("is_active: bool", schema)
        self.assertIn("repo.get_settings_any(db, school_id)", router)
        upsert_block = repository.split("def upsert_settings(", 1)[1].split("def ", 1)[0]
        self.assertIn("get_settings_any(db, school_id)", upsert_block)

    def test_forgot_password_flow_emails_and_verifies_a_six_digit_otp(self):
        schema = (
            ROOT / "services" / "auth_service" / "schemas.py"
        ).read_text(encoding="utf-8")
        router = (
            ROOT / "services" / "auth_service" / "router.py"
        ).read_text(encoding="utf-8")
        service = (
            ROOT / "services" / "auth_service" / "service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("otp: str", schema)
        self.assertNotIn("ForgotPasswordVerifyRequest", schema)
        self.assertNotIn('"/forgot-password/verify"', router)
        self.assertIn('"/forgot-password/reset"', router)
        self.assertIn(
            "forgot_password_reset(db, payload.identifier, payload.otp, payload.new_password)",
            router,
        )
        self.assertIn("_store_otp", service)
        self.assertIn("send_email", service)
        self.assertIn("OTP_EXPIRE_SECONDS", service)

    def test_timetable_entry_contract_contains_school_and_audit_fields(self):
        model = (ROOT / "common" / "models.py").read_text(encoding="utf-8")
        schema = (
            ROOT / "services" / "timetable_service" / "schemas.py"
        ).read_text(encoding="utf-8")
        router = (
            ROOT / "services" / "timetable_service" / "router.py"
        ).read_text(encoding="utf-8")
        for field in (
            "school_id",
            "period_start_time",
            "period_end_time",
            "created_on",
            "created_by",
        ):
            self.assertIn(field, model)
            self.assertIn(field, schema)
        self.assertIn('"/entry/{entry_id}"', router)
        self.assertRegex(router, r'methods=\["POST", "PUT", "PATCH"\]')

    def test_broadcast_contract_contains_sender_role_and_timestamp(self):
        schema = (
            ROOT / "services" / "communication_service" / "schemas.py"
        ).read_text(encoding="utf-8")
        for field in ("role_name", "sender_name", "created_at"):
            self.assertIn(field, schema)
        self.assertIn("class BroadcastUpdate", schema)

    def test_migrations_cover_current_schema_changes(self):
        migrations = (ROOT / "db-migrations.sql").read_text(encoding="utf-8")
        required_fragments = (
            "timetable_entries",
            "school_id INTEGER",
            "period_start_time TIME",
            "period_end_time TIME",
            "created_on TIMESTAMP",
            "created_by INTEGER",
            "broadcasts_set_created_at_ist",
            "period_time TYPE VARCHAR(31)",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, migrations)

    def test_database_init_and_migration_files_are_present(self):
        for filename in ("db-init-schema.sql", "db-migrations.sql"):
            path = ROOT / filename
            self.assertTrue(path.is_file())
            content = path.read_text(encoding="utf-8")
            self.assertGreater(len(content), 20)
            self.assertRegex(content, r"\b(CREATE|ALTER|DO)\b")
            self.assertNotIn("\x00", content)

    def test_gateway_and_shared_package_compile(self):
        for source_file in (
            ROOT / "gateway" / "main.py",
            *ROOT.glob("common/*.py"),
        ):
            with self.subTest(file=source_file.relative_to(ROOT)):
                compile(
                    source_file.read_text(encoding="utf-8"),
                    str(source_file),
                    "exec",
                )

    def test_no_python_source_contains_obvious_merge_markers(self):
        for source_file in [ROOT / "gateway" / "main.py", *ROOT.glob("common/*.py"), *SERVICE_ROOT.glob("*/*.py")]:
            source = source_file.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"^(<<<<<<<|=======|>>>>>>>).*$", source, re.MULTILINE),
                str(source_file),
            )


if __name__ == "__main__":
    unittest.main()
