"""Tenant-scoped upload paths and private media serving."""

from __future__ import annotations

import mimetypes
import os
import uuid
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.utils._os import safe_join

from apps.core.tenancy import ALL_TENANTS, TenantContextRequired, current_tenant

INLINE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}


def tenant_media_prefix() -> str:
    tenant = current_tenant()
    if tenant is None or tenant is ALL_TENANTS:
        raise TenantContextRequired("Uploads require an active workspace context.")
    return f"tenants/{tenant.pk}"


def tenant_media_path(category: str, filename: str = "") -> str:
    """Build an isolated storage path such as ``tenants/3/receipts/<uuid>.pdf``."""
    cleaned = "/".join(
        part for part in PurePosixPath(category.replace("\\", "/")).parts if part not in {"", ".", ".."}
    )
    suffix = Path(filename).suffix.lower() if filename else ""
    if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
        suffix = ""
    prefix = f"{tenant_media_prefix()}/{cleaned}/" if cleaned else f"{tenant_media_prefix()}/"
    return f"{prefix}{uuid.uuid4().hex}{suffix}"


def save_tenant_upload(category: str, upload, *, filename: str = "") -> str:
    return default_storage.save(tenant_media_path(category, filename or upload.name), upload)


def media_path_tenant(path: str) -> int | None:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if len(parts) >= 3 and parts[0] == "tenants":
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None


def serve_private_media(request, path: str) -> FileResponse:
    """Serve an upload only to members of the workspace that owns it."""
    tenant_id = media_path_tenant(path)
    tenant = getattr(request, "tenant", None)
    if tenant_id is None or tenant is None or tenant.pk != tenant_id:
        raise Http404

    full_path = safe_join(str(settings.MEDIA_ROOT), path)
    if full_path is None or not os.path.isfile(full_path):
        raise Http404

    content_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    inline = content_type in INLINE_TYPES
    response = FileResponse(
        open(full_path, "rb"),
        content_type=content_type if inline else "application/octet-stream",
    )
    filename = os.path.basename(full_path)
    response["Content-Disposition"] = (
        f'inline; filename="{filename}"' if inline else f'attachment; filename="{filename}"'
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=300"
    return response
