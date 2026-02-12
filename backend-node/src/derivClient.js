const WebSocket = require("ws");

class DerivClient {
  constructor(appId, logger, onStatus) {
    this.appId = appId;
    this.logger = logger;
    this.onStatus = onStatus;
    this.ws = null;
    this.reqId = 1;
    this.pending = new Map();
    this.pendingSubs = new Map();
    this.subscriptions = new Map();
    this.subscriptionsByReq = new Map();
    this.token = null;
    this.connecting = false;
    this.reconnectAttempts = 0;
    this.shouldReconnect = true;
  }

  get url() {
    return `wss://ws.derivws.com/websockets/v3?app_id=${this.appId}`;
  }

  isOpen() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  setToken(token) {
    this.token = token;
  }

  async connect() {
    if (this.isOpen() || this.connecting) return;
    this.connecting = true;
    await new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url);
      const onOpen = () => {
        this.logger.info("Deriv WS connected");
        this._setStatus("connected");
        this.reconnectAttempts = 0;
        this.connecting = false;
        resolve();
      };
      const onError = (err) => {
        this.logger.error("Deriv WS error", { error: err.message });
        this.connecting = false;
        reject(err);
      };
      this.ws.on("open", onOpen);
      this.ws.on("message", (data) => this._onMessage(data));
      this.ws.on("close", () => this._onClose());
      this.ws.on("error", onError);
    });
  }

  async ensureConnected() {
    if (!this.isOpen()) {
      await this.connect();
    }
  }

  async authorize(token) {
    if (!token) {
      throw new Error("Missing Deriv token");
    }
    this.token = token;
    return this.request({ authorize: token });
  }

  request(payload, timeoutMs = 15000) {
    if (!this.isOpen()) {
      return Promise.reject(new Error("WebSocket not connected"));
    }
    const reqId = this.reqId++;
    const body = { ...payload, req_id: reqId };
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(reqId);
        reject(new Error("Deriv request timeout"));
      }, timeoutMs);
      this.pending.set(reqId, { resolve, reject, timeout });
      this.ws.send(JSON.stringify(body));
    });
  }

  subscribe(label, payload, onMessage, timeoutMs = 15000) {
    if (!this.isOpen()) {
      return Promise.reject(new Error("WebSocket not connected"));
    }
    const reqId = this.reqId++;
    const body = { ...payload, subscribe: 1, req_id: reqId };
    this.subscriptions.set(label, { reqId, id: null, payload, onMessage });
    this.subscriptionsByReq.set(reqId, label);
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pendingSubs.delete(reqId);
        reject(new Error("Deriv subscribe timeout"));
      }, timeoutMs);
      this.pendingSubs.set(reqId, { resolve, reject, timeout });
      this.ws.send(JSON.stringify(body));
    });
  }

  async forget(label) {
    const sub = this.subscriptions.get(label);
    if (!sub || !sub.id) return;
    try {
      await this.request({ forget: sub.id });
    } catch (err) {
      this.logger.warn("Failed to forget subscription", { label, error: err.message });
    }
    this.subscriptions.delete(label);
    if (sub.reqId) {
      this.subscriptionsByReq.delete(sub.reqId);
    }
  }

  async resubscribeAll() {
    for (const [label, sub] of this.subscriptions.entries()) {
      const payload = sub.payload;
      try {
        await this.subscribe(label, payload, sub.onMessage);
      } catch (err) {
        this.logger.warn("Resubscribe failed", { label, error: err.message });
      }
    }
  }

  async close() {
    this.shouldReconnect = false;
    if (this.ws) {
      this.ws.close();
    }
  }

  _onMessage(data) {
    let msg;
    try {
      msg = JSON.parse(data.toString());
    } catch (err) {
      this.logger.warn("Bad WS message", { error: err.message });
      return;
    }
    const reqId = msg.req_id;
    if (msg.error) {
      const err = new Error(msg.error.message || "Deriv error");
      err.code = msg.error.code;
      if (reqId && this.pending.has(reqId)) {
        const pending = this.pending.get(reqId);
        clearTimeout(pending.timeout);
        this.pending.delete(reqId);
        pending.reject(err);
        return;
      }
      if (reqId && this.pendingSubs.has(reqId)) {
        const pending = this.pendingSubs.get(reqId);
        clearTimeout(pending.timeout);
        this.pendingSubs.delete(reqId);
        pending.reject(err);
        return;
      }
      this.logger.warn("Deriv error", { code: err.code, message: err.message });
      return;
    }

    if (reqId && this.pending.has(reqId)) {
      const pending = this.pending.get(reqId);
      clearTimeout(pending.timeout);
      this.pending.delete(reqId);
      pending.resolve(msg);
    }

    if (reqId && this.pendingSubs.has(reqId)) {
      const pending = this.pendingSubs.get(reqId);
      clearTimeout(pending.timeout);
      this.pendingSubs.delete(reqId);
      pending.resolve(msg);
    }

    if (reqId && this.subscriptionsByReq.has(reqId)) {
      const label = this.subscriptionsByReq.get(reqId);
      const sub = this.subscriptions.get(label);
      if (sub) {
        if (msg.subscription && msg.subscription.id) {
          sub.id = msg.subscription.id;
        }
        if (sub.onMessage) {
          sub.onMessage(msg);
        }
      }
    }
  }

  _onClose() {
    this.logger.warn("Deriv WS closed");
    this._setStatus("disconnected");
    for (const [reqId, pending] of this.pending.entries()) {
      clearTimeout(pending.timeout);
      pending.reject(new Error("WebSocket closed"));
    }
    this.pending.clear();
    for (const [reqId, pending] of this.pendingSubs.entries()) {
      clearTimeout(pending.timeout);
      pending.reject(new Error("WebSocket closed"));
    }
    this.pendingSubs.clear();
    if (this.shouldReconnect) {
      this._scheduleReconnect();
    }
  }

  _setStatus(status) {
    if (this.onStatus) {
      this.onStatus(status);
    }
  }

  _scheduleReconnect() {
    this.reconnectAttempts += 1;
    const delay = Math.min(30000, 1000 * Math.pow(2, this.reconnectAttempts));
    this.logger.info("Reconnecting to Deriv WS", { delay_ms: delay });
    setTimeout(async () => {
      try {
        await this.connect();
        if (this.token) {
          await this.authorize(this.token);
        }
        await this.resubscribeAll();
      } catch (err) {
        this.logger.warn("Reconnect failed", { error: err.message });
        this._scheduleReconnect();
      }
    }, delay);
  }
}

module.exports = { DerivClient };
