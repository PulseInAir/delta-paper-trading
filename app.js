// API endpoint setup
// Automatically determines the backend location.
// If running locally on FastAPI, it will poll from relative paths.
// If deployed to Vercel, it can point to a configured environment variable backend, 
// or default to relative path which is standard if front/back are combined.
const API_BASE = ""; 

let equityChart = null;
let lastLogId = 0;
let isFirstChartLoad = true;

// Format Currency in INR (Lakh format)
function formatINR(val) {
    const num = parseFloat(val);
    if (isNaN(num)) return "0.00";
    return num.toLocaleString('en-IN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

// Log message to virtual console
function appendLog(level, message, timestamp) {
    const screen = document.getElementById("terminal-screen-logs");
    if (!screen) return;
    
    const timeStr = timestamp ? new Date(timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
    const line = document.createElement("div");
    
    // Set class depending on level
    const lvl = level.toLowerCase();
    line.className = `log-line ${lvl}`;
    line.innerHTML = `[${timeStr}] [${level.toUpperCase()}] ${message}`;
    
    screen.appendChild(line);
    // Auto scroll to bottom
    screen.scrollTop = screen.scrollHeight;
}

// Initialize Chart.js
function updateChart(historyData) {
    if (!historyData || historyData.length === 0) return;
    
    const labels = historyData.map(h => {
        const d = new Date(h.timestamp);
        return d.toLocaleTimeString() + " " + d.toLocaleDateString();
    });
    const data = historyData.map(h => h.equity);
    
    if (equityChart) {
        equityChart.data.labels = labels;
        equityChart.data.datasets[0].data = data;
        equityChart.update();
    } else {
        const ctx = document.getElementById('equityChart').getContext('2d');
        const gradient = ctx.createLinearGradient(0, 0, 0, 250);
        gradient.addColorStop(0, 'rgba(0, 242, 254, 0.25)');
        gradient.addColorStop(1, 'rgba(0, 242, 254, 0.00)');
        
        equityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Portfolio Equity (INR)',
                    data: data,
                    borderColor: '#00f2fe',
                    borderWidth: 2,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.3,
                    pointRadius: historyData.length > 50 ? 0 : 2,
                    pointHoverRadius: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: {
                        display: false,
                        grid: { display: false }
                    },
                    y: {
                        grid: { color: 'rgba(255,255,255,0.04)' },
                        ticks: {
                            color: '#909296',
                            font: { family: 'Outfit' }
                        }
                    }
                }
            }
        });
    }
}

// Fetch general portfolio metrics
async function fetchPortfolio() {
    try {
        const res = await fetch(`${API_BASE}/api/portfolio`);
        const data = await res.json();
        
        if (data.success) {
            const p = data.result;
            
            // Updates values
            document.getElementById("portfolio-equity").innerText = formatINR(p.total_equity_inr);
            document.getElementById("portfolio-cash").innerText = formatINR(p.cash_inr);
            document.getElementById("portfolio-margin").innerText = formatINR(p.blocked_margin_inr);
            document.getElementById("portfolio-pnl").innerText = formatINR(p.unrealized_pnl_inr);
            
            // Format PnL colors
            const pnlContainer = document.getElementById("portfolio-pnl-container");
            const pnlIcon = document.getElementById("pnl-icon");
            if (p.unrealized_pnl_inr > 0) {
                pnlContainer.className = "metric-value growth-text positive";
                pnlIcon.className = "fa-solid fa-chart-line metric-icon green-icon";
            } else if (p.unrealized_pnl_inr < 0) {
                pnlContainer.className = "metric-value growth-text negative";
                pnlIcon.className = "fa-solid fa-chart-line metric-icon red-icon";
            } else {
                pnlContainer.className = "metric-value";
                pnlIcon.className = "fa-solid fa-chart-line metric-icon";
            }
            
            // Yield % (calculated from starting 100,000 INR)
            const yieldPct = ((p.total_equity_inr - 100000.0) / 100000.0) * 100.0;
            const yieldEl = document.getElementById("portfolio-yield-pct");
            yieldEl.innerText = (yieldPct >= 0 ? "+" : "") + yieldPct.toFixed(2) + "%";
            yieldEl.className = yieldPct >= 0 ? "growth-text positive" : "growth-text negative";
            
            // Margin ratio
            const marginRatio = p.total_equity_inr > 0 ? (p.blocked_margin_inr / p.total_equity_inr) * 100 : 0;
            document.getElementById("margin-ratio").innerText = marginRatio.toFixed(1) + "%";
            
            // Mode checkbox syncing
            const modeToggle = document.getElementById("trading-mode-toggle");
            const isLive = p.trading_mode === "live";
            modeToggle.checked = isLive;
            
            document.getElementById("mode-paper-label").className = isLive ? "toggle-mode-text" : "toggle-mode-text active";
            document.getElementById("mode-live-label").className = isLive ? "toggle-mode-text active" : "toggle-mode-text";
            
            // Greeks Update
            document.getElementById("greek-delta").innerText = p.greeks.delta.toFixed(4);
            document.getElementById("greek-gamma").innerText = p.greeks.gamma.toFixed(4);
            document.getElementById("greek-theta").innerText = p.greeks.theta.toFixed(4);
            document.getElementById("greek-vega").innerText = p.greeks.vega.toFixed(4);
            
            // Update Greeks bar fills (normalized for visual representation)
            updateGreekBar("bar-delta", p.greeks.delta, 0.1);
            updateGreekBar("bar-gamma", p.greeks.gamma, 0.02);
            updateGreekBar("bar-theta", p.greeks.theta, 50.0);
            updateGreekBar("bar-vega", p.greeks.vega, 20.0);
        }
    } catch (e) {
        console.error("Error fetching portfolio:", e);
    }
}

// Normalise Greek value for bar width representation (0 to 100%)
function updateGreekBar(elementId, value, normalizer) {
    const bar = document.getElementById(elementId);
    if (!bar) return;
    const absVal = Math.abs(value);
    const percentage = Math.min(100, Math.max(2, (absVal / normalizer) * 100));
    bar.style.width = percentage + "%";
}

// Fetch active open positions
async function fetchPositions() {
    try {
        const res = await fetch(`${API_BASE}/api/positions`);
        const data = await res.json();
        
        if (data.success) {
            const positions = data.result;
            const tbody = document.getElementById("positions-table-body");
            
            if (positions.length === 0) {
                tbody.innerHTML = `<tr><td colspan="11" class="empty-table">No active option positions. Scanning market...</td></tr>`;
                return;
            }
            
            let html = "";
            positions.forEach(pos => {
                const pnlClass = pos.unrealized_pnl >= 0 ? "positive" : "negative";
                const sideText = pos.side.toUpperCase();
                const sideClass = pos.side === "buy" ? "buy" : "sell";
                
                html += `
                <tr>
                    <td><strong>${pos.symbol}</strong></td>
                    <td><span class="side-badge ${sideClass}">${sideText}</span></td>
                    <td>${pos.size}</td>
                    <td>$${parseFloat(pos.entry_price).toFixed(2)}</td>
                    <td>$${parseFloat(pos.mark_price).toFixed(2)}</td>
                    <td>₹${formatINR(pos.margin)}</td>
                    <td class="growth-text ${pnlClass}">₹${formatINR(pos.unrealized_pnl)}</td>
                    <td class="greek-cell">${parseFloat(pos.delta).toFixed(4)}</td>
                    <td class="greek-cell">${parseFloat(pos.theta).toFixed(4)}</td>
                    <td class="greek-cell">${parseFloat(pos.vega).toFixed(4)}</td>
                    <td><button class="close-pos-btn" onclick="closePosition('${pos.symbol}')">CLOSE</button></td>
                </tr>
                `;
            });
            tbody.innerHTML = html;
        }
    } catch (e) {
        console.error("Error fetching positions:", e);
    }
}

// Fetch system logs
async function fetchLogs() {
    try {
        const res = await fetch(`${API_BASE}/api/logs`);
        const data = await res.json();
        
        if (data.success) {
            const logs = data.result;
            const screen = document.getElementById("terminal-screen-logs");
            
            // If clearing occurred, wipe screen
            if (isFirstChartLoad) {
                screen.innerHTML = "";
                isFirstChartLoad = false;
            }
            
            // Only add logs we haven't seen yet
            logs.forEach(log => {
                if (log.id > lastLogId) {
                    appendLog(log.level, log.message, log.timestamp);
                    lastLogId = log.id;
                }
            });
        }
    } catch (e) {
        console.error("Error fetching logs:", e);
    }
}

// Fetch historical equity and update charts
async function fetchHistory() {
    try {
        const res = await fetch(`${API_BASE}/api/history`);
        const data = await res.json();
        
        if (data.success) {
            updateChart(data.result.equity_curve);
        }
    } catch (e) {
        console.error("Error fetching history:", e);
    }
}

// Fetch status and underlying prices
async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/status`);
        const data = await res.json();
        
        if (data.success) {
            const s = data.result;
            
            // Updates spot labels
            document.getElementById("spot-btc").innerText = s.btc_price > 0 ? `$${formatINR(s.btc_price).replace('.00','')}` : "Scanning...";
            document.getElementById("spot-eth").innerText = s.eth_price > 0 ? `$${formatINR(s.eth_price).replace('.00','')}` : "Scanning...";
            
            // Status dot
            const dot = document.getElementById("engine-status-dot");
            const statusTxt = document.getElementById("engine-status-text");
            if (s.running) {
                dot.className = "status-dot green";
                statusTxt.innerText = "Engine Active";
            } else {
                dot.className = "status-dot red";
                statusTxt.innerText = "Engine Stopped";
            }
            
            // Strategy checkboxes
            document.getElementById("toggle-harvester").checked = s.strategies.volatility_harvester;
            document.getElementById("badge-harvester").className = s.strategies.volatility_harvester ? "strat-badge active" : "strat-badge inactive";
            document.getElementById("badge-harvester").innerText = s.strategies.volatility_harvester ? "ACTIVE" : "INACTIVE";
            
            document.getElementById("toggle-breakout").checked = s.strategies.momentum_breakout;
            document.getElementById("badge-breakout").className = s.strategies.momentum_breakout ? "strat-badge active" : "strat-badge inactive";
            document.getElementById("badge-breakout").innerText = s.strategies.momentum_breakout ? "ACTIVE" : "INACTIVE";
        }
    } catch (e) {
        console.error("Error fetching status:", e);
    }
}

// Manual Position Close Trigger
async function closePosition(symbol) {
    if (!confirm(`Are you sure you want to close position on ${symbol}?`)) return;
    
    try {
        appendLog("INFO", `Sending manual close order for position ${symbol}...`);
        const res = await fetch(`${API_BASE}/api/manual_close`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol: symbol })
        });
        const data = await res.json();
        
        if (data.success) {
            appendLog("TRADE", data.message);
            fetchPositions();
            fetchPortfolio();
        } else {
            appendLog("ERROR", data.message);
        }
    } catch (e) {
        appendLog("ERROR", `Failed to send close order: ${e}`);
    }
}

// Manual Order Form Submission
document.getElementById("manual-order-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    
    const underlying = document.getElementById("trade-underlying").value;
    const side = document.getElementById("trade-side").value;
    const symbol = document.getElementById("trade-symbol").value.trim();
    const product_id = parseInt(document.getElementById("trade-product-id").value);
    const size = parseInt(document.getElementById("trade-size").value);
    const price = parseFloat(document.getElementById("trade-price").value);
    
    const contract_value = underlying === "BTC" ? 0.001 : 0.01;
    
    try {
        appendLog("INFO", `Placing manual order: ${side.toUpperCase()} ${size} ${symbol} at $${price}...`);
        const res = await fetch(`${API_BASE}/api/manual_trade`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                symbol, product_id, underlying, side, size, price, contract_value
            })
        });
        const data = await res.json();
        
        if (data.success) {
            appendLog("TRADE", data.message);
            // Clear symbol input
            document.getElementById("trade-symbol").value = "";
            document.getElementById("trade-product-id").value = "";
            document.getElementById("trade-price").value = "";
            
            fetchPositions();
            fetchPortfolio();
        } else {
            appendLog("ERROR", data.message);
        }
    } catch (err) {
        appendLog("ERROR", `Failed to place manual trade: ${err}`);
    }
});

// Trading Mode Switcher (Simulation vs Live)
document.getElementById("trading-mode-toggle").addEventListener("change", async (e) => {
    const isLive = e.target.checked;
    const targetMode = isLive ? "live" : "paper";
    
    if (isLive) {
        const conf = confirm("WARNING: Switching to LIVE trading mode! Trades will be executed with real INR funds on Delta Exchange India using your configured API keys. Do you want to continue?");
        if (!conf) {
            e.target.checked = false;
            return;
        }
    }
    
    try {
        const res = await fetch(`${API_BASE}/api/config`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: targetMode })
        });
        const data = await res.json();
        if (data.success) {
            appendLog("SYSTEM", `Trading mode updated to ${targetMode.toUpperCase()}`);
            fetchPortfolio();
        }
    } catch (err) {
        console.error("Failed to switch mode:", err);
    }
});

// Strategy Toggles
document.getElementById("toggle-harvester").addEventListener("change", async (e) => {
    const active = e.target.checked;
    try {
        const res = await fetch(`${API_BASE}/api/config`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ volatility_harvester: active })
        });
        const data = await res.json();
        if (data.success) {
            fetchStatus();
        }
    } catch (err) {
        console.error(err);
    }
});

document.getElementById("toggle-breakout").addEventListener("change", async (e) => {
    const active = e.target.checked;
    try {
        const res = await fetch(`${API_BASE}/api/config`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ momentum_breakout: active })
        });
        const data = await res.json();
        if (data.success) {
            fetchStatus();
        }
    } catch (err) {
        console.error(err);
    }
});

// Risk / Leverage slider label update
document.getElementById("risk-leverage").addEventListener("input", (e) => {
    document.getElementById("risk-leverage-val").innerText = parseFloat(e.target.value).toFixed(1) + "x";
});

// Reset Simulation Button
document.getElementById("reset-paper-btn").addEventListener("click", async () => {
    if (!confirm("Are you sure you want to reset the Paper Trading portfolio? This will wipe your simulated trade history, active paper positions, and restore cash to ₹1,00,000.")) return;
    
    try {
        const res = await fetch(`${API_BASE}/api/reset_paper`, { method: "POST" });
        const data = await res.json();
        if (data.success) {
            // Wipes logs locally on screen
            document.getElementById("terminal-screen-logs").innerHTML = "";
            lastLogId = 0;
            appendLog("SYSTEM", data.message);
            
            fetchPortfolio();
            fetchPositions();
            fetchHistory();
        } else {
            alert("Reset failed: " + data.message);
        }
    } catch (err) {
        console.error(err);
    }
});

// Clear screen terminal
document.getElementById("clear-logs-btn").addEventListener("click", () => {
    document.getElementById("terminal-screen-logs").innerHTML = `<div class="log-line system">[SYSTEM] Console cleared by user.</div>`;
});

// Main Loop: Poll data periodically (every 2 seconds)
function startPolling() {
    // Initial fetch
    fetchStatus();
    fetchPortfolio();
    fetchPositions();
    fetchLogs();
    fetchHistory();
    
    // Set Interval
    setInterval(() => {
        fetchStatus();
        fetchPortfolio();
        fetchPositions();
        fetchLogs();
    }, 2000);
    
    // Poll history and charts less frequently (every 10 seconds)
    setInterval(() => {
        fetchHistory();
    }, 10000);
}

// Window Onload Start
window.addEventListener("DOMContentLoaded", () => {
    appendLog("SYSTEM", "Console online. Start polling data endpoints...");
    startPolling();
});
