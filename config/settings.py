import os
import warnings
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    'SECRET_KEY',
    'django-insecure-fuel-route-optimizer-production-key-2026-secure-random-seed'
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True').strip().lower() in ('true', '1', 'yes')

if not DEBUG and 'django-insecure' in SECRET_KEY:
    warnings.warn(
        "Insecure default SECRET_KEY is in use with DEBUG=False. Set SECRET_KEY in your environment.",
        RuntimeWarning,
    )

allowed_hosts_env = os.environ.get('ALLOWED_HOSTS')
if allowed_hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_env.split(',') if h.strip()]
else:
    ALLOWED_HOSTS = ['*'] if DEBUG else ['localhost', '127.0.0.1', '[::1]']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'corsheaders',
    'rest_framework',
    'drf_spectacular',
    # Local apps
    'fuel_optimizer.apps.FuelOptimizerConfig',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'frontend'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
# Default to SQLite for zero-config startup, compatible with PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# In-memory caching for route and geocode queries
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'fuel-route-optimizer-cache',
        'TIMEOUT': 86400,  # 24 hours
        'OPTIONS': {
            'MAX_ENTRIES': 5000,
        }
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'frontend',
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS configuration
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    cors_origins_env = os.environ.get('CORS_ALLOWED_ORIGINS')
    if cors_origins_env:
        CORS_ALLOWED_ORIGINS = [o.strip() for o in cors_origins_env.split(',') if o.strip()]
    else:
        CORS_ALLOW_ALL_ORIGINS = False

# Django REST Framework configuration
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}

# OpenAPI / Swagger settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'Fuel Route Optimizer API',
    'DESCRIPTION': (
        'REST API for calculating optimal driving routes, '
        'identifying nearby fuel stations within configurable corridors, and determining '
        'the globally cost-optimal sequence of fuel stops using Dynamic Programming.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
}

# External Services and Routing Defaults
OSRM_BASE_URL = os.environ.get('OSRM_BASE_URL', 'http://router.project-osrm.org')
OSRM_TIMEOUT = int(os.environ.get('OSRM_TIMEOUT', '15'))
GEOCODING_TIMEOUT = int(os.environ.get('GEOCODING_TIMEOUT', '10'))

# Vehicle and Optimizer Defaults
DEFAULT_MAX_RANGE_MILES = 500.0
DEFAULT_MPG = 10.0
DEFAULT_TANK_CAPACITY_GALLONS = 50.0
DEFAULT_CORRIDOR_MILES = 5.0
DEFAULT_ORIGIN_REFUEL_RADIUS_MILES = 25.0
