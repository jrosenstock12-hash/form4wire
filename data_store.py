"""
data_store.py — Persistent storage for filings, history, followups, clusters
"""

import json
import os
from datetime import datetime, timezone, timedelta
from config import (
    SEEN_FILINGS_FILE, TRADE_HISTORY_FILE,
    FOLLOWUP_QUEUE_FILE, CLUSTER_TRACKER_FILE,
    CLUSTER_WINDOW_DAYS, CLUSTER_MIN_INSIDERS, FOLLOWUP_DAYS,
)


def _load(path: str):  # -> Union[dict, list]
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    # Return appropriate default
    return [] if "seen" in path else {}


def _save(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── SEEN FILINGS ─────────────────────────────────────────────────────────────

def load_seen() -> set:
    data = _load(SEEN_FILINGS_FILE)
    return set(data) if isinstance(data, list) else set()


def save_seen(seen: set):
    _save(SEEN_FILINGS_FILE, list(seen))


# ── TRADE HISTORY ─────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Normalize insider name to Title Case for consistent history key matching."""
    return " ".join(w.capitalize() for w in name.strip().split())


def load_history() -> dict:
    return _load(TRADE_HISTORY_FILE)


def save_trade(trade: dict):
    history = load_history()
    ticker  = trade.get("ticker", "UNKNOWN")
    insider = normalize_name(trade.get("insider_name", "Unknown"))
    key     = f"{ticker}:{insider}"

    if key not in history:
        history[key] = []

    history[key].append({
        "date":            trade.get("transaction_date", ""),
        "code":            trade.get("transaction_code", ""),
        "is_buy":          trade.get("transaction_code", "") in ("P", "M"),
        "total_value":     trade.get("total_value", 0),
        "price_per_share": trade.get("price_per_share", 0),
        "shares":          trade.get("shares_traded", 0),
        "saved_at":        datetime.now(timezone.utc).isoformat(),
    })

    # Keep only last 50 trades per insider
    history[key] = history[key][-50:]
    _save(TRADE_HISTORY_FILE, history)


def get_insider_history(ticker: str, insider_name: str) -> dict:
    """Return history and derived signals for an insider."""
    history = load_history()
    key     = f"{ticker}:{normalize_name(insider_name)}"
    trades  = history.get(key, [])

    if not trades:
        return {"trades": [], "consecutive_buys": 0, "months_since_last": 999, "unusual": True}

    # Sort by date
    trades_sorted = sorted(trades, key=lambda x: x.get("date", ""), reverse=True)

    # Months since last trade
    last_date_str = trades_sorted[0].get("date", "")
    months_since  = 999
    days_since = 9999
    if last_date_str:
        try:
            last_dt = datetime.fromisoformat(last_date_str.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            days_since   = (datetime.now(timezone.utc) - last_dt).days
            months_since = days_since // 30
        except Exception:
            pass

    # Consecutive buys streak — only count if each buy is within 180 days of the previous
    consecutive = 0
    prev_date_str = trades_sorted[0].get("date", "") if trades_sorted else ""
    for t in trades_sorted[1:]:   # Skip current trade (not yet saved)
        if not t.get("is_buy"):
            break
        try:
            prev_dt = datetime.fromisoformat(prev_date_str.replace("Z", "+00:00"))
            curr_dt = datetime.fromisoformat(t["date"].replace("Z", "+00:00"))
            if prev_dt.tzinfo is None:
                prev_dt = prev_dt.replace(tzinfo=timezone.utc)
            if curr_dt.tzinfo is None:
                curr_dt = curr_dt.replace(tzinfo=timezone.utc)
            gap_days = (prev_dt - curr_dt).days
            if gap_days > 180:
                break
            consecutive += 1
            prev_date_str = t["date"]
        except Exception:
            break

    unusual = days_since >= 365

    return {
        "trades":           trades_sorted[:5],
        "consecutive_buys": consecutive,
        "months_since_last": months_since,
        "unusual":          unusual,
    }


# ── FOLLOWUP QUEUE ────────────────────────────────────────────────────────────

def load_followup_queue() -> list:
    data = _load(FOLLOWUP_QUEUE_FILE)
    return data if isinstance(data, list) else []


def add_to_followup_queue(trade: dict, tweet_id: str = ""):
    """Schedule 30/60/90-day followup posts for a trade."""
    queue = load_followup_queue()
    now   = datetime.now(timezone.utc)

    for days in FOLLOWUP_DAYS:
        followup_date = (now + timedelta(days=days)).isoformat()
        queue.append({
            "due_date":              followup_date,
            "days":                  days,
            "posted":                False,
            "prior_followup_posted": False,
            "original_tweet_id":     tweet_id,
            "trade": {
                "ticker":           trade.get("ticker"),
                "insider_name":     trade.get("insider_name"),
                "insider_title":    trade.get("insider_title"),
                "transaction_date": trade.get("transaction_date"),
                "price_per_share":  trade.get("price_per_share"),
                "transaction_code": trade.get("transaction_code"),
                "is_buy":           trade.get("transaction_code", "") in ("P", "M"),
                "total_value":      trade.get("total_value"),
            }
        })

    _save(FOLLOWUP_QUEUE_FILE, queue)


def get_due_followups() -> list:
    """Return followups that are due today and not yet posted."""
    queue = load_followup_queue()
    now   = datetime.now(timezone.utc)
    due   = []

    for item in queue:
        if item.get("posted"):
            continue
        try:
            due_dt = datetime.fromisoformat(item["due_date"])
            if due_dt <= now:
                due.append(item)
        except Exception:
            pass

    return due


def mark_followup_posted(followup: dict):
    queue = load_followup_queue()
    for item in queue:
        if (item["trade"].get("ticker") == followup["trade"].get("ticker") and
                item["days"] == followup["days"] and
                item["trade"].get("transaction_date") == followup["trade"].get("transaction_date")):
            item["posted"] = True
    _save(FOLLOWUP_QUEUE_FILE, queue)


def mark_all_followups_done(followup: dict):
    """Mark all intervals for this trade as prior_followup_posted so only one fires."""
    queue = load_followup_queue()
    for item in queue:
        if (item["trade"].get("ticker") == followup["trade"].get("ticker") and
                item["trade"].get("transaction_date") == followup["trade"].get("transaction_date")):
            item["prior_followup_posted"] = True
            item["posted"] = True
    _save(FOLLOWUP_QUEUE_FILE, queue)


# ── CLUSTER TRACKER ───────────────────────────────────────────────────────────

def load_clusters() -> dict:
    return _load(CLUSTER_TRACKER_FILE)


def record_trade_for_cluster(trade: dict):  # -> Optional[dict]
    """
    Add trade to cluster tracker. Returns cluster data if threshold met, else None.
    """
    clusters = load_clusters()
    ticker   = trade.get("ticker", "UNKNOWN")
    company  = trade.get("company_name", ticker)
    now      = datetime.now(timezone.utc)
    cutoff   = (now - timedelta(days=CLUSTER_WINDOW_DAYS)).isoformat()

    if ticker not in clusters:
        clusters[ticker] = {"company": company, "trades": []}

    # Add this trade
    clusters[ticker]["trades"].append({
        "insider": trade.get("insider_name", ""),
        "title":   trade.get("insider_title", ""),
        "code":    trade.get("transaction_code", ""),
        "value":   trade.get("total_value", 0),
        "date":    trade.get("transaction_date", ""),
        "saved_at": now.isoformat(),
    })

    # Prune old entries outside window
    clusters[ticker]["trades"] = [
        t for t in clusters[ticker]["trades"]
        if t.get("saved_at", "") >= cutoff
    ]

    _save(CLUSTER_TRACKER_FILE, clusters)

    # Check if cluster threshold met
    recent = clusters[ticker]["trades"]
    unique_insiders = len(set(t["insider"] for t in recent))

    if unique_insiders >= CLUSTER_MIN_INSIDERS:
        # Check if we already alerted on this cluster recently (within 24h)
        last_alert = clusters[ticker].get("last_cluster_alert", "")
        if last_alert:
            try:
                last_dt = datetime.fromisoformat(last_alert)
                if (now - last_dt).total_seconds() < 86400:
                    return None  # Already alerted today
            except Exception:
                pass

        clusters[ticker]["last_cluster_alert"] = now.isoformat()
        _save(CLUSTER_TRACKER_FILE, clusters)

        return {
            "ticker":  ticker,
            "company": company,
            "trades":  recent,
            "count":   unique_insiders,
        }

    return None


# ── DAILY TRADE LOG (for digest) ──────────────────────────────────────────────

def log_daily_trade(trade: dict, signal_score: int):
    """Log summarized trade for daily/weekly digest."""
    history  = load_history()
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    digest_key = f"__digest_{date_key}"
    if digest_key not in history:
        history[digest_key] = []

    history[digest_key].append({
        "ticker":    trade.get("ticker"),
        "name":      trade.get("insider_name"),
        "title":     trade.get("insider_title"),
        "code":      trade.get("transaction_code"),
        "value":     trade.get("total_value", 0),
        "score":     signal_score,
        "is_buy":    trade.get("transaction_code", "") in ("P", "M"),
        "logged_at": datetime.now(timezone.utc).isoformat(),
    })

    _save(TRADE_HISTORY_FILE, history)


def get_today_trades() -> list:
    history  = load_history()
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return history.get(f"__digest_{date_key}", [])


def get_last_24h_trades() -> list:
    """Return all significant trades logged in the past 24 hours."""
    history = load_history()
    now     = datetime.now(timezone.utc)
    trades  = []
    # Check today and yesterday to cover the full 24h window
    for i in range(2):
        day_key = (now - timedelta(hours=i*24)).strftime("%Y-%m-%d")
        for trade in history.get(f"__digest_{day_key}", []):
            logged_at = trade.get("logged_at")
            if logged_at:
                try:
                    logged_dt = datetime.fromisoformat(logged_at)
                    if (now - logged_dt).total_seconds() <= 86400:
                        trades.append(trade)
                except Exception:
                    pass
            else:
                # No timestamp — just include today's
                if i == 0:
                    trades.append(trade)
    return trades


def increment_daily_scan(count: int = 1):
    """Track how many filings were scanned today for digest accuracy."""
    history  = load_history()
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scan_key = f"__scan_{date_key}"
    history[scan_key] = history.get(scan_key, 0) + count
    _save(TRADE_HISTORY_FILE, history)


def get_daily_scan_count() -> int:
    """Return total filings scanned today."""
    history  = load_history()
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return history.get(f"__scan_{date_key}", 0)


def get_week_trades() -> list:
    history = load_history()
    now     = datetime.now(timezone.utc)
    trades  = []

    for i in range(7):
        day_key = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        trades.extend(history.get(f"__digest_{day_key}", []))

    return trades
