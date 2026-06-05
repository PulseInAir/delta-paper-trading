import math
import os
from datetime import datetime
from dotenv import load_dotenv
from api.db import DatabaseManager
from api.delta_client import DeltaClient

load_dotenv()

FIXED_INR_USD_RATE = 85.0

# Black-Scholes Mathematical Functions
def standard_normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def standard_normal_pdf(x):
    return math.exp(-0.5 * x**2) / math.sqrt(2.0 * math.pi)

def calculate_greeks(option_type, S, K, T, r, sigma):
    if T <= 0.0001:
        if option_type == 'call':
            delta = 1.0 if S > K else 0.0
        else:
            delta = -1.0 if S < K else 0.0
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        
    if sigma <= 0.0001:
        sigma = 0.0001
        
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        pdf_d1 = standard_normal_pdf(d1)
        cdf_d1 = standard_normal_cdf(d1)
        cdf_d2 = standard_normal_cdf(d2)
        
        if option_type == 'call':
            delta = cdf_d1
            theta = -(S * pdf_d1 * sigma) / (2.0 * math.sqrt(T)) - r * K * math.exp(-r * T) * cdf_d2
        else:
            delta = cdf_d1 - 1.0
            theta = -(S * pdf_d1 * sigma) / (2.0 * math.sqrt(T)) + r * K * math.exp(-r * T) * standard_normal_cdf(-d2)
            
        gamma = pdf_d1 / (S * sigma * math.sqrt(T))
        vega = S * math.sqrt(T) * pdf_d1
        
        return {
            "delta": delta,
            "gamma": gamma,
            "theta": theta / 365.0,
            "vega": vega / 100.0
        }
    except Exception:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}


class PortfolioManager:
    def __init__(self, db: DatabaseManager, client: DeltaClient):
        self.db = db
        self.client = client
        self.mode = os.getenv("TRADING_MODE", "paper").lower()
        
        state = self.db.load_portfolio_state()
        self.cash_inr = state["cash"]
        self.blocked_margin_inr = state["blocked_margin"]
        self.total_equity_inr = state["total_equity"]
        
    def set_mode(self, mode):
        if mode in ["paper", "live"]:
            self.mode = mode
            self.db.add_log("SYSTEM", f"Trading mode switched to {mode.upper()}")
            
    def update_valuation(self, tickers_dict, products_dict):
        if self.mode == "live":
            self._update_live_valuation(tickers_dict, products_dict)
        else:
            self._update_paper_valuation(tickers_dict, products_dict)
            
    def _update_paper_valuation(self, tickers_dict, products_dict):
        positions = self.db.load_positions()
        
        total_unrealized_pnl_inr = 0.0
        total_margin_inr = 0.0
        
        portfolio_delta = 0.0
        portfolio_gamma = 0.0
        portfolio_theta = 0.0
        portfolio_vega = 0.0
        
        for pos in positions:
            symbol = pos["symbol"]
            ticker = tickers_dict.get(symbol)
            prod = products_dict.get(symbol)
            
            if not ticker or not prod:
                continue
                
            mark_price = float(ticker.get("mark_price", pos["mark_price"]))
            spot_price = float(ticker.get("spot_price", 0.0))
            if spot_price == 0.0:
                underlying_symbol = prod.get("underlying_asset", {}).get("symbol")
                underlying_ticker = tickers_dict.get(underlying_symbol)
                if underlying_ticker:
                    spot_price = float(underlying_ticker.get("mark_price", 0.0))
            
            contract_value = float(prod.get("contract_value", 0.001))
            size = int(pos["size"])
            side = pos["side"]
            entry_price = float(pos["entry_price"])
            
            if side == "buy":
                pnl_usd = (mark_price - entry_price) * contract_value * size
                margin_usd = 0.0
            else:
                pnl_usd = (entry_price - mark_price) * contract_value * size
                margin_usd = 0.12 * spot_price * contract_value * size
                
            pnl_inr = pnl_usd * FIXED_INR_USD_RATE
            margin_inr = margin_usd * FIXED_INR_USD_RATE
            
            total_unrealized_pnl_inr += pnl_inr
            total_margin_inr += margin_inr
            
            strike_price = float(prod.get("strike_price", 0.0))
            iv = float(ticker.get("implied_volatility", prod.get("product_specs", {}).get("min_volatility", 0.40)))
            
            settlement_time_str = prod.get("settlement_time")
            if settlement_time_str:
                try:
                    expiry = datetime.strptime(settlement_time_str, "%Y-%m-%dT%H:%M:%SZ")
                    seconds_left = (expiry - datetime.utcnow()).total_seconds()
                    T = max(0.0, seconds_left / (365.0 * 24.0 * 3600.0))
                except Exception:
                    T = 0.08
            else:
                T = 0.08
                
            option_type = 'call' if prod.get("contract_type") == 'call_options' else 'put'
            
            greeks = calculate_greeks(option_type, spot_price, strike_price, T, 0.05, iv)
            
            dir_coeff = 1 if side == "buy" else -1
            pos_weight = size * contract_value * dir_coeff
            
            pos_delta = greeks["delta"] * pos_weight
            pos_gamma = greeks["gamma"] * pos_weight
            pos_theta = greeks["theta"] * pos_weight
            pos_vega = greeks["vega"] * pos_weight
            
            portfolio_delta += pos_delta
            portfolio_gamma += pos_gamma
            portfolio_theta += pos_theta
            portfolio_vega += pos_vega
            
            self.db.save_position(
                symbol, pos["product_id"], pos["underlying"], side, size,
                entry_price, mark_price, margin_inr, pnl_inr,
                pos_delta, pos_gamma, pos_theta, pos_vega
            )
            
        self.blocked_margin_inr = total_margin_inr
        self.total_equity_inr = self.cash_inr + total_unrealized_pnl_inr
        self.db.save_portfolio_state(self.cash_inr, self.blocked_margin_inr, self.total_equity_inr)

    def _update_live_valuation(self, tickers_dict, products_dict):
        bal_res = self.client.get_balances()
        if bal_res.get("success"):
            results = bal_res.get("result", [])
            inr_bal = next((b for b in results if b.get("asset_symbol") == "INR"), None)
            usd_bal = next((b for b in results if b.get("asset_symbol") == "USD"), None)
            
            if inr_bal:
                self.cash_inr = float(inr_bal.get("available_balance", 0.0))
                self.blocked_margin_inr = float(inr_bal.get("blocked_margin", 0.0))
                self.total_equity_inr = float(inr_bal.get("balance", 0.0))
            elif usd_bal:
                self.cash_inr = float(usd_bal.get("available_balance", 0.0)) * FIXED_INR_USD_RATE
                self.blocked_margin_inr = float(usd_bal.get("blocked_margin", 0.0)) * FIXED_INR_USD_RATE
                self.total_equity_inr = float(usd_bal.get("balance", 0.0)) * FIXED_INR_USD_RATE
                
        pos_res = self.client.get_positions()
        if pos_res.get("success"):
            api_positions = pos_res.get("result", [])
            api_symbols = [p.get("product", {}).get("symbol") for p in api_positions]
            local_positions = self.db.load_positions()
            for lp in local_positions:
                if lp["symbol"] not in api_symbols:
                    self.db.delete_position(lp["symbol"])
                    
            for pos in api_positions:
                prod = pos.get("product", {})
                symbol = prod.get("symbol")
                product_id = prod.get("id")
                underlying = prod.get("underlying_asset", {}).get("symbol", "BTC")
                size = abs(int(float(pos.get("size", 0))))
                
                if size == 0:
                    self.db.delete_position(symbol)
                    continue
                    
                side = "buy" if float(pos.get("size", 0)) > 0 else "sell"
                entry_price = float(pos.get("entry_price", 0.0))
                mark_price = float(pos.get("mark_price", 0.0))
                margin_inr = float(pos.get("margin", 0.0)) * FIXED_INR_USD_RATE
                pnl_inr = float(pos.get("unrealized_pnl", 0.0)) * FIXED_INR_USD_RATE
                
                ticker = tickers_dict.get(symbol)
                strike_price = float(prod.get("strike_price", 0.0))
                iv = float(ticker.get("implied_volatility", prod.get("product_specs", {}).get("min_volatility", 0.40))) if ticker else 0.40
                
                settlement_time_str = prod.get("settlement_time")
                if settlement_time_str:
                    try:
                        expiry = datetime.strptime(settlement_time_str, "%Y-%m-%dT%H:%M:%SZ")
                        seconds_left = (expiry - datetime.utcnow()).total_seconds()
                        T = max(0.0, seconds_left / (365.0 * 24.0 * 3600.0))
                    except Exception:
                        T = 0.08
                else:
                    T = 0.08
                    
                spot_price = float(ticker.get("spot_price", 0.0)) if ticker else 0.0
                option_type = 'call' if prod.get("contract_type") == 'call_options' else 'put'
                
                greeks = calculate_greeks(option_type, spot_price, strike_price, T, 0.05, iv)
                
                dir_coeff = 1 if side == "buy" else -1
                contract_value = float(prod.get("contract_value", 0.001))
                pos_weight = size * contract_value * dir_coeff
                
                self.db.save_position(
                    symbol, product_id, underlying, side, size,
                    entry_price, mark_price, margin_inr, pnl_inr,
                    greeks["delta"] * pos_weight,
                    greeks["gamma"] * pos_weight,
                    greeks["theta"] * pos_weight,
                    greeks["vega"] * pos_weight
                )
                
        self.db.save_portfolio_state(self.cash_inr, self.blocked_margin_inr, self.total_equity_inr)

    def execute_paper_order(self, symbol, product_id, underlying, side, size, price, contract_value):
        fee_rate = 0.0005
        notional_value_usd = size * contract_value * price
        fee_inr = notional_value_usd * fee_rate * FIXED_INR_USD_RATE
        premium_inr = notional_value_usd * FIXED_INR_USD_RATE
        
        positions = self.db.load_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        
        if side == "buy":
            required_funds = premium_inr + fee_inr
            if self.cash_inr < required_funds:
                self.db.add_log("WARNING", f"Insufficient funds to buy option {symbol}. Required: {required_funds:.2f} INR, Available: {self.cash_inr:.2f} INR")
                return False
                
            self.cash_inr -= required_funds
            
            if pos:
                if pos["side"] == "buy":
                    new_size = pos["size"] + size
                    new_entry = ((pos["entry_price"] * pos["size"]) + (price * size)) / new_size
                    self.db.save_position(symbol, product_id, underlying, "buy", new_size, new_entry, price, 0.0, 0.0, 0, 0, 0, 0)
                else:
                    if pos["size"] > size:
                        new_size = pos["size"] - size
                        realized_pnl_inr = (pos["entry_price"] - price) * contract_value * size * FIXED_INR_USD_RATE
                        self.cash_inr += (pos["margin"] / pos["size"]) * size
                        self.db.save_position(symbol, product_id, underlying, "sell", new_size, pos["entry_price"], price, pos["margin"] * (new_size / pos["size"]), 0.0, 0, 0, 0, 0)
                        self.db.add_trade(symbol, "buy", size, price, realized_pnl_inr, fee_inr)
                        self.db.add_log("TRADE", f"Bought {size} contracts of {symbol} at ${price:.2f} to cover short. Realized PnL: {realized_pnl_inr:.2f} INR. Fee: {fee_inr:.2f} INR")
                    else:
                        realized_pnl_inr = (pos["entry_price"] - price) * contract_value * pos["size"] * FIXED_INR_USD_RATE
                        self.cash_inr += pos["margin"]
                        self.db.delete_position(symbol)
                        self.db.add_trade(symbol, "buy", pos["size"], price, realized_pnl_inr, fee_inr)
                        self.db.add_log("TRADE", f"Fully covered short position on {symbol} buying {pos['size']} contracts at ${price:.2f}. PnL: {realized_pnl_inr:.2f} INR")
            else:
                self.db.save_position(symbol, product_id, underlying, "buy", size, price, price, 0.0, 0.0, 0, 0, 0, 0)
                self.db.add_trade(symbol, "buy", size, price, 0.0, fee_inr)
                self.db.add_log("TRADE", f"Bought {size} contracts of {symbol} (Long option) at ${price:.2f}. Premium: {premium_inr:.2f} INR. Fee: {fee_inr:.2f} INR")
                
        else:
            margin_usd = 0.12 * price * contract_value * size
            margin_inr = margin_usd * FIXED_INR_USD_RATE
            
            if self.cash_inr < margin_inr + fee_inr:
                self.db.add_log("WARNING", f"Insufficient margin to sell option {symbol}. Required: {margin_inr + fee_inr:.2f} INR, Available: {self.cash_inr:.2f} INR")
                return False
                
            self.cash_inr += premium_inr - fee_inr
            
            if pos:
                if pos["side"] == "sell":
                    new_size = pos["size"] + size
                    new_entry = ((pos["entry_price"] * pos["size"]) + (price * size)) / new_size
                    self.db.save_position(symbol, product_id, underlying, "sell", new_size, new_entry, price, pos["margin"] + margin_inr, 0.0, 0, 0, 0, 0)
                else:
                    if pos["size"] > size:
                        new_size = pos["size"] - size
                        realized_pnl_inr = (price - pos["entry_price"]) * contract_value * size * FIXED_INR_USD_RATE
                        self.db.save_position(symbol, product_id, underlying, "buy", new_size, pos["entry_price"], price, 0.0, 0.0, 0, 0, 0, 0)
                        self.db.add_trade(symbol, "sell", size, price, realized_pnl_inr, fee_inr)
                        self.db.add_log("TRADE", f"Sold {size} contracts of long option {symbol} at ${price:.2f}. Realized PnL: {realized_pnl_inr:.2f} INR")
                    else:
                        realized_pnl_inr = (price - pos["entry_price"]) * contract_value * pos["size"] * FIXED_INR_USD_RATE
                        self.db.delete_position(symbol)
                        self.db.add_trade(symbol, "sell", pos["size"], price, realized_pnl_inr, fee_inr)
                        self.db.add_log("TRADE", f"Fully closed long position on {symbol} selling {pos['size']} contracts at ${price:.2f}. PnL: {realized_pnl_inr:.2f} INR")
            else:
                self.db.save_position(symbol, product_id, underlying, "sell", size, price, price, margin_inr, 0.0, 0, 0, 0, 0)
                self.db.add_trade(symbol, "sell", size, price, 0.0, fee_inr)
                self.db.add_log("TRADE", f"Sold {size} contracts of {symbol} (Short option) at ${price:.2f}. Premium collected: {premium_inr:.2f} INR. Initial Margin blocked: {margin_inr:.2f} INR")
                
        self.db.save_portfolio_state(self.cash_inr, self.blocked_margin_inr, self.total_equity_inr)
        return True

    def place_order(self, symbol, product_id, underlying, side, size, price, contract_value):
        if self.mode == "live":
            res = self.client.place_order(product_id, size, side, price=price)
            if res.get("success"):
                order_id = res.get("result", {}).get("id")
                self.db.add_log("INFO", f"Live order placed: {side.upper()} {size} contracts of {symbol} at ${price:.2f}. Order ID: {order_id}")
                return True
            else:
                error_msg = res.get("error", {}).get("message", "Unknown error")
                self.db.add_log("WARNING", f"Failed to place live order: {error_msg}")
                return False
        else:
            return self.execute_paper_order(symbol, product_id, underlying, side, size, price, contract_value)

    def close_position(self, symbol):
        positions = self.db.load_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if not pos:
            return False
            
        side = "sell" if pos["side"] == "buy" else "buy"
        price = pos["mark_price"]
        contract_value = 0.001 if "BTC" in symbol else 0.01
        
        return self.place_order(symbol, pos["product_id"], pos["underlying"], side, pos["size"], price, contract_value)
