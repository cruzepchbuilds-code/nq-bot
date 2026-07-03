"""
crypto/charts.py

Runs all crypto strategies and generates an HTML dashboard.
Matches the CruzCapital dark-theme style.

Run from project root:
    python crypto/charts.py

Output:
    crypto/results/dashboard.html
"""

import sys, os, json
from datetime import date
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "research"))

from sweep import (
    load_bars, load_funding, run_all, stats, OOS_START, POSITION_USD, COST_PCT
)

OUT_PATH = "crypto/results/dashboard.html"


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────────────────────

def equity_curve(trades):
    """Return (labels, values) for a cumulative P&L chart."""
    sorted_t = sorted(trades, key=lambda t: t["date"])
    labels, values = [], []
    cum = 0.0
    for t in sorted_t:
        cum += t["net"]
        labels.append(t["date"].isoformat())
        values.append(round(cum, 2))
    return labels, values


def monthly_pnl(trades):
    """Return {YYYY-MM: net} dict."""
    m = defaultdict(float)
    for t in trades:
        key = t["date"].strftime("%Y-%m")
        m[key] += t["net"]
    return dict(sorted(m.items()))


def dow_pnl(trades):
    """Return [Mon..Sun] average net P&L per trade."""
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    result = {}
    for i, name in enumerate(names):
        ts = [t for t in trades if t["date"].weekday() == i]
        result[name] = round(sum(t["net"] for t in ts) / len(ts), 2) if ts else 0.0
    return result


def oos_start_index(labels):
    """Find index where OOS period begins."""
    oos_str = OOS_START.isoformat()
    for i, l in enumerate(labels):
        if l >= oos_str:
            return i
    return len(labels)


def verdict(s_oos):
    if not s_oos:
        return ("NO TRADES", "#e74c3c")
    pf = s_oos["pf"]
    if pf >= 1.4:
        return ("BUILD IT", "#2ecc71")
    if pf >= 1.2:
        return ("MARGINAL", "#f39c12")
    return ("DISCARD", "#e74c3c")


# ─────────────────────────────────────────────────────────────────────────────
# HTML generator
# ─────────────────────────────────────────────────────────────────────────────

STRATEGY_NAMES = {
    "London_BO":    "London Open Breakout",
    "NY_Mom":       "NY Open Momentum",
    "CME_Gap":      "CME Weekend Gap Fill",
    "Funding_Fade": "Funding Rate Extreme Fade",
    "Weekly_ORB":   "Weekly ORB (Mon)",
}

STRATEGY_DESC = {
    "London_BO":    "05–08 UTC range · 1% stop · 2R · flatten 14:00 UTC",
    "NY_Mom":       "2%+ pre-mkt drift · 1% stop · 1.5R · flatten 18:00 UTC",
    "CME_Gap":      "Fri→Sun gap >1% · fade to fill · 1% stop · flatten Tue",
    "Funding_Fade": "|rate| >0.08% · fade overcrowded perps · 1.5% stop · 2R · 8h",
    "Weekly_ORB":   "Mon 00–08 UTC range · range-width stop · 2R · flatten 23:00",
}


def build_html(all_trades, first_date, last_date):
    # Precompute stats for all strategies
    strategy_keys = list(all_trades.keys())
    rows_html = ""
    chart_datasets = []
    colors = ["#00d4ff", "#2ecc71", "#f39c12", "#e74c3c", "#a78bfa"]

    best_pf = 0.0
    best_name = ""

    for i, key in enumerate(strategy_keys):
        trades = all_trades[key]
        s_is  = stats([t for t in trades if t["date"] <  OOS_START])
        s_oos = stats([t for t in trades if t["date"] >= OOS_START])
        s_all = stats(trades)
        v_text, v_color = verdict(s_oos)
        color = colors[i % len(colors)]

        if s_oos and s_oos["pf"] > best_pf:
            best_pf = s_oos["pf"]
            best_name = STRATEGY_NAMES[key]

        def fmt(s, field, fmt_str):
            return fmt_str.format(s[field]) if s else "—"

        rows_html += f"""
        <tr>
          <td><strong style="color:{color}">{STRATEGY_NAMES[key]}</strong><br>
              <span style="color:#666;font-size:0.8em">{STRATEGY_DESC[key]}</span></td>
          <td>{fmt(s_is,  'n',   '{:,}')}</td>
          <td>{fmt(s_is,  'wr',  '{:.0%}')}</td>
          <td>{fmt(s_is,  'pf',  '{:.2f}')}</td>
          <td>{fmt(s_oos, 'n',   '{:,}')}</td>
          <td>{fmt(s_oos, 'wr',  '{:.0%}')}</td>
          <td style="font-weight:bold">{fmt(s_oos, 'pf', '{:.2f}')}</td>
          <td style="color:{'#2ecc71' if s_oos and s_oos['net']>0 else '#e74c3c'}">{fmt(s_oos, 'net', '${:,.0f}')}</td>
          <td style="color:{v_color};font-weight:bold">{v_text}</td>
        </tr>"""

        # Equity curve dataset
        labels, values = equity_curve(trades)
        chart_datasets.append({
            "key": key,
            "name": STRATEGY_NAMES[key],
            "color": color,
            "labels": labels,
            "values": values,
            "oos_idx": oos_start_index(labels),
        })

    # Headline stats
    total_oos_trades = sum(
        (s["n"] if (s := stats([t for t in all_trades[k] if t["date"] >= OOS_START])) else 0)
        for k in strategy_keys
    )

    # Monthly P&L for best strategy
    best_key = max(all_trades, key=lambda k: (
        (stats([t for t in all_trades[k] if t["date"] >= OOS_START]) or {"pf": 0})["pf"]
    ))
    best_monthly = monthly_pnl(all_trades[best_key])
    best_oos = stats([t for t in all_trades[best_key] if t["date"] >= OOS_START])
    best_is  = stats([t for t in all_trades[best_key] if t["date"] <  OOS_START])

    monthly_labels = json.dumps(list(best_monthly.keys()))
    monthly_values = json.dumps([round(v, 2) for v in best_monthly.values()])
    monthly_colors = json.dumps(["#2ecc71" if v >= 0 else "#e74c3c" for v in best_monthly.values()])

    # DOW for best strategy
    dow_data = dow_pnl(all_trades[best_key])
    dow_labels = json.dumps(list(dow_data.keys()))
    dow_values = json.dumps(list(dow_data.values()))
    dow_colors = json.dumps(["#2ecc71" if v >= 0 else "#e74c3c" for v in dow_data.values()])

    # Chart.js datasets JSON
    chart_js_data = json.dumps(chart_datasets)

    oos_net_str = f"${best_oos['net']:,.0f}" if best_oos else "—"
    oos_pf_str  = f"{best_oos['pf']:.2f}"   if best_oos else "—"
    oos_wr_str  = f"{best_oos['wr']:.0%}"   if best_oos else "—"
    is_pf_str   = f"{best_is['pf']:.2f}"    if best_is  else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CruzCapital Crypto — Strategy Research</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #1a1a2e; color: #eee;
         margin: 0; padding: 20px; }}
  h1 {{ color: #00d4ff; border-bottom: 2px solid #00d4ff; padding-bottom: 10px; }}
  h2 {{ color: #00d4ff; margin-top: 30px; }}
  h3 {{ color: #aaa; margin-top: 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
  th {{ background: #16213e; color: #00d4ff; padding: 10px 14px; text-align: left; }}
  td {{ padding: 9px 14px; border-bottom: 1px solid #2a2a4a; font-size: 0.95em; }}
  tr:hover {{ background: #16213e; }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 20px 0; }}
  .stat {{ background: #16213e; border-left: 4px solid #00d4ff; padding: 14px 18px; border-radius: 4px; }}
  .stat.green {{ border-left-color: #2ecc71; }}
  .stat.amber {{ border-left-color: #f39c12; }}
  .stat.red   {{ border-left-color: #e74c3c; }}
  .stat-value {{ font-size: 1.6em; font-weight: bold; color: #00d4ff; }}
  .stat-value.green {{ color: #2ecc71; }}
  .stat-value.amber {{ color: #f39c12; }}
  .stat-label {{ font-size: 0.85em; color: #aaa; margin-top: 4px; }}
  .verdict {{ background: #16213e; border-left: 4px solid #2ecc71; padding: 14px 18px;
              margin: 20px 0; border-radius: 4px; line-height: 1.6; }}
  .verdict.amber {{ border-left-color: #f39c12; }}
  .badge {{ display: inline-block; background: #0d3d27; color: #2ecc71; border-radius: 3px;
            padding: 3px 10px; font-size: 0.85em; font-weight: bold; }}
  .chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
  .chart-box {{ background: #16213e; border-radius: 6px; padding: 20px; }}
  .chart-box.wide {{ grid-column: 1 / -1; }}
  .tab-bar {{ display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }}
  .tab {{ background: #16213e; color: #aaa; border: 1px solid #2a2a4a; border-radius: 4px;
          padding: 6px 14px; cursor: pointer; font-size: 0.9em; transition: all 0.2s; }}
  .tab.active {{ background: #0f3460; color: #00d4ff; border-color: #00d4ff; }}
  .tab:hover {{ color: #00d4ff; }}
</style>
</head>
<body>

<h1>CruzCapital Crypto — BTC Strategy Research</h1>
<p style="color:#aaa">
  Binance BTC/USDT 1-min &nbsp;|&nbsp; {first_date} → {last_date} &nbsp;|&nbsp;
  IS = 2022–2023 &nbsp;|&nbsp; OOS = 2024–Jun 2026 &nbsp;|&nbsp;
  $10,000 notional · 0.10% RT cost &nbsp;|&nbsp;
  <span class="badge">5 STRATEGIES TESTED</span>
</p>

<!-- ── Headline Stats ─────────────────────────────────────────────────────── -->
<h2>Best Strategy — {STRATEGY_NAMES[best_key]}</h2>
<div class="stat-grid">
  <div class="stat green">
    <div class="stat-value green">{oos_pf_str}</div>
    <div class="stat-label">OOS Profit Factor</div>
  </div>
  <div class="stat green">
    <div class="stat-value green">{oos_net_str}</div>
    <div class="stat-label">OOS Net P&amp;L ($10k position)</div>
  </div>
  <div class="stat">
    <div class="stat-value">{oos_wr_str}</div>
    <div class="stat-label">OOS Win Rate</div>
  </div>
  <div class="stat amber">
    <div class="stat-value amber">{is_pf_str}</div>
    <div class="stat-label">IS Profit Factor (2022–23)</div>
  </div>
</div>

<!-- ── Strategy Comparison Table ─────────────────────────────────────────── -->
<h2>All 5 Strategies — Walk-Forward Results</h2>
<table>
  <thead><tr>
    <th>Strategy</th>
    <th>IS N</th><th>IS WR</th><th>IS PF</th>
    <th>OOS N</th><th>OOS WR</th><th>OOS PF</th><th>OOS Net</th>
    <th>Verdict</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>

<div class="verdict">
  <strong>Research Verdict:</strong>
  Best OOS performer: <strong style="color:#00d4ff">{best_name}</strong> (PF {oos_pf_str}).
  OOS PF ≥ 1.3 = worth building a live paper bot for.
  OOS PF ≥ 1.4 = deploy with $10k notional.
  Any strategy below PF 1.1 OOS = discard, do not optimize further.
</div>

<!-- ── Equity Curves ──────────────────────────────────────────────────────── -->
<h2>Equity Curves — $10,000 Notional Per Trade</h2>
<div class="tab-bar" id="eq-tabs"></div>
<div class="chart-box wide">
  <canvas id="equityChart" height="80"></canvas>
</div>

<!-- ── Monthly P&L + DOW ─────────────────────────────────────────────────── -->
<h2>Best Strategy Deep Dive — {STRATEGY_NAMES[best_key]}</h2>
<div class="chart-grid">
  <div class="chart-box">
    <h3 style="margin-top:0">Monthly P&amp;L</h3>
    <canvas id="monthlyChart" height="140"></canvas>
  </div>
  <div class="chart-box">
    <h3 style="margin-top:0">Avg P&amp;L by Day of Week</h3>
    <canvas id="dowChart" height="140"></canvas>
  </div>
</div>

<!-- ── Parameter Notes ───────────────────────────────────────────────────── -->
<h2>Strategy Parameters</h2>
<table>
  <thead><tr><th>Strategy</th><th>Entry Trigger</th><th>Stop</th><th>Target</th><th>Flatten</th></tr></thead>
  <tbody>
    <tr><td><strong style="color:#00d4ff">London Open BO</strong></td>
        <td>Close breaks 05–08 UTC range ±0.2%</td><td>1.0%</td><td>2.0% (2R)</td><td>14:00 UTC</td></tr>
    <tr><td><strong style="color:#2ecc71">NY Open Momentum</strong></td>
        <td>≥2% drift midnight→14:00 UTC</td><td>1.0%</td><td>1.5% (1.5R)</td><td>18:00 UTC</td></tr>
    <tr><td><strong style="color:#f39c12">CME Weekend Gap Fill</strong></td>
        <td>Fri close vs Sun 22:00 UTC gap ≥1%</td><td>1.0%</td><td>Fri close (100% fill)</td><td>Tue 14:00 UTC</td></tr>
    <tr><td><strong style="color:#e74c3c">Funding Rate Extreme</strong></td>
        <td>|8h rate| > 0.08% at funding window</td><td>1.5%</td><td>3.0% (2R)</td><td>Next funding window</td></tr>
    <tr><td><strong style="color:#a78bfa">Weekly ORB</strong></td>
        <td>Mon 00–08 UTC range break ±0.1%</td><td>Range width</td><td>2R</td><td>Mon 23:00 UTC</td></tr>
  </tbody>
</table>

<!-- ── Chart.js Scripts ──────────────────────────────────────────────────── -->
<script>
const datasets = {chart_js_data};
const colors   = {json.dumps(colors)};
const OOS_START = "{OOS_START.isoformat()}";

// ── Equity curves ──
const eqCtx = document.getElementById("equityChart").getContext("2d");
let eqChart = null;

function buildEquity(keys) {{
  const chartDatasets = keys.map(k => {{
    const d = datasets.find(x => x.key === k);
    return {{
      label: d.name,
      data: d.labels.map((l, i) => ({{ x: l, y: d.values[i] }})),
      borderColor: d.color,
      backgroundColor: d.color + "22",
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.1,
      fill: false,
    }};
  }});

  // OOS divider annotation (vertical line)
  const allLabels = [...new Set(datasets.flatMap(d => d.labels))].sort();
  const oosIdx = allLabels.findIndex(l => l >= OOS_START);

  if (eqChart) eqChart.destroy();
  eqChart = new Chart(eqCtx, {{
    type: "line",
    data: {{ datasets: chartDatasets }},
    options: {{
      responsive: true,
      interaction: {{ mode: "index", intersect: false }},
      scales: {{
        x: {{
          type: "time",
          time: {{ unit: "month" }},
          ticks: {{ color: "#aaa", maxTicksLimit: 24 }},
          grid: {{ color: "#2a2a4a" }},
        }},
        y: {{
          ticks: {{ color: "#aaa", callback: v => "$" + v.toLocaleString() }},
          grid: {{ color: "#2a2a4a" }},
        }},
      }},
      plugins: {{
        legend: {{ labels: {{ color: "#eee" }} }},
        tooltip: {{
          callbacks: {{
            label: ctx => ` ${{ctx.dataset.label}}: $${{ctx.parsed.y.toLocaleString()}}`,
          }},
        }},
        annotation: {{
          annotations: {{
            oosLine: {{
              type: "line",
              xMin: OOS_START, xMax: OOS_START,
              borderColor: "#f39c12",
              borderWidth: 2,
              borderDash: [6, 4],
              label: {{
                display: true, content: "OOS Start",
                color: "#f39c12", backgroundColor: "transparent",
                position: "start",
              }},
            }},
          }},
        }},
      }},
    }},
  }});
}}

// Tabs
const tabBar = document.getElementById("eq-tabs");
const allKeys = datasets.map(d => d.key);

// "All" tab
const allTab = document.createElement("div");
allTab.className = "tab active";
allTab.textContent = "All Strategies";
allTab.onclick = () => {{
  tabBar.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  allTab.classList.add("active");
  buildEquity(allKeys);
}};
tabBar.appendChild(allTab);

datasets.forEach((d, i) => {{
  const tab = document.createElement("div");
  tab.className = "tab";
  tab.textContent = d.name;
  tab.style.borderColor = d.color;
  tab.onclick = () => {{
    tabBar.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    buildEquity([d.key]);
  }};
  tabBar.appendChild(tab);
}});

buildEquity(allKeys);

// ── Monthly P&L ──
new Chart(document.getElementById("monthlyChart").getContext("2d"), {{
  type: "bar",
  data: {{
    labels: {monthly_labels},
    datasets: [{{
      data: {monthly_values},
      backgroundColor: {monthly_colors},
      borderRadius: 3,
    }}],
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: ctx => ` $${{ctx.parsed.y.toLocaleString()}}` }} }},
    }},
    scales: {{
      x: {{ ticks: {{ color: "#aaa", maxRotation: 45 }}, grid: {{ color: "#2a2a4a" }} }},
      y: {{ ticks: {{ color: "#aaa", callback: v => "$" + v.toLocaleString() }},
            grid: {{ color: "#2a2a4a" }} }},
    }},
  }},
}});

// ── DOW breakdown ──
new Chart(document.getElementById("dowChart").getContext("2d"), {{
  type: "bar",
  data: {{
    labels: {dow_labels},
    datasets: [{{
      data: {dow_values},
      backgroundColor: {dow_colors},
      borderRadius: 3,
    }}],
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: ctx => ` avg $${{ctx.parsed.y.toLocaleString()}}` }} }},
    }},
    scales: {{
      x: {{ ticks: {{ color: "#aaa" }}, grid: {{ color: "#2a2a4a" }} }},
      y: {{ ticks: {{ color: "#aaa", callback: v => "$" + v.toLocaleString() }},
            grid: {{ color: "#2a2a4a" }} }},
    }},
  }},
}});
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Loading BTC data...")
    bars    = load_bars()
    funding = load_funding()
    first   = min(b["dt"] for b in bars).date()
    last    = max(b["dt"] for b in bars).date()
    print(f"  {len(bars):,} bars  ·  {first} → {last}")
    print(f"  {len(funding):,} funding entries")

    print("\nRunning all strategies...")
    all_trades = run_all(bars, funding)
    for name, trades in all_trades.items():
        print(f"  {name}: {len(trades)} trades")

    print("\nGenerating dashboard...")
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    html = build_html(all_trades, first, last)
    with open(OUT_PATH, "w") as f:
        f.write(html)

    print(f"\n  Done → {OUT_PATH}")
    print(f"  Open in browser: open {OUT_PATH}")
