"""CRM forms for clients and vendors."""

from django import forms

from apps.core.models import Client, Vendor


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


class ClientForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            "company_name",
            "contact_person",
            "email",
            "phone",
            "address",
            "city",
            "country",
            "tax_id",
            "status",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
        }


class VendorForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Vendor
        fields = [
            "company_name",
            "contact_person",
            "email",
            "phone",
            "address",
            "city",
            "country",
            "tax_id",
            "payment_terms",
            "status",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_payment_terms(self):
        terms = self.cleaned_data.get("payment_terms")
        if terms is not None and terms < 0:
            raise forms.ValidationError("Payment terms cannot be negative.")
        return terms
