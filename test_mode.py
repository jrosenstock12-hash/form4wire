"""
test_mode.py — Runs the full Form4Wire pipeline WITHOUT posting to X.
Saves all parsed trades and formatted tweets to test_output.json and test_output.txt
so you can review exactly what would be posted before going live.

Requirements: Only needs your ANTHROPIC_API_KEY in .env
No X credentials needed for this test.
"""

import os
import json
import time
from datetime import datetime, timezone

# Load .env file manually (no python-dotenv needed)
def load_env():
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()

load_env()

# ── Verify Anthropic key exists ──────────────────────────────────────────────
if not os.environ.get("ANTHROPIC_API_KEY"):
    print("❌ ANTHROPIC_API_KEY not found in .env file.")
    print("   Open .env and add: ANTHROPIC_API_KEY=sk-ant-...")
    exit(1)

print("✅ Anthropic API key found")
print("🔍 Starting test run — no X account needed\n")

# ── Import bot modules ────────────────────────────────────────────────────────
from sec_fetcher import (
    fetch_form4_feed, fetch_filing_xml, fetch_company_data,
    fetch_stock_price, fetch_short_interest, fetch_next_earnings,
)
from ai_parser import (
    classify_insider_tier, parse_filing, score_signal,
    build_tweet, generate_daily_digest,
)
from data_store import get_insider_history, record_trade_for_cluster
import config


def format_value(v):
    if v >= 1_000_000_000: return f"${v/1_000_000_000:.1f}B"
    if v >= 1_000_000:     return f"${v/1_000_000:.1f}M"
    if v >= 1_000:         return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"


def get_size_threshold(market_cap, tier=1):
    """Return minimum dollar threshold scaled by company size and insider tier."""
    if market_cap >= config.LARGE_CAP:
        base = config.MEGA_LARGE_CAP_MIN
    elif market_cap >= config.MID_CAP:
        base = config.MID_CAP_MIN
    else:
        base = config.SMALL_CAP_MIN
    multiplier = config.TIER_MULTIPLIERS.get(tier, 1.0)
    return int(base * multiplier)


def meets_threshold(trade, tier, market_cap=0):
    # 1. Transaction type filter
    code = trade.get("transaction_code", "")
    if code in config.SKIP_TRANSACTION_CODES:
        return False, f"Transaction type {code} filtered"
    if code == "M" and not trade.get("held_after_exercise", False):
        return False, "Option exercise — shares not held"
    # 2. Skip pre-planned 10b5-1 sales
    if trade.get("is_10b51_plan") or trade.get("is_10b51") and code == "S":
        return False, "Pre-planned 10b5-1 sale — no signal"
    # 3. Size threshold scales with both market cap AND tier
    value = trade.get("total_value", 0)
    threshold = get_size_threshold(market_cap, tier)
    if value < threshold:
        tier_label = {1: "Tier 1", 2: "Tier 2", 3: "Director"}.get(tier, "Tier ?")
        return False, f"{tier_label} below threshold (${value:,.0f} < ${threshold:,.0f})"
    return True, ""


def check_analyst_divergence(trade, stock):
    code  = trade.get("transaction_code", "")
    price = stock.get("price", 0)
    high  = stock.get("52w_high", 0)
    low   = stock.get("52w_low", 0)
    if not price or not high or not low:
        return ""
    pct_from_high = (high - price) / high if high else 1
    if code == "P" and pct_from_high < 0.05:
        return "Buying near 52-week HIGH — strong conviction signal"
    if code == "S" and price <= low * 1.05:
        return "Selling near 52-week LOW — unusual bearish signal"
    return ""


def run_test(max_filings: int = 10):
    """
    Fetch real SEC filings, parse them with Claude, build tweets,
    and save results to files — without posting anything to X.
    """

    results      = []   # Full structured data for JSON output
    tweet_lines  = []   # Human-readable text output
    skipped      = []

    print(f"📡 Fetching latest Form 4 filings from SEC EDGAR...")
    filings = fetch_form4_feed()

    if not filings:
        print("❌ Could not reach SEC EDGAR. Check your internet connection.")
        return

    print(f"✅ Found {len(filings)} filings in feed")
    print(f"🔬 Processing up to {max_filings} filings...\n")
    print("-" * 60)

    processed = 0
    posted    = 0

    for filing in filings[:max_filings]:
        processed += 1
        print(f"[{processed}/{min(max_filings, len(filings))}] {filing['title'][:70]}")

        # 1. Fetch XML content
        xml_content = fetch_filing_xml(filing["url"])
        time.sleep(0.3)  # Respectful SEC rate limiting

        # 2. Fetch company data
        company = fetch_company_data(filing.get("cik", ""))

        # 3. Parse with Claude (xml_content also passed for reliable shares data)
        print("      → Parsing with Claude AI...")
        trade = parse_filing(filing["title"], xml_content, company, xml_content=xml_content)

        if not trade:
            skipped.append({"title": filing["title"], "reason": "Claude could not parse"})
            print("      → SKIP: Could not extract trade data\n")
            continue

        # Fill gaps from company data
        if not trade.get("ticker") and company.get("ticker"):
            trade["ticker"] = company["ticker"]
        if not trade.get("company_name") and company.get("name"):
            trade["company_name"] = company["name"]
        # Always override filed_date from SEC feed (more reliable than Claude's parse)
        trade["filed_date"] = filing.get("filed_date", "")

        ticker = trade.get("ticker", "???")

        # 4. Classify tier
        tier = classify_insider_tier(trade.get("insider_title", ""))

        # 5a. Quick checks before fetching market data
        if config.POST_TIER1_ONLY and tier != 1:
            skipped.append({"title": filing["title"], "ticker": ticker, "reason": f"Not Tier 1 (Tier {tier})"})
            print(f"      → SKIP: Not Tier 1\n")
            continue

        code = trade.get("transaction_code", "")
        if code in config.SKIP_TRANSACTION_CODES:
            skipped.append({"title": filing["title"], "ticker": ticker, "reason": f"Filtered transaction type ({code})"})
            print(f"      → SKIP: Transaction type {code} filtered\n")
            continue

        if code == "M" and not trade.get("held_after_exercise", False):
            skipped.append({"title": filing["title"], "ticker": ticker, "reason": "Option exercise — shares not held"})
            print(f"      → SKIP: Option exercise, shares not held\n")
            continue

        # 5c. Stale trade filter
        tx_date_str    = trade.get("transaction_date", "")
        filed_date_str = trade.get("filed_date", "") or filing.get("filed_date", "")
        if tx_date_str and filed_date_str:
            try:
                import datetime as _dt
                tx_dt    = _dt.date.fromisoformat(tx_date_str[:10])
                filed_dt = _dt.date.fromisoformat(filed_date_str[:10])
                bdays = 0
                cur = tx_dt
                while cur < filed_dt:
                    cur += _dt.timedelta(days=1)
                    if cur.weekday() < 5:
                        bdays += 1
                if bdays > config.MAX_FILING_LAG_BDAYS:
                    skipped.append({"title": filing["title"], "ticker": ticker, "reason": f"Stale filing — {bdays} business days late"})
                    print(f"      → SKIP: Stale filing — {bdays} business days between trade and filing\n")
                    continue
            except Exception:
                pass

        # 6. Fetch market data (needed for size-based threshold)
        print(f"      → Fetching market data for ${ticker}...")
        stock      = fetch_stock_price(ticker)
        short_int  = fetch_short_interest(ticker)
        next_earn  = fetch_next_earnings(ticker)
        time.sleep(0.2)

        # 5b. Size-based dollar threshold
        market_cap = stock.get("market_cap", 0)
        passes, skip_reason = meets_threshold(trade, tier, market_cap)
        if not passes:
            skipped.append({"title": filing["title"], "ticker": ticker, "reason": skip_reason})
            print(f"      → SKIP: {skip_reason}\n")
            continue

        # 7. Get insider history
        history     = get_insider_history(ticker, trade.get("insider_name", ""))
        unusual     = history.get("unusual", False)
        consec_buys = history.get("consecutive_buys", 0)

        # 8. Analyst divergence
        analyst_div = check_analyst_divergence(trade, stock)

        # Inject short interest into stock dict so scorer can access it
        stock["short_interest"] = short_int

        # 9. Signal score
        print("      → Scoring signal with Claude AI...")
        score, reason, breakdown = score_signal(trade, stock, history)

        # 10. Cluster check
        cluster_data = record_trade_for_cluster(trade)
        cluster_flag = cluster_data is not None

        # 11. Build tweet
        tweet = build_tweet(
            trade             = trade,
            stock             = stock,
            tier              = tier,
            signal_score      = score,
            signal_reason     = reason,
            short_interest    = short_int,
            next_earnings     = next_earn,
            unusual_flag      = unusual,
            consecutive_buys  = consec_buys,
            analyst_divergence= analyst_div,
            cluster_flag      = cluster_flag,
        )

        # Score gate
        is_sell = trade.get("transaction_code") == "S"
        min_score = config.MIN_SCORE_SELL if is_sell else config.MIN_SCORE_BUY
        if score < min_score:
            skip_reason = f"Score {score}/10 below threshold ({min_score}/10 for {'sells' if is_sell else 'buys'})"
            print(f"      → SKIP: {skip_reason}\n")
            skipped.append({"ticker": ticker, "reason": skip_reason})
            continue

        posted += 1
        print(f"      → ✅ WOULD POST (Signal: {score}/10)\n")

        # Store full result
        results.append({
            "filing_title":    filing["title"],
            "ticker":          ticker,
            "company":         trade.get("company_name", ""),
            "insider_name":    trade.get("insider_name", ""),
            "insider_title":   trade.get("insider_title", ""),
            "transaction_type": trade.get("transaction_type", ""),
            "transaction_code": trade.get("transaction_code", ""),
            "is_10b51_plan":   trade.get("is_10b51_plan", False),
            "shares_traded":   trade.get("shares_traded", 0),
            "price_per_share": trade.get("price_per_share", 0),
            "total_value":     trade.get("total_value", 0),
            "total_value_fmt": format_value(trade.get("total_value", 0)),
            "shares_after":    trade.get("shares_owned_after", 0),
            "tier":            tier,
            "signal_score":    score,
            "signal_reason":   reason,
            "stock_price":     stock.get("price", 0),
            "market_cap":      stock.get("market_cap", 0),
            "52w_high":        stock.get("52w_high", 0),
            "52w_low":         stock.get("52w_low", 0),
            "short_interest":  short_int,
            "next_earnings":   next_earn,
            "unusual_activity": unusual,
            "consecutive_buys": consec_buys,
            "cluster_alert":   cluster_flag,
            "analyst_note":    analyst_div,
            "tweet":           tweet,
            "tweet_length":    len(tweet),
            "processed_at":    datetime.now(timezone.utc).isoformat(),
        })

        tweet_lines.append(f"{'='*60}")
        tweet_lines.append(f"TRADE #{posted} | Signal: {score}/10 | Tier {tier} | ${ticker}")
        tweet_lines.append(f"{'='*60}")
        tweet_lines.append(tweet)
        tweet_lines.append(f"\n[Tweet length: {len(tweet)} chars]")
        tweet_lines.append("")

    # ── Generate digest preview ───────────────────────────────────────────────
    if results:
        print("\n📊 Generating daily digest preview...")
        digest_trades = [
            {
                "ticker":  r["ticker"],
                "name":    r["insider_name"],
                "title":   r["insider_title"],
                "code":    r["transaction_code"],
                "value":   r["total_value"],
                "score":   r["signal_score"],
                "is_buy":  r["transaction_code"] in ("P", "M"),
            }
            for r in results
        ]
        digest = generate_daily_digest(digest_trades, total_scanned=processed)
        if digest:
            tweet_lines.append("=" * 60)
            tweet_lines.append("DAILY DIGEST PREVIEW")
            tweet_lines.append("=" * 60)
            tweet_lines.append(digest)
            tweet_lines.append("")

    # ── Save outputs ──────────────────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)

    # Save full JSON data
    with open("data/test_output.json", "w") as f:
        json.dump({
            "run_at":    datetime.now(timezone.utc).isoformat(),
            "processed": processed,
            "would_post": posted,
            "skipped":   len(skipped),
            "trades":    results,
            "skipped_details": skipped,
        }, f, indent=2, default=str)

    # Save human-readable tweets
    with open("data/test_output.txt", "w") as f:
        f.write(f"Form4Wire Test Run — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n")
        f.write(f"Processed: {processed} filings | Would post: {posted} | Skipped: {len(skipped)}\n\n")
        f.write("\n".join(tweet_lines))

        if skipped:
            f.write("\n\n" + "="*60 + "\n")
            f.write("SKIPPED FILINGS\n")
            f.write("="*60 + "\n")
            for s in skipped:
                f.write(f"• {s.get('ticker','?')} — {s['reason']}\n")

    # ── Print summary ─────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TEST RUN COMPLETE")
    print("="*60)
    print(f"✅ Filings processed:   {processed}")
    print(f"📤 Would have posted:   {posted}")
    print(f"⏭️  Skipped (filtered):  {len(skipped)}")
    print()
    print("📄 Results saved to:")
    print("   data/test_output.txt  ← READ THIS — formatted tweets")
    print("   data/test_output.json ← Full structured data")
    print()

    if results:
        top = max(results, key=lambda x: x["signal_score"])
        print(f"🏆 Highest signal trade: ${top['ticker']} — {top['insider_name']}")
        print(f"   Score: {top['signal_score']}/10 — {top['signal_reason']}")
        print()
        print("Sample tweet that would have been posted:")
        print("-" * 60)
        print(top["tweet"])
        print("-" * 60)


if __name__ == "__main__":
    import sys
    # Optionally pass number of filings to test: python test_mode.py 20
    max_f = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    run_test(max_filings=max_f)
