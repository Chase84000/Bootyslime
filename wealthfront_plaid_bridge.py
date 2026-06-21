from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request

from wealthfront_plaid_common import (
    CONFIG_FILE,
    DEFAULT_CACHE_FILE,
    ensure_state_defaults,
    exchange_public_token,
    load_state,
    save_state,
    sync_wealthfront,
    create_link_token,
)


APP = Flask(__name__)


def _status_payload() -> dict:
    state = ensure_state_defaults(load_state())
    cache_path = Path(str(state.get("cache_path") or DEFAULT_CACHE_FILE)).expanduser()
    linked = bool(str(state.get("access_token") or "").strip())
    return {
        "configured": bool(str(state.get("client_id") or "").strip() and str(state.get("secret") or "").strip()),
        "client_id": state.get("client_id", ""),
        "secret_present": bool(str(state.get("secret") or "").strip()),
        "linked": linked,
        "env": state.get("env", "development"),
        "cache_path": str(cache_path),
        "cache_exists": cache_path.exists(),
        "last_sync": state.get("last_sync"),
        "linked_at": state.get("linked_at"),
        "user_id": state.get("user_id"),
    }


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Finance Lens - Wealthfront Bridge</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #07140f;
      --panel: #0c1f16;
      --card: #10271c;
      --card-2: #143224;
      --line: #274536;
      --gold: #d6b15a;
      --gold-soft: #ecd99a;
      --text: #f6f1e8;
      --muted: #b2bcad;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Segoe UI, system-ui, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(214,177,90,0.12), transparent 36%),
        radial-gradient(circle at bottom right, rgba(77,163,111,0.18), transparent 38%),
        var(--bg);
      color: var(--text);
    }
    .shell { max-width: 1040px; margin: 0 auto; padding: 28px; }
    .hero, .panel {
      background: linear-gradient(180deg, rgba(255,255,255,0.03), transparent), var(--panel);
      border: 1px solid rgba(214,177,90,0.12);
      border-radius: 28px;
      box-shadow: 0 24px 60px rgba(0,0,0,0.32);
    }
    .hero { padding: 28px; margin-bottom: 18px; }
    .panel { padding: 22px; margin-bottom: 18px; }
    h1, h2 { margin: 0 0 10px 0; }
    h1 { font-size: 38px; letter-spacing: -0.03em; }
    h2 { font-size: 18px; color: var(--gold-soft); }
    p { color: var(--muted); line-height: 1.5; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .field { display: flex; flex-direction: column; gap: 8px; }
    .field label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    input, select {
      width: 100%;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      color: var(--text);
      padding: 12px 14px;
      font: inherit;
    }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
    button {
      border: 0;
      border-radius: 16px;
      padding: 12px 16px;
      font: inherit;
      font-weight: 700;
      background: var(--card-2);
      color: var(--text);
      cursor: pointer;
    }
    button.primary { background: var(--gold); color: #0b160f; }
    button:hover { filter: brightness(1.06); }
    .status {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-top: 18px;
    }
    .stat {
      background: var(--card);
      border-radius: 20px;
      padding: 14px;
      border: 1px solid rgba(214,177,90,0.08);
    }
    .stat .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    .stat .value { font-size: 18px; font-weight: 700; margin-top: 6px; }
    pre {
      background: #09150f;
      border-radius: 18px;
      padding: 16px;
      overflow: auto;
      color: #dfe8dc;
      border: 1px solid rgba(214,177,90,0.08);
    }
    .note { margin-top: 12px; }
    @media (max-width: 840px) {
      .grid, .status { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <h1>Wealthfront bridge</h1>
      <p>Use Plaid Link to connect Wealthfront, store the resulting access token locally, and sync data into Finance Lens.</p>
      <div class="status">
        <div class="stat"><div class="label">Configuration</div><div class="value" id="configured">Loading...</div></div>
        <div class="stat"><div class="label">Linked</div><div class="value" id="linked">Loading...</div></div>
        <div class="stat"><div class="label">Last Sync</div><div class="value" id="last-sync">Loading...</div></div>
        <div class="stat"><div class="label">Cache</div><div class="value" id="cache-path">Loading...</div></div>
      </div>
    </div>

    <div class="panel">
      <h2>Plaid Settings</h2>
      <div class="grid">
        <div class="field">
          <label for="client-id">Client ID</label>
          <input id="client-id" autocomplete="off" placeholder="plaid client id">
        </div>
        <div class="field">
          <label for="secret">Secret</label>
          <input id="secret" autocomplete="off" placeholder="plaid secret">
        </div>
        <div class="field">
          <label for="env">Environment</label>
          <select id="env">
            <option value="development">development</option>
            <option value="production">production</option>
            <option value="sandbox">sandbox</option>
          </select>
        </div>
        <div class="field">
          <label for="cache">Cache Path</label>
          <input id="cache" autocomplete="off" placeholder="wealthfront-cache.json">
        </div>
      </div>
      <div class="actions">
        <button class="primary" id="save-config">Save settings</button>
        <button id="connect">Connect Wealthfront</button>
        <button id="sync-now">Sync now</button>
      </div>
      <div class="note" id="message"></div>
    </div>

    <div class="panel">
      <h2>How this works</h2>
      <p>Finance Lens does not talk to Wealthfront directly. Plaid Link collects the authorization once, then this bridge stores the Plaid access token locally and refreshes a local cache file for the desktop app.</p>
      <pre>1. Save Plaid client ID and secret
2. Click Connect Wealthfront
3. Finish the Plaid Link flow
4. Finance Lens reloads wealthfront-cache.json</pre>
    </div>
  </div>

  <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
  <script>
    const els = {
      configured: document.getElementById('configured'),
      linked: document.getElementById('linked'),
      lastSync: document.getElementById('last-sync'),
      cachePath: document.getElementById('cache-path'),
      clientId: document.getElementById('client-id'),
      secret: document.getElementById('secret'),
      env: document.getElementById('env'),
      cache: document.getElementById('cache'),
      message: document.getElementById('message'),
    };

    function setMessage(text, kind = 'info') {
      const colors = { info: '#b2bcad', success: '#ecd99a', error: '#f4b4a8' };
      els.message.textContent = text;
      els.message.style.color = colors[kind] || colors.info;
    }

    async function refreshStatus() {
      const res = await fetch('/api/status');
      const data = await res.json();
      els.configured.textContent = data.configured ? 'Ready' : 'Missing';
      els.linked.textContent = data.linked ? 'Yes' : 'No';
      els.lastSync.textContent = data.last_sync ? new Date(data.last_sync).toLocaleString() : 'Never';
      els.cachePath.textContent = data.cache_exists ? 'Present' : 'Missing';
      els.clientId.value = data.client_id || '';
      els.env.value = data.env || 'development';
      els.cache.value = data.cache_path || '';
    }

    async function saveConfig() {
      const payload = {
        client_id: els.clientId.value.trim(),
        secret: els.secret.value.trim(),
        env: els.env.value,
        cache_path: els.cache.value.trim(),
      };
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to save config');
      setMessage('Saved Plaid settings.', 'success');
      await refreshStatus();
    }

    async function connectWealthfront() {
      const res = await fetch('/api/create_link_token', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Unable to create link token');
      const handler = Plaid.create({
        token: data.link_token,
        onSuccess: async function(public_token) {
          const exchange = await fetch('/api/exchange_public_token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ public_token }),
          });
          const exchangeData = await exchange.json();
          if (!exchange.ok) throw new Error(exchangeData.error || 'Token exchange failed');
          setMessage('Wealthfront connected. Syncing now...', 'success');
          await syncNow();
        },
        onExit: function(err) {
          if (err) setMessage(err.display_message || err.error_message || 'Plaid Link exited.', 'error');
        }
      });
      handler.open();
    }

    async function syncNow() {
      const res = await fetch('/api/sync', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Sync failed');
      setMessage(`Synced ${data.summary.holdings} holdings and ${data.summary.accounts} accounts.`, 'success');
      await refreshStatus();
    }

    document.getElementById('save-config').addEventListener('click', async () => {
      try { await saveConfig(); } catch (err) { setMessage(err.message, 'error'); }
    });
    document.getElementById('connect').addEventListener('click', async () => {
      try { await saveConfig(); await connectWealthfront(); } catch (err) { setMessage(err.message, 'error'); }
    });
    document.getElementById('sync-now').addEventListener('click', async () => {
      try { await syncNow(); } catch (err) { setMessage(err.message, 'error'); }
    });

    refreshStatus().catch(err => setMessage(err.message, 'error'));
  </script>
</body>
</html>
"""


@APP.get("/")
def index() -> Response:
    return Response(PAGE, mimetype="text/html")


@APP.get("/api/status")
def api_status():
    return jsonify(_status_payload())


@APP.post("/api/config")
def api_config():
    payload = request.get_json(force=True, silent=True) or {}
    state = ensure_state_defaults(load_state())
    state["client_id"] = str(payload.get("client_id") or "").strip()
    state["secret"] = str(payload.get("secret") or "").strip()
    state["env"] = str(payload.get("env") or state.get("env") or "development").strip().lower()
    cache_path = str(payload.get("cache_path") or state.get("cache_path") or DEFAULT_CACHE_FILE).strip()
    state["cache_path"] = cache_path
    if not state.get("user_id"):
        state["user_id"] = uuid.uuid4().hex
    save_state(state)
    return jsonify({"ok": True, "status": _status_payload()})


@APP.post("/api/create_link_token")
def api_create_link_token():
    try:
        return jsonify(create_link_token())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@APP.post("/api/exchange_public_token")
def api_exchange_public_token():
    payload = request.get_json(force=True, silent=True) or {}
    public_token = str(payload.get("public_token") or "").strip()
    if not public_token:
        return jsonify({"error": "public_token is required"}), 400
    try:
        return jsonify(exchange_public_token(public_token))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@APP.post("/api/sync")
def api_sync():
    try:
        cache = sync_wealthfront()
        return jsonify(cache)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


def main() -> int:
    state = ensure_state_defaults(load_state())
    save_state(state)
    APP.run(host="127.0.0.1", port=8766, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
