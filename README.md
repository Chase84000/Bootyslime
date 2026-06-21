# Finance Lens

Beautiful, Apple-level personal finance desktop app built with PySide6.

This folder contains the Finance Lens desktop app (clean, intuitive UI) plus local bridges for Robinhood and Wealthfront.

## What it expects

The CSV should include these columns:

- `date`
- `description`
- `amount`

Optional columns:

- `category`
- `account`
- `balance`

Positive amounts are treated as income. Negative amounts are treated as expenses.

## Run it

```powershell
.\run_desktop.bat
```
or
```powershell
.\launch_desktop.ps1
```

(PySide6 desktop app — the recommended polished experience.)

## How it works

- **Drag & drop** CSVs directly into the window (or use the Import / Folder buttons)
- Auto-detects common bank/broker columns (date, description, amount, etc.)
- Live filtering by month + search with instant dashboard + table updates
- Beautiful KPI cards, refined charts, and holdings snapshots

The design is intentionally calm, spacious, and high-quality — modeled after premium Apple apps.

## Expected columns

The app works best with `date`, `description`, and `amount`.

It also supports many common alternatives:

- `debit` / `credit`
- `category`
- `account`
- `balance`

## Robinhood bridge

The app serves a cached Robinhood snapshot from [robinhood-cache.json](C:\Users\nouve.DESKTOP-IDVQJ79\Bootyslime\robinhood-cache.json) through [bridge-server.js](C:\Users\nouve.DESKTOP-IDVQJ79\Bootyslime\bridge-server.js).

That means the dashboard can show your accounts and positions without copy/paste.

The snapshot is read-only and local. Use the MCP bridge or refresh the cache manually when needed.

## Wealthfront bridge

Wealthfront connects through Plaid, not directly.

Workflow:

1. Open the desktop app
2. Go to `Settings`
3. Paste your Plaid `client_id` and `secret`
4. Click `Open Wealthfront bridge`
5. Finish Plaid Link in the browser
6. The bridge writes `wealthfront-cache.json`

For daily refreshes, run `wealthfront_sync.py` on a schedule (e.g. Windows Task Scheduler).

## Notes

The desktop app requires Python 3.12 plus the installed dependencies listed in `requirements.txt`.
