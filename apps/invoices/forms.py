"""Forms for invoices and payments."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.core.models import BankAccount, Client, Invoice, Payment, Product


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


class InvoiceForm(StyledFormMixin, forms.ModelForm):
    client = forms.ModelChoiceField(
        queryset=Client.objects.filter(deleted_at__isnull=True).order_by("company_name"),
        label="Client",
    )
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.filter(status="active").order_by("bank_name"),
        required=False,
        label="Bank account",
    )
    invoice_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )
    due_date = forms.DateField(
        initial=date.today() + timedelta(days=30),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    payment_terms = forms.CharField(
        required=False, initial="Net 30", label="Payment terms"
    )
    lpo_number = forms.CharField(required=False, label="LPO number")
    discount_amount = forms.DecimalField(
        min_value=Decimal("0"),
        initial=Decimal("0"),
        max_digits=12,
        decimal_places=2,
        label="Discount amount",
    )
    tax_rate = forms.DecimalField(
        min_value=Decimal("0"),
        initial=Decimal("16"),
        max_digits=5,
        decimal_places=2,
        label="Tax rate (%)",
    )

    class Meta:
        model = Invoice
        fields = [
            "client",
            "invoice_date",
            "due_date",
            "status",
            "payment_terms",
            "lpo_number",
            "bank_account",
            "discount_amount",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["status"].initial = "sent"
        else:
            taxable = (self.instance.subtotal or Decimal("0")) - (
                self.instance.discount_amount or Decimal("0")
            )
            if taxable and self.instance.tax_amount:
                self.fields["tax_rate"].initial = (
                    self.instance.tax_amount / taxable * 100
                ).quantize(Decimal("0.01"))

    def clean(self):
        cleaned = super().clean()
        invoice_date = cleaned.get("invoice_date")
        due_date = cleaned.get("due_date")
        if invoice_date and due_date and due_date < invoice_date:
            self.add_error("due_date", "Due date must be on or after the invoice date.")
        return cleaned


class InvoiceItemForm(StyledFormMixin, forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(status="active").order_by("name"),
        required=False,
        label="Product",
        widget=forms.Select(attrs={"class": "form-control item-product"}),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "class": "form-control item-description",
                "placeholder": "Description",
            }
        ),
    )
    quantity = forms.DecimalField(
        min_value=Decimal("0"),
        initial=Decimal("1"),
        max_digits=10,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control item-qty", "step": "0.01"}),
    )
    unit_price = forms.DecimalField(
        min_value=Decimal("0"),
        initial=Decimal("0"),
        max_digits=12,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(
            attrs={"class": "form-control item-price", "step": "0.01"}
        ),
    )
    discount = forms.DecimalField(
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        initial=Decimal("0"),
        max_digits=5,
        decimal_places=2,
        required=False,
        label="Discount (%)",
        widget=forms.NumberInput(
            attrs={"class": "form-control item-discount", "step": "0.01"}
        ),
    )

    def clean(self):
        cleaned = super().clean()
        description = (cleaned.get("description") or "").strip()
        product = cleaned.get("product")
        if not description and not product:
            return cleaned
        if not description and product:
            cleaned["description"] = product.name
        if cleaned.get("quantity") is None:
            self.add_error("quantity", "Enter a quantity.")
        if cleaned.get("unit_price") is None:
            self.add_error("unit_price", "Enter a unit price.")
        return cleaned


InvoiceItemFormSet = formset_factory(InvoiceItemForm, extra=4, can_delete=False)


class PaymentForm(StyledFormMixin, forms.Form):
    amount = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        label="Amount",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    payment_method = forms.ChoiceField(
        choices=Payment._meta.get_field("payment_method").choices,
        label="Payment method",
    )
    payment_date = forms.DateField(
        initial=date.today,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Payment date",
    )
    reference_number = forms.CharField(
        required=False, label="Reference", widget=forms.TextInput(attrs={"placeholder": "Transaction ID, cheque or M-Pesa code"})
    )
    notes = forms.CharField(
        required=False,
        label="Notes",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        self.balance = kwargs.pop("balance", None)
        super().__init__(*args, **kwargs)

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if self.balance is not None and amount > self.balance:
            raise forms.ValidationError(
                f"Amount cannot exceed the balance due of {self.balance:,.2f}."
            )
        return amount
