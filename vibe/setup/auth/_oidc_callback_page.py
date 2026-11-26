"""Static page the OIDC loopback callback returns to the browser.

Vibe Desktop serves the same page from
`ts/apps/le-chat-desktop/src/core/auth/main/oidc-callback-server.ts`; the
markup is duplicated on purpose. The CSS and logo match Desktop's
`CALLBACK_PAGE_CSS` and `LOGO` byte for byte, so both CSPs carry the same
hash; the copy and outcomes are CLI-specific.
"""

from __future__ import annotations

import base64
from enum import Enum, auto
import hashlib


class CallbackPage(Enum):
    COMPLETE = auto()
    CANCELLED = auto()
    FAILED = auto()


_COPY: dict[CallbackPage, tuple[str, str]] = {
    CallbackPage.COMPLETE: (
        "Sign-in complete",
        "You can close this window and return to your terminal.",
    ),
    CallbackPage.CANCELLED: (
        "Sign-in was cancelled",
        "Return to your terminal to try again.",
    ),
    CallbackPage.FAILED: ("Sign-in failed", "Return to your terminal and try again."),
}

_CSS = (
    "*{box-sizing:border-box}"
    "body{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;"
    'font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,'
    '"Helvetica Neue",Arial,sans-serif;'
    "background:#fbfbf8}"
    ".card{display:grid;justify-items:center;gap:16px;width:100%;max-width:28rem;"
    "padding:20px;text-align:center;background:#fff;border:1px solid #0000001a;"
    "border-radius:10px;box-shadow:0 10px 15px -3px #0000001a,0 4px 6px -4px #0000001a}"
    "h1{margin:0;font-size:1.125rem;line-height:1.5;font-weight:600;color:#201f1c}"
    "p{margin:0;font-size:0.9375rem;line-height:1.5;color:#615f57}"
    "@media (min-width:640px){.card{padding:32px}}"
    "@media (prefers-color-scheme:dark){body{background:#1a1a1e}"
    ".card{background:#101013;border-color:#ffffff1a}h1{color:#fff}p{color:#ccccd1}}"
)

_CSS_HASH = base64.b64encode(hashlib.sha256(_CSS.encode()).digest()).decode()

# The callback URL carries the authorization code: the page must not load
# anything, be cached, or send that URL on as a referrer.
CALLBACK_PAGE_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Type", "text/html; charset=utf-8"),
    (
        "Content-Security-Policy",
        f"default-src 'none'; style-src 'sha256-{_CSS_HASH}'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'",
    ),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cache-Control", "no-store"),
)

# Mistral logo, copied from AuraLogo in ts/packages/ui/src/branding/logo.tsx.
_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 162 162" width="48" '
    'height="48" fill="none" aria-hidden="true">'
    '<rect x="31" y="31" width="20" height="20" fill="#FFAF01"/>'
    '<rect x="111" y="31" width="20" height="20" fill="#FFAF01"/>'
    '<rect x="31" y="51" width="40" height="20" fill="#FF8204"/>'
    '<rect x="91" y="51" width="40" height="20" fill="#FF8204"/>'
    '<rect x="31" y="71" width="100" height="20" fill="#FA500F"/>'
    '<rect x="31" y="91" width="20" height="20" fill="#E61300"/>'
    '<rect x="71" y="91" width="20" height="20" fill="#E61300"/>'
    '<rect x="111" y="91" width="20" height="20" fill="#E61300"/>'
    '<rect x="11" y="111" width="60" height="20" fill="#C4001D"/>'
    '<rect x="91" y="111" width="60" height="20" fill="#C4001D"/>'
    "</svg>"
)


def render_callback_page(page: CallbackPage) -> bytes:
    title, message = _COPY[page]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{title}</title>"
        f"<style>{_CSS}</style></head>"
        f'<body><main class="card">{_LOGO_SVG}<h1>{title}</h1><p>{message}</p>'
        "</main></body></html>"
    ).encode()
