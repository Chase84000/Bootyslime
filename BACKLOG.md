# Bootyslime — backlog

> Single source of truth for planned work. The coordinator owns this file; the async scribe is
> the only automated writer. 🔜 planned · 🚧 in progress · ✅ shipped.

## Now

🔜 **EPIC — All-in-One Financial Analyst — stock & company analysis** Finance Lens is currently a portfolio *viewer* with strong data connectors but no stock-analysis UI. The Robinhood MCP already exposes ~40 tools (fundamentals, historicals, earnings, quotes, scans, watchlists) at `mcps/grok_com_robinhood/tools/` that nothing in the desktop app surfaces. Chart widgets (`LineChart` `finance_lens_qt.py:379`, `BarChart` `:257`), KPI cards (`_metric` `:1113`, `_make_titled_graph_card` `:1140`), and a clean Analyst-tab insertion path (sidebar loop `:794-800`, `QStackedWidget self.stack` `:891/:902`, order dict `:1546`, render dispatch `:1601`) are all reusable — this is mostly surfacing existing data + adding analysis logic, not a from-scratch build. Follows CFA/CFI report structure and the five ratio families, modeled on platforms like Koyfin/Stock Rover/Simply Wall St. **Open question:** fundamentals and news depth may exceed what the Robinhood MCP provides (no news/sentiment source, limited history) — a third-party data API (e.g. Financial Modeling Prep / Alpha Vantage / Yahoo Finance) may be needed for Phases 3–5; flag as a decision to make before Phase 3.

- 🔜 **Phase 1 — Analyst tab MVP** Add the Analyst page with ticker search; surface per-ticker snapshot (price, 52-wk range, market cap, buy/hold/sell lean + target), fundamentals, and the five ratio families — Profitability (ROE/ROA/margins), Liquidity (current/quick/cash), Solvency/leverage (debt-equity, interest coverage), Efficiency (asset/inventory turnover), Valuation (P/E, P/B, EV/EBITDA, yield) — plus a price-history `LineChart`, all sourced from the Robinhood MCP (`get_equity_fundamentals`, `get_equity_historicals`, `get_equity_quotes`). Reuse `LineChart` / `_metric` / `_page_frame`; wire the tab via the existing sidebar loop (`:794-800`), `self.stack` (`:891/:902`), order dict (`:1546`), and render dispatch (`:1601`).

## Next

- 🔜 **Phase 2 — Technicals & earnings** MA/RSI/MACD overlays on the Analyst price chart; earnings history vs estimates sourced from `get_earnings_results` and `get_earnings_calendar`.

- 🔜 **Phase 3 — Valuation engine** DCF, comparable multiples, and DDM models → computed fair value + margin of safety with method-convergence shown. *Requires the data-source decision flagged in the epic above.*

- 🔜 **Phase 4 — Comparison, scoring & screener** Multi-symbol side-by-side comparison, composite quality/growth/value/sentiment score, and a desktop screener UI over the MCP scan tools (`run_scan` / `create_scan` / `update_scan_filters`).

- 🔜 **Phase 5 — AI narrative & alerts** In-app plain-language analyst summary per ticker and alerting on earnings/valuation/insider events.

## Recently shipped
