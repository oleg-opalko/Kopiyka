from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
	Account,
	BudgetAlert,
	Category,
	CategoryBudget,
	GoalContribution,
	MonthlyBudget,
	RecurringPayment,
	SavingsGoal,
	Transaction,
)
from .services import account_balance_map


class FinanceFlowTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="olena", password="Strong-password-526")
		self.category = Category.objects.create(
			owner=self.user,
			name="Продукти",
			kind=Category.Kind.EXPENSE,
		)

	def test_dashboard_requires_login(self):
		response = self.client.get(reverse("dashboard"))

		self.assertRedirects(response, f"{reverse('login')}?next={reverse('dashboard')}")

	def test_dashboard_shows_month_totals_and_chart_data(self):
		self.client.force_login(self.user)
		Transaction.objects.create(
			owner=self.user,
			category=self.category,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("245.50"),
			occurred_on=date(2026, 10, 1),
		)

		response = self.client.get(reverse("dashboard"), {"month": "2026-10"})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["expenses"], Decimal("245.50"))
		self.assertEqual(response.context["category_rows"][0]["category__name"], "Продукти")
		self.assertContains(response, "Щоденна динаміка")

	def test_transaction_form_saves_for_current_user(self):
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("transaction_create"),
			{
				"kind": Transaction.Kind.EXPENSE,
				"category": self.category.pk,
				"amount": "125.40",
				"occurred_on": "2026-10-01",
				"note": "Ринок",
			},
		)

		self.assertRedirects(response, reverse("dashboard"))
		transaction = Transaction.objects.get()
		self.assertEqual(transaction.owner, self.user)
		self.assertEqual(transaction.amount, Decimal("125.40"))

	def test_user_cannot_edit_another_users_transaction(self):
		other_user = User.objects.create_user(username="taras", password="Other-password-526")
		other_category = Category.objects.create(
			owner=other_user,
			name="Кава",
			kind=Category.Kind.EXPENSE,
		)
		transaction = Transaction.objects.create(
			owner=other_user,
			category=other_category,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("80.00"),
			occurred_on=date(2026, 10, 1),
		)
		self.client.force_login(self.user)

		response = self.client.get(reverse("transaction_update", args=[transaction.pk]))

		self.assertEqual(response.status_code, 404)

	def test_monthly_budget_is_saved_for_selected_month(self):
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("budget_settings"),
			{"month": "2026-10", "amount": "12000", "save_monthly": "1"},
		)

		self.assertRedirects(response, "/budgets/?month=2026-10")
		budget = MonthlyBudget.objects.get(owner=self.user)
		self.assertEqual(budget.month, date(2026, 10, 1))
		self.assertEqual(budget.amount, Decimal("12000"))

	def test_registration_creates_default_categories_and_signs_in(self):
		response = self.client.post(
			reverse("register"),
			{
				"username": "newperson",
				"email": "new@example.com",
				"password1": "Strong-password-526",
				"password2": "Strong-password-526",
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		new_user = User.objects.get(username="newperson")
		self.assertTrue(self.client.session.get("_auth_user_id"))
		self.assertGreater(new_user.finance_categories.count(), 0)

	def test_history_searches_notes_and_filters_by_date(self):
		self.client.force_login(self.user)
		for occurred_on, note in (
			(date(2026, 10, 3), "Тижневі продукти"),
			(date(2026, 10, 20), "Продукти для вечері"),
			(date(2026, 11, 3), "Продукти"),
		):
			Transaction.objects.create(
				owner=self.user,
				category=self.category,
				kind=Transaction.Kind.EXPENSE,
				amount=Decimal("25.00"),
				occurred_on=occurred_on,
				note=note,
			)

		response = self.client.get(
			reverse("transaction_list"),
			{"search": "вечері", "date_from": "2026-10-01", "date_to": "2026-10-31"},
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["page_obj"].paginator.count, 1)
		self.assertEqual(response.context["transactions"][0].note, "Продукти для вечері")

	def test_history_uses_pages_of_25_transactions(self):
		self.client.force_login(self.user)
		Transaction.objects.bulk_create(
			[
				Transaction(
					owner=self.user,
					category=self.category,
					kind=Transaction.Kind.EXPENSE,
					amount=Decimal("1.00"),
					occurred_on=date(2026, 10, 1),
				)
				for _ in range(30)
			]
		)

		response = self.client.get(reverse("transaction_list"), {"page": "2", "kind": "expense"})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["page_obj"].number, 2)
		self.assertEqual(len(response.context["transactions"]), 5)
		self.assertContains(response, "page=1")

	def test_invalid_history_filters_do_not_return_all_transactions(self):
		self.client.force_login(self.user)
		Transaction.objects.create(
			owner=self.user,
			category=self.category,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("10.00"),
			occurred_on=date(2026, 10, 1),
		)

		response = self.client.get(
			reverse("transaction_list"),
			{"date_from": "2026-10-31", "date_to": "2026-10-01"},
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["page_obj"].paginator.count, 0)

	def test_due_recurring_payments_create_missed_occurrences_without_duplicates(self):
		self.client.force_login(self.user)
		account = Account.objects.create(owner=self.user, name="Основна картка")
		payment = RecurringPayment.objects.create(
			owner=self.user,
			category=self.category,
			account=account,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("9500.00"),
			frequency=RecurringPayment.Frequency.MONTHLY,
			next_due_on=date(2026, 8, 31),
			monthly_day=31,
			note="Оренда",
		)

		response = self.client.post(reverse("recurring_generate", args=[payment.pk]))

		payment.refresh_from_db()
		self.assertRedirects(response, reverse("recurring_list"))
		self.assertEqual(
			list(Transaction.objects.order_by("scheduled_for").values_list("scheduled_for", flat=True)),
			[date(2026, 8, 31), date(2026, 9, 30)],
		)
		self.assertEqual(payment.next_due_on, date(2026, 10, 31))
		self.client.post(reverse("recurring_generate", args=[payment.pk]))
		self.assertEqual(Transaction.objects.count(), 2)
		self.assertEqual(Transaction.objects.first().account, account)

	def test_recurring_payment_cannot_be_generated_by_another_user(self):
		other_user = User.objects.create_user(username="taras", password="Other-password-526")
		other_category = Category.objects.create(
			owner=other_user,
			name="Оренда",
			kind=Category.Kind.EXPENSE,
		)
		payment = RecurringPayment.objects.create(
			owner=other_user,
			category=other_category,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("5000.00"),
			frequency=RecurringPayment.Frequency.MONTHLY,
			next_due_on=date(2026, 10, 1),
		)
		self.client.force_login(self.user)

		response = self.client.post(reverse("recurring_generate", args=[payment.pk]))

		self.assertEqual(response.status_code, 404)
		self.assertFalse(Transaction.objects.exists())

	def test_recurring_list_and_create_form_render(self):
		self.client.force_login(self.user)

		list_response = self.client.get(reverse("recurring_list"))
		form_response = self.client.get(reverse("recurring_create"))

		self.assertEqual(list_response.status_code, 200)
		self.assertContains(list_response, "Ще немає регулярних платежів")
		self.assertEqual(form_response.status_code, 200)
		self.assertContains(form_response, "Наступна дата")

	def test_transfer_changes_account_balances_but_not_income_or_expense_totals(self):
		self.client.force_login(self.user)
		cash = Account.objects.create(
			owner=self.user,
			name="Готівка",
			kind=Account.Kind.CASH,
			initial_balance=Decimal("500.00"),
		)
		bank = Account.objects.create(
			owner=self.user,
			name="Картка",
			kind=Account.Kind.CARD,
			initial_balance=Decimal("100.00"),
		)
		income_category = Category.objects.create(
			owner=self.user,
			name="Зарплата",
			kind=Category.Kind.INCOME,
		)
		Transaction.objects.create(
			owner=self.user,
			category=self.category,
			account=cash,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("25.00"),
			occurred_on=date(2026, 10, 1),
		)
		Transaction.objects.create(
			owner=self.user,
			category=income_category,
			account=bank,
			kind=Transaction.Kind.INCOME,
			amount=Decimal("400.00"),
			occurred_on=date(2026, 10, 1),
		)
		transfer_response = self.client.post(
			reverse("transfer_create"),
			{
				"account": cash.pk,
				"transfer_to": bank.pk,
				"amount": "200.00",
				"occurred_on": "2026-10-01",
				"note": "На картку",
			},
		)

		dashboard_response = self.client.get(reverse("dashboard"), {"month": "2026-10"})
		accounts_response = self.client.get(reverse("account_list"))
		account_balances = {account.name: account.current_balance for account in accounts_response.context["accounts"]}

		self.assertEqual(dashboard_response.context["expenses"], Decimal("25.00"))
		self.assertEqual(dashboard_response.context["income"], Decimal("400.00"))
		self.assertEqual(dashboard_response.context["transaction_count"], 2)
		self.assertEqual(account_balances, {"Готівка": Decimal("275.00"), "Картка": Decimal("700.00")})
		self.assertEqual(accounts_response.context["total_balance"], Decimal("975.00"))
		self.assertRedirects(transfer_response, reverse("account_list"))
		self.assertContains(dashboard_response, "Готівка → Картка")
		self.assertContains(self.client.get(reverse("transaction_list")), "Переказ")

	def test_transfer_form_rejects_same_source_and_destination(self):
		self.client.force_login(self.user)
		account = Account.objects.create(owner=self.user, name="Готівка")

		response = self.client.post(
			reverse("transfer_create"),
			{
				"account": account.pk,
				"transfer_to": account.pk,
				"amount": "10.00",
				"occurred_on": "2026-10-01",
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertIn("transfer_to", response.context["form"].errors)
		self.assertFalse(Transaction.objects.exists())

	def test_transfer_form_rejects_another_users_account(self):
		self.client.force_login(self.user)
		own_account = Account.objects.create(owner=self.user, name="Мій рахунок")
		other_user = User.objects.create_user(username="taras", password="Other-password-526")
		other_account = Account.objects.create(owner=other_user, name="Чужий рахунок")

		response = self.client.post(
			reverse("transfer_create"),
			{
				"account": own_account.pk,
				"transfer_to": other_account.pk,
				"amount": "10.00",
				"occurred_on": "2026-10-01",
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertIn("transfer_to", response.context["form"].errors)
		self.assertFalse(Transaction.objects.exists())

	def test_new_income_or_expense_requires_an_account_when_one_exists(self):
		self.client.force_login(self.user)
		Account.objects.create(owner=self.user, name="Основна картка")

		response = self.client.post(
			reverse("transaction_create"),
			{
				"kind": Transaction.Kind.EXPENSE,
				"category": self.category.pk,
				"amount": "25.00",
				"occurred_on": "2026-10-01",
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertIn("account", response.context["form"].errors)
		self.assertFalse(Transaction.objects.exists())

	def test_new_expense_is_assigned_to_selected_account(self):
		self.client.force_login(self.user)
		account = Account.objects.create(owner=self.user, name="Основна картка")

		response = self.client.post(
			reverse("transaction_create"),
			{
				"kind": Transaction.Kind.EXPENSE,
				"category": self.category.pk,
				"account": account.pk,
				"amount": "25.00",
				"occurred_on": "2026-10-01",
			},
		)

		self.assertRedirects(response, reverse("dashboard"))
		self.assertEqual(Transaction.objects.get().account, account)

	def test_report_compares_months_and_excludes_transfers_from_income_and_expense(self):
		self.client.force_login(self.user)
		cash = Account.objects.create(
			owner=self.user,
			name="Готівка",
			initial_balance=Decimal("500.00"),
		)
		bank = Account.objects.create(
			owner=self.user,
			name="Картка",
			initial_balance=Decimal("100.00"),
		)
		income_category = Category.objects.create(
			owner=self.user,
			name="Зарплата",
			kind=Category.Kind.INCOME,
		)
		Transaction.objects.create(
			owner=self.user,
			category=self.category,
			account=cash,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("120.00"),
			occurred_on=date(2026, 10, 10),
		)
		Transaction.objects.create(
			owner=self.user,
			category=income_category,
			account=bank,
			kind=Transaction.Kind.INCOME,
			amount=Decimal("900.00"),
			occurred_on=date(2026, 10, 5),
		)
		Transaction.objects.create(
			owner=self.user,
			account=cash,
			transfer_to=bank,
			kind=Transaction.Kind.TRANSFER,
			amount=Decimal("200.00"),
			occurred_on=date(2026, 10, 15),
			note="Внутрішній переказ",
		)

		response = self.client.get(
			reverse("reports"),
			{"date_from": "2026-10-01", "date_to": "2026-10-31"},
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["income"], Decimal("900.00"))
		self.assertEqual(response.context["expenses"], Decimal("120.00"))
		self.assertEqual(response.context["transfer_count"], 1)
		self.assertEqual(response.context["monthly_rows"][0]["net"], Decimal("780.00"))
		self.assertEqual(response.context["account_start_total"], Decimal("600.00"))
		self.assertEqual(response.context["account_end_total"], Decimal("1380.00"))
		self.assertEqual(response.context["account_rows"][0]["account"], bank)

	def test_csv_export_respects_owner_and_date_range_and_includes_transfer(self):
		self.client.force_login(self.user)
		account = Account.objects.create(owner=self.user, name="Готівка")
		other_user = User.objects.create_user(username="taras", password="Other-password-526")
		other_category = Category.objects.create(
			owner=other_user,
			name="Чуже",
			kind=Category.Kind.EXPENSE,
		)
		Transaction.objects.create(
			owner=self.user,
			account=account,
			transfer_to=Account.objects.create(owner=self.user, name="Картка"),
			kind=Transaction.Kind.TRANSFER,
			amount=Decimal("50.00"),
			occurred_on=date(2026, 10, 10),
			note="Свій переказ",
		)
		Transaction.objects.create(
			owner=other_user,
			category=other_category,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("999.00"),
			occurred_on=date(2026, 10, 10),
			note="Чужий запис",
		)
		Transaction.objects.create(
			owner=self.user,
			category=self.category,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("20.00"),
			occurred_on=date(2026, 11, 1),
			note="Поза періодом",
		)

		response = self.client.get(
			reverse("export_transactions_csv"),
			{"date_from": "2026-10-01", "date_to": "2026-10-31"},
		)
		content = response.content.decode("utf-8-sig")

		self.assertEqual(response.status_code, 200)
		self.assertIn("text/csv", response["Content-Type"])
		self.assertIn("Свій переказ", content)
		self.assertNotIn("Чужий запис", content)
		self.assertNotIn("Поза періодом", content)
		self.assertIn("Переказ", content)

	def test_reports_require_login(self):
		response = self.client.get(reverse("reports"))

		self.assertRedirects(response, f"{reverse('login')}?next={reverse('reports')}")

	def test_reports_open_with_default_six_month_period(self):
		self.client.force_login(self.user)

		response = self.client.get(reverse("reports"))

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context["has_report"])
		self.assertEqual(len(response.context["monthly_rows"]), 6)

	def test_report_rejects_reversed_or_overlong_date_ranges(self):
		self.client.force_login(self.user)

		for date_range in (
			{"date_from": "2026-10-31", "date_to": "2026-10-01"},
			{"date_from": "2024-01-01", "date_to": "2026-01-01"},
		):
			with self.subTest(date_range=date_range):
				response = self.client.get(reverse("reports"), date_range)

				self.assertEqual(response.status_code, 200)
				self.assertFalse(response.context["has_report"])
				self.assertTrue(response.context["form"].errors)

	def test_pwa_manifest_has_install_metadata_and_quick_add_shortcut(self):
		response = self.client.get(reverse("pwa_manifest"))
		manifest = response.json()

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "application/manifest+json")
		self.assertEqual(manifest["start_url"], "/")
		self.assertEqual(manifest["scope"], "/")
		self.assertEqual(manifest["display"], "standalone")
		self.assertEqual(manifest["shortcuts"][0]["url"], "/transactions/add/")

	def test_service_worker_has_root_scope_and_never_caches_private_pages(self):
		response = self.client.get(reverse("pwa_service_worker"))
		worker = b"".join(response.streaming_content).decode("utf-8")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Service-Worker-Allowed"], "/")
		self.assertIn("request.mode === \"navigate\"", worker)
		self.assertIn("caches.match(OFFLINE_URL)", worker)
		self.assertNotIn("/transactions/", worker)
		self.assertNotIn("/accounts/", worker)

	def test_offline_page_does_not_render_authenticated_user_data(self):
		self.client.force_login(self.user)

		response = self.client.get(reverse("offline"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Фінансові дані не зберігаються")
		self.assertNotContains(response, self.user.username)

	def test_base_template_links_manifest_and_registers_service_worker_script(self):
		self.client.force_login(self.user)

		response = self.client.get(reverse("dashboard"))

		self.assertContains(response, reverse("pwa_manifest"))
		self.assertContains(response, "finance/pwa.js")
		self.assertContains(response, "ios-install-help")

	def test_csv_preview_does_not_write_until_confirmed_and_duplicate_is_skipped(self):
		self.client.force_login(self.user)
		account = Account.objects.create(owner=self.user, name="Готівка")
		csv_content = (
			"Дата;Сума;Тип;Категорія;Рахунок;Нотатка\n"
			"01.10.2026;1.234,50;Витрата;Продукти;Готівка;Тестовий чек\n"
		).encode("utf-8-sig")

		preview_response = self.preview_csv(csv_content)

		self.assertEqual(preview_response.status_code, 200)
		self.assertEqual(preview_response.context["step"], "preview")
		self.assertEqual(preview_response.context["valid_count"], 1)
		self.assertEqual(preview_response.context["preview"][0]["amount"], "1234.50")
		self.assertFalse(Transaction.objects.exists())

		import_response = self.client.post(reverse("transaction_import"), {"action": "import", "selected": "0"})

		self.assertRedirects(import_response, reverse("transaction_list"))
		created = Transaction.objects.get(owner=self.user)
		self.assertEqual(created.amount, Decimal("1234.50"))
		self.assertEqual(created.account, account)
		self.assertEqual(created.note, "Тестовий чек")

		duplicate_response = self.preview_csv(csv_content)

		self.assertEqual(duplicate_response.context["duplicate_count"], 1)
		self.assertEqual(duplicate_response.context["valid_count"], 0)
		self.assertTrue(duplicate_response.context["preview"][0]["duplicate"])
		self.assertEqual(Transaction.objects.filter(owner=self.user).count(), 1)

	def test_csv_import_preview_reports_invalid_rows_without_creating_transactions(self):
		self.client.force_login(self.user)
		csv_content = (
			"date,amount,category\n"
			"not-a-date,12.00,Продукти\n"
		).encode("utf-8")

		response = self.preview_csv(
			csv_content,
			{
				"date_column": "0",
				"amount_column": "1",
				"category_column": "2",
				"default_kind": Transaction.Kind.EXPENSE,
			},
		)

		self.assertEqual(response.context["error_count"], 1)
		self.assertEqual(response.context["valid_count"], 0)
		self.assertIn("Дата", response.context["preview"][0]["errors"][0])
		self.assertFalse(Transaction.objects.exists())

	def test_csv_export_escapes_spreadsheet_formulas_in_user_text(self):
		self.client.force_login(self.user)
		Transaction.objects.create(
			owner=self.user,
			category=self.category,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("10.00"),
			occurred_on=date(2026, 10, 1),
			note="=HYPERLINK(\"https://example.test\")",
		)

		response = self.client.get(
			reverse("export_transactions_csv"),
			{"date_from": "2026-10-01", "date_to": "2026-10-31"},
		)

		self.assertIn("'=HYPERLINK", response.content.decode("utf-8-sig"))

	def preview_csv(self, content, mapping=None):
		upload_response = self.client.post(
			reverse("transaction_import"),
			{
				"action": "upload",
				"file": SimpleUploadedFile("statement.csv", content, content_type="text/csv"),
			},
		)
		self.assertEqual(upload_response.context["step"], "mapping")
		form = upload_response.context["mapping_form"]
		mapping_data = {
			field: form.initial[field]
			for field in ("date_column", "amount_column", "kind_column", "category_column", "account_column", "transfer_to_column", "note_column")
			if form.initial.get(field) is not None
		}
		mapping_data.update({"default_kind": Transaction.Kind.EXPENSE, **(mapping or {})})
		return self.client.post(
			reverse("transaction_import"),
			{"action": "preview", **mapping_data},
		)

	def test_savings_goal_creation_is_owned_by_current_user(self):
		self.client.force_login(self.user)

		response = self.client.post(
			reverse("savings_goal_create"),
			{
				"name": "Відпустка",
				"target_amount": "30000.00",
				"target_date": "2027-06-01",
				"note": "Літо біля моря",
			},
		)

		self.assertRedirects(response, reverse("savings_goal_list"))
		goal = SavingsGoal.objects.get()
		self.assertEqual(goal.owner, self.user)
		self.assertEqual(goal.target_amount, Decimal("30000.00"))

	def test_goal_contributions_update_progress_but_not_financial_transactions(self):
		self.client.force_login(self.user)
		goal = SavingsGoal.objects.create(
			owner=self.user,
			name="Велосипед",
			target_amount=Decimal("1000.00"),
		)
		account = Account.objects.create(owner=self.user, name="Картка", initial_balance=Decimal("500.00"))

		first_response = self.client.post(
			reverse("savings_contribution_create", args=[goal.pk]),
			{"amount": "250.00", "contributed_on": "2026-10-01", "note": "Жовтень"},
		)
		second_response = self.client.post(
			reverse("savings_contribution_create", args=[goal.pk]),
			{"amount": "250.00", "contributed_on": "2026-10-02", "note": "Ще внесок"},
		)

		self.assertRedirects(first_response, reverse("savings_goal_list"))
		self.assertRedirects(second_response, reverse("savings_goal_list"))
		list_response = self.client.get(reverse("savings_goal_list"))
		listed_goal = list_response.context["goals"][0]
		self.assertEqual(listed_goal.saved_amount, Decimal("500.00"))
		self.assertEqual(listed_goal.remaining_amount, Decimal("500.00"))
		self.assertEqual(listed_goal.progress_percent, 50)
		self.assertEqual(goal.contributions.count(), 2)
		self.assertFalse(Transaction.objects.exists())
		self.assertEqual(account_balance_map([account])[account.pk], Decimal("500.00"))

	def test_goal_contribution_cannot_exceed_remaining_target(self):
		self.client.force_login(self.user)
		goal = SavingsGoal.objects.create(
			owner=self.user,
			name="Камера",
			target_amount=Decimal("100.00"),
		)
		GoalContribution.objects.create(
			goal=goal,
			amount=Decimal("75.00"),
			contributed_on=date(2026, 10, 1),
		)

		response = self.client.post(
			reverse("savings_contribution_create", args=[goal.pk]),
			{"amount": "30.00", "contributed_on": "2026-10-02", "note": "Завеликий внесок"},
		)

		self.assertEqual(response.status_code, 200)
		self.assertIn("amount", response.context["form"].errors)
		self.assertEqual(goal.contributions.count(), 1)

	def test_user_cannot_view_or_add_contribution_to_another_users_goal(self):
		other_user = User.objects.create_user(username="taras", password="Other-password-526")
		goal = SavingsGoal.objects.create(
			owner=other_user,
			name="Чужа ціль",
			target_amount=Decimal("1000.00"),
		)
		self.client.force_login(self.user)

		edit_response = self.client.get(reverse("savings_goal_update", args=[goal.pk]))
		contribution_response = self.client.post(
			reverse("savings_contribution_create", args=[goal.pk]),
			{"amount": "10.00", "contributed_on": "2026-10-01"},
		)

		self.assertEqual(edit_response.status_code, 404)
		self.assertEqual(contribution_response.status_code, 404)
		self.assertEqual(goal.contributions.count(), 0)

	def test_budget_alerts_trigger_once_at_80_and_100_percent_and_ignore_transfers(self):
		self.client.force_login(self.user)
		month = timezone.localdate().replace(day=1)
		CategoryBudget.objects.create(
			owner=self.user,
			category=self.category,
			month=month,
			amount=Decimal("100.00"),
		)
		cash = Account.objects.create(owner=self.user, name="Готівка")
		card = Account.objects.create(owner=self.user, name="Картка")
		Transaction.objects.create(
			owner=self.user,
			category=self.category,
			account=cash,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("79.99"),
			occurred_on=month,
		)
		Transaction.objects.create(
			owner=self.user,
			account=cash,
			transfer_to=card,
			kind=Transaction.Kind.TRANSFER,
			amount=Decimal("500.00"),
			occurred_on=month,
		)
		query = {"month": month.strftime("%Y-%m")}

		below_threshold_response = self.client.get(reverse("dashboard"), query)
		self.assertEqual(below_threshold_response.context["budget_alerts"], [])
		self.assertFalse(BudgetAlert.objects.exists())

		Transaction.objects.create(
			owner=self.user,
			category=self.category,
			account=cash,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("0.01"),
			occurred_on=month,
		)
		eighty_response = self.client.get(reverse("dashboard"), query)
		self.assertEqual([alert.threshold for alert in eighty_response.context["budget_alerts"]], [80])

		Transaction.objects.create(
			owner=self.user,
			category=self.category,
			account=cash,
			kind=Transaction.Kind.EXPENSE,
			amount=Decimal("20.00"),
			occurred_on=month,
		)
		over_limit_response = self.client.get(reverse("dashboard"), query)
		self.assertEqual({alert.threshold for alert in over_limit_response.context["budget_alerts"]}, {80, 100})
		self.client.get(reverse("dashboard"), query)
		self.assertEqual(BudgetAlert.objects.filter(owner=self.user).count(), 2)

	def test_budget_alert_can_be_marked_read_only_by_its_owner(self):
		month = timezone.localdate().replace(day=1)
		other_user = User.objects.create_user(username="taras", password="Other-password-526")
		other_category = Category.objects.create(
			owner=other_user,
			name="Оренда",
			kind=Category.Kind.EXPENSE,
		)
		other_budget = CategoryBudget.objects.create(
			owner=other_user,
			category=other_category,
			month=month,
			amount=Decimal("100.00"),
		)
		alert = BudgetAlert.objects.create(
			owner=other_user,
			category_budget=other_budget,
			threshold=BudgetAlert.Threshold.NEAR_LIMIT,
		)
		self.client.force_login(self.user)

		response = self.client.post(reverse("budget_alert_mark_read", args=[alert.pk]))

		self.assertRedirects(response, reverse("dashboard"))
		alert.refresh_from_db()
		self.assertFalse(alert.is_read)

		self.client.force_login(other_user)
		response = self.client.post(reverse("budget_alert_mark_read", args=[alert.pk]))
		alert.refresh_from_db()
		self.assertRedirects(response, reverse("dashboard"))
		self.assertTrue(alert.is_read)
