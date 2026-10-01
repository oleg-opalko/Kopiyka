import calendar
import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.utils import timezone

from .forms import (
	AccountForm,
	AmountForm,
	CategoryAmountForm,
	CategoryForm,
	CSVMappingForm,
	CSVUploadForm,
	GoalContributionForm,
	RecurringPaymentForm,
	ReportFilterForm,
	RegisterForm,
	SavingsGoalForm,
	TransactionFilterForm,
	TransactionForm,
	TransferForm,
)
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
from .csv_imports import existing_transaction_fingerprints, mapped_rows_preview, read_csv_upload, row_fingerprint
from .services import account_balance_map, generate_due_transactions


DEFAULT_CATEGORIES = (
	("Продукти", Category.Kind.EXPENSE, "#258b72"),
	("Житло", Category.Kind.EXPENSE, "#e58b43"),
	("Транспорт", Category.Kind.EXPENSE, "#4779a8"),
	("Здоров'я", Category.Kind.EXPENSE, "#cf6263"),
	("Покупки", Category.Kind.EXPENSE, "#8c70a5"),
	("Розваги", Category.Kind.EXPENSE, "#d2a83f"),
	("Інше", Category.Kind.EXPENSE, "#74827f"),
	("Зарплата", Category.Kind.INCOME, "#258b72"),
	("Додатковий дохід", Category.Kind.INCOME, "#4779a8"),
)


MONTH_NAMES = (
	"січень", "лютий", "березень", "квітень", "травень", "червень",
	"липень", "серпень", "вересень", "жовтень", "листопад", "грудень",
)


def seed_categories(user):
	for name, kind, color in DEFAULT_CATEGORIES:
		Category.objects.get_or_create(
			owner=user,
			name=name,
			kind=kind,
			defaults={"color": color},
		)


def selected_month(value):
	if value:
		try:
			year, month = (int(part) for part in value.split("-", maxsplit=1))
			return date(year, month, 1)
		except (ValueError, TypeError):
			pass
	today = timezone.localdate()
	return today.replace(day=1)


def month_bounds(month):
	return month, date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])


def shift_month(month, amount):
	month_index = month.year * 12 + month.month - 1 + amount
	return date(month_index // 12, month_index % 12 + 1, 1)


def month_range(start_date, end_date):
	month = start_date.replace(day=1)
	last_month = end_date.replace(day=1)
	while month <= last_month:
		year, month_number = month.year, month.month
		next_month = shift_month(month, 1)
		month_end = date(year, month_number, calendar.monthrange(year, month_number)[1])
		yield month, max(start_date, month), min(end_date, month_end)
		month = next_month


def report_filter_form(request):
	today = timezone.localdate()
	default_from = shift_month(today.replace(day=1), -5)
	if "date_from" in request.GET or "date_to" in request.GET:
		return ReportFilterForm(request.GET)
	else:
		return ReportFilterForm(data={"date_from": default_from, "date_to": today})


def month_context_label(month):
	return f"{MONTH_NAMES[month.month - 1].capitalize()} {month.year}"


@login_required
def dashboard(request):
	seed_categories(request.user)
	accounts = list(Account.objects.filter(owner=request.user, is_archived=False))
	account_balances = account_balance_map(accounts)
	for account in accounts:
		account.current_balance = account_balances[account.pk]
	month = selected_month(request.GET.get("month"))
	first_day, last_day = month_bounds(month)
	transactions = Transaction.objects.filter(owner=request.user, occurred_on__range=(first_day, last_day))
	totals = transactions.values("kind").annotate(total=Sum("amount"))
	totals_by_kind = {row["kind"]: row["total"] or Decimal("0") for row in totals}
	expenses = totals_by_kind.get(Transaction.Kind.EXPENSE, Decimal("0"))
	income = totals_by_kind.get(Transaction.Kind.INCOME, Decimal("0"))
	monthly_budget = MonthlyBudget.objects.filter(owner=request.user, month=month).first()
	category_budgets = {
		budget.category_id: budget
		for budget in CategoryBudget.objects.filter(owner=request.user, month=month).select_related("category")
	}
	category_rows = list(
		transactions.filter(kind=Transaction.Kind.EXPENSE)
		.values("category_id", "category__name", "category__color")
		.annotate(total=Sum("amount"))
		.order_by("-total")
	)
	max_category_total = max((row["total"] for row in category_rows), default=Decimal("1"))
	for row in category_rows:
		row["bar_width"] = min(100, int(row["total"] * 100 / max_category_total))
		row["budget"] = category_budgets.get(row["category_id"])
		row["budget_percent"] = (
			int(row["total"] * 100 / row["budget"].amount)
			if row["budget"]
			else 0
		)

	current_month = timezone.localdate().replace(day=1)
	if month == current_month:
		for row in category_rows:
			budget = row["budget"]
			if not budget:
				continue
			for threshold in BudgetAlert.Threshold.values:
				if row["total"] * 100 >= budget.amount * threshold:
					BudgetAlert.objects.get_or_create(
						owner=request.user,
						category_budget=budget,
						threshold=threshold,
					)
	budget_alerts = list(
		BudgetAlert.objects.filter(
			owner=request.user,
				category_budget__month=month,
				is_read=False,
			)
		.select_related("category_budget__category")
		.order_by("-threshold", "-created_at")
	)
	category_totals = {row["category_id"]: row["total"] for row in category_rows}
	for alert in budget_alerts:
		alert.spent_amount = category_totals.get(alert.category_budget.category_id, Decimal("0"))

	daily_totals = {
		row["occurred_on"]: row["total"]
		for row in transactions.filter(kind=Transaction.Kind.EXPENSE)
		.values("occurred_on")
		.annotate(total=Sum("amount"))
	}
	days_in_month = calendar.monthrange(month.year, month.month)[1]
	daily_values = [daily_totals.get(first_day + timedelta(days=offset), Decimal("0")) for offset in range(days_in_month)]
	max_daily_total = max(daily_values, default=Decimal("0"))
	daily_bars = [max(4, int(value * 100 / max_daily_total)) if value else 0 for value in daily_values]
	context = {
		"month": month,
		"month_label": month_context_label(month),
		"previous_month": shift_month(month, -1).strftime("%Y-%m"),
		"next_month": shift_month(month, 1).strftime("%Y-%m"),
		"income": income,
		"expenses": expenses,
		"net": income - expenses,
		"monthly_budget": monthly_budget,
		"budget_remaining": (monthly_budget.amount - expenses) if monthly_budget else None,
		"budget_percent": min(100, int(expenses * 100 / monthly_budget.amount)) if monthly_budget else 0,
		"category_rows": category_rows,
		"budget_alerts": budget_alerts,
		"daily_bars": daily_bars,
		"days_in_month": range(1, days_in_month + 1),
		"latest_transactions": transactions.select_related("category", "account", "transfer_to")[:6],
		"transaction_count": transactions.filter(
			kind__in=[Transaction.Kind.EXPENSE, Transaction.Kind.INCOME]
		).count(),
		"accounts": accounts[:4],
		"account_balances": account_balances,
		"total_account_balance": sum(account_balances.values(), Decimal("0")),
	}
	return render(request, "finance/dashboard.html", context)


def register(request):
	if request.user.is_authenticated:
		return redirect("dashboard")
	form = RegisterForm(request.POST or None)
	if request.method == "POST" and form.is_valid():
		user = form.save()
		seed_categories(user)
		login(request, user)
		return redirect("dashboard")
	return render(request, "finance/register.html", {"form": form})


@login_required
def transaction_list(request):
	transactions = Transaction.objects.filter(owner=request.user).select_related("category")
	filter_form = TransactionFilterForm(request.GET, user=request.user)
	if filter_form.is_valid():
		search = filter_form.cleaned_data["search"]
		kind = filter_form.cleaned_data["kind"]
		category = filter_form.cleaned_data["category"]
		date_from = filter_form.cleaned_data["date_from"]
		date_to = filter_form.cleaned_data["date_to"]
		if search:
			transactions = transactions.filter(Q(note__icontains=search) | Q(category__name__icontains=search))
		if kind:
			transactions = transactions.filter(kind=kind)
		if category:
			transactions = transactions.filter(category=category)
		if date_from:
			transactions = transactions.filter(occurred_on__gte=date_from)
		if date_to:
			transactions = transactions.filter(occurred_on__lte=date_to)
	else:
		transactions = transactions.none()
	query_params = request.GET.copy()
	query_params.pop("page", None)
	page_obj = Paginator(transactions, 25).get_page(request.GET.get("page"))
	return render(
		request,
		"finance/transaction_list.html",
		{
			"page_obj": page_obj,
			"transactions": page_obj.object_list,
			"filter_form": filter_form,
			"page_query": query_params.urlencode(),
		},
	)


@login_required
def transaction_create(request):
	seed_categories(request.user)
	form = TransactionForm(request.POST or None, user=request.user)
	if request.method == "POST" and form.is_valid():
		transaction = form.save(commit=False)
		transaction.owner = request.user
		transaction.save()
		messages.success(request, "Операцію додано.")
		return redirect("dashboard")
	return render(request, "finance/transaction_form.html", {"form": form, "title": "Нова операція"})


@login_required
def transaction_update(request, pk):
	transaction = get_object_or_404(Transaction, pk=pk, owner=request.user)
	form_class = TransferForm if transaction.kind == Transaction.Kind.TRANSFER else TransactionForm
	form = form_class(request.POST or None, instance=transaction, user=request.user)
	if request.method == "POST" and form.is_valid():
		form.save()
		messages.success(request, "Зміни збережено.")
		return redirect("transaction_list")
	return render(request, "finance/transaction_form.html", {"form": form, "title": "Редагувати операцію"})


@login_required
def transaction_delete(request, pk):
	transaction = get_object_or_404(Transaction, pk=pk, owner=request.user)
	if request.method == "POST":
		transaction.delete()
		messages.success(request, "Операцію видалено.")
	return redirect("transaction_list")


@login_required
def category_create(request):
	form = CategoryForm(request.POST or None)
	if request.method == "POST" and form.is_valid():
		category = form.save(commit=False)
		category.owner = request.user
		category.save()
		messages.success(request, "Категорію створено.")
		return redirect("budget_settings")
	return render(request, "finance/category_form.html", {"form": form})


@login_required
def budget_settings(request):
	month = selected_month(request.GET.get("month") or request.POST.get("month"))
	monthly_budget = MonthlyBudget.objects.filter(owner=request.user, month=month).first()
	monthly_form = AmountForm(request.POST or None, initial={"amount": monthly_budget.amount if monthly_budget else None})
	category_form = CategoryAmountForm(request.POST or None, user=request.user)
	if request.method == "POST":
		if "save_monthly" in request.POST and monthly_form.is_valid():
			MonthlyBudget.objects.update_or_create(
				owner=request.user,
				month=month,
				defaults={"amount": monthly_form.cleaned_data["amount"]},
			)
			messages.success(request, "Місячний бюджет збережено.")
			return redirect(f"/budgets/?month={month:%Y-%m}")
		if "save_category" in request.POST and category_form.is_valid():
			CategoryBudget.objects.update_or_create(
				owner=request.user,
				month=month,
				category=category_form.cleaned_data["category"],
				defaults={"amount": category_form.cleaned_data["amount"]},
			)
			messages.success(request, "Ліміт категорії збережено.")
			return redirect(f"/budgets/?month={month:%Y-%m}")
		if "delete_category_budget" in request.POST:
			CategoryBudget.objects.filter(
				pk=request.POST.get("delete_category_budget"), owner=request.user, month=month
			).delete()
			return redirect(f"/budgets/?month={month:%Y-%m}")

	return render(
		request,
		"finance/budget_settings.html",
		{
			"month": month,
			"month_label": month_context_label(month),
			"previous_month": shift_month(month, -1).strftime("%Y-%m"),
			"next_month": shift_month(month, 1).strftime("%Y-%m"),
			"monthly_form": monthly_form,
			"category_form": category_form,
			"monthly_budget": monthly_budget,
			"category_budgets": CategoryBudget.objects.filter(owner=request.user, month=month).select_related("category"),
			"categories": Category.objects.filter(owner=request.user).order_by("kind", "name"),
		},
	)


@login_required
def budget_alert_mark_read(request, pk):
	if request.method == "POST":
		BudgetAlert.objects.filter(pk=pk, owner=request.user).update(is_read=True)
	month = request.POST.get("month")
	if month:
		return redirect(f"/?month={month}")
	return redirect("dashboard")


@login_required
def recurring_list(request):
	return render(
		request,
		"finance/recurring_list.html",
		{
			"payments": RecurringPayment.objects.filter(owner=request.user).select_related("category"),
			"today": timezone.localdate(),
		},
	)


@login_required
def recurring_create(request):
	form = RecurringPaymentForm(request.POST or None, user=request.user)
	if request.method == "POST" and form.is_valid():
		payment = form.save(commit=False)
		payment.owner = request.user
		payment.save()
		messages.success(request, "Регулярний платіж створено.")
		return redirect("recurring_list")
	return render(request, "finance/recurring_form.html", {"form": form, "title": "Новий регулярний платіж"})


@login_required
def recurring_update(request, pk):
	payment = get_object_or_404(RecurringPayment, pk=pk, owner=request.user)
	form = RecurringPaymentForm(request.POST or None, instance=payment, user=request.user)
	if request.method == "POST" and form.is_valid():
		form.save()
		messages.success(request, "Регулярний платіж оновлено.")
		return redirect("recurring_list")
	return render(request, "finance/recurring_form.html", {"form": form, "title": "Редагувати регулярний платіж"})


@login_required
def recurring_generate(request, pk):
	if request.method == "POST":
		payment = get_object_or_404(RecurringPayment, pk=pk, owner=request.user)
		count = generate_due_transactions(payment.pk, request.user, timezone.localdate())
		if count:
			messages.success(request, f"Створено операцій: {count}.")
		else:
			messages.info(request, "Немає прострочених повторів для створення.")
	return redirect("recurring_list")


@login_required
def account_list(request):
	accounts = list(Account.objects.filter(owner=request.user))
	balances = account_balance_map(accounts)
	for account in accounts:
		account.current_balance = balances[account.pk]
	unassigned_transactions = Transaction.objects.filter(
		owner=request.user,
		kind__in=[Transaction.Kind.EXPENSE, Transaction.Kind.INCOME],
		account__isnull=True,
	).count()
	return render(
		request,
		"finance/account_list.html",
		{
			"accounts": accounts,
			"total_balance": sum(balances.values(), Decimal("0")),
			"unassigned_transactions": unassigned_transactions,
		},
	)


@login_required
def account_create(request):
	form = AccountForm(request.POST or None)
	if request.method == "POST" and form.is_valid():
		account = form.save(commit=False)
		account.owner = request.user
		account.save()
		messages.success(request, "Рахунок створено.")
		return redirect("account_list")
	return render(request, "finance/account_form.html", {"form": form, "title": "Новий рахунок"})


@login_required
def account_update(request, pk):
	account = get_object_or_404(Account, pk=pk, owner=request.user)
	form = AccountForm(request.POST or None, instance=account)
	if request.method == "POST" and form.is_valid():
		form.save()
		messages.success(request, "Рахунок оновлено.")
		return redirect("account_list")
	return render(request, "finance/account_form.html", {"form": form, "title": "Налаштування рахунку"})


@login_required
def transfer_create(request):
	form = TransferForm(request.POST or None, user=request.user)
	account_count = Account.objects.filter(owner=request.user, is_archived=False).count()
	if request.method == "POST" and form.is_valid():
		transfer = form.save(commit=False)
		transfer.owner = request.user
		transfer.save()
		messages.success(request, "Переказ збережено. Він не впливає на дохід і витрати.")
		return redirect("account_list")
	return render(
		request,
		"finance/transfer_form.html",
		{"form": form, "account_count": account_count},
	)


@login_required
def reports(request):
	form = report_filter_form(request)
	if not form.is_valid():
		return render(request, "finance/reports.html", {"form": form, "has_report": False})

	date_from = form.cleaned_data["date_from"]
	date_to = form.cleaned_data["date_to"]
	transactions = Transaction.objects.filter(
		owner=request.user,
		occurred_on__range=(date_from, date_to),
	)
	totals = {
		row["kind"]: row["total"] or Decimal("0")
		for row in transactions.filter(kind__in=[Transaction.Kind.INCOME, Transaction.Kind.EXPENSE])
		.values("kind")
		.annotate(total=Sum("amount"))
	}
	income = totals.get(Transaction.Kind.INCOME, Decimal("0"))
	expenses = totals.get(Transaction.Kind.EXPENSE, Decimal("0"))

	monthly_totals = {}
	monthly_transactions = transactions.filter(kind__in=[Transaction.Kind.INCOME, Transaction.Kind.EXPENSE])
	monthly_rows_query = (
		monthly_transactions
		.values("occurred_on__year", "occurred_on__month", "kind")
		.annotate(total=Sum("amount"))
		.order_by("occurred_on__year", "occurred_on__month")
		.values("occurred_on__year", "occurred_on__month", "kind", "total")
	)
	for row in monthly_rows_query:
		month_key = (row["occurred_on__year"], row["occurred_on__month"])
		monthly_totals.setdefault(month_key, {})[row["kind"]] = row["total"] or Decimal("0")
	monthly_rows = []
	for month_start, period_start, period_end in month_range(date_from, date_to):
		month_data = monthly_totals.get((month_start.year, month_start.month), {})
		month_income = month_data.get(Transaction.Kind.INCOME, Decimal("0"))
		month_expenses = month_data.get(Transaction.Kind.EXPENSE, Decimal("0"))
		monthly_rows.append(
			{
				"label": f"{MONTH_NAMES[month_start.month - 1][:3].capitalize()} {month_start.year}",
				"income": month_income,
				"expenses": month_expenses,
				"net": month_income - month_expenses,
			}
		)
	max_month_value = max(
		(max((row["income"] for row in monthly_rows), default=Decimal("0")),
		 max((row["expenses"] for row in monthly_rows), default=Decimal("0"))),
		default=Decimal("0"),
	)
	for row in monthly_rows:
		row["income_width"] = int(row["income"] * 100 / max_month_value) if max_month_value else 0
		row["expenses_width"] = int(row["expenses"] * 100 / max_month_value) if max_month_value else 0

	category_rows = list(
		transactions.filter(kind=Transaction.Kind.EXPENSE)
		.values("category__name", "category__color")
		.annotate(total=Sum("amount"))
		.order_by("-total")
	)
	max_category_total = max((row["total"] for row in category_rows), default=Decimal("0"))
	for row in category_rows:
		row["width"] = int(row["total"] * 100 / max_category_total) if max_category_total else 0
		row["share"] = int(row["total"] * 100 / expenses) if expenses else 0

	accounts = list(Account.objects.filter(owner=request.user))
	start_balances = account_balance_map(accounts, date_from - timedelta(days=1))
	end_balances = account_balance_map(accounts, date_to)
	account_rows = []
	for account in accounts:
		start_balance = start_balances[account.pk]
		end_balance = end_balances[account.pk]
		account_rows.append(
			{
				"account": account,
				"start_balance": start_balance,
				"end_balance": end_balance,
				"change": end_balance - start_balance,
			}
		)
	account_rows.sort(key=lambda row: row["end_balance"], reverse=True)

	return render(
		request,
		"finance/reports.html",
		{
			"form": form,
			"has_report": True,
			"date_from": date_from,
			"date_to": date_to,
			"income": income,
			"expenses": expenses,
			"net": income - expenses,
			"transfer_count": transactions.filter(kind=Transaction.Kind.TRANSFER).count(),
			"monthly_rows": monthly_rows,
			"category_rows": category_rows,
			"account_rows": account_rows,
			"account_start_total": sum(start_balances.values(), Decimal("0")),
			"account_end_total": sum(end_balances.values(), Decimal("0")),
			"export_query": f"date_from={date_from:%Y-%m-%d}&date_to={date_to:%Y-%m-%d}",
		},
	)


@login_required
def export_transactions_csv(request):
	form = report_filter_form(request)
	if not form.is_valid():
		return redirect("reports")
	transactions = Transaction.objects.filter(
		owner=request.user,
		occurred_on__range=(form.cleaned_data["date_from"], form.cleaned_data["date_to"]),
	).select_related("category", "account", "transfer_to")
	response = HttpResponse(content_type="text/csv; charset=utf-8")
	response["Content-Disposition"] = 'attachment; filename="kopiyka-transactions.csv"'
	response.write("\ufeff")
	writer = csv.writer(response)
	writer.writerow(["Дата", "Тип", "Категорія", "З рахунку", "На рахунок", "Сума, грн", "Нотатка"])
	def safe_csv_text(value):
		text = str(value or "")
		if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
			return "'" + text
		return text

	for item in transactions:
		writer.writerow(
			[
				item.occurred_on.isoformat(),
				item.get_kind_display(),
				safe_csv_text(item.category.name if item.category_id else ""),
				safe_csv_text(item.account.name if item.account_id else ""),
				safe_csv_text(item.transfer_to.name if item.transfer_to_id else ""),
				item.amount,
				safe_csv_text(item.note),
			]
		)
	return response


def pwa_manifest(request):
	icon_url = request.build_absolute_uri(static("finance/icon.svg"))
	response = JsonResponse(
		{
			"id": "/",
			"name": "Kopiyka — особисті фінанси",
			"short_name": "Kopiyka",
			"description": "Облік доходів, витрат і бюджету",
			"lang": "uk",
			"dir": "ltr",
			"start_url": "/",
			"scope": "/",
			"display": "standalone",
			"orientation": "any",
			"theme_color": "#24785f",
			"background_color": "#f4f6f1",
			"categories": ["finance", "productivity"],
			"icons": [{"src": icon_url, "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
			"shortcuts": [
				{
					"name": "Додати операцію",
					"short_name": "Додати",
					"url": "/transactions/add/",
					"icons": [{"src": icon_url, "sizes": "any", "type": "image/svg+xml"}],
				}
			],
		},
		content_type="application/manifest+json",
	)
	response["Cache-Control"] = "public, max-age=3600"
	return response


def pwa_service_worker(request):
	worker_path = Path(__file__).resolve().parent / "static" / "finance" / "service-worker.js"
	response = FileResponse(worker_path.open("rb"), content_type="application/javascript; charset=utf-8")
	response["Service-Worker-Allowed"] = "/"
	response["Cache-Control"] = "no-cache"
	return response


def offline_page(request):
	return render(request, "finance/offline.html")


def suggested_column(headers, aliases):
	normalized_headers = [header.casefold().strip() for header in headers]
	for alias in aliases:
		for index, header in enumerate(normalized_headers):
			if header == alias:
				return str(index)
	for alias in aliases:
		for index, header in enumerate(normalized_headers):
			if alias in header:
				return str(index)
	return None


@login_required
def transaction_import(request):
	seed_categories(request.user)
	action = request.POST.get("action", "") if request.method == "POST" else ""

	if request.GET.get("reset"):
		for key in ("csv_import_headers", "csv_import_rows", "csv_import_preview"):
			request.session.pop(key, None)
		return redirect("transaction_import")

	if action == "upload":
		upload_form = CSVUploadForm(request.POST, request.FILES)
		if upload_form.is_valid():
			try:
				headers, raw_rows = read_csv_upload(upload_form.cleaned_data["file"])
			except ValidationError as error:
				upload_form.add_error("file", error.messages)
			else:
				request.session["csv_import_headers"] = headers
				request.session["csv_import_rows"] = raw_rows
				request.session.pop("csv_import_preview", None)
				initial = {
					"date_column": suggested_column(headers, ("date", "дата")),
					"amount_column": suggested_column(headers, ("amount", "сума", "сумма")),
					"kind_column": suggested_column(headers, ("type", "тип", "операція", "operation")),
					"category_column": suggested_column(headers, ("category", "категорія", "категория")),
					"account_column": suggested_column(headers, ("account", "рахунок", "счет")),
					"transfer_to_column": suggested_column(headers, ("transfer_to", "отримувач", "to_account")),
					"note_column": suggested_column(headers, ("note", "нотатка", "опис", "коментар", "description")),
				}
				mapping_form = CSVMappingForm(user=request.user, headers=headers, initial=initial)
				return render(
					request,
					"finance/import_csv.html",
					{
						"step": "mapping",
						"upload_form": upload_form,
						"mapping_form": mapping_form,
						"headers": headers,
						"row_count": len(raw_rows),
					},
				)
		return render(request, "finance/import_csv.html", {"step": "upload", "upload_form": upload_form})

	if action == "preview":
		headers = request.session.get("csv_import_headers")
		raw_rows = request.session.get("csv_import_rows")
		if not headers or not raw_rows:
			messages.error(request, "Сесію імпорту завершено. Завантаж CSV-файл ще раз.")
			return redirect("transaction_import")
		mapping_form = CSVMappingForm(request.POST, user=request.user, headers=headers)
		if mapping_form.is_valid():
			preview = mapped_rows_preview(request.user, raw_rows, mapping_form.cleaned_data)
			request.session["csv_import_preview"] = preview
			return render(
				request,
				"finance/import_csv.html",
				{
					"step": "preview",
					"preview": preview,
					"valid_count": sum(not row["errors"] and not row["duplicate"] for row in preview),
					"duplicate_count": sum(row["duplicate"] for row in preview),
					"error_count": sum(bool(row["errors"]) for row in preview),
				},
			)
		return render(
			request,
			"finance/import_csv.html",
			{"step": "mapping", "mapping_form": mapping_form, "headers": headers, "row_count": len(raw_rows)},
		)

	if action == "import":
		preview = request.session.get("csv_import_preview")
		if not preview:
			messages.error(request, "Попередній перегляд застарів. Завантаж CSV-файл ще раз.")
			return redirect("transaction_import")
		selected_indices = {
			int(value) for value in request.POST.getlist("selected") if value.isdigit()
		}
		selected_rows = [
			row for index, row in enumerate(preview)
			if index in selected_indices and not row["errors"] and not row["duplicate"]
		]
		seen = existing_transaction_fingerprints(request.user, selected_rows)
		category_ids = {row["category_id"] for row in selected_rows if row["category_id"]}
		account_ids = {
			account_id
			for row in selected_rows
			for account_id in (row["account_id"], row["transfer_to_id"])
			if account_id
		}
		categories = Category.objects.filter(owner=request.user, pk__in=category_ids).in_bulk()
		accounts = Account.objects.filter(owner=request.user, pk__in=account_ids).in_bulk()
		created_count = 0
		duplicate_count = 0
		invalid_count = 0
		with db_transaction.atomic():
			for row in selected_rows:
				fingerprint = row_fingerprint(row)
				if fingerprint in seen:
					duplicate_count += 1
					continue
				category = categories.get(row["category_id"]) if row["category_id"] else None
				account = accounts.get(row["account_id"]) if row["account_id"] else None
				transfer_to = accounts.get(row["transfer_to_id"]) if row["transfer_to_id"] else None
				if (row["category_id"] and not category) or (row["account_id"] and not account) or (row["transfer_to_id"] and not transfer_to):
					invalid_count += 1
					continue
				item = Transaction(
					owner=request.user,
					category=category,
					account=account,
					transfer_to=transfer_to,
					kind=row["kind"],
					amount=Decimal(row["amount"]),
					occurred_on=date.fromisoformat(row["occurred_on"]),
					note=row["note"],
				)
				try:
					item.full_clean()
				except ValidationError:
					invalid_count += 1
					continue
				item.save()
				seen.add(fingerprint)
				created_count += 1
		for key in ("csv_import_headers", "csv_import_rows", "csv_import_preview"):
			request.session.pop(key, None)
		messages.success(request, f"Імпортовано операцій: {created_count}.")
		if duplicate_count or invalid_count:
			messages.warning(request, f"Пропущено дублікатів: {duplicate_count}; рядків із помилками: {invalid_count}.")
		return redirect("transaction_list")

	return render(request, "finance/import_csv.html", {"step": "upload", "upload_form": CSVUploadForm()})


@login_required
def savings_goal_list(request):
	goals = list(SavingsGoal.objects.filter(owner=request.user).prefetch_related("contributions"))
	for goal in goals:
		contributions = list(goal.contributions.all())
		goal.saved_amount = sum((item.amount for item in contributions), Decimal("0"))
		goal.remaining_amount = max(Decimal("0"), goal.target_amount - goal.saved_amount)
		goal.progress_percent = min(100, int(goal.saved_amount * 100 / goal.target_amount))
		goal.is_complete = goal.saved_amount >= goal.target_amount
		goal.recent_contributions = contributions[:4]
	return render(request, "finance/savings_goals.html", {"goals": goals})


@login_required
def savings_goal_create(request):
	form = SavingsGoalForm(request.POST or None)
	if request.method == "POST" and form.is_valid():
		goal = form.save(commit=False)
		goal.owner = request.user
		goal.save()
		messages.success(request, "Ціль заощаджень створено.")
		return redirect("savings_goal_list")
	return render(request, "finance/savings_goal_form.html", {"form": form, "title": "Нова ціль"})


@login_required
def savings_goal_update(request, pk):
	goal = get_object_or_404(SavingsGoal, pk=pk, owner=request.user)
	form = SavingsGoalForm(request.POST or None, instance=goal)
	if request.method == "POST" and form.is_valid():
		form.save()
		messages.success(request, "Ціль оновлено.")
		return redirect("savings_goal_list")
	return render(request, "finance/savings_goal_form.html", {"form": form, "title": "Налаштування цілі"})


@login_required
def savings_contribution_create(request, pk):
	goal = get_object_or_404(SavingsGoal, pk=pk, owner=request.user)
	saved_amount = goal.contributions.aggregate(total=Sum("amount"))["total"] or Decimal("0")
	remaining = max(Decimal("0"), goal.target_amount - saved_amount)
	if goal.is_archived or not remaining:
		messages.info(request, "Ціль уже досягнута або перебуває в архіві.")
		return redirect("savings_goal_list")
	form = GoalContributionForm(request.POST or None, remaining=remaining)
	if request.method == "POST" and form.is_valid():
		GoalContribution.objects.create(
			goal=goal,
			amount=form.cleaned_data["amount"],
			contributed_on=form.cleaned_data["contributed_on"],
			note=form.cleaned_data["note"],
		)
		messages.success(request, "Внесок додано до цілі.")
		return redirect("savings_goal_list")
	return render(
		request,
		"finance/goal_contribution_form.html",
		{"form": form, "goal": goal, "remaining": remaining},
	)


@login_required
def savings_contribution_delete(request, pk):
	if request.method == "POST":
		contribution = get_object_or_404(GoalContribution, pk=pk, goal__owner=request.user)
		contribution.delete()
		messages.success(request, "Внесок видалено; прогрес цілі оновлено.")
	return redirect("savings_goal_list")
