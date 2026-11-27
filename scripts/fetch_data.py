"""Weekly data job for Research Compass.

Pulls public research opportunities and builds the two files the site reads:

  web/data/listings.json    programs a student can apply to (REU sites, federal student jobs,
                            hand-checked national programs, reviewed user submissions)
  web/data/professors.json  US university researchers with an active NSF grant, for the
                            professor finder

Sources:
  * NSF Award Search API (public, no key): active "REU Site" awards and active research awards.
  * USAJOBS Search API (free key, optional): federal jobs open to students.
  * data/curated_programs.json: national programs we check by hand each term.
  * data/submitted.json: listings people sent in through the GitHub issue form, after review.

Expired listings are dropped. If a source fails, the last good copy of that source is kept
(minus anything that has since expired), so one bad week never empties the site.

Standard library only. Run:  python scripts/fetch_data.py
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "data"
CURATED = ROOT / "data" / "curated_programs.json"
SUBMITTED = ROOT / "data" / "submitted.json"
DIRECTORIES = ROOT / "data" / "directories.json"

NSF_API = "https://api.nsf.gov/services/v1/awards.json"
NSF_AWARD_PAGE = "https://www.nsf.gov/awardsearch/showAward?AWD_ID={id}"
USAJOBS_API = "https://data.usajobs.gov/api/Search"
USER_AGENT = "ResearchCompass/1.0 (+https://github.com/Knarf24/research-compass)"

# Fields a student can pick, and the words that tag a listing or a grant with that field.
FIELDS: dict[str, list[str]] = {
    "Computer science": ["computer science", "computing", "software", "algorithm", "cybersecurity", "security", "data science", "computational"],
    "AI and machine learning": ["machine learning", "artificial intelligence", "deep learning", "neural network", "computer vision", "natural language"],
    "Robotics": ["robot", "robotics", "autonomous", "drone", "unmanned"],
    "Electrical engineering": ["electrical", "circuit", "semiconductor", "power grid", "signal processing", "wireless", "photonics"],
    "Mechanical and aerospace": ["mechanical", "aerospace", "manufacturing", "fluid", "thermal", "materials processing", "additive"],
    "Civil and environmental eng.": ["civil engineering", "infrastructure", "transportation", "water resources", "structural", "environmental engineering"],
    "Chemistry": ["chemistry", "chemical", "catalysis", "synthesis", "molecular"],
    "Materials science": ["materials science", "nanomaterial", "polymer", "nanotechnology", "materials research"],
    "Physics and astronomy": ["physics", "quantum", "astronomy", "astrophysics", "optics", "particle"],
    "Mathematics and statistics": ["mathematics", "mathematical", "statistics", "statistical", "probability"],
    "Biology": ["biology", "biological", "genetics", "genomics", "cell", "microbial", "evolution", "neuroscience"],
    "Biomedical and health": ["biomedical", "health", "medical", "disease", "cancer", "clinical", "bioengineering"],
    "Ecology and environment": ["ecology", "ecosystem", "environmental", "conservation", "biodiversity", "climate"],
    "Earth and ocean sciences": ["geology", "geoscience", "earth science", "ocean", "marine", "atmospheric", "hydrology", "seismic"],
    "Agriculture and food": ["agriculture", "agricultural", "crop", "plant science", "food", "soil"],
    "Energy": ["energy", "solar", "battery", "renewable", "fuel cell", "nuclear"],
    "Psychology and social sciences": ["psychology", "social science", "sociology", "economics", "political science", "behavioral", "anthropology"],
    "Education research": ["education research", "stem education", "learning sciences", "teaching"],
}

# What the professor finder searches NSF for, one query per field.
PROFESSOR_QUERIES: dict[str, str] = {
    "Computer science": '"computer science"',
    "AI and machine learning": '"machine learning"',
    "Robotics": "robotics",
    "Electrical engineering": '"electrical engineering"',
    "Mechanical and aerospace": '"mechanical engineering"',
    "Civil and environmental eng.": '"civil engineering"',
    "Chemistry": "chemistry",
    "Materials science": '"materials science"',
    "Physics and astronomy": "physics",
    "Mathematics and statistics": "mathematics",
    "Biology": "biology",
    "Biomedical and health": "biomedical",
    "Ecology and environment": "ecology",
    "Earth and ocean sciences": "geoscience",
    "Agriculture and food": "agriculture",
    "Energy": '"renewable energy"',
    "Psychology and social sciences": "psychology",
    "Education research": '"STEM education"',
}

STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "PR", "GU", "VI", "AS", "MP",
}

NOT_RESEARCH_GRANT = re.compile(
    r"\b(REU|conference|workshop|symposium|travel|fellowship|scholarship|S-STEM|summer school|planning grant|student support)\b",
    re.I,
)
UNIVERSITY = re.compile(r"universit|college|institute of technology|polytechnic|school of mines|\bTech\b", re.I)
BOILERPLATE = re.compile(r"This award reflects NSF'?s statutory mission.*$", re.I | re.S)
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+", re.I)

REU_PAGES = 40           # 40 pages x 25 = up to 1,000 active REU site awards
PROF_PAGES = 4           # per field: up to 100 recent grants
PAUSE = 0.4              # seconds between API calls, to be polite


# ---------------------------------------------------------------- helpers
def today() -> dt.date:
    forced = os.environ.get("COMPASS_TODAY")  # tests pin the date
    return dt.date.fromisoformat(forced) if forced else dt.date.today()


def nsf_date(text: str | None) -> str | None:
    """NSF dates are mm/dd/yyyy. Return ISO yyyy-mm-dd, or None."""
    if not text:
        return None
    try:
        return dt.datetime.strptime(text.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def iso_date(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return dt.date.fromisoformat(str(text)[:10]).isoformat()
    except ValueError:
        return None


def clean_text(text: str | None) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = BOILERPLATE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def short(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "))
    if end > limit * 0.5:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def tag_fields(text: str) -> list[str]:
    low = " " + text.lower() + " "
    found = []
    for field, words in FIELDS.items():
        if any(re.search(r"\b" + re.escape(w), low) for w in words):
            found.append(field)
    return found


def title_case(name: str) -> str:
    """NSF sends some names in capitals ('UNIVERSITY OF FLORIDA'). Make them readable."""
    if not name or not name.isupper():
        return name
    small = {"of", "and", "the", "at", "in", "for", "a"}
    words = name.lower().split()
    out = []
    for i, w in enumerate(words):
        if w in {"suny", "cuny", "mit", "ucla", "usc", "lsu", "uc", "ii", "iii"}:
            out.append(w.upper())
        elif i and w in small:
            out.append(w)
        else:
            out.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(out)


def get_json(url: str, headers: dict[str, str] | None = None, tries: int = 3) -> dict:
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            body = err.read()[:300].decode("utf-8", "replace") if err.fp else ""
            last = f"HTTP {err.code} {body}".strip()
            if 400 <= err.code < 500 and err.code != 429:
                break  # a bad request will not fix itself on retry
        except Exception as err:  # network or JSON error
            last = err
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url.split('?')[0]} failed: {last}")


def nsf_search(params: dict[str, str], pages: int) -> list[dict]:
    """Page through the NSF Award Search API (25 per page).

    The live API treats offset as a 0-based record index (offset=1 skips the first award),
    and it rejects unencoded double quotes in the URL, so everything is percent-encoded.
    """
    awards: list[dict] = []
    for page in range(pages):
        query = dict(params, rpp="25", offset=str(page * 25))
        url = NSF_API + "?" + urllib.parse.urlencode(query, safe=",/")
        data = get_json(url).get("response", {})
        if "serviceNotification" in data:
            note = data["serviceNotification"][0]
            raise RuntimeError(f"NSF said {note.get('notificationCode')}: {note.get('notificationMessage')}")
        batch = data.get("award", []) or []
        awards.extend(batch)
        if len(batch) < 25:
            break
        time.sleep(PAUSE)
    return awards


# ---------------------------------------------------------------- sources
NSF_FIELDS = "id,title,awardeeName,awardeeCity,awardeeStateCode,piFirstName,piLastName,startDate,expDate,abstractText,fundProgramName,estimatedTotalAmt"


def fetch_reu_sites() -> list[dict]:
    """Active NSF 'REU Site' awards: each one is a paid summer program run by a US campus."""
    now = today()
    awards = nsf_search(
        {"keyword": '"REU Site"', "expDateStart": now.strftime("%m/%d/%Y")},
        REU_PAGES,
    )
    out = []
    for a in awards:
        title = clean_text(a.get("title"))
        if not re.search(r"\bREU\b", title) and "UNDERGRAD" not in (a.get("fundProgramName") or "").upper():
            continue
        abstract = clean_text(a.get("abstractText"))
        site = next((u.rstrip(".,;") for u in URL_IN_TEXT.findall(abstract) if "nsf.gov" not in u.lower()), None)
        topic = re.sub(r"^(collaborative research:\s*)?(REU Site:?\s*)", "", title, flags=re.I).strip(" :-")
        state = (a.get("awardeeStateCode") or "").upper()
        out.append({
            "id": f"nsf-{a.get('id')}",
            "kind": "reu",
            "title": topic or title,
            "org": title_case(a.get("awardeeName") or ""),
            "city": title_case(a.get("awardeeCity") or ""),
            "state": state if state in STATES else "",
            "fields": tag_fields(title + " " + abstract),
            "summary": short(abstract, 320),
            "url": site or NSF_AWARD_PAGE.format(id=a.get("id")),
            "award_url": NSF_AWARD_PAGE.format(id=a.get("id")),
            "has_site_link": bool(site),
            "deadline": None,
            "ends": nsf_date(a.get("expDate")),
            "paid": True,
            "citizenship": "US citizen or permanent resident",
            "years": ["Freshman", "Sophomore", "Junior", "Senior"],
            "contact": " ".join(x for x in [a.get("piFirstName"), a.get("piLastName")] if x),
            "source": "NSF REU Sites",
        })
    return out


def fetch_professors() -> list[dict]:
    """University researchers with an active NSF grant, grouped by person."""
    now = today()
    since = now.replace(year=now.year - 3)
    people: dict[str, dict] = {}
    for field, keyword in PROFESSOR_QUERIES.items():
        awards = nsf_search(
            {
                "keyword": keyword,
                "expDateStart": now.strftime("%m/%d/%Y"),
                "startDateStart": since.strftime("%m/%d/%Y"),
            },
            PROF_PAGES,
        )
        for a in awards:
            title = clean_text(a.get("title"))
            school = title_case(a.get("awardeeName") or "")
            first, last = (a.get("piFirstName") or "").strip(), (a.get("piLastName") or "").strip()
            if not (first and last) or NOT_RESEARCH_GRANT.search(title) or not UNIVERSITY.search(school):
                continue
            state = (a.get("awardeeStateCode") or "").upper()
            if state not in STATES:
                continue
            key = f"{first} {last}|{school}".lower()
            abstract = clean_text(a.get("abstractText"))
            person = people.setdefault(key, {
                "id": "p" + re.sub(r"[^a-z0-9]", "", key)[:40],
                "name": f"{title_case(first)} {title_case(last)}",
                "school": school,
                "city": title_case(a.get("awardeeCity") or ""),
                "state": state,
                "fields": [],
                "grants": [],
            })
            if field not in person["fields"]:
                person["fields"].append(field)
            for f in tag_fields(title + " " + abstract):
                if f not in person["fields"]:
                    person["fields"].append(f)
            if any(g["id"] == a.get("id") for g in person["grants"]):
                continue
            person["grants"].append({
                "id": a.get("id"),
                "title": title,
                "summary": short(abstract, 300),
                "ends": nsf_date(a.get("expDate")),
                "url": NSF_AWARD_PAGE.format(id=a.get("id")),
            })
        time.sleep(PAUSE)
    out = []
    for p in people.values():
        p["grants"] = sorted(p["grants"], key=lambda g: g["ends"] or "", reverse=True)[:3]
        out.append(p)
    return sorted(out, key=lambda p: (p["state"], p["school"], p["name"]))


def fetch_usajobs() -> list[dict]:
    """Federal research jobs open to students (USAJOBS 'student' hiring path). Needs a free key."""
    key, email = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not key or not email:
        raise RuntimeError("skipped: USAJOBS_API_KEY and USAJOBS_EMAIL secrets are not set")
    now = today()
    seen: dict[str, dict] = {}
    for word in ["research", "laboratory", "scientist", "engineering intern"]:
        query = urllib.parse.urlencode({"Keyword": word, "HiringPath": "student", "ResultsPerPage": "250", "Fields": "Full"})
        data = get_json(f"{USAJOBS_API}?{query}", headers={"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key})
        for item in data.get("SearchResult", {}).get("SearchResultItems", []):
            job = item.get("MatchedObjectDescriptor", {})
            close = iso_date(job.get("ApplicationCloseDate"))
            if not close or close < now.isoformat():
                continue
            jid = str(item.get("MatchedObjectId") or job.get("PositionID"))
            details = (job.get("UserArea") or {}).get("Details") or {}
            pay = (job.get("PositionRemuneration") or [{}])[0]
            locs = job.get("PositionLocation") or [{}]
            state_name = (locs[0].get("CountrySubDivisionCode") or "")
            text = " ".join([job.get("PositionTitle", ""), details.get("JobSummary", ""), " ".join(c.get("Name", "") for c in job.get("JobCategory") or [])])
            seen[jid] = {
                "id": f"usajobs-{jid}",
                "kind": "federal",
                "title": job.get("PositionTitle", ""),
                "org": " · ".join(x for x in [job.get("OrganizationName"), job.get("DepartmentName")] if x),
                "city": (locs[0].get("CityName") or "").split(",")[0] if len(locs) == 1 else "Several locations",
                "state": STATE_CODES.get(state_name, "") if len(locs) == 1 else "",
                "fields": tag_fields(text),
                "summary": short(clean_text(details.get("JobSummary")), 320),
                "url": job.get("PositionURI", ""),
                "deadline": close,
                "ends": close,
                "paid": bool(pay.get("MinimumRange")),
                "pay": _pay_text(pay),
                "citizenship": "US citizen (federal job)",
                "years": ["Freshman", "Sophomore", "Junior", "Senior", "Graduate"],
                "source": "USAJOBS",
            }
        time.sleep(PAUSE)
    return list(seen.values())


def _pay_text(pay: dict) -> str:
    lo, hi, per = pay.get("MinimumRange"), pay.get("MaximumRange"), pay.get("Description") or ""
    try:
        lo_f, hi_f = float(lo), float(hi)
    except (TypeError, ValueError):
        return ""
    fmt = (lambda v: f"${v:,.2f}") if lo_f < 1000 else (lambda v: f"${v:,.0f}")
    return f"{fmt(lo_f)}–{fmt(hi_f)} {per.lower()}".strip() if hi_f != lo_f else f"{fmt(lo_f)} {per.lower()}".strip()


STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Puerto Rico": "PR",
}


def load_file_listings(path: Path, source: str) -> list[dict]:
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    for it in items:
        it.setdefault("source", source)
        it.setdefault("fields", tag_fields(it.get("title", "") + " " + it.get("summary", "")))
    return items


# ---------------------------------------------------------------- build
def not_expired(item: dict, now: str) -> bool:
    """Keep a listing until its deadline (or, without one, its end date) has passed."""
    cutoff = item.get("deadline") or item.get("ends")
    return not cutoff or cutoff >= now


def previous(path: Path, key: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(key, {})
    except (OSError, ValueError):
        return {}


def build(out_dir: Path = OUT_DIR) -> dict:
    now = today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    listings_path, profs_path = out_dir / "listings.json", out_dir / "professors.json"
    old = previous(listings_path, "by_source")
    report: dict[str, dict] = {}
    by_source: dict[str, list[dict]] = {}

    for name, fn in [("NSF REU Sites", fetch_reu_sites), ("USAJOBS", fetch_usajobs)]:
        try:
            items = fn()
            if not items:
                raise RuntimeError("returned nothing")
            by_source[name] = items
            report[name] = {"ok": True, "count": len(items)}
        except Exception as err:
            kept = [i for i in old.get(name, []) if not_expired(i, now)]
            by_source[name] = kept
            report[name] = {"ok": False, "count": len(kept), "note": str(err)[:200], "kept_previous": bool(kept)}
            print(f"[warn] {name}: {err}", file=sys.stderr)

    by_source["Hand-checked programs"] = load_file_listings(CURATED, "Hand-checked programs")
    by_source["Submitted"] = load_file_listings(SUBMITTED, "Submitted")
    for name in ("Hand-checked programs", "Submitted"):
        report[name] = {"ok": True, "count": len(by_source[name])}

    dropped = 0
    for name, items in by_source.items():
        keep = [i for i in items if not_expired(i, now)]
        dropped += len(items) - len(keep)
        by_source[name] = keep
        report[name]["count"] = len(keep)

    listings_path.write_text(json.dumps({"updated": now, "sources": report, "dropped_expired": dropped, "directories": json.loads(DIRECTORIES.read_text(encoding="utf-8")) if DIRECTORIES.exists() else [], "by_source": by_source}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    try:
        people = fetch_professors()
        if not people:
            raise RuntimeError("returned nothing")
        prof_report = {"ok": True, "count": len(people)}
    except Exception as err:
        people = previous(profs_path, "people") or []
        prof_report = {"ok": False, "count": len(people), "note": str(err)[:200], "kept_previous": bool(people)}
        print(f"[warn] professors: {err}", file=sys.stderr)
    profs_path.write_text(json.dumps({"updated": now if prof_report["ok"] else previous(profs_path, "updated") or now, "source": prof_report, "people": people}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    summary = {"listings": {k: v["count"] for k, v in report.items()}, "professors": len(people), "dropped_expired": dropped}
    print(json.dumps({"report": report, "professors": prof_report, "summary": summary}, indent=2))
    return summary


if __name__ == "__main__":
    build()
