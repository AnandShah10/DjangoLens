from .base import *

DEBUG = False

# Risky: hardcoded secret in production.
SECRET_KEY = "hardcoded-production-secret-do-not-use"

# Risky: wildcard disables Django's Host header validation.
ALLOWED_HOSTS = ["*"]
