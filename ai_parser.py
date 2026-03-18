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
  "insider_remarks": "",
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
- For insider_remarks, extract the full text from the "Remarks" or "Explanation of Responses" section if present — this often contains the real job title (e.g. "Principal executive officer and Chief Investment Officer")
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


def _role_score(title: str, remarks: str = "") -> tuple[int, str]:
    """Map insider title to role points under hedge-fund scoring model.
    Falls back to remarks field if title alone is insufficient (e.g. Director who is also CEO).
    """
    # Combine title + remarks for a richer signal
    combined = (title + " " + remarks).lower().strip()
    t = title.lower().strip()

    # Check combined first for CEO-level keywords
    if any(x in combined for x in ["chief executive", "chairman", "founder", "co-founder",
                                     "principal executive"]):
        return 3, "+3 (CEO/Chairman/Founder — from remarks)" if any(x in remarks.lower() for x in ["chief executive", "principal executive", "chairman", "founder"]) else "+3 (CEO/Chairman/Founder)"
    if t == "ceo" or t.startswith("ceo ") or t.endswith(" ceo"):
        return 3, "+3 (CEO)"
    if "president" in combined and "vice" not in combined:
        return 3, "+3 (President)"
    csuite_terms = [
        "chief financial", "chief operating", "general counsel", "chief legal",
        "chief technology", "chief revenue", "chief marketing", "chief information",
        "chief accounting", "chief medical", "chief scientific", "chief compliance",
        "chief human", "chief people", "chief strategy", "chief data", "chief investment",
    ]
    if any(x in combined for x in csuite_terms):
        label = "+2 (C-Suite — from remarks)" if any(x in remarks.lower() for x in csuite_terms) and not any(x in t for x in csuite_terms) else "+2 (C-Suite officer)"
        return 2, label
    if any(t == x or t.startswith(x + " ") or t.endswith(" " + x)
           for x in ["cfo", "coo", "cto", "cro", "cmo", "cio", "cao", "cco", "chro", "cso"]):
        return 2, "+2 (C-Suite officer)"
    if any(x in t for x in ["executive vice", "senior vice", "vice president",
                              "evp", "svp", "treasurer", "secretary",
                              "controller", "director", "board", " vp"]):
        return 1, "+1 (VP/Director/Board)"
    return 1, "+1 (Other insider)"


def _role_header(title: str, remarks: str = "") -> str:
    """Return short clean label for tweet header."""
    combined = (title + " " + remarks).lower()
    t = title.lower()
    role_pts, _ = _role_score(title, remarks)
    if role_pts == 3:
        if any(x in combined for x in ["chief executive", "ceo", "principal executive"]): return "CEO"
        if "chairman" in combined: return "CHAIRMAN"
        if "founder" in combined: return "FOUNDER"
        if "president" in combined: return "PRESIDENT"
        return "EXEC"
    if role_pts == 2:
        if any(x in combined for x in ["chief financial", "cfo"]): return "CFO"
        if any(x in combined for x in ["chief operating", "coo"]): return "COO"
        if any(x in combined for x in ["chief technology", "cto"]): return "CTO"
        if any(x in combined for x in ["chief investment", "cio"]): return "CIO"
        if "general counsel" in combined: return "GEN COUNSEL"
        return "OFFICER"
    return "DIRECTOR" if "director" in t else "INSIDER"


def days_until_earnings(next_earnings: str) -> int:
    """Return days until next earnings date, or 999 if unavailable/unparseable."""
    if not next_earnings:
        return 999
    try:
        from datetime import datetime, timezone
        earn_dt = datetime.strptime(next_earnings, "%b %d, %Y").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        days = (earn_dt - now).days
        return days if days >= 0 else 999
    except Exception:
        return 999


def calculate_base_score(trade: dict, stock: dict, history: dict, next_earnings: str = "") -> tuple[int, dict]:
    """
    Hedge-fund style weighted scoring model.

    ROLE:        CEO/Chairman/Founder +3 | CFO/COO/CTO +2 | VP/Director +1
    VALUE:       >$1M +3 | $500K-$1M +2 | $100K-$500K +1
    POSITION %:  >50% +3 | 25-50% +2 | 10-25% +1
    CLUSTER:     3+ insiders +3 | 2 insiders +2 (7-day window)
    CONTEXT:     Stock down >40% from 52W high +1 | No buys in 12mo +1 | Earnings within 21 days +1
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

    role_pts, role_label = _role_score(title, trade.get("insider_remarks", ""))
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

    consec = history.get("consecutive_buys", 0)
    if consec >= 2:
        points += 1
        breakdown["streak"] = f"+1 ({consec} consecutive buys within 6 months)"
    else:
        breakdown["streak"] = "+0 (no consecutive buy streak)"

    earn_days = days_until_earnings(next_earnings)
    if earn_days <= 21 and code == "P":
        points += 1
        breakdown["earnings"] = f"+1 (buying {earn_days} days before earnings — {next_earnings})"
    else:
        breakdown["earnings"] = "+0 (no earnings within 21 days)" if not next_earnings else f"+0 ({earn_days} days to earnings)"

    return points, breakdown


def score_signal(trade: dict, stock: dict, history: dict, next_earnings: str = "") -> tuple[int, str]:
    """Pure rules-based score — no Claude API call needed."""
    base_score, _ = calculate_base_score(trade, stock, history, next_earnings)
    return max(1, min(10, base_score)), ""


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
    remarks = trade.get("insider_remarks", "")
    rh = _role_header(title, remarks)

    # ── Score line (reasoning capped at 80 chars) ───────────────────────────
    # Strong signal format is longer so needs shorter reasoning
    score_line = f"💡 Signal: {signal_score}/10"

    # ── Position line ───────────────────────────────────────────────────────
    pos_line = ""
    if before > 0 and shares > 0 and is_buy:
        pct = (shares / before) * 100
        if pct < 1:
            pos_line = f"• Position +{pct:.2f}%\n"
        else:
            pos_line = f"• Position +{pct:.0f}%\n"

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
    if consecutive_buys >= 4:
        extra += f"• 🔁 {consecutive_buys} consecutive buys — aggressive accumulation\n"
    elif consecutive_buys == 3:
        extra += f"• 🔁 3rd consecutive buy — strong accumulation\n"
    elif consecutive_buys == 2:
        extra += f"• 🔁 2nd consecutive buy within 6 months\n"
    if short_interest > 0.15 and is_buy:
        extra += f"• ⚡ Short interest {short_interest*100:.0f}% — contrarian bet\n"
    earn_days = days_until_earnings(next_earnings)
    if earn_days <= 21 and is_buy:
        extra += f"• ⚡ Buying {earn_days} days before earnings ({next_earnings})\n"

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
            f"{extra}"
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

    prompt = f"""
You are writing a daily insider trading summary tweet for the account @Form4Wire.

Today's significant trades:
{json.dumps(trades, indent=2)[:3000]}

Write a concise daily digest tweet using EXACTLY this format:

📊 INSIDER DAILY DIGEST

🟢 Top Buys:
• [Insider/TICKER] — [$value]
• [Insider/TICKER] — [$value]
• [Insider/TICKER] — [$value]

🚨 Top Signals (8+):
• [TICKER] — [score]/10
• [TICKER] — [score]/10

#InsiderTrading #Stocks #Finance

Rules:
- Top Buys: show up to 3 biggest buys by dollar value, bullet points only
- Top Signals: show up to 3 trades with score 8 or above only. If none scored 8+, omit this section entirely
- No other lines, no sell section, no filing counts
- Keep entire tweet under 280 characters
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

Write a weekly digest tweet using EXACTLY this format:

📊 INSIDER WEEK IN REVIEW

🟢 Top Buys This Week:
• [Insider/TICKER] — [$value]
• [Insider/TICKER] — [$value]
• [Insider/TICKER] — [$value]

🚨 Top Signals This Week (8+):
• [TICKER] — [score]/10
• [TICKER] — [score]/10

#InsiderTrading #Stocks #Finance

Rules:
- Top Buys: show up to 3 biggest buys by dollar value for the week, bullet points only
- Top Signals: show up to 3 trades with score 8 or above only. If none scored 8+, omit this section entirely
- No other lines, no sell section, no filing counts
- Keep entire tweet under 280 characters
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


def generate_followup_tweet(original_trade: dict, current_price: float, days: int, change_pct: float = None) -> str:
    """Generate a performance follow-up tweet."""
    ticker      = original_trade.get("ticker", "")
    raw_name    = original_trade.get("insider_name", "")
    name        = " ".join(w.capitalize() for w in raw_name.split())
    title_role  = original_trade.get("insider_title", "")
    entry_price = original_trade.get("price_per_share", 0)
    trade_date  = original_trade.get("transaction_date", "")
    total_value = original_trade.get("total_value", 0)

    if not entry_price or not current_price:
        return ""

    if change_pct is None:
        change_pct = ((current_price - entry_price) / entry_price) * 100

    rh = _role_header(title_role)

    # Format trade date nicely
    try:
        from datetime import datetime
        trade_date_fmt = datetime.strptime(trade_date, "%Y-%m-%d").strftime("%b %d")
    except Exception:
        trade_date_fmt = trade_date

    is_win  = change_pct >= 10.0
    is_loss = change_pct <= -20.0

    sign   = "+" if change_pct >= 0 else ""
    arrow  = "📈" if change_pct >= 0 else "📉"
    header = "📈" if is_win else "📉"

    if is_win:
        tweet = (
            f"{header} {days}-DAY FOLLOWUP — ${ticker}\n"
            f"\n"
            f"{name} ({rh}) bought {format_value(total_value)} on {trade_date_fmt}\n"
            f"• Entry: ${entry_price:.2f} → Now: ${current_price:.2f}\n"
            f"• {sign}{change_pct:.1f}% in {days} days {arrow}\n"
            f"\n"
            f"#InsiderTrading #{ticker}"
        )
    else:
        # 90-day loss post
        tweet = (
            f"{header} {days}-DAY FOLLOWUP — ${ticker}\n"
            f"\n"
            f"{name} ({rh}) bought {format_value(total_value)} on {trade_date_fmt}\n"
            f"• Entry: ${entry_price:.2f} → Now: ${current_price:.2f}\n"
            f"• {sign}{change_pct:.1f}% in {days} days {arrow}\n"
            f"\n"
            f"Not every signal plays out. We track them all.\n"
            f"\n"
            f"#InsiderTrading #{ticker}"
        )

    return tweet.strip()


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
