import os

DEBUG = False
SECRET_KEY = os.environ.get("SECRET_KEY", "insecure-fallback-for-local-dev-only")
ALLOWED_HOSTS = []
ROOT_URLCONF = "config.urls"

INSTALLED_APPS = [
    "django.contrib.admin",
    "orders",
    "payments",
    "users",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
]
