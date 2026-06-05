# Delta Exchange India Options Trading Bot & Serverless Console

A professional-grade, 100% cloud-based options trading system and real-time dashboard designed for **Delta Exchange India** and hosted entirely on **Vercel** with a **Supabase** database backend.

Live dashboard url: `https://delta-paper-trading.vercel.app/`

---

## ⚡ How the 24/7 Serverless Architecture Works

Because Vercel is a serverless platform, it cannot run a traditional continuous Python background thread. To run 24/7 in the cloud at **zero cost** and support **tick-level (2-second) operations**, the bot uses a serverless hybrid runner:

1. **Client-Driven Active Ticks**: When you open the dashboard page in your browser, the dashboard polls `/api/portfolio` every **2 seconds**. This triggers the trading engine tick synchronously. The bot instantly fetches tickers from Delta Exchange, runs the strategies, and updates your portfolio in Supabase.
2. **Background Cron Ticks**: When you close your browser tab, the bot continues running 24/7 using a **Vercel Cron Job** configured to trigger `/api/cron` every **1 minute** to scan the market and hedge your positions.
3. **Stateless Persistence**: Because Vercel serverless containers are ephemeral (they spin down after inactivity), all portfolio states, open positions, executed trades, and operation logs are stored in your **Supabase Postgres** database. On container boot, the API automatically synchronizes its local cache with Supabase.

---

## 🛠️ Step 1: Set up your Supabase Database (Free)

1. Create a free account at [Supabase](https://supabase.com).
2. Create a new project (e.g. `delta-trading-db`).
3. In the Supabase Dashboard, click on **SQL Editor** in the left sidebar.
4. Click **New Query**, paste the following script, and click **Run**:

```sql
-- 1. Portfolio State
CREATE TABLE IF NOT EXISTS portfolio (
    id INT PRIMARY KEY,
    cash NUMERIC,
    blocked_margin NUMERIC,
    total_equity NUMERIC,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Initialize portfolio with starting ₹1,00,000 (1 Lakh)
INSERT INTO portfolio (id, cash, blocked_margin, total_equity) 
VALUES (1, 100000, 0, 100000) 
ON CONFLICT (id) DO NOTHING;

-- 2. Active Option Positions
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    product_id INT,
    underlying TEXT,
    side TEXT,
    size INT,
    entry_price NUMERIC,
    mark_price NUMERIC,
    margin NUMERIC,
    unrealized_pnl NUMERIC,
    delta NUMERIC,
    gamma NUMERIC,
    theta NUMERIC,
    vega NUMERIC,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Trade History
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT,
    side TEXT,
    size INT,
    price NUMERIC,
    realized_pnl NUMERIC,
    fee NUMERIC
);

-- 4. Operations Log
CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    level TEXT,
    message TEXT
);

-- 5. Equity History (Chart snapshots)
CREATE TABLE IF NOT EXISTS equity_history (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    equity NUMERIC
);
```

---

## 🔑 Step 2: Configure Environment Variables on Vercel

To allow your Vercel deployment to communicate with Delta Exchange and write data to Supabase, add these environment variables in Vercel:

1. Go to your project on the [Vercel Dashboard](https://vercel.com).
2. Navigate to **Settings** > **Environment Variables**.
3. Add the following keys:

| Variable Name | Value | Description |
| :--- | :--- | :--- |
| `DELTA_API_KEY` | `RpBkm08s12CYltmwRyurjo0iGKsRbg` | Your Delta Exchange India API Key |
| `DELTA_API_SECRET` | `pK8AGHpUaOPu7YTMTf4sN1ogAAU0gPDvSHx4Eca59wJ8LC41CUFXVyg3AcSd` | Your Delta Exchange India API Secret |
| `DELTA_BASE_URL` | `https://api.india.delta.exchange` | India API Base URL |
| `TRADING_MODE` | `paper` | Set to `paper` for simulation (1 Lakh INR), or `live` for real trading |
| `INITIAL_PAPER_BALANCE_INR` | `100000` | Initial virtual cash (1 Lakh INR) |
| `SUPABASE_URL` | *[Your Supabase URL]* | Found in Supabase Settings > API |
| `SUPABASE_KEY` | *[Your Supabase Service/Anon Key]* | Found in Supabase Settings > API |

*After adding these variables, navigate to the **Deployments** tab on Vercel, click the three dots on your latest deployment, and click **Redeploy** to apply the keys.*

---

## 💻 Running Locally

You can also run the exact same bot and console locally on your computer:

1. Install dependencies:
   ```bash
   pip install fastapi uvicorn requests python-dotenv
   ```
2. Create a local `.env` file in the root directory and paste your configuration keys.
3. Start the FastAPI server:
   ```bash
   python api/index.py
   ```
4. Open your browser to `http://localhost:8000` to view the local dashboard. (Locally, the bot will run in a fast 1-second continuous loop and save to a local `data/trading.db` SQLite database if Supabase variables are left empty).
