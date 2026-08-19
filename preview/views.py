from django.shortcuts import render

from .parser import analyze_csv


def upload_view(request):
    """
    Single view that handles both the upload form (GET) and
    the file processing (POST).
    """
    if request.method != "POST":
        return render(request, "preview/upload.html")

    uploaded_file = request.FILES.get("csv_file")

    if not uploaded_file:
        return render(request, "preview/upload.html", {
            "error": "Please select a CSV file to upload.",
        })

    if not uploaded_file.name.lower().endswith(".csv"):
        return render(request, "preview/upload.html", {
            "error": "Please upload a file with a .csv extension.",
        })

    try:
        # Decode as UTF-8 with BOM support
        raw_bytes = uploaded_file.read()
        file_content = raw_bytes.decode("utf-8-sig")
        result = analyze_csv(file_content)
    except ValueError as e:
        # Known errors (e.g., missing headers)
        return render(request, "preview/upload.html", {
            "error": str(e),
        })
    except Exception as e:
        # Unexpected errors — show a clear message instead of a 500
        return render(request, "preview/upload.html", {
            "error": f"Could not process file: {e}",
        })

    return render(request, "preview/results.html", {"result": result})
