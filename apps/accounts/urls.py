from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("dashboard", views.dashboard),
    path("chart-of-accounts", views.chart_of_accounts),
    path("chart-of-accounts/create", views.account_create),
    path("chart-of-accounts/edit/<int:pk>", views.account_edit),
    path("chart-of-accounts/delete/<int:pk>", views.account_delete),
    path("journal-entries", views.journal_entries),
    path("journal-entries/create", views.journal_entry_create),
    path("journal-entries/view/<int:pk>", views.journal_entry_view),
    path("journal-entries/post/<int:pk>", views.journal_entry_post),
    path("ledger", views.ledger),
    path("ledger/account/<int:pk>", views.ledger_account),
    path("trial-balance", views.trial_balance),
    path("reports", views.reports),
    path("reports/general-ledger", views.general_ledger_report),
    path("reports/income-statement", views.income_statement),
    path("reports/balance-sheet", views.balance_sheet),
    path("reports/export", views.export_report),
    path("bank-accounts", views.bank_accounts),
    path("bank-accounts/create", views.bank_account_create),
    path("bank-accounts/edit/<int:pk>", views.bank_account_edit),
    path("bank-accounts/delete/<int:pk>", views.bank_account_delete),
]
