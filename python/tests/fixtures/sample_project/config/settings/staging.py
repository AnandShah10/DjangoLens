from .base import *

# Risky: DEBUG left on in a staging (production-like) environment.
DEBUG = True

# Risky: hardcoded secret in a production-like environment.
SECRET_KEY = "hardcoded-staging-secret-do-not-use"

ALLOWED_HOSTS = ["staging.example.com"]
