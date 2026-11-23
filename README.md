# Meesho Telugu Fashion Channel Automation

**100% Free-Access ($0/month)** Python 3.11+ pipeline that automates a Telugu fashion channel across **YouTube Shorts** and **Instagram Reels/Posts** using trending Meesho ladies' collections.

## 🚀 Features

- **Meesho Product Catalog**: Refresh product details locally with Playwright, then import a validated weekly JSON catalog in GitHub Actions instead of scraping Meesho from the runner.
- **Strict Innerwear Exclusion**: Automatically filters out innerwear/lingerie/nightwear using comprehensive keyword list
- **Telugu Script Generation**: Google Gemini API Free Tier generates 25-28s scripts with hook, fabric/price details, dual CTA
- **Telugu TTS**: Edge TTS (`te-IN-ShrutiNeural`/`te-IN-MohanNeural`) - completely free, no API key
- **Video Builder**: Downloads product images, then streams 1080x1920 Telugu-subtitled Ken Burns frames through FFmpeg with optional BGM ducking
- **YouTube Shorts Upload**: OAuth 2.0 with quota management, affiliate links in description
- **Instagram Reels Upload**: Meta Graph API with public video URL (Cloudflare Tunnel), comment-to-DM webhook
- **FastAPI Server**: Serves media files + handles Instagram webhook for automated DM replies
- **APScheduler**: Continuous pipeline with rate limiting, dry-run mode, daily quotas
- **SQLite Database**: Local storage for products, published posts, DM logs, quotas

## 📁 Project Structure

```
meesho-telugu-fashion/
├── src/
│   ├── __init__.py
│   ├── config.py              # Configuration (pydantic-settings)
│   ├── database.py            # SQLite database layer
│   ├── extractor.py           # Meesho Playwright scraper
│   ├── product_catalog.py     # Validated weekly catalog import/export
│   ├── update_product_catalog.py # Local weekly catalog refresh command
│   ├── script_tts.py          # Gemini script + Edge TTS
│   ├── video_builder.py       # Pillow + FFmpeg video creation
│   ├── youtube_uploader.py    # YouTube Data API v3
│   ├── instagram_uploader.py  # Meta Graph API
│   ├── server_webhook.py      # FastAPI media server + webhook
│   ├── scheduler.py           # APScheduler pipeline
│   └── main.py                # CLI entry point
├── assets/
│   ├── bgm/                   # Royalty-free background music (.mp3/.wav)
│   └── fonts/                 # Noto Sans Telugu (auto-downloaded)
├── data/
│   └── pipeline.db            # SQLite database
├── output/                    # Generated videos
├── logs/                      # Application logs
├── tests/                     # Pytest unit tests
├── requirements.txt
├── .env.example
├── client_secret.json         # Google OAuth (you provide)
└── README.md
```

## 🛠 Prerequisites

1. **Python 3.11+**
2. **FFmpeg** installed and in PATH
3. **Playwright browsers**: `playwright install chromium`
4. **Google Cloud Project** with YouTube Data API v3 enabled
5. **Meta Developer Account** with Instagram Business account
6. **Cloudflare Tunnel** (`cloudflared`) for public HTTPS

## 📦 Installation

```bash
# Clone and enter directory
cd meesho-telugu-fashion

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright Chromium
playwright install chromium

# Verify FFmpeg
ffmpeg -version
```

## ⚙️ Configuration

### 1. Copy and edit environment file

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 2. Google Gemini API (Free Tier)

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Create API key
3. Add to `.env`: `GEMINI_API_KEY=your_key`

### 3. Google OAuth / YouTube Data API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create project → Enable **YouTube Data API v3**
3. OAuth Consent Screen: External, add test users (your email)
4. Credentials → Create **OAuth 2.0 Client ID** (Desktop app)
5. Download `client_secret.json` to project root
6. Run once to get refresh token:
   ```bash
   python -c "
   from google_auth_oauthlib.flow import InstalledAppFlow
   flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', ['https://www.googleapis.com/auth/youtube.upload'])
   creds = flow.run_local_server(port=0)
   print('Refresh token:', creds.refresh_token)
   "
   ```
7. Add to `.env`:
   ```
   GOOGLE_CLIENT_ID=your_client_id
   GOOGLE_CLIENT_SECRET=your_client_secret
   GOOGLE_REFRESH_TOKEN=your_refresh_token
   ```

### 4. Meta / Instagram Graph API

1. Go to [Meta Developers](https://developers.facebook.com/)
2. Create App → Business type
3. Configure the Instagram publishing and messaging products/use cases
4. Get **Page Access Token** (long-lived, 60 days) for your Instagram Business account
5. Get **Instagram Business Account ID** (`ig_user_id`)
6. Set up **Webhook**:
   - Callback URL: `https://your-tunnel.trycloudflare.com/webhook/instagram`
   - Verify Token: random string (add to `META_WEBHOOK_VERIFY_TOKEN`)
   - Subscribe to: `comments` field
   - Configure private replies with Meta's required `instagram_manage_comments`
     and `pages_messaging` permissions and a Page token with the required Page
     task/access. Private replies are limited to one message per comment and
     must be sent within Meta's allowed time window.
7. Add to `.env`:
   ```
   META_APP_ID=your_app_id
   META_APP_SECRET=your_app_secret
   META_PAGE_ACCESS_TOKEN=your_page_access_token
   META_PAGE_ID=your_facebook_page_id
   META_IG_USER_ID=your_ig_business_account_id
   META_WEBHOOK_VERIFY_TOKEN=your_random_token
   ```

### 5. Cloudflare Tunnel (Public HTTPS)

```bash
# Install cloudflared
# Windows: https://github.com/cloudflare/cloudflared/releases
# Mac: brew install cloudflared
# Linux: wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i cloudflared-linux-amd64.deb

# Run tunnel (in separate terminal)
cloudflared tunnel --url http://localhost:8000
```

Copy the `https://*.trycloudflare.com` URL to `.env`:
```
PUBLIC_BASE_URL=https://your-random-subdomain.trycloudflare.com
```

For GitHub Actions, do not rely on a tunnel started inside the workflow: the
runner stops after the job. Create a public-media R2 bucket, enable public
access through a custom domain, and create a bucket-scoped R2 API token with
object-write access. Configure `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, and `R2_PUBLIC_BASE_URL`. Set the
public base URL to the domain root; the application adds `R2_OBJECT_PREFIX`
when uploading videos.

Create a second, private R2 bucket for SQLite state. Use a separate bucket-scoped
token with object read/write access and configure `R2_STATE_BUCKET_NAME`,
`R2_STATE_ACCESS_KEY_ID`, and `R2_STATE_SECRET_ACCESS_KEY`. Do not enable public
access on the state bucket. Both tokens must stay private; only the media
bucket's custom domain is public.

### GitHub Actions automation

The workflow in `.github/workflows/publish.yml` runs daily at 03:23 UTC and can
also be started manually. Manual runs default to dry-run; scheduled runs publish.
First create a GitHub repository and push this project to its default branch;
this workspace is not currently connected to a Git remote. Then enable Actions
and add the following under **Repository → Settings → Secrets and variables →
Actions**.

| GitHub Actions secret | Provider/value |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio API key |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` | YouTube OAuth app and authorized refresh token |
| `META_PAGE_ACCESS_TOKEN`, `META_IG_USER_ID` | Meta app and Instagram professional account |
| `META_APP_SECRET`, `META_PAGE_ID`, `META_WEBHOOK_VERIFY_TOKEN` | Webhook-only Meta app secret, linked Page ID, and random verification token |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | R2 media-bucket scoped token |
| `R2_STATE_ACCESS_KEY_ID`, `R2_STATE_SECRET_ACCESS_KEY` | Separate private state-bucket scoped token |
| `MEESHO_AFFILIATE_ID` | Optional, if you have an affiliate account |
| `ADMIN_API_TOKEN` | Long random token protecting webhook-service admin endpoints |

Add these non-secret values under **Actions variables**:
`GEMINI_MODEL`, `META_GRAPH_API_VERSION`, `R2_BUCKET_NAME`,
`R2_PUBLIC_BASE_URL`, `R2_OBJECT_PREFIX`, `R2_STATE_BUCKET_NAME`,
`R2_STATE_OBJECT_KEY`, `MEESHO_AFFILIATE_BASE_URL`, and `WEBHOOK_PUBLIC_URL`
(only if deploying the webhook service).

The workflow restores SQLite state from the private R2 bucket, imports
`data/product_catalog.json`, then saves a consistent snapshot after the run.
GitHub-hosted runners may receive HTTP 403 from Meesho; the workflow does not
scrape Meesho and requires the catalog file to be committed to the repository.
Refresh it locally once a week from a network where Meesho is accessible:

```bash
python -m src.update_product_catalog --max-per-category 20
```

The command writes `data/product_catalog.json` only if it can extract at least
21 usable, unique, in-stock products with product images. It validates the
catalog structure, writes atomically, and sets a seven-day expiry. If extraction
fails or returns too few products, the previous JSON stays untouched. Review
and upload the generated file before the expiry; Actions rejects missing,
invalid, or expired catalogs. The workflow retains published/used product state
from SQLite when refreshing product details.

The standard standalone live extraction command remains available for local
diagnostics:

```bash
python -m src.main extract
```

Do not enable scheduled
publishing until you've verified each OAuth token and public video URL with a
manual dry-run and one explicitly approved live run.

The FastAPI webhook is not kept online by GitHub Actions. Deploy it separately
to an always-reachable HTTPS host and configure Meta's callback URL there. The
server needs `META_APP_SECRET`, `META_PAGE_ACCESS_TOKEN`, `META_PAGE_ID`,
`META_WEBHOOK_VERIFY_TOKEN`, `ADMIN_API_TOKEN`, `R2_ACCOUNT_ID`,
`R2_STATE_BUCKET_NAME`, `R2_STATE_OBJECT_KEY`, `R2_STATE_ACCESS_KEY_ID`, and
`R2_STATE_SECRET_ACCESS_KEY`. The scheduled workflow calls the protected
`/admin/state/reload` endpoint after each state snapshot; the webhook saves
DM deduplication changes back to the private bucket. Set `WEBHOOK_PUBLIC_URL`
only after the service is live.

The SQLite/R2 synchronization is a snapshot handoff, not a transactional shared
database: a webhook update that overlaps a pipeline run can be overwritten by
the pipeline's later snapshot. Avoid overlapping writers; for reliable
high-volume or concurrent use, migrate both services to a shared database.
A temporary `trycloudflare.com` tunnel is suitable only while its tunnel process
and your machine remain running; it is not an always-on hosted endpoint.

An eligible Cloud Run region has a monthly free compute allowance, but it
requires a Google Cloud billing account and usage beyond the allowance can
incur charges. See [Cloud Run pricing](https://cloud.google.com/run/pricing);
no always-on hosting option is guaranteed to remain free.

### 6. Background Music

Place royalty-free slow-melody tracks in `assets/bgm/`:
```bash
# Example sources:
# - https://pixabay.com/music/
# - https://freemusicarchive.org/
# - YouTube Audio Library
mkdir -p assets/bgm
# Add .mp3 or .wav files
```

## 🚀 Usage

### Initialize Project
```bash
python -m src.main init
```

### Run Extraction Only
```bash
python -m src.main extract
```

### Refresh Weekly Product Catalog
```bash
python -m src.update_product_catalog --max-per-category 20
```

### Generate Video for Next Product
```bash
python -m src.main generate
```

### Upload to YouTube Shorts
```bash
python -m src.main upload-youtube
```

### Upload to Instagram Reels
```bash
python -m src.main upload-instagram
```

### Run Full Pipeline Cycle (Dry Run)
```bash
python -m src.main pipeline --dry-run
```

### Run Continuous Scheduler
```bash
# Production
python -m src.main scheduler

# Dry run (testing)
python -m src.main scheduler --dry-run
```

### Run FastAPI Server (Media + Webhook)
```bash
# In one terminal
python -m src.main server

# In another terminal (for public HTTPS)
cloudflared tunnel --url http://localhost:8000
```

### Check Quota Status
```bash
python -m src.main quota
```

### View Statistics
```bash
python -m src.main stats
```

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_database.py -v
pytest tests/test_extractor.py -v
pytest tests/test_script_tts.py -v
pytest tests/test_video_builder.py -v
pytest tests/test_server_webhook.py -v
pytest tests/test_scheduler.py -v

# With coverage
pytest tests/ --cov=src --cov-report=html
```

## 📊 Pipeline Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Extractor  │────▶│ Script + TTS│────▶│Video Builder│
│ (Playwright)│     │ (Gemini+Edge)│     │(Pillow+FFmpeg)│
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                    ┌─────────────┐             │
                    │  YouTube    │◀────────────┤
                    │  Shorts     │             │
                    └─────────────┘             │
                                               ▼
                    ┌─────────────┐     ┌─────────────┐
                    │ Instagram   │◀────│ FastAPI     │
                    │  Reels      │     │ Server      │
                    └──────┬──────┘     │ + Webhook   │
                           │            └──────┬──────┘
                           ▼                   ▼
                    ┌─────────────────────────────────┐
                    │    SQLite Database              │
                    │ products | published | dms      │
                    └─────────────────────────────────┘
```

## 🔒 Security Notes

- **Never commit** `.env`, `client_secret.json`, `token.json`
- Use **long-lived tokens** for Meta (refresh before expiry)
- Webhook signature verification enabled by default
- DM deduplication prevents spam
- Daily upload quotas prevent API bans

## 📈 Rate Limits & Quotas

| Platform | Daily Limit | API Quota |
|----------|-------------|-----------|
| YouTube  | 5 uploads   | 10,000 units/day (1,600 per upload) |
| Instagram| 10 uploads  | 200 calls/hour per user |

Configure in `.env`:
```bash
MAX_YT_UPLOADS_PER_DAY=5
MAX_IG_UPLOADS_PER_DAY=10
POST_INTERVAL_MINUTES=15
```

## 🐛 Troubleshooting

### Playwright Issues
```bash
playwright install --with-deps chromium
```

### FFmpeg Not Found
```bash
# Windows: Add to PATH or use chocolatey: choco install ffmpeg
# Mac: brew install ffmpeg
# Linux: sudo apt install ffmpeg
```

### Font Issues (Telugu not rendering)
```bash
# Font auto-downloads to assets/fonts/
# Verify: ls assets/fonts/NotoSansTelugu-Regular.ttf
```

### Cloudflare Tunnel Not Accessible
- Ensure `PUBLIC_BASE_URL` in `.env` matches the tunnel URL exactly
- Check firewall allows port 8000
- Try `cloudflared tunnel --url http://localhost:8000 --no-autoupdate`

### Meta Webhook Not Receiving Events
- Verify webhook URL is HTTPS (Cloudflare Tunnel)
- Check Verify Token matches
- Ensure `comments` field is subscribed
- Check Meta App Dashboard → Webhooks for delivery errors

## 📝 License

MIT License - Free for personal and commercial use.

## 🤝 Contributing

1. Fork the repository
2. Create feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit PR

## 📞 Support

For issues, check:
1. Logs in `logs/pipeline.log`
2. Database in `data/pipeline.db`
3. Quota status: `python -m src.main quota`
4. Health check: `GET http://localhost:8000/health`

---

**Built with ❤️ for Telugu fashion creators**  
*100% free, no paid APIs required*