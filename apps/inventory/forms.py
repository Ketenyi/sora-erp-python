"""Forms for products, categories, stock adjustments and transfers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.core.models import (
    Product,
    ProductCategory,
    StockAdjustment,
    StockTransfer,
    Warehouse,
)
from apps.core.services import next_code

ADJUSTMENT_REASONS = [
    ("damage", "Damaged Goods"),
    ("theft", "Theft / Loss"),
    ("expired", "Expired Products"),
    ("count_correction", "Stock Count Correction"),
    ("return", "Customer Return"),
    ("other", "Other"),
]


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


class ProductForm(StyledFormMixin, forms.ModelForm):
    product_code = forms.CharField(
        required=False,
        label="Product code",
        widget=forms.TextInput(attrs={"placeholder": "Leave blank to auto-generate"}),
    )
    name = forms.CharField(label="Product name", max_length=255)
    category = forms.ModelChoiceField(
        queryset=ProductCategory.objects.filter(status="active").order_by("category_name"),
        required=False,
        label="Category",
        empty_label="Select category",
    )
    unit = forms.CharField(required=False, initial="pcs", label="Unit")
    unit_price = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("0"), max_digits=12, decimal_places=2,
        label="Selling price",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    cost_price = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("0"), max_digits=12, decimal_places=2,
        required=False, label="Cost price",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    current_stock = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("0"), max_digits=15, decimal_places=2,
        required=False, label="Initial stock",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    minimum_stock = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("0"), max_digits=15, decimal_places=2,
        required=False, label="Minimum stock",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    maximum_stock = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("0"), max_digits=15, decimal_places=2,
        required=False, label="Maximum stock",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    reorder_level = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("0"), max_digits=15, decimal_places=2,
        required=False, label="Reorder level",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    reorder_quantity = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("0"), max_digits=15, decimal_places=2,
        required=False, label="Reorder quantity",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    tax_rate = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("16"), max_digits=5, decimal_places=2,
        required=False, label="Tax rate (%)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )

    class Meta:
        model = Product
        fields = [
            "product_code", "name", "description", "unit", "type",
            "unit_price", "cost_price", "current_stock", "minimum_stock",
            "maximum_stock", "reorder_level", "reorder_quantity", "tax_rate",
            "status",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def save(self, commit=True):
        existing_code = None
        existing_stock = None
        if self.instance.pk:
            existing = (
                Product.objects.filter(pk=self.instance.pk)
                .values("product_code", "current_stock")
                .first()
            )
            if existing:
                existing_code = existing["product_code"]
                existing_stock = existing["current_stock"]
        product = super().save(commit=False)
        product.name = self.cleaned_data["name"]
        product.product_name = product.name
        category = self.cleaned_data.get("category")
        product.category_id = category.id if category else None
        product.unit = self.cleaned_data.get("unit") or "pcs"
        product.cost_price = self.cleaned_data.get("cost_price") or Decimal("0")
        if product.pk and "current_stock" not in self.data:
            product.current_stock = existing_stock or Decimal("0")
        else:
            product.current_stock = self.cleaned_data.get("current_stock") or Decimal("0")
        product.reorder_level = self.cleaned_data.get("reorder_level") or Decimal("0")
        product.stock = int(product.current_stock)
        if not self.cleaned_data.get("product_code"):
            product.product_code = existing_code or next_code(
                "PRD", "products", "product_code"
            )
        if commit:
            product.save()
        return product


class CategoryForm(StyledFormMixin, forms.ModelForm):
    category_name = forms.CharField(max_length=100, label="Category name")
    parent = forms.ModelChoiceField(
        queryset=ProductCategory.objects.filter(status="active").order_by("category_name"),
        required=False,
        label="Parent category",
        empty_label="None",
    )

    class Meta:
        model = ProductCategory
        fields = ["category_name", "description", "status"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["parent"].initial = self.instance.parent_id

    def save(self, commit=True):
        category = super().save(commit=False)
        parent = self.cleaned_data.get("parent")
        category.parent_id = parent.id if parent else None
        if commit:
            category.save()
        return category


class StockAdjustmentForm(StyledFormMixin, forms.ModelForm):
    adjustment_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )
    reason = forms.ChoiceField(choices=ADJUSTMENT_REASONS, initial="count_correction")

    class Meta:
        model = StockAdjustment
        fields = ["adjustment_date", "type", "reason", "notes"]
        widgets = {
            "notes": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Reason for adjustment, additional details..."}
            )
        }


class StockAdjustmentItemForm(StyledFormMixin, forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(status="active").order_by("name"),
        required=False,
        label="Product",
        empty_label="Select product",
    )
    quantity = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=15,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Quantity"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Notes"}),
    )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        quantity = cleaned.get("quantity")
        if not product and not quantity:
            return cleaned
        if not product:
            raise forms.ValidationError("Select a product for this line.")
        if not quantity:
            raise forms.ValidationError("Enter a quantity for this line.")
        return cleaned


class StockTransferForm(StyledFormMixin, forms.ModelForm):
    from_location = forms.ChoiceField(
        choices=[], label="From location", initial=""
    )
    to_location = forms.ChoiceField(
        choices=[], label="To location", initial=""
    )
    transfer_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )

    class Meta:
        model = StockTransfer
        fields = ["from_location", "to_location", "transfer_date", "notes"]
        widgets = {
            "notes": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Reason for transfer, tracking info..."}
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        names = list(
            Warehouse.objects.order_by("warehouse_name").values_list(
                "warehouse_name", flat=True
            )
        )
        choices = [("", "Select location")] + [(name, name) for name in names]
        self.fields["from_location"].choices = choices
        self.fields["to_location"].choices = choices
        if self.instance and self.instance.pk:
            self.fields["from_location"].initial = self.instance.from_location
            self.fields["to_location"].initial = self.instance.to_location

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("from_location")
        destination = cleaned.get("to_location")
        if source and destination and source == destination:
            raise forms.ValidationError("Source and destination cannot be the same.")
        return cleaned


class StockTransferItemForm(StyledFormMixin, forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(status="active").order_by("name"),
        required=False,
        label="Product",
        empty_label="Select product",
    )
    quantity = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=15,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Quantity"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Notes"}),
    )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        quantity = cleaned.get("quantity")
        if not product and not quantity:
            return cleaned
        if not product:
            raise forms.ValidationError("Select a product for this line.")
        if not quantity:
            raise forms.ValidationError("Enter a quantity for this line.")
        return cleaned


StockAdjustmentItemFormSet = formset_factory(
    StockAdjustmentItemForm, extra=4, can_delete=False
)
StockTransferItemFormSet = formset_factory(
    StockTransferItemForm, extra=4, can_delete=False
)
