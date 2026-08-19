# HRIS Import Preview

A Django web application that accepts an HRIS CSV export and presents a useful
import preview — showing validation errors, org hierarchy, and reporting cycles
— **before** any data is written to a database.

## Setup & Run

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the development server
python manage.py runserver
```

Open **http://127.0.0.1:8000/** in your browser and upload `sample.csv`.

## Run Tests

```bash
python manage.py test preview
```

## Project Structure

```
├── manage.py                   # Django entry point
├── requirements.txt            # Just Django
├── sample.csv                  # Sample HRIS data with various edge cases
├── hris_project/
│   ├── settings.py             # Minimal Django config
│   └── urls.py                 # Root URL → preview app
└── preview/
    ├── parser.py               # Core logic (zero Django dependencies)
    ├── views.py                # Single upload/results view
    ├── urls.py                 # Single route
    ├── tests.py                # Automated tests
    └── templates/preview/
        ├── upload.html         # File upload form
        └── results.html        # Results display
```

### Architecture

All business logic lives in **`preview/parser.py`** with zero Django
dependencies. This makes it testable without a browser and reusable
outside Django.

The pipeline is four functions chained together:

1. **`parse_csv()`** — Parse CSV, normalize values (trim whitespace,
   lowercase emails), handle BOM.
2. **`validate_identities()`** — Check required fields, find duplicates.
   ALL rows sharing a duplicated ID or email are rejected.
3. **`resolve_managers()`** — Look up managers by ID and/or email, detect
   conflicts and self-references. Build the hierarchy.
4. **`detect_cycles()`** — Walk the manager chain to find employees that
   are members of a cycle (NOT employees who merely report into one).

The view (`views.py`) simply reads the uploaded file, calls `analyze_csv()`,
and passes the result to the template.

## Assumptions & Known Limitations

- **No database**: All analysis is done in-memory per request. Results are
  not persisted.
- **File size**: The upload limit is 10 MB (configurable in settings).
  For 100,000 rows this is more than sufficient.
- **Encoding**: Only UTF-8 (with optional BOM) is supported.
- **No authentication**: The app is open to anyone on the network.
- **CSRF**: Django's CSRF middleware is not enabled (no sessions needed).
  In production you would enable this.
- **Single file at a time**: The preview shows results for one upload.
  There's no history or comparison feature.

## Complexity Analysis

For a file with **n** employees:

| Step               | Time     | Space    |
|--------------------|----------|----------|
| Parse CSV          | O(n)     | O(n)     |
| Validate identities| O(n)     | O(n)     |
| Resolve managers   | O(n)     | O(n)     |
| Detect cycles      | O(n)     | O(n)     |
| **Total**          | **O(n)** | **O(n)** |

Each step makes a single pass over the data with dictionary lookups (O(1)
average). A 100,000-row file processes in well under a second.

## AI Tools Used

Google Gemini (Antigravity) was used to help scaffold the project structure,
generate boilerplate, and draft the sample CSV. All logic was reviewed,
understood, and validated by hand.
