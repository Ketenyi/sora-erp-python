"""Forms for purchase orders, receipts, supplier bills, payments and RFQs."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.core.models import (
    BankAccount,
    Product,
    PurchaseOrder,
    RequestForQuotation,
    SupplierBill,
    SupplierPayment,
    Vendor,
)


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


def _vendor_field():
    return forms.ModelChoiceField(
        queryset=Vendor.objects.filter(status="active").order_by("company_name"),
        label="Supplier",
        empty_label="Select supplier",
    )


def _product_field(label="Product"):
    return forms.ModelChoiceField(
        queryset=Product.objects.filter(status="active").order_by("name"),
        required=False,
        label=label,
        empty_label="Select product",
    )


class PurchaseOrderForm(StyledFormMixin, forms.ModelForm):
    vendor = _vendor_field()
    order_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )
    expected_delivery = forms.DateField(
        initial=date.today() + timedelta(days=14),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Expected delivery",
    )

    class Meta:
        model = PurchaseOrder
        fields = ["order_date", "expected_delivery", "terms", "notes"]
        widgets = {
            "terms": forms.Textarea(attrs={"rows": 2, "placeholder": "Payment and delivery terms"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["vendor"].initial = self.instance.vendor_id


class PurchaseOrderItemForm(StyledFormMixin, forms.Form):
    product = _product_field()
    product_name = forms.CharField(
        required=False,
        max_length=200,
        label="Item",
        widget=forms.TextInput(attrs={"placeholder": "Product or service"}),
    )
    description = forms.CharField(
        required=False, widget=forms.TextInput(attrs={"placeholder": "Description"})
    )
    quantity = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=15,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Qty"}),
    )
    unit_price = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=15,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Unit price"}),
    )
    discount = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=15,
        decimal_places=2,
        required=False,
        initial=Decimal("0"),
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    tax_rate = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=5,
        decimal_places=2,
        required=False,
        initial=Decimal("16"),
        label="Tax %",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        name = (cleaned.get("product_name") or "").strip()
        quantity = cleaned.get("quantity")
        if not product and not name:
            return cleaned
        if not name and product:
            cleaned["product_name"] = product.name
        if not quantity:
            raise forms.ValidationError("Enter a quantity for this line.")
        if cleaned.get("unit_price") is None:
            raise forms.ValidationError("Enter a unit price for this line.")
        return cleaned


class SupplierBillForm(StyledFormMixin, forms.ModelForm):
    vendor = _vendor_field()
    bill_number = forms.CharField(
        required=False,
        label="Bill number",
        widget=forms.TextInput(attrs={"placeholder": "Leave blank to auto-generate"}),
    )
    bill_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )
    due_date = forms.DateField(
        initial=date.today() + timedelta(days=30),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    class Meta:
        model = SupplierBill
        fields = ["bill_number", "bill_date", "due_date", "reference", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["vendor"].initial = self.instance.vendor_id

    def clean_bill_number(self):
        value = (self.cleaned_data.get("bill_number") or "").strip()
        if value and SupplierBill.objects.filter(bill_number=value).exists():
            raise forms.ValidationError("A supplier bill with this number already exists.")
        return value


class SupplierBillItemForm(StyledFormMixin, forms.Form):
    product = _product_field()
    description = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Description"}),
    )
    quantity = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=15,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Qty"}),
    )
    unit_price = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=15,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Unit price"}),
    )
    discount = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=15,
        decimal_places=2,
        required=False,
        initial=Decimal("0"),
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    tax_rate = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=5,
        decimal_places=2,
        required=False,
        initial=Decimal("16"),
        label="Tax %",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        description = (cleaned.get("description") or "").strip()
        quantity = cleaned.get("quantity")
        if not product and not description:
            return cleaned
        if not description and product:
            cleaned["description"] = product.name
        if not quantity:
            raise forms.ValidationError("Enter a quantity for this line.")
        if cleaned.get("unit_price") is None:
            raise forms.ValidationError("Enter a unit price for this line.")
        return cleaned


class GoodsReceiptForm(StyledFormMixin, forms.Form):
    receipt_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )
    invoice_number = forms.CharField(required=False, max_length=50)
    delivery_note = forms.CharField(required=False, max_length=100)
    notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class SupplierPaymentForm(StyledFormMixin, forms.ModelForm):
    vendor = _vendor_field()
    supplier_bill = forms.ModelChoiceField(
        queryset=SupplierBill.objects.filter(
            status__in=["approved", "partially_paid"]
        ).order_by("due_date"),
        required=False,
        label="Bill",
        empty_label="No specific bill",
    )
    payment_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.filter(status="active").order_by("bank_name"),
        required=False,
        label="Bank account",
        empty_label="None",
    )
    reference = forms.CharField(required=False, max_length=100)

    class Meta:
        model = SupplierPayment
        fields = [
            "payment_date",
            "amount",
            "payment_method",
            "reference",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
            "amount": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        self.bill_id = kwargs.pop("bill_id", None)
        super().__init__(*args, **kwargs)
        self.fields["amount"].min_value = Decimal("0.01")
        self.fields["amount"].widget.attrs["min"] = "0.01"
        if self.bill_id:
            self.fields["supplier_bill"].initial = self.bill_id
        elif self.instance and self.instance.pk:
            self.fields["vendor"].initial = self.instance.vendor_id
            self.fields["supplier_bill"].initial = self.instance.supplier_bill_id
            self.fields["bank_account"].initial = self.instance.bank_account_id

    def clean(self):
        cleaned = super().clean()
        vendor = cleaned.get("vendor")
        bill = cleaned.get("supplier_bill")
        amount = cleaned.get("amount")
        if vendor and bill:
            if bill.vendor_id != vendor.id:
                raise forms.ValidationError(
                    "The selected bill belongs to a different supplier."
                )
            balance = (bill.total_amount or Decimal("0")) - (
                bill.paid_amount or Decimal("0")
            )
            if amount and amount > balance:
                raise forms.ValidationError(
                    f"Amount cannot exceed the bill balance of {balance:,.2f}."
                )
        return cleaned


class RequestForQuotationForm(StyledFormMixin, forms.ModelForm):
    required_date = forms.DateField(
        initial=date.today() + timedelta(days=14),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Required by",
    )

    class Meta:
        model = RequestForQuotation
        fields = ["title", "required_date", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class RfqItemForm(StyledFormMixin, forms.Form):
    product = _product_field()
    description = forms.CharField(
        required=False,
        label="Item",
        widget=forms.TextInput(attrs={"placeholder": "Product or service"}),
    )
    quantity = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Qty"}),
    )
    unit_of_measure = forms.CharField(
        required=False,
        label="Unit",
        widget=forms.TextInput(attrs={"placeholder": "pcs"}),
    )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        description = (cleaned.get("description") or "").strip()
        quantity = cleaned.get("quantity")
        if not product and not description:
            return cleaned
        if not description and product:
            cleaned["description"] = product.name
        if not quantity:
            raise forms.ValidationError("Enter a quantity for this line.")
        return cleaned


PurchaseOrderItemFormSet = formset_factory(
    PurchaseOrderItemForm, extra=4, can_delete=False
)
SupplierBillItemFormSet = formset_factory(
    SupplierBillItemForm, extra=4, can_delete=False
)
RfqItemFormSet = formset_factory(RfqItemForm, extra=4, can_delete=False)
