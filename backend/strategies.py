import math
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Helper math function for RSI
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0  # Neutral default
    
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-change)
            
    # Initial average gain/loss
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Wilder's smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class BaseStrategy:
    def __init__(self, name, db, portfolio):
        self.name = name
        self.db = db
        self.portfolio = portfolio
        self.enabled = True
        
    def toggle(self, state: bool):
        self.enabled = state
        self.db.add_log("SYSTEM", f"Strategy {self.name} is now {'ENABLED' if state else 'DISABLED'}")
        
    def evaluate(self, tickers_dict, products_dict, underlying_history):
        """Must be overridden by subclasses. Runs on every tick."""
        pass


class VolatilityHarvester(BaseStrategy):
    """
    Volatility Harvesting Strategy (Strangle Seller)
    Sells out-of-the-money Call & Put options when Implied Volatility (IV) is high.
    Theta decay generates consistent profit, while risk is tightly controlled.
    """
    def __init__(self, db, portfolio):
        super().__init__("Volatility Harvester", db, portfolio)
        self.min_iv = 0.40  # Minimum IV of 40% to sell
        self.target_delta_low = 0.10
        self.target_delta_high = 0.20
        self.max_contracts_per_trade = 10
        self.last_trade_time = {}
        
    def evaluate(self, tickers_dict, products_dict, underlying_history):
        if not self.enabled:
            return
            
        # 1. Manage Active Positions (Risk management & Stop Loss)
        positions = self.db.load_positions()
        for pos in positions:
            # We only manage short positions sold by this strategy
            if pos["side"] != "sell":
                continue
                
            symbol = pos["symbol"]
            ticker = tickers_dict.get(symbol)
            prod = products_dict.get(symbol)
            
            if not ticker or not prod:
                continue
                
            # If delta has risen significantly (e.g. > 0.35), the option is getting close to money
            # Buy to cover to prevent large losses
            pos_delta = abs(pos["delta"] / (pos["size"] * float(prod.get("contract_value", 0.001))))
            if pos_delta > 0.35:
                self.db.add_log("WARNING", f"Volatility Harvester: Delta risk exceeded on {symbol} (Delta: {pos_delta:.2f} > 0.35). Closing position to protect capital.")
                self.portfolio.close_position(symbol)
                continue
                
            # Time Decay Profit Taking
            # If the option price has decayed to 15% of entry price, buy to cover to capture profit
            mark_price = float(ticker.get("mark_price", pos["mark_price"]))
            entry_price = float(pos["entry_price"])
            if mark_price < entry_price * 0.15:
                self.db.add_log("INFO", f"Volatility Harvester: Target profit achieved on {symbol} (Price decayed from ${entry_price:.2f} to ${mark_price:.2f}). Closing to take profit.")
                self.portfolio.close_position(symbol)
                
        # 2. Look for New Entry Opportunities (Strangles)
        # We trade BTC and ETH underlyings
        for asset in ["BTC", "ETH"]:
            # Check if we already have short positions for this underlying
            asset_shorts = [p for p in positions if p["underlying"] == asset and p["side"] == "sell"]
            if len(asset_shorts) >= 2:
                # Already have both wings (or multiple wings), skip adding more
                continue
                
            # Find candidate options expiring in 1 to 5 days
            candidates = []
            for symbol, prod in products_dict.items():
                if prod.get("contract_type") not in ["call_options", "put_options"]:
                    continue
                if prod.get("underlying_asset", {}).get("symbol") != asset:
                    continue
                if prod.get("state") != "live" or prod.get("trading_status") != "operational":
                    continue
                    
                settlement_time_str = prod.get("settlement_time")
                if not settlement_time_str:
                    continue
                    
                try:
                    expiry = datetime.strptime(settlement_time_str, "%Y-%m-%dT%H:%M:%SZ")
                    days_left = (expiry - datetime.utcnow()).total_seconds() / (24.0 * 3600.0)
                    if days_left < 1.0 or days_left > 5.0:
                        continue # Expiring too soon or too far
                except Exception:
                    continue
                    
                ticker = tickers_dict.get(symbol)
                if not ticker:
                    continue
                    
                iv = float(ticker.get("implied_volatility") or prod.get("product_specs", {}).get("min_volatility", 0.0))
                if iv < self.min_iv:
                    continue # Volatility too low to harvest
                    
                spot_price = float(ticker.get("spot_price", 0.0))
                strike_price = float(prod.get("strike_price", 0.0))
                
                # Approximate delta
                option_type = 'call' if prod.get("contract_type") == 'call_options' else 'put'
                T = days_left / 365.0
                d1 = (math.log(spot_price / strike_price) + (0.05 + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
                delta = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
                if option_type == 'put':
                    delta = delta - 1.0
                    
                # Check if delta is in target range (0.10 to 0.20 for call, -0.20 to -0.10 for put)
                abs_delta = abs(delta)
                if abs_delta >= self.target_delta_low and abs_delta <= self.target_delta_high:
                    candidates.append({
                        "symbol": symbol,
                        "product_id": prod["id"],
                        "contract_type": prod["contract_type"],
                        "strike_price": strike_price,
                        "iv": iv,
                        "abs_delta": abs_delta,
                        "price": float(ticker.get("mark_price", 0.0)),
                        "contract_value": float(prod.get("contract_value", 0.001))
                    })
                    
            # Try to place orders for call and put wings
            calls = [c for c in candidates if c["contract_type"] == "call_options"]
            puts = [c for c in candidates if c["contract_type"] == "put_options"]
            
            # Sort by delta closest to 0.15
            calls.sort(key=lambda x: abs(x["abs_delta"] - 0.15))
            puts.sort(key=lambda x: abs(x["abs_delta"] - 0.15))
            
            # Sell Call wing if not already holding
            has_call = any(p for p in asset_shorts if products_dict.get(p["symbol"], {}).get("contract_type") == "call_options")
            if not has_call and calls:
                target = calls[0]
                # Margin calculation check: make sure we have enough capital
                self.portfolio.place_order(
                    target["symbol"], target["product_id"], asset, "sell", 
                    self.max_contracts_per_trade, target["price"], target["contract_value"]
                )
                self.db.add_log("INFO", f"Volatility Harvester: Found high IV Call option {target['symbol']} (IV: {target['iv']*100:.1f}%, Delta: {target['abs_delta']:.2f}). Selling {self.max_contracts_per_trade} contracts.")
                
            # Sell Put wing if not already holding
            has_put = any(p for p in asset_shorts if products_dict.get(p["symbol"], {}).get("contract_type") == "put_options")
            if not has_put and puts:
                target = puts[0]
                self.portfolio.place_order(
                    target["symbol"], target["product_id"], asset, "sell", 
                    self.max_contracts_per_trade, target["price"], target["contract_value"]
                )
                self.db.add_log("INFO", f"Volatility Harvester: Found high IV Put option {target['symbol']} (IV: {target['iv']*100:.1f}%, Delta: {target['abs_delta']:.2f}). Selling {self.max_contracts_per_trade} contracts.")


class MomentumBreakout(BaseStrategy):
    """
    Momentum Breakout Strategy (Long Option Buyer)
    Buys Near-the-Money options when the underlying asset experiences a rapid price breakout
    and RSI indicates momentum strength. Sells to close when profit target (2x) or stop loss (-50%) is hit.
    """
    def __init__(self, db, portfolio):
        super().__init__("Momentum Breakout", db, portfolio)
        self.rsi_period = 14
        self.buy_size_contracts = 20
        self.last_breakout_time = {}
        
    def evaluate(self, tickers_dict, products_dict, underlying_history):
        if not self.enabled:
            return
            
        # 1. Manage Active Positions (Long options sold or bought by this strategy)
        positions = self.db.load_positions()
        for pos in positions:
            if pos["side"] != "buy":
                continue # only manage long option buys
                
            symbol = pos["symbol"]
            ticker = tickers_dict.get(symbol)
            if not ticker:
                continue
                
            mark_price = float(ticker.get("mark_price", pos["mark_price"]))
            entry_price = float(pos["entry_price"])
            
            # Profit Target: +100% (Double the premium)
            if mark_price >= entry_price * 2.0:
                self.db.add_log("TRADE", f"Momentum Breakout: Profit target (+100%) hit on {symbol}. Closing position at ${mark_price:.2f}.")
                self.portfolio.close_position(symbol)
                continue
                
            # Stop Loss: -50% (Loss of half the premium)
            if mark_price <= entry_price * 0.50:
                self.db.add_log("TRADE", f"Momentum Breakout: Stop loss (-50%) hit on {symbol}. Closing position at ${mark_price:.2f} to salvage premium.")
                self.portfolio.close_position(symbol)
                continue
                
        # 2. Check for Breakout on Underlyings (BTC and ETH)
        for asset in ["BTC", "ETH"]:
            history = underlying_history.get(asset, [])
            if len(history) < 20:
                continue # wait for enough price history
                
            rsi = calculate_rsi(history, self.rsi_period)
            
            # Check short term change (last 5 ticks/minutes)
            pct_change = (history[-1] - history[-5]) / history[-5]
            
            # Bullish Breakout: RSI > 68 and price increase > 0.4%
            bullish_breakout = (rsi > 68.0 and pct_change > 0.004)
            # Bearish Breakout: RSI < 32 and price decrease > 0.4%
            bearish_breakout = (rsi < 32.0 and pct_change < -0.004)
            
            if not bullish_breakout and not bearish_breakout:
                continue
                
            # Throttle entries (max one trade every 5 minutes per asset)
            now = datetime.utcnow().timestamp()
            last_trade = self.last_breakout_time.get(asset, 0)
            if now - last_trade < 300: # 5 minutes cooldown
                continue
                
            # Already holding a long position on this asset? Skip
            has_long = any(p for p in positions if p["underlying"] == asset and p["side"] == "buy")
            if has_long:
                continue
                
            # Choose option expiry in 1 to 3 days
            candidates = []
            for symbol, prod in products_dict.items():
                if prod.get("underlying_asset", {}).get("symbol") != asset:
                    continue
                if prod.get("state") != "live" or prod.get("trading_status") != "operational":
                    continue
                    
                target_type = "call_options" if bullish_breakout else "put_options"
                if prod.get("contract_type") != target_type:
                    continue
                    
                settlement_time_str = prod.get("settlement_time")
                if not settlement_time_str:
                    continue
                    
                try:
                    expiry = datetime.strptime(settlement_time_str, "%Y-%m-%dT%H:%M:%SZ")
                    days_left = (expiry - datetime.utcnow()).total_seconds() / (24.0 * 3600.0)
                    if days_left < 1.0 or days_left > 3.0:
                        continue # Near-term options
                except Exception:
                    continue
                    
                ticker = tickers_dict.get(symbol)
                if not ticker:
                    continue
                    
                strike_price = float(prod.get("strike_price", 0.0))
                spot_price = float(ticker.get("spot_price", 0.0))
                
                # We want Near-The-Money (Strike closest to spot)
                candidates.append({
                    "symbol": symbol,
                    "product_id": prod["id"],
                    "strike_price": strike_price,
                    "distance": abs(strike_price - spot_price),
                    "price": float(ticker.get("mark_price", 0.0)),
                    "contract_value": float(prod.get("contract_value", 0.001))
                })
                
            if not candidates:
                continue
                
            # Sort by strike closest to spot
            candidates.sort(key=lambda x: x["distance"])
            target = candidates[0]
            
            # Place order
            success = self.portfolio.place_order(
                target["symbol"], target["product_id"], asset, "buy",
                self.buy_size_contracts, target["price"], target["contract_value"]
            )
            
            if success:
                self.last_breakout_time[asset] = now
                direction = "Bullish" if bullish_breakout else "Bearish"
                self.db.add_log("TRADE", f"Momentum Breakout: Detected {direction} breakout on {asset} (RSI: {rsi:.1f}, Change: {pct_change*100:.2f}%). Buying {self.buy_size_contracts} contracts of Near-the-Money option {target['symbol']} at ${target['price']:.2f}")
