import time
import threading
from datetime import datetime, timedelta
from backend.db import DatabaseManager
from backend.delta_client import DeltaClient
from backend.portfolio import PortfolioManager
from backend.strategies import VolatilityHarvester, MomentumBreakout

class TradingEngine:
    def __init__(self, db: DatabaseManager, client: DeltaClient, portfolio: PortfolioManager):
        self.db = db
        self.client = client
        self.portfolio = portfolio
        
        # Load strategies
        self.strategies = {
            "volatility_harvester": VolatilityHarvester(db, portfolio),
            "momentum_breakout": MomentumBreakout(db, portfolio)
        }
        
        # Underlying spot prices history (last 100 ticks/periods)
        self.underlying_history = {
            "BTC": [],
            "ETH": []
        }
        
        # Product cache to avoid querying all products on every tick
        self.products_cache = {}
        self.last_products_fetch = 0
        
        self.running = False
        self.thread = None
        
        # Snapshot frequency: 1 snapshot every 5 minutes (for demo/chart resolution)
        self.last_snapshot_time = 0
        
    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            self.db.add_log("SYSTEM", "Trading Engine background loop started.")
            
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
            self.db.add_log("SYSTEM", "Trading Engine background loop stopped.")

    def _fetch_products_if_needed(self):
        now = time.time()
        # Fetch every 15 minutes (900s) or if cache is empty
        if now - self.last_products_fetch > 900 or not self.products_cache:
            res = self.client.get_products()
            if res.get("success"):
                products = res.get("result", [])
                temp_cache = {}
                for p in products:
                    symbol = p.get("symbol")
                    if symbol:
                        temp_cache[symbol] = p
                self.products_cache = temp_cache
                self.last_products_fetch = now
                # Log stats
                options_count = sum(1 for p in products if p.get("contract_type") in ["call_options", "put_options"])
                self.db.add_log("INFO", f"Fetched {len(products)} products from Delta Exchange (Options: {options_count})")
            else:
                self.db.add_log("WARNING", "Failed to fetch products list from Delta Exchange. Using cached products.")

    def _run_loop(self):
        self.db.add_log("SYSTEM", "Executing initial products load...")
        self._fetch_products_if_needed()
        
        # Ensure we take an initial equity snapshot if history is empty
        history = self.db.load_equity_history(limit=5)
        if not history:
            self.db.add_equity_snapshot(self.portfolio.total_equity_inr)
            self.last_snapshot_time = time.time()
            
        while self.running:
            start_tick = time.time()
            
            try:
                # 1. Fetch products list if stale
                self._fetch_products_if_needed()
                
                # 2. Fetch all tickers from Delta
                tickers_res = self.client.get_tickers()
                if not tickers_res.get("success"):
                    err_msg = tickers_res.get("error", {}).get("message", "Rate limit or Connection Error")
                    self.db.add_log("WARNING", f"Failed to fetch market tickers: {err_msg}")
                    time.sleep(2)
                    continue
                    
                tickers = tickers_res.get("result", [])
                tickers_dict = {t.get("symbol"): t for t in tickers if t.get("symbol")}
                
                # 3. Update Underlying spot histories
                # Delta spot indices are typically '.DEXBTUSD' and '.DEXETHUSD'
                btc_ticker = tickers_dict.get(".DEXBTUSD")
                eth_ticker = tickers_dict.get(".DEXETHUSD")
                
                # If spot pair is returned, fallback
                if not btc_ticker:
                    btc_ticker = tickers_dict.get("BTCUSD")
                if not eth_ticker:
                    eth_ticker = tickers_dict.get("ETHUSD")
                    
                if btc_ticker and btc_ticker.get("mark_price"):
                    self.underlying_history["BTC"].append(float(btc_ticker["mark_price"]))
                    if len(self.underlying_history["BTC"]) > 100:
                        self.underlying_history["BTC"].pop(0)
                        
                if eth_ticker and eth_ticker.get("mark_price"):
                    self.underlying_history["ETH"].append(float(eth_ticker["mark_price"]))
                    if len(self.underlying_history["ETH"]) > 100:
                        self.underlying_history["ETH"].pop(0)
                        
                # 4. Feed prices to portfolio to calculate mark-to-market valuations and Greeks
                self.portfolio.update_valuation(tickers_dict, self.products_cache)
                
                # 5. Evaluate Strategies
                for name, strat in self.strategies.items():
                    if strat.enabled:
                        try:
                            strat.evaluate(tickers_dict, self.products_cache, self.underlying_history)
                        except Exception as e:
                            self.db.add_log("ERROR", f"Error evaluating strategy {strat.name}: {str(e)}")
                            
                # 6. Save Equity Snapshots periodically (every 5 minutes in loop, or hourly in production)
                # For demonstration and live charting, we do every 5 minutes
                now = time.time()
                if now - self.last_snapshot_time > 300: # 5 minutes
                    self.db.add_equity_snapshot(self.portfolio.total_equity_inr)
                    self.last_snapshot_time = now
                    
            except Exception as e:
                self.db.add_log("ERROR", f"Error in trading engine loop: {str(e)}")
                
            # Throttle loop to run exactly every 1 second
            elapsed = time.time() - start_tick
            sleep_time = max(0.1, 1.0 - elapsed)
            time.sleep(sleep_time)
            
    def get_status(self):
        btc_price = self.underlying_history["BTC"][-1] if self.underlying_history["BTC"] else 0.0
        eth_price = self.underlying_history["ETH"][-1] if self.underlying_history["ETH"] else 0.0
        
        return {
            "running": self.running,
            "mode": self.portfolio.mode,
            "btc_price": btc_price,
            "eth_price": eth_price,
            "strategies": {
                "volatility_harvester": self.strategies["volatility_harvester"].enabled,
                "momentum_breakout": self.strategies["momentum_breakout"].enabled
            }
        }
