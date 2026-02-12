const statusText = document.getElementById("statusText");
const statusDot = document.getElementById("statusDot");
const symbolSelect = document.getElementById("symbolSelect");
const symbolCount = document.getElementById("symbolCount");
const modeBadge = document.getElementById("modeBadge");
const tradeModeBadge = document.getElementById("tradeModeBadge");
const balanceValue = document.getElementById("balanceValue");
const pnlValue = document.getElementById("pnlValue");
const durationLabel = document.getElementById("durationLabel");
const granularityLabel = document.getElementById("granularityLabel");
const signalPill = document.getElementById("signalPill");
const lastClose = document.getElementById("lastClose");
const rsiValue = document.getElementById("rsiValue");
const emaFastValue = document.getElementById("emaFastValue");
const emaSlowValue = document.getElementById("emaSlowValue");
const trendValue = document.getElementById("trendValue");
const macdHistValue = document.getElementById("macdHistValue");
const bbPositionValue = document.getElementById("bbPositionValue");
const bbUpperValue = document.getElementById("bbUpperValue");
const bbLowerValue = document.getElementById("bbLowerValue");
const confirmScoreValue = document.getElementById("confirmScoreValue");
const confirmRequiredValue = document.getElementById("confirmRequiredValue");
const confirmations = document.getElementById("confirmations");
const scanGrid = document.getElementById("scanGrid");
const modeSelect = document.getElementById("modeSelect");
const demoToken = document.getElementById("demoToken");
const liveToken = document.getElementById("liveToken");
const saveTokens = document.getElementById("saveTokens");
const tokenStatus = document.getElementById("tokenStatus");
const placeTrade = document.getElementById("placeTrade");
const tradeStatus = document.getElementById("tradeStatus");
const durationHint = document.getElementById("durationHint");
const openContractsList = document.getElementById("openContracts");
const openContractsEmpty = document.getElementById("openContractsEmpty");
const botStart = document.getElementById("botStart");
const botStop = document.getElementById("botStop");
const botStatus = document.getElementById("botStatus");
const derivStatus = document.getElementById("derivStatus");
const botLogs = document.getElementById("botLogs");
const tickCanvas = document.getElementById("tickCanvas");
const tickPrice = document.getElementById("tickPrice");

let currentSignal = "WAIT";
let symbolMeta = {};
let activeContractId = null;
let tradePoll = null;
let activeMode = "demo";
let derivStream = null;
let tickSymbol = null;
let tickData = [];
let openContractDetails = {};
let lastOpenPositions = [];

const refreshAnalysis = document.getElementById("refreshAnalysis");
const refreshScan = document.getElementById("refreshScan");
const scanNow = document.getElementById("scanNow");

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || "Request failed");
  }
  return res.json();
}

function setStatus(ok) {
  statusText.textContent = ok ? "Connected" : "Disconnected";
  statusDot.style.background = ok ? "var(--signal-call)" : "var(--signal-put)";
}

function setSignal(signal) {
  currentSignal = signal || "WAIT";
  signalPill.textContent = signal || "WAIT";
  signalPill.style.color = "var(--signal-wait)";
  signalPill.style.background = "rgba(141, 124, 104, 0.16)";
  if (signal === "CALL") {
    signalPill.style.color = "white";
    signalPill.style.background = "var(--signal-call)";
  }
  if (signal === "PUT") {
    signalPill.style.color = "white";
    signalPill.style.background = "var(--signal-put)";
  }
}

function clearConfirmations() {
  confirmations.innerHTML = "";
}

function addConfirmation(text, variant) {
  const chip = document.createElement("span");
  chip.className = `chip ${variant || ""}`.trim();
  chip.textContent = text;
  confirmations.appendChild(chip);
}

function formatNumber(value) {
  if (value === null || value === undefined) return "--";
  if (typeof value === "number") return value.toFixed(4);
  return value;
}

async function loadHealth() {
  try {
    await getJSON("/api/health");
    setStatus(true);
  } catch (err) {
    setStatus(false);
  }
}

async function loadConfig() {
  const config = await getJSON("/api/config");
  modeBadge.textContent = config.dry_run ? "DRY RUN" : "LIVE";
  if (tradeModeBadge) {
    tradeModeBadge.textContent = (config.trade_mode || "auto").toUpperCase();
  }
  durationLabel.textContent = `${config.duration} ${config.duration_unit}`;
  granularityLabel.textContent = `${config.candle_granularity}s`;
  const strategyName = document.getElementById("strategyName");
  if (strategyName) {
    strategyName.textContent = "RSI + EMA + MACD + BB";
  }
}

async function loadBalance() {
  if (!balanceValue) return;
  try {
    const data = await getJSON("/api/balance");
    if (typeof data.balance === "number") {
      const cur = data.currency ? ` ${data.currency}` : "";
      balanceValue.textContent = `${data.balance.toFixed(2)}${cur}`;
      if (pnlValue) {
        const pnlKey = `pnlBase:${activeMode}`;
        const stored = localStorage.getItem(pnlKey);
        let base = stored ? Number.parseFloat(stored) : Number.NaN;
        if (!Number.isFinite(base)) {
          base = data.balance;
          localStorage.setItem(pnlKey, `${base}`);
        }
        const pnl = data.balance - base;
        const sign = pnl >= 0 ? "+" : "";
        pnlValue.textContent = `${sign}${pnl.toFixed(2)}${cur}`;
        pnlValue.style.color = pnl >= 0 ? "var(--signal-call)" : "var(--signal-put)";
      }
    } else {
      balanceValue.textContent = "--";
      if (pnlValue) pnlValue.textContent = "--";
    }
  } catch (err) {
    balanceValue.textContent = "--";
    if (pnlValue) pnlValue.textContent = "--";
  }
}

function appendBotLog(level, message) {
  if (!botLogs) return;
  const line = document.createElement("div");
  line.className = "log-line";
  const levelTag = document.createElement("span");
  levelTag.className = `log-level ${level || "info"}`;
  levelTag.textContent = level ? level.toUpperCase() : "INFO";
  const msg = document.createElement("span");
  msg.textContent = message;
  line.appendChild(levelTag);
  line.appendChild(msg);
  botLogs.prepend(line);
  while (botLogs.childElementCount > 40) {
    botLogs.removeChild(botLogs.lastChild);
  }
}

function renderOpenContracts(contracts) {
  if (!openContractsList || !openContractsEmpty) return;
  openContractsList.innerHTML = "";
  if (!contracts || contracts.length === 0) {
    openContractsEmpty.textContent = "No open contracts.";
    openContractsEmpty.style.display = "block";
    return;
  }
  openContractsEmpty.style.display = "none";
  contracts.forEach((item) => {
    const merged = { ...item, ...(openContractDetails[item.contract_id] || {}) };
    const card = document.createElement("div");
    card.className = "position-card";
    const profit = typeof merged.profit === "number" ? merged.profit : 0;
    const cur = merged.currency ? ` ${merged.currency}` : "";
    const sign = profit >= 0 ? "+" : "";
    const pnlText = `${sign}${profit.toFixed(2)}${cur}`;
    const symbolName = symbolMeta[merged.symbol]?.display_name || merged.symbol || "--";
    const type = merged.contract_type || "--";
    const typeLabel = type === "CALL" ? "Rise" : type === "PUT" ? "Fall" : type;
    const typeClass = type === "CALL" ? "call" : type === "PUT" ? "put" : "wait";
    const stake = typeof merged.buy_price === "number" ? merged.buy_price.toFixed(2) : "--";
    const payout = typeof merged.payout === "number" ? merged.payout.toFixed(2) : "--";
    const contractValue = typeof merged.bid_price === "number" ? merged.bid_price.toFixed(2) : stake;
    const timeLeft = formatTimeLeft(merged.date_expiry);
    const started = formatTimestamp(merged.date_start);
    const currentSpot = typeof merged.current_spot === "number" ? merged.current_spot.toFixed(5) : "--";
    const entrySpot = typeof merged.entry_spot === "number" ? merged.entry_spot.toFixed(5) : "--";
    const pct = typeof merged.profit_percentage === "number" ? `${merged.profit_percentage.toFixed(2)}%` : "--";
    const longcode = merged.longcode ? `${merged.longcode}` : "";
    const barrier = merged.barrier ? `Barrier: ${merged.barrier}` : "";
    const barrier2 = merged.barrier2 ? `Barrier2: ${merged.barrier2}` : "";

    card.innerHTML = `
      <div class="position-top">
        <div class="position-symbol">
          <strong>${symbolName}</strong>
          <span class="position-pill ${typeClass}">${typeLabel}</span>
        </div>
        <strong style="color:${profit >= 0 ? "var(--signal-call)" : "var(--signal-put)"}">${pnlText}</strong>
      </div>
      <div class="position-meta">
        <span>Time left: ${timeLeft}</span>
        <span>Opened: ${started}</span>
        <span>ID: ${merged.contract_id || "--"}</span>
        ${barrier ? `<span>${barrier}</span>` : ""}
        ${barrier2 ? `<span>${barrier2}</span>` : ""}
      </div>
      ${longcode ? `<div class="hint">${longcode}</div>` : ""}
      <div class="position-stats">
        <div class="position-stat">
          Stake
          <strong>${stake}${cur}</strong>
        </div>
        <div class="position-stat">
          Payout
          <strong>${payout}${cur}</strong>
        </div>
        <div class="position-stat">
          Contract value
          <strong>${contractValue}${cur}</strong>
        </div>
        <div class="position-stat">
          Entry spot
          <strong>${entrySpot}</strong>
        </div>
        <div class="position-stat">
          Current spot
          <strong>${currentSpot}</strong>
        </div>
        <div class="position-stat">
          PnL
          <strong style="color:${profit >= 0 ? "var(--signal-call)" : "var(--signal-put)"}">${pnlText} (${pct})</strong>
        </div>
      </div>
    `;
    openContractsList.appendChild(card);
  });
}

function formatTimeLeft(expiry) {
  if (!expiry) return "--";
  const now = Math.floor(Date.now() / 1000);
  const remaining = Math.max(0, Number(expiry) - now);
  const mins = Math.floor(remaining / 60);
  const secs = remaining % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function formatTimestamp(ts) {
  if (!ts) return "--";
  const date = new Date(Number(ts) * 1000);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

async function loadOpenContracts() {
  if (!openContractsList || !openContractsEmpty) return;
  try {
    const data = await getJSON("/api/deriv/open-positions");
    lastOpenPositions = data.open_positions || [];
    renderOpenContracts(lastOpenPositions);
  } catch (err) {
    try {
      const fallback = await getJSON("/api/open-contracts");
      lastOpenPositions = fallback.open_contracts || [];
      renderOpenContracts(lastOpenPositions);
    } catch (err2) {
      openContractsList.innerHTML = "";
      openContractsEmpty.textContent = "Unable to load open contracts.";
      openContractsEmpty.style.display = "block";
    }
  }
}

async function loadAuth() {
  if (!modeSelect || !tokenStatus) return;
  try {
    const data = await getJSON("/api/auth");
    activeMode = data.active_mode || "demo";
    modeSelect.value = activeMode;
    const demoSet = data.demo_token_set ? "set" : "missing";
    const liveSet = data.live_token_set ? "set" : "missing";
    tokenStatus.textContent = `Status: demo ${demoSet} | live ${liveSet}`;
  } catch (err) {
    tokenStatus.textContent = "Status: unable to load";
  }
}

async function saveAuth() {
  if (!modeSelect || !tokenStatus) return;
  const payload = { active_mode: modeSelect.value };
  if (demoToken && demoToken.value.trim().length > 0) {
    payload.demo_token = demoToken.value.trim();
  }
  if (liveToken && liveToken.value.trim().length > 0) {
    payload.live_token = liveToken.value.trim();
  }
  try {
    const res = await fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || "Failed");
    }
    if (demoToken) demoToken.value = "";
    if (liveToken) liveToken.value = "";
    const selected = modeSelect.value;
    const connectToken =
      selected === "demo" ? payload.demo_token : selected === "live" ? payload.live_token : null;
    if (connectToken) {
      await fetch("/api/deriv/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: connectToken, account_mode: selected }),
      });
      appendBotLog("info", `Deriv connected (${selected})`);
    }
    await loadAuth();
    await loadBalance();
  } catch (err) {
    tokenStatus.textContent = "Status: save failed";
    appendBotLog("error", err.message || "Token save failed");
  }
}

async function loadSymbols() {
  const data = await getJSON("/api/active-symbols?brief=false&open_only=true");
  const symbols = data.symbols || [];
  symbolCount.textContent = data.count || symbols.length;
  symbolSelect.innerHTML = "";
  symbolMeta = {};
  symbols.forEach((item) => {
    const symbol = item.symbol;
    const opt = document.createElement("option");
    opt.value = symbol;
    opt.textContent = item.display_name ? `${item.display_name} (${symbol})` : symbol;
    symbolMeta[symbol] = item;
    symbolSelect.appendChild(opt);
  });
  if (symbols.length > 0) {
    symbolSelect.value = symbols[0].symbol;
  }
}

async function loadAnalysis() {
  const symbol = symbolSelect.value;
  if (!symbol) return;
  setSignal("WAIT");
  clearConfirmations();
  addConfirmation("Loading indicator confirmations...", "wait");
  try {
    const data = await getJSON(`/api/analysis?symbol=${encodeURIComponent(symbol)}`);
    setSignal(data.signal);
    lastClose.textContent = formatNumber(data.last_close);
    rsiValue.textContent = formatNumber(data.rsi);
    emaFastValue.textContent = formatNumber(data.ema_fast);
    emaSlowValue.textContent = formatNumber(data.ema_slow);
    trendValue.textContent = data.trend;
    macdHistValue.textContent = formatNumber(data.macd_hist);
    bbPositionValue.textContent = formatNumber(data.bb_position);
    bbUpperValue.textContent = formatNumber(data.bb_upper);
    bbLowerValue.textContent = formatNumber(data.bb_lower);
    confirmScoreValue.textContent = `${data.confirmation_score ?? "--"}`;
    confirmRequiredValue.textContent = `${data.confirmations_required ?? "--"}`;
    clearConfirmations();
    if (data.confirmations && data.confirmations.length) {
      data.confirmations.forEach((text) => {
        let variant = "wait";
        if (data.signal === "CALL") variant = "call";
        if (data.signal === "PUT") variant = "put";
        addConfirmation(text, variant);
      });
    } else {
      addConfirmation("No confirmations yet", "wait");
    }
    if (tradeStatus) {
      tradeStatus.textContent = `Trade status: ${data.signal === "WAIT" ? "No signal" : "Ready"}`;
    }
    applyTradeParams(data.trade, data.trade_params);
  } catch (err) {
    clearConfirmations();
    addConfirmation("Failed to load analysis", "put");
    if (tradeStatus) {
      tradeStatus.textContent = "Trade status: error";
    }
    if (durationHint) {
      durationHint.textContent = "Allowed duration: --";
    }
  }
}

function renderScanCard(result) {
  const card = document.createElement("div");
  card.className = "scan-card";
  const signalClass = result.signal === "CALL" ? "var(--signal-call)" : result.signal === "PUT" ? "var(--signal-put)" : "var(--signal-wait)";
  card.innerHTML = `
    <h4>${result.symbol}</h4>
    <div class="scan-signal" style="color:${signalClass}">${result.signal}</div>
    <p>RSI ${formatNumber(result.rsi)}</p>
    <p>Trend ${result.trend}</p>
    <p>Score ${result.confirmation_score}/${result.confirmations_required}</p>
  `;
  return card;
}

async function loadScan() {
  scanGrid.innerHTML = "";
  const loading = document.createElement("div");
  loading.className = "scan-card";
  loading.textContent = "Scanning markets...";
  scanGrid.appendChild(loading);
  try {
    const data = await getJSON("/api/scan");
    scanGrid.innerHTML = "";
    if (!data.results || data.results.length === 0) {
      const empty = document.createElement("div");
      empty.className = "scan-card";
      empty.textContent = "No signals found yet.";
      scanGrid.appendChild(empty);
      return;
    }
    data.results.forEach((result) => {
      scanGrid.appendChild(renderScanCard(result));
    });
  } catch (err) {
    scanGrid.innerHTML = "";
    const fail = document.createElement("div");
    fail.className = "scan-card";
    fail.textContent = "Scan failed. Try again.";
    scanGrid.appendChild(fail);
  }
}

function applyTradeParams(recommended, tradeParams) {
  if (!durationHint) return;
  const trade = recommended || null;
  if (!trade && tradeParams) {
    trade = tradeParams.CALL || tradeParams.PUT || null;
  }
  if (!trade) {
    durationHint.textContent = "Allowed duration: unavailable";
    return;
  }

  const minLabel = trade.min_duration || "--";
  const maxLabel = trade.max_duration || "--";
  const contractLabel = trade.contract_type ? ` (${trade.contract_type})` : "";
  const recommendedLabel = trade.duration && trade.duration_unit ? ` | recommended ${trade.duration}${trade.duration_unit}` : "";
  durationHint.textContent = `Allowed duration${contractLabel}: ${minLabel} to ${maxLabel}${recommendedLabel}`;
}

function clearTradePoll() {
  if (tradePoll) {
    clearTimeout(tradePoll);
    tradePoll = null;
  }
  activeContractId = null;
}

async function pollContract(contractId, attempt = 0) {
  if (!tradeStatus || !contractId) return;
  const maxAttempts = 160;
  try {
    const data = await getJSON(`/api/contract?contract_id=${encodeURIComponent(contractId)}`);
    const status = data.status || (data.is_sold ? "sold" : "open");
    const profit = typeof data.profit === "number" ? data.profit.toFixed(2) : "--";
    const currency = data.currency ? ` ${data.currency}` : "";
    const contract = data.contract_id ? ` | id ${data.contract_id}` : "";
    if (data.is_sold) {
      tradeStatus.textContent = `Trade status: closed (${status}) | profit ${profit}${currency}${contract}`;
      clearTradePoll();
      placeTrade.disabled = false;
      await loadBalance();
      return;
    }
    tradeStatus.textContent = `Trade status: open (${status}) | P/L ${profit}${currency}${contract}`;
  } catch (err) {
    tradeStatus.textContent = `Trade status: error (${err.message || "poll failed"})`;
  }

  if (attempt >= maxAttempts) {
    tradeStatus.textContent = "Trade status: still open (check later)";
    placeTrade.disabled = false;
    clearTradePoll();
    return;
  }
  tradePoll = setTimeout(() => pollContract(contractId, attempt + 1), 3000);
}

function revealElements() {
  const elements = Array.from(document.querySelectorAll("[data-reveal]"));
  elements.forEach((el, index) => {
    el.style.animationDelay = `${index * 120}ms`;
    el.classList.add("reveal");
  });
}

refreshAnalysis.addEventListener("click", loadAnalysis);
refreshScan.addEventListener("click", loadScan);
scanNow.addEventListener("click", loadScan);
symbolSelect.addEventListener("change", loadAnalysis);
symbolSelect.addEventListener("change", () => subscribeTicks(symbolSelect.value));
if (saveTokens) {
  saveTokens.addEventListener("click", saveAuth);
}

async function placeTradeNow() {
  if (!placeTrade || !tradeStatus) return;
  if (currentSignal === "WAIT") {
    tradeStatus.textContent = "Trade status: no signal to trade";
    return;
  }
  const symbol = symbolSelect.value;
  if (!symbol) return;
  placeTrade.disabled = true;
  tradeStatus.textContent = "Trade status: placing trade...";
  clearTradePoll();
  try {
    const res = await fetch("/api/trade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol,
        direction: currentSignal,
        wait: false,
      }),
    });
    if (!res.ok) {
      const text = await res.text();
      let detail = text || "Trade failed";
      try {
        const parsed = JSON.parse(text);
        if (parsed && parsed.detail) {
          detail = parsed.detail;
        }
      } catch (err) {
        // ignore JSON parse errors
      }
      throw new Error(detail);
    }
    const data = await res.json();
    const currency = data.currency ? ` ${data.currency}` : "";
    const contract = data.contract_id ? ` | id ${data.contract_id}` : "";
    const duration = data.duration ? ` | ${data.duration}${data.duration_unit || ""}` : "";
    const adjusted = data.duration_adjusted ? " | adjusted" : "";
    if (data.is_sold) {
      const profit = typeof data.profit === "number" ? data.profit.toFixed(2) : data.profit;
      const status = data.status ? ` | ${data.status}` : "";
      tradeStatus.textContent = `Trade status: ${data.direction} closed (profit ${profit}${currency})${duration}${adjusted}${status}${contract}`;
      await loadBalance();
      await loadOpenContracts();
      return;
    }
    tradeStatus.textContent = `Trade status: open${duration}${adjusted}${contract}`;
    activeContractId = data.contract_id;
    tradePoll = setTimeout(() => pollContract(activeContractId, 0), 2000);
    await loadOpenContracts();
  } catch (err) {
    tradeStatus.textContent = `Trade status: failed (${err.message || "unknown error"})`;
  } finally {
    placeTrade.disabled = false;
  }
}

if (placeTrade) {
  placeTrade.addEventListener("click", placeTradeNow);
}

async function startBot() {
  if (!botStatus) return;
  botStatus.textContent = "Bot status: starting...";
  try {
    const res = await fetch("/api/deriv/bot/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || "Bot start failed");
    }
    botStatus.textContent = "Bot status: running";
  } catch (err) {
    botStatus.textContent = `Bot status: error (${err.message || "failed"})`;
    appendBotLog("error", err.message || "Bot start failed");
  }
}

async function stopBot() {
  if (!botStatus) return;
  botStatus.textContent = "Bot status: stopping...";
  try {
    const res = await fetch("/api/deriv/bot/stop", { method: "POST" });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || "Bot stop failed");
    }
    botStatus.textContent = "Bot status: stopped";
  } catch (err) {
    botStatus.textContent = `Bot status: error (${err.message || "failed"})`;
    appendBotLog("error", err.message || "Bot stop failed");
  }
}

async function loadBotStatus() {
  if (!botStatus) return;
  try {
    const data = await getJSON("/api/deriv/bot/status");
    botStatus.textContent = data.running ? "Bot status: running" : "Bot status: stopped";
    if (data.last_error) {
      appendBotLog("error", data.last_error);
    }
  } catch (err) {
    botStatus.textContent = "Bot status: unavailable";
  }
}

function initDerivStream() {
  if (derivStream) return;
  derivStream = new EventSource("/api/deriv/stream");
  derivStream.addEventListener("status", (event) => {
    try {
      const data = JSON.parse(event.data || "{}");
      if (derivStatus) {
        derivStatus.textContent = `Deriv stream: ${data.status || "--"}`;
      }
    } catch (err) {
      // ignore
    }
  });
  derivStream.addEventListener("open_positions", (event) => {
    try {
      const data = JSON.parse(event.data || "{}");
      lastOpenPositions = data.open_positions || [];
      renderOpenContracts(lastOpenPositions);
    } catch (err) {
      // ignore
    }
  });
  derivStream.addEventListener("tick", (event) => {
    try {
      const data = JSON.parse(event.data || "{}");
      if (data.symbol && typeof data.quote === "number") {
        if (tickSymbol === data.symbol) {
          updateTick(data.quote);
        }
      }
    } catch (err) {
      // ignore
    }
  });
  derivStream.addEventListener("open_contract", (event) => {
    try {
      const data = JSON.parse(event.data || "{}");
      if (data.contract_id) {
        openContractDetails[data.contract_id] = {
          bid_price: data.bid_price,
          longcode: data.longcode,
          barrier: data.barrier,
          barrier2: data.barrier2,
          profit_percentage: data.profit_percentage,
          current_spot: data.current_spot,
          entry_spot: data.entry_spot,
          profit: data.profit,
          date_start: data.date_start,
          date_expiry: data.date_expiry,
        };
        renderOpenContracts(lastOpenPositions);
      }
      appendBotLog("info", `Open contract update ${data.contract_id || ""}`);
    } catch (err) {
      // ignore
    }
  });
  derivStream.addEventListener("trade_opened", (event) => {
    try {
      const data = JSON.parse(event.data || "{}");
      appendBotLog("info", `Trade opened ${data.contract_id || ""}`);
    } catch (err) {
      // ignore
    }
  });
  derivStream.addEventListener("trade_closed", (event) => {
    try {
      const data = JSON.parse(event.data || "{}");
      appendBotLog("info", `Trade closed ${data.contract_id || ""}`);
    } catch (err) {
      // ignore
    }
  });
  derivStream.addEventListener("bot_error", (event) => {
    try {
      const data = JSON.parse(event.data || "{}");
      appendBotLog("error", data.message || "Bot error");
    } catch (err) {
      // ignore
    }
  });
  derivStream.addEventListener("log", (event) => {
    try {
      const data = JSON.parse(event.data || "{}");
      appendBotLog(data.level || "info", data.message || "");
    } catch (err) {
      // ignore
    }
  });
  derivStream.onerror = () => {
    if (derivStatus) {
      derivStatus.textContent = "Deriv stream: disconnected";
    }
  };
}

if (botStart) {
  botStart.addEventListener("click", startBot);
}
if (botStop) {
  botStop.addEventListener("click", stopBot);
}

async function subscribeTicks(symbol) {
  if (!symbol) return;
  if (tickSymbol === symbol) return;
  tickSymbol = symbol;
  tickData = [];
  drawTickChart();
  if (tickPrice) tickPrice.textContent = "--";
  try {
    await fetch("/api/deriv/stream/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    });
  } catch (err) {
    // ignore
  }
}

function updateTick(quote) {
  tickData.push(quote);
  if (tickData.length > 60) {
    tickData.shift();
  }
  if (tickPrice) {
    tickPrice.textContent = quote.toFixed(5);
  }
  drawTickChart();
}

function drawTickChart() {
  if (!tickCanvas) return;
  const ctx = tickCanvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, tickCanvas.width, tickCanvas.height);
  if (tickData.length < 2) return;
  const min = Math.min(...tickData);
  const max = Math.max(...tickData);
  const range = max - min || 1;
  const w = tickCanvas.width;
  const h = tickCanvas.height;
  ctx.beginPath();
  tickData.forEach((val, idx) => {
    const x = (idx / (tickData.length - 1)) * w;
    const y = h - ((val - min) / range) * h;
    if (idx === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  const rising = tickData[tickData.length - 1] >= tickData[0];
  ctx.strokeStyle = rising ? "rgba(31, 122, 115, 0.9)" : "rgba(193, 69, 44, 0.9)";
  ctx.lineWidth = 2;
  ctx.stroke();
}

async function init() {
  revealElements();
  await loadHealth();
  await loadConfig();
  await loadAuth();
  await loadBalance();
  await loadSymbols();
  await subscribeTicks(symbolSelect.value);
  await loadAnalysis();
  await loadScan();
  await loadOpenContracts();
  await loadBotStatus();
  initDerivStream();
}

init();

setInterval(loadOpenContracts, 10000);
