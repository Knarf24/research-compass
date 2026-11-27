"""Community submissions: automatic checks, then publishing once Frank approves.

How it works (see .github/workflows/check-submission.yml):

  1. Someone fills in the "Submit a research opportunity" issue form.
  2. `python scripts/submissions.py check` runs automatically and comments on the issue with what it
     could verify: the official link opens, it is on a university or government website, the page
     names the program, the deadline makes sense, it isn't already listed, and no personal contact
     details were pasted in.
  3. Frank opens the official page himself. If it's real, he adds the `approved` label.
  4. `python scripts/submissions.py publish` adds it to data/submitted.json, closes the issue and
     republishes the site. Nothing goes live without step 3.

Standard library only. The issue text is untrusted input: it is parsed as data, never run.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_data import EMAIL, USER_AGENT, tag_fields  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SUBMITTED = ROOT / "data" / "submitted.json"
LISTINGS = ROOT / "web" / "data" / "listings.json"
OFFICIAL = (".edu", ".gov", ".mil", ".us")
PHONE = re.compile(r"\+?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}")
STATE = re.compile(r",\s*([A-Z]{2})\b")
SMALL = {"the", "and", "for", "of", "in", "at", "a", "an", "to", "on", "program", "summer", "research", "undergraduate"}

FORM_KEYS = {
    "Program or position name": "title",
    "University, lab or organization": "org",
    "Official page (link)": "url",
    "City and state": "location",
    "Application deadline": "deadline",
    "Is it paid?": "paid",
    "Short description in your own words (2-3 sentences)": "summary",
    "Who can apply?": "eligibility",
    "Can international students apply?": "international",
    "Is it made for HBCU students?": "hbcu",
    "How do you know about it?": "relation",
}


# ---------------------------------------------------------------- parsing
def parse_form(body: str) -> dict:
    """Turn the issue form's markdown ("### Label\\n\\nvalue") into a dict."""
    out: dict[str, str] = {}
    for block in re.split(r"^###\s+", body or "", flags=re.M)[1:]:
        label, _, value = block.partition("\n")
        key = FORM_KEYS.get(label.strip())
        if key:
            value = value.strip()
            out[key] = "" if value in ("_No response_", "None") else value
    return out


def _date(value: str) -> dt.date | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _page_text(url: str) -> tuple[int | None, str, str]:
    """(HTTP status, final URL, lowercase visible text) for a public page; never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            raw = res.read(2_000_000).decode("utf-8", "replace")
            text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
            text = unescape(re.sub(r"<[^>]+>", " ", text))
            return res.status, res.geturl(), re.sub(r"\s+", " ", text).lower()
    except urllib.error.HTTPError as err:
        return err.code, url, ""
    except Exception:  # DNS failure, timeout, bad certificate…
        return None, url, ""


def existing() -> tuple[set[str], set[str]]:
    urls, titles = set(), set()
    if LISTINGS.exists():
        data = json.loads(LISTINGS.read_text(encoding="utf-8"))
        for rows in data.get("by_source", {}).values():
            for r in rows:
                urls.add((r.get("url") or "").rstrip("/").lower())
                titles.add((r.get("title") or "").strip().lower())
    return urls, titles


# ---------------------------------------------------------------- checks
def run_checks(form: dict, today: dt.date, fetch=_page_text) -> list[tuple[str, str]]:
    """Return [(icon, message)] where icon is ✅ (passed), ⚠️ (check by hand) or ❌ (problem)."""
    checks: list[tuple[str, str]] = []
    for key, label in (("title", "program name"), ("org", "organization"), ("url", "official link"), ("summary", "description")):
        if not form.get(key):
            checks.append(("❌", f"The {label} is missing."))

    url = form.get("url", "")
    parsed = urllib.parse.urlparse(url)
    if url and (parsed.scheme not in ("http", "https") or not parsed.netloc):
        checks.append(("❌", "The official link isn't a normal web address (it should start with https://)."))
    elif url:
        host = parsed.hostname or ""
        if host.endswith(OFFICIAL):
            checks.append(("✅", f"The link is on an official university or government website ({host})."))
        else:
            checks.append(("⚠️", f"The link is on {host}, not a .edu or .gov site. Make sure it's the organization's own website."))
        status, final, text = fetch(url)
        if status == 200:
            checks.append(("✅", "The official page opens."))
            if final.rstrip("/") != url.rstrip("/"):
                checks.append(("⚠️", f"The link redirects to {final}. Check that's still the right page."))
            words = [w for w in re.findall(r"[a-z0-9]+", form.get("title", "").lower()) if w not in SMALL and len(w) > 2]
            found = [w for w in words if w in text]
            if words and len(found) >= max(1, len(words) // 2 + len(words) % 2):
                checks.append(("✅", "The page mentions the program by name."))
            else:
                checks.append(("⚠️", "We couldn't find the program's name on that page. It may load with JavaScript; open it and look."))
            if form.get("paid", "").lower() == "yes":
                if re.search(r"stipend|paid|salary|\$\s?\d|compensation|wage|hourly", text):
                    checks.append(("✅", "The page mentions pay or a stipend."))
                else:
                    checks.append(("⚠️", "It was marked as paid, but the page doesn't mention a stipend or pay."))
        elif status:
            checks.append(("❌", f"The official page didn't open (error {status})."))
        else:
            checks.append(("❌", "The official page didn't open (the site couldn't be reached)."))

    deadline = form.get("deadline", "")
    if deadline:
        d = _date(deadline)
        if not d:
            checks.append(("⚠️", f"The deadline \"{deadline[:40]}\" isn't a date we can read. Use YYYY-MM-DD."))
        elif d < today:
            checks.append(("❌", f"The deadline ({d.isoformat()}) has already passed."))
        elif d > today + dt.timedelta(days=550):
            checks.append(("⚠️", f"The deadline ({d.isoformat()}) is more than 18 months away. Double-check the year."))
        else:
            checks.append(("✅", f"The deadline ({d.isoformat()}) is in the future."))
    else:
        checks.append(("⚠️", "No deadline given, so it will be listed as rolling or check-the-page."))

    urls, titles = existing()
    if url.rstrip("/").lower() in urls or form.get("title", "").strip().lower() in titles:
        checks.append(("❌", "This looks like it's already listed on Research Compass."))
    else:
        checks.append(("✅", "It isn't already listed."))

    pasted = " ".join(form.get(k, "") for k in ("summary", "eligibility", "location"))
    if EMAIL.search(pasted) or PHONE.search(pasted):
        checks.append(("⚠️", "The description includes an email address or phone number. These are removed when it's published; the official page is enough."))
    return checks


def report(form: dict, checks: list[tuple[str, str]]) -> str:
    problems = sum(1 for icon, _ in checks if icon == "❌")
    lines = ["### Automatic checks", ""] + [f"- {icon} {msg}" for icon, msg in checks] + [""]
    if problems:
        lines.append(f"**{problems} problem{'s' if problems > 1 else ''} to fix.** The person who submitted it can edit the issue, and the checks run again.")
    else:
        lines.append("Nothing blocking was found.")
    lines += [
        "",
        "**Before it goes live**, the repository owner opens the official page and confirms the program is real, current and "
        "matches the description, then adds the `approved` label. It's published within a few minutes after that.",
        "",
        f"<sub>Relationship given by the submitter: {form.get('relation') or 'not given'}. Checked automatically; "
        "a passing check doesn't prove a listing is real, which is why a person approves every one.</sub>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- publishing
def to_listing(form: dict, number: int, today: dt.date) -> dict:
    loc = form.get("location", "")
    state = STATE.search(loc)
    clean = lambda s, n: re.sub(r"\s+", " ", PHONE.sub("", EMAIL.sub("", s or ""))).strip()[:n]  # noqa: E731
    deadline = _date(form.get("deadline", ""))
    paid = {"yes": True, "no": False}.get(form.get("paid", "").lower())
    intl = {"yes": "yes", "no": "no"}.get(form.get("international", "").lower(), "check")
    title, summary = clean(form.get("title"), 200), clean(form.get("summary"), 500)
    return {
        "id": f"sub-{number}",
        "kind": "submitted",
        "title": title,
        "org": clean(form.get("org"), 200),
        "city": clean(loc.split(",")[0], 80) if loc else "",
        "state": state.group(1) if state else "",
        "fields": tag_fields(title, summary),
        "summary": summary,
        "url": form.get("url", "").strip(),
        "deadline": deadline.isoformat() if deadline else None,
        "ends": None,
        "paid": paid,
        "citizenship": clean(form.get("eligibility"), 160) or "See official page",
        "years": [],
        "checked": today.isoformat(),
        "international": intl,
        "hbcu_focus": form.get("hbcu", "").lower() == "yes",
    }


# ---------------------------------------------------------------- GitHub glue
def _api(method: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        body = res.read()
        return json.loads(body) if body else {}


MARKER = "<!-- research-compass-checks -->"


def _comment(number: int, text: str, replace: bool = False) -> None:
    if replace:
        for c in _api("GET", f"/issues/{number}/comments?per_page=100"):
            if MARKER in (c.get("body") or ""):
                _api("PATCH", f"/issues/comments/{c['id']}", {"body": text})
                return
    _api("POST", f"/issues/{number}/comments", {"body": text})


def main(argv: list[str]) -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    issue = event["issue"]
    form = parse_form(issue.get("body") or "")
    today = dt.date.today()
    if argv[1:2] == ["check"]:
        _comment(issue["number"], MARKER + "\n" + report(form, run_checks(form, today)), replace=True)
        return 0
    if argv[1:2] == ["publish"]:
        items = json.loads(SUBMITTED.read_text(encoding="utf-8")) if SUBMITTED.exists() else []
        entry = to_listing(form, issue["number"], today)
        if not entry["title"] or not entry["url"]:
            _comment(issue["number"], "Couldn't publish: the program name or official link is missing.")
            return 1
        items = [i for i in items if i.get("id") != entry["id"]] + [entry]
        SUBMITTED.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Added {entry['id']}: {entry['title']}")
        return 0
    print("usage: submissions.py check|publish")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
