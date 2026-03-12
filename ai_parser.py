"""
ai_parser.py — Claude-powered parsing, signal scoring, and tweet generation
"""

import os
import re
import json
import anthropic
from config import FAST_MODEL, ANALYSIS_MODEL

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# ─────────────────────────────────────────────────────────────────────────────
# TIER CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

TIER1_TITLES = [
    "chief executive", "ceo", "chief financial", "cfo",
    "chief operating", "coo", "chairman",
    "general counsel", "chief legal", "clo",
    "chief technology", "cto", "chief revenue", "cro",
]
TIER2_TITLES = [
    "executive vice president", "evp", "senior vice president", "svp",
    "vice president", "vp", "chief accounting", "treasurer",
    "chief marketing", "cmo", "chief people", "chief hr",
]

# President titles — must check AFTER vice president to avoid false matches
TIER1_PRESIDENT = ["president"]
TIER2_VP = ["vice president"]


def classify_insider_tier(title: str) -> int:
    t = title.lower()
    # Check Tier 2 VP titles FIRST to avoid "vice president" matching "president"
    if any(k in t for k in TIER2_TITLES):
        return 2
    # Now safe to check Tier 1 (president won't match vice president)
    if any(k in t for k in TIER1_TITLES):
        return 1
    if any(k in t for k in TIER1_PRESIDENT):
        return 1
    return 3  # Director, board member, 10% owner, other


def tier_emoji(tier: int) -> str:
    return "💼"  # Always consistent role emoji


def cap_label(market_cap: float) -> str:
    if market_cap >= 100_000_000_000:
        return "🏦 MEGA CAP"
    if market_cap >= 10_000_000_000:
        return "🏢 LARGE CAP"
    if market_cap >= 2_000_000_000:
        return "🏗️ MID CAP"
    return "🔬 SMALL CAP"


def format_value(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"


def pct_from_52w(price, low, high) -> str:
    if not high or not low or high == low:
        return ""
    pct = (price - low) / (high - low) * 100
    return f"{pct:.0f}% above 52w low"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PARSE — extracts structured data from Form 4 XML/HTML
# ─────────────────────────────────────────────────────────────────────────────

PARSE_PROMPT = """
You are a financial data parser specializing in SEC Form 4 insider trading filings.

Filing title: {title}
Company info: {company}
Filing content:
{content}

Extract ALL of the following fields. Return ONLY valid JSON, no markdown, no explanation.

{{
  "insider_name": "",
  "insider_title": "",
  "company_name": "",
  "ticker": "",
  "transaction_date": "",
  "transaction_type": "",       // Full description: "Open Market Purchase", "Open Market Sale", "Option Exercise", "Gift", "Tax Withholding Sale", "Award/Grant", "Disposition to Company"
  "transaction_code": "",       // Single letter: P, S, A, D, F, M, G, etc.
  "is_10b51_plan": false,       // true if filing explicitly mentions 10b5-1 plan
  "shares_traded": 0,
  "price_per_share": 0.0,
  "total_value": 0.0,
  "shares_owned_after": 0,
  "shares_owned_before": 0,
  "is_derivative": false,       // true if options/warrants/convertible
  "derivative_type": "",        // "Stock Option", "RSU", "Warrant", etc. if applicable
  "exercise_price": 0.0,        // for options
  "expiration_date": "",        // for options
  "held_after_exercise": false  // for options: did they keep shares or immediately sell?
}}

Rules:
- If the filing contains MULTIPLE transaction rows with the same transaction_code (e.g. two P rows), aggregate them into ONE:
  * shares_traded = sum of all rows
  * total_value = sum of all rows (or sum of shares × price per row)
  * price_per_share = total_value / shares_traded (weighted average)
  * transaction_date = date of the FIRST transaction
  * shares_owned_after = the FINAL "amount owned after" from the last row
  * shares_owned_before = shares_owned_after - shares_traded (for buys) or shares_owned_after + shares_traded (for sells)
- If insider_title says "See Remarks", look in the Remarks section at the bottom of the filing for the actual title
- If total_value is not stated, calculate: shares_traded × price_per_share
- If shares_owned_before is missing, estimate: shares_owned_after - shares_traded (for buys) or shares_owned_after + shares_traded (for sells)
- If ANY critical field (insider_name, shares_traded, transaction_code) is missing, set ticker to "SKIP"
- For transaction_type, use the full human-readable description, not the code letter
"""


def parse_filing(title: str, content: str, company: dict, xml_content: str = ""):  # -> Optional[dict]
    """Use Claude Haiku to extract structured data from a Form 4 filing."""
    prompt = PARSE_PROMPT.format(
        title=title,
        company=json.dumps(company),
        content=content[:10000],
    )
    try:
        msg = claude.messages.create(
            model=FAST_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()

        # Strip markdown fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()

        # If Claude returned a list, take the first item
        if raw.startswith("["):
            raw = json.loads(raw)
            raw = raw[0] if raw else {}
            if isinstance(raw, dict):
                data = raw
            else:
                return None
        else:
            # Fix common JSON issues — truncated strings
            # Try to parse as-is first
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Try to extract just the JSON object
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group())
                    except Exception:
                        return None
                else:
                    return None

        if not isinstance(data, dict):
            return None
        if data.get("ticker") == "SKIP":
            return None
        if not data.get("ticker") and not data.get("insider_name"):
            return None
        return data

    except Exception as e:
        print(f"[AI] Parse error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL SCORING
# ─────────────────────────────────────────────────────────────────────────────

SCORE_ADJUST_PROMPT = """
You are a financial analyst reviewing an insider trade for Form4Wire.

A rules-based system has already calculated a base score of {base_score}/10 using hard facts.
Your job is to adjust this by -1, 0, or +1 based on context the formula cannot capture.

Trade data: {trade_data}
Stock data: {stock_data}
Base score breakdown: {breakdown}

ADJUST +1 if you see any of:
- Trade is within 30 days of earnings — insider has most current info
- Insider is buying while stock is near 52-week low — strong contrarian conviction
- Insider is selling 50%+ of their entire personal holdings — major bearish signal
- Context strongly suggests this is a highly unusual or notable trade

ADJUST -1 if you see any of:
- Trade is tiny relative to insider's total wealth/holdings (under 2% of their stake)
- Sell but insider retains over 90% of holdings — routine diversification
- Any other context that makes this trade less significant than the formula suggests

ADJUST 0 if no strong reason to move it either way.

Return ONLY a JSON object with no other text:
{{
  "adjustment": 0,
  "final_score": {base_score}
}}
"""


def calculate_base_score(trade: dict, stock: dict, history: dict) -> tuple[int, dict]:
    """
    Deterministic rules-based base score. Consistent and explainable.

    SCORING PHILOSOPHY:
    Any trade that passes all filters is already noteworthy — it's a Tier 1/2/3
    insider making a real open-market decision with their own money. Base floor
    reflects that. Bonus points reward extra conviction signals.

    BASE FLOOR:
    +4  Open market BUY  — intentional, directional, personal money
    +2  Open market SELL — less signal (many reasons to sell), but still notable

    BONUS POINTS (stacked on top of floor):
    +2  Trade > 0.1% of company market cap   (highly significant relative size)
    +1  Trade > 0.01% of market cap          (moderate relative size)
    +1  First trade in 12+ months            (unusual — broke a long silence)
    +1  Consecutive buys 2+ in a row         (conviction pattern)
    +1  High short interest > 15% on a buy   (contrarian bet)

    Claude then adjusts -1, 0, or +1 for context it can see that rules cannot.
    Final score is clamped 1-10.

    TYPICAL SCORES:
    Clean CEO open-market buy, normal size  → 4 base + 0-1 bonus + Claude = 5-6
    CEO buy > 0.1% market cap              → 4 + 2 + Claude = 7-8
    CEO buy, 12mo silence, high conviction  → 4 + 1 + 1 + Claude = 7-8
    Routine insider sell, no signals        → 2 base + Claude adj = 2-3 (filtered)
    """
    points     = 0
    breakdown  = {}
    code       = trade.get("transaction_code", "")
    total      = trade.get("total_value", 0)
    market_cap = stock.get("market_cap", 0)
    unusual    = history.get("unusual", False)
    consec     = history.get("consecutive_buys", 0)
    short_int  = stock.get("short_interest", 0)

    # Base floor by direction
    if code == "P":
        points += 4
        breakdown["direction"] = "+4 (open market buy — personal money, directional)"
    else:
        points += 2
        breakdown["direction"] = "+2 (open market sell)"

    # Bonus: size relative to market cap
    if market_cap and total:
        ratio_pct = (total / market_cap) * 100
        if ratio_pct >= 0.1:
            points += 2
            breakdown["relative_size"] = f"+2 ({ratio_pct:.3f}% of market cap — highly significant)"
        elif ratio_pct >= 0.01:
            points += 1
            breakdown["relative_size"] = f"+1 ({ratio_pct:.3f}% of market cap — moderate)"
        else:
            breakdown["relative_size"] = f"+0 ({ratio_pct:.4f}% of market cap — small relative to company)"
    else:
        breakdown["relative_size"] = "+0 (market cap unavailable)"

    # Bonus: first trade in 12+ months
    if unusual:
        points += 1
        breakdown["unusual"] = "+1 (no trades in 12+ months — broke silence)"
    else:
        breakdown["unusual"] = "+0"

    # Bonus: consecutive buys
    if consec >= 2:
        points += 1
        breakdown["consecutive"] = f"+1 ({consec} consecutive buys — conviction pattern)"
    else:
        breakdown["consecutive"] = "+0"

    # Bonus: high short interest contrarian buy
    if short_int and short_int >= 15 and code == "P":
        points += 1
        breakdown["short_interest"] = f"+1 (high short interest {short_int:.1f}% — contrarian buy)"
    else:
        breakdown["short_interest"] = "+0"

    return points, breakdown


def score_signal(trade: dict, stock: dict, history: dict) -> tuple[int, str]:
    # Step 1 — deterministic base score
    base_score, breakdown = calculate_base_score(trade, stock, history)

    # Step 2 — Claude adjusts by -1, 0, or +1 (no reasoning from Claude)
    prompt = SCORE_ADJUST_PROMPT.format(
        base_score=base_score,
        trade_data=json.dumps(trade),
        stock_data=json.dumps(stock),
        breakdown=json.dumps(breakdown),
    )
    try:
        msg = claude.messages.create(
            model=FAST_MODEL,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        raw  = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        data = json.loads(raw)
        adjustment  = max(-1, min(1, int(data.get("adjustment", 0))))
        final_score = max(1, min(10, base_score + adjustment))
    except Exception:
        final_score = max(1, min(10, base_score))

    # Build reasoning from verified data only
    reasoning = _build_reasoning(trade, stock, final_score, breakdown, history)
    return final_score, reasoning


def _build_reasoning(trade: dict, stock: dict, score: int, breakdown: dict = None, history: dict = None) -> str:
    """Build a short signal phrase — prioritise what actually scored bonus points."""
    breakdown = breakdown or {}
    history   = history or {}

    tx_type = trade.get("transaction_type", "")
    code    = trade.get("transaction_code", "")
    is_buy  = code == "P" or "purchase" in tx_type.lower()
    action  = "buying" if is_buy else "selling"
    short_title = shorten_title(trade.get("insider_title", "Insider"))

    high  = stock.get("52w_high", 0)
    price = stock.get("price", 0)
    total = trade.get("total_value", 0)

    before = trade.get("shares_owned_before", 0)
    shares = trade.get("shares_traded", 0)
    after  = trade.get("shares_owned_after", 0)
    if before == 0 and after > 0 and shares > 0:
        before = after - shares if is_buy else after + shares

    parts = []

    # 1. HISTORY SIGNALS — these are invisible in the tweet bullets above, so show them first
    months_since  = history.get("months_since_last", 999)
    consec_buys   = history.get("consecutive_buys", 0)
    short_int     = stock.get("short_interest", 0)

    if breakdown.get("unusual", "").startswith("+1") and months_since < 999:
        parts.append(f"first buy in {months_since}+ months")

    if breakdown.get("consecutive", "").startswith("+1") and consec_buys >= 2:
        parts.append(f"{consec_buys + 1} consecutive buys")

    if breakdown.get("short_interest", "").startswith("+1") and short_int > 0:
        parts.append(f"{short_int:.0f}% short interest — buying against the crowd")

    # 2. POSITION % — unique context not shown elsewhere if no history signal fired
    if not parts and before > 0 and shares > 0:
        pct = (shares / before) * 100
        if pct >= 10:
            direction_word = "+" if is_buy else "-"
            parts.append(f"position {direction_word}{pct:.0f}%")

    # 3. 52W context — secondary color
    if high and price and is_buy:
        pct_from_high = ((price - high) / high) * 100
        if pct_from_high <= -10:
            if not parts:
                parts.append(f"stock down {abs(pct_from_high):.0f}% from 52W high")
            elif len(", ".join(parts)) < 35:
                parts.append("near 52W low")

    # 4. Fallbacks
    if not parts and total:
        parts.append(f"{format_value(total)} {action}")
    if not parts:
        parts.append(f"{short_title} {action}")

    return ", ".join(parts)[:60]


def score_emoji(score: int) -> str:
    return "💡"  # Always consistent


# ─────────────────────────────────────────────────────────────────────────────
# TWEET FORMATTER
# ─────────────────────────────────────────────────────────────────────────────

# Junk values Claude sometimes returns for insider_title
_JUNK_TITLES = {
    "see remarks", "see attached", "n/a", "none", "other", "unknown",
    "not applicable", "see exhibit", "officer", "see form",
}

def shorten_title(title: str) -> str:
    """Convert long insider titles to short readable labels."""
    t = title.lower().strip()
    if not t or t in _JUNK_TITLES:
        return "Insider"
    if "chief executive" in t or t == "ceo":
        return "CEO"
    if "chief financial" in t or t == "cfo":
        return "CFO"
    if "chief operating" in t or t == "coo":
        return "COO"
    if "chief technology" in t or t == "cto":
        return "CTO"
    if "chief legal" in t or "general counsel" in t or t == "clo":
        return "CLO"
    if "chief accounting" in t or t == "cao":
        return "CAO"
    if "chief marketing" in t or t == "cmo":
        return "CMO"
    if "chief people" in t or "chief hr" in t or "chief human" in t:
        return "CPO"
    if "chief revenue" in t or t == "cro":
        return "CRO"
    if "chief administrative" in t:
        return "CAO"
    if "chairman" in t:
        return "Chairman"
    if "president" in t and "vice" not in t:
        return "President"
    if "executive vice president" in t or t == "evp":
        return "EVP"
    if "senior vice president" in t or t == "svp":
        return "SVP"
    if "vice president" in t or t == "vp":
        return "VP"
    if "10%" in t or "10 percent" in t or "beneficial owner" in t:
        return "10% Owner"
    if "director" in t:
        return "Director"
    # Fallback: truncate if too long
    if len(title) > 20:
        return title[:20].strip()
    return title


def build_tweet(
    trade: dict,
    stock: dict,
    tier: int,
    signal_score: int,
    signal_reason: str,
    short_interest: float,
    next_earnings: str,
    unusual_flag: bool,
    consecutive_buys: int,
    analyst_divergence: str,
    cluster_flag: bool,
) -> str:
    """Assemble the final tweet from all data sources."""

    ticker     = trade.get("ticker", "???")
    raw_name   = trade.get("insider_name", "Unknown")
    name       = " ".join(w.capitalize() for w in raw_name.split())
    title      = trade.get("insider_title", "Insider")
    tx_type    = trade.get("transaction_type", "")
    code       = trade.get("transaction_code", "")
    shares     = int(trade.get("shares_traded", 0))
    price      = trade.get("price_per_share", 0)
    total      = trade.get("total_value", 0)
    after      = int(trade.get("shares_owned_after", 0))
    before     = int(trade.get("shares_owned_before", 0))
    tx_date    = trade.get("transaction_date", "")
    filed_date = trade.get("filed_date", "")

    # Direction
    is_buy = code in ("P", "M") or "purchase" in tx_type.lower() or "exercise" in tx_type.lower()
    direction_emoji = "🟢" if is_buy else "🔴"
    direction_label = "BUY" if is_buy else "SELL"

    # Shortened title for first line
    short_title = shorten_title(title)

    # Format dates as "Mar 9"
    def fmt_date_short(d):
        try:
            from datetime import datetime
            return datetime.strptime(d, "%Y-%m-%d").strftime("%b %-d")
        except Exception:
            return d

    tx_date_fmt    = fmt_date_short(tx_date)
    tx_date_end    = trade.get("transaction_date_end", "")
    tx_date_end_fmt = fmt_date_short(tx_date_end) if tx_date_end else ""
    filed_date_fmt = fmt_date_short(filed_date) if filed_date else tx_date_fmt
    # Show date range if multi-day trade (e.g. "Mar 10-11")
    if tx_date_end_fmt and tx_date_end_fmt != tx_date_fmt:
        date_str = f"Trade: {tx_date_fmt}–{tx_date_end_fmt} | Filed: {filed_date_fmt}"
    else:
        date_str = f"Trade: {tx_date_fmt} | Filed: {filed_date_fmt}"

    # Position change — only show if we have both before and after
    position_str = ""
    if before > 0 and after > 0 and shares > 0:
        pct = (shares / before) * 100
        direction_word = "+" if is_buy else "-"
        if round(pct) > 0:
            position_str = f"• Position {direction_word}{pct:.0f}% | Now owns {after:,} shares\n"

    # Market cap
    market_cap = stock.get("market_cap", 0)
    cap_str = f"• Market Cap: {format_value(market_cap)}\n" if market_cap else ""

    # % from 52W high
    week52_str = ""
    high = stock.get("52w_high", 0)
    current_price = stock.get("price", 0)
    if high and current_price:
        pct_from_high = ((current_price - high) / high) * 100
        if pct_from_high >= 0:
            week52_str = f"• 📈 Stock +{pct_from_high:.0f}% from 52W high\n"
        else:
            week52_str = f"• 📉 Stock {pct_from_high:.0f}% from 52W high\n"

    # Signal reasoning — always include, truncate at word boundary
    reason = signal_reason.strip() if signal_reason else "Notable insider activity"
    if len(reason) > 60:
        reason = reason[:60].rsplit(" ", 1)[0]
    score_str = f"💡 Signal: {signal_score}/10 — {reason}"

    # Build tweet
    tweet = (
        f"{direction_emoji} {short_title} {direction_label} — ${ticker}\n"
        f"\n"
        f"{name} buys {format_value(total)}\n"
        f"\n"
        f"• {shares:,} shares @ ${price:.2f}\n"
        f"{position_str}"
        f"{cap_str}"
        f"{week52_str}"
        f"• {date_str}\n"
        f"\n"
        f"{score_str}\n"
        f"\n"
        f"#InsiderTrading #{ticker}"
    )

    # If over 280, trim position and cap lines
    if len(tweet) > 280:
        tweet = (
            f"{direction_emoji} {short_title} {direction_label} — ${ticker}\n"
            f"\n"
            f"{name} buys {format_value(total)}\n"
            f"\n"
            f"• {shares:,} shares @ ${price:.2f}\n"
            f"{week52_str}"
            f"• {date_str}\n"
            f"\n"
            f"💡 Signal: {signal_score}/10 — {reason[:80]}\n"
            f"\n"
            f"#InsiderTrading #{ticker}"
        )

    return tweet.strip()


# ─────────────────────────────────────────────────────────────────────────────
# DIGEST GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

def generate_daily_digest(trades: list[dict], total_scanned: int = 0) -> str:
    """Generate daily summary tweet — deterministic ranked list, no AI call."""
    if not trades:
        return ""

    # Separate buys and sells, sort each by total value descending
    def _val(t): return t.get("total_value") or t.get("value") or 0

    buys = sorted(
        [t for t in trades if t.get("is_buy", True)],
        key=_val,
        reverse=True,
    )
    sells = sorted(
        [t for t in trades if not t.get("is_buy", True)],
        key=_val,
        reverse=True,
    )

    def rank_line(t):
        ticker      = t.get("ticker", "???")
        raw_title   = t.get("insider_title") or t.get("title") or "Insider"
        short_title = shorten_title(raw_title)
        total       = t.get("total_value") or t.get("value") or 0
        verb        = "buys" if t.get("is_buy", True) else "sells"
        return f"${ticker} — {short_title} {verb} {format_value(total)}"

    buy_lines  = [rank_line(t) for t in buys[:3]]
    sell_lines = [rank_line(t) for t in sells[:2]]

    sections = []
    if buy_lines:
        sections.append("🟢 TOP BUYS\n" + "\n".join(buy_lines))
    if sell_lines:
        sections.append("🔴 TOP SELLS\n" + "\n".join(sell_lines))

    tweet = (
        "🚨 TOP INSIDER TRADES TODAY\n\n"
        + "\n\n".join(sections)
        + "\n\n#InsiderTrading #Stocks"
    )

    return tweet.strip()


def generate_weekly_digest(trades: list[dict]) -> str:
    """Generate weekly roundup tweet using Claude Sonnet."""
    if not trades:
        return ""
    prompt = f"""
You are writing a weekly insider trading summary tweet for @Form4Wire.

This week's trades:
{json.dumps(trades, indent=2)[:4000]}

Write an engaging weekly digest. Include:
- Biggest single buy of the week
- Biggest single sell of the week  
- Best signal trade of the week
- Any sector rotation patterns (e.g. insiders selling tech, buying energy)
- Total $ value of all buys vs sells
- Most active company (most insider trades)
- Any cluster activity

Format as a thread-style tweet (single tweet, punchy):

📊 INSIDER WEEK IN REVIEW
Week of [dates]

🟢 Biggest Buy: ...
🔴 Biggest Sell: ...
🚨 Best Signal: ...
💡 Sector Watch: ...
👥 Most Active: ...

#InsiderTrading #WeeklyWrap #Stocks

Keep under 280 chars. Make it shareable.
"""
    try:
        msg = claude.messages.create(
            model=ANALYSIS_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return ""


def generate_followup_tweet(original_trade: dict, current_price: float, days: int) -> str:
    """Generate a performance follow-up tweet."""
    ticker      = original_trade.get("ticker", "")
    name        = original_trade.get("insider_name", "")
    title_role  = original_trade.get("insider_title", "")
    entry_price = original_trade.get("price_per_share", 0)
    direction   = "bought" if original_trade.get("is_buy") else "sold"
    trade_date  = original_trade.get("transaction_date", "")

    if not entry_price or not current_price:
        return ""

    change_pct = ((current_price - entry_price) / entry_price) * 100
    change_emoji = "📈" if change_pct > 0 else "📉"

    prompt = f"""
Write a brief follow-up tweet for @Form4Wire tracking a past insider trade.

Insider: {name} ({title_role})
Company: ${ticker}
They {direction} shares at ${entry_price:.2f} on {trade_date}
Current price: ${current_price:.2f}
Change: {change_pct:+.1f}% in {days} days

Write a punchy, engaging follow-up tweet. If the trade was profitable, make it feel exciting.
If it was a loss, keep it neutral/analytical. Include what the insider did and how it played out.

Format:
📊 {days}-DAY FOLLOWUP — ${ticker}
[What happened]
[Performance line]
[Brief commentary]
#InsiderTrading #{ticker}

Under 280 chars.
"""
    try:
        msg = claude.messages.create(
            model=FAST_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception:
        return ""


def generate_cluster_alert(ticker: str, company: str, trades: list[dict]) -> str:
    """Generate a cluster buying/selling alert."""
    prompt = f"""
Write a cluster insider trading alert tweet for @Form4Wire.

Company: {company} (${ticker})
Multiple insiders have traded recently:
{json.dumps(trades, indent=2)[:2000]}

Write an urgent, informative tweet about this cluster activity.
Mention how many insiders, total value, whether they're buying or selling.
This is a strong signal — make it feel important.

Format:
👥 CLUSTER ALERT — ${ticker}
[Details]
#InsiderTrading #{ticker} #ClusterBuy or #ClusterSell

Under 280 chars.
"""
    try:
        msg = claude.messages.create(
            model=FAST_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception:
        return ""
