"""Shared UI helpers for module views."""

from __future__ import annotations

import csv
import io

from django.core.paginator import Paginator
from django.http import HttpResponse


def paginate(request, queryset, per_page: int = 15):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


def csv_response(filename: str, headers: list[str], rows) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    response = HttpResponse(buffer.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
