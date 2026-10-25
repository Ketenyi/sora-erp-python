"""Django settings for the SORA ERP Python port."""

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


DEV_SECRET = "dev-only-change-me-sora-erp"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", DEV_SECRET)
SORA_SECRET_ENCRYPTION_KEY = os.getenv("SORA_SECRET_ENCRYPTION_KEY", "")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
# Custom tenant domains are validated by apps.saas.middleware.HostValidationMiddleware.
# Django's static allowlist cannot know about customer hostnames, so the wildcard
# is only enabled alongside that middleware (which rejects anything unknown).
SORA_CUSTOM_DOMAINS = env_bool("SORA_CUSTOM_DOMAINS", True)
if SORA_CUSTOM_DOMAINS:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, "*"]
TRUST_PROXY_HEADERS = env_bool("TRUST_PROXY_HEADERS", False)
# Absolute origin used in client-facing helpdesk links (status, confirmation, CSAT).
# Leave blank to build links from the current request host.
SORA_PUBLIC_BASE_URL = os.getenv("SORA_PUBLIC_BASE_URL", "").rstrip("/")

if not DEBUG:
    if SECRET_KEY == DEV_SECRET or len(SECRET_KEY) < 50:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set to a unique value of at least 50 characters."
        )
    if not SORA_SECRET_ENCRYPTION_KEY:
        raise ImproperlyConfigured(
            "SORA_SECRET_ENCRYPTION_KEY is required in production; it protects stored credentials."
        )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # SORA modules
    "apps.core",
    "apps.sales",
    "apps.crm",
    "apps.invoices",
    "apps.accounts",
    "apps.inventory",
    "apps.purchasing",
    "apps.hr",
    "apps.settings_center",
    "apps.admin_console",
    "apps.project_management",
    "apps.helpdesk",
    "apps.assets",
    "apps.pos",
    "apps.saas",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "apps.saas.middleware.HostValidationMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.core.middleware.MFAEnforcementMiddleware",
    "apps.saas.middleware.TenantMiddleware",
    "apps.core.middleware.RequestAuditMiddleware",
    "apps.core.middleware.SubmoduleAccessMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.sora",
                "apps.saas.context_processors.platform",
                "apps.saas.context_processors.cloud_badges",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


def database_from_url(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme in {"sqlite", "sqlite3"}:
        name = parsed.path.lstrip("/") or "db.sqlite3"
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / name,
        }
    if parsed.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")) or "sora_erp",
            "USER": unquote(parsed.username or "sora"),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "127.0.0.1",
            "PORT": str(parsed.port or 5432),
            "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
            "OPTIONS": {
                "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5")),
            },
        }
    raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")


DATABASES = {
    "default": database_from_url(
        os.getenv("DATABASE_URL", "postgresql://sora:sora@127.0.0.1:5432/sora_erp")
    )
}

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "Africa/Nairobi")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/uploads/"
MEDIA_ROOT = BASE_DIR / "uploads"

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

LOGIN_URL = "/login"
LOGIN_REDIRECT_URL = "/dashboard"
LOGOUT_REDIRECT_URL = "/login"

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", str(60 * 60 * 8)))
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
)

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "8"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@sora.com")

DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", str(60 * 60 * 24 * 365)))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", True)
    if TRUST_PROXY_HEADERS:
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
