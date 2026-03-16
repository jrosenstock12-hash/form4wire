"""
config.py — Central configuration for Form4Wire
"""

# ── POLLING ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 60       # Check SEC every 60 seconds
MIN_SECONDS_BETWEEN_POSTS = 900  # Minimum 15 minutes between tweets (X anti-spam)
MAX_FILING_LAG_BDAYS      = 2    # Skip trades filed more than 2 business days after the trade date

# ── DRY RUN MODE ─────────────────────────────────────────────────────────────
# Set to True until X API is approved — bot runs fully but doesn't post to X
# Saves pending tweets to data/pending_tweets.txt for manual posting
# Set to False once X API credentials are in .env to go fully live
DRY_RUN = False

# Signal score thresholds — minimum score to post
MIN_SCORE_BUY  = 5   # Buys need 5/10+ to post
MIN_SCORE_SELL = 6   # Sells need 6/10+ to post (higher bar — selling is more common)
DIGEST_HOUR_UTC       = 23       # Post daily digest at 11PM UTC (6PM ET)
WEEKLY_DIGEST_DAY     = 4        # Friday (0=Mon, 4=Fri)
FOLLOWUP_DAYS         = [30, 60, 90]  # Days after trade to post performance followup

# ── TIER FILTERING ────────────────────────────────────────────────────────
# Tier 1: CEO, CFO, COO, President, Chairman, GC, CTO, CRO
# Tier 2: EVP, SVP, VP — allowed with higher dollar threshold
# Tier 3: Directors, board members — allowed with highest dollar threshold
POST_TIER1_ONLY = False          # Allow Tier 2+3 with stricter size filters

# Tier multipliers applied ON TOP of the market-cap thresholds below
# Tier 1 (CEO/CFO/COO etc) = 1x base  — highest earners, $100K is routine for them
# Tier 2 (EVP/SVP/VP)      = 0.5x base — meaningful commitment at half the bar
# Tier 3 (Director/other)  = 0.25x base — board members earn less, $25K is real money
# Example: Large cap ($10B+) base = $250K -> Tier 2 = $125K, Tier 3 = $62.5K
TIER_MULTIPLIERS = {1: 1.0, 2: 0.5, 3: 0.25}

# Skip pre-planned 10b5-1 sales — set up 90-120 days in advance, no real signal
SKIP_10B51_SALES = True

# ── DOLLAR THRESHOLDS BY COMPANY SIZE (Tier 1 only) ──────────────────────
# Mega/Large cap (over $10B market cap) — trade must be over $250K
# Mid cap ($2B-$10B) — trade must be over $100K
# Small cap (under $2B) — trade must be over $50K
MEGA_LARGE_CAP_MIN  = 250_000    # $250K minimum for mega/large cap companies
MID_CAP_MIN         = 100_000    # $100K minimum for mid cap companies
SMALL_CAP_MIN       =  50_000    # $50K minimum for small cap companies

# ── TRANSACTION TYPE FILTERING ────────────────────────────────────────────
# Only post intentional trades — not automatic/compensation transactions
ALLOWED_TRANSACTION_CODES = {
    "P",   # Open market purchase — most bullish, 100% intentional
    # "S" removed — insider sells are too noisy (diversification, taxes, etc.)
}

# Transaction codes to always skip — no market signal
SKIP_TRANSACTION_CODES = {
    "F",   # Tax withholding sale — automatic, zero signal
    "A",   # Award/grant — company giving shares, not insider's choice
    "G",   # Gift — no market signal
    "D",   # Disposition to company — no market signal
    "S",   # Open market sale — not tracking sells
    "J",   # Other acquisition — often derivative/synthetic
    "K",   # Equity swap or similar derivative
}

# ── SIGNAL STRENGTH WEIGHTS ──────────────────────────────────────────────
SIGNAL_WEIGHTS = {
    "open_market":        3,   # Not a planned/automatic transaction
    "not_10b51":          2,   # Not a pre-planned 10b5-1 sale
    "tier1_exec":         2,   # CEO/CFO/COO/GC
    "large_trade":        1,   # Over $1M
    "consecutive_buys":   1,   # 3+ buys in a row
    "no_trade_12mo":      1,   # Hadn't traded in 12+ months
}

# ── CLUSTER DETECTION ────────────────────────────────────────────────────
CLUSTER_WINDOW_DAYS   = 10    # Multiple insiders trading same company within 10 days
CLUSTER_MIN_INSIDERS  = 3     # Need at least 3 insiders to trigger cluster alert

# ── EARNINGS PROXIMITY ───────────────────────────────────────────────────
EARNINGS_PROXIMITY_DAYS = 30  # Flag trades within 30 days of earnings

# ── UNUSUAL ACTIVITY ─────────────────────────────────────────────────────
UNUSUAL_INACTIVITY_MONTHS = 12  # Flag if insider hasn't traded in 12+ months

# ── COMPANY SIZE TIERS ───────────────────────────────────────────────────
MEGA_CAP  = 100_000_000_000   # $100B+
LARGE_CAP =  10_000_000_000   # $10B+
MID_CAP   =   2_000_000_000   # $2B+
# Below $2B = small cap

# ── MODELS ───────────────────────────────────────────────────────────────
FAST_MODEL     = "claude-haiku-4-5-20251001"  # Real-time parsing (cheap + fast)
ANALYSIS_MODEL = "claude-sonnet-4-6"          # Weekly digests + deep analysis

# ── FILES ────────────────────────────────────────────────────────────────
# Use Railway volume if mounted, otherwise fall back to local data/
import os as _os
_DATA_DIR = "/app/data" if _os.path.exists("/app/data") else "data"

SEEN_FILINGS_FILE    = f"{_DATA_DIR}/seen_filings.json"
TRADE_HISTORY_FILE   = f"{_DATA_DIR}/trade_history.json"
FOLLOWUP_QUEUE_FILE  = f"{_DATA_DIR}/followup_queue.json"
CLUSTER_TRACKER_FILE = f"{_DATA_DIR}/cluster_tracker.json"
LOG_FILE             = "logs/bot.log"
