from django.urls import path

from apps.helpdesk import views

app_name = "helpdesk"

urlpatterns = [
    path("", views.dashboard),
    path("dashboard", views.dashboard),
    path("tickets", views.tickets),
    path("tickets/new", views.ticket_create),
    path("tickets/<int:ticket_id>", views.ticket_detail),
    path("tickets/<int:ticket_id>/reply", views.ticket_reply),
    path("tickets/<int:ticket_id>/update", views.ticket_update),
    path("tickets/<int:ticket_id>/notify", views.ticket_notify_client),
    path("settings", views.settings_page),
    path("settings/mailbox", views.settings_mailbox),
    path("settings/sla", views.settings_sla),
    path("settings/survey", views.settings_survey),
    path("settings/agents", views.settings_agents),
    path("settings/poll", views.settings_poll),
    path("settings/routing", views.routing_rules),
    path("settings/routing/new", views.routing_rule_create),
    path("settings/routing/<int:rule_id>/edit", views.routing_rule_edit),
    path("settings/routing/<int:rule_id>/delete", views.routing_rule_delete),
    path("status/<int:ticket_id>/<str:token>/", views.client_status),
    path("contracts", views.contracts),
    path("contracts/new", views.contract_create),
    path("contracts/<int:contract_id>", views.contract_detail),
    path("contracts/<int:contract_id>/edit", views.contract_edit),
    path("contracts/<int:contract_id>/schedule-health", views.schedule_health_check),
    path("health-checks", views.health_checks),
    path("health-checks/<int:check_id>/complete", views.health_check_complete),
]
