#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import socket
import subprocess
import webbrowser
import time
import ctypes
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QBrush, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QSizePolicy,
)

from finance_analyzer import Transaction, fmt_money, load_transactions, month_key


APP_TITLE = "Finance Lens"
ROOT = Path(__file__).resolve().parent
DEFAULT_ROBINHOOD_CACHE = ROOT / "robinhood-cache.json"
DEFAULT_WEALTHFRONT_CACHE = ROOT / "wealthfront-cache.json"
SETTINGS_FILE = Path.home() / ".finance_lens_settings.json"
WEALTHFRONT_BRIDGE_URL = "http://127.0.0.1:8766"
WEALTHFRONT_BRIDGE_PORT = 8766

# Premium Apple-level dark palette (refined, clean, elegant)
GREEN_BG = "#0a0c0f"
GREEN_PANEL = "#121418"
GREEN_CARD = "#181c22"
GREEN_CARD_2 = "#20262e"
GREEN_LINE = "#2a3039"
GOLD = "#c9a35f"
GOLD_SOFT = "#e5d09c"
TEXT = "#f1f1f4"
MUTED = "#959aa3"
SUBTLE = "#6f757f"
POSITIVE = "#4ade80"
NEGATIVE = "#f87171"
RH_GREEN = "#00C805"
RH_RED = "#FF5000"
BLUE = "#3B82F6"


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


def compute_portfolio_value(snapshot: dict | None, wf_snapshot: dict | None, manual_assets: dict | None = None) -> float:
    total = 0.0
    if snapshot:
        equity_positions = snapshot.get("equity_positions", {})
        for positions in equity_positions.values():
            for pos in positions:
                q = pos.get("quote", {})
                last = float(q.get("last_trade_price", 0) or 0)
                qty = float(pos.get("quantity", 0) or 0)
                total += qty * last
        # options approx value if available
        option_positions = snapshot.get("option_positions", {})
        for positions in option_positions.values():
            for pos in positions:
                q = pos.get("quote", {})
                mark = float(q.get("mark_price", 0) or 0)
                qty = float(pos.get("quantity", 0) or 0)
                total += qty * mark * 100  # contracts
        # Cash positions in Robinhood (uninvested cash, buying power etc.)
        for acc in snapshot.get("accounts", []):
            for key in ("cash", "buying_power", "cash_balance", "available_cash", "balance"):
                val = acc.get(key)
                if val is not None:
                    try:
                        cval = float(val)
                        if cval > 0:
                            total += cval
                        break
                    except (ValueError, TypeError):
                        pass
    if wf_snapshot:
        holdings = wf_snapshot.get("holdings", [])
        for h in holdings:
            value = float(h.get("institution_value") or h.get("market_value") or 0)
            total += value
        # Include cash/balances from Wealthfront accounts (cash accounts or uninvested)
        for acc in wf_snapshot.get("accounts", []):
            bal = 0.0
            balances = acc.get("balances") or acc
            for k in ("current", "available", "cash", "balance"):
                v = balances.get(k) if isinstance(balances, dict) else acc.get(k)
                if v is not None:
                    try:
                        bval = float(v)
                        if bval > 0:
                            bal = bval
                            break
                    except (ValueError, TypeError):
                        pass
            if bal > 0:
                total += bal
    if manual_assets:
        total += sum(float(v) for v in manual_assets.values() if v)
    return total


def pythonw_executable() -> str:
    candidate = Path(sys.executable)
    if candidate.name.lower() == "python.exe":
        pythonw = candidate.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(candidate)


def is_port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


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


class BarChart(QWidget):
    """Premium custom chart with Apple-like clarity and polish."""
    def __init__(self, title: str, parent: QWidget | None = None, show_title: bool = True) -> None:
        super().__init__(parent)
        self._title = title
        self._rows: list[tuple[str, float]] = []
        self._show_title = show_title
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_rows(self, rows: list[tuple[str, float]]) -> None:
        self._rows = rows
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        # Blend with page background (no contrasting bar behind title)
        painter.fillRect(self.rect(), QColor(GREEN_BG))

        # Refined title (optional when external highlight used)
        if self._show_title:
            painter.setPen(QColor(GOLD_SOFT))
            painter.setFont(QFont("Segoe UI", 15, QFont.DemiBold))
            painter.drawText(22, 26, self._title)

        if not self._rows:
            painter.setPen(QColor(SUBTLE))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(self.rect().adjusted(0, 30, 0, 0), Qt.AlignCenter, "No data yet")
            return

        if "cash flow" in self._title.lower():
            # Monthly cash flow – beautiful diverging bars
            values = [value for _, value in self._rows]
            max_abs = max(max(abs(v) for v in values), 1.0)

            left = 26
            right = self.width() - 26
            top = 46
            bottom = self.height() - 30
            chart_h = self.height() - 94
            n = max(len(self._rows), 1)
            gap = max(14, (right - left) / n)
            bar_w = max(14, min(52, gap * 0.62))

            # Zero line (subtle)
            zero_y = top + chart_h / 2
            painter.setPen(QPen(QColor(GREEN_LINE), 1.5))
            painter.drawLine(left - 6, int(zero_y), self.width() - 14, int(zero_y))

            for idx, (label, value) in enumerate(self._rows):
                x = left + idx * gap + (gap - bar_w) / 2
                scale = abs(value) / max_abs
                bar_h = max(2, scale * (chart_h * 0.46))

                if value >= 0:
                    y = zero_y - bar_h
                    color = QColor(GOLD)
                else:
                    y = zero_y
                    color = QColor("#4a9c6f")

                # Soft shadow bar (slightly offset)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(0, 0, 0, 35))
                painter.drawRoundedRect(QRectF(x + 1.5, y + 2, bar_w, bar_h), 9, 9)

                # Main bar
                painter.setBrush(QBrush(color))
                painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), 9, 9)

                # Month label
                painter.setPen(QColor(MUTED))
                painter.setFont(QFont("Segoe UI", 8))
                painter.drawText(int(x), bottom, int(bar_w), 14, Qt.AlignCenter, label)

                # Value label above/below
                painter.setPen(QColor(TEXT))
                painter.setFont(QFont("Segoe UI", 8, QFont.Medium))
                val_y = y - 14 if value >= 0 else y + bar_h + 3
                painter.drawText(int(x), int(val_y), int(bar_w), 13, Qt.AlignCenter, fmt_money(value))
        else:
            # Top categories / spending – elegant horizontal bars
            max_val = max((abs(v) for _, v in self._rows), default=1.0)
            top = 10 if not getattr(self, '_show_title', True) else 44
            left = 24
            right = self.width() - 24
            usable = max(len(self._rows), 1)
            row_h = max(26, min(38, (self.height() - 72) / usable))

            for idx, (label, value) in enumerate(self._rows[:14]):
                y = top + idx * row_h
                label_w = 168

                # Label - consistent truncation
                painter.setPen(QColor(TEXT))
                painter.setFont(QFont("Segoe UI", 10))
                display_label = label if len(label) <= 28 else label[:25] + "…"
                painter.drawText(left, int(y + row_h / 2 + 3), display_label)

                # Bar background track
                bar_track_x = left + label_w
                track_width = right - bar_track_x - 70
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(GREEN_LINE))
                painter.drawRoundedRect(QRectF(bar_track_x, y + 7, track_width, row_h - 14), 6, 6)

                # Actual bar - min width so small values are still visible
                bar_w = max(10, (track_width) * (abs(value) / max_val))
                painter.setBrush(QBrush(QColor(GOLD)))
                painter.drawRoundedRect(QRectF(bar_track_x, y + 7, bar_w, row_h - 14), 6, 6)

                # Value - slightly larger font
                painter.setPen(QColor(MUTED))
                painter.setFont(QFont("Segoe UI", 10))
                painter.drawText(int(right - 70), int(y + row_h / 2 + 3), fmt_money(value))


class LineChart(QWidget):
    """Robinhood-banking inspired net worth line chart: clean green line + subtle area fill,
    nice grid, prominent last point, better labels."""
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[str, float]] = []
        self.setMinimumHeight(200)
        self.setMaximumHeight(300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, points: list[tuple[str, float]]) -> None:
        self._points = points or []
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(GREEN_BG))

        if len(self._points) < 1:
            painter.setPen(QColor(SUBTLE))
            painter.setFont(QFont("Segoe UI", 14))
            painter.drawText(self.rect(), Qt.AlignCenter, "No history data yet — load data or import CSVs with balances")
            return

        values = [v for _, v in self._points]
        min_v = min(values)
        max_v = max(values)
        if max_v == min_v:
            max_v += 1
            min_v -= 1

        margin_left = 60
        margin_right = 15
        margin_top = 10
        margin_bottom = 28
        chart_w = self.width() - margin_left - margin_right
        chart_h = self.height() - margin_top - margin_bottom
        left = margin_left
        top = margin_top

        def y(v):
            return top + chart_h - (v - min_v) / (max_v - min_v) * chart_h

        n = len(self._points)
        step = chart_w / max(n - 1, 1)

        # Subtle area fill under the line (Robinhood green, low opacity)
        fill_path = QPainterPath()
        for i, (_, v) in enumerate(self._points):
            x = left + i * step
            yy = y(v)
            if i == 0:
                fill_path.moveTo(x, yy)
            else:
                fill_path.lineTo(x, yy)
        fill_path.lineTo(left + (n-1)*step, top + chart_h)
        fill_path.lineTo(left, top + chart_h)
        fill_path.closeSubpath()
        fill_color = QColor(RH_GREEN)
        fill_color.setAlpha(28)
        painter.fillPath(fill_path, QBrush(fill_color))

        # Main line - thick bright green like Robinhood
        painter.setPen(QPen(QColor(RH_GREEN), 4))
        line_path = QPainterPath()
        for i, (_, v) in enumerate(self._points):
            x = left + i * step
            yy = y(v)
            if i == 0:
                line_path.moveTo(x, yy)
            else:
                line_path.lineTo(x, yy)
        painter.drawPath(line_path)

        # Small dots on points (subtle)
        painter.setBrush(QColor(RH_GREEN))
        painter.setPen(Qt.NoPen)
        for i, (_, v) in enumerate(self._points):
            x = left + i * step
            yy = y(v)
            painter.drawEllipse(int(x - 3), int(yy - 3), 6, 6)

        # Last point highlighted with ring (RH style)
        last_x = left + (n-1) * step
        last_y = y(values[-1])
        painter.setPen(QPen(QColor(RH_GREEN), 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(int(last_x - 6), int(last_y - 6), 12, 12)

        # Value label at end of line (like RH hover value)
        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
        val_str = fmt_money(values[-1])
        painter.drawText(int(last_x + 8), int(last_y - 5), val_str)

        # Minimal y labels (left side, RH clean style - only min/max)
        painter.setPen(QColor(MUTED))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(4, int(top + 8), fmt_money(max_v))
        painter.drawText(4, int(top + chart_h - 2), fmt_money(min_v))

        # X labels - spaced for this month (RH minimal dates)
        painter.setPen(QColor(SUBTLE))
        painter.setFont(QFont("Segoe UI", 9))
        label_step = max(1, (n - 1) // 5 + 1)
        for i in range(0, n, label_step):
            x = left + i * step
            lbl = self._points[i][0]
            short = lbl  # already MM/DD
            painter.drawText(int(x - 12), self.height() - 6, short)


class FinanceLensApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1540, 960)
        # Allow smaller windows; content below the hero will scroll vertically when needed.
        self.setMinimumSize(QSize(900, 600))
        self.setAcceptDrops(True)

        # Center the window on the primary screen (prevents off-screen / multi-monitor issues)
        try:
            screen = QGuiApplication.primaryScreen().geometry()
            x = max(50, (screen.width() - self.width()) // 2)
            y = max(50, (screen.height() - self.height()) // 2)
            self.move(x, y)
        except Exception:
            pass

        # Set app icon (cached)
        if not hasattr(FinanceLensApp, '_cached_icon'):
            try:
                icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
                if not os.path.exists(icon_path):
                    icon_path = os.path.join(os.path.dirname(__file__), "icon.jpg")
                if os.path.exists(icon_path):
                    FinanceLensApp._cached_icon = QIcon(icon_path)
                else:
                    FinanceLensApp._cached_icon = None
            except Exception:
                FinanceLensApp._cached_icon = None
        if FinanceLensApp._cached_icon:
            self.setWindowIcon(FinanceLensApp._cached_icon)

        # Make Windows title bar dark (instead of bright white)
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                value = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value)
                )
            except Exception:
                pass

        self.settings = load_settings()
        self.load_manual_assets()
        self.transactions: list[Transaction] = []
        self.filtered: list[Transaction] = []
        self.net_worth_history: list[tuple[str, float]] = []
        self.networth_chart = None
        self.snapshot: dict | None = None
        self.wealthfront_snapshot: dict | None = None
        self.robinhood_plaid_snapshot: dict | None = None

        self.top_count = 8

        self._build_ui()
        self._apply_style()
        self._load_startup_state()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                background: {GREEN_BG};
                color: {TEXT};
                font-family: "Segoe UI Variable", "Segoe UI", "Inter", system-ui, sans-serif;
            }}
            #Sidebar {{
                background: {GREEN_PANEL};
                border-radius: 28px;
            }}
            #MainShell {{
                background: {GREEN_BG};
            }}
            #Hero {{
                background: transparent;
                border-radius: 0px;
            }}
            #BrandBox, #HoldingsTop, #SettingsCard {{
                background: {GREEN_PANEL};
                border-radius: 28px;
            }}
            #Card {{
                background: {GREEN_CARD};
                border-radius: 24px;
            }}
            #CardTitle {{
                color: {SUBTLE};
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }}
            #CardValue {{
                color: {TEXT};
                font-size: 26px;
                font-weight: 700;
                letter-spacing: -0.3px;
            }}
            #CardSubtitle {{
                color: {MUTED};
                font-size: 12px;
            }}
            QLabel#SidebarTitle {{
                color: {TEXT};
                font-size: 20px;
                font-weight: 700;
                letter-spacing: -0.2px;
            }}
            QLabel#SidebarSub {{
                color: {MUTED};
                font-size: 12px;
                letter-spacing: 0.3px;
            }}
            QPushButton {{
                background: {GREEN_CARD};
                border: 1px solid {GREEN_LINE};
                color: {TEXT};
                padding: 10px 16px;
                border-radius: 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {GREEN_CARD_2};
                border-color: #333a43;
            }}
            QPushButton:pressed {{
                background: {GREEN_LINE};
            }}
            QPushButton#PrimaryButton {{
                background: {GOLD};
                color: #0f120f;
                border: 0px;
                font-weight: 600;
            }}
            QPushButton#PrimaryButton:hover {{
                background: {GOLD_SOFT};
            }}
            QPushButton#PrimaryButton:pressed {{
                background: #b38f4f;
            }}
            QLineEdit, QComboBox {{
                background: {GREEN_CARD};
                border: 1px solid {GREEN_LINE};
                border-radius: 14px;
                padding: 9px 14px;
                color: {TEXT};
                selection-background-color: {GOLD};
                selection-color: #111;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border: 1px solid {GOLD};
                background: #1f242b;
            }}
            QComboBox QAbstractItemView {{
                background: {GREEN_PANEL};
                color: {TEXT};
                border: 1px solid {GREEN_LINE};
                selection-background-color: {GREEN_CARD_2};
                padding: 4px;
            }}
            QTableWidget {{
                background: {GREEN_PANEL};
                border: 0px;
                gridline-color: {GREEN_LINE};
                outline: 0;
            }}
            QTableWidget::item {{
                padding: 10px 12px;
                border: 0px;
                font-size: 14px;
            }}
            QTableWidget::item:selected {{
                background: {GREEN_CARD_2};
                color: {TEXT};
            }}
            QHeaderView::section {{
                background: {GREEN_PANEL};
                color: {GOLD_SOFT};
                padding: 12px 14px;
                border: 0px;
                font-weight: 600;
                font-size: 14px;
                letter-spacing: 0.5px;
            }}
            /* Bigger text + styles for Activity tree (monthly cash flow) */
            QTreeWidget {{
                background: {GREEN_PANEL};
                border: 0px;
                outline: 0;
                font-size: 14px;
            }}
            QTreeWidget::item {{
                padding: 10px 12px;
                border: 0px;
            }}
            QTreeWidget::item:selected {{
                background: {GREEN_CARD_2};
                color: {TEXT};
            }}

            /* Modern rounded scrollbars (replaces old XP-style) */
            QScrollBar:vertical {{
                background: {GREEN_PANEL};
                width: 10px;
                margin: 0;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: #3a404b;
                border-radius: 5px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #4a515e;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
                background: none;
            }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: none;
            }}

            QScrollBar:horizontal {{
                background: {GREEN_PANEL};
                height: 10px;
                margin: 0;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: #3a404b;
                border-radius: 5px;
                min-width: 30px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: #4a515e;
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0px;
                border: none;
                background: none;
            }}
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: none;
            }}

            /* QLabel styles inherited from global; removed unsupported font-feature-settings */
            """
        )

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("MainShell")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        self.sidebar = QWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(210)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(16, 16, 16, 16)
        side_layout.setSpacing(10)

        brand = QFrame()
        brand.setObjectName("BrandBox")
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(18, 18, 18, 18)
        brand_layout.setSpacing(3)

        # Logo inside the app
        try:
            icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
            if not os.path.exists(icon_path):
                icon_path = os.path.join(os.path.dirname(__file__), "icon.jpg")
            if os.path.exists(icon_path):
                logo = QLabel()
                pix = QPixmap(icon_path).scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                logo.setPixmap(pix)
                logo.setAlignment(Qt.AlignCenter)
                brand_layout.addWidget(logo)
        except Exception:
            pass

        title = QLabel("Finance Lens")
        title.setObjectName("SidebarTitle")
        sub = QLabel("Beautiful money clarity")
        sub.setObjectName("SidebarSub")
        brand_layout.addWidget(title)
        brand_layout.addWidget(sub)
        side_layout.addWidget(brand)
        self._apply_shadow(brand, radius=22, blur=16, y=2)

        self.nav_buttons: dict[str, QToolButton] = {}
        for key, label in [
            ("dashboard", "📊  Dashboard"),
            ("activity", "📋  Activity"),
            ("holdings", "📁  Holdings"),
            ("positions", "📈  Positions"),
            ("networth", "💰  Net Worth"),
            ("settings", "⚙️  Settings"),
        ]:
            btn = QToolButton()
            btn.setText(label)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
            btn.clicked.connect(lambda _checked=False, k=key: self.show_view(k))
            # Base style (Apple sidebar feel)
            btn.setStyleSheet(f"""
                QToolButton {{
                    background: transparent;
                    color: {MUTED};
                    border: 0px;
                    padding: 11px 16px;
                    border-radius: 14px;
                    text-align: left;
                    font-size: 14px;
                }}
            """)
            side_layout.addWidget(btn)
            self.nav_buttons[key] = btn

        side_layout.addStretch(1)
        self.sidebar_bridge = QLabel("No Robinhood cache loaded")
        self.sidebar_bridge.setWordWrap(True)
        self.sidebar_bridge.setStyleSheet(f"color: {MUTED};")
        side_layout.addWidget(self.sidebar_bridge)

        self.main = QWidget()
        main_layout = QVBoxLayout(self.main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(14)

        self.hero = QFrame()
        self.hero.setObjectName("Hero")
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(20, 14, 20, 14)
        hero_layout.setSpacing(14)

        left_hero = QVBoxLayout()
        left_hero.setSpacing(3)

        # Logo in main hero area
        try:
            icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
            if not os.path.exists(icon_path):
                icon_path = os.path.join(os.path.dirname(__file__), "icon.jpg")
            if os.path.exists(icon_path):
                hero_logo = QLabel()
                pix = QPixmap(icon_path).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                hero_logo.setPixmap(pix)
                left_hero.addWidget(hero_logo)
        except Exception:
            pass

        self.hero_title = QLabel("Overview")
        self.hero_title.setStyleSheet(f"color: {TEXT}; font-size: 26px; font-weight: 700; letter-spacing: -0.4px;")
        self.hero_subtitle = QLabel("Cash flow, holdings & insights. Drop CSVs anywhere to import.")
        self.hero_subtitle.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        left_hero.addWidget(self.hero_title)
        left_hero.addWidget(self.hero_subtitle)
        left_hero.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.import_btn = QPushButton("Import CSV")
        self.import_btn.setObjectName("PrimaryButton")
        self.import_btn.clicked.connect(self.open_csv)
        self.folder_btn = QPushButton("Folder")
        self.folder_btn.clicked.connect(self.open_folder)
        self.reload_btn = QPushButton("Reload")
        self.reload_btn.clicked.connect(self.load_snapshot)
        actions.addWidget(self.import_btn)
        actions.addWidget(self.folder_btn)
        actions.addWidget(self.reload_btn)

        hero_layout.addLayout(left_hero, 1)
        hero_layout.addLayout(actions)
        main_layout.addWidget(self.hero)

        self.stack = QStackedWidget()
        # Wrap the page stack in a scroll area so the dashboard (and other pages) become
        # scrollable vertically when the window is resized smaller than the content.
        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setWidget(self.stack)
        content_scroll.setFrameShape(QFrame.NoFrame)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        main_layout.addWidget(content_scroll, 1)

        self.dashboard_page = self._build_dashboard_page()
        self.activity_page = self._build_activity_page()
        self.holdings_page = self._build_holdings_page()
        self.settings_page = self._build_settings_page()
        self.positions_page = self._build_positions_page()
        self.networth_page = self._build_networth_page()
        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.activity_page)
        self.stack.addWidget(self.holdings_page)
        self.stack.addWidget(self.positions_page)
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.networth_page)

        # Subtle depth on key surfaces (hero is now flat to avoid bars at top)
        self._apply_shadow(self.sidebar)

        # Hidden path widgets for compatibility with load methods (config lives in Settings)
        self.cache_path = QLineEdit(str(self.settings.get("last_cache", str(DEFAULT_ROBINHOOD_CACHE))))
        self.wealthfront_cache_path = QLineEdit(str(self.settings.get("wealthfront_cache", str(DEFAULT_WEALTHFRONT_CACHE))))

        outer.addWidget(self.sidebar)
        outer.addWidget(self.main, 1)

    def _page_frame(self) -> QWidget:
        return QWidget()

    def _compute_net_worth_history(self) -> list[tuple[str, float]]:
        """Compute net worth over time from transactions (prefers 'balance' column if present).
        Supports multiple accounts (e.g. Pension, TDA, Checking) by summing latest balance per account per date.
        """
        if not self.transactions:
            return []
        # Only this month for cleaner chart (user request) - start from June 1
        from datetime import datetime as dtmod
        now = dtmod.now()
        txs = [t for t in sorted(self.transactions, key=lambda t: t.date)
               if t.date.year == now.year and t.date.month == now.month]
        has_balance = any(getattr(tx, 'balance', None) is not None for tx in txs)
        history: list[tuple[str, float]] = []
        if has_balance:
            from collections import defaultdict
            by_date_accounts = defaultdict(dict)
            for tx in txs:
                bal = getattr(tx, 'balance', None)
                if bal is not None:
                    acc = getattr(tx, 'account', None) or "Unknown"
                    by_date_accounts[tx.date][acc] = bal
            for d in sorted(by_date_accounts.keys()):
                total = sum(by_date_accounts[d].values())
                history.append((d.strftime('%m/%d'), total))
        else:
            running = 0.0
            by_date: dict = {}
            for tx in txs:
                running += tx.amount
                by_date[tx.date] = running
            for d in sorted(by_date.keys()):
                history.append((d.strftime('%m/%d'), by_date[d]))

        # Ensure the chart always starts from the 1st of this month
        start_label = f"{now.month:02d}/01"
        if not history:
            # No tx this month: show flat from 1st to today with current value
            current_inv = compute_portfolio_value(self.snapshot, self.wealthfront_snapshot, getattr(self, 'manual_assets', None))
            if current_inv > 0:
                history.append((start_label, current_inv))
                today_label = dtmod.now().strftime('%m/%d')
                if today_label != start_label:
                    history.append((today_label, current_inv))
        else:
            if history[0][0] != start_label:
                history.insert(0, (start_label, history[0][1]))
            # Append today's point with current portfolio (if new day)
            current_inv = compute_portfolio_value(self.snapshot, self.wealthfront_snapshot, getattr(self, 'manual_assets', None))
            today_label = dtmod.now().strftime('%m/%d')
            if current_inv > 0:
                last_val = history[-1][1]
                if history[-1][0] != today_label:
                    history.append((today_label, last_val + current_inv))
        return history

    def _apply_shadow(self, widget: QWidget, radius: int = 28, blur: int = 26, x: int = 0, y: int = 4) -> None:
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur)
        effect.setColor(QColor(0, 0, 0, 110))
        effect.setOffset(x, y)
        widget.setGraphicsEffect(effect)

    def _build_dashboard_page(self) -> QWidget:
        """Dashboard: KPIs with highlight boxes + allocation graphs (bars in cards) + Top Categories to fill space. Clean, consistent cards."""
        page = QWidget()
        main_lay = QVBoxLayout(page)
        main_lay.setContentsMargins(16, 12, 16, 16)
        main_lay.setSpacing(14)

        # KPI row (4 metrics) - prominent
        kpi = QHBoxLayout()
        kpi.setSpacing(10)
        self.kpi_net = self._metric("Net worth", "$0.00")
        self.kpi_day = self._metric("Day change", "$0.00")
        self.kpi_pl = self._metric("Holdings P/L", "$0.00", "0.0%")
        self.kpi_cash = self._metric("Cash / Buying power", "$0.00")
        kpi_highlight = f"background: {GREEN_CARD}; border-radius: 12px;"
        for m in [self.kpi_net, self.kpi_day, self.kpi_pl, self.kpi_cash]:
            kpi.addWidget(m)
            m.setStyleSheet(kpi_highlight)
        main_lay.addLayout(kpi)
        # Removed divider (was one of the dark bars behind text area); spacing provides clean separation

        # Pie charts section + compact summary (helps reduce repetition feel)
        pies_title = QLabel("Allocation & Holdings")
        pies_title.setStyleSheet(f"color: {TEXT}; font-size: 15px; font-weight: 600;")
        main_lay.addWidget(pies_title)

        # Allocation graphs in cards with highlighted titles (Robinhood-inspired)
        pies_row1 = QHBoxLayout()
        pies_row1.setSpacing(12)

        # Use helper for consistent card + highlighted title rect
        self.alloc_graph = BarChart("Asset allocation", show_title=False)
        card_alloc, self.alloc_title = self._make_titled_graph_card("Asset allocation", self.alloc_graph, 300)
        pies_row1.addWidget(card_alloc, 1)

        self.account_graph = BarChart("Value by account", show_title=False)
        card_account, self.account_title = self._make_titled_graph_card("Value by account", self.account_graph, 300)
        pies_row1.addWidget(card_account, 1)

        main_lay.addLayout(pies_row1)

        self.holdings_graph = BarChart("Market value by symbol", show_title=False)
        card_hold, self.holdings_graph_title = self._make_titled_graph_card("Market value by symbol", self.holdings_graph, 300)
        main_lay.addWidget(card_hold)

        # Top categories in card (using helper for consistency)
        self.top_categories_graph = BarChart("Categories", show_title=False)
        card_cats, _ = self._make_titled_graph_card("Top Categories", self.top_categories_graph, 220)
        main_lay.addWidget(card_cats)

        # Small footer hint (kept minimal)
        exp = QLabel("Data from caches + CSVs • Use Positions tab for full details")
        exp.setStyleSheet(f"color: {SUBTLE}; font-size: 11px;")
        main_lay.addWidget(exp)

        return page

    def _build_networth_page(self) -> QWidget:
        """Net Worth page styled to mirror Robinhood Banking: clean big number at top, delta, large prominent chart with green line/area, minimal text. Chart limited to this month."""
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(8)

        # Compact header at top
        header_widget = QWidget()
        hlay = QVBoxLayout(header_widget)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(2)

        self.nw_label = QLabel("Net worth")
        self.nw_label.setStyleSheet(f"color: {MUTED}; font-size: 13px; font-weight: 500;")
        hlay.addWidget(self.nw_label)

        self.nw_amount = QLabel("$0.00")
        self.nw_amount.setStyleSheet(f"color: {TEXT}; font-size: 52px; font-weight: 700; letter-spacing: -1.5px;")
        hlay.addWidget(self.nw_amount)

        self.nw_change = QLabel("+$0.00 (+0.00%)")
        self.nw_change.setStyleSheet(f"color: {RH_GREEN}; font-size: 16px; font-weight: 600;")
        hlay.addWidget(self.nw_change)

        self.nw_range = QLabel("June 2026")
        self.nw_range.setStyleSheet(f"color: {SUBTLE}; font-size: 12px;")
        hlay.addWidget(self.nw_range)

        header_widget.setMaximumHeight(120)
        lay.addWidget(header_widget)

        # Prominent chart (fixed large height like RH to dominate the view nicely)
        self.networth_chart = LineChart()
        self.networth_chart.setFixedHeight(500)
        lay.addWidget(self.networth_chart)

        return page

    def _build_positions_page(self) -> QWidget:
        """Separate tab for positions. The page itself can be scrollable if needed,
        but no small internal scrollable tabs/sections.
        """
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)

        title = QLabel("Positions")
        title.setStyleSheet(f"color: {TEXT}; font-size: 18px; font-weight: 700;")
        lay.addWidget(title)

        sub = QLabel("All holdings from Robinhood and Wealthfront (sorted by value)")
        sub.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        lay.addWidget(sub)

        # Big total at top of positions page (clean summary header)
        self.positions_total = QLabel("$0.00")
        self.positions_total.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 28px; font-weight: 700;")
        lay.addWidget(self.positions_total)

        self.positions_table = QTableWidget(0, 8)
        self.positions_table.setHorizontalHeaderLabels(["Symbol", "Account", "Qty", "Avg Cost", "Price", "Market Val", "P/L", "P/L %"])
        self.positions_table.verticalHeader().setVisible(False)
        self.positions_table.setAlternatingRowColors(False)
        self.positions_table.setShowGrid(False)
        self.positions_table.horizontalHeader().setStretchLastSection(True)
        # Let the page scroll, not the table itself
        self.positions_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.positions_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.positions_table, 1)

        # Return plain page; outer scroll area (in main layout) will handle page-level scrolling
        # when window is small. Table has scrollbars disabled.
        return page

    def _metric(self, label: str, value: str, sub: str = "") -> QFrame:
        f = QFrame()
        f.setObjectName("Card")
        l = QVBoxLayout(f)
        l.setContentsMargins(16, 12, 16, 12)
        l.setSpacing(4)
        lab = QLabel(label)
        lab.setStyleSheet(f"color: {SUBTLE}; font-size: 13px; letter-spacing: 0.5px;")
        val = QLabel(value)
        val.setStyleSheet(f"color: {TEXT}; font-size: 26px; font-weight: 700;")
        sublab = QLabel(sub)
        sublab.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        l.addWidget(lab)
        l.addWidget(val)
        if sub:
            l.addWidget(sublab)
        self._apply_shadow(f, radius=16, blur=12, y=2)
        f._val_label = val  # for updates
        f._sub_label = sublab
        return f

    def _set_metric(self, metric_frame: QFrame, value: str, sub: str = ""):
        if hasattr(metric_frame, '_val_label'):
            metric_frame._val_label.setText(value)
        if sub and hasattr(metric_frame, '_sub_label'):
            metric_frame._sub_label.setText(sub)

    def _make_titled_graph_card(self, title_text: str, graph_widget: QWidget, min_graph_height: int = 280):
        """Helper to reduce card duplication and ensure consistent title highlight + card style.
        Returns (card, title_label) so caller can keep ref for dynamic updates.
        """
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 14)
        v.setSpacing(6)
        title = QLabel(title_text)
        title.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 14px; font-weight: 600; background: {GREEN_PANEL}; border-radius: 6px; padding: 4px 10px;")
        v.addWidget(title)
        graph_widget.setMinimumHeight(min_graph_height)
        v.addWidget(graph_widget, 1)
        return card, title

    def _build_activity_page(self) -> QWidget:
        """Ground-up recreation: Monthly inflows and outflows for Wealthfront only.
        Pulls from Plaid cash_transactions (account_label "Wealthfront") + any matching CSV txns.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Header
        header = QFrame()
        header.setObjectName("Card")
        hlay = QVBoxLayout(header)
        hlay.setContentsMargins(20, 16, 20, 16)
        hlay.setSpacing(4)
        title = QLabel("Cash Flow")
        title.setStyleSheet(f"color: {TEXT}; font-size: 20px; font-weight: 700;")
        sub = QLabel("Monthly inflows and outflows — Wealthfront (Plaid)")
        sub.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        hlay.addWidget(title)
        hlay.addWidget(sub)
        self._apply_shadow(header, radius=20, blur=14)
        layout.addWidget(header)

        # One clean KPI card (single rectangle) for the three titles/metrics
        kpi_card = QFrame()
        kpi_card.setObjectName("Card")
        kpi_layout = QHBoxLayout(kpi_card)
        kpi_layout.setContentsMargins(20, 10, 20, 10)
        kpi_layout.setSpacing(32)

        def _kpi_block(label_text, sub_text):
            block = QFrame()
            bl = QVBoxLayout(block)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(1)
            lab = QLabel(label_text)
            lab.setStyleSheet(f"color: {SUBTLE}; font-size: 12px; letter-spacing: 0.5px;")
            val = QLabel("$0.00")
            val.setStyleSheet(f"color: {TEXT}; font-size: 22px; font-weight: 700;")
            sub = QLabel(sub_text)
            sub.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            bl.addWidget(lab)
            bl.addWidget(val)
            bl.addWidget(sub)
            block._val_label = val
            return block

        self.wf_in_kpi = _kpi_block("Wealthfront In", "All time inflows")
        self.wf_out_kpi = _kpi_block("Wealthfront Out", "All time outflows")
        self.wf_net_kpi = _kpi_block("Wealthfront Net", "All time net")
        kpi_layout.addWidget(self.wf_in_kpi)
        kpi_layout.addWidget(self.wf_out_kpi)
        kpi_layout.addWidget(self.wf_net_kpi)
        self._apply_shadow(kpi_card, radius=18, blur=12, y=2)
        layout.addWidget(kpi_card)

        # Monthly breakdown table
        table_card = QFrame()
        table_card.setObjectName("Card")
        tlay = QVBoxLayout(table_card)
        tlay.setContentsMargins(16, 12, 16, 16)
        tlay.setSpacing(8)

        tbl_title = QLabel("Monthly Breakdown")
        tbl_title.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 13px; font-weight: 600;")
        tlay.addWidget(tbl_title)

        self.flow_tree = QTreeWidget()
        self.flow_tree.setHeaderLabels(["Month", "Description", "Inflow", "Outflow"])
        self.flow_tree.setAlternatingRowColors(False)
        self.flow_tree.setSortingEnabled(False)
        self.flow_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.flow_tree.setMinimumHeight(300)
        self.flow_tree.header().setStretchLastSection(True)

        # Reduced text size for Activity (was too big)
        tree_font = self.flow_tree.font()
        tree_font.setPointSize(14)
        self.flow_tree.setFont(tree_font)
        header = self.flow_tree.header()
        hfont = header.font()
        hfont.setPointSize(13)
        header.setFont(hfont)
        self.flow_tree.setColumnWidth(0, 170)  # more room for Month column

        tlay.addWidget(self.flow_tree, 1)
        self._apply_shadow(table_card, radius=18, blur=12)
        layout.addWidget(table_card, 1)

        # === Polished action bar (like the Holdings page) ===
        actions = QHBoxLayout()
        actions.setSpacing(10)

        btn_sync = QPushButton("Sync Wealthfront")
        btn_sync.setObjectName("PrimaryButton")
        btn_sync.clicked.connect(self.launch_wealthfront_bridge)

        btn_reload = QPushButton("Reload")
        btn_reload.clicked.connect(self._reload_for_activity)

        actions.addWidget(btn_sync)
        actions.addWidget(btn_reload)
        actions.addStretch(1)
        note = QLabel("Data comes from Wealthfront Plaid caches (cash_transactions) and matching imported CSVs.")
        note.setStyleSheet(f"color: {SUBTLE}; font-size: 13px;")
        actions.addWidget(note)
        layout.addLayout(actions)

        return page

    def _build_holdings_page(self) -> QWidget:
        """Premium, clean holdings view (Robinhood / Wealthfront / Apple quality).
        Self-contained page (no global hero), rich header, properly sized table, clear actions.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        # === Rich header ===
        header = QFrame()
        header.setObjectName("HoldingsTop")
        hlay = QVBoxLayout(header)
        hlay.setContentsMargins(24, 18, 24, 18)
        hlay.setSpacing(6)

        title_row = QHBoxLayout()
        self.holdings_title = QLabel("Holdings")
        self.holdings_title.setStyleSheet(f"color: {TEXT}; font-size: 22px; font-weight: 700; letter-spacing: -0.3px;")
        title_row.addWidget(self.holdings_title)
        title_row.addStretch(1)
        hlay.addLayout(title_row)

        # Center the main total (market value) and sub
        total_h = QHBoxLayout()
        total_h.addStretch(1)
        self.holdings_total = QLabel("$0.00")
        self.holdings_total.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 36px; font-weight: 700; letter-spacing: -1px;")
        total_h.addWidget(self.holdings_total)
        total_h.addStretch(1)
        hlay.addLayout(total_h)

        sub_h = QHBoxLayout()
        sub_h.addStretch(1)
        self.holdings_sub = QLabel("Loading holdings…")
        self.holdings_sub.setStyleSheet(f"color: {MUTED}; font-size: 14px;")
        sub_h.addWidget(self.holdings_sub)
        sub_h.addStretch(1)
        hlay.addLayout(sub_h)

        # Source breakdown row (populated in render)
        self.holdings_breakdown = QHBoxLayout()
        self.holdings_breakdown.setSpacing(16)
        hlay.addLayout(self.holdings_breakdown)

        self._apply_shadow(header, radius=22, blur=16)
        layout.addWidget(header)

        # === Holdings table in its own card ===
        table_card = QFrame()
        table_card.setObjectName("Card")
        tlay = QVBoxLayout(table_card)
        tlay.setContentsMargins(18, 14, 18, 16)
        tlay.setSpacing(10)

        tbl_label = QLabel("Holdings (sorted by market value)")
        tbl_label.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 15px; font-weight: 600;")
        tbl_label.setAlignment(Qt.AlignCenter)
        tlay.addWidget(tbl_label)

        self.holdings_table = QTableWidget(0, 7)
        self.holdings_table.setHorizontalHeaderLabels([
            "Source", "Symbol", "Shares", "Price", "Market Value", "Unrealized P/L", "% of Total"
        ])
        # Center the "Market Value" header (col 4) to match centered values
        if self.holdings_table.horizontalHeaderItem(4):
            self.holdings_table.horizontalHeaderItem(4).setTextAlignment(Qt.AlignCenter)
        self.holdings_table.verticalHeader().setVisible(False)
        self.holdings_table.setShowGrid(False)
        self.holdings_table.setAlternatingRowColors(False)
        self.holdings_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        hdr = self.holdings_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setHighlightSections(False)

        # Bigger fonts for holdings table
        tbl_font = self.holdings_table.font()
        tbl_font.setPointSize(16)
        self.holdings_table.setFont(tbl_font)
        h_font = self.holdings_table.horizontalHeader().font()
        h_font.setPointSize(15)
        self.holdings_table.horizontalHeader().setFont(h_font)

        tlay.addWidget(self.holdings_table, 1)

        self._apply_shadow(table_card, radius=18, blur=14)
        layout.addWidget(table_card, 1)

        # === Polished action bar ===
        actions = QHBoxLayout()
        actions.setSpacing(10)

        btn_rh = QPushButton("Reload Robinhood")
        btn_rh.clicked.connect(self.reload_robinhood_holdings)

        btn_wf = QPushButton("Sync Wealthfront")
        btn_wf.setObjectName("PrimaryButton")
        btn_wf.clicked.connect(self.launch_wealthfront_bridge)

        btn_all = QPushButton("Reload All")
        btn_all.clicked.connect(self.reload_all_holdings)

        actions.addWidget(btn_rh)
        actions.addWidget(btn_wf)
        actions.addStretch(1)
        actions.addWidget(btn_all)

        layout.addLayout(actions)

        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        card = QFrame()
        card.setObjectName("SettingsCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        title = QLabel("Settings")
        title.setStyleSheet(f"color: {TEXT}; font-size: 18px; font-weight: 700;")
        lay.addWidget(title)
        desc = QLabel("The app remembers your last CSV and cache path locally.")
        desc.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(desc)
        plaid_title = QLabel("Plaid / Wealthfront")
        plaid_title.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 14px; font-weight: 700;")
        lay.addWidget(plaid_title)
        self.plaid_client_id = QLineEdit(str(self.settings.get("plaid_client_id", "")))
        self.plaid_client_id.setPlaceholderText("Plaid client ID")
        lay.addWidget(self.plaid_client_id)
        self.plaid_secret = QLineEdit(str(self.settings.get("plaid_secret", "")))
        self.plaid_secret.setPlaceholderText("Plaid secret")
        self.plaid_secret.setEchoMode(QLineEdit.Password)
        lay.addWidget(self.plaid_secret)
        self.plaid_env = QComboBox()
        self.plaid_env.addItems(["development", "production", "sandbox"])
        current_env = str(self.settings.get("plaid_env", "development")).strip().lower()
        if current_env:
            idx = self.plaid_env.findText(current_env)
            if idx >= 0:
                self.plaid_env.setCurrentIndex(idx)
        lay.addWidget(self.plaid_env)
        self.plaid_cache_path = QLineEdit(str(self.settings.get("wealthfront_cache", DEFAULT_WEALTHFRONT_CACHE)))
        self.plaid_cache_path.setPlaceholderText("Wealthfront cache path")
        lay.addWidget(self.plaid_cache_path)
        btn_save_plaid = QPushButton("Save Plaid settings")
        btn_save_plaid.setObjectName("PrimaryButton")
        btn_save_plaid.clicked.connect(self.save_plaid_settings)
        lay.addWidget(btn_save_plaid)
        btn_bridge = QPushButton("Open Wealthfront bridge")
        btn_bridge.clicked.connect(self.launch_wealthfront_bridge)
        lay.addWidget(btn_bridge)
        btn_sync = QPushButton("Sync Wealthfront now")
        btn_sync.clicked.connect(self.sync_wealthfront_now)
        lay.addWidget(btn_sync)

        btn_reset = QPushButton("Reset Plaid link (fix 'cursor not associated' errors)")
        btn_reset.clicked.connect(self.reset_plaid_link)
        lay.addWidget(btn_reset)

        # === Robinhood Plaid Section ===
        rh_plaid_title = QLabel("Plaid / Robinhood (Credit Card or Account)")
        rh_plaid_title.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 14px; font-weight: 700;")
        lay.addWidget(rh_plaid_title)
        rh_plaid_desc = QLabel("Link your Robinhood account/credit card via Plaid (optional). Use the bridge, set cache path, save, connect via Plaid Link, then Sync. (Robinhood data is not shown on the Activity tab.)")
        rh_plaid_desc.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        lay.addWidget(rh_plaid_desc)

        self.robinhood_plaid_cache_path = QLineEdit(str(self.settings.get("robinhood_plaid_cache", str(ROOT / "robinhood-plaid-cache.json"))))
        self.robinhood_plaid_cache_path.setPlaceholderText("Robinhood Plaid cache path")
        lay.addWidget(self.robinhood_plaid_cache_path)

        btn_save_rh_plaid = QPushButton("Save Robinhood Plaid settings")
        btn_save_rh_plaid.setObjectName("PrimaryButton")
        btn_save_rh_plaid.clicked.connect(self.save_robinhood_plaid_settings)
        lay.addWidget(btn_save_rh_plaid)

        btn_rh_bridge = QPushButton("Open Robinhood Plaid bridge")
        btn_rh_bridge.clicked.connect(self.launch_robinhood_plaid_bridge)
        lay.addWidget(btn_rh_bridge)

        btn_rh_sync = QPushButton("Sync Robinhood Plaid now")
        btn_rh_sync.clicked.connect(self.sync_robinhood_plaid_now)
        lay.addWidget(btn_rh_sync)

        # === Robinhood Cache ===
        robin_title = QLabel("Robinhood Cache (holdings & orders)")
        robin_title.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 14px; font-weight: 700;")
        lay.addWidget(robin_title)
        self.robinhood_cache_path = QLineEdit(str(self.settings.get("last_cache", str(DEFAULT_ROBINHOOD_CACHE))))
        self.robinhood_cache_path.setPlaceholderText("robinhood-cache.json")
        lay.addWidget(self.robinhood_cache_path)
        btn_save_robin_cache = QPushButton("Save Robinhood cache path")
        btn_save_robin_cache.clicked.connect(self.save_robinhood_cache_path)
        lay.addWidget(btn_save_robin_cache)
        btn_reload_robin = QPushButton("Reload Robinhood cache")
        btn_reload_robin.clicked.connect(self.reload_robinhood_cache)
        lay.addWidget(btn_reload_robin)

        # === Manual Assets Section ===
        manual_title = QLabel("Manual Assets / Other Accounts")
        manual_title.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 14px; font-weight: 700;")
        lay.addWidget(manual_title)
        manual_desc = QLabel("Add pensions, TDAs, real estate, or any other assets not in Robinhood/Wealthfront.")
        manual_desc.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        lay.addWidget(manual_desc)

        self.manual_assets_table = QTableWidget(0, 2)
        self.manual_assets_table.setHorizontalHeaderLabels(["Account Name", "Current Value"])
        self.manual_assets_table.setColumnWidth(0, 200)
        self.manual_assets_table.setColumnWidth(1, 140)
        header = self.manual_assets_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.manual_assets_table.verticalHeader().setVisible(False)
        lay.addWidget(self.manual_assets_table)

        manual_btn_layout = QHBoxLayout()
        add_asset_btn = QPushButton("Add Asset")
        add_asset_btn.clicked.connect(self.add_manual_asset)
        remove_asset_btn = QPushButton("Remove Selected")
        remove_asset_btn.clicked.connect(self.remove_manual_asset)
        save_manual_btn = QPushButton("Save Manual Assets")
        save_manual_btn.setObjectName("PrimaryButton")
        save_manual_btn.clicked.connect(self.save_manual_assets)
        manual_btn_layout.addWidget(add_asset_btn)
        manual_btn_layout.addWidget(remove_asset_btn)
        manual_btn_layout.addStretch(1)
        manual_btn_layout.addWidget(save_manual_btn)
        lay.addLayout(manual_btn_layout)

        # Load existing manual assets into the table
        self.load_manual_assets_into_table()

        btn1 = QPushButton("Open last CSV folder")
        btn1.clicked.connect(self.open_last_folder)
        lay.addWidget(btn1)
        btn2 = QPushButton("Open cache file")
        btn2.clicked.connect(self.browse_cache)
        lay.addWidget(btn2)
        btn3 = QPushButton("Import sample CSV")
        btn3.setObjectName("PrimaryButton")
        btn3.clicked.connect(self.load_sample)
        lay.addWidget(btn3)
        lay.addStretch(1)
        self._apply_shadow(card, radius=24, blur=22, y=2)
        layout.addWidget(card, 1)
        return page

    # Legacy scroll helper removed for simplified UI

    def _load_startup_state(self) -> None:
        imports = self.settings.get("last_imports", [])
        if isinstance(imports, list):
            for item in imports:
                path = Path(item)
                if path.exists():
                    self._load_transactions([path])
                    break
        self.load_snapshot(silent=True)
        self.load_wealthfront_snapshot(silent=True)
        self.load_robinhood_plaid_snapshot(silent=True)
        self.net_worth_history = self._compute_net_worth_history()
        # Only load sample if truly no real data from CSV or Wealthfront link (even if no txns yet)
        if not self.transactions and not self.wealthfront_snapshot:
            self.load_sample()
        self.show_view("dashboard")

    def show_view(self, key: str) -> None:
        order = {"dashboard": 0, "activity": 1, "holdings": 2, "positions": 3, "settings": 4, "networth": 5}
        self.stack.setCurrentIndex(order.get(key, 0))
        for name, btn in self.nav_buttons.items():
            is_active = name == key
            btn.setChecked(is_active)
            if is_active:
                btn.setStyleSheet(
                    f"""
                    QToolButton {{
                        background: {GREEN_CARD_2};
                        color: {GOLD_SOFT};
                        border: 0px;
                        padding: 11px 16px 11px 20px;
                        border-radius: 14px;
                        text-align: left;
                        font-weight: 600;
                    }}
                    """
                )
            else:
                btn.setStyleSheet(
                    f"""
                    QToolButton {{
                        background: transparent;
                        color: {MUTED};
                        border: 0px;
                        padding: 11px 16px;
                        border-radius: 14px;
                        text-align: left;
                    }}
                    QToolButton:hover {{
                        background: {GREEN_CARD};
                        color: {TEXT};
                    }}
                    """
                )

        # Make hero contextual per tab (avoid always "Overview")
        titles = {
            "dashboard": ("Dashboard", "Allocation, cash & P/L overview"),
            "positions": ("Positions", "Detailed holdings from all accounts"),
            "networth": ("Net Worth", "History from transactions + current portfolio"),
            "activity": ("Activity", "Monthly inflows & outflows: Wealthfront"),
            "holdings": ("Holdings", "Consolidated portfolio view"),
            "settings": ("Settings", "Caches, Plaid and preferences"),
        }
        title, sub = titles.get(key, ("Overview", "Cash flow, holdings & insights. Drop CSVs anywhere to import."))
        self.hero_title.setText(title)
        self.hero_title.setStyleSheet(f"color: {TEXT}; font-size: 24px; font-weight: 700; letter-spacing: -0.4px;")
        self.hero_subtitle.setText(sub)
        self.hero_subtitle.setStyleSheet(f"color: {MUTED}; font-size: 13px;")

        # Tab-specific hero: hide on detail pages to avoid heavy top bar on Positions/Net Worth etc.
        self.hero.setVisible(key in ("dashboard", "settings"))

        if key == "dashboard":
            self.render_dashboard()
        elif key == "activity":
            self.refresh_transactions()
        elif key == "holdings":
            self.render_holdings()
        elif key == "positions":
            self.render_positions()
        elif key == "settings":
            self.load_manual_assets_into_table()
        elif key == "networth":
            self.render_networth()

    # ----- Apple-polished drag & drop support -----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".csv"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        # Optional: could dim hero if we had visual feedback
        event.accept()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local.lower().endswith(".csv"):
                paths.append(Path(local))
        if paths:
            event.acceptProposedAction()
            self._load_transactions(paths)
        else:
            event.ignore()

    def open_csv(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(self, "Select CSV files", "", "CSV Files (*.csv);;All Files (*)")
        if names:
            self._load_transactions([Path(name) for name in names])

    def open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select a folder with CSV files")
        if not folder:
            return
        paths = sorted(Path(folder).glob("*.csv"))
        if not paths:
            QMessageBox.information(self, APP_TITLE, "No CSV files found in that folder.")
            return
        self._load_transactions(paths)

    def browse_cache(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Select Robinhood cache JSON", str(DEFAULT_ROBINHOOD_CACHE.parent), "JSON Files (*.json);;All Files (*)")
        if name:
            self.cache_path.setText(name)
            self.load_snapshot()

    def browse_wealthfront_cache(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Select Wealthfront cache JSON", str(DEFAULT_WEALTHFRONT_CACHE.parent), "JSON Files (*.json);;All Files (*)")
        if name:
            self.wealthfront_cache_path.setText(name)
            if hasattr(self, 'plaid_cache_path'):
                self.plaid_cache_path.setText(name)
            self.load_wealthfront_snapshot()

    def open_last_folder(self) -> None:
        imports = self.settings.get("last_imports", [])
        if isinstance(imports, list) and imports:
            folder = Path(imports[0]).parent
            if folder.exists():
                os.startfile(str(folder))

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

    def save_plaid_settings(self, notify: bool = True) -> None:
        cache_path = Path(self.plaid_cache_path.text()).expanduser()
        self.settings["plaid_client_id"] = self.plaid_client_id.text().strip()
        self.settings["plaid_secret"] = self.plaid_secret.text().strip()
        self.settings["plaid_env"] = self.plaid_env.currentText().strip().lower()
        self.settings["wealthfront_cache"] = str(cache_path)
        save_settings(self.settings)
        if hasattr(self, 'wealthfront_cache_path'):
            self.wealthfront_cache_path.setText(str(cache_path))
        try:
            from wealthfront_plaid_common import ensure_state_defaults, load_state, save_state

            state = ensure_state_defaults(load_state())
            state["client_id"] = self.settings["plaid_client_id"]
            state["secret"] = self.settings["plaid_secret"]
            state["env"] = self.settings["plaid_env"]
            state["cache_path"] = str(cache_path)
            save_state(state)
        except Exception:
            pass
        self.load_wealthfront_snapshot(silent=True)
        if notify:
            QMessageBox.information(self, APP_TITLE, "Plaid settings saved.")

    def load_manual_assets(self):
        self.manual_assets = dict(self.settings.get("manual_assets", {}) or {})

    def load_manual_assets_into_table(self):
        if not hasattr(self, 'manual_assets_table') or self.manual_assets_table is None:
            return
        self.manual_assets_table.setRowCount(0)
        for name, value in getattr(self, 'manual_assets', {}).items():
            row = self.manual_assets_table.rowCount()
            self.manual_assets_table.insertRow(row)
            self.manual_assets_table.setItem(row, 0, QTableWidgetItem(str(name)))
            val_item = QTableWidgetItem(f"{value:.2f}")
            val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.manual_assets_table.setItem(row, 1, val_item)

    def save_manual_assets(self, notify: bool = True):
        self.manual_assets = {}
        for row in range(self.manual_assets_table.rowCount()):
            name_item = self.manual_assets_table.item(row, 0)
            value_item = self.manual_assets_table.item(row, 1)
            if name_item:
                name = name_item.text().strip()
                try:
                    value = float(value_item.text().strip()) if value_item else 0.0
                except:
                    value = 0.0
                if name:
                    self.manual_assets[name] = value
        self.settings["manual_assets"] = self.manual_assets
        save_settings(self.settings)
        # Refresh views that use the data
        self.net_worth_history = self._compute_net_worth_history()
        self.render_dashboard()
        self.render_positions()
        self.render_holdings()
        if notify:
            QMessageBox.information(self, APP_TITLE, "Manual assets saved.")

    def add_manual_asset(self):
        row = self.manual_assets_table.rowCount()
        self.manual_assets_table.insertRow(row)
        self.manual_assets_table.setItem(row, 0, QTableWidgetItem("New Asset"))
        val_item = QTableWidgetItem("0")
        val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.manual_assets_table.setItem(row, 1, val_item)

    def remove_manual_asset(self):
        rows = sorted({item.row() for item in self.manual_assets_table.selectedItems()}, reverse=True)
        for row in rows:
            self.manual_assets_table.removeRow(row)

    def _launch_wealthfront_script(self, script_name: str, open_browser: bool = False, extra_args: list = None) -> None:
        script = ROOT / script_name
        if not script.exists():
            QMessageBox.warning(self, APP_TITLE, f"Missing script: {script.name}")
            return
        cmd = [pythonw_executable(), str(script)]
        if extra_args:
            cmd.extend(extra_args)
        if open_browser and not is_port_open("127.0.0.1", WEALTHFRONT_BRIDGE_PORT):
            try:
                subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                QMessageBox.critical(self, APP_TITLE, f"Could not launch {script.name}: {exc}")
                return
        elif not open_browser:
            try:
                subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                QMessageBox.critical(self, APP_TITLE, f"Could not launch {script.name}: {exc}")
                return

    def launch_wealthfront_bridge(self) -> None:
        self.save_plaid_settings(notify=False)
        self._launch_wealthfront_script("wealthfront_plaid_bridge.py", open_browser=True)
        for _ in range(20):
            if is_port_open("127.0.0.1", WEALTHFRONT_BRIDGE_PORT):
                break
            time.sleep(0.1)
        webbrowser.open(WEALTHFRONT_BRIDGE_URL)

    def sync_wealthfront_now(self) -> None:
        self.save_plaid_settings(notify=False)
        script = ROOT / "wealthfront_sync.py"
        if not script.exists():
            QMessageBox.warning(self, APP_TITLE, "Missing wealthfront_sync.py.")
            return
        try:
            proc = subprocess.run(
                [pythonw_executable(), str(script)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if proc.returncode != 0:
                detail = proc.stderr.strip() or proc.stdout.strip() or "Unknown sync failure."
                if "cursor not associated with access_token" in detail.lower():
                    detail = (detail + "\n\n"
                              "This usually means a stale sync cursor from a previous link.\n"
                              "Fix: Re-run the Wealthfront bridge and Connect again (or delete "
                              "'transactions_cursor' from ~/.finance_lens_plaid.json).")
                QMessageBox.critical(self, APP_TITLE, detail)
                return
            self.load_wealthfront_snapshot()
            self.refresh_transactions()
            QMessageBox.information(self, APP_TITLE, proc.stdout.strip() or "Wealthfront sync complete.")
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"Could not sync Wealthfront: {exc}")

    def reset_plaid_link(self) -> None:
        try:
            from wealthfront_plaid_common import reset_plaid_link as do_reset
            do_reset()
            QMessageBox.information(
                self, APP_TITLE,
                "Plaid link data cleared (access token + cursor).\n\n"
                "Now re-open the bridge and click Connect to link again."
            )
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"Could not reset: {exc}")

    def save_robinhood_plaid_settings(self, notify: bool = True) -> None:
        cache_path = Path(self.robinhood_plaid_cache_path.text()).expanduser()
        self.settings["robinhood_plaid_cache"] = str(cache_path)
        save_settings(self.settings)
        if notify:
            QMessageBox.information(self, APP_TITLE, "Robinhood Plaid settings saved.")

    def launch_robinhood_plaid_bridge(self):
        self.save_robinhood_plaid_settings(notify=False)
        cache_path = Path(self.robinhood_plaid_cache_path.text()).expanduser()
        self._launch_wealthfront_script("wealthfront_plaid_bridge.py", open_browser=True, extra_args=["--cache-path", str(cache_path)])

    def sync_robinhood_plaid_now(self):
        self.save_robinhood_plaid_settings(notify=False)
        script = ROOT / "wealthfront_sync.py"
        if not script.exists():
            QMessageBox.warning(self, APP_TITLE, "Missing wealthfront_sync.py.")
            return
        try:
            proc = subprocess.run(
                [pythonw_executable(), str(script)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if proc.returncode != 0:
                detail = proc.stderr.strip() or proc.stdout.strip() or "Unknown sync failure."
                if "cursor not associated with access_token" in detail.lower():
                    detail = (detail + "\n\n"
                              "Stale Plaid cursor (common after re-linking).\n"
                              "Solution: Use the Robinhood Plaid bridge to Connect/Link again.")
                QMessageBox.critical(self, APP_TITLE, detail)
                return
            self.load_robinhood_plaid_snapshot()
            QMessageBox.information(self, APP_TITLE, proc.stdout.strip() or "Robinhood Plaid sync complete.")
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"Could not sync Robinhood Plaid: {exc}")

    def save_robinhood_cache_path(self):
        path = Path(self.robinhood_cache_path.text()).expanduser()
        self.settings["last_cache"] = str(path)
        save_settings(self.settings)
        if hasattr(self, 'cache_path'):
            self.cache_path.setText(str(path))
        QMessageBox.information(self, APP_TITLE, "Robinhood cache path saved.")

    def reload_robinhood_cache(self):
        if hasattr(self, 'cache_path'):
            self.cache_path.setText(self.robinhood_cache_path.text())
        self.load_snapshot(silent=False)

    def _load_transactions(self, paths: list[Path]) -> None:
        loaded: list[Transaction] = []
        for path in paths:
            try:
                loaded.extend(load_transactions(path))
            except Exception as exc:
                QMessageBox.critical(self, APP_TITLE, f"{path.name}: {exc}")
                return
        self.transactions = sorted(loaded, key=lambda tx: tx.date)
        self.filtered = list(self.transactions)
        self.settings["last_imports"] = [str(p) for p in paths]
        save_settings(self.settings)
        self._add_plaid_cash_transactions()
        self.net_worth_history = self._compute_net_worth_history()
        self.render_dashboard()
        self.refresh_transactions()
        self.render_holdings()
        self.render_positions()



    def _on_top_change(self, value: int) -> None:
        self.top_count = int(value)
        self.top_value.setText(str(self.top_count))
        self.render_dashboard()

    def render_dashboard(self) -> None:
        tx_data = summary_for(self.filtered if self.filtered is not None else self.transactions)
        portfolio_val = compute_portfolio_value(self.snapshot, self.wealthfront_snapshot, getattr(self, 'manual_assets', None))

        # KPIs
        self._set_metric(self.kpi_net, fmt_money(portfolio_val))

        # Compute simple 1-day change using previous_close when available (RH quotes)
        day_change = 0.0
        if self.snapshot:
            for poss in self.snapshot.get("equity_positions", {}).values():
                for p in poss:
                    q = p.get("quote", {})
                    last = float(q.get("last_trade_price", 0) or 0)
                    prev = float(q.get("previous_close", last) or last)
                    qty = float(p.get("quantity", 0) or 0)
                    day_change += (last - prev) * qty
        self._set_metric(self.kpi_day, fmt_money(day_change))

        # Holdings total unrealized P/L + %
        pl_total = 0.0
        if self.snapshot:
            for poss in self.snapshot.get("equity_positions", {}).values():
                for p in poss:
                    q = p.get("quote", {})
                    last = float(q.get("last_trade_price", 0) or 0)
                    qty = float(p.get("quantity", 0) or 0)
                    avg = float(p.get("average_buy_price", 0) or 0)
                    pl_total += (last * qty) - (avg * qty)
        pl_pct = (pl_total / portfolio_val * 100.0) if portfolio_val > 1 else 0.0
        self._set_metric(self.kpi_pl, fmt_money(pl_total), f"{pl_pct:+.1f}%")

        cash_approx = 0.0
        if self.snapshot:
            for acc in self.snapshot.get("accounts", []):
                for k in ("cash", "buying_power"):
                    try:
                        cash_approx += float(acc.get(k, 0) or 0)
                    except (ValueError, TypeError):
                        pass
        self._set_metric(self.kpi_cash, fmt_money(cash_approx or tx_data.get("net", 0)))

        # Prepare allocation (all assets: equities, holdings, cash, manual)
        alloc = {}
        if self.snapshot:
            eq_v = 0.0
            for poss in self.snapshot.get("equity_positions", {}).values():
                for p in poss:
                    q = p.get("quote", {})
                    eq_v += float(p.get("quantity", 0)) * float(q.get("last_trade_price", 0) or 0)
            if eq_v: alloc["Equities (RH)"] = eq_v
            # options
            opt_v = 0.0
            for poss in self.snapshot.get("option_positions", {}).values():
                for p in poss:
                    q = p.get("quote", {})
                    mark = float(q.get("mark_price", 0) or 0)
                    qty = float(p.get("quantity", 0) or 0)
                    opt_v += qty * mark * 100
            if opt_v: alloc["Options (RH)"] = opt_v
        if self.wealthfront_snapshot:
            wf_v = sum(float(h.get("institution_value") or h.get("market_value") or 0) for h in self.wealthfront_snapshot.get("holdings", []))
            if wf_v: alloc["Wealthfront"] = wf_v
        if cash_approx > 0:
            alloc["Cash"] = cash_approx

        # Include Wealthfront cash (from accounts) in Cash so total matches portfolio
        if self.wealthfront_snapshot:
            for acc in self.wealthfront_snapshot.get("accounts", []):
                bal = 0.0
                balances = acc.get("balances") or acc
                for k in ("current", "available", "cash", "balance"):
                    v = balances.get(k) if isinstance(balances, dict) else acc.get(k)
                    if v is not None:
                        try:
                            bval = float(v)
                            if bval > 0:
                                bal = bval
                                break
                        except (ValueError, TypeError):
                            pass
                if bal > 0:
                    alloc["Cash"] = alloc.get("Cash", 0) + bal

        # Manual assets from Settings
        for name, val in getattr(self, 'manual_assets', {}).items():
            if val > 0:
                alloc[name] = val

        alloc_total = portfolio_val  # ensure matches the Net Worth KPI / full assets
        if hasattr(self, 'alloc_title'):
            self.alloc_title.setText(f"Asset allocation  •  {fmt_money(alloc_total)}")
        alloc_rows = sorted(alloc.items(), key=lambda x: -x[1])
        if hasattr(self, 'alloc_graph'):
            self.alloc_graph.set_rows(alloc_rows)

        # Value by account graph (equities + cash + holdings + manual)
        acc_data = {}
        if self.snapshot:
            for acc in self.snapshot.get("accounts", []):
                acc_v = 0.0
                aid = acc.get("account_number")
                for p in self.snapshot.get("equity_positions", {}).get(aid, []):
                    q = p.get("quote", {})
                    acc_v += float(p.get("quantity", 0)) * float(q.get("last_trade_price", 0) or 0)
                nick = acc.get("nickname") or acc.get("type", "RH")
                if acc_v > 0:
                    acc_data[nick] = acc_v
                # include cash for the account
                cash = 0.0
                for k in ("cash", "buying_power", "cash_balance", "available_cash", "balance"):
                    try:
                        c = float(acc.get(k, 0) or 0)
                        if c > 0:
                            cash += c
                            break
                    except (ValueError, TypeError):
                        pass
                if cash > 0:
                    acc_data[nick] = acc_data.get(nick, 0) + cash
        if self.wealthfront_snapshot:
            wf_tot = sum(float(h.get("institution_value") or h.get("market_value") or 0) for h in self.wealthfront_snapshot.get("holdings", []))
            wf_cash = 0.0
            for acc in self.wealthfront_snapshot.get("accounts", []):
                bal = 0.0
                b = acc.get("balances") or acc
                for k in ("current", "available", "cash", "balance"):
                    v = b.get(k) if isinstance(b, dict) else acc.get(k)
                    if v is not None:
                        try:
                            bval = float(v)
                            if bval > 0:
                                bal = bval
                                break
                        except (ValueError, TypeError):
                            pass
                if bal > 0:
                    wf_cash += bal
            total_wf = wf_tot + wf_cash
            if total_wf > 0:
                acc_data["Wealthfront"] = total_wf

        # Manual assets from Settings
        for name, val in getattr(self, 'manual_assets', {}).items():
            if val > 0:
                acc_data[name] = val

        acc_total = sum(acc_data.values())
        if hasattr(self, 'account_title'):
            self.account_title.setText(f"Value by account  •  {fmt_money(acc_total)}")
        acc_rows = sorted(acc_data.items(), key=lambda x: -x[1])
        if hasattr(self, 'account_graph'):
            self.account_graph.set_rows(acc_rows)

        # Holdings value as graph (bars for symbols)
        hold_val = {}
        hold_pl = {}
        if self.snapshot:
            for poss in self.snapshot.get("equity_positions", {}).values():
                for p in poss:
                    q = p.get("quote", {})
                    last = float(q.get("last_trade_price", 0) or 0)
                    qty = float(p.get("quantity", 0) or 0)
                    avg = float(p.get("average_buy_price", 0) or 0)
                    val = qty * last
                    pl = val - (qty * avg)
                    sym = p.get("symbol", "?")
                    if val > 0:
                        hold_val[sym] = val
                    hold_pl[sym] = pl
        if self.wealthfront_snapshot:
            for h in self.wealthfront_snapshot.get("holdings", []):
                sym = "WF"
                for s in self.wealthfront_snapshot.get("securities", []):
                    if str(s.get("security_id")) == str(h.get("security_id")):
                        sym = s.get("ticker_symbol", "WF")
                        break
                val = float(h.get("institution_value") or h.get("market_value") or 0)
                cost = float(h.get("cost_basis") or 0)
                pl = val - cost
                if val > 0:
                    hold_val[sym] = hold_val.get(sym, 0) + val
                hold_pl[sym] = hold_pl.get(sym, 0) + pl
        hold_val_total = sum(hold_val.values())
        if hasattr(self, 'holdings_graph_title'):
            self.holdings_graph_title.setText(f"Market value by symbol  •  {fmt_money(hold_val_total)}")
        hold_rows = sorted(hold_val.items(), key=lambda x: -x[1])
        if hasattr(self, 'holdings_graph'):
            self.holdings_graph.set_rows(hold_rows)
        # using graphs for cleaner look

        # Top categories graph (fills page space)
        if hasattr(self, 'top_categories_graph'):
            cats = tx_data.get("categories", [])[:5]
            # Clean ugly labels like "Income:Income"
            cleaned_cats = [(cat.replace("Income: ", "Income - ") if cat.startswith("Income: ") else cat, val) for cat, val in cats]
            self.top_categories_graph.set_rows(cleaned_cats)

        # Update sidebar
        self.sidebar_bridge.setText(self._bridge_status_text())

    def render_networth(self) -> None:
        if not self.net_worth_history:
            self.net_worth_history = self._compute_net_worth_history()
        data = self.net_worth_history or []
        if hasattr(self, 'networth_chart') and self.networth_chart:
            self.networth_chart.set_data(data)

        if data:
            current = data[-1][1]
            if hasattr(self, 'nw_amount'):
                self.nw_amount.setText(fmt_money(current))

            if len(data) > 1:
                first = data[0][1]
                delta = current - first
                pct = (delta / first * 100) if first != 0 else 0
                color = RH_GREEN if delta >= 0 else NEGATIVE
                if hasattr(self, 'nw_change'):
                    self.nw_change.setText(f"{fmt_money(delta)} ({pct:+.1f}%)")
                    self.nw_change.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: 600;")
            else:
                if hasattr(self, 'nw_change'):
                    self.nw_change.setText("")

            if hasattr(self, 'nw_range'):
                self.nw_range.setText("June 2026")
            if hasattr(self, 'nw_updated'):
                self.nw_updated.setText("Based on transaction history + current holdings")
        else:
            if hasattr(self, 'nw_amount'):
                self.nw_amount.setText("$0.00")
            if hasattr(self, 'nw_change'):
                self.nw_change.setText("")
            if hasattr(self, 'nw_range'):
                self.nw_range.setText("June 2026")

    def render_positions(self) -> None:
        """Populate the positions table on the separate Positions tab.
        Use all available data (no artificial limit).
        """
        self.positions_table.setRowCount(0)
        hold_val = {}
        hold_pl = {}
        if self.snapshot:
            for poss in self.snapshot.get("equity_positions", {}).values():
                for p in poss:
                    q = p.get("quote", {})
                    last = float(q.get("last_trade_price", 0) or 0)
                    qty = float(p.get("quantity", 0) or 0)
                    avg = float(p.get("average_buy_price", 0) or 0)
                    val = qty * last
                    pl = val - (qty * avg)
                    sym = p.get("symbol", "?")
                    if val > 0:
                        hold_val[sym] = val
                    hold_pl[sym] = pl
        if self.wealthfront_snapshot:
            for h in self.wealthfront_snapshot.get("holdings", []):
                sym = "WF"
                for s in self.wealthfront_snapshot.get("securities", []):
                    if str(s.get("security_id")) == str(h.get("security_id")):
                        sym = s.get("ticker_symbol", "WF")
                        break
                val = float(h.get("institution_value") or h.get("market_value") or 0)
                cost = float(h.get("cost_basis") or 0)
                pl = val - cost
                if val > 0:
                    hold_val[sym] = hold_val.get(sym, 0) + val
                hold_pl[sym] = hold_pl.get(sym, 0) + pl

        # Manual assets
        for name, val in getattr(self, 'manual_assets', {}).items():
            if val > 0:
                hold_val[name] = val
                hold_pl[name] = 0

        total_val = sum(hold_val.values())
        if hasattr(self, "positions_total") and self.positions_total:
            self.positions_total.setText(fmt_money(total_val))

        for sym, val in sorted(hold_val.items(), key=lambda x: -x[1]):
            pl = hold_pl.get(sym, 0)
            r = self.positions_table.rowCount()
            self.positions_table.insertRow(r)
            self.positions_table.setItem(r, 0, QTableWidgetItem(sym))
            self.positions_table.setItem(r, 1, QTableWidgetItem("Combined"))
            self.positions_table.setItem(r, 2, QTableWidgetItem("-"))
            self.positions_table.setItem(r, 3, QTableWidgetItem("-"))
            self.positions_table.setItem(r, 4, QTableWidgetItem("-"))
            it = QTableWidgetItem(fmt_money(val))
            it.setTextAlignment(Qt.AlignRight)
            self.positions_table.setItem(r, 5, it)
            it2 = QTableWidgetItem(fmt_money(pl))
            it2.setTextAlignment(Qt.AlignRight)
            it2.setForeground(QColor(POSITIVE if pl >= 0 else NEGATIVE))
            self.positions_table.setItem(r, 6, it2)
            self.positions_table.setItem(r, 7, QTableWidgetItem(f"{(pl / val * 100 if val else 0):.1f}%"))

    def refresh_transactions(self) -> None:
        """Populate the monthly cash-flow Activity view (Wealthfront only)."""
        if not hasattr(self, "flow_tree") or self.flow_tree is None:
            return
        # Auto-load/reload Wealthfront data if no WF txns yet (in case tab viewed before load/sync or after external link)
        has_wf = any('wealthfront' in (getattr(tx, 'account', '') or '').lower() for tx in (self.transactions or []))
        if not has_wf:
            self.load_wealthfront_snapshot(silent=True)
        self._populate_flow_table()

    def _reload_for_activity(self) -> None:
        self.load_wealthfront_snapshot(silent=True)
        self.load_snapshot(silent=True)
        self._populate_flow_table()

    def _populate_flow_table(self) -> None:
        """Compute and display monthly inflows/outflows for Wealthfront,
        with individual transactions listed under each month.
        """
        if not hasattr(self, "flow_tree") or self.flow_tree is None:
            return

        from collections import defaultdict
        wf_month_agg = defaultdict(lambda: {"in": 0.0, "out": 0.0})
        wf_tx_by_month = defaultdict(list)

        for tx in (self.transactions or []):
            acc = (getattr(tx, "account", "") or "").lower()
            if not acc:
                continue
            # Only Wealthfront (Plaid uses "Wealthfront Cash/Card", CSV may use similar)
            is_wf = "wealthfront" in acc
            if not is_wf:
                continue

            mkey = month_key(tx.date)
            amt = float(tx.amount or 0)
            if amt > 0:
                wf_month_agg[mkey]["in"] += amt
            else:
                wf_month_agg[mkey]["out"] += -amt
            wf_tx_by_month[mkey].append(tx)

        all_months = sorted(wf_month_agg.keys(), reverse=True)

        # Lifetime aggregates for KPIs
        wf_in = sum(v["in"] for v in wf_month_agg.values())
        wf_out = sum(v["out"] for v in wf_month_agg.values())
        wf_net = wf_in - wf_out

        if hasattr(self, "wf_in_kpi"):
            self._set_metric(self.wf_in_kpi, fmt_money(wf_in))
        if hasattr(self, "wf_out_kpi"):
            self._set_metric(self.wf_out_kpi, fmt_money(wf_out))
        if hasattr(self, "wf_net_kpi"):
            self._set_metric(self.wf_net_kpi, fmt_money(wf_net))

        # Build tree: months as parents, txns as children
        self.flow_tree.clear()
        self.flow_tree.setColumnWidth(0, 170)  # more room for Month
        self.flow_tree.setColumnWidth(1, 340)
        self.flow_tree.setColumnWidth(2, 100)
        self.flow_tree.setColumnWidth(3, 100)

        for m in all_months:
            wi = wf_month_agg[m]["in"]
            wo = wf_month_agg[m]["out"]
            wnet = wi - wo

            # Parent row with summary
            month_item = QTreeWidgetItem([
                m,
                f"Summary (Net: {fmt_money(wnet)})",
                fmt_money(wi),
                fmt_money(wo)
            ])
            # Style parent row as bold
            for col in range(4):
                font = month_item.font(col)
                font.setBold(True)
                month_item.setFont(col, font)
            # Color the summary amounts
            month_item.setForeground(2, QColor(POSITIVE))
            month_item.setForeground(3, QColor(NEGATIVE))

            # Add child transactions for this month (oldest first within month)
            txs = sorted(wf_tx_by_month.get(m, []), key=lambda t: t.date)
            for tx in txs:
                date_str = tx.date.strftime("%b %d")
                desc = (tx.description or "Transaction")[:60]
                amt = float(tx.amount or 0)
                inflow = fmt_money(amt) if amt > 0 else ""
                outflow = fmt_money(-amt) if amt < 0 else ""
                child = QTreeWidgetItem([date_str, desc, inflow, outflow])
                if amt >= 0:
                    child.setForeground(2, QColor(POSITIVE))
                else:
                    child.setForeground(3, QColor(NEGATIVE))
                month_item.addChild(child)

            self.flow_tree.addTopLevelItem(month_item)
            month_item.setExpanded(True)  # show transactions by default

        if not all_months:
            # Empty state
            item = QTreeWidgetItem([
                "No Wealthfront transactions",
                "Use 'Sync Wealthfront now' in Settings (or run the bridge sync) to populate cash flow.",
                "",
                ""
            ])
            item.setForeground(0, QColor(MUTED))
            self.flow_tree.addTopLevelItem(item)

    def load_snapshot(self, silent: bool = False) -> None:
        path_str = getattr(self, 'cache_path', None)
        path = Path(path_str.text() if path_str else DEFAULT_ROBINHOOD_CACHE).expanduser()
        if not path.exists() or not str(path).strip():
            path = DEFAULT_ROBINHOOD_CACHE
        if not path.exists():
            self.snapshot = None
            if not silent:
                QMessageBox.warning(self, APP_TITLE, f"Could not find cache file: {path}")
            self.render_holdings()
            return
        try:
            self.snapshot = load_json(path)
            self.settings["last_cache"] = str(path)
            save_settings(self.settings)
            self._add_robinhood_orders_as_transactions()
            self.net_worth_history = self._compute_net_worth_history()
            self.render_holdings()
            self.render_positions()
            self.refresh_transactions()
        except Exception as exc:
            self.snapshot = None
            if not silent:
                QMessageBox.critical(self, APP_TITLE, f"Could not read cache file: {exc}")

    def _add_robinhood_orders_as_transactions(self) -> None:
        """Convert filled equity orders from robinhood-cache.json into app Transactions.
        (These show as account=Robinhood; the Activity cash-flow tab focuses on WF/RH credit card Plaid txns.)
        """
        orders = self.snapshot.get("orders", []) if self.snapshot else []
        if not orders:
            return
        new_txs = []
        for o in orders:
            if o.get("state") != "filled":
                continue
            symbol = o.get("symbol", "?")
            side = o.get("side", "")
            qty = float(o.get("cumulative_quantity") or o.get("quantity", 0) or 0)
            avg_price = float(o.get("average_price") or 0)
            fees = float(o.get("fees") or 0)
            dollar_amount = o.get("dollar_based_amount")
            if dollar_amount and isinstance(dollar_amount, dict):
                amount = float(dollar_amount.get("amount", 0))
            else:
                amount = qty * avg_price
            if side == "buy":
                app_amount = - (amount + fees)
            else:
                app_amount = amount - fees
            created = o.get("created_at", "") or o.get("last_transaction_at", "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except:
                dt = datetime.now()
            description = f"{side.upper()} {symbol} {qty:.4f} @ ${avg_price:.2f}"
            if fees > 0:
                description += f" (fee ${fees:.2f})"
            tx = Transaction(
                date=dt,
                description=description,
                amount=app_amount,
                category="Robinhood Trade",
                account="Robinhood",
                balance=None
            )
            new_txs.append(tx)
        if new_txs:
            base = [tx for tx in (self.transactions or []) if getattr(tx, 'account', '') != "Robinhood"]
            all_txs = base + new_txs
            self.transactions = sorted(all_txs, key=lambda tx: tx.date)
            self.filtered = list(self.transactions)

    def load_wealthfront_snapshot(self, silent: bool = False) -> None:
        # Prefer the path from Settings UI if available (widgets may lag settings)
        path_str = None
        if hasattr(self, 'plaid_cache_path') and self.plaid_cache_path.text().strip():
            path_str = self.plaid_cache_path.text()
        elif hasattr(self, 'wealthfront_cache_path') and self.wealthfront_cache_path.text().strip():
            path_str = self.wealthfront_cache_path.text()
        path = Path(path_str or self.settings.get("wealthfront_cache", DEFAULT_WEALTHFRONT_CACHE)).expanduser()
        if not path.exists():
            self.wealthfront_snapshot = None
            if not silent:
                QMessageBox.warning(self, APP_TITLE, f"Could not find Wealthfront cache file: {path}")
            self.render_holdings()
            return
        try:
            self.wealthfront_snapshot = load_json(path)
            self.settings["wealthfront_cache"] = str(path)
            save_settings(self.settings)
            # Keep widgets in sync
            if hasattr(self, 'wealthfront_cache_path'):
                self.wealthfront_cache_path.setText(str(path))
            if hasattr(self, 'plaid_cache_path'):
                self.plaid_cache_path.setText(str(path))
            self._add_plaid_cash_transactions()
            self.net_worth_history = self._compute_net_worth_history()
            self.render_holdings()
            self.render_positions()
            self.refresh_transactions()  # ensure Activity cashflow updates
        except Exception as exc:
            self.wealthfront_snapshot = None
            if not silent:
                QMessageBox.critical(self, APP_TITLE, f"Could not read Wealthfront cache file: {exc}")

    def load_robinhood_plaid_snapshot(self, silent: bool = False) -> None:
        """Load Plaid cache for Robinhood credit card (or other Robinhood accounts).
        (Note: Robinhood is no longer shown on the Activity cash flow page.)
        """
        path_str = self.settings.get("robinhood_plaid_cache", str(ROOT / "robinhood-plaid-cache.json"))
        path = Path(path_str).expanduser()
        if not path.exists():
            # Fallback: user linked Robinhood via the wealthfront bridge and copied/renamed
            fallback = ROOT / "wealthfront-cache.json"
            if fallback.exists():
                try:
                    temp = load_json(fallback)
                    # Detect if this cache looks like Robinhood (has robinhood accounts or institution)
                    accs = temp.get("accounts", [])
                    if any("robinhood" in str(a).lower() or "investing" in str(a.get("nickname","")).lower() for a in accs):
                        self.robinhood_plaid_snapshot = temp
                        self.robinhood_plaid_snapshot["account_label"] = "Robinhood"
                        self._add_plaid_cash_transactions()
                        self.net_worth_history = self._compute_net_worth_history()
                        self.refresh_transactions()
                        return
                except:
                    pass
            self.robinhood_plaid_snapshot = None
            return
        try:
            self.robinhood_plaid_snapshot = load_json(path)
            if "account_label" not in self.robinhood_plaid_snapshot:
                self.robinhood_plaid_snapshot["account_label"] = "Robinhood"
            self._add_plaid_cash_transactions()
            self.net_worth_history = self._compute_net_worth_history()
            self.render_holdings()
            self.render_positions()
            self.refresh_transactions()
        except Exception as exc:
            self.robinhood_plaid_snapshot = None
            if not silent:
                QMessageBox.critical(self, APP_TITLE, f"Could not read Robinhood Plaid cache: {exc}")

    def _add_plaid_cash_transactions(self) -> None:
        """Add Plaid cash/credit transactions from Wealthfront or Robinhood (credit card).
        Wealthfront ones power the Activity tab cash flow view.
        """
        # Support Wealthfront snapshot
        snapshots = []
        if self.wealthfront_snapshot:
            snapshots.append(self.wealthfront_snapshot)
        if self.robinhood_plaid_snapshot:
            snapshots.append(self.robinhood_plaid_snapshot)
        # Support Robinhood credit card via Plaid if a robinhood_plaid snapshot exists
        # Also check main snapshot for future
        if self.snapshot and self.snapshot.get("cash_transactions"):
            snapshots.append(self.snapshot)

        for snapshot in snapshots:
            cash_tx = snapshot.get("cash_transactions", {})
            added = cash_tx.get("added", []) or []
            label = snapshot.get("account_label") or snapshot.get("institution", "Plaid Account")
            account = f"{label} Cash/Card"
            new_txs = []
            for t in added:
                date_str = t.get("date")
                if not date_str:
                    continue
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                except Exception:
                    continue
                name = t.get("name") or t.get("merchant_name") or "Transaction"
                plaid_amount = float(t.get("amount", 0) or 0)
                # Plaid: positive amount = outflow (expense for bank/credit)
                # App: positive = income, negative = expense
                app_amount = -plaid_amount
                cats = t.get("category") or []
                category = " / ".join(cats) if cats else "Uncategorized"
                tx = Transaction(
                    date=dt,
                    description=str(name)[:100],
                    amount=app_amount,
                    category=category,
                    account=account,
                    balance=None
                )
                new_txs.append(tx)
            # For Wealthfront, also include investment_transactions as they affect cash (buy/sell) - even if no cash added
            if "wealthfront" in label.lower() or snapshot.get("institution", "").lower() == "wealthfront":
                inv_txs = snapshot.get("investment_transactions", []) or []
                for t in inv_txs:
                    date_str = t.get("date")
                    if not date_str:
                        continue
                    try:
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                    except Exception:
                        continue
                    name = t.get("name") or t.get("security_id") or "Investment"
                    amt = float(t.get("amount", 0) or 0)
                    ttype = (t.get("type") or "").lower()
                    if ttype in ("sell", "dividend", "cash"):
                        app_amount = amt  # inflow
                    else:
                        app_amount = -amt  # buy or other = outflow
                    tx = Transaction(
                        date=dt,
                        description=str(name)[:100],
                        amount=app_amount,
                        category="Investment",
                        account=account,
                        balance=None
                    )
                    new_txs.append(tx)

            if new_txs:
                # Merge avoiding dupes based on account prefix
                prefix = account.split()[0] if " " in account else account
                base = [tx for tx in (self.transactions or []) if not getattr(tx, 'account', '').startswith(prefix)]
                all_txs = base + new_txs
                self.transactions = sorted(all_txs, key=lambda tx: tx.date)
                self.filtered = list(self.transactions)
                self.net_worth_history = self._compute_net_worth_history()

    def render_holdings(self) -> None:
        """Premium holdings table. Fixed truncation, proper column sizing, % of portfolio,
        clean no-alternating rows, dynamic breakdown, high quality formatting.
        """
        if not hasattr(self, 'holdings_table') or self.holdings_table is None:
            return
        self.holdings_table.setRowCount(0)

        total_val = compute_portfolio_value(self.snapshot, self.wealthfront_snapshot, getattr(self, 'manual_assets', None))
        if hasattr(self, 'holdings_total'):
            self.holdings_total.setText(fmt_money(total_val))

        positions = []

        # Robinhood equities
        if self.snapshot:
            for eqs in self.snapshot.get("equity_positions", {}).values():
                for pos in eqs:
                    q = pos.get("quote", {})
                    last = float(q.get("last_trade_price", 0) or 0)
                    qty = float(pos.get("quantity", 0) or 0)
                    avg = float(pos.get("average_buy_price", 0) or 0)
                    value = qty * last
                    pnl = value - (qty * avg)
                    positions.append({
                        "source": "Robinhood", "symbol": pos.get("symbol", "?"),
                        "qty": qty, "price": last, "value": value, "pnl": pnl
                    })

        # Robinhood cash
        if self.snapshot:
            for acc in self.snapshot.get("accounts", []):
                cash_val = 0.0
                for key in ("cash", "buying_power", "cash_balance", "available_cash", "balance"):
                    val = acc.get(key)
                    if val is not None:
                        try:
                            cash_val = float(val)
                            break
                        except (ValueError, TypeError):
                            pass
                if cash_val > 0:
                    positions.append({
                        "source": "Cash",
                        "symbol": acc.get("nickname", "Cash"),
                        "qty": "-",
                        "price": 0,
                        "value": cash_val,
                        "pnl": 0
                    })

        # Wealthfront
        if self.wealthfront_snapshot:
            secs = {str(s.get("security_id") or ""): s for s in self.wealthfront_snapshot.get("securities", [])}
            for h in self.wealthfront_snapshot.get("holdings", []):
                sec = secs.get(str(h.get("security_id") or ""), {})
                sym = sec.get("ticker_symbol") or sec.get("symbol") or "Pos"
                qty = float(h.get("quantity") or 0)
                value = float(h.get("institution_value") or h.get("market_value") or 0)
                cost = float(h.get("cost_basis") or 0)
                pnl = value - cost
                price = value / qty if qty else 0
                positions.append({
                    "source": "Wealthfront", "symbol": sym,
                    "qty": qty, "price": price, "value": value, "pnl": pnl
                })

            # Wealthfront cash / account balances (for cash accounts or available funds)
            for acc in self.wealthfront_snapshot.get("accounts", []):
                bal = 0.0
                balances = acc.get("balances") or acc
                for k in ("current", "available", "cash", "balance"):
                    v = balances.get(k) if isinstance(balances, dict) else acc.get(k)
                    if v is not None:
                        try:
                            bval = float(v)
                            if bval > 0:
                                bal = bval
                                break
                        except (ValueError, TypeError):
                            pass
                if bal > 0:
                    positions.append({
                        "source": "Wealthfront",
                        "symbol": acc.get("name") or acc.get("official_name") or acc.get("nickname", "Cash"),
                        "qty": "-",
                        "price": 0,
                        "value": bal,
                        "pnl": 0
                    })

        # Manual assets
        for name, value in getattr(self, 'manual_assets', {}).items():
            if value > 0:
                positions.append({
                    "source": "Manual",
                    "symbol": name,
                    "qty": "-",
                    "price": 0,
                    "value": float(value),
                    "pnl": 0
                })

        positions.sort(key=lambda p: -p["value"])

        # Compute breakdown for header
        rh_total = sum(p["value"] for p in positions if p["source"] == "Robinhood")
        wf_total = sum(p["value"] for p in positions if p["source"] == "Wealthfront")
        cash_total = sum(p["value"] for p in positions if p["source"] == "Cash")
        manual_total = sum(p["value"] for p in positions if p["source"] == "Manual")
        rh_cnt = sum(1 for p in positions if p["source"] == "Robinhood")
        wf_cnt = sum(1 for p in positions if p["source"] == "Wealthfront")
        cash_cnt = sum(1 for p in positions if p["source"] == "Cash")
        man_cnt = sum(1 for p in positions if p["source"] == "Manual")

        # Clear old breakdown widgets and rebuild nice modern pills
        if hasattr(self, "holdings_breakdown"):
            while self.holdings_breakdown.count():
                item = self.holdings_breakdown.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            for label, cnt, val in [
                ("Robinhood", rh_cnt, rh_total),
                ("Wealthfront", wf_cnt, wf_total),
                ("Cash", cash_cnt, cash_total),
                ("Manual", man_cnt, manual_total),
            ]:
                pill = QLabel(f"{label} ({cnt})   {fmt_money(val)}")
                pill.setStyleSheet(f"color: {TEXT}; background: {GREEN_CARD}; border-radius: 999px; padding: 5px 14px; font-size: 13px;")
                self.holdings_breakdown.addWidget(pill)
            self.holdings_breakdown.addStretch(1)

        # Update sub label
        if hasattr(self, 'holdings_sub'):
            count = len(positions)
            self.holdings_sub.setText(f"{count} holdings  •  sorted by value  •  consolidated from caches")

        # Populate table (limit for visual cleanliness; user can see all important ones)
        display = positions[:25]
        for p in display:
            row = self.holdings_table.rowCount()
            self.holdings_table.insertRow(row)

            val = p["value"]
            pct = (val / total_val * 100.0) if total_val > 0 else 0.0

            src = p["source"]
            is_special = src in ("Cash", "Manual") or str(p.get("qty", "")) == "-"

            if is_special:
                vals = [
                    src,
                    p["symbol"][:18] if len(p["symbol"]) > 18 else p["symbol"],
                    "-",
                    "-",
                    fmt_money(val),
                    "-",
                    f"{pct:.1f}%"
                ]
            else:
                qty_str = f"{p['qty']:.4f}".rstrip("0").rstrip(".") if p['qty'] < 1000 else f"{p['qty']:.2f}"
                vals = [
                    src,
                    p["symbol"][:12] if len(p.get("symbol", "")) > 12 else p["symbol"],
                    qty_str,
                    fmt_money(p["price"]),
                    fmt_money(val),
                    fmt_money(p["pnl"]),
                    f"{pct:.1f}%"
                ]

            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                # Center Market Value (col 4), right-align other numerics
                if c == 4:
                    it.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                elif c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    it.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

                # Source coloring
                if c == 0:
                    if src == "Robinhood":
                        it.setForeground(QColor(RH_GREEN))
                    elif src == "Wealthfront":
                        it.setForeground(QColor(GOLD))
                    elif src == "Cash":
                        it.setForeground(QColor(BLUE))

                # P/L coloring
                if c == 5 and not is_special:
                    it.setForeground(QColor(POSITIVE if p["pnl"] >= 0 else NEGATIVE))

                self.holdings_table.setItem(row, c, it)

        # Smart sizing to eliminate sloppy truncation
        self.holdings_table.resizeColumnsToContents()
        # Tweak a few for balance (Market Value centered, not stretched too far)
        self.holdings_table.setColumnWidth(0, max(85, self.holdings_table.columnWidth(0)))   # Source
        self.holdings_table.setColumnWidth(1, max(95, self.holdings_table.columnWidth(1)))   # Symbol
        self.holdings_table.setColumnWidth(2, max(72, self.holdings_table.columnWidth(2)))   # Shares
        self.holdings_table.setColumnWidth(3, max(88, self.holdings_table.columnWidth(3)))   # Price
        self.holdings_table.setColumnWidth(4, max(110, self.holdings_table.columnWidth(4)))  # Market Value (centered)
        self.holdings_table.setColumnWidth(5, max(115, self.holdings_table.columnWidth(5)))  # P/L
        self.holdings_table.setColumnWidth(6, 78)                                            # %

        if not positions and hasattr(self, 'holdings_sub'):
            self.holdings_sub.setText("No holdings loaded • use Settings or Reload to connect Robinhood / Wealthfront")

    def reload_all_holdings(self):
        self.load_snapshot(silent=True)
        self.load_wealthfront_snapshot(silent=True)
        self.load_robinhood_plaid_snapshot(silent=True)
        self.render_dashboard()
        self.render_holdings()
        self.refresh_transactions()

    def reload_robinhood_holdings(self):
        self.load_snapshot(silent=True)
        self.render_dashboard()
        self.render_holdings()

    # Card helpers removed - using consistent #Card style

    def _bridge_status_text(self) -> str:
        parts = []
        if self.snapshot:
            accounts = self.snapshot.get("accounts", [])
            equity_positions = self.snapshot.get("equity_positions", {})
            option_positions = self.snapshot.get("option_positions", {})
            equity_total = sum(len(v) for v in equity_positions.values())
            option_total = sum(len(v) for v in option_positions.values())
            parts.append(f"{len(accounts)} Robinhood accounts • {equity_total} equities • {option_total} options")
        else:
            parts.append("Robinhood cache not loaded")
        if self.wealthfront_snapshot:
            wf = self.wealthfront_snapshot
            parts.append(
                f"{len(wf.get('accounts', []))} Wealthfront accounts • {len(wf.get('holdings', []))} holdings • {fmt_date(wf.get('updated_at'))}"
            )
        else:
            parts.append("Wealthfront cache not loaded")
        return "\n".join(parts)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Critical for custom taskbar icon on Windows when running as Python script
    # This makes Windows use our icon instead of the pythonw.exe default
    if sys.platform == "win32":
        try:
            app_id = "BootySlime.FinanceLens.1.0"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass

    # Clean system font for Apple-like feel
    font = app.font()
    font.setFamily("Segoe UI Variable, Segoe UI, Inter, system-ui")
    font.setPointSize(10)
    app.setFont(font)

    # Set application icon (will show in taskbar, alt-tab, etc.)
    icon_path = None
    try:
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join(os.path.dirname(__file__), "icon.jpg")
        if icon_path and os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))
    except Exception:
        icon_path = None
        pass

    # Dark palette to ensure consistent dark look (title bar + widgets)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(0x0a, 0x0c, 0x0f))
    palette.setColor(QPalette.WindowText, QColor(0xf1, 0xf1, 0xf4))
    palette.setColor(QPalette.Base, QColor(0x12, 0x14, 0x18))
    palette.setColor(QPalette.AlternateBase, QColor(0x18, 0x1c, 0x22))
    palette.setColor(QPalette.ToolTipBase, QColor(0x12, 0x14, 0x18))
    palette.setColor(QPalette.ToolTipText, QColor(0xf1, 0xf1, 0xf4))
    palette.setColor(QPalette.Text, QColor(0xf1, 0xf1, 0xf4))
    palette.setColor(QPalette.Button, QColor(0x18, 0x1c, 0x22))
    palette.setColor(QPalette.ButtonText, QColor(0xf1, 0xf1, 0xf4))
    palette.setColor(QPalette.BrightText, QColor(0xf1, 0xf1, 0xf4))
    palette.setColor(QPalette.Link, QColor(0x00, 0xc8, 0x05))  # RH green
    palette.setColor(QPalette.Highlight, QColor(0x00, 0xc8, 0x05))
    palette.setColor(QPalette.HighlightedText, QColor(0x0a, 0x0c, 0x0f))
    app.setPalette(palette)

    window = FinanceLensApp()
    window.show()
    window.raise_()
    window.activateWindow()

    # Re-set icon after show for taskbar reliability
    if icon_path and os.path.exists(icon_path):
        try:
            app.setWindowIcon(QIcon(icon_path))
            window.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

    # Ensure dark title bar on Windows after show (some cases need it here)
    if sys.platform == "win32":
        try:
            hwnd = int(window.winId())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            pass

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
