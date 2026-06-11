"""
Hermes App Configuration — Dual Mode (Local / AWS)
===================================================
Set DEPLOY_MODE and AUTH0_ENABLED below to control how the app
resolves credentials and authentication.

In LOCAL mode, credentials are loaded from environment variables.
Set them as Windows system environment variables (or in a .env file).
"""
import os
import json

# ============================================================
# DEPLOYMENT MODE: Set to "local" for testing, "aws" for prod
# ============================================================
DEPLOY_MODE = os.getenv("DEPLOY_MODE", "aws")   # "local" | "aws"
# Default to True ONLY if aws, False if local
AUTH0_ENABLED_DEFAULT = "true" if DEPLOY_MODE == "aws" else "false"
AUTH0_ENABLED = os.getenv("AUTH0_ENABLED", AUTH0_ENABLED_DEFAULT).lower() == "true"


# ============================================================
# LOCAL MODE — Reads credentials from environment variables
# ============================================================
_LOCAL_SNOWFLAKE_CONNECTION = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT", ""),
    "user": os.getenv("SNOWFLAKE_USER", ""),
    "password": os.getenv("SNOWFLAKE_PASSWORD", ""),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", ""),
    "database": os.getenv("SNOWFLAKE_DATABASE", "SANDBOX"),
    "schema": os.getenv("SNOWFLAKE_SCHEMA", "MCA"),
    "role": os.getenv("SNOWFLAKE_ROLE", ""),
}

_LOCAL_PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")


# ============================================================
# AWS MODE — Retrieves secrets from AWS Secrets Manager
# ============================================================
def _get_secret(secret_name):
    """Fetch a secret from AWS Secrets Manager and return as dict."""
    import boto3
    region_name = os.getenv("AWS_REGION", "us-east-1")
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=region_name)
    try:
        resp = client.get_secret_value(SecretId=secret_name)
        secret = resp["SecretString"]
        return json.loads(secret)
    except Exception as e:
        raise Exception(f"Unable to retrieve secret '{secret_name}': {e}")


def _build_auth0_config():
    """Build Auth0 config dict from environment variables (AWS mode only)."""
    env = os.getenv("BITBUCKET_DEPLOYMENT_ENVIRONMENT", "dev")
    subdomain_map = {
        "dev": "dev.",
        "test": "test.",
        "qa": "qa.",
        "psup": "psup.",
        "prod": "",
    }
    subdomain = subdomain_map.get(env, "dev.")
    return {
        "clientId": os.getenv("CLIENTID"),
        "domain": os.getenv("DOMAIN"),
        "redirect_uri": f"https://hermes.{subdomain}aws.wfscorp.com/",
    }


# ============================================================
# RESOLVED CONFIG — Used by the rest of the app
# ============================================================
if DEPLOY_MODE == "local":
    SNOWFLAKE_CONNECTION = _LOCAL_SNOWFLAKE_CONNECTION
    PERPLEXITY_API_KEY = _LOCAL_PERPLEXITY_API_KEY
    AUTH0_CONFIG = {}  # Not used when AUTH0_ENABLED = False
else:
    SNOWFLAKE_CONNECTION = _get_secret("hermes_secret_json")
    PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
    AUTH0_CONFIG = _build_auth0_config()

# Auth0 role required for access
REQUIRED_ROLE = "Poseidon:Sales"

# Snowflake source table (runtime override via env var)
SNOWFLAKE_TABLE = os.getenv("SNOWFLAKE_TABLE", "MARINE_SALES_PLANNING_V")
