const levels = ["debug", "info", "warn", "error"];

function makeLogger(level = "info") {
  const minIdx = Math.max(0, levels.indexOf(level));
  function log(lvl, message, meta = {}) {
    if (levels.indexOf(lvl) < minIdx) return;
    const payload = {
      ts: new Date().toISOString(),
      level: lvl,
      msg: message,
      ...meta,
    };
    const out = JSON.stringify(payload);
    if (lvl === "error") {
      console.error(out);
    } else if (lvl === "warn") {
      console.warn(out);
    } else {
      console.log(out);
    }
  }
  return {
    debug: (msg, meta) => log("debug", msg, meta),
    info: (msg, meta) => log("info", msg, meta),
    warn: (msg, meta) => log("warn", msg, meta),
    error: (msg, meta) => log("error", msg, meta),
  };
}

module.exports = { makeLogger };
