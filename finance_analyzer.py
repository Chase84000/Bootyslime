#!/usr/bin/env python3
"""
Analyze personal finance CSV exports.

Expected columns (case-insensitive):
- date
- description
- amount

Optional columns:
- category
- account
- balance

Positive amounts are treated as income, negative amounts as expenses.

Example:
    python finance_analyzer.py transactions.csv
    python finance_analyzer.py transactions.csv --top 10 --month 2026-06
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Iterable, Optional


DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
)


@dataclass(frozen=True)
class Transaction:
    date: datetime
    description: str
    amount: float
    category: str = "Uncategorized"
    account: str = "Unknown"
    balance: Optional[float] = None


def normalize_header(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def parse_date(value: str) -> datetime:
    text = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError(f"Unsupported date format: {value!r}")


def parse_amount(value: str) -> float:
    text = value.strip().replace("$", "").replace(",", "")
    if not text:
        raise ValueError("Empty amount")
    return float(text)


def parse_optional_float(value: str) -> Optional[float]:
    text = value.strip().replace("$", "").replace(",", "")
    if not text:
        return None
    return float(text)


def load_transactions(path: Path) -> list[Transaction]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV file has no header row")

        headers = {normalize_header(h): h for h in reader.fieldnames}
        required = ["date", "description", "amount"]
        missing = [name for name in required if name not in headers]
        if missing:
            raise ValueError(
                "Missing required columns: "
                + ", ".join(missing)
                + ". Expected at least date, description, amount."
            )

        transactions: list[Transaction] = []
        for row_num, row in enumerate(reader, start=2):
            try:
                date = parse_date(row[headers["date"]])
                description = row[headers["description"]].strip()
                amount = parse_amount(row[headers["amount"]])
                category = row.get(headers.get("category", ""), "").strip() or "Uncategorized"
                account = row.get(headers.get("account", ""), "").strip() or "Unknown"
                balance = None
                if "balance" in headers:
                    balance = parse_optional_float(row[headers["balance"]])

                transactions.append(
                    Transaction(
                        date=date,
                        description=description,
                        amount=amount,
                        category=category,
                        account=account,
                        balance=balance,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Row {row_num}: {exc}") from exc

    return transactions


def month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def fmt_money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def print_kv(label: str, value: str) -> None:
    print(f"{label:<24} {value}")


def summarize(transactions: list[Transaction], top_n: int, month_filter: Optional[str]) -> None:
    if month_filter:
        try:
            year_s, month_s = month_filter.split("-", 1)
            year = int(year_s)
            month = int(month_s)
        except ValueError as exc:
            raise ValueError("--month must be in YYYY-MM format") from exc

        transactions = [
            tx for tx in transactions if tx.date.year == year and tx.date.month == month
        ]

    if not transactions:
        print("No transactions matched the selected filters.")
        return

    transactions = sorted(transactions, key=lambda tx: tx.date)

    income = sum(tx.amount for tx in transactions if tx.amount > 0)
    expenses = sum(-tx.amount for tx in transactions if tx.amount < 0)
    net = income - expenses
    avg_tx = mean(abs(tx.amount) for tx in transactions)
    months = sorted({month_key(tx.date) for tx in transactions})
    monthly_net: dict[str, float] = defaultdict(float)
    monthly_expenses: dict[str, float] = defaultdict(float)
    category_totals: Counter[str] = Counter()
    merchant_totals: Counter[str] = Counter()
    account_totals: Counter[str] = Counter()

    for tx in transactions:
        key = month_key(tx.date)
        monthly_net[key] += tx.amount
        if tx.amount < 0:
            monthly_expenses[key] += -tx.amount
            category_totals[tx.category] += -tx.amount
            merchant_totals[tx.description or "Unknown"] += -tx.amount
        else:
            category_totals[f"Income: {tx.category}"] += tx.amount
        account_totals[tx.account] += abs(tx.amount)

    print_section("Overview")
    print_kv("Transactions", str(len(transactions)))
    print_kv("Income", fmt_money(income))
    print_kv("Expenses", fmt_money(expenses))
    print_kv("Net", fmt_money(net))
    print_kv("Average transaction", fmt_money(avg_tx))
    print_kv("Months covered", f"{months[0]} to {months[-1]}")

    print_section("Monthly Cash Flow")
    for month in months:
        print(
            f"{month:<8} net {fmt_money(monthly_net[month]):>12}  "
            f"expenses {fmt_money(monthly_expenses.get(month, 0.0)):>12}"
        )

    print_section("Top Spending Categories")
    if category_totals:
        for name, total in category_totals.most_common(top_n):
            print(f"{name:<30} {fmt_money(total)}")
    else:
        print("No categories available.")

    print_section("Top Merchants / Descriptions")
    if merchant_totals:
        for name, total in merchant_totals.most_common(top_n):
            print(f"{name:<30} {fmt_money(total)}")
    else:
        print("No expense merchants available.")

    print_section("Accounts")
    for name, total in account_totals.most_common():
        print(f"{name:<30} {fmt_money(total)}")

    balances = [tx.balance for tx in transactions if tx.balance is not None]
    if balances:
        print_section("Balance Snapshot")
        print_kv("Latest balance", fmt_money(balances[-1]))
        print_kv("Min balance", fmt_money(min(balances)))
        print_kv("Max balance", fmt_money(max(balances)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze a personal finance CSV export."
    )
    parser.add_argument("csv_file", type=Path, help="Path to the transaction CSV file")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="How many categories and merchants to show",
    )
    parser.add_argument(
        "--month",
        type=str,
        default=None,
        help="Limit analysis to a single month in YYYY-MM format",
    )
    return parser


def main(argv: Iterable[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv))

    if not args.csv_file.exists():
        print(f"File not found: {args.csv_file}", file=sys.stderr)
        return 1

    try:
        transactions = load_transactions(args.csv_file)
        summarize(transactions, args.top, args.month)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
