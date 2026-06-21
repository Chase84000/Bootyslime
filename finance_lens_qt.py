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
import math
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


def compute_portfolio_value(snapshot: dict | None, wf_snapshot: dict | None) -> float:
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


class PieChart(QWidget):
    """Simple donut pie for allocation, Robinhood style."""
    def __init__(self, title: str = "Asset allocation", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._data: dict[str, float] = {}  # label -> percent or value
        self._total: float | None = None
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, data: dict[str, float], total: float | None = None) -> None:
        self._data = data or {}
        if total is not None:
            self._total = total
        self.update()

    def set_title(self, title: str) -> None:
        self._title = title
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(GREEN_CARD))

        if not self._data:
            painter.setPen(QColor(SUBTLE))
            painter.setFont(QFont("Segoe UI", 12))
            painter.drawText(self.rect().adjusted(0, 10, 0, 0), Qt.AlignCenter, "No data")
            return

        total = sum(self._data.values()) or 1.0
        colors = [QColor(RH_GREEN), QColor(BLUE), QColor("#A855F7"), QColor("#F7931A"), QColor("#14B8A6"), QColor(GOLD)]
        # Less top padding now that titles are outside (smooth, no rect behind text)
        rect = self.rect().adjusted(10, 10, -10, -10)
        size = min(rect.width(), rect.height()) - 30
        cx = rect.left() + rect.width() / 2
        cy = rect.top() + rect.height() / 2 + 2
        pie_rect = QRectF(cx - size/2, cy - size/2, size, size)

        # Draw slices
        start = 90 * 16
        i = 0
        slice_info = []
        for label, val in self._data.items():
            ang = int(360 * 16 * (val / total))
            color = colors[i % len(colors)]
            painter.setBrush(QBrush(color))
            painter.setPen(QColor(GREEN_LINE))
            painter.drawPie(pie_rect, start, ang)
            mid = start + ang / 2
            # Compute label position for slice - push out a bit for better readability
            rad = math.radians(mid / 16 - 90)
            lx = cx + (size/2 * 0.82) * math.cos(rad)
            ly = cy + (size/2 * 0.82) * math.sin(rad)
            pct = (val / total) * 100
            slice_info.append((label, pct, lx, ly, color))
            start += ang
            i += 1

        # Center hole
        hole = size * 0.55
        painter.setBrush(QColor(GREEN_CARD))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(cx - hole/2, cy - hole/2, hole, hole))

        # Center total (use actual value if available, RH style)
        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Segoe UI", 14, QFont.Bold))
        if self._total is not None and self._total > 0:
            val_str = fmt_money(self._total)
            # center the text
            painter.drawText(int(cx - len(val_str)*3.5), int(cy + 4), val_str)
        else:
            painter.drawText(int(cx - 20), int(cy + 5), "Total")

        # Draw labels on slices if big enough, else legend
        painter.setFont(QFont("Segoe UI", 12, QFont.Bold))
        legend_y = int(pie_rect.top() + 5)
        legend_x = int(pie_rect.right() + 5)
        for label, pct, lx, ly, color in slice_info:
            if pct > 5:  # only label big slices on pie
                painter.setPen(QColor(TEXT))
                short = label if len(label) <= 11 else label[:8] + "…"
                text = f"{short}\n{pct:.0f}%"
                painter.drawText(int(lx - 28), int(ly - 6), text)
            else:
                # legend for small
                painter.setBrush(QBrush(color))
                painter.drawRect(legend_x, legend_y, 8, 8)
                painter.setPen(QColor(TEXT))
                painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
                short = label if len(label) <= 11 else label[:8] + "…"
                painter.drawText(legend_x + 12, legend_y + 7, f"{short} {pct:.0f}%")
                legend_y += 14

        # Full legend on right if space
        if legend_y > pie_rect.bottom():
            pass  # already drew inline


class LineChart(QWidget):
    """Simple line chart for net worth history."""
    def __init__(self, title: str = "Net Worth History", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._points: list[tuple[str, float]] = []  # (date_label, value)
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, points: list[tuple[str, float]]) -> None:
        self._points = points or []
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(GREEN_BG))
        painter.setPen(QColor(GOLD_SOFT))
        painter.setFont(QFont("Segoe UI", 15, QFont.DemiBold))
        painter.drawText(16, 18, self._title)

        if len(self._points) < 2:
            painter.setPen(QColor(SUBTLE))
            painter.setFont(QFont("Segoe UI", 12))
            painter.drawText(self.rect().adjusted(0, 25, 0, 0), Qt.AlignCenter, "No history data yet")
            return

        values = [v for _, v in self._points]
        min_v = min(values)
        max_v = max(values)
        if max_v == min_v:
            max_v += 1
            min_v -= 1

        margin = 30
        chart_w = self.width() - 2 * margin
        chart_h = self.height() - 55
        left = margin
        top = 25

        def y(v):
            return top + chart_h - (v - min_v) / (max_v - min_v) * chart_h

        n = len(self._points)
        step = chart_w / (n - 1) if n > 1 else chart_w

        # zero line if crosses
        if min_v < 0 < max_v:
            zero_y = y(0)
            painter.setPen(QPen(QColor(GREEN_LINE), 1))
            painter.drawLine(left, int(zero_y), left + int(chart_w), int(zero_y))

        # line
        painter.setPen(QPen(QColor(RH_GREEN), 2.5))
        path = QPainterPath()
        for i, (lbl, v) in enumerate(self._points):
            x = left + i * step
            yy = y(v)
            if i == 0:
                path.moveTo(x, yy)
            else:
                path.lineTo(x, yy)
        painter.drawPath(path)

        # points
        painter.setBrush(QColor(RH_GREEN))
        painter.setPen(QPen(QColor(TEXT), 1))
        for i, (lbl, v) in enumerate(self._points):
            x = left + i * step
            yy = y(v)
            painter.drawEllipse(int(x-4), int(yy-4), 8, 8)

        # x labels (dates)
        painter.setPen(QColor(MUTED))
        painter.setFont(QFont("Segoe UI", 11))
        for i, (lbl, v) in enumerate(self._points):
            x = left + i * step
            painter.drawText(int(x - 25), self.height() - 8, lbl[-5:] if len(lbl) > 5 else lbl)

        # y min/max labels
        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Segoe UI", 11))
        painter.drawText(2, int(top + 10), fmt_money(min_v))
        painter.drawText(2, int(top + chart_h), fmt_money(max_v))


class Card(QFrame):
    """Polished metric card with Apple-like hierarchy and spacing."""
    def __init__(self, title: str, subtitle: str = "", value: str = "") -> None:
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(3)

        self.title = QLabel(title)
        self.title.setObjectName("CardTitle")
        self.title.setContentsMargins(0, 0, 0, 2)

        self.value = QLabel(value)
        self.value.setObjectName("CardValue")

        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("CardSubtitle")

        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.subtitle)

    def set_value(self, value: str) -> None:
        self.value.setText(value)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)


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
        self.transactions: list[Transaction] = []
        self.filtered: list[Transaction] = []
        self.net_worth_history: list[tuple[str, float]] = []
        self.networth_chart = None
        self.snapshot: dict | None = None
        self.wealthfront_snapshot: dict | None = None

        self.month_var = ""
        self.search_var = ""
        self.top_count = 8

        self.portfolio_value = None
        self.portfolio_change = None
        self.net_worth_card = None
        self.positions_card = None
        self.income_card = None
        self.expense_card = None
        self.recent_list = None
        self.allocation_chart = None

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
            #BrandBox, #HoldingsTop, #SettingsCard, #ActivityCard {{
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
        self.sidebar_snapshot = Card("Snapshot", "Transactions loaded", "—")
        self._apply_shadow(self.sidebar_snapshot, radius=20, blur=16, y=2)
        side_layout.addWidget(self.sidebar_snapshot)
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
        self.cache_path = QLineEdit(str(DEFAULT_ROBINHOOD_CACHE))
        self.wealthfront_cache_path = QLineEdit(str(DEFAULT_WEALTHFRONT_CACHE))

        outer.addWidget(self.sidebar)
        outer.addWidget(self.main, 1)

    def _page_frame(self) -> QWidget:
        return QWidget()

    def _count_positions(self) -> int:
        count = 0
        if self.snapshot:
            for v in self.snapshot.get("equity_positions", {}).values():
                count += len(v)
            for v in self.snapshot.get("option_positions", {}).values():
                count += len(v)
        if self.wealthfront_snapshot:
            count += len(self.wealthfront_snapshot.get("holdings", []))
        return count

    def _compute_net_worth_history(self) -> list[tuple[str, float]]:
        """Compute net worth over time from transactions (prefers 'balance' column if present)."""
        if not self.transactions:
            return []
        txs = sorted(self.transactions, key=lambda t: t.date)
        has_balance = any(getattr(tx, 'balance', None) is not None for tx in txs)
        history: list[tuple[str, float]] = []
        if has_balance:
            # Use latest balance per date
            by_date: dict = {}
            for tx in txs:
                bal = getattr(tx, 'balance', None)
                if bal is not None:
                    by_date[tx.date] = bal
            for d in sorted(by_date.keys()):
                history.append((d.strftime('%m/%d'), by_date[d]))
        else:
            # Running cumulative
            running = 0.0
            by_date: dict = {}
            for tx in txs:
                running += tx.amount
                by_date[tx.date] = running
            for d in sorted(by_date.keys()):
                history.append((d.strftime('%m/%d'), by_date[d]))
        # Append current investment value as latest point (for full net worth view)
        current_inv = compute_portfolio_value(self.snapshot, self.wealthfront_snapshot)
        if history and current_inv > 0:
            last_date = txs[-1].date
            # use today's date or last + current inv (simple: add to last or separate)
            # for demo, append with current date label if different
            from datetime import datetime as dtmod
            today_label = dtmod.now().strftime('%m/%d')
            last_val = history[-1][1]
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

        # Cards for pies with rectangle highlight behind the titles (Robinhood-inspired clean cards)
        # Each pie gets its own rounded card container so the title text has a nice highlighted rect behind it
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
        card_hold, self.holdings_title = self._make_titled_graph_card("Market value by symbol", self.holdings_graph, 300)
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
        """Separate tab for net worth history as requested."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)

        title = QLabel("Net Worth History")
        title.setStyleSheet(f"color: {TEXT}; font-size: 18px; font-weight: 700;")
        lay.addWidget(title)

        sub = QLabel("Based on transaction balances + current investment values")
        sub.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        lay.addWidget(sub)

        self.networth_chart = LineChart("Net Worth Over Time")
        self.networth_chart.setMinimumHeight(400)
        lay.addWidget(self.networth_chart, 1)

        stats = QLabel("Use Dashboard for allocation & positions. History updates when you load new data.")
        stats.setStyleSheet(f"color: {SUBTLE}; font-size: 12px;")
        lay.addWidget(stats)

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

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.HLine)
        d.setStyleSheet(f"color: {GREEN_LINE};")
        return d

    def _build_activity_page(self) -> QWidget:
        # Simplified like RH activity feed - useful columns only
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Minimal filter
        filt = QFrame()
        filt.setObjectName("ActivityCard")
        flay = QHBoxLayout(filt)
        flay.setContentsMargins(18, 12, 18, 12)
        flay.setSpacing(10)
        flay.addWidget(QLabel("Search"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("description or account...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.apply_filters)
        flay.addWidget(self.search_input, 1)
        flay.addWidget(QLabel("Month"))
        self.month_combo = QComboBox()
        self.month_combo.addItem("All")
        self.month_combo.currentTextChanged.connect(lambda _: self.apply_filters())
        flay.addWidget(self.month_combo)
        self._apply_shadow(filt, radius=20, blur=14)
        layout.addWidget(filt)

        self.tx_table = QTableWidget(0, 4)
        self.tx_table.setHorizontalHeaderLabels(["Date", "Description", "Amount", "Account"])
        self.tx_table.verticalHeader().setVisible(False)
        self.tx_table.setShowGrid(False)
        self.tx_table.setAlternatingRowColors(True)
        self.tx_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.tx_table, 1)
        return page

    def _build_holdings_page(self) -> QWidget:
        # Robinhood/Wealthfront style: Clean portfolio view - only useful position info
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Header with total
        header = QFrame()
        header.setObjectName("HoldingsTop")
        hlay = QVBoxLayout(header)
        hlay.setContentsMargins(22, 16, 22, 16)
        self.holdings_title = QLabel("Portfolio Holdings")
        self.holdings_title.setStyleSheet(f"color: {TEXT}; font-size: 18px; font-weight: 700;")
        self.holdings_total = QLabel("$0.00")
        self.holdings_total.setStyleSheet(f"color: {GOLD_SOFT}; font-size: 32px; font-weight: 700;")
        self.holdings_sub = QLabel("Consolidated from Robinhood + Wealthfront caches • sorted by value")
        self.holdings_sub.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        hlay.addWidget(self.holdings_title)
        hlay.addWidget(self.holdings_total)
        hlay.addWidget(self.holdings_sub)
        self._apply_shadow(header, radius=24, blur=18)
        layout.addWidget(header)

        # Useful list/table only - focused on symbol, value, P/L like RH/WF
        self.holdings_table = QTableWidget(0, 6)
        self.holdings_table.setHorizontalHeaderLabels(["Source", "Symbol", "Qty", "Price", "Value", "Unrealized P/L"])
        self.holdings_table.verticalHeader().setVisible(False)
        self.holdings_table.setShowGrid(False)
        self.holdings_table.setAlternatingRowColors(True)
        self.holdings_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.holdings_table, 1)

        # Minimal actions
        actions = QHBoxLayout()
        reload_all = QPushButton("Reload All")
        reload_all.clicked.connect(self.reload_all_holdings)
        connect_btn = QPushButton("Connect / Sync Wealthfront")
        connect_btn.setObjectName("PrimaryButton")
        connect_btn.clicked.connect(self.launch_wealthfront_bridge)
        actions.addWidget(reload_all)
        actions.addStretch(1)
        actions.addWidget(connect_btn)
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
        self.net_worth_history = self._compute_net_worth_history()
        # Only load sample if truly no real data from CSV or WF cache
        if not self.transactions and not (self.wealthfront_snapshot and self.wealthfront_snapshot.get("cash_transactions", {}).get("added")):
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
            "activity": ("Activity", "Transactions and cash flow"),
            "holdings": ("Holdings", "Consolidated portfolio view"),
            "settings": ("Settings", "Caches, Plaid and preferences"),
        }
        title, sub = titles.get(key, ("Overview", "Cash flow, holdings & insights. Drop CSVs anywhere to import."))
        self.hero_title.setText(title)
        self.hero_title.setStyleSheet(f"color: {TEXT}; font-size: 24px; font-weight: 700; letter-spacing: -0.4px;")
        self.hero_subtitle.setText(sub)
        self.hero_subtitle.setStyleSheet(f"color: {MUTED}; font-size: 13px;")

        # Tab-specific hero: hide on detail pages to avoid heavy top bar on Positions/Net Worth etc.
        self.hero.setVisible(key in ("dashboard", "activity", "holdings", "settings"))

        if key == "dashboard":
            self.render_dashboard()
        elif key == "activity":
            self.refresh_transactions()
        elif key == "holdings":
            self.render_holdings()
        elif key == "positions":
            self.render_positions()
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

    def _launch_wealthfront_script(self, script_name: str, open_browser: bool = False) -> None:
        script = ROOT / script_name
        if not script.exists():
            QMessageBox.warning(self, APP_TITLE, f"Missing script: {script.name}")
            return
        if open_browser and not is_port_open("127.0.0.1", WEALTHFRONT_BRIDGE_PORT):
            try:
                subprocess.Popen(
                    [pythonw_executable(), str(script)],
                    cwd=str(ROOT),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                QMessageBox.critical(self, APP_TITLE, f"Could not launch {script.name}: {exc}")
                return
        elif not open_browser:
            try:
                subprocess.Popen(
                    [pythonw_executable(), str(script)],
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
                QMessageBox.critical(self, APP_TITLE, detail)
                return
            self.load_wealthfront_snapshot()
            QMessageBox.information(self, APP_TITLE, proc.stdout.strip() or "Wealthfront sync complete.")
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"Could not sync Wealthfront: {exc}")

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
        self._add_wealthfront_cash_transactions()
        self.net_worth_history = self._compute_net_worth_history()
        self._sync_months()
        self.render_dashboard()
        self.refresh_transactions()
        self.render_holdings()
        self.render_positions()

    def _sync_months(self) -> None:
        months = sorted({month_key(tx.date) for tx in self.transactions})
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        self.month_combo.addItem("All")
        self.month_combo.addItems(months)
        self.month_combo.blockSignals(False)
        if self.month_var and self.month_var not in months:
            self.month_var = ""

    def apply_filters(self) -> None:
        month_text = self.month_combo.currentText().strip()
        self.month_var = "" if month_text == "All" else month_text
        self.search_var = self.search_input.text().strip().lower()
        txs = self.transactions
        if self.month_var:
            txs = [tx for tx in txs if month_key(tx.date) == self.month_var]
        if self.search_var:
            txs = [
                tx
                for tx in txs
                if self.search_var in (tx.description or "").lower()
                or self.search_var in (tx.category or "").lower()
                or self.search_var in (tx.account or "").lower()
            ]
        self.filtered = txs
        self.render_dashboard()
        self.refresh_transactions()

    def _on_top_change(self, value: int) -> None:
        self.top_count = int(value)
        self.top_value.setText(str(self.top_count))
        self.render_dashboard()

    def render_dashboard(self) -> None:
        tx_data = summary_for(self.filtered if self.filtered is not None else self.transactions)
        portfolio_val = compute_portfolio_value(self.snapshot, self.wealthfront_snapshot)
        pos_count = self._count_positions()

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
                    except: pass
        self._set_metric(self.kpi_cash, fmt_money(cash_approx or tx_data.get("net", 0)))

        # Prepare allocation (pure asset data only - removed cash flow pollution for cleaner view)
        alloc = {}
        if self.snapshot:
            eq_v = 0.0
            for poss in self.snapshot.get("equity_positions", {}).values():
                for p in poss:
                    q = p.get("quote", {})
                    eq_v += float(p.get("quantity", 0)) * float(q.get("last_trade_price", 0) or 0)
            if eq_v: alloc["Equities (RH)"] = eq_v
        if self.wealthfront_snapshot:
            wf_v = sum(float(h.get("institution_value") or h.get("market_value") or 0) for h in self.wealthfront_snapshot.get("holdings", []))
            if wf_v: alloc["Wealthfront"] = wf_v
        if cash_approx > 0:
            alloc["Cash"] = cash_approx
        alloc_total = sum(alloc.values())
        if hasattr(self, 'alloc_title'):
            self.alloc_title.setText(f"Asset allocation  •  {fmt_money(alloc_total)}")
        alloc_rows = sorted(alloc.items(), key=lambda x: -x[1])
        if hasattr(self, 'alloc_graph'):
            self.alloc_graph.set_rows(alloc_rows)

        # Value by account graph (pure data, no cash flow)
        acc_data = {}
        if self.snapshot:
            for acc in self.snapshot.get("accounts", []):
                acc_v = 0.0
                aid = acc.get("account_number")
                for p in self.snapshot.get("equity_positions", {}).get(aid, []):
                    q = p.get("quote", {})
                    acc_v += float(p.get("quantity", 0)) * float(q.get("last_trade_price", 0) or 0)
                if acc_v > 0:
                    acc_data[acc.get("nickname") or acc.get("type", "RH")] = acc_v
        if self.wealthfront_snapshot:
            wf_tot = sum(float(h.get("institution_value") or 0) for h in self.wealthfront_snapshot.get("holdings", []))
            if wf_tot > 0:
                acc_data["Wealthfront"] = wf_tot
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
        if hasattr(self, 'holdings_title'):
            self.holdings_title.setText(f"Market value by symbol  •  {fmt_money(hold_val_total)}")
        hold_rows = sorted(hold_val.items(), key=lambda x: -x[1])
        if hasattr(self, 'holdings_graph'):
            self.holdings_graph.set_rows(hold_rows)
        # (old pie removed; using graphs for cleaner look)

        # Top categories graph (fills page space)
        if hasattr(self, 'top_categories_graph'):
            cats = tx_data.get("categories", [])[:5]
            # Clean ugly labels like "Income:Income"
            cleaned_cats = [(cat.replace("Income: ", "Income - ") if cat.startswith("Income: ") else cat, val) for cat, val in cats]
            self.top_categories_graph.set_rows(cleaned_cats)

        # Update sidebar
        self.sidebar_snapshot.set_value(f"{tx_data['count']} txns")
        self.sidebar_snapshot.set_subtitle(f"{len(tx_data['months'])} mo")
        self.sidebar_bridge.setText(self._bridge_status_text())

    def render_networth(self) -> None:
        if not self.net_worth_history:
            self.net_worth_history = self._compute_net_worth_history()
        if hasattr(self, 'networth_chart') and self.networth_chart:
            self.networth_chart.set_data(self.net_worth_history or [])

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
        txs = self.filtered if self.filtered is not None else self.transactions
        self.tx_table.setRowCount(0)
        for tx in txs[:1500]:
            row = self.tx_table.rowCount()
            self.tx_table.insertRow(row)

            date_str = tx.date.strftime("%b %d")
            amount_str = fmt_money(tx.amount)

            values = [
                date_str,
                (tx.description or "")[:40],
                amount_str,
                (tx.account or "")[:18],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setForeground(QColor(POSITIVE if tx.amount >= 0 else NEGATIVE))
                self.tx_table.setItem(row, col, item)
        self.tx_table.setColumnWidth(0, 70)
        self.tx_table.setColumnWidth(2, 90)

    def load_snapshot(self, silent: bool = False) -> None:
        path_str = getattr(self, 'cache_path', None)
        path = Path(path_str.text() if path_str else DEFAULT_ROBINHOOD_CACHE).expanduser()
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
            self.net_worth_history = self._compute_net_worth_history()
            self.render_holdings()
            self.render_positions()
        except Exception as exc:
            self.snapshot = None
            if not silent:
                QMessageBox.critical(self, APP_TITLE, f"Could not read cache file: {exc}")

    def load_wealthfront_snapshot(self, silent: bool = False) -> None:
        path_str = getattr(self, 'wealthfront_cache_path', None)
        path = Path(path_str.text() if path_str else DEFAULT_WEALTHFRONT_CACHE).expanduser()
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
            self._add_wealthfront_cash_transactions()
            self.net_worth_history = self._compute_net_worth_history()
            self.render_holdings()
            self.render_positions()
        except Exception as exc:
            self.wealthfront_snapshot = None
            if not silent:
                QMessageBox.critical(self, APP_TITLE, f"Could not read Wealthfront cache file: {exc}")

    def _add_wealthfront_cash_transactions(self) -> None:
        """Add real Wealthfront bank (Cash Account) transactions from Plaid cache to activity tab."""
        if not self.wealthfront_snapshot:
            return
        cash_tx = self.wealthfront_snapshot.get("cash_transactions", {})
        added = cash_tx.get("added", []) or []
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
            # Plaid: positive amount = outflow (expense for bank)
            # App: positive = income, negative = expense
            app_amount = -plaid_amount
            cats = t.get("category") or []
            category = " / ".join(cats) if cats else "Uncategorized"
            account = "Wealthfront Cash"
            tx = Transaction(
                date=dt,
                description=str(name)[:100],
                amount=app_amount,
                category=category,
                account=account,
                balance=None
            )
            new_txs.append(tx)
        if new_txs:
            # Merge with existing CSV transactions (avoid dupes from WF)
            base = [tx for tx in (self.transactions or []) if not getattr(tx, 'account', '').startswith('Wealthfront')]
            all_txs = base + new_txs
            self.transactions = sorted(all_txs, key=lambda tx: tx.date)
            self.filtered = list(self.transactions)
            self.net_worth_history = self._compute_net_worth_history()
            self._sync_months()

    def render_holdings(self) -> None:
        """Clean Robinhood/Wealthfront style holdings table - only useful info."""
        if not hasattr(self, 'holdings_table') or self.holdings_table is None:
            return
        self.holdings_table.setRowCount(0)
        total_val = compute_portfolio_value(self.snapshot, self.wealthfront_snapshot)
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
                        "source": "RH", "symbol": pos.get("symbol", "?"),
                        "qty": qty, "price": last, "value": value, "pnl": pnl
                    })

        # Robinhood cash positions (uninvested cash per account, from portfolio data)
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
                        "source": "RH Cash",
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
                    "source": "WF", "symbol": sym[:10],
                    "qty": qty, "price": price, "value": value, "pnl": pnl
                })

        positions.sort(key=lambda p: -p["value"])

        for p in positions[:20]:
            row = self.holdings_table.rowCount()
            self.holdings_table.insertRow(row)
            if p.get("source") == "RH Cash":
                vals = [
                    p["source"],
                    p["symbol"],
                    "-",
                    "-",
                    fmt_money(p["value"]),
                    "-"
                ]
            else:
                vals = [
                    p["source"],
                    p["symbol"],
                    f"{p['qty']:.2f}",
                    fmt_money(p["price"]),
                    fmt_money(p["value"]),
                    fmt_money(p["pnl"])
                ]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c > 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 5 and p.get("source") != "RH Cash":
                    it.setForeground(QColor(POSITIVE if p["pnl"] >= 0 else NEGATIVE))
                self.holdings_table.setItem(row, c, it)

        self.holdings_table.setColumnWidth(0, 50)
        self.holdings_table.setColumnWidth(1, 100)
        self.holdings_table.setColumnWidth(2, 60)
        self.holdings_table.setColumnWidth(3, 80)
        self.holdings_table.setColumnWidth(4, 85)

        if not positions and hasattr(self, 'holdings_sub'):
            self.holdings_sub.setText("Load a Robinhood cache.json or connect Wealthfront to populate")

    def reload_all_holdings(self):
        self.load_snapshot(silent=True)
        self.load_wealthfront_snapshot(silent=True)
        self.render_dashboard()
        self.render_holdings()

    # Old card helpers removed - new UI uses clean tables only (Robinhood/Wealthfront inspired minimalism)

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
