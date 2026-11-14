"""Application configuration (env-based, production-safe)."""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_TRUE_STRINGS = ("1", "true", "yes", "on")
_DEFAULT_CORS = "http://localhost:3000,http://localhost:3001"
# Access tokens are deliberately short-lived: they are validated against the
# user record (token_version / tokens_valid_after) but not against the
# revocation store, so a leaked token must expire quickly. Long-lived
# sessions come from refresh-token rotation, not from long access tokens.
_DEFAULT_JWT_EXPIRE_MINUTES = 30


def _parse_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return raw in _TRUE_STRINGS if raw else default


def _parse_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ── Environment ───────────────────────────────────────────────────────────────
ENV = os.getenv("ENV", "development").strip().lower()
IS_PRODUCTION = ENV == "production"
DEBUG = _parse_bool("DEBUG", default=not IS_PRODUCTION)
DISABLE_DOCS = _parse_bool("DISABLE_DOCS", default=IS_PRODUCTION)

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ORIGINS_STR = os.getenv("CORS_ORIGINS", _DEFAULT_CORS)
CORS_ORIGINS = [o.strip() for o in CORS_ORIGINS_STR.split(",") if o.strip()]

# ── JWT ───────────────────────────────────────────────────────────────────────
_JWT_ALGORITHM_RAW = (os.getenv("JWT_ALGORITHM", "HS256") or "HS256").strip()
JWT_ALGORITHM = "HS256" if _JWT_ALGORITHM_RAW.lower() == "none" else _JWT_ALGORITHM_RAW
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
JWT_EXPIRE_MINUTES = _parse_int("JWT_EXPIRE_MINUTES", _DEFAULT_JWT_EXPIRE_MINUTES)

# ── Auth bypass (dev / tests only) ────────────────────────────────────────────
ENABLE_DEV_AUTH_HEADER = _parse_bool("ENABLE_DEV_AUTH_HEADER", default=not IS_PRODUCTION)

# ── API Key guard ─────────────────────────────────────────────────────────────
API_KEY = os.getenv("API_KEY", "").strip()
REQUIRE_API_KEY = _parse_bool("REQUIRE_API_KEY", default=bool(API_KEY))

# ── AWS / DynamoDB ────────────────────────────────────────────────────────────
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", "").strip() or None

DYNAMODB_MAIN_TABLE = os.getenv("DYNAMODB_MAIN_TABLE", "ai-marketing-dev-main")
DYNAMODB_CONTENT_TABLE = os.getenv("DYNAMODB_CONTENT_TABLE", "ai-marketing-dev-content")
DYNAMODB_SCHEDULE_TABLE = os.getenv("DYNAMODB_SCHEDULE_TABLE", "ai-marketing-dev-schedule")
DYNAMODB_ANALYTICS_TABLE = os.getenv("DYNAMODB_ANALYTICS_TABLE", "ai-marketing-dev-analytics")
DYNAMODB_ACCOUNTS_TABLE = os.getenv("DYNAMODB_ACCOUNTS_TABLE", "ai-marketing-dev-accounts")

# ── File Storage ──────────────────────────────────────────────────────────────
STORAGE_DRIVER = os.getenv("STORAGE_DRIVER", "local").strip()  # local | s3 | r2
MEDIA_LOCAL_PATH = os.getenv("MEDIA_LOCAL_PATH", "./uploads").strip()
MEDIA_BASE_URL = os.getenv("MEDIA_BASE_URL", "http://localhost:8000/uploads").strip()
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "").strip()

# ── AI ────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

# ── Media Generation ──────────────────────────────────────────────────────────
HIGGSFIELD_API_KEY = os.getenv("HIGGSFIELD_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# ── Social Publishing ─────────────────────────────────────────────────────────
META_APP_ID = os.getenv("META_APP_ID", "").strip()
META_APP_SECRET = os.getenv("META_APP_SECRET", "").strip()
META_REDIRECT_URI = os.getenv("META_REDIRECT_URI", "http://localhost:8000/api/v1/integrations/meta/callback").strip()
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v21.0").strip()

TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
TIKTOK_REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", "http://localhost:8000/api/v1/integrations/tiktok/callback").strip()

# ── Paid Ads Platforms ────────────────────────────────────────────────────────
# Meta Ads reuses META_APP_ID / META_APP_SECRET (Marketing API scopes). Only the
# redirect differs so the ads-connect flow requests ads_read / ads_management.
META_ADS_REDIRECT_URI = os.getenv(
    "META_ADS_REDIRECT_URI", "http://localhost:8000/api/v1/integrations/meta_ads/callback"
).strip()

# Google Ads (OAuth2 + developer token; manager (MCC) id optional)
GOOGLE_ADS_CLIENT_ID = os.getenv("GOOGLE_ADS_CLIENT_ID", "").strip()
GOOGLE_ADS_CLIENT_SECRET = os.getenv("GOOGLE_ADS_CLIENT_SECRET", "").strip()
GOOGLE_ADS_DEVELOPER_TOKEN = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()
GOOGLE_ADS_LOGIN_CUSTOMER_ID = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip()
GOOGLE_ADS_REDIRECT_URI = os.getenv(
    "GOOGLE_ADS_REDIRECT_URI", "http://localhost:8000/api/v1/integrations/google_ads/callback"
).strip()

# TikTok Ads (separate app from TikTok content publishing)
TIKTOK_ADS_APP_ID = os.getenv("TIKTOK_ADS_APP_ID", "").strip()
TIKTOK_ADS_SECRET = os.getenv("TIKTOK_ADS_SECRET", "").strip()
TIKTOK_ADS_REDIRECT_URI = os.getenv(
    "TIKTOK_ADS_REDIRECT_URI", "http://localhost:8000/api/v1/integrations/tiktok_ads/callback"
).strip()

# Reddit Ads
REDDIT_ADS_CLIENT_ID = os.getenv("REDDIT_ADS_CLIENT_ID", "").strip()
REDDIT_ADS_CLIENT_SECRET = os.getenv("REDDIT_ADS_CLIENT_SECRET", "").strip()
REDDIT_ADS_REDIRECT_URI = os.getenv(
    "REDDIT_ADS_REDIRECT_URI", "http://localhost:8000/api/v1/integrations/reddit_ads/callback"
).strip()
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "ai-marketing-service/1.0").strip()

# How many days of ad insights to pull on each sync.
ADS_SYNC_LOOKBACK_DAYS = _parse_int("ADS_SYNC_LOOKBACK_DAYS", 30)

# ── Token encryption (OAuth tokens at rest) ───────────────────────────────────
# Fernet key (urlsafe base64, 32 bytes). Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# If unset, tokens are stored unencrypted (dev only — a warning is logged).
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "").strip()

# ── Billing (Stripe) ──────────────────────────────────────────────────────────
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

# Stripe Price IDs per plan / billing interval (create these in the Stripe dashboard).
STRIPE_PRICE_SOLO_MONTHLY = os.getenv("STRIPE_PRICE_SOLO_MONTHLY", "").strip()
STRIPE_PRICE_SOLO_ANNUAL = os.getenv("STRIPE_PRICE_SOLO_ANNUAL", "").strip()
STRIPE_PRICE_TEAM_MONTHLY = os.getenv("STRIPE_PRICE_TEAM_MONTHLY", "").strip()
STRIPE_PRICE_TEAM_ANNUAL = os.getenv("STRIPE_PRICE_TEAM_ANNUAL", "").strip()
STRIPE_PRICE_AGENCY_MONTHLY = os.getenv("STRIPE_PRICE_AGENCY_MONTHLY", "").strip()
STRIPE_PRICE_AGENCY_ANNUAL = os.getenv("STRIPE_PRICE_AGENCY_ANNUAL", "").strip()

# Stripe Price IDs for one-time AI credit top-up packs.
STRIPE_PRICE_CREDITS_STARTER = os.getenv("STRIPE_PRICE_CREDITS_STARTER", "").strip()
STRIPE_PRICE_CREDITS_GROWTH = os.getenv("STRIPE_PRICE_CREDITS_GROWTH", "").strip()
STRIPE_PRICE_CREDITS_PRO = os.getenv("STRIPE_PRICE_CREDITS_PRO", "").strip()
STRIPE_PRICE_CREDITS_AGENCY = os.getenv("STRIPE_PRICE_CREDITS_AGENCY", "").strip()

# Where Stripe redirects after checkout / billing-portal sessions.
_APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:3000").strip()
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", f"{_APP_BASE_URL}/dashboard/billing?status=success").strip()
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", f"{_APP_BASE_URL}/dashboard/billing?status=cancelled").strip()
STRIPE_PORTAL_RETURN_URL = os.getenv("STRIPE_PORTAL_RETURN_URL", f"{_APP_BASE_URL}/dashboard/billing").strip()

# ── Email ─────────────────────────────────────────────────────────────────────
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:3000").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", "noreply@example.com").strip()
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", EMAIL_FROM).strip()

# ── Rate Limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT = os.getenv("RATE_LIMIT", "100/minute").strip()
