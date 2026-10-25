from django.urls import path

from apps.project_management import views

app_name = "project_management"

urlpatterns = [
    path("", views.dashboard),
    path("dashboard", views.dashboard),
    path("portfolio", views.portfolio),
    path("create", views.project_create),
    path("edit/<int:pk>", views.project_edit),
    path("<int:pk>", views.project_detail),
    path("<int:pk>/lock-baseline", views.project_lock_baseline),
    # Tender and Gantt
    path("tender", views.tender),
    path("gantt/<int:pk>", views.gantt),
    path("gantt/<int:pk>/download", views.gantt_download),
    path("gantt/<int:pk>/reschedule/<int:task_id>", views.task_reschedule),
    # Tasks
    path("tasks/my", views.my_tasks),
    path("my-tasks", views.my_tasks),
    path("<int:pk>/tasks", views.task_list),
    path("<int:pk>/tasks/create", views.task_create),
    path("<int:pk>/tasks/edit/<int:task_id>", views.task_edit),
    path("<int:pk>/board", views.board),
    path("<int:pk>/board/move", views.board_move),
    # Team and timesheets
    path("<int:pk>/team", views.team),
    path("<int:pk>/team/remove/<int:member_id>", views.team_member_remove),
    path("<int:pk>/timesheets", views.timesheets),
    path("<int:pk>/timesheets/<int:entry_id>/decision", views.time_entry_decision),
    # Field expenses
    path("expenses", views.expenses_all),
    path("<int:pk>/expenses", views.expenses),
    path("<int:pk>/expenses/<int:expense_id>/decision", views.expense_decision),
    # Minutes of meeting
    path("<int:pk>/moms", views.moms),
    path("<int:pk>/moms/create", views.mom_create),
    path("<int:pk>/moms/edit/<int:mom_id>", views.mom_edit),
    path("<int:pk>/moms/view/<int:mom_id>", views.mom_detail),
    path("<int:pk>/moms/view/<int:mom_id>/pdf", views.mom_pdf_download),
    path("<int:pk>/moms/<int:mom_id>/send", views.mom_send),
    # Change requests
    path("<int:pk>/change-requests", views.change_requests),
    path("<int:pk>/change-requests/create", views.change_request_create),
    path("<int:pk>/change-requests/view/<int:cr_id>", views.change_request_detail),
    path("<int:pk>/change-requests/<int:cr_id>/send", views.change_request_send),
    # Approval magic links
    path("approvals", views.approvals_hub),
    path("<int:pk>/send-uat", views.project_send_uat),
    path("approval/<str:token>", views.approval, name="approval"),
    # Audit
    path("audit", views.audit_log),
    path("<int:pk>/audit", views.audit_log),
    # Handovers
    path("handovers", views.handover_index),
    path("handover", views.handover_index),
    path("handovers/<int:handover_id>", views.handover_detail),
    path("<int:pk>/handover", views.handover_project),
    # Scorecards
    path("scorecards", views.scorecards),
    # SLA hub
    path("sla", views.sla_hub),
    path("sla/contracts/create", views.sla_contract_create),
    path("sla/contracts/<int:contract_id>", views.sla_contract_detail),
    path("sla/contracts/<int:contract_id>/edit", views.sla_contract_edit),
    path("sla/contracts/<int:contract_id>/schedule-health", views.health_check_schedule),
    path("sla/convert/<int:pk>", views.sla_contract_convert),
    path("sla/tickets", views.sla_tickets),
    path("sla/tickets/create", views.sla_ticket_create),
    path("sla/tickets/<int:ticket_id>", views.sla_ticket_detail),
    path("sla/tickets/<int:ticket_id>/update", views.sla_ticket_update),
    path("sla/tickets/from-task/<int:task_id>", views.sla_ticket_from_task),
    path("sla/health-checks", views.health_checks),
    path("sla/health-checks/<int:check_id>/complete", views.health_check_complete),
    # Analytics and reports
    path("analytics", views.analytics),
    path("analytics/query", views.analytics_query),
    path("analytics/export", views.analytics_export),
    path("analytics/reports", views.reports),
    path("analytics/reports/save", views.report_save),
    path("analytics/reports/<int:report_id>", views.report_detail),
    path("analytics/reports/<int:report_id>/export", views.report_export),
    path("analytics/reports/<int:report_id>/pdf", views.report_pdf_download),
    path("analytics/reports/<int:report_id>/dispatch", views.report_dispatch),
    # Custom dashboards
    path("dashboards", views.dashboards),
    path("dashboards/create", views.dashboard_create),
    path("dashboards/<int:dashboard_id>", views.dashboard_detail),
    path("dashboards/<int:dashboard_id>/edit", views.dashboard_edit),
    path("dashboards/<int:dashboard_id>/delete", views.dashboard_delete),
    # Licence and flags
    path("license", views.license_features),
    path("license/<int:flag_id>/toggle", views.flag_toggle),
]
