from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from .models import Account, Category, RecurringPayment, SavingsGoal, Transaction


class RegisterForm(UserCreationForm):
    email = forms.EmailField(label="Електронна пошта", required=False)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")
        labels = {"username": "Ім'я користувача"}


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ("kind", "category", "account", "amount", "occurred_on", "note")
        labels = {
            "kind": "Тип операції",
            "category": "Категорія",
            "account": "Рахунок",
            "amount": "Сума, грн",
            "occurred_on": "Дата",
            "note": "Нотатка",
        }
        widgets = {
            "occurred_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "note": forms.TextInput(attrs={"placeholder": "Наприклад, продукти на тиждень"}),
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(owner=user)
        self.fields["kind"].choices = [
            choice for choice in self.fields["kind"].choices
            if choice[0] in (Transaction.Kind.EXPENSE, Transaction.Kind.INCOME)
        ]
        accounts = Account.objects.filter(owner=user, is_archived=False)
        if self.instance.pk and self.instance.account_id:
            accounts = Account.objects.filter(Q(owner=user) & (Q(is_archived=False) | Q(pk=self.instance.account_id)))
        self.fields["account"].queryset = accounts
        self.fields["account"].required = accounts.exists()
        if self.instance.pk and not self.instance.account_id:
            self.fields["account"].required = False
        self.fields["category"].label_from_instance = lambda category: (
            f"{category.get_kind_display()} · {category.name}"
        )
        self.fields["occurred_on"].initial = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        account = cleaned_data.get("account")
        kind = cleaned_data.get("kind")
        if category and category.owner_id != self.user.id:
            self.add_error("category", "Оберіть власну категорію.")
        elif category and kind and category.kind != kind:
            self.add_error("category", "Тип категорії має відповідати типу операції.")
        if account and account.owner_id != self.user.id:
            self.add_error("account", "Оберіть власний рахунок.")
        return cleaned_data


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ("name", "kind", "initial_balance", "color", "is_archived")
        labels = {
            "name": "Назва рахунку",
            "kind": "Тип рахунку",
            "initial_balance": "Початковий баланс, грн",
            "color": "Колір",
            "is_archived": "Архівувати рахунок",
        }
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class TransferForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ("account", "transfer_to", "amount", "occurred_on", "note")
        labels = {
            "account": "З рахунку",
            "transfer_to": "На рахунок",
            "amount": "Сума, грн",
            "occurred_on": "Дата",
            "note": "Нотатка",
        }
        widgets = {
            "occurred_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "note": forms.TextInput(attrs={"placeholder": "Наприклад, переказ на заощадження"}),
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.instance.kind = Transaction.Kind.TRANSFER
        self.instance.category = None
        accounts = Account.objects.filter(owner=user, is_archived=False)
        if self.instance.pk:
            accounts = Account.objects.filter(
                Q(owner=user) & (
                    Q(is_archived=False)
                    | Q(pk=self.instance.account_id)
                    | Q(pk=self.instance.transfer_to_id)
                )
            )
        self.fields["account"].queryset = accounts
        self.fields["transfer_to"].queryset = accounts
        self.fields["occurred_on"].initial = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        account = cleaned_data.get("account")
        transfer_to = cleaned_data.get("transfer_to")
        if account and transfer_to and account.pk == transfer_to.pk:
            self.add_error("transfer_to", "Оберіть інший рахунок для отримання коштів.")
        if account and account.owner_id != self.user.id:
            self.add_error("account", "Оберіть власний рахунок.")
        if transfer_to and transfer_to.owner_id != self.user.id:
            self.add_error("transfer_to", "Оберіть власний рахунок.")
        return cleaned_data

    def save(self, commit=True):
        transfer = super().save(commit=False)
        transfer.kind = Transaction.Kind.TRANSFER
        transfer.category = None
        if commit:
            transfer.save()
        return transfer


class RecurringPaymentForm(forms.ModelForm):
    class Meta:
        model = RecurringPayment
        fields = ("kind", "category", "account", "amount", "frequency", "next_due_on", "note", "is_active")
        labels = {
            "kind": "Тип операції",
            "category": "Категорія",
            "account": "Рахунок",
            "amount": "Сума, грн",
            "frequency": "Повторювати",
            "next_due_on": "Наступна дата",
            "note": "Нотатка",
            "is_active": "Активний графік",
        }
        widgets = {
            "next_due_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "note": forms.TextInput(attrs={"placeholder": "Наприклад, оренда квартири"}),
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(owner=user)
        accounts = Account.objects.filter(owner=user, is_archived=False)
        if self.instance.pk and self.instance.account_id:
            accounts = Account.objects.filter(Q(owner=user) & (Q(is_archived=False) | Q(pk=self.instance.account_id)))
        self.fields["account"].queryset = accounts
        self.fields["account"].required = accounts.exists()
        if self.instance.pk and not self.instance.account_id:
            self.fields["account"].required = False
        self.fields["category"].label_from_instance = lambda category: (
            f"{category.get_kind_display()} · {category.name}"
        )
        self.fields["next_due_on"].initial = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        kind = cleaned_data.get("kind")
        if category and category.owner_id != self.user.id:
            self.add_error("category", "Оберіть власну категорію.")
        elif category and kind and category.kind != kind:
            self.add_error("category", "Тип категорії має відповідати типу операції.")
        account = cleaned_data.get("account")
        if account and account.owner_id != self.user.id:
            self.add_error("account", "Оберіть власний рахунок.")
        return cleaned_data

    def save(self, commit=True):
        payment = super().save(commit=False)
        if payment.frequency == RecurringPayment.Frequency.MONTHLY:
            if (
                not payment.pk
                or self.initial.get("frequency") != payment.frequency
                or self.initial.get("next_due_on") != payment.next_due_on
            ):
                payment.monthly_day = payment.next_due_on.day
        if commit:
            payment.save()
        return payment


class TransactionFilterForm(forms.Form):
    search = forms.CharField(label="Пошук", required=False, max_length=100)
    kind = forms.ChoiceField(
        label="Тип",
        required=False,
        choices=[("", "Усі операції"), *Transaction.Kind.choices],
    )
    category = forms.ModelChoiceField(
        label="Категорія",
        queryset=Category.objects.none(),
        required=False,
    )
    date_from = forms.DateField(
        label="Від",
        required=False,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="До",
        required=False,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(owner=user)

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "Кінцева дата має бути не раніше початкової.")
        return cleaned_data


class ReportFilterForm(forms.Form):
    date_from = forms.DateField(
        label="Період від",
        required=True,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="Період до",
        required=True,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to:
            if date_from > date_to:
                self.add_error("date_to", "Кінцева дата має бути не раніше початкової.")
            elif (date_to.year - date_from.year) * 12 + date_to.month - date_from.month > 23:
                self.add_error("date_to", "Звіт можна побудувати максимум за 24 місяці.")
        return cleaned_data


class CSVUploadForm(forms.Form):
    file = forms.FileField(
        label="CSV-файл виписки",
        widget=forms.FileInput(attrs={"accept": ".csv,text/csv", "class": "file-input"}),
    )

    def clean_file(self):
        file = self.cleaned_data["file"]
        if not file.name.lower().endswith(".csv"):
            raise forms.ValidationError("Завантаж файл із розширенням .csv.")
        if file.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Максимальний розмір CSV-файлу — 2 МБ.")
        return file


class CSVMappingForm(forms.Form):
    date_column = forms.ChoiceField(label="Дата", required=True)
    amount_column = forms.ChoiceField(label="Сума", required=True)
    kind_column = forms.ChoiceField(label="Тип операції", required=False)
    category_column = forms.ChoiceField(label="Категорія", required=False)
    account_column = forms.ChoiceField(label="Рахунок", required=False)
    transfer_to_column = forms.ChoiceField(label="Рахунок отримувача", required=False)
    note_column = forms.ChoiceField(label="Нотатка", required=False)
    default_kind = forms.ChoiceField(
        label="Тип, якщо у файлі його немає",
        choices=[(Transaction.Kind.EXPENSE, "Витрата"), (Transaction.Kind.INCOME, "Дохід")],
    )
    default_account = forms.ModelChoiceField(
        label="Рахунок за замовчуванням",
        queryset=Account.objects.none(),
        required=False,
    )

    def __init__(self, *args, user, headers, **kwargs):
        super().__init__(*args, **kwargs)
        column_choices = [(str(index), f"{index + 1}. {header[:60] or '(порожній заголовок)'}") for index, header in enumerate(headers)]
        optional_choices = [("", "Не використовувати"), *column_choices]
        for field_name in ("date_column", "amount_column"):
            self.fields[field_name].choices = column_choices
        for field_name in ("kind_column", "category_column", "account_column", "transfer_to_column", "note_column"):
            self.fields[field_name].choices = optional_choices
        self.fields["default_account"].queryset = Account.objects.filter(owner=user, is_archived=False)

    def clean(self):
        cleaned_data = super().clean()
        accounts = self.fields["default_account"].queryset
        if accounts.exists() and not cleaned_data.get("account_column") and not cleaned_data.get("default_account"):
            self.add_error("default_account", "Обери рахунок або зістав колонку з рахунком у файлі.")
        return cleaned_data


class SavingsGoalForm(forms.ModelForm):
    class Meta:
        model = SavingsGoal
        fields = ("name", "target_amount", "target_date", "note", "is_archived")
        labels = {
            "name": "Назва цілі",
            "target_amount": "Потрібна сума, грн",
            "target_date": "Бажана дата",
            "note": "Нотатка",
            "is_archived": "Архівувати ціль",
        }
        widgets = {
            "target_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "note": forms.TextInput(attrs={"placeholder": "Наприклад, відпустка в Карпатах"}),
        }


class GoalContributionForm(forms.Form):
    amount = forms.DecimalField(
        label="Сума внеску, грн",
        min_value=0.01,
        max_digits=12,
        decimal_places=2,
    )
    contributed_on = forms.DateField(
        label="Дата внеску",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    note = forms.CharField(label="Нотатка", required=False, max_length=160)

    def __init__(self, *args, remaining, **kwargs):
        self.remaining = remaining
        super().__init__(*args, **kwargs)
        self.fields["contributed_on"].initial = timezone.localdate()
        self.fields["note"].widget = forms.TextInput(attrs={"placeholder": "Необов'язково"})

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount > self.remaining:
            raise forms.ValidationError("Внесок не може перевищувати суму, що залишилася до цілі.")
        return amount


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "kind", "color")
        labels = {"name": "Назва", "kind": "Тип", "color": "Колір"}
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class AmountForm(forms.Form):
    amount = forms.DecimalField(
        label="Місячний ліміт, грн",
        min_value=0.01,
        max_digits=12,
        decimal_places=2,
    )


class CategoryAmountForm(forms.Form):
    category = forms.ModelChoiceField(queryset=Category.objects.none(), label="Категорія")
    amount = forms.DecimalField(
        label="Ліміт, грн",
        min_value=0.01,
        max_digits=12,
        decimal_places=2,
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(owner=user, kind=Category.Kind.EXPENSE)