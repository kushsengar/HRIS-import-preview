"""
HRIS CSV parser and hierarchy analyzer.

This module contains ALL the business logic for parsing, validating,
and analyzing HRIS CSV data. It has zero Django dependencies so it
can be tested and reused independently.

Pipeline:  parse_csv  →  validate_identities  →  resolve_managers  →  detect_cycles
"""

import csv
import io


# ---------------------------------------------------------------------------
# Step 1: Parse CSV
# ---------------------------------------------------------------------------

REQUIRED_HEADERS = {
    "employee_id", "employee_name", "email",
    "manager_id", "manager_email", "department",
}


def parse_csv(file_content: str) -> list[dict]:
    """
    Parse CSV text into a list of normalized row dicts.

    Normalization rules:
    - Trim whitespace from every value.
    - Lowercase email and manager_email.
    - Keep employee_id case-sensitive.

    Handles UTF-8 BOM if present.
    """
    # Strip BOM if present
    if file_content.startswith("\ufeff"):
        file_content = file_content[1:]

    reader = csv.DictReader(io.StringIO(file_content))

    # Validate that all required headers exist
    actual_headers = set(reader.fieldnames or [])
    missing = REQUIRED_HEADERS - actual_headers
    if missing:
        raise ValueError(f"Missing required CSV headers: {', '.join(sorted(missing))}")

    rows = []
    for i, raw_row in enumerate(reader, start=2):  # Row 1 = header
        row = {
            "row_number": i,
            "employee_id": (raw_row.get("employee_id") or "").strip(),
            "employee_name": (raw_row.get("employee_name") or "").strip(),
            "email": (raw_row.get("email") or "").strip().lower(),
            "manager_id": (raw_row.get("manager_id") or "").strip(),
            "manager_email": (raw_row.get("manager_email") or "").strip().lower(),
            "department": (raw_row.get("department") or "").strip(),
        }
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Step 2: Validate employee identities
# ---------------------------------------------------------------------------

def validate_identities(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Check that every row has a unique employee_id and email.

    Returns:
        (valid_employees, errors)

    Every row sharing a duplicated employee ID or email
    is invalid.  So ALL rows with the same ID/email are rejected, not
    just the second occurrence.
    """
    errors = []

    # Count occurrences to find duplicates
    id_counts: dict[str, int] = {}
    email_counts: dict[str, int] = {}
    for row in rows:
        if row["employee_id"]:
            id_counts[row["employee_id"]] = id_counts.get(row["employee_id"], 0) + 1
        if row["email"]:
            email_counts[row["email"]] = email_counts.get(row["email"], 0) + 1

    duplicate_ids = {eid for eid, count in id_counts.items() if count > 1}
    duplicate_emails = {email for email, count in email_counts.items() if count > 1}

    valid = []
    for row in rows:
        row_errors = []

        # Required field checks
        if not row["employee_id"]:
            row_errors.append("Missing employee_id")
        elif row["employee_id"] in duplicate_ids:
            row_errors.append(f"Duplicate employee_id: {row['employee_id']}")

        if not row["email"]:
            row_errors.append("Missing email")
        elif row["email"] in duplicate_emails:
            row_errors.append(f"Duplicate email: {row['email']}")

        if row_errors:
            for msg in row_errors:
                errors.append({
                    "row_number": row["row_number"],
                    "employee_id": row["employee_id"] or "(empty)",
                    "message": msg,
                })
        else:
            valid.append(row)

    return valid, errors


# ---------------------------------------------------------------------------
# Step 3: Resolve manager relationships
# ---------------------------------------------------------------------------

def resolve_managers(employees: list[dict]) -> tuple[list[dict], dict, dict, list[dict]]:
    """
    Resolve manager references and build the hierarchy.

    Returns:
        (roots, manager_reports, relationships, errors)

        roots           - employees with no manager (both fields blank)
        manager_reports - dict: manager_id → [list of direct reports]
        relationships   - dict: employee_id → manager_id (for cycle detection)
        errors          - manager resolution errors
    """
    # Build lookup maps from valid employees
    by_id = {emp["employee_id"]: emp for emp in employees}
    by_email = {emp["email"]: emp for emp in employees}

    roots = []
    manager_reports: dict[str, list[dict]] = {}
    relationships: dict[str, str] = {}
    errors = []

    for emp in employees:
        mid = emp["manager_id"]
        memail = emp["manager_email"]

        # ---- Both blank → root employee ----
        if not mid and not memail:
            roots.append(emp)
            continue

        # ---- Resolve the manager reference ----
        manager = None
        error = None

        if mid and memail:
            # Both supplied: both must point to the same employee
            mgr_by_id = by_id.get(mid)
            mgr_by_email = by_email.get(memail)

            if mgr_by_id is None and mgr_by_email is None:
                error = f"Manager not found by ID '{mid}' or email '{memail}'"
            elif mgr_by_id is None:
                error = f"Manager not found by ID '{mid}'"
            elif mgr_by_email is None:
                error = f"Manager not found by email '{memail}'"
            elif mgr_by_id["employee_id"] != mgr_by_email["employee_id"]:
                error = (
                    f"Manager ID '{mid}' and email '{memail}' "
                    f"refer to different employees"
                )
            else:
                manager = mgr_by_id

        elif mid:
            # Only manager_id supplied
            manager = by_id.get(mid)
            if manager is None:
                error = f"Manager not found by ID '{mid}'"

        else:
            # Only manager_email supplied
            manager = by_email.get(memail)
            if manager is None:
                error = f"Manager not found by email '{memail}'"

        # ---- Self-management check ----
        if manager and manager["employee_id"] == emp["employee_id"]:
            error = "Employee manages themselves"
            manager = None

        # ---- Record result ----
        if error:
            errors.append({
                "row_number": emp["row_number"],
                "employee_id": emp["employee_id"],
                "message": error,
            })
            # Spec: employee with manager error stays accepted,
            # but produces no relationship and is NOT a root.
        else:
            mgr_id = manager["employee_id"]
            relationships[emp["employee_id"]] = mgr_id
            manager_reports.setdefault(mgr_id, []).append(emp)

    return roots, manager_reports, relationships, errors


# ---------------------------------------------------------------------------
# Step 4: Detect reporting cycles
# ---------------------------------------------------------------------------

def detect_cycles(relationships: dict[str, str]) -> set[str]:
    """
    Find employees that are members of a reporting cycle.

    Uses a path-following algorithm on the functional graph
    (each node has at most one outgoing edge = their manager).

    IMPORTANT: An employee who reports INTO a cycle (but is not
    part of the loop itself) is NOT classified as a cycle member.

    Time complexity:  O(n) — each node is visited at most once.
    Space complexity: O(n) — for the visited set and path list.
    """
    cycle_members: set[str] = set()
    visited: set[str] = set()  # Fully processed nodes

    for start_node in relationships:
        if start_node in visited:
            continue

        # Follow the manager chain, recording the path
        path: list[str] = []
        path_set: set[str] = set()
        current = start_node

        while (current not in visited
               and current in relationships
               and current not in path_set):
            path.append(current)
            path_set.add(current)
            current = relationships[current]

        # Did we loop back to a node in our current path?
        if current in path_set:
            # Everything from that node onward in the path is the cycle
            cycle_start = path.index(current)
            for node in path[cycle_start:]:
                cycle_members.add(node)

        # Mark everything in this path as fully processed
        visited.update(path)

    return cycle_members


# ---------------------------------------------------------------------------
# Main entry point: run the full analysis pipeline
# ---------------------------------------------------------------------------

def analyze_csv(file_content: str) -> dict:
    """
    Run the full pipeline: parse → validate → resolve → detect cycles.

    Returns a dict with all the data the view needs to render results.
    """
    rows = parse_csv(file_content)
    total_rows = len(rows)

    valid_employees, identity_errors = validate_identities(rows)
    roots, manager_reports, relationships, manager_errors = resolve_managers(valid_employees)
    cycle_members = detect_cycles(relationships)

    # Combine all errors into one list, sorted by row number
    all_errors = sorted(
        identity_errors + manager_errors,
        key=lambda e: e["row_number"],
    )

    # Build a lookup so the template can show manager names
    emp_lookup = {emp["employee_id"]: emp for emp in valid_employees}

    # Convert manager_reports to a template-friendly list
    manager_list = []
    for mgr_id, reports in sorted(manager_reports.items()):
        mgr = emp_lookup.get(mgr_id, {})
        manager_list.append({
            "employee_id": mgr_id,
            "employee_name": mgr.get("employee_name", "Unknown"),
            "department": mgr.get("department", ""),
            "direct_report_count": len(reports),
            "direct_reports": reports,
        })

    # Build cycle member details for display
    cycle_list = [emp_lookup[eid] for eid in cycle_members if eid in emp_lookup]
    cycle_list.sort(key=lambda e: e["row_number"])

    return {
        "total_rows": total_rows,
        "accepted_count": len(valid_employees),
        "accepted_employees": valid_employees,
        "errors": all_errors,
        "error_count": len(all_errors),
        "roots": roots,
        "managers": manager_list,
        "cycle_members": cycle_list,
    }
