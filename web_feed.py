"""
web_feed.py — writes posted trades to docs/trades.json and pushes to GitHub.
Called after every successful post to X.
"""

import json
import logging
import os
import base64
import requests
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

WEB_FEED_PATH = Path(__file__).parent / "docs" / "trades.json"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "jrosenstock12-hash/form4wire"
GITHUB_FILE  = "docs/trades.json"
GITHUB_API   = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"


def _format_value(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"


def _role_header(title: str) -> str:
    if not title:
        return "INSIDER"
    t = title.upper()
    if any(x in t for x in ["CHIEF EXEC", "CEO"]):
        return "CEO"
    if any(x in t for x in ["CHAIRMAN", "CHAIR"]):
        return "CHAIRMAN"
    if any(x in t for x in ["FOUNDER"]):
        return "FOUNDER"
    if any(x in t for x in ["PRESIDENT"]) and "VICE" not in t:
        return "PRESIDENT"
    if any(x in t for x in ["CHIEF FINANCIAL", "CFO"]):
        return "CFO"
    if any(x in t for x in ["CHIEF OPERATING", "COO"]):
        return "COO"
    if any(x in t for x in ["CHIEF TECH", "CTO"]):
        return "CTO"
    if any(x in t for x in ["GENERAL COUNSEL"]):
        return "GEN. COUNSEL"
    if "CHIEF" in t:
        return "C-SUITE"
    if any(x in t for x in ["EVP", "EXEC. VP", "EXECUTIVE VP"]):
        return "EVP"
    if any(x in t for x in ["SVP", "SENIOR VP", "SENIOR VICE"]):
        return "SVP"
    if any(x in t for x in ["VP", "VICE PRES"]):
        return "VP"
    if "DIRECTOR" in t:
        return "DIRECTOR"
    if "TREASURER" in t:
        return "TREASURER"
    if "BOARD" in t:
        return "BOARD"
    return "INSIDER"


GITHUB_INDEX_FILE = "docs/index.html"
GITHUB_INDEX_API  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_INDEX_FILE}"
INDEX_PATH        = Path(__file__).parent / "docs" / "index.html"


def _build_seo_block(trades: list) -> str:
    """Build a plain-text SEO div with all trade data for search engine crawling."""
    lines = []
    for t in reversed(trades):  # newest first
        name    = t.get("insider_name", "")
        ticker  = t.get("ticker", "")
        company = t.get("company_name", ticker)
        title   = t.get("insider_title", "")
        role    = t.get("role_header", "Insider")
        value   = t.get("total_value_fmt", "")
        shares  = t.get("shares_traded", 0)
        price   = t.get("price_per_share", 0)
        tx_date = t.get("transaction_date", "")
        score   = t.get("signal_score", 0)
        lines.append(
            f"{name} ({role}, {title}) bought {value} of {company} (${ticker}) — "
            f"{shares:,} shares at ${price:.2f} on {tx_date}. Signal score: {score}/10. "
            f"SEC Form 4 insider trading alert."
        )
    block = "\n".join(f"<p>{line}</p>" for line in lines)
    return (
        f'<div id="seo-content" style="position:absolute;left:-9999px;width:1px;'
        f'height:1px;overflow:hidden;" aria-hidden="true">\n'
        f'<h2>Recent SEC Form 4 Insider Trading Alerts</h2>\n'
        f'{block}\n'
        f'</div>'
    )


def _push_seo_index_to_github(trades: list):
    """Regenerate the SEO div in index.html and push to GitHub."""
    if not GITHUB_TOKEN:
        return
    try:
        # Read current index.html from local file
        if not INDEX_PATH.exists():
            log.warning("  → SEO: index.html not found locally, skipping")
            return

        html = INDEX_PATH.read_text(encoding="utf-8")

        # Replace the SEO div content
        import re
        new_seo = _build_seo_block(trades)
        # Replace from <div id="seo-content" to the closing </div>
        html = re.sub(
            r'<div id="seo-content"[^>]*>.*?</div>',
            new_seo,
            html,
            flags=re.DOTALL
        )

        # Write locally
        INDEX_PATH.write_text(html, encoding="utf-8")

        # Push to GitHub
        encoded = base64.b64encode(html.encode("utf-8")).decode()
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        r = requests.get(GITHUB_INDEX_API, headers=headers, timeout=10)
        sha = r.json().get("sha", "") if r.status_code == 200 else ""

        payload = {
            "message": "Update SEO index",
            "content": encoded,
            "branch": "main",
        }
        if sha:
            payload["sha"] = sha

        r = requests.put(GITHUB_INDEX_API, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            log.info(f"  → SEO index pushed to GitHub ({len(trades)} trades indexed)")
        else:
            log.warning(f"  → SEO index push failed: {r.status_code} {r.text[:100]}")

    except Exception as e:
        log.warning(f"  → SEO index push error: {e}")



    """Push trades.json to GitHub so GitHub Pages serves the latest data."""
    if not GITHUB_TOKEN:
        log.warning("  → Web feed: GITHUB_TOKEN not set, skipping GitHub push")
        return

    try:
        content = json.dumps(trades, indent=2)
        encoded = base64.b64encode(content.encode()).decode()

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }

        # Get current file SHA (needed for update)
        r = requests.get(GITHUB_API, headers=headers, timeout=10)
        sha = r.json().get("sha", "") if r.status_code == 200 else ""

        payload = {
            "message": "Update trades feed",
            "content": encoded,
            "branch": "main",
        }
        if sha:
            payload["sha"] = sha

        r = requests.put(GITHUB_API, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            log.info(f"  → Web feed pushed to GitHub ({len(trades)} trades)")
        else:
            log.warning(f"  → Web feed GitHub push failed: {r.status_code} {r.text[:100]}")

    except Exception as e:
        log.warning(f"  → Web feed GitHub push error: {e}")


def save_to_web_feed(trade: dict, score: int, cluster_count: int = 0):
    """Append a posted trade to docs/trades.json and push to GitHub."""
    try:
        # Load existing from local file
        trades = []
        if WEB_FEED_PATH.exists():
            try:
                with open(WEB_FEED_PATH) as f:
                    trades = json.load(f)
            except Exception:
                trades = []

        # Calculate pct from 52w high
        price = trade.get("price_per_share", 0)
        high  = trade.get("stock_52w_high", 0) or trade.get("52w_high", 0)
        pct_from_high = round(((high - price) / high) * 100, 1) if high and high > price else None

        # Calculate position change %
        before = trade.get("shares_owned_before", 0) or 0
        after  = trade.get("shares_owned_after", 0) or 0
        pos_change = round(((after - before) / before) * 100, 1) if before > 0 else None

        entry = {
            "ticker":            trade.get("ticker", ""),
            "company_name":      trade.get("company_name", ""),
            "insider_name":      trade.get("insider_name", ""),
            "insider_title":     trade.get("insider_title", ""),
            "role_header":       _role_header(trade.get("insider_title", "")),
            "transaction_date":  trade.get("transaction_date", ""),
            "filed_date":        trade.get("filed_date", ""),
            "shares_traded":     trade.get("shares_traded", 0),
            "price_per_share":   trade.get("price_per_share", 0),
            "total_value":       trade.get("total_value", 0),
            "total_value_fmt":   _format_value(trade.get("total_value", 0)),
            "shares_owned_after": after,
            "position_change_pct": pos_change,
            "pct_from_52w_high": pct_from_high,
            "signal_score":      score,
            "cluster_count":     cluster_count,
            "unusual_flag":      trade.get("unusual_flag", False),
            "unusual_flag":      trade.get("unusual_flag", False),
            "posted_at":         datetime.now(timezone.utc).isoformat(),
        }

        trades.append(entry)

        # Write locally
        WEB_FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(WEB_FEED_PATH, "w") as f:
            json.dump(trades, f, indent=2)

        log.info(f"  → Web feed updated locally ({len(trades)} trades)")

        # Push trades.json to GitHub so website updates
        _push_to_github(trades)

        # Regenerate SEO index in index.html and push
        _push_seo_index_to_github(trades)

    except Exception as e:
        log.warning(f"  → Web feed update failed: {e}")
