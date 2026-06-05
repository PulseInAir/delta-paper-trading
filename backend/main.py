import os
import sys
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

# Add parent directory to path so imports work when running from backend/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import DatabaseManager
from backend.delta_client import DeltaClient
from backend.portfolio import PortfolioManager
from backend.trading_engine import TradingEngine

load_dotenv()

# Initialize managers
db = DatabaseManager()
client = DeltaClient()
portfolio = PortfolioManager(db, client)
engine = TradingEngine(db, client, portfolio)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    db.add_log("SYSTEM", "FastAPI Server Starting Up...")
    engine.start()
    yield
    # Shutdown
    db.add_log("SYSTEM", "FastAPI Server Shutting Down...")
    engine.stop()

app = FastAPI(
    title="Delta Exchange Options Trading Console",
    description="Hedge Fund Grade Options Market Maker & Volatility Harvester",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware to allow Vercel dashboard or other local frontends to query
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request Models
class TradeRequest(BaseModel):
    symbol: str
    product_id: int
    underlying: str
    side: str  # 'buy' or 'sell'
    size: int
    price: float
    contract_value: float = 0.001

class CloseRequest(BaseModel):
    symbol: str

class ConfigRequest(BaseModel):
    mode: str = None  # 'paper' or 'live'
    volatility_harvester: bool = None
    momentum_breakout: bool = None

# API Endpoints
@app.get("/api/status")
async def get_status():
    status = engine.get_status()
    # Add portfolio overview summary
    status.update({
        "equity_inr": portfolio.total_equity_inr,
        "cash_inr": portfolio.cash_inr,
        "margin_inr": portfolio.blocked_margin_inr,
        "unrealized_pnl_inr": portfolio.total_equity_inr - portfolio.cash_inr
    })
    return {"success": True, "result": status}

@app.get("/api/portfolio")
async def get_portfolio():
    positions = db.load_positions()
    
    # Calculate portfolio Greeks
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
    logs = db.load_logs(limit=50)
    # Reverse to send chronological logs for terminal
    logs.reverse()
    return {"success": True, "result": logs}

@app.post("/api/config")
async def update_config(req: ConfigRequest):
    if req.mode is not None:
        if req.mode in ["paper", "live"]:
            portfolio.set_mode(req.mode)
        else:
            raise HTTPException(status_code=400, detail="Mode must be 'paper' or 'live'")
            
    if req.volatility_harvester is not None:
        engine.strategies["volatility_harvester"].toggle(req.volatility_harvester)
        
    if req.momentum_breakout is not None:
        engine.strategies["momentum_breakout"].toggle(req.momentum_breakout)
        
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
        
    # Reset cash and positions
    initial_balance = float(os.getenv("INITIAL_PAPER_BALANCE_INR", "100000"))
    portfolio.cash_inr = initial_balance
    portfolio.blocked_margin_inr = 0.0
    portfolio.total_equity_inr = initial_balance
    
    # Delete positions in database
    conn = db.init_sqlite() # re-init just in case
    import sqlite3
    conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trading.db"))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM positions")
    cursor.execute("DELETE FROM trades")
    cursor.execute("DELETE FROM equity_history")
    cursor.execute("INSERT INTO equity_history (timestamp, equity) VALUES (?, ?)", (db.datetime.utcnow().isoformat(), initial_balance))
    conn.commit()
    conn.close()
    
    # Re-save
    db.save_portfolio_state(initial_balance, 0.0, initial_balance)
    db.add_log("SYSTEM", f"Paper portfolio reset to {initial_balance} INR.")
    return {"success": True, "message": "Paper portfolio reset successfully."}

# Mount static frontend files at the root
frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="static")
else:
    db.add_log("WARNING", "Frontend folder not found. API is running, but static website serving is disabled.")

if __name__ == "__main__":
    # Host on 0.0.0.0 to make it accessible inside docker/cloud platforms
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
