#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

PYTHON_HOME = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312"
TCL_DIR = PYTHON_HOME / "tcl"
os.environ.setdefault("TCL_LIBRARY", str(TCL_DIR / "tcl8.6"))
os.environ.setdefault("TK_LIBRARY", str(TCL_DIR / "tk8.6"))

import customtkinter as ctk

from finance_analyzer import Transaction, fmt_money, load_transactions, month_key


APP_TITLE = "Finance Lens"
ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = ROOT / "robinhood-cache.json"
SETTINGS_FILE = Path.home() / ".finance_lens_settings.json"

GREEN_BG = "#06150f"
GREEN_PANEL = "#0b1f16"
GREEN_CARD = "#10271c"
GREEN_CARD_2 = "#143224"
GREEN_LINE = "#274536"
GOLD = "#d6b15a"
GOLD_SOFT = "#ecd99a"
TEXT = "#f5f1e8"
MUTED = "#b2bcad"
SUBTLE = "#7f8f82"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return load_json(SETTINGS_FILE)
        except Exception:
            return {}
    return {}


def save_settings(settings: dict) -> None:
    try:
        save_json(SETTINGS_FILE, settings)
    except Exception:
        pass


def friendly_name(account: dict[str, object]) -> str:
    return str(account.get("nickname") or account.get("brokerage_account_type") or "Account")


def mask_account(account_number: str) -> str:
    return account_number if len(account_number) <= 4 else f"••••{account_number[-4:]}"


def fmt_date(value: str | None) -> str:
    if not value:
        return "Unknown"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%b %d, %Y")
    except Exception:
        return value


def format_money(value: float) -> str:
    return fmt_money(value)


def summary_for(transactions: list[Transaction]) -> dict:
    if not transactions:
        return {
            "count": 0,
            "income": 0.0,
            "expenses": 0.0,
            "net": 0.0,
            "avg": 0.0,
            "months": [],
            "monthly": [],
            "categories": [],
            "merchants": [],
            "recurring": [],
        }

    txs = sorted(transactions, key=lambda tx: tx.date)
    count = len(txs)
    income = sum(tx.amount for tx in txs if tx.amount > 0)
    expenses = sum(-tx.amount for tx in txs if tx.amount < 0)
    net = income - expenses
    avg = sum(abs(tx.amount) for tx in txs) / count
    months = sorted({month_key(tx.date) for tx in txs})

    monthly_net: dict[str, float] = defaultdict(float)
    category_totals: Counter[str] = Counter()
    merchant_totals: Counter[str] = Counter()
    recurring_groups: dict[tuple[str, float], list[Transaction]] = defaultdict(list)

    for tx in txs:
        monthly_net[month_key(tx.date)] += tx.amount
        if tx.amount < 0:
            category_totals[tx.category] += -tx.amount
            merchant_totals[tx.description or "Unknown"] += -tx.amount
        else:
            category_totals[f"Income: {tx.category}"] += tx.amount
        recurring_groups[(tx.description.strip().lower(), round(abs(tx.amount), 2))].append(tx)

    recurring = []
    for group in recurring_groups.values():
        if len(group) < 3:
            continue
        group = sorted(group, key=lambda tx: tx.date)
        gaps = [(group[i].date - group[i - 1].date).days for i in range(1, len(group))]
        avg_gap = sum(gaps) / len(gaps)
        if 18 <= avg_gap <= 45:
            recurring.append(
                {
                    "description": group[0].description,
                    "count": len(group),
                    "amount": group[0].amount,
                    "cadence": "biweekly" if avg_gap < 27 else "monthly",
                    "last_date": group[-1].date.strftime("%b %d, %Y"),
                }
            )

    recurring.sort(key=lambda item: (-item["count"], -abs(item["amount"])))

    return {
        "count": count,
        "income": income,
        "expenses": expenses,
        "net": net,
        "avg": avg,
        "months": months,
        "monthly": [(m, monthly_net[m]) for m in months],
        "categories": category_totals.most_common(),
        "merchants": merchant_totals.most_common(),
        "recurring": recurring,
    }


class FinanceLensApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1500x950")
        self.minsize(1280, 820)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.configure(fg_color=GREEN_BG)

        self.settings = load_settings()
        self.transactions: list[Transaction] = []
        self.filtered: list[Transaction] = []
        self.snapshot: dict | None = None

        self.month_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")
        self.top_var = tk.IntVar(value=8)
        self.status_var = tk.StringVar(value="Ready")
        self.bridge_var = tk.StringVar(value="No Robinhood snapshot loaded")
        self.cache_var = tk.StringVar(value=str(DEFAULT_CACHE))

        self._build_shell()
        self._load_startup_state()

    def _build_shell(self) -> None:
        self.sidebar = ctk.CTkFrame(self, width=270, corner_radius=28, fg_color=GREEN_PANEL)
        self.sidebar.pack(side="left", fill="y", padx=18, pady=18)
        self.sidebar.pack_propagate(False)

        self.main = ctk.CTkFrame(self, corner_radius=32, fg_color=GREEN_BG)
        self.main.pack(side="right", fill="both", expand=True, padx=(0, 18), pady=18)

        self._build_sidebar()
        self._build_header()
        self._build_views()

    def _build_sidebar(self) -> None:
        brand = ctk.CTkFrame(self.sidebar, corner_radius=22, fg_color=GREEN_CARD)
        brand.pack(fill="x", padx=16, pady=(16, 12))
        ctk.CTkLabel(brand, text="Finance Lens", font=ctk.CTkFont("Segoe UI", 24, "bold"), text_color=TEXT).pack(anchor="w", padx=16, pady=(16, 2))
        ctk.CTkLabel(brand, text="Clean money view", font=ctk.CTkFont("Segoe UI", 12), text_color=MUTED).pack(anchor="w", padx=16, pady=(0, 16))

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        nav = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav.pack(fill="x", padx=10, pady=6)
        for key, label in [
            ("dashboard", "Dashboard"),
            ("activity", "Activity"),
            ("holdings", "Holdings"),
            ("settings", "Settings"),
        ]:
            btn = ctk.CTkButton(
                nav,
                text=label,
                height=44,
                corner_radius=18,
                fg_color=GREEN_CARD,
                hover_color=GREEN_CARD_2,
                text_color=TEXT,
                font=ctk.CTkFont("Segoe UI", 13, "medium"),
                anchor="w",
                command=lambda k=key: self.show_view(k),
            )
            btn.pack(fill="x", pady=6)
            self.nav_buttons[key] = btn

        self.sidebar_stat = ctk.CTkFrame(self.sidebar, corner_radius=22, fg_color=GREEN_CARD)
        self.sidebar_stat.pack(fill="both", expand=True, padx=16, pady=(16, 16))
        ctk.CTkLabel(self.sidebar_stat, text="Snapshot", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=GOLD).pack(anchor="w", padx=16, pady=(16, 6))
        self.sidebar_status = ctk.CTkLabel(self.sidebar_stat, textvariable=self.status_var, justify="left", text_color=TEXT, wraplength=220, font=ctk.CTkFont("Segoe UI", 12))
        self.sidebar_status.pack(anchor="w", padx=16, pady=(0, 16))
        ctk.CTkLabel(self.sidebar_stat, textvariable=self.bridge_var, justify="left", text_color=MUTED, wraplength=220, font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=16, pady=(0, 16))

    def _build_header(self) -> None:
        self.header = ctk.CTkFrame(self.main, corner_radius=30, fg_color=GREEN_PANEL)
        self.header.pack(fill="x", padx=0, pady=(0, 14))

        header_left = ctk.CTkFrame(self.header, fg_color="transparent")
        header_left.pack(side="left", fill="x", expand=True, padx=26, pady=24)
        ctk.CTkLabel(header_left, text="Overview", font=ctk.CTkFont("Segoe UI", 28, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(header_left, text="Cash flow, holdings, and account snapshots in one place.", font=ctk.CTkFont("Segoe UI", 13), text_color=MUTED).pack(anchor="w", pady=(6, 0))

        header_right = ctk.CTkFrame(self.header, fg_color="transparent")
        header_right.pack(side="right", padx=24, pady=22)
        self.btn_import = ctk.CTkButton(header_right, text="Import CSV", corner_radius=16, fg_color=GOLD, hover_color=GOLD_SOFT, text_color="#0d160f", command=self.open_csv, width=118, height=40)
        self.btn_import.pack(side="left", padx=6)
        ctk.CTkButton(header_right, text="Folder", corner_radius=16, fg_color=GREEN_CARD, hover_color=GREEN_CARD_2, text_color=TEXT, command=self.open_folder, width=92, height=40).pack(side="left", padx=6)
        ctk.CTkButton(header_right, text="Reload", corner_radius=16, fg_color=GREEN_CARD, hover_color=GREEN_CARD_2, text_color=TEXT, command=self.load_snapshot, width=92, height=40).pack(side="left", padx=6)

    def _build_views(self) -> None:
        self.view_host = ctk.CTkFrame(self.main, fg_color="transparent")
        self.view_host.pack(fill="both", expand=True)

        self.views: dict[str, ctk.CTkFrame] = {}
        self._build_dashboard_view()
        self._build_activity_view()
        self._build_holdings_view()
        self._build_settings_view()

        self.show_view("dashboard")

    def _build_dashboard_view(self) -> None:
        view = ctk.CTkFrame(self.view_host, fg_color="transparent")
        self.views["dashboard"] = view

        cards = ctk.CTkFrame(view, fg_color="transparent")
        cards.pack(fill="x")
        self.metric_cards = {
            "transactions": self._metric_card(cards, 0, 0, "Transactions", "0", "Rows loaded"),
            "income": self._metric_card(cards, 0, 1, "Income", "$0.00", "Positive cash flow"),
            "expenses": self._metric_card(cards, 0, 2, "Expenses", "$0.00", "Spending total"),
            "net": self._metric_card(cards, 0, 3, "Net", "$0.00", "Income minus spending"),
        }
        for idx in range(4):
            cards.grid_columnconfigure(idx, weight=1)

        lower = ctk.CTkFrame(view, fg_color="transparent")
        lower.pack(fill="both", expand=True, pady=(16, 0))
        self.flow_card = self._panel(lower, "Cash Flow", 0, 0)
        self.top_card = self._panel(lower, "What You Spend On", 0, 1)
        self.flow_chart = tk.Canvas(self.flow_card, bg=GREEN_CARD, highlightthickness=0)
        self.flow_chart.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.top_chart = tk.Canvas(self.top_card, bg=GREEN_CARD, highlightthickness=0)
        self.top_chart.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self._bind_panel_resize(self.flow_card, self.render_dashboard_charts)
        self._bind_panel_resize(self.top_card, self.render_dashboard_charts)

    def _build_activity_view(self) -> None:
        view = ctk.CTkFrame(self.view_host, fg_color="transparent")
        self.views["activity"] = view

        controls = ctk.CTkFrame(view, corner_radius=26, fg_color=GREEN_PANEL)
        controls.pack(fill="x", pady=(0, 14))
        controls_inner = ctk.CTkFrame(controls, fg_color="transparent")
        controls_inner.pack(fill="x", padx=18, pady=16)

        ctk.CTkLabel(controls_inner, text="Activity", font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=TEXT).pack(side="left", padx=(0, 16))
        ctk.CTkLabel(controls_inner, text="Month", text_color=MUTED).pack(side="left")
        self.month_menu = ctk.CTkOptionMenu(controls_inner, values=[""], variable=self.month_var, fg_color=GREEN_CARD, button_color=GREEN_CARD_2, button_hover_color=GREEN_LINE, dropdown_fg_color=GREEN_PANEL, dropdown_text_color=TEXT, text_color=TEXT, width=140, command=lambda _v: self.apply_filters())
        self.month_menu.pack(side="left", padx=(8, 16))
        ctk.CTkLabel(controls_inner, text="Search", text_color=MUTED).pack(side="left")
        self.search_entry = ctk.CTkEntry(controls_inner, textvariable=self.search_var, fg_color=GREEN_CARD, border_color=GREEN_LINE, text_color=TEXT, placeholder_text="merchant, category, account", width=280)
        self.search_entry.pack(side="left", padx=(8, 16))
        ctk.CTkLabel(controls_inner, text="Top", text_color=MUTED).pack(side="left")
        self.top_slider = ctk.CTkSlider(controls_inner, from_=3, to=25, number_of_steps=22, command=self._on_top_change, width=180, progress_color=GOLD, button_color=GOLD)
        self.top_slider.set(self.top_var.get())
        self.top_slider.pack(side="left", padx=(8, 12))
        self.top_value = ctk.CTkLabel(controls_inner, text="8", text_color=TEXT)
        self.top_value.pack(side="left")
        self.search_entry.bind("<KeyRelease>", lambda _e: self.apply_filters())

        body = ctk.CTkFrame(view, fg_color=GREEN_PANEL, corner_radius=26)
        body.pack(fill="both", expand=True)
        self.tx_tree = ttk.Treeview(body, columns=("date", "description", "amount", "category", "account"), show="headings")
        for col, width, anchor in [
            ("date", 120, "w"),
            ("description", 350, "w"),
            ("amount", 120, "e"),
            ("category", 180, "w"),
            ("account", 140, "w"),
        ]:
            self.tx_tree.heading(col, text=col.title())
            self.tx_tree.column(col, width=width, anchor=anchor)
        style = ttk.Style()
        style.configure("Treeview", rowheight=28, background=GREEN_PANEL, fieldbackground=GREEN_PANEL, foreground=TEXT, borderwidth=0)
        style.configure("Treeview.Heading", background=GREEN_CARD_2, foreground=GOLD_SOFT, font=("Segoe UI", 10, "bold"))
        self.tx_tree.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=16)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tx_tree.yview)
        scrollbar.pack(side="right", fill="y", padx=16, pady=16)
        self.tx_tree.configure(yscrollcommand=scrollbar.set)

    def _build_holdings_view(self) -> None:
        view = ctk.CTkFrame(self.view_host, fg_color="transparent")
        self.views["holdings"] = view

        top = ctk.CTkFrame(view, corner_radius=26, fg_color=GREEN_PANEL)
        top.pack(fill="x", pady=(0, 14))
        header = ctk.CTkFrame(top, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=16)
        ctk.CTkLabel(header, text="Holdings", font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkLabel(header, textvariable=self.bridge_var, text_color=MUTED).pack(side="right")

        controls = ctk.CTkFrame(top, fg_color="transparent")
        controls.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkLabel(controls, text="Cache", text_color=MUTED).pack(side="left")
        self.cache_entry = ctk.CTkEntry(controls, textvariable=self.cache_var, fg_color=GREEN_CARD, border_color=GREEN_LINE, text_color=TEXT, width=420)
        self.cache_entry.pack(side="left", padx=10, fill="x", expand=True)
        ctk.CTkButton(controls, text="Browse", corner_radius=16, fg_color=GREEN_CARD, hover_color=GREEN_CARD_2, text_color=TEXT, command=self.browse_cache, width=92).pack(side="left", padx=6)
        ctk.CTkButton(controls, text="Reload", corner_radius=16, fg_color=GOLD, hover_color=GOLD_SOFT, text_color="#0d160f", command=self.load_snapshot, width=92).pack(side="left", padx=6)

        split = ctk.CTkFrame(view, fg_color="transparent")
        split.pack(fill="both", expand=True)
        self.accounts_card = self._panel(split, "Accounts", 0, 0)
        self.equities_card = self._panel(split, "Equities", 0, 1)
        self.options_card = self._panel(split, "Options", 0, 2)
        self.accounts_box = ctk.CTkScrollableFrame(self.accounts_card, fg_color=GREEN_CARD, corner_radius=18)
        self.accounts_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.equities_box = ctk.CTkScrollableFrame(self.equities_card, fg_color=GREEN_CARD, corner_radius=18)
        self.equities_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.options_box = ctk.CTkScrollableFrame(self.options_card, fg_color=GREEN_CARD, corner_radius=18)
        self.options_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def _build_settings_view(self) -> None:
        view = ctk.CTkFrame(self.view_host, fg_color="transparent")
        self.views["settings"] = view
        card = ctk.CTkFrame(view, corner_radius=26, fg_color=GREEN_PANEL)
        card.pack(fill="both", expand=True)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=24, pady=24)
        ctk.CTkLabel(inner, text="Settings", font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(inner, text="The app remembers your last CSV and cache paths locally.", text_color=MUTED).pack(anchor="w", pady=(6, 18))
        ctk.CTkButton(inner, text="Open last CSV folder", corner_radius=16, fg_color=GREEN_CARD, hover_color=GREEN_CARD_2, text_color=TEXT, command=self.open_last_folder).pack(anchor="w", pady=6)
        ctk.CTkButton(inner, text="Open cache file", corner_radius=16, fg_color=GREEN_CARD, hover_color=GREEN_CARD_2, text_color=TEXT, command=self.browse_cache).pack(anchor="w", pady=6)
        ctk.CTkButton(inner, text="Import sample CSV", corner_radius=16, fg_color=GOLD, hover_color=GOLD_SOFT, text_color="#0d160f", command=self.load_sample).pack(anchor="w", pady=6)

    def _panel(self, parent, title: str, row: int, col: int):
        frame = ctk.CTkFrame(parent, corner_radius=26, fg_color=GREEN_PANEL)
        frame.grid(row=row, column=col, sticky="nsew", padx=8)
        parent.grid_columnconfigure(col, weight=1)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 6))
        ctk.CTkLabel(frame, text=" ", text_color=SUBTLE).pack(anchor="w", padx=18, pady=(0, 6))
        return frame

    def _metric_card(self, parent, row: int, col: int, title: str, value: str, subtitle: str):
        card = ctk.CTkFrame(parent, corner_radius=24, fg_color=GREEN_PANEL)
        card.grid(row=row, column=col, sticky="nsew", padx=8)
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont("Segoe UI", 11), text_color=SUBTLE).pack(anchor="w", padx=18, pady=(16, 0))
        value_label = ctk.CTkLabel(card, text=value, font=ctk.CTkFont("Segoe UI", 24, "bold"), text_color=TEXT)
        value_label.pack(anchor="w", padx=18, pady=(8, 0))
        ctk.CTkLabel(card, text=subtitle, font=ctk.CTkFont("Segoe UI", 11), text_color=MUTED).pack(anchor="w", padx=18, pady=(4, 16))
        return {"frame": card, "value": value_label}

    def _bind_panel_resize(self, widget, callback) -> None:
        widget.bind("<Configure>", lambda _e: callback())

    def _load_startup_state(self) -> None:
        imports = self.settings.get("last_imports", [])
        if isinstance(imports, list):
            for item in imports:
                path = Path(item)
                if path.exists():
                    self._load_transactions([path])
                    break
        self.load_snapshot(silent=True)
        if not self.transactions:
            self.load_sample()

    def show_view(self, key: str) -> None:
        for name, view in self.views.items():
            if name == key:
                view.pack(fill="both", expand=True)
            else:
                view.pack_forget()
        for name, button in self.nav_buttons.items():
            button.configure(fg_color=GOLD if name == key else GREEN_CARD, text_color="#0d160f" if name == key else TEXT)
        self.active_view = key
        if key == "dashboard":
            self.render_dashboard()
        elif key == "activity":
            self.refresh_transactions()
        elif key == "holdings":
            self.render_holdings()

    def open_csv(self) -> None:
        names = filedialog.askopenfilenames(title="Select CSV files", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if names:
            self._load_transactions([Path(name) for name in names])

    def open_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select a folder with CSV files")
        if not folder:
            return
        paths = sorted(Path(folder).glob("*.csv"))
        if not paths:
            messagebox.showinfo(APP_TITLE, "No CSV files found in that folder.")
            return
        self._load_transactions(paths)

    def open_last_folder(self) -> None:
        imports = self.settings.get("last_imports", [])
        if isinstance(imports, list) and imports:
            path = Path(imports[0]).parent
            if path.exists():
                os.startfile(str(path))

    def browse_cache(self) -> None:
        name = filedialog.askopenfilename(
            title="Select Robinhood cache JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=str(DEFAULT_CACHE.parent),
        )
        if name:
            self.cache_var.set(name)
            self.load_snapshot()

    def load_sample(self) -> None:
        sample = """date,description,amount,category,account,balance
2026-01-01,Salary,5000,Income,Checking,5000
2026-01-03,Rent,-1800,Housing,Checking,3200
2026-01-04,Groceries,-142.73,Food,Checking,3057.27
2026-01-08,Spotify,-11.99,Entertainment,Checking,3045.28
2026-01-15,Uber,-24.55,Transport,Checking,3020.73
2026-02-01,Salary,5000,Income,Checking,8020.73
2026-02-03,Rent,-1800,Housing,Checking,6220.73
2026-02-06,Groceries,-153.44,Food,Checking,6067.29
2026-02-08,Spotify,-11.99,Entertainment,Checking,6055.30
2026-02-15,Uber,-19.12,Transport,Checking,6036.18
"""
        path = ROOT / "_sample_finance.csv"
        path.write_text(sample, encoding="utf-8")
        self._load_transactions([path])

    def _load_transactions(self, paths: list[Path]) -> None:
        loaded: list[Transaction] = []
        for path in paths:
            try:
                loaded.extend(load_transactions(path))
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"{path.name}: {exc}")
                return
        self.transactions = sorted(loaded, key=lambda tx: tx.date)
        self.filtered = list(self.transactions)
        self.settings["last_imports"] = [str(p) for p in paths]
        save_settings(self.settings)
        self._sync_months()
        self.render_dashboard()
        self.refresh_transactions()

    def _sync_months(self) -> None:
        months = sorted({month_key(tx.date) for tx in self.transactions})
        values = [""] + months
        self.month_menu.configure(values=values)
        if self.month_var.get() not in months:
            self.month_var.set("")

    def apply_filters(self) -> None:
        month = self.month_var.get().strip()
        search = self.search_var.get().strip().lower()
        txs = self.transactions
        if month:
            txs = [tx for tx in txs if month_key(tx.date) == month]
        if search:
            txs = [
                tx for tx in txs
                if search in tx.description.lower()
                or search in tx.category.lower()
                or search in tx.account.lower()
            ]
        self.filtered = txs
        self.render_dashboard()
        self.refresh_transactions()

    def _on_top_change(self, value: float) -> None:
        self.top_var.set(max(3, min(25, int(round(value)))))
        self.top_value.configure(text=str(self.top_var.get()))
        self.render_dashboard_charts()
        self.render_holdings()

    def render_dashboard(self) -> None:
        data = summary_for(self.filtered if self.filtered is not None else self.transactions)
        self.metric_cards["transactions"]["value"].configure(text=str(data["count"]))
        self.metric_cards["income"]["value"].configure(text=format_money(data["income"]))
        self.metric_cards["expenses"]["value"].configure(text=format_money(data["expenses"]))
        self.metric_cards["net"]["value"].configure(text=format_money(data["net"]))
        self._format_summary_status(data)
        self.render_dashboard_charts()

    def render_dashboard_charts(self) -> None:
        data = summary_for(self.filtered if self.filtered is not None else self.transactions)
        self._draw_bar_chart(self.flow_chart, data["monthly"], title="Monthly cash flow", value_formatter=format_money)
        self._draw_bar_chart(self.top_chart, data["categories"][: self.top_var.get()], title="Top categories", value_formatter=format_money)

    def _draw_bar_chart(self, canvas: tk.Canvas, rows: list, title: str, value_formatter) -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 100)
        height = max(canvas.winfo_height(), 100)
        if width < 120 or height < 120:
            return
        canvas.create_text(18, 16, text=title, anchor="w", fill=GOLD_SOFT, font=("Segoe UI", 13, "bold"))
        if not rows:
            canvas.create_text(width / 2, height / 2, text="No data yet", fill=SUBTLE, font=("Segoe UI", 12))
            return
        if title == "Monthly cash flow":
            months = rows
            values = [value for _, value in months]
            max_abs = max(max(abs(v) for v in values), 1)
            left = 24
            bottom = height - 34
            chart_h = height - 80
            gap = max(16, (width - 72) / max(len(months), 1))
            bar_w = max(18, min(44, gap * 0.56))
            zero_y = 42 + chart_h / 2
            canvas.create_line(left, zero_y, width - 24, zero_y, fill=GREEN_LINE, width=1)
            for idx, (month, value) in enumerate(months):
                x = left + idx * gap + (gap - bar_w) / 2
                bar_h = (abs(value) / max_abs) * (chart_h * 0.42)
                y = zero_y - bar_h if value >= 0 else zero_y
                color = GOLD if value >= 0 else "#4da36f"
                canvas.create_rectangle(x, y, x + bar_w, y + max(bar_h, 1), fill=color, outline=color)
                canvas.create_text(x + bar_w / 2, bottom, text=month, fill=MUTED, font=("Segoe UI", 9))
                canvas.create_text(x + bar_w / 2, y - 10 if value >= 0 else y + bar_h + 10, text=value_formatter(value), fill=TEXT, font=("Segoe UI", 8))
        else:
            max_val = max((abs(v) for _, v in rows), default=1)
            top = 40
            left = 24
            right = width - 24
            row_h = max(32, min(44, (height - 60) / max(len(rows), 1)))
            for i, (label, value) in enumerate(rows):
                y = top + i * row_h
                canvas.create_text(left, y + row_h / 2, text=label, anchor="w", fill=TEXT, font=("Segoe UI", 10))
                bar_x = 200
                bar_w = max(8, (right - bar_x - 90) * (abs(value) / max_val))
                canvas.create_rectangle(bar_x, y + 8, bar_x + bar_w, y + row_h - 8, fill=GOLD, outline=GOLD)
                canvas.create_text(right, y + row_h / 2, text=value_formatter(value), anchor="e", fill=MUTED, font=("Segoe UI", 9))

    def refresh_transactions(self) -> None:
        txs = self.filtered if self.filtered is not None else self.transactions
        self.tx_tree.delete(*self.tx_tree.get_children())
        for tx in txs[:2000]:
            self.tx_tree.insert(
                "",
                "end",
                values=(tx.date.strftime("%Y-%m-%d"), tx.description, format_money(tx.amount), tx.category, tx.account),
            )

    def load_snapshot(self, silent: bool = False) -> None:
        path = Path(self.cache_var.get()).expanduser()
        if not path.exists():
            self.snapshot = None
            self.bridge_var.set("Robinhood cache not found")
            if not silent:
                messagebox.showwarning(APP_TITLE, f"Could not find cache file: {path}")
            self.render_holdings()
            return
        try:
            self.snapshot = load_json(path)
            self.settings["last_cache"] = str(path)
            save_settings(self.settings)
            self.render_holdings()
        except Exception as exc:
            self.snapshot = None
            self.bridge_var.set("Failed to load Robinhood cache")
            if not silent:
                messagebox.showerror(APP_TITLE, f"Could not read cache file: {exc}")

    def render_holdings(self) -> None:
        for box in [self.accounts_box, self.equities_box, self.options_box]:
            for child in box.winfo_children():
                child.destroy()

        snapshot = self.snapshot
        if not snapshot:
            self.bridge_var.set("Robinhood snapshot unavailable")
            ctk.CTkLabel(self.accounts_box, text="No snapshot loaded.", text_color=MUTED).pack(anchor="w", padx=14, pady=14)
            ctk.CTkLabel(self.equities_box, text="No snapshot loaded.", text_color=MUTED).pack(anchor="w", padx=14, pady=14)
            ctk.CTkLabel(self.options_box, text="No snapshot loaded.", text_color=MUTED).pack(anchor="w", padx=14, pady=14)
            return

        accounts = snapshot.get("accounts", [])
        equity_positions = snapshot.get("equity_positions", {})
        option_positions = snapshot.get("option_positions", {})
        option_instruments = snapshot.get("option_instruments", {})
        equity_total = sum(len(v) for v in equity_positions.values())
        option_total = sum(len(v) for v in option_positions.values())
        self.bridge_var.set(
            f"Loaded {len(accounts)} accounts, {equity_total} equity positions, {option_total} option positions. Updated {fmt_date(snapshot.get('updated_at'))}"
        )

        for account in accounts:
            account_number = str(account.get("account_number", ""))
            equities = equity_positions.get(account_number, [])
            options = option_positions.get(account_number, [])
            card = ctk.CTkFrame(self.accounts_box, corner_radius=18, fg_color=GREEN_CARD)
            card.pack(fill="x", padx=8, pady=8)
            ctk.CTkLabel(card, text=friendly_name(account), font=ctk.CTkFont("Segoe UI", 14, "bold"), text_color=TEXT).pack(anchor="w", padx=14, pady=(12, 0))
            ctk.CTkLabel(
                card,
                text=f"{mask_account(account_number)}  •  {account.get('brokerage_account_type', '')}  •  {account.get('type', '')}",
                text_color=MUTED,
            ).pack(anchor="w", padx=14, pady=(4, 0))
            ctk.CTkLabel(
                card,
                text=f"{len(equities)} equity positions  •  {len(options)} option positions",
                text_color=GOLD_SOFT,
            ).pack(anchor="w", padx=14, pady=(4, 12))

        equity_rows = []
        for positions in equity_positions.values():
            equity_rows.extend(positions)
        for pos in equity_rows:
            q = pos.get("quote", {})
            last = float(q.get("last_trade_price", 0) or 0)
            qty = float(pos.get("quantity", 0) or 0)
            avg = float(pos.get("average_buy_price", 0) or 0)
            value = qty * last
            pnl = value - (qty * avg)
            card = ctk.CTkFrame(self.equities_box, corner_radius=18, fg_color=GREEN_CARD)
            card.pack(fill="x", padx=8, pady=8)
            ctk.CTkLabel(card, text=pos.get("symbol", "Unknown"), font=ctk.CTkFont("Segoe UI", 14, "bold"), text_color=TEXT).pack(anchor="w", padx=14, pady=(12, 0))
            ctk.CTkLabel(card, text=f"{qty:.6f} shares  •  Avg {format_money(avg)}", text_color=MUTED).pack(anchor="w", padx=14, pady=(4, 0))
            ctk.CTkLabel(card, text=f"Value {format_money(value)}  •  P&L {format_money(pnl)}", text_color=GOLD_SOFT if pnl >= 0 else "#ffb0a7").pack(anchor="w", padx=14, pady=(4, 12))

        option_rows = []
        for positions in option_positions.values():
            option_rows.extend(positions)
        for pos in option_rows:
            inst = option_instruments.get(pos.get("option_id"), {})
            q = pos.get("quote", {})
            card = ctk.CTkFrame(self.options_box, corner_radius=18, fg_color=GREEN_CARD)
            card.pack(fill="x", padx=8, pady=8)
            title = f"{pos.get('chain_symbol', 'Unknown')} {inst.get('strike_price', '')} {inst.get('type', '')}"
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont("Segoe UI", 14, "bold"), text_color=TEXT).pack(anchor="w", padx=14, pady=(12, 0))
            ctk.CTkLabel(
                card,
                text=f"{pos.get('type', '')}  •  {pos.get('quantity', '')} contract(s)  •  Expires {pos.get('expiration_date', '')}",
                text_color=MUTED,
            ).pack(anchor="w", padx=14, pady=(4, 0))
            ctk.CTkLabel(
                card,
                text=f"Mark {format_money(float(q.get('mark_price', 0) or 0))}  •  Bid {format_money(float(q.get('bid_price', 0) or 0))}  •  Ask {format_money(float(q.get('ask_price', 0) or 0))}",
                text_color=GOLD_SOFT,
            ).pack(anchor="w", padx=14, pady=(4, 12))

    def _bar(self, target: list[tuple[str, float]], limit: int) -> list[tuple[str, float]]:
        return target[: max(3, min(limit, 25))]

    def _format_summary_status(self, data: dict) -> None:
        if data["count"] == 0:
            self.status_var.set("No transactions loaded")
        else:
            self.status_var.set(f"{data['count']} transactions  •  {len(data['months'])} months  •  {len(data['categories'])} categories")


def main() -> int:
    if sys.version_info < (3, 11):
        print("Python 3.11+ is required.", file=sys.stderr)
        return 1
    app = FinanceLensApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
