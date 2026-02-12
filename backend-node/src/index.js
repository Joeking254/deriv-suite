require("dotenv").config();

const express = require("express");
const cors = require("cors");
const helmet = require("helmet");
const { DerivClient } = require("./derivClient");
const { saveToken, loadToken } = require("./tokenStore");
const { initMongo, insertTrade, updateTrade, listTrades } = require("./tradeStore");
const { makeLogger } = require("./logger");

const fetchCompat =
  global.fetch ||
  ((...args) => import("node-fetch").then(({ default: fetch }) => fetch(...args)));

const app = express();
app.use(helmet());
app.use(cors());
app.use(express.json({ limit: "1mb" }));

const logger = makeLogger(process.env.DERIV_NODE_LOG_LEVEL || "info");

const APP_ID = (process.env.DERIV_APP_ID || "").trim();
if (!APP_ID) {
  throw new Error("DERIV_APP_ID is required");
}

const DASH_USER = (process.env.DASH_USER || "").trim();
const DASH_PASS = (process.env.DASH_PASS || "").trim();
const ANALYSIS_BASE_URL = (process.env.ANALYSIS_BASE_URL || "http://127.0.0.1:8080").trim();

const contractCache = new Map();
const CONTRACT_TTL_MS = 10 * 60 * 1000;

const clients = new Set();
let portfolioTimer = null;
let lastStatus = "disconnected";
const openContractSubs = new Map();

function sendEvent(event, data) {
  const payload = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  clients.forEach((res) => {
    res.write(payload);
  });
}

function sendLog(level, message, meta = {}) {
  sendEvent("log", {
    ts: new Date().toISOString(),
    level,
    message,
    ...meta,
  });
}

function sendStatus(status) {
  lastStatus = status;
  sendEvent("status", { status });
  sendLog("info", `Deriv status ${status}`);
}

function authMiddleware(req, res, next) {
  if (!DASH_USER || !DASH_PASS) return next();
  const auth = req.headers.authorization || "";
  if (!auth.toLowerCase().startsWith("basic ")) {
    res.set("WWW-Authenticate", "Basic");
    return res.status(401).end();
  }
  const encoded = auth.split(" ", 2)[1] || "";
  const decoded = Buffer.from(encoded, "base64").toString("utf8");
  const [user, pass] = decoded.split(":", 2);
  if (user === DASH_USER && pass === DASH_PASS) {
    return next();
  }
  res.set("WWW-Authenticate", "Basic");
  return res.status(401).end();
}

app.use("/api/deriv", authMiddleware);

const deriv = new DerivClient(APP_ID, logger, sendStatus);
let authorizedToken = null;
let authorizedMode = null;
let botState = {
  running: false,
  last_error: null,
  last_trade: null,
  open_positions: 0,
};
let tickSymbol = null;

function parseDuration(value) {
  if (!value) return null;
  const trimmed = String(value).trim();
  const num = trimmed.replace(/[^0-9]/g, "");
  const unit = trimmed.replace(/[0-9]/g, "");
  if (!num || !unit) return null;
  return { amount: Number(num), unit: unit.toLowerCase() };
}

function durationToSeconds(amount, unit) {
  if (unit === "t") return amount;
  if (unit === "s") return amount;
  if (unit === "m") return amount * 60;
  if (unit === "h") return amount * 3600;
  if (unit === "d") return amount * 86400;
  return null;
}

function durationInRange(amount, unit, minDur, maxDur) {
  if (!minDur || !maxDur) return true;
  const minParsed = parseDuration(minDur);
  const maxParsed = parseDuration(maxDur);
  if (!minParsed || !maxParsed) return true;
  if (unit === minParsed.unit && unit === maxParsed.unit) {
    return amount >= minParsed.amount && amount <= maxParsed.amount;
  }
  const amountSec = durationToSeconds(amount, unit);
  const minSec = durationToSeconds(minParsed.amount, minParsed.unit);
  const maxSec = durationToSeconds(maxParsed.amount, maxParsed.unit);
  if (amountSec == null || minSec == null || maxSec == null) return false;
  return amountSec >= minSec && amountSec <= maxSec;
}

async function ensureAuthorized() {
  const stored = loadToken();
  if (!stored || !stored.token) {
    throw new Error("Deriv token not set");
  }
  await deriv.ensureConnected();
  if (authorizedToken !== stored.token) {
    await deriv.authorize(stored.token);
    authorizedToken = stored.token;
    authorizedMode = stored.account_mode || "demo";
    logger.info("Authorized Deriv token", { mode: authorizedMode });
    sendLog("info", "Authorized Deriv token", { mode: authorizedMode });
  }
  return stored;
}

function contractTypeLabel(input) {
  const value = String(input || "").trim().toUpperCase();
  if (value === "RISE" || value === "CALL") return "CALL";
  if (value === "FALL" || value === "PUT") return "PUT";
  return value;
}

async function getContracts(symbol) {
  const cached = contractCache.get(symbol);
  if (cached && Date.now() - cached.ts < CONTRACT_TTL_MS) {
    return cached.data;
  }
  await ensureAuthorized();
  const resp = await deriv.request({ contracts_for: symbol, currency: "USD" });
  const data = resp.contracts_for || {};
  contractCache.set(symbol, { ts: Date.now(), data });
  return data;
}

function validateTradePayload(rules, payload) {
  const available = rules.available || [];
  const contract = available.find((item) => item.contract_type === payload.contract_type);
  if (!contract) {
    return { ok: false, message: "Contract type not allowed for this symbol" };
  }
  const minDur = contract.min_duration;
  const maxDur = contract.max_duration;
  if (!durationInRange(payload.duration, payload.duration_unit, minDur, maxDur)) {
    return {
      ok: false,
      message: `Duration must be between ${minDur} and ${maxDur}`,
    };
  }
  const barrierCategory = contract.barrier_category || "none";
  if (barrierCategory !== "none" && !payload.barrier) {
    return {
      ok: false,
      message: `Barrier is required for ${payload.contract_type} (${barrierCategory})`,
    };
  }
  return { ok: true, contract };
}

async function createProposal(payload) {
  const proposal = await deriv.request({
    proposal: 1,
    amount: payload.stake,
    basis: "stake",
    contract_type: payload.contract_type,
    currency: payload.currency || "USD",
    duration: payload.duration,
    duration_unit: payload.duration_unit,
    symbol: payload.symbol,
    ...(payload.barrier ? { barrier: payload.barrier } : {}),
  });
  return proposal.proposal;
}

async function buyContract(proposal) {
  const buy = await deriv.request({ buy: proposal.id, price: proposal.ask_price });
  return buy.buy || {};
}

async function subscribeOpenContract(contractId) {
  if (!contractId) return;
  if (openContractSubs.has(contractId)) return;
  const label = `open_contract:${contractId}`;
  await deriv.subscribe(
    label,
    { proposal_open_contract: 1, contract_id: contractId },
    async (msg) => {
      const poc = msg.proposal_open_contract || {};
      const payload = {
        contract_id: poc.contract_id,
        symbol: poc.symbol,
        status: poc.status,
        is_sold: Boolean(poc.is_sold),
        is_expired: Boolean(poc.is_expired),
        entry_spot: poc.entry_spot,
        current_spot: poc.current_spot,
        bid_price: poc.bid_price,
        buy_price: poc.buy_price,
        sell_price: poc.sell_price,
        profit: poc.profit,
        profit_percentage: poc.profit_percentage,
        date_start: poc.date_start,
        date_expiry: poc.date_expiry,
        longcode: poc.longcode,
        barrier: poc.barrier,
        barrier2: poc.barrier2,
      };
      sendEvent("open_contract", payload);
      sendLog("debug", "Open contract update", { contract_id: payload.contract_id, status: payload.status });
      await updateTrade(payload.contract_id, {
        ...payload,
        updated_at: new Date(),
      });
      if (payload.is_sold) {
        sendEvent("trade_closed", payload);
        sendLog("info", "Trade closed", { contract_id: payload.contract_id, profit: payload.profit });
        await deriv.forget(label);
        openContractSubs.delete(contractId);
      }
    }
  );
  openContractSubs.set(contractId, label);
}

async function syncOpenContractSubscriptions(openPositions) {
  const activeIds = new Set((openPositions || []).map((p) => p.contract_id).filter(Boolean));
  for (const id of activeIds) {
    if (!openContractSubs.has(id)) {
      await subscribeOpenContract(id);
    }
  }
  for (const [contractId, label] of openContractSubs.entries()) {
    if (!activeIds.has(contractId)) {
      await deriv.forget(label);
      openContractSubs.delete(contractId);
    }
  }
}

async function subscribeTicks(symbol) {
  if (!symbol) return;
  tickSymbol = symbol;
  await deriv.forget("ticks");
  await deriv.subscribe(
    "ticks",
    { ticks: symbol },
    (msg) => {
      const tick = msg.tick || {};
      sendEvent("tick", {
        symbol: tick.symbol,
        quote: tick.quote,
        epoch: tick.epoch,
      });
    }
  );
}

async function fetchOpenPositions() {
  await ensureAuthorized();
  const resp = await deriv.request({ portfolio: 1 });
  const contracts = (resp.portfolio && resp.portfolio.contracts) || [];
  const open = contracts
    .filter((item) => !item.is_sold && item.status !== "sold" && item.status !== "expired")
    .map((item) => ({
      contract_id: item.contract_id,
      symbol: item.symbol,
      contract_type: item.contract_type,
      buy_price: item.buy_price,
      bid_price: item.bid_price,
      payout: item.payout,
      entry_spot: item.entry_spot,
      current_spot: item.current_spot,
      profit: item.profit,
      profit_percentage: item.profit_percentage,
      longcode: item.longcode,
      barrier: item.barrier,
      barrier2: item.barrier2,
      currency: item.currency || "USD",
      date_start: item.date_start,
      date_expiry: item.date_expiry,
    }));
  botState.open_positions = open.length;
  return open;
}

async function startPortfolioPolling() {
  if (portfolioTimer) return;
  portfolioTimer = setInterval(async () => {
    try {
      const open = await fetchOpenPositions();
      await syncOpenContractSubscriptions(open);
      sendEvent("open_positions", { open_positions: open });
    } catch (err) {
      logger.warn("Portfolio poll failed", { error: err.message });
    }
  }, 2000);
}

async function analyzeSymbol(symbol) {
  const authHeader =
    DASH_USER && DASH_PASS
      ? { Authorization: `Basic ${Buffer.from(`${DASH_USER}:${DASH_PASS}`).toString("base64")}` }
      : {};
  const res = await fetchCompat(`${ANALYSIS_BASE_URL}/api/analysis?symbol=${encodeURIComponent(symbol)}`, {
    headers: authHeader,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || "Analysis request failed");
  }
  return res.json();
}

let botTimer = null;
let botSettings = {
  loopSec: Number(process.env.BOT_LOOP_SEC || "12"),
  stake: Number(process.env.BOT_STAKE || "1"),
  duration: Number(process.env.BOT_DURATION || "1"),
  duration_unit: process.env.BOT_DURATION_UNIT || "m",
  contract_type: process.env.BOT_CONTRACT_TYPE || "CALL",
  symbols: [],
  maxOpen: Number(process.env.BOT_MAX_OPEN_POSITIONS || "0"),
};

async function botTick() {
  if (!botState.running) return;
  try {
    if (botSettings.maxOpen > 0 && botState.open_positions >= botSettings.maxOpen) {
      return;
    }
    const symbols = botSettings.symbols;
    if (!symbols || symbols.length === 0) return;
    const symbol = symbols.shift();
    symbols.push(symbol);
    const analysis = await analyzeSymbol(symbol);
    const signal = analysis.signal;
    if (!analysis.entry_ready || (signal !== "CALL" && signal !== "PUT")) {
      return;
    }
    const contractType = contractTypeLabel(signal);
    await executeTrade({
      symbol,
      contract_type: contractType,
      stake: botSettings.stake,
      duration: analysis.trade?.duration || botSettings.duration,
      duration_unit: analysis.trade?.duration_unit || botSettings.duration_unit,
      strategy: "analysis-confirmation",
      indicator_snapshot: analysis,
    });
  } catch (err) {
    botState.last_error = err.message;
    sendEvent("bot_error", { message: err.message });
  }
}

async function startBot(payload = {}) {
  botSettings.loopSec = Number(payload.loopSec || botSettings.loopSec || 12);
  botSettings.stake = Number(payload.stake || botSettings.stake || 1);
  botSettings.duration = Number(payload.duration || botSettings.duration || 1);
  botSettings.duration_unit = payload.duration_unit || botSettings.duration_unit || "m";
  botSettings.contract_type = payload.contract_type || botSettings.contract_type || "CALL";
  botSettings.maxOpen = Number(payload.max_open_positions ?? botSettings.maxOpen ?? 0);

  if (payload.symbols && Array.isArray(payload.symbols) && payload.symbols.length > 0) {
    botSettings.symbols = [...payload.symbols];
  } else if (!botSettings.symbols.length) {
    const symbolsResp = await deriv.request({ active_symbols: "brief", product_type: "basic" });
    botSettings.symbols = (symbolsResp.active_symbols || []).slice(0, 20).map((s) => s.symbol);
  }

  botState.running = true;
  botState.last_error = null;
  if (botTimer) clearInterval(botTimer);
  botTimer = setInterval(botTick, botSettings.loopSec * 1000);
  await subscribeTicks(botSettings.symbols[0]);
  sendEvent("bot_status", { running: true });
}

function stopBot() {
  botState.running = false;
  if (botTimer) clearInterval(botTimer);
  botTimer = null;
  sendEvent("bot_status", { running: false });
}

async function executeTrade(payload) {
  await ensureAuthorized();
  const contractType = contractTypeLabel(payload.contract_type);
  const rules = await getContracts(payload.symbol);
  const validation = validateTradePayload(rules, {
    symbol: payload.symbol,
    contract_type: contractType,
    duration: payload.duration,
    duration_unit: payload.duration_unit,
    barrier: payload.barrier,
  });
  if (!validation.ok) {
    throw new Error(validation.message);
  }

  let proposal;
  try {
    sendLog("info", "Proposal request", {
      symbol: payload.symbol,
      contract_type: contractType,
      duration: payload.duration,
      duration_unit: payload.duration_unit,
    });
    proposal = await createProposal({
      symbol: payload.symbol,
      contract_type: contractType,
      stake: payload.stake,
      duration: payload.duration,
      duration_unit: payload.duration_unit,
      barrier: payload.barrier,
      currency: payload.currency,
    });
  } catch (err) {
    throw new Error(err.message);
  }

  const buy = await buyContract(proposal);
  sendLog("info", "Buy response", {
    contract_id: buy.contract_id,
    transaction_id: buy.transaction_id,
  });
  const tradeDoc = {
    created_at: new Date(),
    symbol: payload.symbol,
    contract_type: contractType,
    duration: payload.duration,
    duration_unit: payload.duration_unit,
    barrier: payload.barrier || null,
    strategy: payload.strategy || "manual",
    indicator_snapshot: payload.indicator_snapshot || null,
    proposal_request: payload,
    proposal_response: proposal,
    buy_response: buy,
    contract_id: buy.contract_id,
    transaction_id: buy.transaction_id,
    buy_price: buy.buy_price,
    payout: buy.payout,
    currency: buy.currency,
  };
  await insertTrade(tradeDoc);
  botState.last_trade = tradeDoc;
  sendEvent("trade_opened", tradeDoc);
  sendLog("info", "Trade opened", {
    contract_id: buy.contract_id,
    symbol: payload.symbol,
  });

  if (buy.contract_id) {
    await subscribeOpenContract(buy.contract_id);
  }
  return tradeDoc;
}

app.post("/api/deriv/connect", async (req, res) => {
  const token = (req.body && req.body.token) || "";
  const account_mode = (req.body && req.body.account_mode) || "demo";
  if (!token || token.length < 5) {
    return res.status(400).json({ detail: "Token is required" });
  }
  try {
    saveToken(token, account_mode);
    await ensureAuthorized();
    await startPortfolioPolling();
    res.json({ status: "ok", account_mode });
  } catch (err) {
    res.status(400).json({ detail: err.message });
  }
});

app.get("/api/deriv/symbols", async (req, res) => {
  try {
    await ensureAuthorized();
    const resp = await deriv.request({ active_symbols: "brief", product_type: "basic" });
    res.json({ symbols: resp.active_symbols || [] });
  } catch (err) {
    res.status(400).json({ detail: err.message });
  }
});

app.get("/api/deriv/contracts", async (req, res) => {
  const symbol = String(req.query.symbol || "").trim();
  if (!symbol) return res.status(400).json({ detail: "symbol is required" });
  try {
    const data = await getContracts(symbol);
    res.json(data);
  } catch (err) {
    res.status(400).json({ detail: err.message });
  }
});

app.get("/api/deriv/open-positions", async (req, res) => {
  try {
    const open = await fetchOpenPositions();
    res.json({ open_positions: open });
  } catch (err) {
    res.status(400).json({ detail: err.message });
  }
});

app.post("/api/deriv/bot/start", async (req, res) => {
  try {
    await ensureAuthorized();
    await startPortfolioPolling();
    await startBot(req.body || {});
    res.json({ status: "running" });
  } catch (err) {
    botState.last_error = err.message;
    res.status(400).json({ detail: err.message });
  }
});

app.post("/api/deriv/bot/stop", async (req, res) => {
  stopBot();
  res.json({ status: "stopped" });
});

app.post("/api/deriv/stream/subscribe", async (req, res) => {
  const symbol = String((req.body && req.body.symbol) || "").trim();
  if (!symbol) return res.status(400).json({ detail: "symbol is required" });
  try {
    await ensureAuthorized();
    await subscribeTicks(symbol);
    res.json({ status: "ok", symbol });
  } catch (err) {
    res.status(400).json({ detail: err.message });
  }
});

app.get("/api/deriv/bot/status", async (req, res) => {
  res.json({
    running: botState.running,
    last_error: botState.last_error,
    last_trade: botState.last_trade,
    open_positions: botState.open_positions,
  });
});

app.get("/api/deriv/trades/history", async (req, res) => {
  const limit = Number(req.query.limit || "50");
  const trades = await listTrades(limit);
  res.json({ trades });
});

app.get("/api/deriv/stream", async (req, res) => {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  res.write(`event: status\ndata: ${JSON.stringify({ status: lastStatus })}\n\n`);
  clients.add(res);
  const keepAlive = setInterval(() => {
    res.write(": ping\n\n");
  }, 15000);
  req.on("close", () => {
    clearInterval(keepAlive);
    clients.delete(res);
  });
});

app.post("/api/deriv/trade", async (req, res) => {
  try {
    const payload = req.body || {};
    if (!payload.symbol || !payload.contract_type) {
      return res.status(400).json({ detail: "symbol and contract_type are required" });
    }
    const trade = await executeTrade(payload);
    res.json({ trade });
  } catch (err) {
    res.status(400).json({ detail: err.message });
  }
});

const port = Number(process.env.DERIV_NODE_PORT || "8081");
const host = process.env.DERIV_NODE_HOST || "0.0.0.0";

initMongo(process.env.MONGODB_URI, process.env.MONGODB_DB, logger).catch((err) => {
  logger.error("Mongo init failed", { error: err.message });
});

app.listen(port, host, async () => {
  logger.info("Deriv automation server running", { host, port });
  try {
    const stored = loadToken();
    if (stored && stored.token) {
      await ensureAuthorized();
      await startPortfolioPolling();
    }
  } catch (err) {
    logger.warn("Startup auth failed", { error: err.message });
  }
});
