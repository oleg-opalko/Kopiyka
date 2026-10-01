from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q


class Category(models.Model):
	class Kind(models.TextChoices):
		EXPENSE = "expense", "Витрата"
		INCOME = "income", "Дохід"

	owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="finance_categories")
	name = models.CharField(max_length=48)
	kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.EXPENSE)
	color = models.CharField(
		max_length=7,
		default="#258B72",
		validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Вкажіть колір у форматі #RRGGBB.")],
	)

	class Meta:
		ordering = ["name"]
		constraints = [
			models.UniqueConstraint(fields=["owner", "name", "kind"], name="unique_category_per_owner_kind"),
		]

	def __str__(self):
		return self.name


class Account(models.Model):
	class Kind(models.TextChoices):
		CASH = "cash", "Готівка"
		CARD = "card", "Банківська картка"
		SAVINGS = "savings", "Заощадження"
		OTHER = "other", "Інше"

	owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="finance_accounts")
	name = models.CharField(max_length=48)
	kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.CASH)
	initial_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
	color = models.CharField(
		max_length=7,
		default="#258B72",
		validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Вкажіть колір у форматі #RRGGBB.")],
	)
	is_archived = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["is_archived", "name"]
		constraints = [models.UniqueConstraint(fields=["owner", "name"], name="unique_account_name_per_owner")]

	def __str__(self):
		return self.name


class Transaction(models.Model):
	class Kind(models.TextChoices):
		EXPENSE = "expense", "Витрата"
		INCOME = "income", "Дохід"
		TRANSFER = "transfer", "Переказ"

	owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="finance_transactions")
	category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions")
	account = models.ForeignKey(
		Account,
		null=True,
		blank=True,
		on_delete=models.PROTECT,
		related_name="transactions",
	)
	transfer_to = models.ForeignKey(
		Account,
		null=True,
		blank=True,
		on_delete=models.PROTECT,
		related_name="incoming_transfers",
	)
	kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.EXPENSE)
	amount = models.DecimalField(
		max_digits=12,
		decimal_places=2,
		validators=[MinValueValidator(Decimal("0.01"))],
	)
	occurred_on = models.DateField(db_index=True)
	note = models.CharField(max_length=160, blank=True)
	recurring_payment = models.ForeignKey(
		"RecurringPayment",
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="transactions",
	)
	scheduled_for = models.DateField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-occurred_on", "-created_at"]
		indexes = [models.Index(fields=["owner", "kind", "occurred_on"])]
		constraints = [
			models.CheckConstraint(
				condition=(
					Q(
						kind="transfer",
						category__isnull=True,
						account__isnull=False,
						transfer_to__isnull=False,
					)
					& ~Q(account=F("transfer_to"))
					| Q(
						kind__in=["expense", "income"],
						category__isnull=False,
						transfer_to__isnull=True,
					)
				),
				name="transaction_transfer_fields_match_kind",
			),
			models.UniqueConstraint(
				fields=["recurring_payment", "scheduled_for"],
				condition=Q(recurring_payment__isnull=False, scheduled_for__isnull=False),
				name="unique_recurring_transaction_date",
			),
		]

	def clean(self):
		super().clean()
		if self.kind == self.Kind.TRANSFER:
			if self.category_id or not self.account_id or not self.transfer_to_id:
				raise ValidationError("Переказ має містити два рахунки й не може мати категорію.")
			if self.account_id == self.transfer_to_id:
				raise ValidationError({"transfer_to": "Рахунок отримувача має відрізнятися від рахунку відправника."})
		else:
			if not self.category_id or self.transfer_to_id:
				raise ValidationError("Дохід або витрата мають містити категорію, але не рахунок отримувача.")
			if (self.owner_id and self.category.owner_id != self.owner_id) or self.category.kind != self.kind:
				raise ValidationError({"category": "Оберіть власну категорію відповідного типу."})
		if self.owner_id and self.account_id and self.account.owner_id != self.owner_id:
			raise ValidationError({"account": "Оберіть власний рахунок."})
		if self.owner_id and self.transfer_to_id and self.transfer_to.owner_id != self.owner_id:
			raise ValidationError({"transfer_to": "Оберіть власний рахунок."})

	def __str__(self):
		return f"{self.get_kind_display()}: {self.amount}"


class MonthlyBudget(models.Model):
	owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="monthly_budgets")
	month = models.DateField(help_text="Перший день місяця")
	amount = models.DecimalField(
		max_digits=12,
		decimal_places=2,
		validators=[MinValueValidator(Decimal("0.01"))],
	)

	class Meta:
		ordering = ["-month"]
		constraints = [models.UniqueConstraint(fields=["owner", "month"], name="unique_monthly_budget_per_owner")]

	def clean(self):
		super().clean()
		if self.month and self.month.day != 1:
			raise ValidationError({"month": "Бюджет має починатися з першого числа місяця."})

	def __str__(self):
		return f"{self.owner}: {self.month:%Y-%m}"


class CategoryBudget(models.Model):
	owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="category_budgets")
	category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="budgets")
	month = models.DateField(help_text="Перший день місяця")
	amount = models.DecimalField(
		max_digits=12,
		decimal_places=2,
		validators=[MinValueValidator(Decimal("0.01"))],
	)

	class Meta:
		ordering = ["category__name"]
		constraints = [
			models.UniqueConstraint(fields=["owner", "category", "month"], name="unique_category_budget_per_month"),
		]

	def clean(self):
		super().clean()
		if self.month and self.month.day != 1:
			raise ValidationError({"month": "Бюджет має починатися з першого числа місяця."})
		if self.category_id and self.owner_id and self.category.owner_id != self.owner_id:
			raise ValidationError({"category": "Оберіть власну категорію."})
		if self.category_id and self.category.kind != Category.Kind.EXPENSE:
			raise ValidationError({"category": "Ліміт можна встановити лише для витрат."})

	def __str__(self):
		return f"{self.category}: {self.amount}"


class BudgetAlert(models.Model):
	class Threshold(models.IntegerChoices):
		NEAR_LIMIT = 80, "Наближається до ліміту"
		OVER_LIMIT = 100, "Ліміт досягнуто або перевищено"

	owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budget_alerts")
	category_budget = models.ForeignKey(CategoryBudget, on_delete=models.CASCADE, related_name="alerts")
	threshold = models.PositiveSmallIntegerField(choices=Threshold.choices)
	is_read = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["is_read", "-created_at"]
		constraints = [
			models.UniqueConstraint(
				fields=["category_budget", "threshold"],
				name="unique_budget_alert_threshold",
			),
			models.CheckConstraint(
				condition=Q(threshold__in=[80, 100]),
				name="budget_alert_threshold_is_supported",
			),
		]

	def clean(self):
		super().clean()
		if self.category_budget_id and self.owner_id:
			if self.category_budget.owner_id != self.owner_id:
				raise ValidationError({"category_budget": "Оберіть власний категорійний бюджет."})

	def __str__(self):
		return f"{self.category_budget.category}: {self.threshold}%"


class RecurringPayment(models.Model):
	class Frequency(models.TextChoices):
		WEEKLY = "weekly", "Щотижня"
		MONTHLY = "monthly", "Щомісяця"

	owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recurring_payments")
	category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="recurring_payments")
	account = models.ForeignKey(
		Account,
		null=True,
		blank=True,
		on_delete=models.PROTECT,
		related_name="recurring_payments",
	)
	kind = models.CharField(
		max_length=8,
		choices=[(Transaction.Kind.EXPENSE, "Витрата"), (Transaction.Kind.INCOME, "Дохід")],
		default=Transaction.Kind.EXPENSE,
	)
	amount = models.DecimalField(
		max_digits=12,
		decimal_places=2,
		validators=[MinValueValidator(Decimal("0.01"))],
	)
	frequency = models.CharField(max_length=8, choices=Frequency.choices, default=Frequency.MONTHLY)
	next_due_on = models.DateField(db_index=True)
	monthly_day = models.PositiveSmallIntegerField(default=1)
	note = models.CharField(max_length=160, blank=True)
	is_active = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["is_active", "next_due_on", "category__name"]

	def clean(self):
		super().clean()
		if self.category_id and self.owner_id:
			if self.category.owner_id != self.owner_id or self.category.kind != self.kind:
				raise ValidationError({"category": "Оберіть власну категорію відповідного типу."})
		if self.account_id and self.owner_id and self.account.owner_id != self.owner_id:
			raise ValidationError({"account": "Оберіть власний рахунок."})
		if self.monthly_day < 1 or self.monthly_day > 31:
			raise ValidationError({"monthly_day": "День місяця має бути від 1 до 31."})

	def __str__(self):
		return f"{self.category}: {self.amount} ({self.get_frequency_display()})"


class SavingsGoal(models.Model):
	owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="savings_goals")
	name = models.CharField(max_length=80)
	target_amount = models.DecimalField(
		max_digits=12,
		decimal_places=2,
		validators=[MinValueValidator(Decimal("0.01"))],
	)
	target_date = models.DateField(null=True, blank=True)
	note = models.CharField(max_length=160, blank=True)
	is_archived = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["is_archived", "target_date", "name"]

	def __str__(self):
		return self.name


class GoalContribution(models.Model):
	goal = models.ForeignKey(SavingsGoal, on_delete=models.CASCADE, related_name="contributions")
	amount = models.DecimalField(
		max_digits=12,
		decimal_places=2,
		validators=[MinValueValidator(Decimal("0.01"))],
	)
	contributed_on = models.DateField(db_index=True)
	note = models.CharField(max_length=160, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-contributed_on", "-created_at"]

	def __str__(self):
		return f"{self.goal}: {self.amount}"
