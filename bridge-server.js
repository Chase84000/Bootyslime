const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const url = require("node:url");

const PORT = Number(process.env.PORT || 8787);
const ROOT = __dirname;
const CACHE_FILE = path.join(ROOT, "robinhood-cache.json");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".ps1": "text/plain; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

function readCache() {
  try {
    return JSON.parse(fs.readFileSync(CACHE_FILE, "utf8"));
  } catch {
    return {
      updated_at: null,
      accounts: [],
      equity_positions: {},
      option_positions: {},
      quotes: {},
      option_quotes: {},
      option_instruments: {},
    };
  }
}

function sendJson(res, code, body) {
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(body, null, 2));
}

function sendFile(res, filePath) {
  const ext = path.extname(filePath).toLowerCase();
  const type = MIME[ext] || "application/octet-stream";
  res.writeHead(200, { "Content-Type": type, "Cache-Control": "no-store" });
  fs.createReadStream(filePath).pipe(res);
}

function isStatic(pathname) {
  return ["/", "/index.html", "/app.js", "/styles.css", "/README.md", "/launch.ps1"].includes(pathname);
}

const server = http.createServer((req, res) => {
  const parsed = url.parse(req.url || "/", true);
  const pathname = parsed.pathname || "/";

  if (pathname === "/api/health") {
    sendJson(res, 200, { ok: true, updated_at: readCache().updated_at });
    return;
  }

  if (pathname === "/api/robinhood/snapshot") {
    sendJson(res, 200, readCache());
    return;
  }

  if (pathname === "/api/robinhood/accounts") {
    const cache = readCache();
    sendJson(res, 200, { updated_at: cache.updated_at, accounts: cache.accounts || [] });
    return;
  }

  if (pathname === "/api/robinhood/equities") {
    const cache = readCache();
    sendJson(res, 200, { updated_at: cache.updated_at, positions: cache.equity_positions || {} });
    return;
  }

  if (pathname === "/api/robinhood/options") {
    const cache = readCache();
    sendJson(res, 200, {
      updated_at: cache.updated_at,
      positions: cache.option_positions || {},
      instruments: cache.option_instruments || {},
      quotes: cache.option_quotes || {},
    });
    return;
  }

  if (isStatic(pathname)) {
    const file = pathname === "/" ? "index.html" : pathname.slice(1);
    sendFile(res, path.join(ROOT, file));
    return;
  }

  res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
  res.end("Not found");
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Finance Lens running at http://127.0.0.1:${PORT}`);
});
