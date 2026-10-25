"""Forms for the asset register, custody, maintenance and depreciation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django import forms

from apps.assets.models import (
    Asset,
    AssetCategory,
    AssetMaintenance,
)
from apps.core.models import Employee


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


def _employee_field(label="Employee", required=False):
    return forms.ModelChoiceField(
        queryset=Employee.objects.filter(status="active").order_by("first_name", "last_name"),
        required=required,
        label=label,
        empty_label="No employee" if required else "Unassigned",
    )


class AssetCategoryForm(StyledFormMixin, forms.ModelForm):
    category_code = forms.CharField(
        required=False,
        max_length=50,
        label="Category code",
        widget=forms.TextInput(attrs={"placeholder": "Leave blank to auto-generate"}),
    )

    class Meta:
        model = AssetCategory
        fields = [
            "category_code",
            "category_name",
            "description",
            "useful_life_years",
            "depreciation_rate",
            "status",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "depreciation_rate": forms.NumberInput(attrs={"min": "0", "max": "100", "step": "0.01"}),
            "useful_life_years": forms.NumberInput(attrs={"min": "1"}),
        }

    def clean_category_code(self):
        code = (self.cleaned_data.get("category_code") or "").strip()
        if code:
            matches = AssetCategory.objects.filter(category_code__iexact=code)
            if self.instance.pk:
                matches = matches.exclude(pk=self.instance.pk)
            if matches.exists():
                raise forms.ValidationError("Another category already uses this code.")
        return code


class AssetForm(StyledFormMixin, forms.ModelForm):
    asset_code = forms.CharField(
        required=False,
        max_length=50,
        label="Asset code",
        widget=forms.TextInput(attrs={"placeholder": "Leave blank to auto-generate"}),
    )
    purchase_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Purchase date",
    )

    class Meta:
        model = Asset
        fields = [
            "asset_code",
            "name",
            "category",
            "serial_number",
            "model_name",
            "manufacturer",
            "location",
            "purchase_date",
            "purchase_cost",
            "salvage_value",
            "useful_life_years",
            "depreciation_method",
            "depreciation_rate",
            "status",
            "condition",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Toyota Hilux pickup"}),
            "serial_number": forms.TextInput(attrs={"placeholder": "Manufacturer serial / VIN"}),
            "purchase_cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "salvage_value": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "useful_life_years": forms.NumberInput(attrs={"min": "1"}),
            "depreciation_rate": forms.NumberInput(attrs={"min": "0", "max": "100", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        cost = cleaned.get("purchase_cost") or Decimal("0.00")
        salvage = cleaned.get("salvage_value") or Decimal("0.00")
        if salvage > cost:
            self.add_error("salvage_value", "Salvage value cannot exceed the purchase cost.")
        if cleaned.get("depreciation_method") == "none":
            cleaned["depreciation_rate"] = Decimal("0.00")
        return cleaned

    def clean_asset_code(self):
        code = (self.cleaned_data.get("asset_code") or "").strip()
        if code:
            matches = Asset.objects.filter(asset_code__iexact=code)
            if self.instance.pk:
                matches = matches.exclude(pk=self.instance.pk)
            if matches.exists():
                raise forms.ValidationError("Another asset already uses this code.")
        return code


class CheckOutForm(StyledFormMixin, forms.Form):
    employee = _employee_field("Assign to employee")
    custodian_name = forms.CharField(
        required=False,
        max_length=150,
        label="Or external custodian",
        widget=forms.TextInput(attrs={"placeholder": "Name of person holding the asset"}),
    )
    expected_return_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Expected return date",
    )
    condition_out = forms.ChoiceField(
        choices=Asset._meta.get_field("condition").choices,
        initial="good",
        label="Condition at check-out",
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Notes")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("employee") and not (cleaned.get("custodian_name") or "").strip():
            self.add_error("custodian_name", "Choose an employee or enter an external custodian.")
        return cleaned


class CheckInForm(StyledFormMixin, forms.Form):
    condition_in = forms.ChoiceField(
        choices=Asset._meta.get_field("condition").choices,
        initial="good",
        label="Condition at check-in",
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Return notes")


class MaintenanceForm(StyledFormMixin, forms.ModelForm):
    asset = forms.ModelChoiceField(
        queryset=Asset.objects.exclude(status__in=["disposed", "retired"]).order_by("asset_code"),
        label="Asset",
        empty_label="Select asset",
    )
    scheduled_date = forms.DateField(
        initial=date.today,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Scheduled date",
    )

    class Meta:
        model = AssetMaintenance
        fields = [
            "asset",
            "maintenance_type",
            "title",
            "description",
            "scheduled_date",
            "vendor",
            "cost",
            "status",
            "performed_by",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. 20,000 km service"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
        }
        labels = {"performed_by": "Assigned technician"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["performed_by"].queryset = Employee.objects.filter(status="active").order_by(
            "first_name", "last_name"
        )
        self.fields["performed_by"].empty_label = "Unassigned"


class MaintenanceCompleteForm(StyledFormMixin, forms.Form):
    completed_date = forms.DateField(
        initial=date.today,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Completed date",
    )
    vendor = forms.CharField(required=False, max_length=150, label="Vendor / workshop")
    cost = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        max_digits=15,
        decimal_places=2,
        initial=Decimal("0.00"),
        label="Actual cost",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Completion notes",
    )


class DepreciationRunForm(StyledFormMixin, forms.Form):
    period = forms.DateField(
        initial=date.today,
        input_formats=["%Y-%m", "%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"),
        label="Period",
        help_text="Depreciation is posted for the month containing this date.",
    )


class EnrollmentTokenForm(StyledFormMixin, forms.Form):
    label = forms.CharField(
        max_length=120,
        label="Token label",
        widget=forms.TextInput(
            attrs={"placeholder": "e.g. Finance laptops — October rollout"}
        ),
        help_text="Shown in the token list so you can tell rollouts apart.",
    )
    expires_days = forms.IntegerField(
        min_value=1,
        max_value=90,
        initial=7,
        label="Valid for (days)",
        widget=forms.NumberInput(attrs={"min": "1", "max": "90"}),
    )
    max_uses = forms.IntegerField(
        min_value=1,
        max_value=100,
        initial=1,
        label="Enrolments allowed",
        widget=forms.NumberInput(attrs={"min": "1", "max": "100"}),
    )
    notes = forms.CharField(
        required=False,
        label="Notes",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional deployment notes"}),
    )


class AgentLinkForm(StyledFormMixin, forms.Form):
    asset = forms.ModelChoiceField(
        queryset=Asset.objects.none(),
        label="Link to asset",
        empty_label="Choose an asset",
    )

    def __init__(self, *args, agent=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Asset.objects.filter(agent__isnull=True).exclude(
            status__in=["retired", "disposed"]
        )
        if agent is not None and agent.asset_id:
            queryset = Asset.objects.filter(pk=agent.asset_id)
        self.fields["asset"].queryset = queryset.order_by("asset_code")
