"""
Google OIDC identity token verification and minting.
Used for service-to-service auth (Cloud Run ↔ Cloud Run, local via ADC).
"""

import logging
import os
from functools import lru_cache
from typing import Optional

import google.auth
import google.auth.transport.requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)

# ── Inbound: Verify OIDC identity tokens ──

_request = google.auth.transport.requests.Request()


def verify_oidc_token(token: str, expected_audience: Optional[str] = None) -> dict:
    """
    Verify a Google OIDC identity token.
    Returns the decoded payload with sub, email, aud, iss.
    Raises ValueError if verification fails.
    """
    payload = google_id_token.verify_oauth2_token(
        token, _request, audience=expected_audience
    )
    return {
        "sub": payload.get("sub"),
        "email": payload.get("email"),
        "email_verified": payload.get("email_verified", False),
        "aud": payload.get("aud"),
        "iss": payload.get("iss"),
    }


# ── Outbound: Mint identity tokens for service-to-service calls ──


def get_identity_token(audience: str) -> str:
    """
    Get an identity token for a target service URL.
    On Cloud Run: uses the metadata server (automatic).
    Locally: uses ADC from `gcloud auth application-default login`.
    """
    credentials, _ = google.auth.default()
    # google.oauth2.id_token.fetch_id_token uses ADC to mint an identity token
    token = google_id_token.fetch_id_token(_request, audience)
    return token


def get_auth_header(target_url: str) -> dict:
    """
    Get the appropriate auth header for an outbound service call.
    Tries OIDC first, falls back to API key.
    """
    # Try OIDC
    try:
        from urllib.parse import urlparse
        audience = f"{urlparse(target_url).scheme}://{urlparse(target_url).netloc}"
        token = get_identity_token(audience)
        return {"Authorization": f"Bearer {token}"}
    except Exception as e:
        logger.debug(f"OIDC token minting failed for {target_url}: {e}")

    # Fall back to API key
    api_key = os.environ.get("GRAPHOLOGY_API_KEY") or os.environ.get("RUN_AGENT_API_KEY")
    if api_key:
        return {"x-api-key": api_key}

    return {}


# ── Inbound: Check auth on incoming requests ──


def check_auth(authorization: Optional[str] = None, x_api_key: Optional[str] = None) -> dict:
    """
    Verify an incoming request's auth. Accepts either:
    - Authorization: Bearer {oidc_or_firebase_token}
    - x-api-key: {api_key}

    Returns the verified identity or raises ValueError.
    """
    # Try Bearer token (OIDC)
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            payload = verify_oidc_token(token)
            logger.info(f"OIDC auth: sub={payload['sub']} email={payload.get('email')}")
            return payload
        except Exception as e:
            logger.debug(f"OIDC verification failed: {e}")
            raise ValueError(f"Invalid OIDC token: {e}")

    # Fall back to API key
    if x_api_key:
        expected = os.environ.get("RUN_AGENT_API_KEY", "")
        if expected and x_api_key == expected:
            return {"sub": "api-key-user", "email": None, "provider": "api-key"}
        raise ValueError("Invalid API key")

    raise ValueError("No auth credentials provided. Send Authorization: Bearer {token} or x-api-key header.")
