"""Contact extraction from fetched pages (pure functions; input HTML is untrusted).

Only addresses that literally appear in the content are returned — nothing is guessed or
generated from name patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}(?![\w-])"
)
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+-]{1,64})\s*(?:\[|\(|\{)\s*at\s*(?:\]|\)|\})\s*([A-Za-z0-9-]{1,63}"
    r"(?:\s*(?:\[|\(|\{)\s*dot\s*(?:\]|\)|\})\s*[A-Za-z0-9-]{1,63})+)",
    re.I,
)
DOT_RE = re.compile(r"\s*(?:\[|\(|\{)\s*dot\s*(?:\]|\)|\})\s*", re.I)

CONTACT_PAGE_HINTS = re.compile(
    r"(contact|kontakt|contacto|about|ueber-uns|uber-uns|a-propos|imprint|impressum|legal|"
    r"mentions-legales|aviso-legal|get-in-touch|reach-us|quote)",
    re.I,
)
IGNORED_LOCALPARTS = re.compile(
    r"^(no-?reply|do-?not-?reply|bounce|mailer-daemon|postmaster)$", re.I
)
IGNORED_DOMAINS = (
    "sentry.io",
    "wixpress.com",
    "sentry-next.wixpress.com",
    "example.invalid",
    "domain.com",
    "email.com",
    "yourdomain.com",
    "yoursite.com",
    "test.com",
)
ASSET_SUFFIX = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$", re.I)
ROLE_LOCALPARTS = (
    "info",
    "contact",
    "hello",
    "office",
    "enquiries",
    "inquiries",
    "sales",
    "admin",
    "bookings",
    "mail",
    "kontakt",
    "contacto",
)
FREE_MAIL = (
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "hotmail.co.uk",
    "yahoo.com",
    "yahoo.co.uk",
    "icloud.com",
    "aol.com",
    "gmx.de",
    "web.de",
    "btinternet.com",
    "live.co.uk",
    "proton.me",
    "protonmail.com",
)


@dataclass(frozen=True, slots=True)
class FoundEmail:
    email: str
    context: str  # surrounding text, kept as evidence


@dataclass(frozen=True, slots=True)
class FoundForm:
    action: str
    fields: tuple[str, ...]


def _clean(email: str) -> str | None:
    email = unquote(email).strip().strip(".,;:<>()[]\"'").lower()
    if not EMAIL_RE.fullmatch(email):
        return None
    local, _, domain = email.partition("@")
    if IGNORED_LOCALPARTS.match(local) or ASSET_SUFFIX.search(email):
        return None
    if domain in IGNORED_DOMAINS or domain.endswith(IGNORED_DOMAINS):
        return None
    return email


def decode_cfemail(encoded: str) -> str | None:
    """Cloudflare email protection: hex string XOR-ed with its first byte."""
    try:
        data = bytes.fromhex(encoded)
    except ValueError:
        return None
    if len(data) < 2:
        return None
    key = data[0]
    return bytes(b ^ key for b in data[1:]).decode("utf-8", errors="ignore")


def extract_emails(html_or_text: str) -> list[FoundEmail]:
    """Emails present in the content: mailto links, plain text, [at]/[dot] and Cloudflare."""
    found: dict[str, str] = {}
    soup = BeautifulSoup(html_or_text, "lxml")

    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if href.lower().startswith("mailto:"):
            raw = href[7:].split("?", 1)[0]
            for part in raw.split(","):
                email = _clean(part)
                if email:
                    found.setdefault(email, a.get_text(" ", strip=True)[:200] or "mailto link")
    for node in soup.select("[data-cfemail]"):
        decoded = decode_cfemail(str(node.get("data-cfemail", "")))
        email = _clean(decoded or "")
        if email:
            found.setdefault(email, "cloudflare-protected address")
    for script in soup(["script", "style", "noscript"]):
        script.decompose()
    text = soup.get_text(" ", strip=True)
    for m in EMAIL_RE.finditer(text):
        email = _clean(m.group(0))
        if email:
            found.setdefault(email, text[max(0, m.start() - 80) : m.end() + 80])
    for m in OBFUSCATED_RE.finditer(text):
        email = _clean(f"{m.group(1)}@{DOT_RE.sub('.', m.group(2))}")
        if email:
            found.setdefault(email, text[max(0, m.start() - 80) : m.end() + 80])
    return [FoundEmail(e, ctx) for e, ctx in found.items()]


def extract_contact_forms(html: str, base_url: str) -> list[FoundForm]:
    forms = []
    for form in BeautifulSoup(html, "lxml").find_all("form"):
        names = tuple(
            str(el.get("name") or el.get("id") or el.get("type") or "").lower()
            for el in form.find_all(["input", "textarea", "select"])
        )
        has_message = any(el.name == "textarea" for el in form.find_all("textarea"))
        has_email = any("email" in n or "mail" in n for n in names)
        is_search = any(n in ("q", "s", "search") for n in names) and not has_message
        if has_message and has_email and not is_search:
            forms.append(FoundForm(urljoin(base_url, str(form.get("action") or base_url)), names))
    return forms


def candidate_pages(html: str, base_url: str, limit: int = 5) -> list[str]:
    """Same-site links that look like contact/about/imprint pages, best first."""
    host = (urlsplit(base_url).hostname or "").removeprefix("www.")
    scored: dict[str, int] = {}
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, str(a["href"])).split("#", 1)[0]
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        if (parts.hostname or "").removeprefix("www.") != host:
            continue
        label = f"{a.get_text(' ', strip=True)} {parts.path}"
        if not CONTACT_PAGE_HINTS.search(label):
            continue
        score = (
            3
            if re.search(r"contact|kontakt|contacto", label, re.I)
            else 2
            if re.search(r"imprint|impressum|legal|mentions", label, re.I)
            else 1
        )
        scored[url] = max(score, scored.get(url, 0))
    ranked = sorted(scored, key=lambda u: (-scored[u], len(u)))
    return [u for u in ranked if u.rstrip("/") != base_url.rstrip("/")][:limit]


def rank_email(email: str, business_domain: str | None) -> int:
    """Higher is better: role address on own domain > own domain > free-mail > other."""
    local, _, domain = email.partition("@")
    own = bool(business_domain) and (
        domain == business_domain or domain.endswith(f".{business_domain}")
    )
    score = 0
    if own:
        score += 50
    elif domain in FREE_MAIL:
        score += 20
    if local in ROLE_LOCALPARTS:
        score += 10
    return score
