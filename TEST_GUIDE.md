# 🧪 Form4Wire — Test Mode Quick Start

Run the full pipeline with REAL SEC data and REAL Claude AI parsing
— without needing an X account yet.

---

## What You Need

- A computer with Python installed
- Your Anthropic API key ONLY (no X credentials needed yet)

---

## Step 1 — Install Python (if not already installed)

**Mac:**
Open Terminal (search "Terminal" in Spotlight) and type:
```
python3 --version
```
If you see a version number, you're good.
If not, go to **python.org/downloads** and install Python 3.11 or newer.

**Windows:**
Go to **python.org/downloads**, download the installer.
⚠️ During install, CHECK the box that says "Add Python to PATH"

---

## Step 2 — Get Your Anthropic API Key

1. Go to **console.anthropic.com**
2. Sign up with your email
3. Click **"API Keys"** in the left sidebar
4. Click **"Create Key"** — name it anything
5. Copy the key — it starts with `sk-ant-api03-...`
6. Click **"Billing"** and add a credit card
7. Add $10 credit — this test will cost less than $0.10

---

## Step 3 — Set Up the Bot Files

1. Download and unzip the bot files into a folder called `form4wire`

2. Open Terminal (Mac) or Command Prompt (Windows)

3. Navigate to the folder:
```
cd Desktop/form4wire
```
(adjust path to wherever you saved it)

4. Install required packages:
```
pip install -r requirements.txt
```

5. Create your credentials file:
```
cp .env.example .env
```

6. Open `.env` in any text editor and add your Anthropic key:
```
X_API_KEY=
X_API_SECRET=
X_ACCESS_TOKEN=
X_ACCESS_TOKEN_SECRET=
ANTHROPIC_API_KEY=sk-ant-api03-YOUR-KEY-HERE
```
Leave the X lines blank for now — test mode doesn't need them.

---

## Step 4 — Run the Test

```
python test_mode.py
```

To test more filings (default is 10):
```
python test_mode.py 20
```

---

## Step 5 — Review the Results

When it finishes, open this file:
```
data/test_output.txt
```

You'll see every tweet that WOULD have been posted, formatted exactly
as it would appear on X. For example:

```
============================================================
TRADE #1 | Signal: 8/10 | Tier 1 | $NVDA
============================================================
🟢 INSIDER BUY — $NVDA
🔥 TIER 1 | Chief Executive Officer
👤 Jensen Huang
📦 50,000 shares @ $124.50
💰 Total: $6.2M
📊 2.1% of holdings | 2.3M shares remain
✅ NOT a planned sale
⚡ Short interest: 2.1%
💡 Signal: 8/10 — CEO open market buy, strong conviction
📅 Feb 20, 2025
#InsiderTrading #NVDA #Stocks

[Tweet length: 241 chars]
```

For full structured data on every trade, open:
```
data/test_output.json
```

---

## What to Look For

✅ Good signs:
- Tweets look clean and well formatted
- Signal scores make intuitive sense
- Tier 1 executives are correctly identified
- Dollar values and share counts look accurate
- Tweet length stays under 280 chars

⚠️ Things to check:
- Any tweets where the ticker shows as "???" (parsing issue)
- Any trades where total value seems wrong
- Signal scores that seem too high or too low for the trade

---

## What Happens Next

Once you're happy with the output quality:
1. Create your X account (@Form4Wire or similar)
2. Get X developer credentials
3. Add them to your .env file
4. Switch from `python test_mode.py` to `bash run.sh`
5. Go live

The test mode and live mode use identical parsing logic —
the only difference is test saves to a file, live posts to X.
