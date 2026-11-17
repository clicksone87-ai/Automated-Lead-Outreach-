"""Brand extraction: colours, fonts, logo, social links and brand name from HTML and CSS."""

from __future__ import annotations

from app.agents.branding.extract import (
    colors_in,
    extract,
    is_neutral,
    normalize_hex,
)

BASE = "https://smithplumbing.example/"

HTML = """<!doctype html>
<html><head>
<title>Home | Smith Plumbing</title>
<meta name="theme-color" content="#0B5FA5">
<meta property="og:image" content="/img/share.jpg">
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Poppins:wght@600&family=Open+Sans&display=swap">
<link rel="stylesheet" href="/css/site.css">
<link rel="stylesheet" href="https://cdn.other.example/widget.css">
<style>
  :root { --brand-primary: #0b5fa5; --accent-color: #F28C28; --gray-100: #f5f5f5; }
  body { font-family: 'Open Sans', Arial, sans-serif; color: #222; background: #fff; }
  h1, h2 { font-family: "Poppins", sans-serif; }
  .btn-primary { background-color: #F28C28; }
  .btn-primary:hover { background-color: #f0892a; }
  .icon { font-family: "Font Awesome 6 Free"; }
  .overlay { background: rgba(0, 0, 0, 0.3); }
</style>
</head><body>
<header>
  <a href="/"><img src="/img/smith-logo.svg" alt="Smith Plumbing logo"></a>
  <nav><a href="/services">Services</a></nav>
</header>
<main><h1>Emergency plumbers in Leeds</h1></main>
<footer>
  <a href="https://www.facebook.com/smithplumbingleeds">Facebook</a>
  <a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
  <a href="https://instagram.com/smith.plumbing/">Instagram</a>
  <a href="https://twitter.com/">Twitter home (not a profile)</a>
</footer>
</body></html>"""

SITE_CSS = """
/* site.css */
header { background: #0B5FA5; }
.hero { background-color: rgb(11, 95, 165); color: #ffffff; }
@font-face { font-family: 'Smith Display'; src: url(/f.woff2); }
"""


def test_color_helpers() -> None:
    assert normalize_hex("#ABC") == "#aabbcc"
    assert colors_in("linear-gradient(#fff, rgb(255, 0, 0))") == ["#ffffff", "#ff0000"]
    assert colors_in("rgba(0,0,0,0.2)") == []  # mostly transparent → ignored
    assert is_neutral("#ffffff") and is_neutral("#222222") and is_neutral("#f5f5f5")
    assert not is_neutral("#0b5fa5") and not is_neutral("#f28c28")


def test_extracts_brand_colors_ranked_and_neutrals_separated() -> None:
    result = extract(HTML, BASE, [("https://smithplumbing.example/css/site.css", SITE_CSS)])
    hexes = [c["hex"] for c in result.colors]
    assert hexes[0] == "#0b5fa5"  # theme-color + brand var + header + hero
    assert "#f28c28" in hexes
    assert "#f0892a" not in hexes  # hover shade merged into the button colour
    assert all(not is_neutral(h) for h in hexes)
    assert set(result.neutrals) <= {"#222222", "#ffffff", "#f5f5f5"}
    top = result.colors[0]
    assert "meta:theme-color" in top["sources"]


def test_extracts_fonts_with_roles_and_skips_icon_and_generic_fonts() -> None:
    result = extract(HTML, BASE, [("site.css", SITE_CSS)])
    families = {f["family"]: f for f in result.fonts}
    assert "Poppins" in families and "Open Sans" in families
    assert families["Poppins"]["role"] == "heading"
    assert families["Open Sans"]["role"] == "body"
    assert not any("Awesome" in f for f in families)
    assert not any(f.lower() in ("sans-serif", "arial") for f in families)


def test_logo_candidates_ranked_header_logo_first() -> None:
    result = extract(HTML, BASE, [])
    urls = [c["url"] for c in result.logo_candidates]
    assert urls[0] == "https://smithplumbing.example/img/smith-logo.svg"
    assert "https://smithplumbing.example/apple-touch-icon.png" in urls
    assert urls.index("https://smithplumbing.example/apple-touch-icon.png") < urls.index(
        "https://smithplumbing.example/favicon.ico"
    )


def test_json_ld_logo_wins_and_names_brand() -> None:
    html = """<html><head><title>Welcome</title>
    <script type="application/ld+json">
    {"@type": "LocalBusiness", "name": "Acme Roofing Ltd", "logo": {"url": "https://cdn.acme.example/l.png"}}
    </script></head><body><img src="/x-logo.png"></body></html>"""
    result = extract(html, "https://acme.example/", [])
    assert result.logo_candidates[0]["url"] == "https://cdn.acme.example/l.png"
    assert result.brand_name == "Acme Roofing Ltd"


def test_social_profiles_only_real_profiles() -> None:
    result = extract(HTML, BASE, [])
    assert {p["network"]: p["url"] for p in result.social_profiles} == {
        "facebook": "https://www.facebook.com/smithplumbingleeds",
        "instagram": "https://instagram.com/smith.plumbing/",
    }


def test_brand_name_from_title_and_stylesheet_order() -> None:
    result = extract(HTML, BASE, [])
    assert result.brand_name == "Smith Plumbing"
    assert result.stylesheets == [
        "https://smithplumbing.example/css/site.css",
        "https://cdn.other.example/widget.css",
    ]


def test_empty_page_is_safe() -> None:
    result = extract("<html></html>", BASE, [])
    assert result.colors == [] and result.fonts == [] and result.logo_candidates == []
    assert result.brand_name is None


def test_brand_name_prefers_title_part_matching_the_domain() -> None:
    html = "<html><head><title>Hub City Plumbing and Mechanical -  located in Lubbock, Texas</title></head></html>"
    assert (
        extract(html, "https://hubcityplumbing.com/", []).brand_name
        == "Hub City Plumbing and Mechanical"
    )
    html2 = "<html><head><title>Emergency plumbers | Smith &amp; Sons</title></head></html>"
    assert extract(html2, "https://smithandsons.co.uk/", []).brand_name == "Smith & Sons"


def test_font_shorthand_keeps_multiword_family() -> None:
    css = [
        (
            "s.css",
            'p{font: italic 700 16px/1.4 "Helvetica Neue", Arial, sans-serif} h1{font:bold 2em Poppins}',
        )
    ]
    families = {f["family"] for f in extract("<html></html>", BASE, css).fonts}
    assert families == {"Helvetica Neue", "Poppins"}
