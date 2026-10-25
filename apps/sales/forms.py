"""Forms for sales quotes."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.core.models import BankAccount, Client, Product, Quote, User


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


class QuoteForm(StyledFormMixin, forms.Form):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("sent", "Sent"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("expired", "Expired"),
    ]

    client = forms.ModelChoiceField(
        queryset=Client.objects.filter(deleted_at__isnull=True).order_by("company_name"),
        label="Client",
    )
    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.filter(status="active").order_by("first_name", "last_name"),
        required=False,
        label="Assigned to",
    )
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.filter(status="active").order_by("bank_name"),
        required=False,
        label="Bank account",
    )
    issue_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )
    valid_until = forms.DateField(
        initial=date.today() + timedelta(days=30),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    status = forms.ChoiceField(choices=STATUS_CHOICES, initial="draft")
    discount_amount = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("0"), max_digits=12, decimal_places=2,
        label="Discount amount",
    )
    tax_rate = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("16"), max_digits=5, decimal_places=2,
        label="Tax rate (%)",
    )
    notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Notes"
    )
    terms_conditions = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Terms & conditions",
    )

    def clean(self):
        cleaned = super().clean()
        issue = cleaned.get("issue_date")
        valid = cleaned.get("valid_until")
        if issue and valid and valid < issue:
            self.add_error("valid_until", "Valid until must be on or after the issue date.")
        return cleaned


class QuoteItemForm(StyledFormMixin, forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(status="active").order_by("name"),
        required=False,
        label="Product",
        widget=forms.Select(attrs={"class": "form-control item-product"}),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control item-description", "placeholder": "Description"}),
    )
    quantity = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("1"), max_digits=10, decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control item-qty", "step": "0.01"}),
    )
    unit_price = forms.DecimalField(
        min_value=Decimal("0"), initial=Decimal("0"), max_digits=12, decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control item-price", "step": "0.01"}),
    )
    discount = forms.DecimalField(
        min_value=Decimal("0"), max_value=Decimal("100"), initial=Decimal("0"),
        max_digits=5, decimal_places=2, required=False,
        label="Discount (%)",
        widget=forms.NumberInput(attrs={"class": "form-control item-discount", "step": "0.01"}),
    )

    def clean(self):
        cleaned = super().clean()
        description = (cleaned.get("description") or "").strip()
        product = cleaned.get("product")
        if not description and not product:
            return cleaned
        if not description and product:
            cleaned["description"] = product.name
        quantity = cleaned.get("quantity")
        unit_price = cleaned.get("unit_price")
        if quantity is None:
            self.add_error("quantity", "Enter a quantity.")
        if unit_price is None:
            self.add_error("unit_price", "Enter a unit price.")
        return cleaned

    def has_content(self):
        return bool(self.cleaned_data.get("description"))


QuoteItemFormSet = formset_factory(QuoteItemForm, extra=4, can_delete=False)


def quote_status_choices():
    return Quote._meta.get_field("status").choices
