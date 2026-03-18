"""
bot.py — Main orchestrator for Form4Wire
Ties together SEC fetching, AI parsing, data storage, and X posting.
"""

import os
import time
import logging
from datetime import datetime, timezone, date, timedelta
from dotenv import load_dotenv
load_dotenv()

import config
from sec_fetcher  import (
    fetch_form4_feed, fetch_filing_xml, fetch_company_data,
    fetch_stock_price, fetch_short_interest, fetch_next_earnings,
    parse_transactions_from_xml,
)
from ai_parser    import (
    classify_insider_tier, parse_filing, score_signal,
    build_tweet, generate_daily_digest, generate_weekly_digest,
    generate_followup_tweet, generate_cluster_alert,
)
from data_store   import (
    load_seen, save_seen,
    save_trade, get_insider_history,
    add_to_followup_queue, get_due_followups, mark_followup_posted, mark_all_followups_done,
    record_trade_for_cluster,
    log_daily_trade, get_last_24h_trades, get_week_trades,
    increment_daily_scan, get_daily_scan_count,
)
from x_poster import post_tweet
from web_feed import save_to_web_feed

# ── LOGGING ──────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("Form4Wire")


# ── VALUE THRESHOLDS ─────────────────────────────────────────────────────────

def get_size_threshold(market_cap: float, tier: int = 1) -> float:
    """Return minimum dollar threshold scaled by company size and insider tier."""
    if market_cap >= config.LARGE_CAP:
        base = config.MEGA_LARGE_CAP_MIN
    elif market_cap >= config.MID_CAP:
        base = config.MID_CAP_MIN
    else:
        base = config.SMALL_CAP_MIN
    multiplier = config.TIER_MULTIPLIERS.get(tier, 1.0)
    return int(base * multiplier)


def meets_threshold(trade: dict, tier: int, market_cap: float = 0) -> tuple[bool, str]:
    code = trade.get("transaction_code", "")
    if code in config.SKIP_TRANSACTION_CODES:
        return False, f"Skipped transaction type ({code})"
    if code == "M" and not trade.get("held_after_exercise", False):
        return False, "Option exercise — shares not held"
    if trade.get("is_10b51_plan") or trade.get("is_10b51") and code == "S":
        return False, "Pre-planned 10b5-1 sale — no signal"
    value = trade.get("total_value", 0)
    threshold = get_size_threshold(market_cap, tier)
    if value < threshold:
        tier_label = {1: "Tier 1", 2: "Tier 2", 3: "Director"}.get(tier, "Tier ?")
        return False, f"{tier_label} below threshold (${value:,.0f} < ${threshold:,.0f})"
    return True, ""


# ── ANALYST DIVERGENCE CHECK ─────────────────────────────────────────────────

def check_analyst_divergence(trade: dict, stock: dict) -> str:
    """
    Simple divergence check: if insider is buying but stock is near 52w high
    that's unusual. If insider is selling near 52w low, flag it.
    More sophisticated version would pull analyst ratings via an API.
    """
    code  = trade.get("transaction_code", "")
    price = stock.get("price", 0)
    low   = stock.get("52w_low", 0)
    high  = stock.get("52w_high", 0)

    if not price or not high or not low:
        return ""

    pct_from_high = (high - price) / high if high else 1

    if code == "P" and pct_from_high < 0.05:
        return "Buying near 52-week HIGH — strong conviction signal"
    if code == "S" and price <= low * 1.05:
        return "Selling near 52-week LOW — unusual bearish signal"
    return ""


# ── PROCESS A SINGLE FILING ───────────────────────────────────────────────────

def process_filing(filing: dict, last_post_time: float = 0) -> bool:
    """
    Full pipeline for one Form 4 filing.
    Returns True if a tweet was posted.
    """
    log.info(f"Processing: {filing['title']}")

    # 1. Fetch XML detail
    xml_content = fetch_filing_xml(filing["url"])

    # 2. Fetch company metadata
    company = fetch_company_data(filing.get("cik", ""))

    # 3. Parse with Claude
    trade = parse_filing(filing["title"], xml_content, company, xml_content=xml_content)
    if not trade:
        log.info("  → SKIP: could not parse")
        return False

    # 3b. Override shares data with direct XML parse — aggregates ALL rows including
    # multi-date transactions (e.g. insider buys over Mar 10 + Mar 11, files once)
    xml_data = parse_transactions_from_xml(xml_content)
    if xml_data:
        log.info(f"  XML override: {xml_data.get('shares_traded')} shares, ${xml_data.get('total_value'):,.0f} value")
        trade["shares_traded"]       = xml_data.get("shares_traded",       trade.get("shares_traded", 0))
        trade["price_per_share"]     = xml_data.get("price_per_share",     trade.get("price_per_share", 0))
        trade["total_value"]         = xml_data.get("total_value",         trade.get("total_value", 0))
        trade["shares_owned_after"]  = xml_data.get("shares_owned_after",  trade.get("shares_owned_after", 0))
        trade["shares_owned_before"] = xml_data.get("shares_owned_before", trade.get("shares_owned_before", 0))
        trade["transaction_code"]    = xml_data.get("transaction_code",    trade.get("transaction_code", ""))
        # Store date range for tweet (e.g. "Mar 10-11" if multi-day)
        if xml_data.get("transaction_date"):
            trade["transaction_date"] = xml_data["transaction_date"]
        if xml_data.get("transaction_date_end"):
            trade["transaction_date_end"] = xml_data["transaction_date_end"]

    # Fill in company data gaps
    if not trade.get("ticker") and company.get("ticker"):
        trade["ticker"] = company["ticker"]
    if not trade.get("company_name") and company.get("name"):
        trade["company_name"] = company["name"]
    # Always use filed_date from SEC feed (more reliable than Claude's parse)
    trade["filed_date"] = filing.get("filed_date", "")

    # 4. Classify tier
    tier   = classify_insider_tier(trade.get("insider_title", ""))
    ticker = trade.get("ticker", "")

    # 5a. Quick checks before fetching market data
    code = trade.get("transaction_code", "")
    if code in config.SKIP_TRANSACTION_CODES:
        log.info(f"  → SKIP: Transaction type {code} filtered")
        return False

    # Skip derivatives — swaps, options not held, synthetic positions
    if trade.get("is_derivative", False):
        log.info(f"  → SKIP: Derivative transaction (is_derivative=True)")
        return False

    if code == "M" and not trade.get("held_after_exercise", False):
        log.info(f"  → SKIP: Option exercise, shares not held")
        return False

    # Skip pre-planned 10b5-1 sales
    if trade.get("is_10b51_plan") or trade.get("is_10b51") and code == "S":
        log.info(f"  → SKIP: Pre-planned 10b5-1 sale — no signal")
        return False

    # 5c. Stale trade filter — skip if trade was filed more than N business days late
    tx_date_str    = trade.get("transaction_date", "")
    filed_date_str = trade.get("filed_date", "") or filing.get("filed_date", "")
    if tx_date_str and filed_date_str:
        try:

            tx_dt    = date.fromisoformat(tx_date_str[:10])
            filed_dt = date.fromisoformat(filed_date_str[:10])
            bdays = 0
            cur = tx_dt
            while cur < filed_dt:
                cur += timedelta(days=1)
                if cur.weekday() < 5:
                    bdays += 1
            if bdays > config.MAX_FILING_LAG_BDAYS:
                log.info(f"  → SKIP: Stale filing — {bdays} business days between trade and filing")
                return False
        except Exception:
            pass

    # 6. Fetch market data (needed for size-based threshold)
    stock       = fetch_stock_price(ticker)
    short_int   = fetch_short_interest(ticker)
    next_earn   = fetch_next_earnings(ticker)

    # Save ALL non-derivative code P purchases to history before threshold filter
    if code == "P":
        save_trade(trade)
        log.info(f"  → History saved: {trade.get('insider_name','?')} | ${trade.get('total_value',0):,.0f}")

    # 5b. Size-based dollar threshold
    market_cap = stock.get("market_cap", 0)
    passes, skip_reason = meets_threshold(trade, tier, market_cap)
    if not passes:
        log.info(f"  → SKIP: {skip_reason}")
        return False

    # 7. Get insider history
    history     = get_insider_history(ticker, trade.get("insider_name", ""))
    log.info(f"  → History: {len(history.get('trades',[]))} prior trades | unusual={history.get('unusual',False)} | days={history.get('months_since_last',999)}")
    unusual     = history.get("unusual", False)
    consec_buys = history.get("consecutive_buys", 0)

    # 8. Check analyst divergence
    analyst_div = check_analyst_divergence(trade, stock)

    # Inject short interest into stock dict so scorer can access it
    stock["short_interest"] = short_int

    # 10. Check cluster BEFORE scoring so cluster_count feeds into score
    cluster_data  = record_trade_for_cluster(trade)
    cluster_flag  = cluster_data is not None
    cluster_count = 0
    if cluster_data:
        trades_in_window = cluster_data.get("trades", [])
        cluster_count = len(set(t.get("insider", "") for t in trades_in_window))
    history["cluster_count"] = cluster_count

    # 9. Score signal (now includes cluster_count via history)
    score, reason = score_signal(trade, stock, history, next_earn)
    log.info(f"  Score: {score}/10")

    # 11. Build tweet
    tweet = build_tweet(
        trade            = trade,
        stock            = stock,
        tier             = tier,
        signal_score     = score,
        signal_reason    = reason,
        short_interest   = short_int,
        next_earnings    = next_earn,
        unusual_flag     = unusual,
        consecutive_buys = consec_buys,
        analyst_divergence = analyst_div,
        cluster_flag     = cluster_count if cluster_count >= 2 else cluster_flag,
    )

    # (history already saved before threshold check above)

    # Score gate — different thresholds for buys vs sells
    is_sell = code == "S"
    min_score = config.MIN_SCORE_SELL if is_sell else config.MIN_SCORE_BUY
    if score < min_score:
        log.info(f"  → SKIP: Score {score}/10 below threshold ({min_score}/10 for {'sells' if is_sell else 'buys'})")
        return False

    # 13. Post to X (or save for manual posting in dry run mode)
    if config.DRY_RUN:
        # Save tweet to pending file for manual posting
        os.makedirs("data", exist_ok=True)
        with open("data/pending_tweets.txt", "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] Signal: {score}/10 | ${ticker}\n")
            f.write(f"{'='*60}\n")
            f.write(tweet + "\n")
        log.info(f"  → [DRY RUN] Tweet saved to data/pending_tweets.txt")
        log.info(f"  → COPY AND POST MANUALLY: ${ticker} | {trade.get('insider_name')} | Signal {score}/10")
        print(f"\n{'='*60}")
        print(f"📋 NEW TWEET READY TO POST MANUALLY:")
        print(f"{'='*60}")
        print(tweet)
        print(f"{'='*60}\n")
        tweet_id = "dry_run"
    else:
        tweet_id = post_tweet(tweet)
        if not tweet_id:
            return False

    # Post cluster alert if triggered
    if cluster_data and not config.DRY_RUN:
        time.sleep(2)
        cluster_tweet = generate_cluster_alert(
            cluster_data["ticker"],
            cluster_data["company"],
            cluster_data["trades"],
        )
        if cluster_tweet:
            post_tweet(cluster_tweet, reply_to_id=tweet_id)

    # 14. Queue followups (history already saved above)
    add_to_followup_queue(trade, tweet_id=tweet_id)
    log_daily_trade(trade, score)
    trade["unusual_flag"] = unusual
    trade["stock_52w_high"] = stock.get("52w_high", 0)
    trade["stock_52w_low"] = stock.get("52w_low", 0)
    trade["stock_price"] = stock.get("price", 0)
    save_to_web_feed(trade, score, cluster_count)

    log.info(f"  → {'[DRY RUN] ' if config.DRY_RUN else ''}POSTED: ${ticker} | {trade.get('insider_name')} | Signal {score}/10")
    return True


# ── FOLLOWUP PROCESSOR ────────────────────────────────────────────────────────

def process_followups():
    due = get_due_followups()
    for item in due:
        trade    = item["trade"]
        days     = item["days"]
        ticker   = trade.get("ticker", "")

        if not ticker:
            mark_followup_posted(item)
            continue

        # Skip if a followup already posted for this trade at an earlier interval
        if item.get("prior_followup_posted"):
            mark_followup_posted(item)
            log.info(f"  → FOLLOWUP skipped: prior followup already posted for ${ticker}")
            continue

        stock         = fetch_stock_price(ticker)
        current_price = stock.get("price", 0)

        if not current_price:
            mark_followup_posted(item)
            continue

        entry_price = trade.get("price_per_share", 0)
        if not entry_price:
            mark_followup_posted(item)
            continue

        change_pct = ((current_price - entry_price) / entry_price) * 100

        # Determine whether to post
        is_up_10   = change_pct >= 10.0
        is_down_20 = change_pct <= -20.0 and days == 90

        if not is_up_10 and not is_down_20:
            mark_followup_posted(item)
            log.info(f"  → FOLLOWUP skipped: ${ticker} {change_pct:+.1f}% — threshold not met at {days} days")
            continue

        tweet = generate_followup_tweet(trade, current_price, days, change_pct)
        if tweet:
            original_tweet_id = item.get("original_tweet_id")
            if original_tweet_id and original_tweet_id != "dry_run":
                post_tweet(tweet, reply_to_id=original_tweet_id)
            else:
                post_tweet(tweet)
            time.sleep(2)
            # Mark all intervals for this trade done — only one followup per trade
            mark_all_followups_done(item)
            log.info(f"  → FOLLOWUP posted: ${ticker} {change_pct:+.1f}% at {days} days")
        else:
            mark_followup_posted(item)


# ── DIGEST SCHEDULER ─────────────────────────────────────────────────────────

last_daily_digest_date  = None
last_weekly_digest_date = None


def maybe_post_digests():
    global last_daily_digest_date, last_weekly_digest_date
    now = datetime.now(timezone.utc)

    # Daily digest at 6PM ET (23 UTC), Monday–Friday only
    if (now.hour == config.DIGEST_HOUR_UTC and
            now.weekday() < 5 and
            now.date() != last_daily_digest_date):
        trades = get_last_24h_trades()
        if trades:
            digest = generate_daily_digest(trades, total_scanned=get_daily_scan_count())
            if digest:
                if config.DRY_RUN:
                    print(f"\n📊 DAILY DIGEST READY TO POST:\n{digest}\n")
                    log.info("  → [DRY RUN] Daily digest saved for manual posting")
                else:
                    post_tweet(digest)
                    log.info("  → Daily digest posted")
        last_daily_digest_date = now.date()

    # Weekly digest on configured day
    if (now.weekday() == config.WEEKLY_DIGEST_DAY and
            now.hour == config.DIGEST_HOUR_UTC and
            now.date() != last_weekly_digest_date):
        trades = get_week_trades()
        if trades:
            digest = generate_weekly_digest(trades)
            if digest:
                post_tweet(digest)
                log.info("  → Weekly digest posted")
        last_weekly_digest_date = now.date()


# ── MAIN LOOP ────────────────────────────────────────────────────────────────

def _seed_volume_data():
    """Log the actual data directory being used."""
    import config as _cfg
    log.info(f"  → Data directory: {_cfg._DATA_DIR}")
    import os
    app_data_exists = os.path.exists("/app/data"); log.info(f"  → /app/data exists: {app_data_exists}")
    log.info(f"  → trade_history.json path: {_cfg.TRADE_HISTORY_FILE}")


def main():
    log.info("🚀 Form4Wire starting...")
    _seed_volume_data()
    if config.DRY_RUN:
        log.info("⚠️  DRY RUN MODE — tweets will NOT be posted to X")
        log.info("   Pending tweets saved to: data/pending_tweets.txt")
        log.info("   Set DRY_RUN = False in config.py to go fully live")
        print("\n" + "="*60)
        print("⚠️  DRY RUN MODE ACTIVE")
        print("   Bot is running but NOT posting to X")
        print("   New tweets will print here AND save to:")
        print("   data/pending_tweets.txt")
        print("   Post them manually to @Form4Wire")
        print("="*60 + "\n")
    else:
        log.info("✅ LIVE MODE — posting to X automatically")
    log.info(f"   Poll interval: {config.POLL_INTERVAL_SECONDS}s")
    log.info(f"   Min between posts: {config.MIN_SECONDS_BETWEEN_POSTS//60} min")

    seen = load_seen()

    # On startup, build a set of "ticker:insider" keys from trades posted
    # in the last 24 hours. Used to prevent reposts after Railway restarts.
    recent_trades = get_last_24h_trades()
    recently_posted = set()
    for t in recent_trades:
        ticker  = t.get("ticker", "")
        name    = t.get("name", "") or t.get("insider_name", "")
        if ticker:
            recently_posted.add(f"{ticker}:{name}".lower())
    if recently_posted:
        log.info(f"  Startup: {len(recently_posted)} ticker/insider combos posted in last 24h — will skip reposts")

    last_post_time = 0  # Unix timestamp of last tweet posted
    first_poll = True  # On first poll, mark all existing filings as seen without processing

    while True:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        log.info(f"[{now_str}] Checking SEC EDGAR...")

        try:
            filings = fetch_form4_feed()
            new     = [f for f in filings if f["id"] not in seen]

            if first_poll:
                log.info(f"  First poll — {len(new)} new filings to process (volume persists seen_filings)")
                first_poll = False
                save_seen(seen)

            if new:
                log.info(f"  {len(new)} new filing(s) found")
                for filing in new:
                    # Skip if this ticker was already posted in last 24h (restart dedup guard)
                    title_lower = filing.get("title", "").lower()
                    skip_repost = any(
                        rp.split(":")[0] in title_lower
                        for rp in recently_posted
                        if rp.split(":")[0]
                    )
                    if skip_repost:
                        log.info(f"  → SKIP: Recently posted ticker found in filing — restart dedup")
                        seen.add(filing["id"])
                        continue
                    # Check cooldown before processing
                    elapsed = time.time() - last_post_time
                    if last_post_time > 0 and elapsed < config.MIN_SECONDS_BETWEEN_POSTS:
                        wait = int(config.MIN_SECONDS_BETWEEN_POSTS - elapsed)
                        log.info(f"  → Cooldown active — {wait}s remaining, breaking filing loop")
                        seen.add(filing["id"])
                        save_seen(seen)
                        break
                    posted = process_filing(filing, last_post_time)
                    seen.add(filing["id"])
                    increment_daily_scan(1)
                    if posted:
                        last_post_time = time.time()
                        log.info(f"  → Next post allowed after {config.MIN_SECONDS_BETWEEN_POSTS//60} min cooldown")
                    time.sleep(1)
                save_seen(seen)
            else:
                log.info("  No new filings.")
                save_seen(seen)

            # Check followups
            process_followups()

            # Check digests
            maybe_post_digests()

        except KeyboardInterrupt:
            log.info("Bot stopped by user.")
            break
        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)

        time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
