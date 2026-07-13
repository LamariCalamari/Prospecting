"""Central configuration. All secrets come from environment variables.

Nothing here should ever contain a hardcoded API key. Values are read from
the process environment, which `python-dotenv` populates from a local `.env`
file during development (see `.env.example`).
"""
import os

from dotenv import load_dotenv

load_dotenv()  # loads .env into os.environ if present; no-op in production

# On Streamlit Community Cloud there is no .env file — keys are entered in the
# app's Secrets manager instead. Read those as a fallback so the same config
# works locally (via .env) and when deployed (via st.secrets), no edits needed.
try:  # streamlit may be absent (e.g. running tests) or have no secrets file
    import streamlit as st

    _SECRETS = dict(st.secrets)
except Exception:  # noqa: BLE001 - any failure just means "no secrets available"
    _SECRETS = {}


def _cfg(key: str, default: str = "") -> str:
    """Config value from the environment, falling back to Streamlit secrets."""
    val = os.getenv(key)
    if val is None:
        val = _SECRETS.get(key, default)
    return str(val).strip()


# --- Companies House --------------------------------------------------------
COMPANIES_HOUSE_API_KEY = _cfg("COMPANIES_HOUSE_API_KEY")
COMPANIES_HOUSE_BASE_URL = "https://api.company-information.service.gov.uk"

# --- Wikipedia --------------------------------------------------------------
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_REST_URL = "https://en.wikipedia.org/api/rest_v1"

# --- Wikidata (free, no key) ------------------------------------------------
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"

# --- The Gazette (official public record, free, no key) ---------------------
GAZETTE_DATA_URL = "https://www.thegazette.co.uk/all-notices/notice/data.json"

# --- FCA Financial Services Register (free key + email) ---------------------
FCA_API_BASE_URL = "https://register.fca.org.uk/services/V0.1"
FCA_API_EMAIL = _cfg("FCA_API_EMAIL")
FCA_API_KEY = _cfg("FCA_API_KEY")

# --- Charity Commission (free subscription key) -----------------------------
CHARITY_COMMISSION_API_BASE = "https://api.charitycommission.gov.uk/register/api"
CHARITY_COMMISSION_API_KEY = _cfg("CHARITY_COMMISSION_API_KEY")

# --- OpenSanctions (PEP/sanctions screening; free tier needs a key) ---------
OPENSANCTIONS_API_URL = "https://api.opensanctions.org"
OPENSANCTIONS_API_KEY = _cfg("OPENSANCTIONS_API_KEY")

# --- Google News RSS (free, no key) -----------------------------------------
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
# Region/language for results. GB + English gives UK-weighted coverage.
NEWS_HL = "en-GB"
NEWS_GL = "GB"
NEWS_CEID = "GB:en"

# --- HTTP defaults ----------------------------------------------------------
REQUEST_TIMEOUT = 15  # seconds
APP_CONTACT_EMAIL = _cfg("APP_CONTACT_EMAIL", "unknown@example.com")
USER_AGENT = f"ProspectingResearchApp/0.1 (contact: {APP_CONTACT_EMAIL})"


def companies_house_configured() -> bool:
    """True when a Companies House key is available."""
    return bool(COMPANIES_HOUSE_API_KEY)


def fca_configured() -> bool:
    """FCA needs both an email and a key."""
    return bool(FCA_API_KEY and FCA_API_EMAIL)


def charity_commission_configured() -> bool:
    return bool(CHARITY_COMMISSION_API_KEY)


def opensanctions_configured() -> bool:
    return bool(OPENSANCTIONS_API_KEY)
