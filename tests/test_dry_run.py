import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import DatabaseManager
from backend.delta_client import DeltaClient
from backend.portfolio import PortfolioManager
from backend.trading_engine import TradingEngine

def dry_run():
    print("Initializing Database Manager...")
    db = DatabaseManager()
    
    print("Initializing Delta Exchange Client...")
    client = DeltaClient()
    
    print("Initializing Portfolio Manager...")
    portfolio = PortfolioManager(db, client)
    
    print("Initializing Trading Engine...")
    engine = TradingEngine(db, client, portfolio)
    
    print("Testing public products and ticker retrieval...")
    # Fetch products (public endpoint)
    engine._fetch_products_if_needed()
    assert len(engine.products_cache) > 0, "Products cache is empty!"
    print(f"Products cache loaded successfully: {len(engine.products_cache)} items.")
    
    # Fetch tickers (public endpoint)
    tickers_res = client.get_tickers()
    assert tickers_res.get("success"), "Failed to fetch tickers!"
    tickers = tickers_res.get("result", [])
    print(f"Tickers retrieved successfully: {len(tickers)} items.")
    
    # Process a single tick manually
    print("Executing single loop cycle manually...")
    tickers_dict = {t.get("symbol"): t for t in tickers if t.get("symbol")}
    
    # Update spot price histories
    btc_ticker = tickers_dict.get(".DEXBTUSD") or tickers_dict.get("BTCUSD")
    if btc_ticker:
        engine.underlying_history["BTC"].append(float(btc_ticker["mark_price"]))
        print(f"BTC Spot Price: ${btc_ticker['mark_price']}")
        
    eth_ticker = tickers_dict.get(".DEXETHUSD") or tickers_dict.get("ETHUSD")
    if eth_ticker:
        engine.underlying_history["ETH"].append(float(eth_ticker["mark_price"]))
        print(f"ETH Spot Price: ${eth_ticker['mark_price']}")
        
    portfolio.update_valuation(tickers_dict, engine.products_cache)
    print("Portfolio valuation updated successfully.")
    
    for name, strat in engine.strategies.items():
        if strat.enabled:
            print(f"Evaluating strategy: {strat.name}...")
            strat.evaluate(tickers_dict, engine.products_cache, engine.underlying_history)
            
    print("Status:", engine.get_status())
    print("Dry run completed successfully! All systems integrated.")

if __name__ == "__main__":
    try:
        dry_run()
    except Exception as e:
        print("Dry run FAILED:", e)
        sys.exit(1)
