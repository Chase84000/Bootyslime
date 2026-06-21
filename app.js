const state = {
  files: [],
  transactions: [],
  filtered: null,
  months: [],
  robinhood: null,
  activeMonth: "",
  search: "",
  topCount: 8,
};

const els = {
  fileInput: document.getElementById("file-input"),
  folderInput: document.getElementById("folder-input"),
  dropzone: document.getElementById("dropzone"),
  fileList: document.getElementById("file-list"),
  status: document.getElementById("status"),
  metrics: document.getElementById("metrics"),
  monthlyChart: document.getElementById("monthly-chart"),
  categoryList: document.getElementById("category-list"),
  merchantList: document.getElementById("merchant-list"),
  recurringList: document.getElementById("recurring-list"),
  monthFilter: document.getElementById("month-filter"),
  topCount: document.getElementById("top-count"),
  search: document.getElementById("search"),
  sampleBtn: document.getElementById("sample-btn"),
  bridgeStatus: document.getElementById("bridge-status"),
  rhAccounts: document.getElementById("rh-accounts"),
  rhEquities: document.getElementById("rh-equities"),
  rhOptions: document.getElementById("rh-options"),
};

const aliasMap = {
  date: ["date", "posted_date", "transaction_date", "trans_date", "datetime", "time", "posted", "booked_date"],
  description: ["description", "merchant", "name", "payee", "details", "memo", "original_description", "title"],
  amount: ["amount", "transaction_amount", "value", "net_amount", "net", "total"],
  debit: ["debit", "withdrawal", "withdrawals", "outflow", "money_out", "payment"],
  credit: ["credit", "deposit", "deposits", "inflow", "money_in", "received"],
  category: ["category", "type", "group", "spending_category"],
  account: ["account", "account_name", "account_title", "source_account", "destination_account"],
  balance: ["balance", "running_balance", "available_balance", "ending_balance"],
};

function normalizeHeader(value) {
  return String(value)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function stripMoney(value) {
  if (value == null) return "";
  return String(value).trim().replace(/[$,]/g, "");
}

function parseMoney(value) {
  const text = stripMoney(value);
  if (!text) return null;
  const paren = /^\((.*)\)$/.exec(text);
  if (paren) return -Math.abs(parseFloat(paren[1]));
  const num = parseFloat(text);
  return Number.isFinite(num) ? num : null;
}

function parseDate(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const parsed = new Date(text);
  if (!Number.isNaN(parsed.getTime())) return parsed;

  const parts = text.match(/^(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})$/);
  if (!parts) return null;
  const [, a, b, c] = parts;
  const first = Number(a);
  const second = Number(b);
  const third = Number(c);
  const year = a.length === 4 ? first : c.length === 4 ? third : 2000 + third;
  const month = a.length === 4 ? second : first > 12 ? second : first;
  const day = a.length === 4 ? third : first > 12 ? first : second;
  const dt = new Date(year, month - 1, day);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

function fmtMoney(value) {
  const n = Number(value || 0);
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

function fmtInt(value) {
  return Number(value || 0).toLocaleString();
}

function fmtMonth(date) {
  return date.toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

function maskAccount(accountNumber) {
  const text = String(accountNumber || "");
  return text.length > 4 ? `••••${text.slice(-4)}` : text;
}

function keyMonth(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function keyMonthDisplay(key) {
  const [year, month] = key.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

function normalizeText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function findColumn(headers, aliases) {
  for (const alias of aliases) {
    const match = headers.find((header) => normalizeHeader(header) === alias);
    if (match) return match;
  }
  return null;
}

function parseCsv(text) {
  const rows = [];
  const data = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  let row = [];
  let value = "";
  let quoted = false;

  const pushValue = () => {
    row.push(value);
    value = "";
  };

  const pushRow = () => {
    if (row.length > 1 || (row.length === 1 && String(row[0]).trim() !== "")) {
      rows.push(row);
    }
    row = [];
  };

  for (let i = 0; i < data.length; i += 1) {
    const char = data[i];
    const next = data[i + 1];
    if (quoted) {
      if (char === '"') {
        if (next === '"') {
          value += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        value += char;
      }
      continue;
    }

    if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      pushValue();
    } else if (char === "\n") {
      pushValue();
      pushRow();
    } else {
      value += char;
    }
  }

  pushValue();
  pushRow();
  return rows;
}

function detectTransactions(csvText, sourceName = "CSV") {
  const rows = parseCsv(csvText);
  if (!rows.length) return [];
  const headers = rows.shift().map((cell) => String(cell || "").trim());

  const dateCol = findColumn(headers, aliasMap.date);
  const descCol = findColumn(headers, aliasMap.description);
  const amountCol = findColumn(headers, aliasMap.amount);
  const debitCol = findColumn(headers, aliasMap.debit);
  const creditCol = findColumn(headers, aliasMap.credit);
  const categoryCol = findColumn(headers, aliasMap.category);
  const accountCol = findColumn(headers, aliasMap.account);
  const balanceCol = findColumn(headers, aliasMap.balance);

  if (!dateCol || (!amountCol && !debitCol && !creditCol) || !descCol) {
    throw new Error(
      `Could not find required columns in ${sourceName}. Need date/description and amount or debit/credit.`
    );
  }

  const index = (name) => headers.indexOf(name);
  const txs = [];

  for (const rawRow of rows) {
    if (rawRow.every((cell) => String(cell || "").trim() === "")) continue;
    const date = parseDate(rawRow[index(dateCol)]);
    if (!date) continue;

    let amount = parseMoney(rawRow[index(amountCol)]);
    if (!Number.isFinite(amount)) {
      const debit = parseMoney(rawRow[index(debitCol)]);
      const credit = parseMoney(rawRow[index(creditCol)]);
      if (Number.isFinite(credit) && credit !== 0) amount = Math.abs(credit);
      else if (Number.isFinite(debit) && debit !== 0) amount = -Math.abs(debit);
      else amount = 0;
    }

    const description = String(rawRow[index(descCol)] || "").trim() || "Unknown";
    const category = categoryCol ? String(rawRow[index(categoryCol)] || "").trim() : "";
    const account = accountCol ? String(rawRow[index(accountCol)] || "").trim() : "";
    const balance = balanceCol ? parseMoney(rawRow[index(balanceCol)]) : null;

    txs.push({
      date,
      description,
      amount,
      category: category || "Uncategorized",
      account: account || "Unknown",
      balance,
      source: sourceName,
    });
  }

  return txs;
}

function aggregateBy(items, keyFn, valueFn) {
  const map = new Map();
  for (const item of items) {
    const key = keyFn(item);
    const value = valueFn(item);
    map.set(key, (map.get(key) || 0) + value);
  }
  return [...map.entries()].sort((a, b) => b[1] - a[1]);
}

function aggregateMonthly(transactions) {
  const map = new Map();
  for (const tx of transactions) {
    const key = keyMonth(tx.date);
    map.set(key, (map.get(key) || 0) + tx.amount);
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

function detectRecurring(transactions) {
  const groups = new Map();
  for (const tx of transactions) {
    const key = `${normalizeText(tx.description)}|${Math.abs(tx.amount).toFixed(2)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(tx);
  }

  const patterns = [];
  for (const txs of groups.values()) {
    if (txs.length < 3) continue;
    const sorted = txs.slice().sort((a, b) => a.date - b.date);
    const gaps = [];
    for (let i = 1; i < sorted.length; i += 1) {
      gaps.push((sorted[i].date - sorted[i - 1].date) / (1000 * 60 * 60 * 24));
    }
    const avgGap = gaps.reduce((sum, gap) => sum + gap, 0) / gaps.length;
    if (avgGap >= 18 && avgGap <= 45) {
      patterns.push({
        description: sorted[0].description,
        count: txs.length,
        amount: txs[0].amount,
        cadence: avgGap < 27 ? "roughly biweekly" : "roughly monthly",
        lastDate: sorted[sorted.length - 1].date,
      });
    }
  }

  return patterns.sort((a, b) => b.count - a.count || Math.abs(b.amount) - Math.abs(a.amount));
}

function renderMetrics(transactions) {
  const income = transactions.filter((tx) => tx.amount > 0).reduce((sum, tx) => sum + tx.amount, 0);
  const expenses = transactions.filter((tx) => tx.amount < 0).reduce((sum, tx) => sum + Math.abs(tx.amount), 0);
  const net = income - expenses;
  const avg = transactions.length
    ? transactions.reduce((sum, tx) => sum + Math.abs(tx.amount), 0) / transactions.length
    : 0;
  const months = [...new Set(transactions.map((tx) => keyMonth(tx.date)))];
  const avgMonthlyNet = months.length
    ? transactions.reduce((sum, tx) => sum + tx.amount, 0) / months.length
    : 0;
  const savingsRate = income > 0 ? (net / income) * 100 : 0;
  const largestExpense = transactions
    .filter((tx) => tx.amount < 0)
    .sort((a, b) => a.amount - b.amount)[0];

  const cards = [
    {
      label: "Transactions",
      value: fmtInt(transactions.length),
      note: months.length ? `${months.length} month${months.length === 1 ? "" : "s"} covered` : "No date range",
    },
    {
      label: "Income",
      value: fmtMoney(income),
      note: "Total positive cash flow",
    },
    {
      label: "Expenses",
      value: fmtMoney(expenses),
      note: "Total spending",
    },
    {
      label: "Net",
      value: fmtMoney(net),
      note: `Average monthly net ${fmtMoney(avgMonthlyNet)}`,
    },
    {
      label: "Savings rate",
      value: `${savingsRate.toFixed(1)}%`,
      note: "Net / income",
    },
    {
      label: "Average size",
      value: fmtMoney(avg),
      note: largestExpense ? `Largest expense ${largestExpense.description}` : "No expense found",
    },
  ];

  els.metrics.innerHTML = cards
    .map(
      (card) => `
        <div class="metric">
          <div class="metric-label">${card.label}</div>
          <div class="metric-value">${card.value}</div>
          <div class="metric-note">${card.note}</div>
        </div>`
    )
    .join("");
}

function renderMonthlyChart(monthly) {
  if (!monthly.length) {
    els.monthlyChart.innerHTML = `<div class="empty-state">No monthly data yet.</div>`;
    return;
  }

  const width = 900;
  const height = 320;
  const padding = { top: 16, right: 18, bottom: 40, left: 18 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;
  const values = monthly.map(([, value]) => value);
  const maxAbs = Math.max(...values.map((v) => Math.abs(v)), 1);
  const zeroY = padding.top + innerH / 2;
  const baseline = zeroY;
  const gap = innerW / monthly.length;
  const barW = Math.max(24, Math.min(58, gap * 0.58));

  const bars = monthly
    .map(([month, value], index) => {
      const x = padding.left + gap * index + (gap - barW) / 2;
      const barH = (Math.abs(value) / maxAbs) * (innerH * 0.42);
      const y = value >= 0 ? baseline - barH : baseline;
      const labelY = height - 14;
      return `
        <rect x="${x}" y="${y}" width="${barW}" height="${Math.max(barH, 1)}" rx="12" class="${value >= 0 ? "positive" : "negative"}"></rect>
        <text x="${x + barW / 2}" y="${labelY}" text-anchor="middle" class="label">${keyMonthDisplay(month)}</text>
        <text x="${x + barW / 2}" y="${y - 6}" text-anchor="middle" class="label">${fmtMoney(value)}</text>
      `;
    })
    .join("");

  const gridLines = [-0.5, 0, 0.5].map((frac) => {
    const y = padding.top + innerH * (0.5 + frac / 1);
    return `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" class="gridline"></line>`;
  });

  els.monthlyChart.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Monthly cash flow chart">
      ${gridLines.join("")}
      <line x1="${padding.left}" y1="${baseline}" x2="${width - padding.right}" y2="${baseline}" class="zero"></line>
      ${bars}
    </svg>
  `;
}

function renderBars(target, entries, positive = true) {
  if (!entries.length) {
    target.innerHTML = `<div class="empty-state">No data to show.</div>`;
    return;
  }
  const max = Math.max(...entries.map(([, value]) => Math.abs(value)), 1);
  target.innerHTML = `
    <div class="bar-chart">
      ${entries
        .map(([name, value]) => {
          const amount = positive ? value : Math.abs(value);
          const pct = (Math.abs(value) / max) * 100;
          return `
            <div class="bar-row">
              <div class="bar-name">${name}</div>
              <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
              <div class="bar-value">${fmtMoney(amount)}</div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderRecurring(patterns) {
  if (!patterns.length) {
    els.recurringList.innerHTML = `<div class="empty-state">No recurring pattern found yet.</div>`;
    return;
  }

  els.recurringList.innerHTML = patterns
    .map(
      (pattern) => `
        <div class="pattern">
          <div class="pattern-head">
            <div class="pattern-desc">${pattern.description}</div>
            <div>${fmtMoney(pattern.amount)}</div>
          </div>
          <div class="pattern-meta">
            ${pattern.count} occurrences, ${pattern.cadence}, last seen ${fmtMonth(pattern.lastDate)}
          </div>
        </div>`
    )
    .join("");
}

function renderEmpty(target, text) {
  target.innerHTML = `<div class="empty-state">${text}</div>`;
}

function renderRobinhood(snapshot) {
  if (!snapshot || !snapshot.accounts) {
    els.bridgeStatus.textContent = "No Robinhood snapshot loaded.";
    renderEmpty(els.rhAccounts, "No accounts in the cache.");
    renderEmpty(els.rhEquities, "No equity positions in the cache.");
    renderEmpty(els.rhOptions, "No options positions in the cache.");
    return;
  }

  els.bridgeStatus.textContent = `Connected to local cache updated ${new Date(snapshot.updated_at).toLocaleString()}.`;

  const accounts = snapshot.accounts.slice();
  const equityTotals = Object.values(snapshot.equity_positions || {}).flat();
  const optionTotals = Object.values(snapshot.option_positions || {}).flat();

  els.rhAccounts.innerHTML = accounts
    .map((account) => {
      const equities = (snapshot.equity_positions?.[account.account_number] || []).length;
      const options = (snapshot.option_positions?.[account.account_number] || []).length;
      return `
        <div class="bridge-item">
          <div class="bridge-item-head">
            <div class="bridge-item-title">${account.nickname || account.brokerage_account_type}</div>
            <div>${maskAccount(account.account_number)}</div>
          </div>
          <div class="bridge-item-sub">
            ${account.brokerage_account_type} / ${account.type} / options ${account.option_level || "none"}
          </div>
          <div class="bridge-item-metrics">
            <span>${equities} equity position${equities === 1 ? "" : "s"}</span>
            <span>${options} option position${options === 1 ? "" : "s"}</span>
          </div>
        </div>
      `;
    })
    .join("");

  if (!equityTotals.length) {
    renderEmpty(els.rhEquities, "No equity positions found.");
  } else {
    els.rhEquities.innerHTML = equityTotals
      .map((position) => {
        const q = position.quote || {};
        const price = q.last_trade_price || 0;
        const value = Number(position.quantity) * Number(price);
        const cost = Number(position.quantity) * Number(position.average_buy_price || 0);
        const pnl = value - cost;
        const pnlClass = pnl >= 0 ? "bridge-positive" : "bridge-negative";
        const change = Number(price) - Number(q.previous_close || price);
        return `
          <div class="bridge-item">
            <div class="bridge-item-head">
              <div class="bridge-item-title">${position.symbol}</div>
              <div>${fmtMoney(value)}</div>
            </div>
            <div class="bridge-item-sub">
              ${Number(position.quantity).toFixed(6)} shares at ${fmtMoney(position.average_buy_price)}
            </div>
            <div class="bridge-item-metrics">
              <span>Last ${fmtMoney(price)} (${change >= 0 ? "+" : ""}${fmtMoney(change)})</span>
              <span class="${pnlClass}">${pnl >= 0 ? "+" : ""}${fmtMoney(pnl)}</span>
            </div>
          </div>
        `;
      })
      .join("");
  }

  if (!optionTotals.length) {
    renderEmpty(els.rhOptions, "No options positions found.");
  } else {
    els.rhOptions.innerHTML = optionTotals
      .map((position) => {
        const instr = position.instrument || {};
        const q = position.quote || {};
        const qty = Number(position.quantity);
        const multiplier = Number(position.trade_value_multiplier || 100);
        const mark = Number(q.mark_price || 0);
        const avg = Math.abs(Number(position.average_price || 0));
        const pnl = position.type === "short"
          ? (avg - mark) * multiplier * qty
          : (mark - avg) * multiplier * qty;
        const pnlClass = pnl >= 0 ? "bridge-positive" : "bridge-negative";
        return `
          <div class="bridge-item">
            <div class="bridge-item-head">
              <div class="bridge-item-title">${position.chain_symbol} ${instr.strike_price ? `$${Number(instr.strike_price).toFixed(2)}` : ""} ${instr.type || ""}</div>
              <div>${fmtMoney(mark)}</div>
            </div>
            <div class="bridge-item-sub">
              ${position.type} ${qty.toFixed(0)} contract${qty === 1 ? "" : "s"} exp ${position.expiration_date}
            </div>
            <div class="bridge-item-metrics">
              <span>Bid ${fmtMoney(q.bid_price || 0)} / Ask ${fmtMoney(q.ask_price || 0)}</span>
              <span class="${pnlClass}">${pnl >= 0 ? "+" : ""}${fmtMoney(pnl)}</span>
            </div>
          </div>
        `;
      })
      .join("");
  }

  const lastUpdated = snapshot.updated_at ? new Date(snapshot.updated_at).toLocaleString() : "unknown";
  els.bridgeStatus.textContent = `Local Robinhood cache loaded ${lastUpdated}. ${accounts.length} accounts, ${equityTotals.length} equity positions, ${optionTotals.length} option positions.`;
}

function renderFileList() {
  if (!state.files.length) {
    els.fileList.innerHTML = `<div class="file-empty">No files loaded yet.</div>`;
    return;
  }

  els.fileList.innerHTML = state.files
    .map(
      (file) => `
        <div class="file-row">
          <div>
            <div>${file.name}</div>
            <div class="file-meta">${fmtInt(file.rows)} rows</div>
          </div>
          <div class="file-meta">${fmtInt(file.bytes)} bytes</div>
        </div>`
    )
    .join("");
}

function renderMonthFilter(months) {
  const current = els.monthFilter.value;
  els.monthFilter.innerHTML = `<option value="">All months</option>${months
    .map((month) => `<option value="${month}">${keyMonthDisplay(month)}</option>`)
    .join("")}`;
  if (months.includes(current)) els.monthFilter.value = current;
}

function applyFilters() {
  const search = normalizeText(els.search.value);
  const month = els.monthFilter.value;
  let filtered = state.transactions.slice();
  if (month) {
    filtered = filtered.filter((tx) => keyMonth(tx.date) === month);
  }
  if (search) {
    filtered = filtered.filter((tx) => normalizeText(tx.description).includes(search));
  }

  state.filtered = filtered;
  updateView();
}

function updateView() {
  const transactions = state.filtered ?? state.transactions;
  const topCount = Math.max(3, Math.min(25, Number(els.topCount.value) || 8));
  state.topCount = topCount;

  if (!transactions.length) {
    els.status.textContent = state.transactions.length ? "No rows match the current filters." : "Waiting for a file.";
    renderMetrics([]);
    renderMonthlyChart([]);
    renderBars(els.categoryList, []);
    renderBars(els.merchantList, []);
    renderRecurring([]);
    return;
  }

  const income = transactions.filter((tx) => tx.amount > 0).reduce((sum, tx) => sum + tx.amount, 0);
  const expenses = transactions.filter((tx) => tx.amount < 0).reduce((sum, tx) => sum + Math.abs(tx.amount), 0);
  const months = [...new Set(transactions.map((tx) => keyMonth(tx.date)))];
  const monthly = aggregateMonthly(transactions);
  const categories = aggregateBy(
    transactions.filter((tx) => tx.amount < 0),
    (tx) => tx.category || "Uncategorized",
    (tx) => Math.abs(tx.amount)
  ).slice(0, topCount);
  const merchants = aggregateBy(
    transactions.filter((tx) => tx.amount < 0),
    (tx) => tx.description || "Unknown",
    (tx) => Math.abs(tx.amount)
  ).slice(0, topCount);
  const recurring = detectRecurring(transactions).slice(0, topCount);

  els.status.textContent = `${fmtInt(transactions.length)} rows loaded, ${months.length} month${months.length === 1 ? "" : "s"} shown.`;
  renderMetrics(transactions);
  renderMonthlyChart(monthly);
  renderBars(els.categoryList, categories);
  renderBars(els.merchantList, merchants);
  renderRecurring(recurring);
}

function ingestFiles(fileList) {
  const files = [...fileList].filter((file) => file.name.toLowerCase().endsWith(".csv"));
  if (!files.length) return;

  Promise.all(
    files.map((file) =>
      file.text().then((text) => ({
        name: file.name,
        bytes: file.size,
        rows: parseCsv(text).length - 1,
        transactions: detectTransactions(text, file.name),
      }))
    )
  )
    .then((loaded) => {
      const combined = loaded.flatMap((item) => item.transactions);
      combined.sort((a, b) => a.date - b.date);
      state.files = loaded.map(({ name, bytes, rows }) => ({ name, bytes, rows }));
      state.transactions = combined;
      state.months = [...new Set(combined.map((tx) => keyMonth(tx.date)))];
      state.filtered = combined.slice();
      renderFileList();
      renderMonthFilter(state.months);
      updateView();
    })
    .catch((error) => {
      els.status.textContent = error.message || "Failed to load CSV.";
      console.error(error);
    });
}

function loadSample() {
  const sample = `date,description,amount,category,account,balance
2026-01-01,Salary,5000,Income,Checking,5000
2026-01-03,Rent,-1800,Housing,Checking,3200
2026-01-04,Groceries,-142.73,Food,Checking,3057.27
2026-01-08,Spotify,-11.99,Entertainment,Checking,3045.28
2026-01-15,Uber,-24.55,Transport,Checking,3020.73
2026-02-01,Salary,5000,Income,Checking,8020.73
2026-02-03,Rent,-1800,Housing,Checking,6220.73
2026-02-06,Groceries,-153.44,Food,Checking,6067.29
2026-02-08,Spotify,-11.99,Entertainment,Checking,6055.30
2026-02-15,Uber,-19.12,Transport,Checking,6036.18`;
  const transactions = detectTransactions(sample, "sample.csv");
  state.files = [{ name: "sample.csv", bytes: sample.length, rows: transactions.length }];
  state.transactions = transactions;
  state.filtered = transactions.slice();
  state.months = [...new Set(transactions.map((tx) => keyMonth(tx.date)))];
  renderFileList();
  renderMonthFilter(state.months);
  updateView();
}

async function loadRobinhoodSnapshot() {
  try {
    const res = await fetch("/api/robinhood/snapshot", { cache: "no-store" });
    if (!res.ok) throw new Error(`Snapshot request failed: ${res.status}`);
    const snapshot = await res.json();
    state.robinhood = snapshot;
    renderRobinhood(snapshot);
  } catch (error) {
    console.error(error);
    els.bridgeStatus.textContent = "Robinhood bridge unavailable. Start the local server.";
    renderEmpty(els.rhAccounts, "Start the local server to load Robinhood data.");
    renderEmpty(els.rhEquities, "Start the local server to load Robinhood data.");
    renderEmpty(els.rhOptions, "Start the local server to load Robinhood data.");
  }
}

function wireEvents() {
  els.fileInput.addEventListener("change", (event) => ingestFiles(event.target.files));
  els.folderInput.addEventListener("change", (event) => ingestFiles(event.target.files));
  els.dropzone.addEventListener("click", () => els.fileInput.click());
  els.dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    els.dropzone.classList.add("dragover");
  });
  els.dropzone.addEventListener("dragleave", () => els.dropzone.classList.remove("dragover"));
  els.dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    els.dropzone.classList.remove("dragover");
    ingestFiles(event.dataTransfer.files);
  });
  els.monthFilter.addEventListener("change", applyFilters);
  els.topCount.addEventListener("input", updateView);
  els.search.addEventListener("input", applyFilters);
  els.sampleBtn.addEventListener("click", loadSample);
}

wireEvents();
renderFileList();
renderMonthFilter([]);
updateView();
loadRobinhoodSnapshot();
