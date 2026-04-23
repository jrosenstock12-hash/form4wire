"""
rebuild_followup_queue.py — Rebuilds followup_queue.json from docs/trades.json
Run once locally: python3 rebuild_followup_queue.py
"""

import json
import os
from datetime import datetime, timezone, timedelta

TRADES_FILE        = "docs/trades.json"
FOLLOWUP_QUEUE_OUT = "data/followup_queue.json"
FOLLOWUP_DAYS      = [30, 60, 90]
INVALID_TICKERS    = {"N/A", "NA", "NONE", "UNKNOWN", "???", ""}

def main():
    if not os.path.exists(TRADES_FILE):
        print(f"ERROR: {TRADES_FILE} not found")
        return

    trades = json.load(open(TRADES_FILE))
    print(f"Found {len(trades)} posted trades in {TRADES_FILE}")

    queue = []
    now = datetime.now(timezone.utc)
    skipped = 0

    for trade in trades:
        posted_at_str = trade.get("posted_at", "")
        if not posted_at_str:
            skipped += 1
            continue
        try:
            posted_at = datetime.fromisoformat(posted_at_str.replace("Z", "+00:00"))
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
        except Exception:
            skipped += 1
            continue

        ticker      = (trade.get("ticker", "") or "").upper().strip()
        insider     = trade.get("insider_name", "")
        price       = trade.get("price_per_share", 0)
        tx_date     = trade.get("transaction_date", "")
        total_value = trade.get("total_value", 0)
        title       = trade.get("insider_title", "")

        # Skip invalid tickers or missing price
        if ticker in INVALID_TICKERS or not price:
            skipped += 1
            continue

        for days in FOLLOWUP_DAYS:
            due_dt = posted_at + timedelta(days=days)
            already_past = due_dt < (now - timedelta(days=3))

            queue.append({
                "due_date":              due_dt.isoformat(),
                "days":                  days,
                "posted":                already_past,
                "prior_followup_posted": False,
                "original_tweet_id":     "",
                "trade": {
                    "ticker":           ticker,
                    "insider_name":     insider,
                    "insider_title":    title,
                    "transaction_date": tx_date,
                    "price_per_share":  price,
                    "transaction_code": "P",
                    "is_buy":           True,
                    "total_value":      total_value,
                }
            })

    os.makedirs(os.path.dirname(FOLLOWUP_QUEUE_OUT), exist_ok=True)
    with open(FOLLOWUP_QUEUE_OUT, "w") as f:
        json.dump(queue, f, indent=2)

    pending = [i for i in queue if not i.get("posted")]
    print(f"\n✅ Written to {FOLLOWUP_QUEUE_OUT}")
    print(f"   Total entries: {len(queue)}")
    print(f"   Pending: {len(pending)}")
    print(f"   Already past/skipped: {len(queue) - len(pending)}")
    print(f"\nUpcoming pending followups:")
    for item in sorted(pending, key=lambda x: x["due_date"])[:15]:
        print(f"  {item['trade']['ticker']:6} | {item['days']}d | due {item['due_date'][:10]}")

if __name__ == "__main__":
    main()
