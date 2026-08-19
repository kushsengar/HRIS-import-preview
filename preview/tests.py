"""
Automated tests for the HRIS parser module.

Run with:  python manage.py test preview
"""

from django.test import TestCase

from .parser import (
    analyze_csv,
    detect_cycles,
    parse_csv,
    resolve_managers,
    validate_identities,
)


class TestDuplicateIdentities(TestCase):
    """
    Test that ALL rows sharing a duplicated employee_id or email
    are marked invalid — not just the second occurrence.
    """

    def test_duplicate_employee_id_rejects_all_rows(self):
        csv_text = (
            "employee_id,employee_name,email,manager_id,manager_email,department\n"
            "E001,Alice,alice@example.com,,,HR\n"
            "E001,Also Alice,also_alice@example.com,,,HR\n"
            "E002,Bob,bob@example.com,,,HR\n"
        )
        rows = parse_csv(csv_text)
        valid, errors = validate_identities(rows)

        # Both E001 rows should be rejected
        valid_ids = [r["employee_id"] for r in valid]
        self.assertNotIn("E001", valid_ids)
        self.assertIn("E002", valid_ids)
        self.assertEqual(len(valid), 1)

        # Two errors for the two E001 rows
        dup_errors = [e for e in errors if "Duplicate" in e["message"]]
        self.assertEqual(len(dup_errors), 2)

    def test_duplicate_email_rejects_all_rows(self):
        csv_text = (
            "employee_id,employee_name,email,manager_id,manager_email,department\n"
            "E001,Alice,shared@example.com,,,HR\n"
            "E002,Bob,SHARED@Example.com,,,HR\n"  # same after lowercase
            "E003,Carol,unique@example.com,,,HR\n"
        )
        rows = parse_csv(csv_text)
        valid, errors = validate_identities(rows)

        valid_ids = [r["employee_id"] for r in valid]
        self.assertNotIn("E001", valid_ids)
        self.assertNotIn("E002", valid_ids)
        self.assertIn("E003", valid_ids)
        self.assertEqual(len(valid), 1)


class TestCycleDetection(TestCase):
    """
    Test that cycle detection correctly identifies only employees
    who are part of the cycle — NOT employees who merely report
    into a cycle.
    """

    def test_simple_cycle(self):
        # A→B→C→A  (3-node cycle)
        relationships = {"A": "B", "B": "C", "C": "A"}
        cycles = detect_cycles(relationships)
        self.assertEqual(cycles, {"A", "B", "C"})

    def test_employee_reporting_into_cycle_is_not_flagged(self):
        # D→A→B→C→A    D reports to A, but only A/B/C are in the cycle
        relationships = {"A": "B", "B": "C", "C": "A", "D": "A"}
        cycles = detect_cycles(relationships)
        self.assertEqual(cycles, {"A", "B", "C"})
        self.assertNotIn("D", cycles)

    def test_no_cycle(self):
        # Simple chain: A→B→C (C is a root, not in relationships)
        relationships = {"A": "B", "B": "C"}
        cycles = detect_cycles(relationships)
        self.assertEqual(cycles, set())

    def test_two_node_cycle(self):
        # A→B→A
        relationships = {"A": "B", "B": "A"}
        cycles = detect_cycles(relationships)
        self.assertEqual(cycles, {"A", "B"})


class TestManagerResolution(TestCase):
    """Test manager lookup rules including conflicts and self-management."""

    def test_conflicting_manager_fields_produces_error(self):
        """When manager_id and manager_email point to different people."""
        employees = [
            {"row_number": 2, "employee_id": "E001", "employee_name": "Alice",
             "email": "alice@x.com", "manager_id": "", "manager_email": "",
             "department": "HR"},
            {"row_number": 3, "employee_id": "E002", "employee_name": "Bob",
             "email": "bob@x.com", "manager_id": "", "manager_email": "",
             "department": "HR"},
            {"row_number": 4, "employee_id": "E003", "employee_name": "Carol",
             "email": "carol@x.com", "manager_id": "E001",
             "manager_email": "bob@x.com", "department": "HR"},
        ]
        roots, reports, relationships, errors = resolve_managers(employees)

        # E003 should have an error because E001 ≠ E002
        self.assertEqual(len(errors), 1)
        self.assertIn("different employees", errors[0]["message"])
        self.assertEqual(errors[0]["employee_id"], "E003")

        # E003 should NOT appear in any relationship
        self.assertNotIn("E003", relationships)

    def test_self_manager_produces_error(self):
        employees = [
            {"row_number": 2, "employee_id": "E001", "employee_name": "Alice",
             "email": "alice@x.com", "manager_id": "E001", "manager_email": "",
             "department": "HR"},
        ]
        _, _, _, errors = resolve_managers(employees)
        self.assertEqual(len(errors), 1)
        self.assertIn("manages themselves", errors[0]["message"])


class TestBOMHandling(TestCase):
    """Test that UTF-8 BOM is handled correctly."""

    def test_csv_with_bom_parses_correctly(self):
        csv_text = (
            "\ufeff"  # UTF-8 BOM
            "employee_id,employee_name,email,manager_id,manager_email,department\n"
            "E001,Alice,alice@example.com,,,HR\n"
        )
        rows = parse_csv(csv_text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["employee_id"], "E001")


class TestFullPipeline(TestCase):
    """End-to-end test of analyze_csv."""

    def test_full_analysis(self):
        csv_text = (
            "employee_id,employee_name,email,manager_id,manager_email,department\n"
            "E001,Alice,alice@x.com,,,Exec\n"
            "E002,Bob,bob@x.com,E001,,Eng\n"
            "E003,Carol,carol@x.com,E002,,Eng\n"
        )
        result = analyze_csv(csv_text)

        self.assertEqual(result["total_rows"], 3)
        self.assertEqual(result["accepted_count"], 3)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(len(result["roots"]), 1)
        self.assertEqual(result["roots"][0]["employee_id"], "E001")
        self.assertEqual(len(result["managers"]), 2)  # E001 and E002 manage someone
        self.assertEqual(len(result["cycle_members"]), 0)
