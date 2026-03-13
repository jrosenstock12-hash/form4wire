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
    if v >= 100_000:
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
  "transaction_type": "",
  "transaction_code": "",
  "is_10b51_plan": false,
  "shares_traded": 0,
  "price_per_share": 0.0,
  "total_value": 0.0,
  "shares_owned_after": 0,
  "shares_owned_before": 0,
  "is_derivative": false,
  "derivative_type": "",
  "exercise_price": 0.0,
  "expiration_date": "",
  "held_after_exercise": false
}}

Rules:
- If total_value is not stated, calculate: shares_traded × price_per_share
- If shares_owned_before is missing, estimate: shares_owned_after + shares_traded (for sells) or shares_owned_after - shares_traded (for buys)
- If ANY critical field (insider_name, shares_traded, transaction_code) is missing, set ticker to "SKIP"
- For transaction_type, use the full human-readable description, not the code letter
"""


def parse_filing(title: str, content: str, company: dict, xml_content: str = ""):  # -> Optional[dict]
    """Use Claude Haiku to extract structured data from a Form 4 filing."""
    prompt = PARSE_PROMPT.format(
        title=title,
        company=json.dumps(company),
        content=content[:5000],
    )
    try:
        msg = claude.messages.create(
            model=FAST_MODEL,
            max_tokens=1500,
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
Historical context: {history}
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
  "final_score": {base_score},
  "reasoning": "One punchy sentence a finance follower would find interesting"
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

    # Step 2 — Claude adjusts by -1, 0, or +1
    prompt = SCORE_ADJUST_PROMPT.format(
        base_score=base_score,
        trade_data=json.dumps(trade),
        stock_data=json.dumps(stock),
        history=json.dumps(history),
        breakdown=json.dumps(breakdown),
    )
    try:
        msg = claude.messages.create(
            model=FAST_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw  = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        data = json.loads(raw)
        adjustment  = max(-1, min(1, int(data.get("adjustment", 0))))  # clamp to -1/0/+1
        final_score = max(1, min(10, base_score + adjustment))          # clamp to 1-10
        reasoning   = data.get("reasoning", "")
        return final_score, reasoning
    except Exception:
        return max(1, min(10, base_score)), "Signal score unavailable"


def score_emoji(score: int) -> str:
    return "💡"  # Always consistent


# ─────────────────────────────────────────────────────────────────────────────
# TWEET FORMATTER
# ─────────────────────────────────────────────────────────────────────────────

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
    is_10b51   = trade.get("is_10b51_plan", False)
    shares     = int(trade.get("shares_traded", 0))
    price      = trade.get("price_per_share", 0)
    total      = trade.get("total_value", 0)
    after      = trade.get("shares_owned_after", 0)
    before     = trade.get("shares_owned_before", 0)
    is_deriv   = trade.get("is_derivative", False)
    deriv_type = trade.get("derivative_type", "")
    held_after = trade.get("held_after_exercise", False)
    tx_date    = trade.get("transaction_date", "")
    filed_date = trade.get("filed_date", "")

    # Format dates nicely
    def fmt_date(d):
        try:
            from datetime import datetime
            return datetime.strptime(d, "%Y-%m-%d").strftime("%b %d, %Y")
        except Exception:
            return d

    tx_date_fmt    = fmt_date(tx_date)
    filed_date_fmt = fmt_date(filed_date) if filed_date else ""
    date_str = f"Trade: {tx_date_fmt} | Filed: {filed_date_fmt if filed_date_fmt else tx_date_fmt}"

    # Late filing flag
    late_flag = ""
    try:
        from datetime import datetime
        t = datetime.strptime(tx_date, "%Y-%m-%d")
        f = datetime.strptime(filed_date, "%Y-%m-%d") if filed_date else t
        days_late = (f - t).days
        if days_late > 5:
            late_flag = f"\n⚠️ Late filing — trade was {days_late} days ago"
    except Exception:
        pass

    # Current price vs trade price
    current_price_str = ""
    current_price = stock.get("price", 0)
    if current_price and price:
        pct_change = ((current_price - price) / price) * 100
        arrow = "📈" if pct_change >= 0 else "📉"
        sign = "+" if pct_change >= 0 else ""
        current_price_str = f"{arrow} Current: ${current_price:.2f} ({sign}{pct_change:.1f}% since trade)\n"

    # Direction
    is_buy = code in ("P", "M") or "purchase" in tx_type.lower() or "exercise" in tx_type.lower()
    direction_emoji = "🟢" if is_buy else "🔴"
    direction_label = "BUY" if is_buy else "SELL"

    # Stake change
    stake_pct = ""
    if before > 0 and shares > 0:
        pct = (shares / before) * 100
        stake_pct = f"📊 {pct:.1f}% of holdings | {after:,} shares remain\n"

    # 52-week position
    week52 = ""
    if stock.get("52w_high") and stock.get("52w_low") and stock.get("price"):
        week52 = f"📉 52w: ${stock['52w_low']:.2f} — ${stock['52w_high']:.2f}\n"

    # Short interest
    short_str = ""
    if short_interest > 0.10:
        short_str = f"⚡ Short interest: {short_interest*100:.1f}%"
        if is_buy and short_interest > 0.20:
            short_str += " 🔥 HIGH SHORT + INSIDER BUY"

    # Earnings proximity
    earnings_str = ""
    if next_earnings:
        earnings_str = f"📅 Next earnings: {next_earnings}\n"

    # 10b5-1 flag
    plan_str = "📋 Pre-planned 10b5-1 sale\n" if is_10b51 else ("✅ NOT a planned sale\n" if not is_buy else "")

    # Unusual activity
    unusual_str = "🚨 UNUSUAL — No trades in 12 months\n" if unusual_flag else ""

    # Consecutive buys
    streak_str = f"🔁 {consecutive_buys}rd consecutive buy — showing conviction\n" if consecutive_buys >= 3 else ""

    # Analyst divergence
    analyst_str = f"🤔 {analyst_divergence}\n" if analyst_divergence else ""

    # Cluster flag
    cluster_str = "👥 CLUSTER ALERT — Multiple insiders trading\n" if cluster_flag else ""

    # Derivative details
    deriv_str = ""
    if is_deriv and deriv_type:
        deriv_str = f"🔧 {deriv_type}\n"
        if held_after:
            deriv_str += " — shares HELD after exercise (bullish)"
        else:
            deriv_str += " — shares sold after exercise"

    # Market cap
    cap_str = f"{cap_label(stock.get('market_cap', 0))}\n" if stock.get("market_cap") else ""

    # Signal label — more meaningful than a raw number
    def signal_label(score: int) -> str:
        if score >= 9: return "MAX CONVICTION"
        if score >= 7: return "HIGH CONVICTION"
        if score >= 5: return "MODERATE"
        return "LOW"

    score_str = f"💡 Signal: {signal_score}/10 — {signal_reason}\n"

    tweet = (
        f"{direction_emoji} INSIDER {direction_label} — ${ticker}\n"
        f"👤 {name}\n"
        f"💼 {title}\n"
        f"📦 {shares:,} shares @ ${price:.2f}\n"
        f"💰 {format_value(total)}\n"
        f"📅 {date_str}{late_flag}\n"
        f"{current_price_str}"
        f"{stake_pct}"
        f"{unusual_str}"
        f"{streak_str}"
        f"{cluster_str}"
        f"{short_str}"
        f"{week52}"
        f"{earnings_str}"
        f"{analyst_str}"
        f"{deriv_str}"
        f"{score_str}"f"#InsiderTrading #{ticker}"
    )

    # Trim to 280 chars if needed (keep most important lines)
    if len(tweet) > 280:
        # Build a compact version
        tweet = (
            f"{direction_emoji} INSIDER {direction_label} — ${ticker}\n"
            f"👤 {name} | 💼 {title}\n"
            f"📦 {shares:,} shares @ ${price:.2f}\n"
            f"💰 {format_value(total)}\n"
            f"📅 {date_str}{late_flag}\n"
            f"{current_price_str}"
            f"{unusual_str}"
            f"{short_str}"
            f"💡 Signal: {signal_score}/10\n"
            f"#InsiderTrading #{ticker}"
        )

    return tweet.strip()


# ─────────────────────────────────────────────────────────────────────────────
# DIGEST GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

def generate_daily_digest(trades: list[dict], total_scanned: int = 0) -> str:
    """Generate daily summary tweet using Claude Sonnet."""
    if not trades:
        return ""

    significant = len(trades)
    scan_note = f"{significant} signals from {total_scanned:,} filings reviewed" if total_scanned else f"{significant} signals today"

    prompt = f"""
You are writing a daily insider trading summary tweet for the account @Form4Wire.

Today's significant trades ({scan_note}):
{json.dumps(trades, indent=2)[:3000]}

Write a concise, engaging daily digest tweet. Include:
- How many significant trades were flagged vs total reviewed (use the note: "{scan_note}"))
- Biggest buy (name, ticker, value)
- Biggest sell if any — if no sells were flagged as significant today, say "No notable sells" NOT "None — all buys today"
- Most significant signal (highest score)
- Any notable patterns (cluster activity, unusual trades)

Format:
📊 INSIDER DAILY DIGEST

🟢 Top Buy: [details]
🔴 Top Sell: [details or "No notable sells"]
🚨 Top Signal: [details]
📈 [X] signals from [Y] filings reviewed

#InsiderTrading #Stocks #Finance

Keep it under 280 characters. Be punchy and informative.
"""
    try:
        msg = claude.messages.create(
            model=ANALYSIS_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return ""


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
