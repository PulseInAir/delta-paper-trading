import os
import time
import sqlite3
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# In Vercel serverless, the only writable directory is /tmp
SQLITE_PATH = "/tmp/trading.db"

    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")
        self.use_supabase = bool(self.supabase_url and self.supabase_key)
        self._config_cache = None
        self._last_config_fetch = 0
        self.init_sqlite()
        if self.use_supabase:
            self.sync_from_supabase()
        
    def init_sqlite(self):
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        
        # Portfolio table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY,
            cash REAL,
            blocked_margin REAL,
            total_equity REAL,
            updated_at TEXT
        )
        """)
        
        # Positions table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            product_id INTEGER,
            underlying TEXT,
            side TEXT,
            size INTEGER,
            entry_price REAL,
            mark_price REAL,
            margin REAL,
            unrealized_pnl REAL,
            delta REAL,
            gamma REAL,
            theta REAL,
            vega REAL,
            updated_at TEXT
        )
        """)
        
        # Trades table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            side TEXT,
            size INTEGER,
            price REAL,
            realized_pnl REAL,
            fee REAL
        )
        """)
        
        # Logs table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            level TEXT,
            message TEXT
        )
        """)
        
        # Equity history table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS equity_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            equity REAL
        )
        """)
        
        # Initialize portfolio row locally
        cursor.execute("SELECT COUNT(*) FROM portfolio WHERE id = 1")
        if cursor.fetchone()[0] == 0:
            initial_balance = float(os.getenv("INITIAL_PAPER_BALANCE_INR", "100000"))
            cursor.execute(
                "INSERT INTO portfolio (id, cash, blocked_margin, total_equity, updated_at) VALUES (1, ?, 0, ?, ?)",
                (initial_balance, initial_balance, datetime.utcnow().isoformat())
            )
            
        # Initialize config row locally (id = 2)
        cursor.execute("SELECT COUNT(*) FROM portfolio WHERE id = 2")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO portfolio (id, cash, blocked_margin, total_equity, updated_at) VALUES (2, 0, 1, 1, ?)",
                (datetime.utcnow().isoformat(),)
            )
            
        conn.commit()
        conn.close()

    def sync_from_supabase(self):
        """Pulls state from Supabase to initialize local ephemeral SQLite db on container boot."""
        print("[DATABASE] Syncing state from Supabase...")
        try:
            # Sync Portfolio
            port_res = self._supabase_request("GET", "portfolio", params={"id": "eq.1"})
            if port_res and len(port_res) > 0:
                p = port_res[0]
                conn = sqlite3.connect(SQLITE_PATH)
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE portfolio SET cash = ?, blocked_margin = ?, total_equity = ?, updated_at = ? WHERE id = 1",
                    (float(p["cash"]), float(p["blocked_margin"]), float(p["total_equity"]), p["updated_at"])
                )
                conn.commit()
                conn.close()
                print("[DATABASE] Portfolio synchronized from Supabase.")
                
            # Sync Config
            conf_res = self._supabase_request("GET", "portfolio", params={"id": "eq.2"})
            if conf_res and len(conf_res) > 0:
                c = conf_res[0]
                conn = sqlite3.connect(SQLITE_PATH)
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE portfolio SET cash = ?, blocked_margin = ?, total_equity = ?, updated_at = ? WHERE id = 2",
                    (float(c["cash"]), float(c["blocked_margin"]), float(c["total_equity"]), c["updated_at"])
                )
                conn.commit()
                conn.close()
                print("[DATABASE] Config synchronized from Supabase.")
                
            # Sync Positions
            pos_res = self._supabase_request("GET", "positions")
            if pos_res is not None:
                conn = sqlite3.connect(SQLITE_PATH)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM positions")  # Clear stale cache
                for pos in pos_res:
                    cursor.execute(
                        """
                        INSERT INTO positions (symbol, product_id, underlying, side, size, entry_price, mark_price, margin, unrealized_pnl, delta, gamma, theta, vega, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (pos["symbol"], pos["product_id"], pos["underlying"], pos["side"], pos["size"],
                         pos["entry_price"], pos["mark_price"], pos["margin"], pos["unrealized_pnl"],
                         pos["delta"], pos["gamma"], pos["theta"], pos["vega"], pos["updated_at"])
                    )
                conn.commit()
                conn.close()
                print(f"[DATABASE] Synchronized {len(pos_res)} positions from Supabase.")
                
            # Sync Trades
            trades_res = self._supabase_request("GET", "trades", params={"order": "id.desc", "limit": "100"})
            if trades_res is not None:
                conn = sqlite3.connect(SQLITE_PATH)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM trades")
                for t in reversed(trades_res):  # Re-insert in order
                    cursor.execute(
                        "INSERT INTO trades (id, timestamp, symbol, side, size, price, realized_pnl, fee) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (t["id"], t["timestamp"], t["symbol"], t["side"], t["size"], t["price"], t["realized_pnl"], t["fee"])
                    )
                conn.commit()
                conn.close()
                
            # Sync Equity History
            eq_res = self._supabase_request("GET", "equity_history", params={"order": "id.desc", "limit": "200"})
            if eq_res is not None:
                conn = sqlite3.connect(SQLITE_PATH)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM equity_history")
                for e in reversed(eq_res):
                    cursor.execute(
                        "INSERT INTO equity_history (id, timestamp, equity) VALUES (?, ?, ?)",
                        (e["id"], e["timestamp"], e["equity"])
                    )
                conn.commit()
                conn.close()
                
            # Sync Logs
            logs_res = self._supabase_request("GET", "logs", params={"order": "id.desc", "limit": "100"})
            if logs_res is not None:
                conn = sqlite3.connect(SQLITE_PATH)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM logs")
                for l in reversed(logs_res):
                    cursor.execute(
                        "INSERT INTO logs (id, timestamp, level, message) VALUES (?, ?, ?, ?)",
                        (l["id"], l["timestamp"], l["level"], l["message"])
                    )
                conn.commit()
                conn.close()
                
            print("[DATABASE] Full database sync with Supabase completed successfully.")
        except Exception as e:
            print(f"[DATABASE] Sync from Supabase failed (using local fallback): {str(e)}")

    def _supabase_request(self, method, table, data=None, params=None):
        if not self.use_supabase:
            return None
        url = f"{self.supabase_url}/rest/v1/{table}"
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
        try:
            if method.upper() == "POST":
                headers["Prefer"] = "resolution=merge-duplicates"
                response = requests.post(url, headers=headers, json=data, params=params, timeout=5)
            elif method.upper() == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=5)
            elif method.upper() == "DELETE":
                response = requests.delete(url, headers=headers, params=params, timeout=5)
            elif method.upper() == "PATCH":
                response = requests.patch(url, headers=headers, json=data, params=params, timeout=5)
            return response.json() if response.status_code in [200, 201] else None
        except Exception:
            return None

    # Portfolio state operations
    def save_portfolio_state(self, cash, blocked_margin, total_equity):
        updated_at = datetime.utcnow().isoformat()
        
        # Save to SQLite
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE portfolio SET cash = ?, blocked_margin = ?, total_equity = ?, updated_at = ? WHERE id = 1",
            (cash, blocked_margin, total_equity, updated_at)
        )
        conn.commit()
        conn.close()
        
        # Sync to Supabase
        if self.use_supabase:
            payload = {
                "id": 1,
                "cash": cash,
                "blocked_margin": blocked_margin,
                "total_equity": total_equity,
                "updated_at": updated_at
            }
            self._supabase_request("POST", "portfolio", payload)

    def load_portfolio_state(self):
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT cash, blocked_margin, total_equity FROM portfolio WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {"cash": row[0], "blocked_margin": row[1], "total_equity": row[2]}
        return {"cash": 100000.0, "blocked_margin": 0.0, "total_equity": 100000.0}

    def save_config_state(self, mode, harvester_enabled, breakout_enabled):
        updated_at = datetime.utcnow().isoformat()
        cash = 1.0 if mode == "live" else 0.0
        blocked_margin = 1.0 if harvester_enabled else 0.0
        total_equity = 1.0 if breakout_enabled else 0.0
        
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE portfolio SET cash = ?, blocked_margin = ?, total_equity = ?, updated_at = ? WHERE id = 2",
            (cash, blocked_margin, total_equity, updated_at)
        )
        conn.commit()
        conn.close()
        
        if self.use_supabase:
            payload = {
                "id": 2,
                "cash": cash,
                "blocked_margin": blocked_margin,
                "total_equity": total_equity,
                "updated_at": updated_at
            }
            self._supabase_request("POST", "portfolio", payload)

    def load_config_state(self):
        now = time.time()
        if self._config_cache and now - self._last_config_fetch < 10:
            return self._config_cache
            
        if self.use_supabase:
            try:
                conf_res = self._supabase_request("GET", "portfolio", params={"id": "eq.2"})
                if conf_res and len(conf_res) > 0:
                    c = conf_res[0]
                    conn = sqlite3.connect(SQLITE_PATH)
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE portfolio SET cash = ?, blocked_margin = ?, total_equity = ?, updated_at = ? WHERE id = 2",
                        (float(c["cash"]), float(c["blocked_margin"]), float(c["total_equity"]), c["updated_at"])
                    )
                    conn.commit()
                    conn.close()
                    self._config_cache = {
                        "mode": "live" if float(c["cash"]) > 0.5 else "paper",
                        "volatility_harvester": bool(float(c["blocked_margin"]) > 0.5),
                        "momentum_breakout": bool(float(c["total_equity"]) > 0.5)
                    }
                    self._last_config_fetch = now
                    return self._config_cache
            except Exception:
                pass
                
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT cash, blocked_margin, total_equity FROM portfolio WHERE id = 2")
        row = cursor.fetchone()
        conn.close()
        
        if row:
            self._config_cache = {
                "mode": "live" if row[0] > 0.5 else "paper",
                "volatility_harvester": bool(row[1] > 0.5),
                "momentum_breakout": bool(row[2] > 0.5)
            }
        else:
            self._config_cache = {"mode": "paper", "volatility_harvester": True, "momentum_breakout": True}
            
        self._last_config_fetch = now
        return self._config_cache

    # Positions operations
    def save_position(self, symbol, product_id, underlying, side, size, entry_price, mark_price, margin, unrealized_pnl, delta, gamma, theta, vega):
        updated_at = datetime.utcnow().isoformat()
        
        # Save to SQLite
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO positions (symbol, product_id, underlying, side, size, entry_price, mark_price, margin, unrealized_pnl, delta, gamma, theta, vega, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                size = excluded.size,
                entry_price = excluded.entry_price,
                mark_price = excluded.mark_price,
                margin = excluded.margin,
                unrealized_pnl = excluded.unrealized_pnl,
                delta = excluded.delta,
                gamma = excluded.gamma,
                theta = excluded.theta,
                vega = excluded.vega,
                updated_at = excluded.updated_at
            """,
            (symbol, product_id, underlying, side, size, entry_price, mark_price, margin, unrealized_pnl, delta, gamma, theta, vega, updated_at)
        )
        conn.commit()
        conn.close()
        
        # Sync to Supabase
        if self.use_supabase:
            payload = {
                "symbol": symbol,
                "product_id": product_id,
                "underlying": underlying,
                "side": side,
                "size": size,
                "entry_price": entry_price,
                "mark_price": mark_price,
                "margin": margin,
                "unrealized_pnl": unrealized_pnl,
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "vega": vega,
                "updated_at": updated_at
            }
            self._supabase_request("POST", "positions", payload)

    def delete_position(self, symbol):
        # Delete from SQLite
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        conn.commit()
        conn.close()
        
        # Delete from Supabase
        if self.use_supabase:
            self._supabase_request("DELETE", "positions", params={"symbol": f"eq.{symbol}"})

    def load_positions(self):
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM positions WHERE size > 0")
        rows = cursor.fetchall()
        conn.close()
        
        positions = []
        for r in rows:
            positions.append(dict(r))
        return positions

    # Trades operations
    def add_trade(self, symbol, side, size, price, realized_pnl, fee):
        timestamp = datetime.utcnow().isoformat()
        
        # Save to SQLite
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trades (timestamp, symbol, side, size, price, realized_pnl, fee) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (timestamp, symbol, side, size, price, realized_pnl, fee)
        )
        conn.commit()
        conn.close()
        
        # Sync to Supabase
        if self.use_supabase:
            payload = {
                "timestamp": timestamp,
                "symbol": symbol,
                "side": side,
                "size": size,
                "price": price,
                "realized_pnl": realized_pnl,
                "fee": fee
            }
            self._supabase_request("POST", "trades", payload)

    def load_trades(self, limit=100):
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        trades = []
        for r in rows:
            trades.append(dict(r))
        return trades

    # Logs operations
    def add_log(self, level, message):
        timestamp = datetime.utcnow().isoformat()
        print(f"[{level}] {message}")
        
        # Save to SQLite
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO logs (timestamp, level, message) VALUES (?, ?, ?)",
            (timestamp, level, message)
        )
        conn.commit()
        cursor.execute("DELETE FROM logs WHERE id IN (SELECT id FROM logs ORDER BY id DESC LIMIT -1 OFFSET 1000)")
        conn.commit()
        conn.close()
        
        # Sync to Supabase
        if self.use_supabase:
            payload = {
                "timestamp": timestamp,
                "level": level,
                "message": message
            }
            self._supabase_request("POST", "logs", payload)

    def load_logs(self, limit=100):
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        logs = []
        for r in rows:
            logs.append(dict(r))
        return logs

    # Equity history operations
    def add_equity_snapshot(self, equity):
        timestamp = datetime.utcnow().isoformat()
        
        # Save to SQLite
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO equity_history (timestamp, equity) VALUES (?, ?)",
            (timestamp, equity)
        )
        conn.commit()
        conn.close()
        
        # Sync to Supabase
        if self.use_supabase:
            payload = {
                "timestamp": timestamp,
                "equity": equity
            }
            self._supabase_request("POST", "equity_history", payload)

    def load_equity_history(self, limit=500):
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM equity_history ORDER BY id ASC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        history = []
        for r in rows:
            history.append(dict(r))
        return history
