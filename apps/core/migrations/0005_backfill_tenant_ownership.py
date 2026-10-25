"""Backfill tenant ownership for existing data and enforce it at the database level."""

import django.db.models.deletion
from django.db import migrations, models


SCOPED_MODELS = [
    "aianomalydetection",
    "aitransactioncategory",
    "approvalrule",
    "approvaltracking",
    "approvalworkflow",
    "approver",
    "attachment",
    "attendance",
    "auditdetail",
    "auditlog",
    "bankaccount",
    "bankrule",
    "bankstatement",
    "bankstatementline",
    "budget",
    "budgetline",
    "candidate",
    "cashflowforecast",
    "cashflowscenario",
    "chartofaccount",
    "client",
    "department",
    "depreciationentry",
    "documentaicategory",
    "documentapproval",
    "documentauditlog",
    "documentcategory",
    "documentshare",
    "documenttag",
    "documenttemplate",
    "documentversion",
    "documentworkflow",
    "documentworkflowinstance",
    "employee",
    "employeedocument",
    "employeefeedback",
    "employeekpicontract",
    "employeemovement",
    "employeeperformance",
    "employeerecognition",
    "employeeskill",
    "employeetraining",
    "fiscalperiod",
    "fiscalyear",
    "fixedasset",
    "generateddocument",
    "goal",
    "goodsreceipt",
    "goodsreceiptitem",
    "interview",
    "invoice",
    "invoiceitem",
    "jobposting",
    "journalentry",
    "journalentryline",
    "journalentrytype",
    "kpiperformance",
    "leavebalance",
    "leaverequest",
    "leavetransaction",
    "leavetype",
    "notification",
    "onboarding",
    "overtimerequest",
    "payment",
    "payrollitem",
    "payrollperiod",
    "performancenotification",
    "performancereview",
    "performancetask",
    "pmdashboard",
    "pmreportdefinition",
    "position",
    "product",
    "productcategory",
    "project",
    "projectauditlog",
    "projectbudget",
    "projectchangerequest",
    "projectcomment",
    "projectexpense",
    "projecthandover",
    "projecthandoveritem",
    "projectinvoice",
    "projectmagiclink",
    "projectmom",
    "projecttask",
    "projectteam",
    "projecttimeentry",
    "purchaseorder",
    "purchaseorderitem",
    "quote",
    "quotefollowup",
    "quoteitem",
    "quotemessage",
    "recognitionbadge",
    "recurringjournal",
    "recurringjournalline",
    "reportschedule",
    "requestforquotation",
    "rfq",
    "rfqitem",
    "savedreport",
    "skill",
    "slacontract",
    "slahealthcheck",
    "slaticket",
    "stockadjustment",
    "stockadjustmentitem",
    "stocklayer",
    "stockmovement",
    "stocktransfer",
    "stocktransferitem",
    "successionplan",
    "supplierbill",
    "supplierbillitem",
    "supplierpayment",
    "taxjurisdiction",
    "taxrate",
    "taxrule",
    "trainingprogram",
    "useractivitylog",
    "vendor",
    "vendorquotation",
    "vendorquotationitem",
    "warehouse",
    "wellnesschallenge",
    "wellnesschallengeprogress",
    "wellnesscheckin",
]


def backfill_tenants(apps, schema_editor):
    Tenant = apps.get_model("saas", "Tenant")
    tenant = Tenant.objects.filter(slug="sora-demo").first()
    if tenant is None:
        tenant = Tenant.objects.order_by("id").first()
    if tenant is None:
        for label in SCOPED_MODELS:
            model = apps.get_model("core", label)
            if model.objects.using(schema_editor.connection.alias).exists():
                raise RuntimeError(
                    "Tenant-owned rows exist but no workspace is available to own them."
                )
        return
    using = schema_editor.connection.alias
    for label in SCOPED_MODELS:
        model = apps.get_model("core", label)
        model.objects.using(using).filter(tenant_id__isnull=True).update(tenant_id=tenant.pk)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_tenant_scope_fields"),
    ]

    operations = [
        migrations.RunPython(backfill_tenants, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="aianomalydetection",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="aitransactioncategory",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="approvalrule",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="approvaltracking",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="approvalworkflow",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="approver",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="attachment",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="attendance",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="auditdetail",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="auditlog",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="bankaccount",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="bankrule",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="bankstatement",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="bankstatementline",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="budget",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="budgetline",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="candidate",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="cashflowforecast",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="cashflowscenario",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="chartofaccount",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="client",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="department",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="depreciationentry",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documentaicategory",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documentapproval",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documentauditlog",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documentcategory",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documentshare",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documenttag",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documenttemplate",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documentversion",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documentworkflow",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="documentworkflowinstance",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="employee",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="employeedocument",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="employeefeedback",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="employeekpicontract",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="employeemovement",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="employeeperformance",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="employeerecognition",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="employeeskill",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="employeetraining",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="fiscalperiod",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="fiscalyear",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="fixedasset",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="generateddocument",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="goal",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="goodsreceipt",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="goodsreceiptitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="interview",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="invoice",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="invoiceitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="jobposting",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="journalentry",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="journalentryline",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="journalentrytype",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="kpiperformance",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="leavebalance",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="leaverequest",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="leavetransaction",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="leavetype",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="notification",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="onboarding",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="overtimerequest",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="payment",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="payrollitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="payrollperiod",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="performancenotification",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="performancereview",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="performancetask",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="pmdashboard",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="pmreportdefinition",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="position",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="product",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="productcategory",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="project",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projectauditlog",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projectbudget",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projectchangerequest",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projectcomment",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projectexpense",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projecthandover",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projecthandoveritem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projectinvoice",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projectmagiclink",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projectmom",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projecttask",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projectteam",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="projecttimeentry",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="purchaseorder",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="purchaseorderitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="quote",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="quotefollowup",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="quoteitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="quotemessage",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="recognitionbadge",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="recurringjournal",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="recurringjournalline",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="reportschedule",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="requestforquotation",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="rfq",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="rfqitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="savedreport",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="skill",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="slacontract",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="slahealthcheck",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="slaticket",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="stockadjustment",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="stockadjustmentitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="stocklayer",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="stockmovement",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="stocktransfer",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="stocktransferitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="successionplan",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="supplierbill",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="supplierbillitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="supplierpayment",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="taxjurisdiction",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="taxrate",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="taxrule",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="trainingprogram",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="useractivitylog",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="vendor",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="vendorquotation",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="vendorquotationitem",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="warehouse",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="wellnesschallenge",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="wellnesschallengeprogress",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
        migrations.AlterField(
            model_name="wellnesscheckin",
            name="tenant",
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='saas.tenant'),
        ),
    ]
