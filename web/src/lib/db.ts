import Database from "better-sqlite3";
import path from "path";

const DB_PATH = path.resolve(process.cwd(), "..", "data", "trading_bot.db");

let _db: Database.Database | null = null;
let _dbWrite: Database.Database | null = null;

export function getDb(): Database.Database {
  if (!_db) {
    _db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
    _db.pragma("journal_mode = WAL");
    _db.pragma("query_only = ON");
  }
  return _db;
}

export function getWriteDb(): Database.Database {
  if (!_dbWrite) {
    _dbWrite = new Database(DB_PATH, { fileMustExist: true, timeout: 30000 });
    _dbWrite.pragma("journal_mode = WAL");
    _dbWrite.pragma("busy_timeout = 30000");
  }
  return _dbWrite;
}
