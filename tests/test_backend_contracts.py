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
        settings_block = model.split("class WebsiteSettings", 1)[1].split("\nclass ", 1)[0]
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

    def test_forgot_password_reset_requires_emailed_token_and_never_returns_it(self):
        schema = (
            ROOT / "services" / "auth_service" / "schemas.py"
        ).read_text(encoding="utf-8")
        router = (
            ROOT / "services" / "auth_service" / "router.py"
        ).read_text(encoding="utf-8")
        service = (
            ROOT / "services" / "auth_service" / "service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("reset_token: str", schema)
        self.assertIn('"/forgot-password/verify"', router)
        self.assertIn('"/forgot-password/reset"', router)
        self.assertIn(
            "forgot_password_reset(db, payload.identifier, payload.reset_token, payload.new_password)",
            router,
        )
        self.assertIn("_issue_reset_token", service)
        self.assertIn("_deliver_reset_token", service)
        self.assertIn("send_email", service)
        self.assertIn("password_reset_token", service)
        self.assertIn('"reset_token": None', router)

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

    def test_broadcast_contract_supports_route_scope(self):
        model = (ROOT / "common" / "models.py").read_text(encoding="utf-8")
        enums = (ROOT / "common" / "enums.py").read_text(encoding="utf-8")
        schema = (
            ROOT / "services" / "communication_service" / "schemas.py"
        ).read_text(encoding="utf-8")
        repository = (
            ROOT / "services" / "communication_service" / "repository.py"
        ).read_text(encoding="utf-8")
        migrations = (ROOT / "db-migrations.sql").read_text(encoding="utf-8")
        for source in (model, schema, repository):
            self.assertIn("route_id", source)
        for source in (enums, schema, repository):
            self.assertIn('"route"', source)
        self.assertIn("broadcasts_route_id_fkey", migrations)
        self.assertIn("route_id INTEGER", migrations)
        self.assertIn("'route'", migrations)

    def test_marks_service_contract_supports_class_grade_and_teacher_scope(self):
        router_path = ROOT / "services" / "marks_service" / "router.py"
        repository_path = ROOT / "services" / "marks_service" / "repository.py"
        schema_path = ROOT / "services" / "marks_service" / "schemas.py"
        model_path = ROOT / "common" / "models.py"
        router = router_path.read_text(encoding="utf-8")
        repository = repository_path.read_text(encoding="utf-8")
        schema = schema_path.read_text(encoding="utf-8")
        model = model_path.read_text(encoding="utf-8")

        # Reading surface: teacher-scoped class marks plus per-student marks.
        self.assertIn('"/class/{class_id}"', router)
        self.assertIn('"/student/{student_id}"', router)
        self.assertIn("get_subject_ids_for_class", repository)
        self.assertIn("get_for_class", repository)
        self.assertIn('require_role("teacher", "admin")', router)

        # Writing surface: upsert that only a teacher of the subject may use.
        self.assertIn('"/{student_id}/{subject_id}"', router)
        self.assertIn("@router.put(", router)
        self.assertIn("teacher_can_grade", repository)
        self.assertIn("upsert_mark", repository)

        # Shared schema guarantees: term default "Term 1" and score 0..100,
        # riding on the (student_id, subject_id, term) unique upsert target.
        self.assertIn('term: str = "Term 1"', schema)
        self.assertIn("ge=0, le=100", schema)
        self.assertIn('UniqueConstraint("student_id", "subject_id", "term")', model)

    def test_parent_reads_are_scoped_to_own_children(self):
        """Parents must not read other families' marks/attendance/children."""
        marks_router = (ROOT / "services" / "marks_service" / "router.py").read_text(encoding="utf-8")
        attendance_router = (ROOT / "services" / "attendance_service" / "router.py").read_text(encoding="utf-8")
        people_router = (ROOT / "services" / "people_service" / "router.py").read_text(encoding="utf-8")

        for name, router in (("marks", marks_router), ("attendance", attendance_router)):
            self.assertIn("is_parent_of", router, f"{name} router lacks parent scoping")
            self.assertIn('current_user.role == "parent"', router, f"{name} router lacks parent branch")

        # Parent-facing children listing must bind the id to the caller.
        self.assertIn("parent_id = current_user.linked_person_id", people_router)

    def test_marks_audit_columns_and_scope_helpers_contract(self):
        """Admin edits must be auditable and reads scoped per role."""
        model = (ROOT / "common" / "models.py").read_text(encoding="utf-8")
        schema = (ROOT / "services" / "marks_service" / "schemas.py").read_text(encoding="utf-8")
        router = (ROOT / "services" / "marks_service" / "router.py").read_text(encoding="utf-8")
        repository = (ROOT / "services" / "marks_service" / "repository.py").read_text(encoding="utf-8")

        # Admins have no teacher row, so the acting user id is stored separately.
        for source in (model, schema, repository, router):
            self.assertIn("updated_by_user", source)
        self.assertIn("updated_by_user=current_user.user_id", router)

        # Existence + per-role scope helpers used by the read/write guards.
        for helper in ("student_exists", "subject_exists", "student_in_school", "teacher_teaches_student"):
            self.assertIn(helper, repository)
            self.assertIn(helper, router)
        self.assertIn("NotFoundError", router)
        self.assertIn('current_user.role == "teacher"', router)

    def test_attendance_status_and_teacher_scope_contract(self):
        schema = (ROOT / "services" / "attendance_service" / "schemas.py").read_text(encoding="utf-8")
        router = (ROOT / "services" / "attendance_service" / "router.py").read_text(encoding="utf-8")
        repository = (ROOT / "services" / "attendance_service" / "repository.py").read_text(encoding="utf-8")

        # Only the two canonical statuses may be persisted.
        self.assertIn('Literal["Present", "Absent"]', schema)

        for helper in ("student_exists", "student_in_school", "teacher_teaches_student"):
            self.assertIn(helper, repository)
            self.assertIn(helper, router)
        self.assertIn("NotFoundError", router)
        self.assertIn('current_user.role == "teacher"', router)

    def test_parent_children_endpoints_contract(self):
        router = (ROOT / "services" / "people_service" / "router.py").read_text(encoding="utf-8")
        repository = (ROOT / "services" / "people_service" / "repository.py").read_text(encoding="utf-8")

        # Self-service route for the logged-in parent...
        self.assertIn('"/parents/me/children"', router)
        self.assertIn('require_role("parent")', router)
        # ...plus the school-scoped lookup used by admins.
        self.assertIn("get_parent", repository)
        self.assertIn("children_of_parent", repository)
        self.assertIn("get_parent(db, school_id, parent_id)", router)

    def test_timetable_writes_are_admin_only(self):
        router_path = ROOT / "services" / "timetable_service" / "router.py"
        router = router_path.read_text(encoding="utf-8")
        writes = (
            'router.post("/class/{class_id}/period"',
            'router.api_route(',
            'router.patch("/entry/{entry_id}/clear-override"',
            'router.delete("/entry/{entry_id}"',
        )
        for marker in writes:
            self.assertIn(marker, router)
        # Every write endpoint must be admin-only — no teacher-grade writes.
        self.assertEqual(router.count('require_role("admin")'), 4)
        self.assertNotIn('require_role("teacher", "admin")', router)
        # Reads stay open to parents, teachers, and admins.
        self.assertEqual(router.count('require_role("parent", "teacher", "admin")'), 2)

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
            "updated_by_user",
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
