from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("manifest.webmanifest", views.pwa_manifest, name="pwa_manifest"),
    path("service-worker.js", views.pwa_service_worker, name="pwa_service_worker"),
    path("offline/", views.offline_page, name="offline"),
    path("", views.dashboard, name="dashboard"),
    path("register/", views.register, name="register"),
    path("login/", auth_views.LoginView.as_view(template_name="finance/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("transactions/import/", views.transaction_import, name="transaction_import"),
    path("goals/", views.savings_goal_list, name="savings_goal_list"),
    path("goals/add/", views.savings_goal_create, name="savings_goal_create"),
    path("goals/<int:pk>/edit/", views.savings_goal_update, name="savings_goal_update"),
    path("goals/<int:pk>/contributions/add/", views.savings_contribution_create, name="savings_contribution_create"),
    path("goal-contributions/<int:pk>/delete/", views.savings_contribution_delete, name="savings_contribution_delete"),
    path("transactions/add/", views.transaction_create, name="transaction_create"),
    path("transactions/<int:pk>/edit/", views.transaction_update, name="transaction_update"),
    path("transactions/<int:pk>/delete/", views.transaction_delete, name="transaction_delete"),
    path("accounts/", views.account_list, name="account_list"),
    path("accounts/add/", views.account_create, name="account_create"),
    path("accounts/<int:pk>/edit/", views.account_update, name="account_update"),
    path("transfers/add/", views.transfer_create, name="transfer_create"),
    path("recurring/", views.recurring_list, name="recurring_list"),
    path("recurring/add/", views.recurring_create, name="recurring_create"),
    path("recurring/<int:pk>/edit/", views.recurring_update, name="recurring_update"),
    path("recurring/<int:pk>/generate/", views.recurring_generate, name="recurring_generate"),
    path("categories/add/", views.category_create, name="category_create"),
    path("budgets/", views.budget_settings, name="budget_settings"),
    path("budget-alerts/<int:pk>/read/", views.budget_alert_mark_read, name="budget_alert_mark_read"),
    path("reports/", views.reports, name="reports"),
    path("reports/export.csv", views.export_transactions_csv, name="export_transactions_csv"),
]