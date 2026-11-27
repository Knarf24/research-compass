"""Makes a small shareable page for every program: _site/p/<slug>/index.html.

When someone shares a program (WhatsApp, text, email), the link opens this page on Research Compass first, like
a LinkedIn post: it shows the program, the official-page button, and a way into the full site. The page has
Open Graph tags, so WhatsApp, iMessage and others show a proper preview card. Pages for programs that have
closed disappear with the weekly refresh; 404.html then explains that and points to the site.

Run by build_site.sh:  python3 scripts/share_pages.py _site
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The public address used in shared links and previews (a neutral name, not a GitHub username).
def _site_url() -> str:
    if os.environ.get("COMPASS_SITE_URL"):
        return os.environ["COMPASS_SITE_URL"]
    m = re.search(r'shareBase:\s*"([^"]+)"', (ROOT / "web" / "config.js").read_text(encoding="utf-8"))
    return m.group(1) if m else "https://knarf24.github.io/research-compass/"


SITE_URL = _site_url().rstrip("/") + "/"
KIND = {"university": "University-run program", "reu": "NSF REU summer site", "program": "National program",
        "federal": "Federal student job", "submitted": "Community submitted"}


def slug(listing_id: str) -> str:
    """Must match shareSlug() in web/main.js."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", str(listing_id).lower())).strip("-")[:80] or "program"


def fmt(date: str | None) -> str:
    if not date:
        return ""
    try:
        d = dt.date.fromisoformat(date)
        return d.strftime("%b %-d, %Y")
    except ValueError:
        return date


def page(r: dict, updated: str) -> str:
    where = " · ".join(x for x in [r.get("org", ""), ", ".join(x for x in [r.get("city", ""), r.get("state", "")] if x)] if x)
    deadline = f"Deadline {fmt(r['deadline'])}" if r.get("deadline") else "Deadline: see official page"
    pay = r.get("pay") or ("Paid" if r.get("paid") else "")
    intl = {"yes": "🌍 Open to international students", "no": "🇺🇸 US citizens / permanent residents"}.get(r.get("international"), "🌍 International: check eligibility")
    hbcu = "🎓 For HBCU students" if r.get("hbcu_focus") else ("🎓 Hosted at an HBCU" if r.get("hbcu_host") else "")
    desc_bits = [where, deadline, pay]
    og_desc = " · ".join(x for x in desc_bits if x)
    summary = r.get("summary") or ""
    official = r.get("url") or SITE_URL
    official_label = "NSF award page ↗" if r.get("kind") == "reu" and not r.get("has_site_link") else "Official page ↗"
    url = f"{SITE_URL}p/{slug(r['id'])}/"
    pills = "".join(f'<span class="pill{c}">{escape(t)}</span>' for t, c in [
        ("⏰ " + deadline, ""), (("💵 " + pay) if pay else "", " good"), (intl, ""), (hbcu, " hbcu"),
    ] + [(f, " soft") for f in (r.get("fields") or [])[:3]] if t)
    t = escape(r.get("title", "Research opportunity"))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{t} · Research Compass</title>
<meta name="description" content="{escape(og_desc + '. ' + summary[:160])}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Research Compass">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{escape(og_desc)}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{SITE_URL}og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0b1426">
<link rel="canonical" href="{url}">
<link rel="icon" href="../../icon.svg" type="image/svg+xml">
<link rel="stylesheet" href="../../style.css">
</head>
<body>
<header class="top">
  <a class="brand" href="../../" style="text-decoration:none;color:inherit">
    <img src="../../icon.svg" alt="" class="logo" width="44" height="44">
    <div><h1>Research Compass</h1><p class="tag">Paid undergrad research across the US, matched to you.</p></div>
  </a>
</header>
<main class="app shared">
  <p class="small">Someone shared this research opportunity with you.</p>
  <article class="card">
    <div class="kind">{escape(KIND.get(r.get("kind"), r.get("source", "")))}</div>
    <h2 class="shared-title">{t}</h2>
    <div class="where">{escape(where)}</div>
    <p class="summary">{escape(summary)}</p>
    <div class="pills">{pills}</div>
    <div class="row">
      <a class="primary btn" href="{escape(official)}" target="_blank" rel="noopener">{official_label}</a>
      <a class="ghost btn" href="../../#p={escape(r['id'])}">See how well it fits you</a>
    </div>
    <div class="src small">Source: {escape(r.get("source", ""))}{(", checked " + fmt(r["checked"])) if r.get("checked") else ""}. Always confirm the details on the official page before applying.</div>
  </article>
  <div class="card block submit">
    <div>
      <h3>Find more paid research like this</h3>
      <p class="small">Research Compass lists hundreds of paid undergraduate research programs across the US, ranks them for your major and year, finds professors with funded projects, and helps you write the email. Free, no sign-up.</p>
    </div>
    <a class="primary btn" href="../../">Explore Research Compass</a>
  </div>
</main>
<footer class="foot small"><p>Research Compass is an independent student project, not affiliated with any program listed. Data updated {escape(fmt(updated) or updated or "")}.</p></footer>
</body>
</html>
"""


NOT_FOUND = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not found · Research Compass</title><link rel="stylesheet" href="SITE_URLstyle.css"></head>
<body><main class="app shared"><article class="card" style="margin-top:40px">
<h2>This opportunity isn't listed anymore</h2>
<p class="small">Its deadline may have passed, or the page moved. There are hundreds more on Research Compass.</p>
<div class="row"><a class="primary btn" href="SITE_URL">Explore Research Compass</a></div>
</article></main></body></html>
"""


def main(out: str) -> int:
    site = Path(out)
    data = json.loads((site / "data" / "listings.json").read_text(encoding="utf-8"))
    n = 0
    for rows in data.get("by_source", {}).values():
        for r in rows:
            if not r.get("id") or not r.get("title"):
                continue
            d = site / "p" / slug(r["id"])
            d.mkdir(parents=True, exist_ok=True)
            (d / "index.html").write_text(page(r, data.get("updated") or ""), encoding="utf-8")
            n += 1
    (site / "404.html").write_text(NOT_FOUND.replace("SITE_URL", SITE_URL), encoding="utf-8")
    print(f"Wrote {n} share pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "_site"))
