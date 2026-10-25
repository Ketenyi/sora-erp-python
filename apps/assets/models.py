"""Operational asset register, custody, maintenance and depreciation."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel

ZERO = Decimal("0.00")
AGENT_ONLINE_WINDOW = timedelta(minutes=20)
AGENT_STALE_WINDOW = timedelta(hours=24)

ASSET_STATUSES = [
    ("available", "Available"),
    ("in_use", "In Use"),
    ("under_maintenance", "Under Maintenance"),
    ("retired", "Retired"),
    ("disposed", "Disposed"),
]

ASSET_CONDITIONS = [
    ("new", "New"),
    ("good", "Good"),
    ("fair", "Fair"),
    ("poor", "Poor"),
    ("damaged", "Damaged"),
]

DEPRECIATION_METHODS = [
    ("straight_line", "Straight Line"),
    ("declining_balance", "Declining Balance"),
    ("none", "Do Not Depreciate"),
]


class AssetCategory(TenantScopedModel):
    category_code = models.CharField(max_length=50)
    category_name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    useful_life_years = models.PositiveIntegerField(default=5)
    depreciation_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("20.00"))
    status = models.CharField(
        max_length=8,
        choices=[("active", "Active"), ("inactive", "Inactive")],
        default="active",
        db_index=True,
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "assets_categories"
        unique_together = (("tenant", "category_code"),)
        ordering = ["category_name"]
        verbose_name_plural = "asset categories"

    def __str__(self):
        return self.category_name


class Asset(TenantScopedModel):
    asset_code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)
    category = models.ForeignKey(
        AssetCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="assets"
    )
    serial_number = models.CharField(max_length=120, blank=True)
    model_name = models.CharField(max_length=120, blank=True)
    manufacturer = models.CharField(max_length=120, blank=True)
    location = models.CharField(max_length=150, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    salvage_value = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    useful_life_years = models.PositiveIntegerField(default=5)
    depreciation_method = models.CharField(
        max_length=18, choices=DEPRECIATION_METHODS, default="straight_line"
    )
    depreciation_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("20.00"))
    accumulated_depreciation = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    status = models.CharField(
        max_length=18, choices=ASSET_STATUSES, default="available", db_index=True
    )
    condition = models.CharField(max_length=8, choices=ASSET_CONDITIONS, default="good")
    assigned_to = models.ForeignKey(
        "core.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="assets_held"
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assets_register"
        unique_together = (("tenant", "asset_code"),)
        ordering = ["asset_code"]
        indexes = [models.Index(fields=["status", "category"])]

    def __str__(self):
        return f"{self.asset_code} · {self.name}"

    @property
    def book_value(self):
        return max((self.purchase_cost or ZERO) - (self.accumulated_depreciation or ZERO), ZERO)

    @property
    def depreciation_percent(self):
        if not self.purchase_cost:
            return 0.0
        return round(float((self.accumulated_depreciation or ZERO) / self.purchase_cost * 100), 1)

    def monthly_depreciation(self):
        cost = self.purchase_cost or ZERO
        salvage = min(self.salvage_value or ZERO, cost)
        if self.depreciation_method == "none" or cost <= salvage:
            return ZERO
        life = max(self.useful_life_years or 1, 1)
        if self.depreciation_method == "declining_balance":
            rate = (self.depreciation_rate or Decimal("20.00")) / Decimal("100")
            return max((self.book_value * rate / Decimal("12")).quantize(Decimal("0.01")), ZERO)
        return ((cost - salvage) / Decimal(life) / Decimal("12")).quantize(Decimal("0.01"))


class AssetAssignment(TenantScopedModel):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="assignments")
    employee = models.ForeignKey(
        "core.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="asset_assignments"
    )
    custodian_name = models.CharField(max_length=150, blank=True)
    assigned_by = models.IntegerField(null=True)
    assigned_at = models.DateTimeField(default=timezone.now)
    expected_return_date = models.DateField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    condition_out = models.CharField(max_length=8, choices=ASSET_CONDITIONS, default="good")
    condition_in = models.CharField(max_length=8, choices=ASSET_CONDITIONS, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "assets_assignments"
        ordering = ["-assigned_at", "-id"]

    def __str__(self):
        return f"{self.asset} → {self.custodian_name or self.employee_id}"

    @property
    def is_open(self):
        return self.returned_at is None


class AssetMaintenance(TenantScopedModel):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="maintenance")
    maintenance_type = models.CharField(
        max_length=12,
        choices=[
            ("preventive", "Preventive"),
            ("corrective", "Corrective"),
            ("inspection", "Inspection"),
            ("calibration", "Calibration"),
        ],
        default="preventive",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    scheduled_date = models.DateField()
    completed_date = models.DateField(null=True, blank=True)
    vendor = models.CharField(max_length=150, blank=True)
    cost = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    status = models.CharField(
        max_length=12,
        choices=[
            ("scheduled", "Scheduled"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="scheduled",
        db_index=True,
    )
    performed_by = models.ForeignKey(
        "core.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="asset_maintenance_jobs"
    )
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "assets_maintenance"
        ordering = ["scheduled_date", "id"]

    def __str__(self):
        return f"{self.asset.asset_code} · {self.title}"


class AssetDepreciation(TenantScopedModel):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="depreciation_entries")
    period_date = models.DateField()
    period_label = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    accumulated_total = models.DecimalField(max_digits=15, decimal_places=2)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "assets_depreciation"
        unique_together = (("tenant", "asset", "period_date"),)
        ordering = ["-period_date", "-id"]

    def __str__(self):
        return f"{self.asset.asset_code} {self.period_label}"


class AssetAgentEnrollmentToken(TenantScopedModel):
    """One-time secret that authorises a machine to join the workspace."""

    label = models.CharField(max_length=120)
    token_prefix = models.CharField(max_length=24)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(default=1)
    use_count = models.PositiveIntegerField(default=0)
    is_revoked = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "asset_agent_enrollment_tokens"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.label} ({self.token_prefix}…)"

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_exhausted(self) -> bool:
        return self.use_count >= self.max_uses

    @property
    def is_usable(self) -> bool:
        return not self.is_revoked and not self.is_expired and not self.is_exhausted

    @property
    def status(self) -> str:
        if self.is_revoked:
            return "revoked"
        if self.is_expired:
            return "expired"
        if self.is_exhausted:
            return "used"
        return "active"

    @property
    def uses_remaining(self) -> int:
        return max(self.max_uses - self.use_count, 0)


class AssetAgent(TenantScopedModel):
    """An enrolled Windows, Linux or macOS endpoint that reports inventory."""

    machine_id = models.CharField(max_length=128)
    hostname = models.CharField(max_length=200)
    display_name = models.CharField(max_length=200, blank=True)
    os_family = models.CharField(
        max_length=10,
        choices=[
            ("windows", "Windows"),
            ("linux", "Linux"),
            ("macos", "macOS"),
            ("other", "Other"),
        ],
        default="other",
    )
    os_name = models.CharField(max_length=200, blank=True)
    os_version = models.CharField(max_length=120, blank=True)
    os_build = models.CharField(max_length=120, blank=True)
    architecture = models.CharField(max_length=40, blank=True)
    cpu_architecture = models.CharField(max_length=40, blank=True)
    agent_version = models.CharField(max_length=40, blank=True)
    secret_hash = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64, blank=True)
    provisioning_method = models.CharField(
        max_length=20,
        choices=[
            ("manual", "Manual install"),
            ("gpo", "Active Directory / GPO"),
            ("portal", "Portal self-onboarding"),
            ("linux", "Linux bootstrap"),
            ("macos", "macOS install"),
            ("other", "Other"),
        ],
        default="manual",
    )
    token_version = models.PositiveIntegerField(default=1)
    last_token_at = models.DateTimeField(null=True, blank=True)
    asset = models.OneToOneField(
        Asset,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="agent",
    )
    manufacturer = models.CharField(max_length=120, blank=True)
    model_name = models.CharField(max_length=120, blank=True)
    serial_number = models.CharField(max_length=120, blank=True)
    cpu_model = models.CharField(max_length=200, blank=True)
    cpu_cores = models.PositiveIntegerField(null=True, blank=True)
    memory_total_mb = models.PositiveBigIntegerField(null=True, blank=True)
    disk_total_gb = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    primary_ip = models.CharField(max_length=45, blank=True)
    logged_in_user = models.CharField(max_length=150, blank=True)
    mac_addresses = models.JSONField(default=list, blank=True)
    disk_serials = models.JSONField(default=list, blank=True)
    listening_ports = models.JSONField(default=list, blank=True)
    security = models.JSONField(default=dict, blank=True)
    hardware = models.JSONField(default=dict, blank=True)
    enrolled_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_inventory_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.CharField(max_length=45, blank=True)
    is_active = models.BooleanField(default=True)
    enrolled_via = models.ForeignKey(
        AssetAgentEnrollmentToken,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="agents",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "asset_agents"
        ordering = ["hostname", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "machine_id"], name="uniq_asset_agent_machine"
            )
        ]
        indexes = [
            models.Index(fields=["last_seen_at"]),
            models.Index(fields=["is_active", "os_family"]),
        ]

    def __str__(self):
        return self.display_name or self.hostname or self.machine_id

    @property
    def connectivity(self) -> str:
        if not self.is_active:
            return "disabled"
        if not self.last_seen_at:
            return "enrolled"
        age = timezone.now() - self.last_seen_at
        if age <= AGENT_ONLINE_WINDOW:
            return "online"
        if age <= AGENT_STALE_WINDOW:
            return "stale"
        return "offline"

    @property
    def memory_total_gb(self) -> float | None:
        if not self.memory_total_mb:
            return None
        return round(self.memory_total_mb / 1024, 1)

    @property
    def disk_free_gb(self) -> float | None:
        disks = (self.hardware or {}).get("disks") or []
        values = []
        for disk in disks:
            try:
                values.append(float(disk.get("free_gb") or 0))
            except (TypeError, ValueError):
                continue
        return round(sum(values), 1) if values else None


class AssetInventorySnapshot(TenantScopedModel):
    """Immutable, normalised inventory payload received from an agent."""

    agent = models.ForeignKey(
        AssetAgent, on_delete=models.CASCADE, related_name="snapshots"
    )
    collected_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(default=timezone.now)
    payload = models.JSONField(default=dict)
    software_count = models.PositiveIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "asset_agent_snapshots"
        ordering = ["-received_at", "-id"]
        indexes = [models.Index(fields=["agent", "received_at"])]

    def __str__(self):
        return f"{self.agent} @ {self.received_at:%Y-%m-%d %H:%M}"


class AssetSoftware(TenantScopedModel):
    """Latest software inventory per endpoint; replaced on every report."""

    agent = models.ForeignKey(
        AssetAgent, on_delete=models.CASCADE, related_name="software"
    )
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=120, blank=True)
    publisher = models.CharField(max_length=200, blank=True)
    source = models.CharField(max_length=40, blank=True)
    installed_on = models.DateField(null=True, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "asset_agent_software"
        ordering = ["name", "version"]
        unique_together = (("tenant", "agent", "name", "version"),)
        indexes = [models.Index(fields=["tenant", "name"])]

    def __str__(self):
        return f"{self.name} {self.version}".strip()


class AssetAgentEvent(TenantScopedModel):
    """Audit trail for enrolment, inventory and custody changes."""

    agent = models.ForeignKey(
        AssetAgent, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    enrollment_token = models.ForeignKey(
        AssetAgentEnrollmentToken,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    kind = models.CharField(
        max_length=20,
        choices=[
            ("enrolled", "Agent enrolled"),
            ("inventory", "Inventory received"),
            ("heartbeat", "Heartbeat"),
            ("linked", "Linked to asset"),
            ("unlinked", "Unlinked from asset"),
            ("activated", "Agent activated"),
            ("deactivated", "Agent deactivated"),
            ("token_issued", "Enrolment token issued"),
            ("token_revoked", "Enrolment token revoked"),
        ],
        db_index=True,
    )
    detail = models.TextField(blank=True)
    actor_user_id = models.IntegerField(null=True, blank=True)
    ip_address = models.CharField(max_length=45, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "asset_agent_events"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "created_at"])]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.created_at:%Y-%m-%d %H:%M}"


__all__ = [
    "AGENT_STALE_WINDOW",
    "AGENT_ONLINE_WINDOW",
    "ASSET_CONDITIONS",
    "ASSET_STATUSES",
    "DEPRECIATION_METHODS",
    "Asset",
    "AssetAgent",
    "AssetAgentEnrollmentToken",
    "AssetAgentEvent",
    "AssetAssignment",
    "AssetCategory",
    "AssetDepreciation",
    "AssetInventorySnapshot",
    "AssetMaintenance",
    "AssetSoftware",
]
