from django.contrib import admin

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


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
	list_display = ("name", "kind", "owner")
	list_filter = ("kind",)
	search_fields = ("name", "owner__username")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
	list_display = ("occurred_on", "owner", "kind", "category", "amount")
	list_filter = ("kind", "occurred_on", "category")
	search_fields = ("note", "owner__username", "category__name")
	date_hierarchy = "occurred_on"


admin.site.register(MonthlyBudget)
admin.site.register(CategoryBudget)
admin.site.register(RecurringPayment)
admin.site.register(Account)
admin.site.register(SavingsGoal)
admin.site.register(GoalContribution)
admin.site.register(BudgetAlert)
