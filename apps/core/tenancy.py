"""Tenant context and row-level scoping for shared-schema workspaces."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from apps.saas.models import Tenant


class TenantContextRequired(RuntimeError):
    """Raised when tenant-owned data is written without a workspace in scope."""


class _AllTenants:
    """Sentinel for explicit cross-tenant system operations."""

    def __repr__(self) -> str:
        return "<all tenants>"


ALL_TENANTS = _AllTenants()
_current_tenant: ContextVar[object] = ContextVar("sora_current_tenant", default=None)


def current_tenant():
    return _current_tenant.get()


def set_current_tenant(tenant):
    return _current_tenant.set(tenant)


def reset_current_tenant(token) -> None:
    _current_tenant.reset(token)


@contextmanager
def tenant_context(tenant):
    """Scope ORM access to one workspace for the duration of the block."""
    token = set_current_tenant(tenant)
    try:
        yield tenant
    finally:
        reset_current_tenant(token)


@contextmanager
def all_tenants_context():
    """Explicitly opt out of tenant filtering for maintenance and bootstrap tasks."""
    token = set_current_tenant(ALL_TENANTS)
    try:
        yield ALL_TENANTS
    finally:
        reset_current_tenant(token)


def default_tenant():
    """Resolve the bootstrap workspace for management commands and seeders."""
    from apps.saas.models import Tenant

    return Tenant.objects.filter(slug="sora-demo").first() or Tenant.objects.order_by("id").first()


def _active_tenant():
    tenant = current_tenant()
    if tenant is None or tenant is ALL_TENANTS:
        return None
    return tenant


def _require_tenant() -> Tenant:
    tenant = _active_tenant()
    if tenant is None:
        raise TenantContextRequired(
            "Tenant-owned data was accessed without a workspace context. "
            "Wrap the operation in tenant_context(...) or assign tenant explicitly."
        )
    return tenant


class TenantQuerySet(models.QuerySet):
    """Scope reads and writes to the workspace active when they are evaluated.

    Querysets declared at import time (form fields, module constants) are
    built before any request has a tenant. They stay unresolved here and pick
    up the tenant on first evaluation instead of being frozen empty.
    """

    _tenant_resolved = False

    def _clone(self):
        clone = super()._clone()
        clone._tenant_resolved = self._tenant_resolved
        return clone

    def _resolved(self):
        self._tenant_resolved = True
        return self

    def _scoped_for_read(self):
        if self._tenant_resolved:
            return self
        tenant = current_tenant()
        clone = self._chain()._resolved()
        if tenant is ALL_TENANTS:
            return clone
        query = clone.query
        low, high = query.low_mark, query.high_mark
        has_limits = low != 0 or high is not None
        if has_limits:
            query.clear_limits()
        if tenant is None:
            query.set_empty()
            if has_limits:
                query.set_limits(low, high)
            return clone
        clone = clone.filter(tenant=tenant)
        if has_limits:
            clone.query.set_limits(low, high)
        return clone

    def _write_target(self):
        if self._tenant_resolved:
            return self
        if current_tenant() is None:
            raise TenantContextRequired(
                "Cannot modify tenant-owned data without a workspace context."
            )
        return self._scoped_for_read()

    def _fetch_all(self):
        if not self._tenant_resolved:
            self.__dict__.update(self._scoped_for_read().__dict__)
        return super()._fetch_all()

    def iterator(self, *args, **kwargs):
        if not self._tenant_resolved:
            return self._scoped_for_read().iterator(*args, **kwargs)
        return super().iterator(*args, **kwargs)

    def aiterator(self, *args, **kwargs):
        if not self._tenant_resolved:
            return self._scoped_for_read().aiterator(*args, **kwargs)
        return super().aiterator(*args, **kwargs)

    def count(self):
        if not self._tenant_resolved and self._result_cache is None:
            return self._scoped_for_read().count()
        return super().count()

    def exists(self):
        if not self._tenant_resolved and self._result_cache is None:
            return self._scoped_for_read().exists()
        return super().exists()

    def aggregate(self, *args, **kwargs):
        if not self._tenant_resolved:
            return self._scoped_for_read().aggregate(*args, **kwargs)
        return super().aggregate(*args, **kwargs)

    def update(self, **kwargs):
        target = self._write_target()
        if target is not self:
            return target.update(**kwargs)
        return super().update(**kwargs)

    def delete(self):
        target = self._write_target()
        if target is not self:
            return target.delete()
        return super().delete()

    def _raw_delete(self, using):
        target = self._write_target()
        if target is not self:
            return target._raw_delete(using)
        return super()._raw_delete(using)

    def create(self, **kwargs):
        tenant = kwargs.get("tenant")
        if tenant is None:
            kwargs["tenant"] = _require_tenant()
        elif _active_tenant() is not None and tenant.pk != _require_tenant().pk:
            raise TenantContextRequired("Cannot create a record for another workspace.")
        return super().create(**kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        tenant = _active_tenant()
        for obj in objs:
            if obj.tenant_id is None:
                if tenant is None:
                    raise TenantContextRequired(
                        "Cannot bulk-create tenant-owned records without a workspace context."
                    )
                obj.tenant = tenant
            elif tenant is not None and obj.tenant_id != tenant.pk:
                raise TenantContextRequired("Cannot bulk-create records for another workspace.")
        return super().bulk_create(objs, *args, **kwargs)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Default manager: fail closed when no workspace is active."""

    def get_queryset(self):
        queryset = super().get_queryset()
        tenant = current_tenant()
        if tenant is ALL_TENANTS:
            return queryset._resolved()
        if tenant is None:
            return queryset
        return queryset.filter(tenant=tenant)._resolved()

    def unscoped(self):
        return super().get_queryset()._resolved()

    def all_tenants(self):
        return super().get_queryset()._resolved()


class TenantScopedModel(models.Model):
    """Abstract base for every table that holds workspace-owned data."""

    tenant = models.ForeignKey(
        "saas.Tenant",
        on_delete=models.CASCADE,
        related_name="+",
        db_index=True,
        editable=False,
    )

    objects = TenantManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        default_manager_name = "objects"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.tenant_id is None:
            active = _active_tenant()
            if active is not None:
                self.tenant = active

    def save(self, *args, **kwargs):
        active = _active_tenant()
        if self.tenant_id is None:
            if active is None:
                raise TenantContextRequired(
                    "Cannot save tenant-owned data without a workspace context."
                )
            self.tenant = active
        elif active is not None and self.tenant_id != active.pk:
            raise TenantContextRequired("Cannot save a record for another workspace.")
        return super().save(*args, **kwargs)
