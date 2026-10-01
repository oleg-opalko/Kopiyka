import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from .models import Account, Category, Transaction


MAX_CSV_COLUMNS = 50
MAX_CSV_ROWS = 1000


def read_csv_upload(upload):
	content = upload.read()
	try:
		text = content.decode("utf-8-sig")
	except UnicodeDecodeError:
		try:
			text = content.decode("cp1251")
		except UnicodeDecodeError as error:
			raise ValidationError("Не вдалося прочитати файл. Збережи його у UTF-8 або Windows-1251.") from error
	if "\x00" in text:
		raise ValidationError("Файл містить некоректні символи.")

	sample = text[:4096]
	try:
		dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
	except csv.Error:
		dialect = csv.excel

	reader = csv.reader(io.StringIO(text, newline=""), dialect)
	try:
		headers = [header.strip() for header in next(reader)]
	except StopIteration as error:
		raise ValidationError("CSV-файл порожній.") from error
	if not headers or len(headers) > MAX_CSV_COLUMNS:
		raise ValidationError(f"У файлі має бути від 1 до {MAX_CSV_COLUMNS} колонок.")

	rows = []
	for line_number, row in enumerate(reader, start=2):
		if not any(cell.strip() for cell in row):
			continue
		if len(rows) >= MAX_CSV_ROWS:
			raise ValidationError(f"Максимум рядків для одного імпорту — {MAX_CSV_ROWS}.")
		row = row[:len(headers)] + [""] * max(0, len(headers) - len(row))
		rows.append({"line": line_number, "cells": [cell.strip()[:500] for cell in row]})
	if not rows:
		raise ValidationError("У файлі немає рядків з операціями.")
	return headers, rows


def parse_date(value):
	for date_format in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
		try:
			return datetime.strptime(value.strip(), date_format).date()
		except ValueError:
			continue
	raise ValueError("невідомий формат дати")


def parse_amount(value):
	cleaned = value.replace("\u00a0", " ").replace(" ", "").replace("₴", "").replace("грн", "")
	if not cleaned:
		raise ValueError("сума порожня")
	if "," in cleaned and "." in cleaned:
		if cleaned.rfind(",") > cleaned.rfind("."):
			cleaned = cleaned.replace(".", "").replace(",", ".")
		else:
			cleaned = cleaned.replace(",", "")
	elif "," in cleaned:
		cleaned = cleaned.replace(",", ".")
	try:
		amount = Decimal(cleaned)
	except InvalidOperation as error:
		raise ValueError("сума має бути числом") from error
	if not amount.is_finite() or amount == 0:
		raise ValueError("сума має бути ненульовим числом")
	try:
		return abs(amount).quantize(Decimal("0.01"))
	except InvalidOperation as error:
		raise ValueError("сума завелика") from error


def parse_kind(value, default_kind):
	value = value.strip().casefold()
	if not value:
		return default_kind
	if value in {"expense", "витрата", "витрати", "debit", "списання", "видаток", "покупка"}:
		return Transaction.Kind.EXPENSE
	if value in {"income", "дохід", "доходи", "credit", "зарахування", "поповнення"}:
		return Transaction.Kind.INCOME
	if value in {"transfer", "переказ", "перекази", "внутрішній переказ"}:
		return Transaction.Kind.TRANSFER
	raise ValueError("невідомий тип операції")


def row_fingerprint(row):
	return (
		row["kind"],
		row["occurred_on"],
		row["amount"],
		row["category_id"],
		row["account_id"],
		row["transfer_to_id"],
		row["note"].strip().casefold(),
	)


def existing_transaction_fingerprints(user, rows):
	valid_rows = [row for row in rows if row.get("occurred_on")]
	if not valid_rows:
		return set()
	start_date = min(row["occurred_on"] for row in valid_rows)
	end_date = max(row["occurred_on"] for row in valid_rows)
	transactions = Transaction.objects.filter(
		owner=user,
		occurred_on__range=(start_date, end_date),
	).values("kind", "occurred_on", "amount", "category_id", "account_id", "transfer_to_id", "note")
	return {
		(
			row["kind"], row["occurred_on"].isoformat(), str(row["amount"]), row["category_id"],
			row["account_id"], row["transfer_to_id"], row["note"].strip().casefold(),
		)
		for row in transactions
	}


def mapped_rows_preview(user, raw_rows, mapping):
	categories = {
		(category.kind, category.name.casefold()): category
		for category in Category.objects.filter(owner=user)
	}
	accounts = {
		account.name.casefold(): account
		for account in Account.objects.filter(owner=user, is_archived=False)
	}
	accounts_required = bool(accounts)
	default_account = mapping.get("default_account")
	default_account = accounts.get(default_account.name.casefold()) if default_account else None

	preview = []
	for raw_row in raw_rows:
		cells = raw_row["cells"]
		def cell(field_name):
			column = mapping.get(field_name)
			return cells[int(column)] if column not in (None, "") else ""

		errors = []
		try:
			occurred_on = parse_date(cell("date_column"))
		except ValueError as error:
			occurred_on = None
			errors.append(f"Дата: {error}.")
		try:
			amount = parse_amount(cell("amount_column"))
		except ValueError as error:
			amount = None
			errors.append(f"Сума: {error}.")
		try:
			kind = parse_kind(cell("kind_column"), mapping["default_kind"])
		except ValueError as error:
			kind = None
			errors.append(f"Тип: {error}.")

		category = None
		account = None
		transfer_to = None
		if kind == Transaction.Kind.TRANSFER:
			account_name = cell("account_column")
			account = accounts.get(account_name.casefold()) if account_name else default_account
			destination_name = cell("transfer_to_column")
			transfer_to = accounts.get(destination_name.casefold()) if destination_name else None
			if not account:
				errors.append("Не вдалося визначити власний рахунок відправника.")
			if not transfer_to:
				errors.append("Не вдалося визначити власний рахунок отримувача.")
			elif account and transfer_to.pk == account.pk:
				errors.append("Рахунок відправника й отримувача збігаються.")
		else:
			category_name = cell("category_column")
			if not category_name:
				category_name = "Інше" if kind == Transaction.Kind.EXPENSE else "Додатковий дохід"
			category = categories.get((kind, category_name.casefold()))
			if not category:
				errors.append(f"Не знайдено власну категорію «{category_name}» для цього типу.")
			account_name = cell("account_column")
			account = accounts.get(account_name.casefold()) if account_name else default_account
			if accounts_required and not account:
				errors.append("Не вдалося визначити рахунок. Зістав колонку або обери рахунок за замовчуванням.")

		preview.append(
			{
				"line": raw_row["line"],
				"occurred_on": occurred_on.isoformat() if occurred_on else "",
				"amount": str(amount) if amount is not None else "",
				"kind": kind or "",
				"kind_label": Transaction.Kind(kind).label if kind else "",
				"category_id": category.pk if category else None,
				"category_name": category.name if category else "Переказ",
				"account_id": account.pk if account else None,
				"account_name": account.name if account else "",
				"transfer_to_id": transfer_to.pk if transfer_to else None,
				"transfer_to_name": transfer_to.name if transfer_to else "",
				"note": cell("note_column")[:160],
				"errors": errors,
				"duplicate": False,
			}
		)

	valid_rows = [row for row in preview if not row["errors"]]
	seen = existing_transaction_fingerprints(user, valid_rows)
	for row in valid_rows:
		fingerprint = row_fingerprint(row)
		if fingerprint in seen:
			row["duplicate"] = True
		else:
			seen.add(fingerprint)

	return preview