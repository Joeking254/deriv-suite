const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

function resolveStorePath() {
  const storePath = process.env.DERIV_TOKEN_STORE || "backend-node/data/token.enc";
  const base = path.resolve(__dirname, "..", "..");
  return path.isAbsolute(storePath) ? storePath : path.join(base, storePath);
}

function getKey() {
  const raw = (process.env.DERIV_TOKEN_KEY || "").trim();
  if (!raw) {
    throw new Error("DERIV_TOKEN_KEY is required");
  }
  let key = Buffer.from(raw, "base64");
  if (key.length !== 32) {
    key = Buffer.from(raw, "hex");
  }
  if (key.length !== 32) {
    throw new Error("DERIV_TOKEN_KEY must be 32 bytes (base64 or hex)");
  }
  return key;
}

function encrypt(text) {
  const key = getKey();
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
  const encrypted = Buffer.concat([cipher.update(text, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return {
    iv: iv.toString("base64"),
    tag: tag.toString("base64"),
    data: encrypted.toString("base64"),
  };
}

function decrypt(payload) {
  const key = getKey();
  const iv = Buffer.from(payload.iv, "base64");
  const tag = Buffer.from(payload.tag, "base64");
  const data = Buffer.from(payload.data, "base64");
  const decipher = crypto.createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(tag);
  const decrypted = Buffer.concat([decipher.update(data), decipher.final()]);
  return decrypted.toString("utf8");
}

function saveToken(token, accountMode) {
  const storePath = resolveStorePath();
  const dir = path.dirname(storePath);
  fs.mkdirSync(dir, { recursive: true });
  const payload = {
    token,
    account_mode: accountMode || "demo",
    updated_at: new Date().toISOString(),
  };
  const encrypted = encrypt(JSON.stringify(payload));
  fs.writeFileSync(storePath, JSON.stringify(encrypted), "utf8");
}

function loadToken() {
  const storePath = resolveStorePath();
  if (!fs.existsSync(storePath)) return null;
  const raw = fs.readFileSync(storePath, "utf8");
  if (!raw) return null;
  const encrypted = JSON.parse(raw);
  const decrypted = decrypt(encrypted);
  return JSON.parse(decrypted);
}

module.exports = { saveToken, loadToken, resolveStorePath };
