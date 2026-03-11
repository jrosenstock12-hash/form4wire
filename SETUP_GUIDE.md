# 📈 Form4Wire — Complete Setup & Growth Guide

---

## 🏷️ Recommended Account Name: @Form4Wire

**Why this name wins:**
- Memorable and descriptive
- "Flow" implies real-time, constant stream of data
- Easy to search, tag, and remember
- Professional enough for financial media to cite you

**Backup names if taken:**
- @Form4Flow
- @Form4WireLive  
- @SECForm4Wire
- @InsiderAlertBot

---

## 📊 What This Bot Posts

Every tweet includes ALL of the following where available:

**Core Data**
- 🟢/🔴 Buy or Sell direction
- 🔥⭐📌 Insider tier (Tier 1 = CEO/CFO/COO, Tier 2 = EVP/SVP, Tier 3 = Directors)
- Insider name and exact title
- Shares traded, price per share, total value
- % of holdings sold/bought (stake context)

**Signal Quality Flags**
- ✅ Whether it's an open market trade vs pre-planned 10b5-1 sale
- 🚨 Unusual activity (no trades in 12+ months)
- 🔁 Consecutive buy streak (3+ in a row = conviction)
- 👥 Cluster alert (3+ insiders at same company trading same direction)

**Market Context**
- ⚡ Short interest % (explosive combo when insider buys high-shorted stock)
- 52-week price range
- Next earnings date
- Market cap tier (Mega/Large/Mid/Small cap)
- Analyst vs insider divergence flags

**AI Signal Score**
- 💡 1-10 signal strength score with one-line reasoning

**Scheduled Posts**
- 📊 Daily digest at 3PM ET (top buy, top sell, top signal)
- 📊 Weekly roundup every Friday (sector rotation, biggest moves)
- 📈 30/60/90-day followup posts showing if trades were profitable

---

## ✅ STEP-BY-STEP SETUP

---

### PHASE 1: Create Your X Account (Day 1)

**Step 1: Create the account**
1. Go to **x.com** and click "Sign up"
2. Use an email address — create a new one like `support@form4wire.com` if you want separation
3. Username: **@Form4Wire** (or your backup name)
4. Password: Use a strong, unique password and save it in a password manager

**Step 2: Complete your profile immediately**
This matters for both the algorithm and API approval.

- **Profile photo:** Use a clean financial chart or stock ticker graphic. 
  Free option: Go to **canva.com**, search "finance logo", customize one in 5 minutes.
  Make it dark background with green/red color scheme (familiar to traders)

- **Header/banner:** Create a 1500×500px banner on Canva showing:
  - "@Form4Wire" in large text
  - "Real-time SEC Form 4 insider trading alerts"
  - A subtle stock chart background

- **Bio (160 chars max):**
  ```
  🔴🟢 Real-time SEC insider trading alerts | Form 4 filings posted within minutes | 
  Signal scored 1-10 | All public companies | No fluff, pure data
  ```

- **Location:** New York, NY (financial credibility)

- **Website:** Leave blank for now, add later when you have a landing page

**Step 3: Verify your account**
- Confirm your email
- Add and verify your phone number (required for API access)
- Turn on 2-factor authentication

**Step 4: Initial activity before applying for API**
Do this for 3-5 days before applying for developer access:
- Follow 50-100 accounts: @SEC_Enforcement, financial journalists, $AAPL $NVDA $TSLA
- Like and retweet 10-15 posts about insider trading, SEC filings, stock market
- Post 5-10 manual tweets like:
  - "Setting up a real-time insider trading alert bot. $NVDA insiders have been active lately. 👀 #InsiderTrading"
  - "Form 4 filings are public but nobody's watching them in real time. About to change that."
  - "Did you know corporate insiders have to report stock trades within 2 business days? SEC Form 4. The data is all public — most people just don't know where to look."

---

### PHASE 2: Get X Developer Access (Day 3-5)

**Step 1: Apply at developer.x.com**
1. Go to **developer.x.com**
2. Click "Sign in" — use your @Form4Wire account
3. Click "Sign up for Free Account"

**Step 2: Fill out the application**
When asked about your use case, write this (copy/paste and personalize):

> "I am building a personal automation tool that monitors the SEC EDGAR database for new Form 4 insider trading filings and automatically posts structured alerts to my own X account (@Form4Wire). The bot extracts publicly available data from SEC filings — including insider name, title, shares traded, and transaction type — formats it into concise informational tweets, and posts them in real time. This is informational/educational content about publicly available regulatory filings. I will only be posting to my own account and not scraping X data."

**Step 3: Create your project and app**
1. Once approved (usually same day), click "Create Project"
2. Name it: "Form4Wire"
3. Use case: "Making a bot"
4. Create an app within the project

**Step 4: Set permissions BEFORE generating tokens**
⚠️ This is critical — do this BEFORE you generate your access tokens.
1. Go to your app's settings
2. Find "User authentication settings" 
3. Click Edit
4. Set App permissions to: **Read and Write**
5. App type: **Web App, Automated App or Bot**
6. Callback URI: `http://localhost`
7. Website URL: `http://localhost`
8. Save

**Step 5: Generate your credentials**
Go to "Keys and Tokens" tab:
- Copy **API Key** → this is your X_API_KEY
- Copy **API Key Secret** → this is your X_API_SECRET
- Click "Generate" under Access Token and Secret
- Copy **Access Token** → this is your X_ACCESS_TOKEN
- Copy **Access Token Secret** → this is your X_ACCESS_TOKEN_SECRET

Save all 4 in a secure notes app. You won't see them again.

---

### PHASE 3: Get Your Anthropic API Key (Day 1)

1. Go to **console.anthropic.com**
2. Sign up with your email
3. Click **"API Keys"** in the left menu
4. Click **"Create Key"** — name it "Form4Wire"
5. Copy the key (starts with `sk-ant-api03-...`)
6. Add a payment method
   - The bot uses Claude Haiku for parsing (~$0.25/million tokens)
   - Estimated monthly cost: **$2–8/month** depending on filing volume
   - Claude Sonnet is used only for weekly digests (minimal usage)

---

### PHASE 4: Install and Run the Bot (Day 5)

**Step 1: Install Python**
- Mac: Open Terminal, type `python3 --version`
  - If not installed: go to python.org and download Python 3.11+
- Windows: Download from python.org, check "Add to PATH" during install

**Step 2: Download the bot files**
Save all bot files in a folder called `form4wire` on your computer.

**Step 3: Open Terminal (Mac) or Command Prompt (Windows)**
Navigate to your folder:
```bash
cd Desktop/form4wire
```

**Step 4: Install required packages**
```bash
pip install -r requirements.txt
```

**Step 5: Set up your credentials**
```bash
cp .env.example .env
```
Then open `.env` in any text editor (Notepad, TextEdit, VS Code) and fill in all 5 values:
```
X_API_KEY=AbCdEfGhIjKlMnOpQrStUv
X_API_SECRET=AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEfGhIjKlMnOpQrStUv
X_ACCESS_TOKEN=1234567890-AbCdEfGhIjKlMnOpQrStUvWxYz
X_ACCESS_TOKEN_SECRET=AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEfGhIjKlMnOpQrStUv
ANTHROPIC_API_KEY=sk-ant-api03-...
```

**Step 6: Run the bot**
```bash
bash run.sh
```

You'll see:
```
🚀 Form4Wire starting...
   Poll interval: 45s
   Tier 1 threshold: $25,000
[14:32:01 UTC] Checking SEC EDGAR...
  No new filings.
[14:32:46 UTC] Checking SEC EDGAR...
  1 new filing(s) found
[X] ✅ Posted (id=1234567890):
🟢 INSIDER BUY — $NVDA...
```

---

### PHASE 5: Run It 24/7

Your laptop can't run this forever. You need a cloud server.

**Easiest option: Railway.app (Free to start)**
1. Go to **railway.app** and sign up with GitHub
2. Create a new project → "Deploy from GitHub repo"
   - First: put your bot files on GitHub (github.com → new repo → upload files)
   - DO NOT upload your `.env` file to GitHub
3. In Railway dashboard, go to your service → "Variables"
4. Add each of your 5 credentials as variables
5. Set the start command: `bash run.sh`
6. Deploy — it runs forever automatically

**Alternative: DigitalOcean ($6/month, most reliable)**
1. Sign up at digitalocean.com
2. Create a Droplet: Ubuntu 22.04, Basic, $6/month plan
3. Connect via SSH (they give you instructions)
4. Upload your files: `scp -r form4wire/ root@YOUR_IP:~/`
5. SSH in, install Python packages, run:
   ```bash
   pip install -r requirements.txt
   nohup bash run.sh > logs/output.log 2>&1 &
   ```
6. It runs in the background even when you close your computer

---

## 🚀 GROWTH STRATEGY

### How Fast Can You Grow?

Realistic timeline for a well-executed account:
- **Month 1:** 100-500 followers (organic, establishing credibility)
- **Month 2-3:** 500-2,000 followers (if you boost posts — see below)
- **Month 6:** 5,000-15,000 followers (if content is high quality and consistent)
- **Year 1:** 20,000-50,000+ followers (with ads + viral posts)

The accounts doing this well have 50K-200K followers. That space is yours to take.

---

### Organic Growth Tactics (Free)

**1. Reply to big finance accounts**
When @FinancialTimes, @WSJ, @Bloomberg, or major finance influencers post about a company, reply with your insider data:
> "Worth noting — $TSLA's CFO just sold $8.2M in shares 14 days before this. Signal score 7/10. #InsiderTrading"

These replies can get thousands of impressions if the original tweet is big.

**2. Tag the company ticker in every post**
$AAPL $NVDA $TSLA — people search these, and your posts appear.

**3. Post at peak times**
Best times for financial Twitter:
- 8:00-9:30 AM ET (before market open — highest engagement)
- 12:00-1:00 PM ET (lunch)
- 4:00-5:00 PM ET (market close)

SEC filings come in throughout the day — your bot posts them instantly whenever they arrive. That's your edge.

**4. Create "best of" moments**
When a past insider trade plays out spectacularly (e.g. CEO bought stock, it went up 40%), post a follow-up celebrating it. People love accuracy.

**5. Engage with the finance community**
Like and reply to posts from accounts like:
- @unusual_whales (large insider trading account — study their format)
- @markets
- @financialjuice
- @wallstreetmemes

---

### Paid Growth (Ads & Boosts — Highly Recommended)

**Yes, you should absolutely boost posts and run ads.** Here's why and how:

**X Promote Mode ($99/month)**
- X automatically promotes all your posts
- Good for getting initial momentum
- Recommended for months 1-3

**Boosting Individual Posts**
Best posts to boost:
- High signal score trades (8-10/10)
- Cluster alerts (3+ insiders buying same stock)
- Unusual activity flags (first trade in 18 months)
- Big dollar trades ($5M+) by Tier 1 executives
- Your 30/60/90-day follow-ups that show accurate predictions

**Budget recommendation:**
- Start: $5-10/day on your best performing organic posts
- Month 2+: $20-30/day once you know which content format performs best
- Expected result: Each dollar spent on a good post can bring 5-15 new followers in the finance niche

**X Ads Manager (more control)**
1. Go to ads.x.com
2. Campaign objective: "Follower growth" or "Reach"
3. Target audience:
   - Interests: Investing, Stock Market, Finance, Trading
   - Keywords: "insider trading", "stocks", "investing", "SEC", "$AAPL", "$NVDA"
   - Follower lookalikes: @unusual_whales, @markets, @WSJ
4. Budget: $10-30/day
5. Ad format: Promote your best organic tweets

**Expected ROI:** In the finance niche, $300-500/month in ads can realistically add 3,000-8,000 targeted followers per month.

---

### Making Money: Realistic Timeline

**0-500 followers: Build and optimize**
- Focus on content quality and consistency
- No monetization yet, just growth

**500-2,000 followers: First revenue**
- Apply for X Premium ($8/month) — required for Creator Revenue Sharing
- You'll need 500 followers + 5M impressions in 3 months to qualify for revenue sharing
- Sign up for **Webull** affiliate ($50-200 per funded account) at webull.com/introducing
- Add affiliate link to your bio

**2,000-10,000 followers: Real income begins**
- X Creator Revenue: $50-300/month depending on engagement
- Affiliate commissions: $200-1,000/month if you have engaged followers
- Start a **free email newsletter** (Substack or Beehiiv) with your best weekly analysis — migrate followers there for more direct monetization

**10,000+ followers: Scale up**
- Sponsored posts: $200-1,000 per post from fintech companies
- Premium Discord or Substack newsletter: $10-20/month × subscribers
- Data partnership inquiries from hedge funds and fintech startups
- Course or ebook: "How to Read Insider Trading Data"

---

### Content Calendar (in addition to automated posts)

Post these manually 2-3x per week to build personality:

**Monday:** "Insider activity to watch this week" — list 3 tickers with recent significant trades

**Wednesday:** "Fun fact" educational post — "Did you know? Corporate insiders made $X billion in reported trades last year. Here's how to read the signals..."

**Friday:** Repost your weekly digest with your own commentary

**When a major stock moves big:** Check your feed and reply with "We called this — [insider name] bought $X million X days ago. Signal score was 9/10. 👀"

---

## 📊 Sample Tweets Your Bot Will Post

**Standard trade:**
```
🟢 INSIDER BUY — $NVDA
🔥 TIER 1 | Chief Executive Officer
👤 Jensen Huang
📦 50,000 shares @ $124.50
💰 Total: $6.2M
📊 2.1% of holdings | 2.3M shares remain
✅ NOT a planned sale
⚡ Short interest: 2.1%
📉 52w: $47.32 — $153.13
📅 Next earnings: May 28, 2025
💡 Signal: 9/10 — CEO open market buy, not pre-planned, buying near 52w high shows strong conviction
📅 Feb 24, 2025
#InsiderTrading #NVDA #Stocks
```

**Cluster alert:**
```
👥 CLUSTER ALERT — $META
3 insiders bought in 10 days
Total: $24.7M combined
CFO + 2 Board Members all buying
💡 Signal: 9/10
#InsiderTrading #META #ClusterBuy
```

**Followup post:**
```
📊 60-DAY FOLLOWUP — $AMZN
Andy Jassy (CEO) bought $5.2M at $178.40
Current price: $227.80
📈 +27.7% in 60 days
💰 That $5.2M buy is now worth $6.6M
#InsiderTrading #AMZN
```

---

## ⚠️ Important Notes

**Legal:** You are only sharing publicly available SEC data. This is 100% legal and is what financial data companies charge thousands for. You are not providing investment advice — add "Not financial advice" to your bio.

**SEC Rate Limits:** The bot is configured to be respectful of SEC's servers (45-second polling, small delays between requests). Do not reduce these delays or your IP may get temporarily blocked.

**X Rate Limits:** Free tier allows 1,500 tweets/month. On busy filing days (earnings season) you may approach this. If needed, upgrade to X Basic API ($100/month) for higher limits.

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---|---|
| `401 Unauthorized` from X | Regenerate Access Token after setting Read+Write permissions |
| `403 Forbidden` from X | App permissions not set to Read+Write |
| Bot posts nothing | Trades may be below thresholds — check logs/ folder |
| Parsing errors | Check ANTHROPIC_API_KEY is correct and has credit |
| SEC fetch errors | SEC may be temporarily slow — bot retries automatically |
| Empty ticker in tweets | SEC filing may lack ticker — bot skips these |

---

## 📁 File Structure
```
form4wire/
├── bot.py              # Main orchestrator
├── sec_fetcher.py      # SEC EDGAR data fetching
├── ai_parser.py        # Claude AI parsing & tweet generation  
├── data_store.py       # Persistent storage & tracking
├── x_poster.py         # X API posting
├── config.py           # All settings in one place
├── requirements.txt    # Python dependencies
├── run.sh              # Startup script
├── .env.example        # Credentials template
├── data/               # Auto-created: filing history, clusters, queue
└── logs/               # Auto-created: bot activity logs
```
