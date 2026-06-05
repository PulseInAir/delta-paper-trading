import time
from datetime import datetime
from api.db import DatabaseManager
from api.delta_client import DeltaClient
from api.portfolio import PortfolioManager
from api.strategies import VolatilityHarvester, MomentumBreakout

class TradingEngine:
    def __init__(self, db: DatabaseManager, client: DeltaClient, portfolio: PortfolioManager):
        self.db = db
        self.client = client
        self.portfolio = portfolio
        
        self.strategies = {
            "volatility_harvester": VolatilityHarvester(db, portfolio),
            "momentum_breakout": MomentumBreakout(db, portfolio)
        }
        
        self.underlying_history = {
            "BTC": [],
            "ETH": []
        }
        
        # Product cache is stored in the warm container memory
        self.products_cache = {}
        self.last_products_fetch = 0
        self.last_tick_time = 0
        self.last_snapshot_time = 0
        
    def tick(self):
        """
        Serverless Tick Runner.
        Throttled to prevent multiple executions per second.
        Executes a single market scan, valuation update, and strategy check.
        """
        now = time.time()
        # Throttle: Only run at most once every 1.5 seconds
        if now - self.last_tick_time < 1.5:
            return False
            
        self.last_tick_time = now
        
        try:
            # 1. Fetch products list if cache is stale (>15 minutes) or empty
            self._fetch_products_if_needed()
            
            # 2. Fetch all tickers from Delta Exchange India
            tickers_res = self.client.get_tickers()
            if not tickers_res.get("success"):
                err_msg = tickers_res.get("error", {}).get("message", "Rate limit or Connection Error")
                self.db.add_log("WARNING", f"Serverless Tick: Ticker fetch failed ({err_msg})")
                return False
                
            tickers = tickers_res.get("result", [])
            tickers_dict = {t.get("symbol"): t for t in tickers if t.get("symbol")}
            
            # 3. Update Spot histories
            btc_ticker = tickers_dict.get(".DEXBTUSD") or tickers_dict.get("BTCUSD")
            eth_ticker = tickers_dict.get(".DEXETHUSD") or tickers_dict.get("ETHUSD")
            
            if btc_ticker and btc_ticker.get("mark_price"):
                self.underlying_history["BTC"].append(float(btc_ticker["mark_price"]))
                if len(self.underlying_history["BTC"]) > 100:
                    self.underlying_history["BTC"].pop(0)
                    
            if eth_ticker and eth_ticker.get("mark_price"):
                self.underlying_history["ETH"].append(float(eth_ticker["mark_price"]))
                if len(self.underlying_history["ETH"]) > 100:
                    self.underlying_history["ETH"].pop(0)
                    
            # 4. Update Portfolio valuations & Option Greeks
            self.portfolio.update_valuation(tickers_dict, self.products_cache)
            
            # 5. Evaluate Strategies
            for name, strat in self.strategies.items():
                if strat.enabled:
                    try:
                        strat.evaluate(tickers_dict, self.products_cache, self.underlying_history)
                    except Exception as e:
                        self.db.add_log("ERROR", f"Strategy evaluation fail ({strat.name}): {str(e)}")
                        
            # 6. Save Equity Snapshots periodically (every 5 minutes)
            if now - self.last_snapshot_time > 300:
                self.db.add_equity_snapshot(self.portfolio.total_equity_inr)
                self.last_snapshot_time = now
                
            return True
        except Exception as e:
            self.db.add_log("ERROR", f"Error in Serverless Engine Tick: {str(e)}")
            return False

    def _fetch_products_if_needed(self):
        now = time.time()
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
                options_count = sum(1 for p in products if p.get("contract_type") in ["call_options", "put_options"])
                self.db.add_log("INFO", f"Synced products list: {len(products)} products (Options: {options_count})")
            else:
                self.db.add_log("WARNING", "Serverless: Failed to sync products, using cache.")

    def get_status(self):
        btc_price = self.underlying_history["BTC"][-1] if self.underlying_history["BTC"] else 0.0
        eth_price = self.underlying_history["ETH"][-1] if self.underlying_history["ETH"] else 0.0
        
        return {
            "running": True, # Always online in Vercel API
            "mode": self.portfolio.mode,
            "btc_price": btc_price,
            "eth_price": eth_price,
            "strategies": {
                "volatility_harvester": self.strategies["volatility_harvester"].enabled,
                "momentum_breakout": self.strategies["momentum_breakout"].enabled
            }
        }
