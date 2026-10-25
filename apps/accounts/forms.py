"""Forms for the accounts module."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.core.models import BankAccount, ChartOfAccount


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


class AccountForm(StyledFormMixin, forms.ModelForm):
    parent = forms.ModelChoiceField(
        queryset=ChartOfAccount.objects.exclude(status="deleted").order_by("account_code"),
        required=False,
        label="Parent account",
    )

    class Meta:
        model = ChartOfAccount
        fields = [
            "account_code",
            "account_name",
            "account_type",
            "normal_balance",
            "description",
            "status",
            "opening_balance",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["parent"].queryset = self.fields["parent"].queryset.exclude(
                pk=self.instance.pk
            )
            self.fields["parent"].initial = self.instance.parent_account_id
        self.fields["account_code"].help_text = "Unique code, e.g. 1000"

    def clean_account_code(self):
        code = (self.cleaned_data["account_code"] or "").strip()
        if not code:
            raise forms.ValidationError("Enter an account code.")
        return code


class BankAccountForm(StyledFormMixin, forms.ModelForm):
    is_default = forms.BooleanField(required=False, label="Set as default account")

    class Meta:
        model = BankAccount
        fields = [
            "bank_name",
            "branch",
            "account_name",
            "account_number",
            "swift_code",
            "currency",
            "opening_balance",
            "is_default",
            "status",
        ]
        widgets = {
            "opening_balance": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.is_default = 1 if self.cleaned_data.get("is_default") else 0
        if commit:
            instance.save()
        return instance


class JournalEntryForm(StyledFormMixin, forms.Form):
    entry_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"})
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), label="Description"
    )
    reference = forms.CharField(required=False, label="Reference")


class JournalLineForm(StyledFormMixin, forms.Form):
    account = forms.ModelChoiceField(
        queryset=ChartOfAccount.objects.filter(status="active").order_by("account_code"),
        required=False,
        label="Account",
        widget=forms.Select(attrs={"class": "form-control line-account"}),
    )
    entry_type = forms.ChoiceField(
        choices=[("debit", "Debit"), ("credit", "Credit")],
        required=False,
        label="Type",
        widget=forms.Select(attrs={"class": "form-control line-type"}),
    )
    amount = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=15,
        decimal_places=2,
        required=False,
        label="Amount",
        widget=forms.NumberInput(attrs={"class": "form-control line-amount", "step": "0.01"}),
    )
    description = forms.CharField(
        required=False,
        label="Line description",
        widget=forms.TextInput(attrs={"class": "form-control line-description"}),
    )

    def clean(self):
        cleaned = super().clean()
        account = cleaned.get("account")
        amount = cleaned.get("amount")
        entry_type = cleaned.get("entry_type")
        description = (cleaned.get("description") or "").strip()
        if not account and not amount and not entry_type and not description:
            return cleaned
        if not account:
            self.add_error("account", "Select an account.")
        if not entry_type:
            self.add_error("entry_type", "Choose debit or credit.")
        if amount is None:
            self.add_error("amount", "Enter an amount.")
        return cleaned


JournalLineFormSet = formset_factory(
    JournalLineForm, extra=4, min_num=2, validate_min=True, can_delete=False
)


class ReportPeriodForm(StyledFormMixin, forms.Form):
    from_date = forms.DateField(
        initial=date.today().replace(day=1),
        widget=forms.DateInput(attrs={"type": "date"}),
        label="From",
    )
    to_date = forms.DateField(
        initial=date.today(),
        widget=forms.DateInput(attrs={"type": "date"}),
        label="To",
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("from_date")
        end = cleaned.get("to_date")
        if start and end and end < start:
            self.add_error("to_date", "End date must be on or after the start date.")
        return cleaned
