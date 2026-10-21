"""
Gmail OAuth setup for headless Docker environment.
Generates token.json for API access.
"""
import os
import sys
from config import GMAIL_TOKEN_PATH, GMAIL_CREDS_PATH, GMAIL_SCOPES


def setup_oauth():
    """Run OAuth flow to generate token.json. Requires a browser ON THIS MACHINE.
    On a headless server use setup_oauth_headless() instead."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.exists(GMAIL_CREDS_PATH):
        print(f"ERROR: credentials.json not found at {GMAIL_CREDS_PATH}")
        print("Download it from Google Cloud Console -> APIs & Services -> Credentials")
        return False

    flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDS_PATH, GMAIL_SCOPES)
    creds = flow.run_local_server(port=0)

    os.makedirs(os.path.dirname(GMAIL_TOKEN_PATH), exist_ok=True)
    with open(GMAIL_TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"Token saved to {GMAIL_TOKEN_PATH}")
    return True


def setup_oauth_headless():
    """Headless OAuth flow for servers with no local browser.

    Prints an auth URL to open in ANY browser; you approve, get redirected to a
    (non-loading) http://localhost/... URL, and paste that URL back here. Requests
    an OFFLINE refresh token with prompt=consent. Once the OAuth consent screen is
    published to 'Production', the resulting refresh token no longer dies on the
    7-day 'Testing' clock.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    from urllib.parse import urlparse, parse_qs

    if not os.path.exists(GMAIL_CREDS_PATH):
        print(f"ERROR: credentials.json not found at {GMAIL_CREDS_PATH}")
        return False

    flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDS_PATH, GMAIL_SCOPES)
    flow.redirect_uri = "http://localhost"  # matches the Desktop client's registered redirect
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    print("\n1) Open this URL in any browser and approve access:\n")
    print(auth_url)
    print("\n2) The browser will redirect to a http://localhost/... page that FAILS to load.")
    print("   That is expected. Copy the FULL address-bar URL (or just the code= value).\n")
    redirected = input("Paste the redirected URL (or code): ").strip()

    code = redirected
    if "code=" in redirected:
        code = parse_qs(urlparse(redirected).query).get("code", [redirected])[0]

    flow.fetch_token(code=code)
    creds = flow.credentials
    os.makedirs(os.path.dirname(GMAIL_TOKEN_PATH), exist_ok=True)
    with open(GMAIL_TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    print(f"\nToken saved to {GMAIL_TOKEN_PATH} (refresh_token present: {bool(creds.refresh_token)})")
    if not creds.refresh_token:
        print("WARNING: no refresh_token returned — re-run and ensure you approve fresh consent.")
    return True


def check_auth():
    """Check if Gmail auth is configured and valid."""
    if not os.path.exists(GMAIL_TOKEN_PATH):
        return {"status": "not_configured", "message": "No token.json found. Run auth setup."}

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, GMAIL_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(GMAIL_TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
            return {"status": "ok", "message": "Token refreshed successfully"}
        elif creds.valid:
            return {"status": "ok", "message": "Token is valid"}
        else:
            return {"status": "expired", "message": "Token expired and cannot be refreshed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "setup":
        setup_oauth()
    elif arg == "setup-headless":
        setup_oauth_headless()
    else:
        print(check_auth())
