"""
web_feed.py — writes posted trades to docs/trades.json for the Form4Wire website.
Called after every successful post to X.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

WEB_FEED_PATH = Path(__file__).parent / "docs" / "trades.json"
MAX_TRADES = 100  # Keep last 100 trades on the site


def _format_value(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"


def _role_header(title: str) -> str:
    """Return short role label for display."""
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


def save_to_web_feed(trade: dict, score: int, cluster_count: int = 0):
    """Append a posted trade to docs/trades.json."""
    try:
        # Load existing
        trades = []
        if WEB_FEED_PATH.exists():
            try:
                with open(WEB_FEED_PATH) as f:
                    trades = json.load(f)
            except Exception:
                trades = []

        # Calculate pct from 52w high
        price = trade.get("price_per_share", 0)
        high = trade.get("stock_52w_high", 0) or trade.get("52w_high", 0)
        pct_from_high = round(((high - price) / high) * 100, 1) if high and high > price else None

        # Calculate position change %
        before = trade.get("shares_owned_before", 0) or 0
        after = trade.get("shares_owned_after", 0) or 0
        pos_change = round(((after - before) / before) * 100, 1) if before > 0 else None

        entry = {
            "ticker": trade.get("ticker", ""),
            "company_name": trade.get("company_name", ""),
            "insider_name": trade.get("insider_name", ""),
            "insider_title": trade.get("insider_title", ""),
            "role_header": _role_header(trade.get("insider_title", "")),
            "transaction_date": trade.get("transaction_date", ""),
            "filed_date": trade.get("filed_date", ""),
            "shares_traded": trade.get("shares_traded", 0),
            "price_per_share": trade.get("price_per_share", 0),
            "total_value": trade.get("total_value", 0),
            "total_value_fmt": _format_value(trade.get("total_value", 0)),
            "shares_owned_after": after,
            "position_change_pct": pos_change,
            "pct_from_52w_high": pct_from_high,
            "signal_score": score,
            "cluster_count": cluster_count,
            "posted_at": datetime.now(timezone.utc).isoformat(),
        }

        trades.append(entry)

        # Keep last MAX_TRADES
        if len(trades) > MAX_TRADES:
            trades = trades[-MAX_TRADES:]

        # Write
        WEB_FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(WEB_FEED_PATH, "w") as f:
            json.dump(trades, f, indent=2)

        log.info(f"  → Web feed updated ({len(trades)} trades)")

    except Exception as e:
        log.warning(f"  → Web feed update failed: {e}")
