"""POS session and void forms."""

from __future__ import annotations

from decimal import Decimal

from django import forms


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


class SessionOpenForm(StyledFormMixin, forms.Form):
    terminal_name = forms.CharField(
        max_length=100,
        initial="Main Terminal",
        label="Terminal name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Front counter"}),
    )
    opening_float = forms.DecimalField(
        max_digits=15,
        decimal_places=2,
        min_value=Decimal("0"),
        initial=Decimal("0.00"),
        label="Opening float",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Notes",
    )


class SessionCloseForm(StyledFormMixin, forms.Form):
    closing_float = forms.DecimalField(
        max_digits=15,
        decimal_places=2,
        min_value=Decimal("0"),
        label="Counted cash in drawer",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Closing notes",
    )


class VoidSaleForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        required=False,
        max_length=250,
        label="Void reason",
        widget=forms.TextInput(attrs={"placeholder": "e.g. duplicate charge"}),
    )


class RefundForm(StyledFormMixin, forms.Form):
    method = forms.ChoiceField(label="Refund method")
    reference = forms.CharField(
        required=False,
        max_length=120,
        label="Transaction reference",
        widget=forms.TextInput(attrs={"placeholder": "e.g. M-Pesa code"}),
    )
    reason = forms.CharField(
        max_length=250,
        label="Reason",
        widget=forms.TextInput(attrs={"placeholder": "e.g. damaged item"}),
    )

    def __init__(self, *args, methods=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["method"].choices = [
            (row["code"], row["label"]) for row in (methods or [])
        ]

