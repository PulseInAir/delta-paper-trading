import os
import sys
import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Ensure api/ directory is on the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import DatabaseManager
from core.delta_client import DeltaClient
from core.portfolio import PortfolioManager
from core.trading_engine import TradingEngine

load_dotenv()

# Instantiate singletons inside the serverless execution context
# (These remain warm between requests on Vercel)
db = DatabaseManager()
client = DeltaClient()
portfolio = PortfolioManager(db, client)
engine = TradingEngine(db, client, portfolio)

app = FastAPI(
    title="Delta Exchange Options Serverless Console",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request schemas
class TradeRequest(BaseModel):
    symbol: str
    product_id: int
    underlying: str
    side: str
    size: int
    price: float
    contract_value: float = 0.001

class CloseRequest(BaseModel):
    symbol: str

class ConfigRequest(BaseModel):
    mode: str = None
    volatility_harvester: bool = None
    momentum_breakout: bool = None

# Helper to run tick on standard API requests
def trigger_tick():
    engine.tick()

# API Endpoints
@app.get("/api/status")
async def get_status():
    trigger_tick()
    status = engine.get_status()
    status.update({
        "equity_inr": portfolio.total_equity_inr,
        "cash_inr": portfolio.cash_inr,
        "margin_inr": portfolio.blocked_margin_inr,
        "unrealized_pnl_inr": portfolio.total_equity_inr - portfolio.cash_inr
    })
    return {"success": True, "result": status}

@app.get("/api/portfolio")
async def get_portfolio():
    trigger_tick()
    positions = db.load_positions()
    
    p_delta = sum(p.get("delta", 0.0) for p in positions)
    p_gamma = sum(p.get("gamma", 0.0) for p in positions)
    p_theta = sum(p.get("theta", 0.0) for p in positions)
    p_vega = sum(p.get("vega", 0.0) for p in positions)
    
    unrealized_pnl = portfolio.total_equity_inr - portfolio.cash_inr
    
    return {
        "success": True,
        "result": {
            "cash_inr": portfolio.cash_inr,
            "blocked_margin_inr": portfolio.blocked_margin_inr,
            "total_equity_inr": portfolio.total_equity_inr,
            "unrealized_pnl_inr": unrealized_pnl,
            "greeks": {
                "delta": p_delta,
                "gamma": p_gamma,
                "theta": p_theta,
                "vega": p_vega
            },
            "trading_mode": portfolio.mode
        }
    }

@app.get("/api/positions")
async def get_positions():
    trigger_tick()
    positions = db.load_positions()
    return {"success": True, "result": positions}

@app.get("/api/history")
async def get_history():
    trades = db.load_trades(limit=50)
    equity = db.load_equity_history(limit=200)
    return {
        "success": True,
        "result": {
            "trades": trades,
            "equity_curve": equity
        }
    }

@app.get("/api/logs")
async def get_logs():
    # Only return recent logs
    logs = db.load_logs(limit=50)
    logs.reverse()
    return {"success": True, "result": logs}

@app.post("/api/config")
async def update_config(req: ConfigRequest):
    conf = db.load_config_state()
    new_mode = conf["mode"]
    new_harv = conf["volatility_harvester"]
    new_break = conf["momentum_breakout"]
    
    if req.mode is not None:
        if req.mode in ["paper", "live"]:
            new_mode = req.mode
            portfolio.set_mode(req.mode)
        else:
            raise HTTPException(status_code=400, detail="Mode must be 'paper' or 'live'")
            
    if req.volatility_harvester is not None:
        new_harv = req.volatility_harvester
        engine.strategies["volatility_harvester"].toggle(req.volatility_harvester)
        
    if req.momentum_breakout is not None:
        new_break = req.momentum_breakout
        engine.strategies["momentum_breakout"].toggle(req.momentum_breakout)
        
    db.save_config_state(new_mode, new_harv, new_break)
        
    return {"success": True, "message": "Configuration updated successfully."}

@app.post("/api/manual_trade")
async def place_manual_trade(req: TradeRequest):
    success = portfolio.place_order(
        req.symbol, req.product_id, req.underlying, req.side,
        req.size, req.price, req.contract_value
    )
    if success:
        return {"success": True, "message": f"Order submitted: {req.side.upper()} {req.size} contracts of {req.symbol}."}
    else:
        return {"success": False, "message": "Order execution failed (Check margin/api logs)."}

@app.post("/api/manual_close")
async def close_position(req: CloseRequest):
    success = portfolio.close_position(req.symbol)
    if success:
        return {"success": True, "message": f"Cover order submitted for {req.symbol}."}
    else:
        return {"success": False, "message": f"Cover order failed for {req.symbol}."}

@app.post("/api/reset_paper")
async def reset_paper_portfolio():
    if portfolio.mode != "paper":
        raise HTTPException(status_code=400, detail="Portfolio reset only allowed in Paper trading mode.")
        
    initial_balance = float(os.getenv("INITIAL_PAPER_BALANCE_INR", "100000"))
    portfolio.cash_inr = initial_balance
    portfolio.blocked_margin_inr = 0.0
    portfolio.total_equity_inr = initial_balance
    
    conn = sqlite3.connect("/tmp/trading.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM positions")
    cursor.execute("DELETE FROM trades")
    cursor.execute("DELETE FROM equity_history")
    cursor.execute("INSERT INTO equity_history (timestamp, equity) VALUES (?, ?)", (db.datetime.utcnow().isoformat(), initial_balance))
    conn.commit()
    conn.close()
    
    db.save_portfolio_state(initial_balance, 0.0, initial_balance)
    db.add_log("SYSTEM", f"Paper portfolio reset to {initial_balance} INR.")
    return {"success": True, "message": "Paper portfolio reset successfully."}

# Cron Trigger - forced tick called by Vercel Cron Job every 1 minute
@app.get("/api/cron")
async def vercel_cron_trigger():
    # Force run loop tick bypassing standard 1.5s throttle to guarantee execution
    run_status = engine.tick()
    db.add_log("SYSTEM", f"Vercel Cron Job trigger executed (Tick run: {run_status})")
    return {"success": True, "run_status": run_status}
