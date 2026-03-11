"""
x_poster.py — Handles all X (Twitter) API posting
"""

import os
import time
import tweepy
from dotenv import load_dotenv
load_dotenv()

client = tweepy.Client(
    consumer_key        = os.environ["X_API_KEY"],
    consumer_secret     = os.environ["X_API_SECRET"],
    access_token        = os.environ["X_ACCESS_TOKEN"],
    access_token_secret = os.environ["X_ACCESS_TOKEN_SECRET"],
    wait_on_rate_limit  = True,
)


def post_tweet(text: str, reply_to_id: str = None) -> str | None:
    """
    Post a tweet. Returns tweet ID on success, None on failure.
    Optionally reply to an existing tweet.
    """
    try:
        kwargs = {"text": text}
        if reply_to_id:
            kwargs["in_reply_to_tweet_id"] = reply_to_id

        response = client.create_tweet(**kwargs)
        tweet_id = response.data["id"]
        print(f"[X] ✅ Posted (id={tweet_id}):\n{text[:100]}...\n")
        return tweet_id

    except tweepy.TooManyRequests:
        print("[X] ⚠️  Rate limited — waiting 15 minutes...")
        time.sleep(900)
        return None
    except tweepy.Forbidden as e:
        print(f"[X] ❌ Forbidden: {e} — check Read+Write permissions")
        return None
    except tweepy.TweepyException as e:
        print(f"[X] ❌ Error: {e}")
        return None


def post_thread(tweets: list[str]) -> list[str]:
    """Post a series of tweets as a thread. Returns list of tweet IDs."""
    ids    = []
    prev   = None

    for text in tweets:
        tweet_id = post_tweet(text, reply_to_id=prev)
        if tweet_id:
            ids.append(tweet_id)
            prev = tweet_id
            time.sleep(1)   # Small delay between thread posts
        else:
            break

    return ids
