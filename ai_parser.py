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
            print(f"[AI] Parse failed: response not a dict")
            return None
        if data.get("ticker") == "SKIP":
            print(f"[AI] Parse returned SKIP")
            return None
        if not data.get("ticker") and not data.get("insider_name"):
            print(f"[AI] Parse failed: no ticker or insider_name")
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

A rules-based system scored this {base_score}/10 using:
role (+1-3), purchase size (+1-3), position % increase (+1-3),
cluster buying (+2-3), stock down >40% from 52W high (+1), no buys in 12mo (+1).

Your job: adjust by -1, 0, or +1 based on context the formula cannot capture.

Trade: {trade_data}
Stock: {stock_data}
History: {history}
Breakdown: {breakdown}

ADJUST +1 if:
- Buying within 30 days of earnings (insider has freshest info)
- Buying near 52-week low (maximum contrarian conviction)
- Unusually large relative to what this insider normally does

ADJUST -1 if:
- Trade is trivially small vs total stake (under 1% of holdings)
- Clear non-signal context (matching a company stock purchase plan)

ADJUST 0 if no clear reason to move it.

Return ONLY a JSON object with no other text:
{{
  "adjustment": 0,
  "final_score": {base_score},
  "reasoning": "One punchy phrase under 35 chars — e.g. 'First buy in 2 years at 52W low'"
}}
"""


def _role_score(title: str) -> tuple[int, str]:
    """Map insider title to role points under hedge-fund scoring model."""
    t = title.lower().strip()
    if any(x in t for x in ["chief executive", "chairman", "founder", "co-founder"]):
        return 3, "+3 (CEO/Chairman/Founder)"
    if t == "ceo" or t.startswith("ceo ") or t.endswith(" ceo"):
        return 3, "+3 (CEO)"
    if "president" in t and "vice" not in t:
        return 3, "+3 (President)"
    csuite_terms = [
        "chief financial", "chief operating", "general counsel", "chief legal",
        "chief technology", "chief revenue", "chief marketing", "chief information",
        "chief accounting", "chief medical", "chief scientific", "chief compliance",
        "chief human", "chief people", "chief strategy", "chief data",
    ]
    if any(x in t for x in csuite_terms):
        return 2, "+2 (C-Suite officer)"
    if any(t == x or t.startswith(x + " ") or t.endswith(" " + x)
           for x in ["cfo", "coo", "cto", "cro", "cmo", "cio", "cao", "cco", "chro", "cso"]):
        return 2, "+2 (C-Suite officer)"
    if any(x in t for x in ["executive vice", "senior vice", "vice president",
                              "evp", "svp", "treasurer", "secretary",
                              "controller", "director", "board", " vp"]):
        return 1, "+1 (VP/Director/Board)"
    return 1, "+1 (Other insider)"


def _role_header(title: str) -> str:
    """Return short clean label for tweet header."""
    t = title.lower()
    role_pts, _ = _role_score(title)
    if role_pts == 3:
        if any(x in t for x in ["chief executive", "ceo"]): return "CEO"
        if "chairman" in t: return "CHAIRMAN"
        if "founder" in t: return "FOUNDER"
        if "president" in t: return "PRESIDENT"
        return "EXEC"
    if role_pts == 2:
        if any(x in t for x in ["chief financial", "cfo"]): return "CFO"
        if any(x in t for x in ["chief operating", "coo"]): return "COO"
        if any(x in t for x in ["chief technology", "cto"]): return "CTO"
        if "general counsel" in t: return "GEN COUNSEL"
        return "OFFICER"
    return "DIRECTOR" if "director" in t else "INSIDER"


def calculate_base_score(trade: dict, stock: dict, history: dict) -> tuple[int, dict]:
    """
    Hedge-fund style weighted scoring model.

    ROLE:        CEO/Chairman/Founder +3 | CFO/COO/CTO +2 | VP/Director +1
    VALUE:       >$1M +3 | $500K-$1M +2 | $100K-$500K +1
    POSITION %:  >50% +3 | 25-50% +2 | 10-25% +1
    CLUSTER:     3+ insiders +3 | 2 insiders +2 (7-day window)
    CONTEXT:     Stock down >40% from 52W high +1 | No buys in 12mo +1
    Claude:      -1/0/+1 adjustment. Final clamped 1-10.
    """
    points    = 0
    breakdown = {}
    code      = trade.get("transaction_code", "")
    total     = trade.get("total_value", 0)
    title     = trade.get("insider_title", "")
    before    = trade.get("shares_owned_before", 0)
    traded    = trade.get("shares_traded", 0)
    unusual   = history.get("unusual", False)
    cluster_n = history.get("cluster_count", 0)
    price     = stock.get("price", 0)
    high_52w  = stock.get("52w_high", 0)

    role_pts, role_label = _role_score(title)
    points += role_pts
    breakdown["role"] = role_label

    if total >= 1_000_000:
        points += 3
        breakdown["value"] = f"+3 (>${total/1e6:.1f}M purchase)"
    elif total >= 500_000:
        points += 2
        breakdown["value"] = f"+2 (${total/1e3:.0f}K purchase)"
    elif total >= 100_000:
        points += 1
        breakdown["value"] = f"+1 (${total/1e3:.0f}K purchase)"
    else:
        breakdown["value"] = f"+0 (${total/1e3:.0f}K — under $100K)"

    if before > 0 and traded > 0 and code == "P":
        pct = (traded / before) * 100
        if pct > 50:
            points += 3
            breakdown["position"] = f"+3 (position +{pct:.0f}% — very high conviction)"
        elif pct > 25:
            points += 2
            breakdown["position"] = f"+2 (position +{pct:.0f}%)"
        elif pct > 10:
            points += 1
            breakdown["position"] = f"+1 (position +{pct:.0f}%)"
        else:
            breakdown["position"] = f"+0 (position +{pct:.0f}%)"
    else:
        breakdown["position"] = "+0 (position data unavailable)"

    if cluster_n >= 3:
        points += 3
        breakdown["cluster"] = f"+3 ({cluster_n} insiders buying same ticker in 7 days)"
    elif cluster_n >= 2:
        points += 2
        breakdown["cluster"] = f"+2 ({cluster_n} insiders buying same ticker in 7 days)"
    else:
        breakdown["cluster"] = "+0 (no cluster)"

    if price and high_52w and code == "P":
        pct_from_high = (high_52w - price) / high_52w
        if pct_from_high > 0.40:
            points += 1
            breakdown["52w_high"] = f"+1 (stock −{pct_from_high*100:.0f}% from 52W high)"
        else:
            breakdown["52w_high"] = f"+0 (stock −{pct_from_high*100:.0f}% from 52W high)"
    else:
        breakdown["52w_high"] = "+0"

    if unusual:
        points += 1
        breakdown["unusual"] = "+1 (no insider buys in 12+ months)"
    else:
        breakdown["unusual"] = "+0"

    return points, breakdown


def score_signal(trade: dict, stock: dict, history: dict) -> tuple[int, str, dict]:
    """Pure rules-based score — no Claude API call needed."""
    base_score, breakdown = calculate_base_score(trade, stock, history)
    return max(1, min(10, base_score)), "", breakdown


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

    # ── Role header ────────────────────────────────────────────────────────
    rh = _role_header(title)

    # ── Score line (reasoning capped at 80 chars) ───────────────────────────
    # Strong signal format is longer so needs shorter reasoning
    score_line = f"💡 Signal: {signal_score}/10"

    # ── Position line ───────────────────────────────────────────────────────
    pos_line = ""
    if before > 0 and shares > 0 and is_buy:
        pct = (shares / before) * 100
        pos_line = f"• Position +{pct:.0f}% | Now owns {after:,} shares\n"
    elif after > 0:
        pos_line = f"• Now owns {after:,} shares\n"

    # ── 52W high line ───────────────────────────────────────────────────────
    high_line = ""
    if is_buy and stock.get("52w_high") and stock.get("price"):
        pct_from_high = (stock["52w_high"] - stock["price"]) / stock["52w_high"] * 100
        if pct_from_high > 5:
            high_line = f"• Stock −{pct_from_high:.0f}% from 52W high\n"

    # ── Cluster line ────────────────────────────────────────────────────────
    cluster_line = ""
    if isinstance(cluster_flag, int) and cluster_flag >= 2:
        cluster_line = f"• {cluster_flag} insiders buying this week\n"
    elif cluster_flag and not isinstance(cluster_flag, int):
        cluster_line = "• Multiple insiders buying this week\n"

    # ── Extra signals ───────────────────────────────────────────────────────
    extra = ""
    if unusual_flag:
        extra += "• First insider buy in 12+ months\n"
    if consecutive_buys >= 3:
        extra += f"• 🔁 {consecutive_buys} consecutive buys\n"
    if short_interest > 0.15 and is_buy:
        extra += f"• ⚡ Short interest {short_interest*100:.0f}% — contrarian bet\n"

    # ── STRONG SIGNAL (score >= 9) ──────────────────────────────────────────
    if signal_score >= 9:
        tweet = (
            f"🚨 STRONG INSIDER SIGNAL — ${ticker}\n"
            f"\n"
            f"{name} ({rh}) buys {format_value(total)}\n"
            f"\n"
            f"• {shares:,} shares @ ${price:.2f}\n"
            f"{pos_line}"
            f"{high_line}"
            f"{cluster_line}"
            f"{extra}"
            f"• {date_str}\n"
            f"\n"
            f"{score_line}\n"
            f"\n"
            f"#InsiderTrading #{ticker}"
        )
    else:
        # ── STANDARD format ────────────────────────────────────────────────
        tweet = (
            f"{direction_emoji} {rh} {direction_label} — ${ticker}\n"
            f"\n"
            f"{name} buys {format_value(total)}\n"
            f"• {shares:,} shares @ ${price:.2f}\n"
            f"{pos_line}"
            f"{high_line}"
            f"{cluster_line}"
            f"{extra}"
            f"• {date_str}\n"
            f"\n"
            f"{score_line}\n"
            f"\n"
            f"#InsiderTrading #{ticker}"
        )

    # ── Compact fallback if over 280 chars — drop extra signals ──────────────
    if len(tweet) > 280:
        header_line = (f"🚨 STRONG INSIDER SIGNAL — ${ticker}"
                       if signal_score >= 9 else
                       f"{direction_emoji} {rh} {direction_label} — ${ticker}")
        name_line = (f"{name} ({rh}) buys {format_value(total)}"
                     if signal_score >= 9 else
                     f"{name} buys {format_value(total)}")
        tweet = (
            f"{header_line}\n"
            f"\n"
            f"{name_line}\n"
            f"\n"
            f"• {shares:,} shares @ ${price:.2f}\n"
            f"{pos_line}"
            f"{high_line}"
            f"{cluster_line}"
            f"• {date_str}\n"
            f"\n"
            f"💡 Signal: {signal_score}/10\n"
            f"\n"
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
