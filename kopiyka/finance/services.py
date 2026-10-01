import calendar
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Sum

from .models import RecurringPayment, Transaction


def account_balance_map(accounts, as_of_date=None):
	accounts = list(accounts)
	account_ids = [account.pk for account in accounts]
	balances = {account.pk: account.initial_balance for account in accounts}
	if not account_ids:
		return balances

	ordinary_transactions = Transaction.objects.filter(
		account_id__in=account_ids,
		kind__in=[Transaction.Kind.INCOME, Transaction.Kind.EXPENSE],
	)
	if as_of_date:
		ordinary_transactions = ordinary_transactions.filter(occurred_on__lte=as_of_date)
	ordinary_totals = ordinary_transactions.values("account_id", "kind").annotate(total=Sum("amount"))
	for row in ordinary_totals:
		sign = 1 if row["kind"] == Transaction.Kind.INCOME else -1
		balances[row["account_id"]] += row["total"] * sign

	outgoing_transactions = Transaction.objects.filter(
		account_id__in=account_ids,
		kind=Transaction.Kind.TRANSFER,
	)
	if as_of_date:
		outgoing_transactions = outgoing_transactions.filter(occurred_on__lte=as_of_date)
	outgoing_totals = outgoing_transactions.values("account_id").annotate(total=Sum("amount"))
	for row in outgoing_totals:
		balances[row["account_id"]] -= row["total"]

	incoming_transactions = Transaction.objects.filter(
		transfer_to_id__in=account_ids,
		kind=Transaction.Kind.TRANSFER,
	)
	if as_of_date:
		incoming_transactions = incoming_transactions.filter(occurred_on__lte=as_of_date)
	incoming_totals = incoming_transactions.values("transfer_to_id").annotate(total=Sum("amount"))
	for row in incoming_totals:
		balances[row["transfer_to_id"]] += row["total"]

	return balances


def next_due_date(payment, current_due_date):
	if payment.frequency == RecurringPayment.Frequency.WEEKLY:
		return current_due_date + timedelta(days=7)

	month_index = current_due_date.year * 12 + current_due_date.month
	year, month = divmod(month_index, 12)
	month += 1
	day = min(payment.monthly_day, calendar.monthrange(year, month)[1])
	return date(year, month, day)


@transaction.atomic
def generate_due_transactions(payment_id, owner, today):
	payment = RecurringPayment.objects.select_for_update().get(pk=payment_id, owner=owner)
	if not payment.is_active:
		return 0

	created_count = 0
	while payment.next_due_on <= today:
		due_date = payment.next_due_on
		_, created = Transaction.objects.get_or_create(
			recurring_payment=payment,
			scheduled_for=due_date,
			defaults={
				"owner": owner,
				"category": payment.category,
				"account": payment.account,
				"kind": payment.kind,
				"amount": payment.amount,
				"occurred_on": due_date,
				"note": payment.note,
			},
		)
		created_count += int(created)
		payment.next_due_on = next_due_date(payment, due_date)

	payment.save(update_fields=["next_due_on"])
	return created_count