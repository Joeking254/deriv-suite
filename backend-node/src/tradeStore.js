const { MongoClient } = require("mongodb");

let client = null;
let db = null;
let trades = null;

async function initMongo(uri, dbName, logger) {
  if (!uri) {
    logger.warn("MONGODB_URI not set; trades will not be persisted");
    return;
  }
  client = new MongoClient(uri);
  await client.connect();
  db = client.db(dbName || "deriv_suite");
  trades = db.collection("trades");
  await trades.createIndex({ contract_id: 1 }, { unique: false });
  logger.info("MongoDB connected");
}

async function insertTrade(doc) {
  if (!trades) return null;
  return trades.insertOne(doc);
}

async function updateTrade(contractId, patch) {
  if (!trades) return null;
  return trades.updateOne({ contract_id: contractId }, { $set: patch }, { upsert: true });
}

async function listTrades(limit = 50) {
  if (!trades) return [];
  return trades.find({}).sort({ created_at: -1 }).limit(limit).toArray();
}

module.exports = { initMongo, insertTrade, updateTrade, listTrades };
