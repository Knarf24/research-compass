"""Research Compass: the thinking part, in Python, running in the browser with Pyodide.

The page (main.js) only draws and saves. Everything here is plain standard-library Python:

  * load            read the weekly data files
  * programs        rank programs for a student's profile, with the reasons for each match
  * professors      find researchers with active NSF grants that match the student's interests
  * email           draft a cold email (or follow-up / thank-you) from the profile and the target
  * tracker         deadlines, days left and reminders for the student's saved applications
  * ics             a calendar file with every deadline, for Google/Apple/Outlook calendars
  * resume          suggest profile fields from the text of a résumé (on this device)

The bridge is one function: call(name, json_args) -> json {"ok": result} or {"error": message}.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from collections import Counter

DATA: dict = {"listings": [], "people": [], "directories": [], "updated": None, "prof_updated": None, "sources": {}}
YEARS = ["Community college", "Freshman", "Sophomore", "Junior", "Senior", "Graduate"]
STOP = set(
    """a an and are as at be by for from has have in into is it its of on or our that the their this to with we will
    research project projects study studies new using use based develop development support award students student
    university program science sciences data system systems approach understanding provide also these which""".split()
)
CITIZEN_ONLY = re.compile(r"\bUS citizen\b", re.I)


# ---------------------------------------------------------------- helpers
def _today(args: dict) -> dt.date:
    return dt.date.fromisoformat(args["today"]) if args.get("today") else dt.date.today()


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z\-]{2,}", (text or "").lower()) if w not in STOP]


def _stem(w: str) -> str:
    for suffix in ("ing", "ies", "es", "s", "al", "ic"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def _terms(text: str) -> list[str]:
    return [_stem(w) for w in _words(text)]


def _unstem(term: str, source: str) -> str:
    """Show the student's own word ('robots'), not the matching stem ('robot')."""
    for w in _words(source):
        if _stem(w) == term:
            return w
    return term


def _days_left(date: str | None, today: dt.date) -> int | None:
    if not date:
        return None
    try:
        return (dt.date.fromisoformat(date) - today).days
    except ValueError:
        return None


def _profile_fields(profile: dict) -> list[str]:
    out = []
    for f in [profile.get("major")] + list(profile.get("fields") or []):
        if f and f not in out:
            out.append(f)
    return out


# ---------------------------------------------------------------- load
def load(args: dict) -> dict:
    listings = json.loads(args["listings"])
    people = json.loads(args.get("professors") or '{"people": []}')
    items = []
    for source, rows in listings.get("by_source", {}).items():
        for r in rows:
            r = dict(r)
            r.setdefault("source", source)
            r["_text"] = " ".join([r.get("title", ""), r.get("org", ""), r.get("summary", ""), " ".join(r.get("fields", []))]).lower()
            items.append(r)
    DATA["listings"] = items
    DATA["directories"] = listings.get("directories", [])
    DATA["updated"] = listings.get("updated")
    DATA["sources"] = listings.get("sources", {})

    plist = people.get("people", [])
    df: Counter = Counter()
    for p in plist:
        text = " ".join(g["title"] + " " + g.get("summary", "") for g in p.get("grants", []))
        p["_terms"] = Counter(_terms(text))
        p["_text"] = (p["name"] + " " + p["school"] + " " + text).lower()
        df.update(set(p["_terms"]))
    n = max(len(plist), 1)
    DATA["idf"] = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
    DATA["people"] = plist
    DATA["prof_updated"] = people.get("updated")

    fields = sorted({f for r in items for f in r.get("fields", [])} | {f for p in plist for f in p.get("fields", [])})
    states = sorted({r["state"] for r in items if r.get("state")} | {p["state"] for p in plist if p.get("state")})
    return {
        "listings": len(items),
        "hbcu_listings": sum(1 for r in items if r.get("hbcu_focus") or r.get("hbcu_host")),
        "intl_listings": sum(1 for r in items if r.get("international") == "yes"),
        "hbcu_professors": sum(1 for p in plist if p.get("hbcu")),
        "professors": len(plist),
        "schools": len({p["school"] for p in plist}),
        "fields": fields,
        "states": states,
        "updated": DATA["updated"],
        "prof_updated": DATA["prof_updated"],
        "sources": DATA["sources"],
        "directories": DATA["directories"],
        "years": YEARS,
    }


# ---------------------------------------------------------------- programs
_GPA = re.compile(r"gpa[^0-9.;]{0,25}([1-4]\.\d{1,2})|([1-4]\.\d{1,2})\s*(?:/\s*4\.0\s*)?(?:or (?:higher|above|better)\s*)?(?:cumulative\s*|overall\s*)?gpa", re.I)
_PHD = re.compile(r"\b(interested in|considering|planning|pursue|pursuing|preparation for|path to)\b[^.]{0,40}\b(ph\.?d|doctoral|graduate school|graduate study)", re.I)


def _min_gpa(r: dict) -> float | None:
    if r.get("min_gpa"):
        return float(r["min_gpa"])
    m = _GPA.search(r.get("summary") or "")
    if m:
        v = float(m.group(1) or m.group(2))
        if 2.0 <= v <= 4.0:
            return v
    return None


def _gpa(profile: dict) -> float | None:
    try:
        v = float(str(profile.get("gpa") or "").strip())
        return v if 0 < v <= 4.0 else None
    except ValueError:
        return None


def _score_listing(r: dict, profile: dict, today: dt.date) -> tuple[int, list[str], list[str], bool]:
    """Return (score 0-100, reasons it fits, warnings, blocked).

    A rule-based fit score, not a prediction of being accepted. Points (100 at most):
      field 35 (your major) or 25 (a field you'd consider) · your interest words 0-20 ·
      class year 15 (listed and matches; 8 when the program doesn't say) · eligibility 15 (confirmed;
      7 when it needs checking) · paid 5 · deadline more than two weeks away 5 · location 5.
    Then caps: a requirement you don't meet (class year, citizenship, GPA) caps it at 40 and marks it
    "blocked"; a requirement we can't confirm (GPA not given, international eligibility unclear)
    caps it at 90, so 100 only means everything we can check lines up.
    """
    reasons, warnings = [], []
    score = 0.0
    cap = 100
    blocked = False
    mine = _profile_fields(profile)
    fields = r.get("fields", [])
    hit = [f for f in mine if f in fields]
    if profile.get("major") and profile.get("major") in fields:
        score += 35
        reasons.append(f"Matches your major ({profile['major']})")
    elif hit:
        score += 25
        reasons.append(f"Matches your interest in {hit[0]}")
    elif not fields or not mine:
        score += 10

    # Interests count as phrases ("machine learning" is one interest, not two words).
    phrases = [ph.strip() for ph in re.split(r",|;|/|\band\b", (profile.get("interests") or "").lower()) if _words(ph)]
    found = [ph for ph in phrases if all(_stem(w) in r["_text"] for w in _words(ph))]
    if found:
        score += min(20, 10 * len(found))
        reasons.append("Mentions " + ", ".join(found[:3]))

    year = profile.get("year")
    years = r.get("years") or []
    if year and years:
        if year in years:
            score += 15
            reasons.append(f"Open to {year.lower()}s" if year != "Community college" else "Open to community college students")
        else:
            cap, blocked = min(cap, 40), True
            warnings.append(f"Usually for {', '.join(years)} students")
    else:
        score += 8

    intl = r.get("international") or ("no" if CITIZEN_ONLY.search(r.get("citizenship") or "") else "check")
    citizen = profile.get("citizen")
    if citizen == "no":
        if intl == "yes":
            score += 15
            reasons.append("Open to international students")
        elif intl == "no":
            cap, blocked = min(cap, 40), True
            warnings.append("Requires US citizenship or permanent residency")
        else:
            score += 7
            cap = min(cap, 90)
            warnings.append("Check whether international students can apply")
    elif citizen == "yes":
        score += 15
    else:
        score += 7
        if intl == "no":
            warnings.append("US citizens and permanent residents only")

    need = _min_gpa(r)
    mine_gpa = _gpa(profile)
    if need:
        if mine_gpa is None:
            cap = min(cap, 90)
            warnings.append(f"Needs a GPA of {need:.1f} or higher (add your GPA to your profile to check)")
        elif mine_gpa + 1e-9 >= need:
            reasons.append(f"Meets the {need:.1f} GPA minimum")
        else:
            cap, blocked = min(cap, 40), True
            warnings.append(f"Needs a GPA of {need:.1f} or higher; yours is {mine_gpa:.2f}")

    if _PHD.search(r.get("summary") or ""):
        warnings.append("Meant for undergraduates thinking about a PhD or graduate school")

    if r.get("paid"):
        score += 5
        if profile.get("paid_only"):
            reasons.append("Paid")

    hbcu = r.get("hbcu_focus") or r.get("hbcu_host")
    if hbcu and profile.get("hbcu"):
        score += 10
        reasons.append("Made for HBCU students" if r.get("hbcu_focus") else "Hosted at an HBCU")

    st = r.get("state")
    pref = profile.get("states") or []
    if not pref or not st:
        score += 5
    elif st in pref:
        score += 5
        reasons.append(f"In {st}, a state you picked")

    left = _days_left(r.get("deadline"), today)
    if left is not None:
        if 0 <= left <= 14:
            warnings.append(f"Deadline in {left} day{'s' if left != 1 else ''}")
        elif left > 14:
            score += 5
    else:
        score += 3
    return max(0, min(cap, 100, round(score))), reasons, warnings, blocked


def programs(args: dict) -> dict:
    profile = args.get("profile") or {}
    f = args.get("filters") or {}
    today = _today(args)
    q = (f.get("q") or "").strip().lower()
    q_terms = q.split()
    out = []
    for r in DATA["listings"]:
        if f.get("field") and f["field"] not in r.get("fields", []):
            continue
        if f.get("state") and r.get("state") != f["state"]:
            continue
        if f.get("kind") and r.get("kind") != f["kind"]:
            continue
        if f.get("paid") and not r.get("paid"):
            continue
        if f.get("hbcu") and not (r.get("hbcu_focus") or r.get("hbcu_host")):
            continue
        if f.get("intl") and r.get("international") != "yes":
            continue
        if q_terms and not all(t in r["_text"] or t in (r.get("state") or "").lower() for t in q_terms):
            continue
        score, reasons, warnings, blocked = _score_listing(r, profile, today)
        if f.get("eligible") and blocked:
            continue
        item = {k: v for k, v in r.items() if not k.startswith("_")}
        item.update(score=score, reasons=reasons, warnings=warnings, days_left=_days_left(r.get("deadline"), today))
        out.append(item)
    sort = f.get("sort") or "match"
    if sort == "deadline":
        out.sort(key=lambda x: (x["days_left"] is None, x["days_left"] if x["days_left"] is not None else 0, -x["score"]))
    elif sort == "name":
        out.sort(key=lambda x: (x.get("org") or "", x["title"]))
    else:
        out.sort(key=lambda x: (-x["score"], x["days_left"] if x["days_left"] is not None else 9999, x["title"]))
    total = len(out)
    limit = int(f.get("limit") or 60)
    return {"total": total, "items": out[:limit]}


def program(args: dict) -> dict:
    """One program, scored for the student (for shared links)."""
    for r in DATA["listings"]:
        if r["id"] == args.get("id"):
            score, reasons, warnings, blocked = _score_listing(r, args.get("profile") or {}, _today(args))
            item = {k: v for k, v in r.items() if not k.startswith("_")}
            item.update(score=score, reasons=reasons, warnings=warnings, days_left=_days_left(r.get("deadline"), _today(args)))
            return item
    return {}


def listing(args: dict) -> dict:
    for r in DATA["listings"]:
        if r["id"] == args["id"]:
            return {k: v for k, v in r.items() if not k.startswith("_")}
    raise KeyError("That listing is no longer available.")


# ---------------------------------------------------------------- professors
def professors(args: dict) -> dict:
    profile = args.get("profile") or {}
    f = args.get("filters") or {}
    q = (f.get("q") or "").strip()
    mine = _profile_fields(profile)
    want = Counter(_terms(q or profile.get("interests", "")))
    idf = DATA.get("idf", {})
    pref = profile.get("states") or []
    out = []
    for p in DATA["people"]:
        if f.get("field") and f["field"] not in p.get("fields", []):
            continue
        if f.get("state") and p.get("state") != f["state"]:
            continue
        if f.get("school") and f["school"].lower() not in p["school"].lower():
            continue
        if f.get("hbcu") and not p.get("hbcu"):
            continue
        name_hit = bool(q) and q.lower() in p["_text"]
        overlap = {t: idf.get(t, 0) for t in want if t in p["_terms"]}
        if q and not overlap and not name_hit:
            continue
        score = 0.0
        reasons = []
        if overlap:
            raw = sum(overlap.values())
            best = sum(sorted((idf.get(t, 0) for t in want), reverse=True)[: len(want)]) or 1
            score += 60 * min(1.0, raw / best) + 5 * min(len(overlap), 4)
            top = sorted(overlap, key=overlap.get, reverse=True)[:3]
            reasons.append("Their grants mention " + ", ".join(_unstem(t, q or profile.get("interests", "")) for t in top))
        if name_hit and not overlap:
            score += 40
        field_hit = [x for x in mine if x in p.get("fields", [])]
        if field_hit:
            score += 20
            if not overlap:
                reasons.append(f"Works in {field_hit[0]}")
        if pref and p.get("state") in pref:
            score += 10
            reasons.append(f"In {p['state']}, a state you picked")
        if p.get("hbcu") and profile.get("hbcu"):
            score += 10
            reasons.append("At an HBCU")
        if not want and not field_hit and not f.get("field") and not f.get("school") and not f.get("state") and not f.get("hbcu"):
            continue
        item = {k: v for k, v in p.items() if not k.startswith("_")}
        item.update(score=min(100, round(score)), reasons=reasons,
                    matched=[_unstem(t, q or profile.get("interests", "")) for t in sorted(overlap, key=overlap.get, reverse=True)[:5]])
        out.append(item)
    out.sort(key=lambda x: (-x["score"], x["name"]))
    limit = int(f.get("limit") or 40)
    return {"total": len(out), "items": out[:limit]}


def professor(args: dict) -> dict:
    for p in DATA["people"]:
        if p["id"] == args["id"]:
            return {k: v for k, v in p.items() if not k.startswith("_")}
    raise KeyError("That researcher is no longer in this week's data.")


# ---------------------------------------------------------------- email writer
GENERIC = [
    (re.compile(r"\bto whom it may concern\b", re.I), "Use their name instead of 'To whom it may concern'."),
    (re.compile(r"\bdear sir\b|\bdear madam\b", re.I), "Use their name and title instead of 'Dear Sir/Madam'."),
    (re.compile(r"\bI am passionate\b", re.I), "Show it with one specific thing you did instead of 'I am passionate'."),
    (re.compile(r"\[[^\]]+\]"), "Fill in the parts in [brackets]."),
]


def _first_sentence(text: str, limit: int = 160) -> str:
    text = (text or "").strip()
    m = re.match(r"(.{30,}?[.!?])\s", text + " ")
    s = m.group(1) if m else text
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def _topic(title: str) -> str:
    t = re.sub(r"^(collaborative research|career|rui|eager|reu site|conference)\s*:\s*", "", title or "", flags=re.I)
    t = re.sub(r"^(collaborative research|career|rui|eager)\s*:\s*", "", t, flags=re.I)
    return t.strip().rstrip(".")


def email(args: dict) -> dict:
    p = args.get("profile") or {}
    kind = args.get("kind") or "intro"
    target = args.get("target") or {}
    name = p.get("name") or "[Your name]"
    year = (p.get("year") or "[year]").lower()
    major = p.get("major") or "[major]"
    school = p.get("school") or "[your college]"
    skills = p.get("skills") or "[one or two skills, e.g. Python, lab work]"
    proof = p.get("project") or "[one project or class you are proud of, in one line]"
    when = args.get("when") or "this summer"
    ttype = target.get("type")

    if ttype == "professor":
        grant = (target.get("grants") or [{}])[0]
        last = (target.get("name") or "").split()[-1] if target.get("name") else "[Last name]"
        greet = f"Dear Dr. {last},"
        topic = _topic(grant.get("title", "")) or "[their research topic]"
        hook = f"I read the summary of your NSF-funded project, \u201c{topic},\u201d and {_first_sentence(args.get('why') or '') or 'it connects directly to what I want to learn'}"
        org = target.get("school") or "[their university]"
    else:
        greet = "Dear " + (target.get("contact") and f"Dr. {target['contact'].split()[-1]}," or "Program Coordinator,")
        topic = target.get("title") or "[program name]"
        hook = f"I am very interested in the {topic} program{' at ' + target['org'] if target.get('org') else ''}, and {_first_sentence(args.get('why') or '') or 'its focus fits what I want to work on'}"
        org = target.get("org") or "[organization]"
    hook = hook.rstrip(".") + "."

    if kind == "follow_up":
        subject = f"Following up: {('research position in your lab' if ttype == 'professor' else topic)}"
        body = f"""{greet}

I hope your week is going well. I wrote on [date of first email] about {'joining your research on ' + topic if ttype == 'professor' else 'the ' + topic + ' program'} and wanted to follow up in case my message got buried.

I am still very interested, and I am happy to share my résumé or meet for 15 minutes at a time that suits you.

Thank you for your time,
{name}
{year.title()}, {major}, {school}"""
    elif kind == "thank_you":
        subject = "Thank you for your time"
        body = f"""{greet}

Thank you for taking the time to talk with me about {topic}. I especially enjoyed hearing about [one thing you discussed].

{('I would be glad to help with your work ' + when + '. ') if ttype == 'professor' else ''}Please let me know if there is anything else you need from me.

Best regards,
{name}"""
    elif kind == "question" and ttype != "professor":
        subject = f"Question about {topic}"
        body = f"""{greet}

My name is {name}, a {year} {major} student at {school}. I am planning to apply to {topic} and had one question I could not find on the website:

[Your question, e.g. whether students from community colleges are eligible, or the expected start date.]

Thank you for your help,
{name}"""
    else:
        if ttype == "professor":
            subject = f"{year.title()} {major} student interested in your research on {_short_topic(topic)}"
            ask = f"Would you have room for an undergraduate researcher in your group {when}? I would also be grateful for any advice on how students usually get involved in your lab."
        else:
            subject = f"Interest in {topic}"
            ask = "Could you tell me whether there is anything I can do to strengthen my application, or whether there is someone I should speak with?"
        body = f"""{greet}

My name is {name}, and I am a {year} studying {major} at {school}. {hook}

{_skills_line(skills, proof)}

{ask} I have attached my résumé and am happy to meet at a time that works for you.

Thank you for your time,
{name}
{p.get('email_sig') or school}"""

    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    words = len(re.findall(r"\S+", body))
    tips = [msg for rx, msg in GENERIC if rx.search(body)]
    if words > 220:
        tips.append(f"It is {words} words. Professors skim; aim for under 200.")
    if ttype == "professor":
        tips.append("Find their email on their official university faculty page, not on other sites.")
        tips.append("Read one of their recent papers and mention it in one line. It makes the email stand out.")
    tips.append("Attach a one-page résumé as a PDF, and send from your school email.")
    return {"subject": subject, "body": body, "words": words, "tips": tips, "org": org}


def _short_topic(topic: str) -> str:
    words = topic.split()
    return " ".join(words[:7]) + ("…" if len(words) > 7 else "")


def _skills_line(skills: str, proof: str) -> str:
    line = f"I have experience with {skills.strip().rstrip('.')}"
    proof = proof.strip().rstrip(".")
    if proof.startswith("["):
        return f"{line}, and {proof}."
    proof = re.sub(r"^(i|i have|i've)\s+", "", proof, flags=re.I)
    return f"{line}, and I recently {proof[0].lower() + proof[1:]}."


# ---------------------------------------------------------------- tracker
STAGES = ["Interested", "Emailed", "Applied", "Interview", "Offer", "Declined", "Not selected"]


def tracker(args: dict) -> dict:
    """Sort saved items by urgency and say what to do next for each."""
    today = _today(args)
    rows = []
    for it in args.get("items", []):
        left = _days_left(it.get("deadline"), today)
        stage = it.get("stage") or "Interested"
        follow = _days_left(it.get("follow_up"), today)
        nudge = None
        if stage in ("Interested",) and left is not None and 0 <= left <= 14:
            nudge = f"Deadline in {left} day{'s' if left != 1 else ''}. Start the application."
        elif stage == "Emailed" and it.get("emailed_on"):
            since = -(_days_left(it["emailed_on"], today) or 0)
            if since >= 7:
                nudge = f"Emailed {since} days ago with no reply. Send a polite follow-up."
        elif stage == "Applied" and left is not None and left < 0:
            nudge = "Deadline passed. Results usually come in a few weeks; keep an eye on your inbox."
        if follow is not None and follow <= 0 and stage not in ("Offer", "Declined", "Not selected"):
            nudge = "Your reminder for today: " + (it.get("note") or "check on this one.")
        urgent = (left is not None and 0 <= left <= 7) or (follow is not None and follow <= 0)
        closed = stage in ("Offer", "Declined", "Not selected")
        rows.append(dict(it, days_left=left, nudge=nudge, urgent=urgent and not closed, closed=closed))
    rows.sort(key=lambda r: (r["closed"], not r["urgent"], r["days_left"] is None, r["days_left"] if r["days_left"] is not None else 0))
    counts = Counter(r.get("stage") or "Interested" for r in rows)
    return {"items": rows, "counts": {s: counts.get(s, 0) for s in STAGES}, "stages": STAGES,
            "due_soon": sum(1 for r in rows if r["urgent"])}


def ics(args: dict) -> str:
    """An .ics calendar with each deadline (all-day) and a reminder 7 days before."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Research Compass//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for it in args.get("items", []):
        for label, key in (("Deadline", "deadline"), ("Reminder", "follow_up")):
            date = it.get(key)
            if not date:
                continue
            try:
                d = dt.date.fromisoformat(date)
            except ValueError:
                continue
            uid = re.sub(r"[^A-Za-z0-9]", "", f"{it.get('id', '')}{key}") + "@research-compass"
            title = _ics_escape(f"{label}: {it.get('title', '')}")
            desc = _ics_escape(" · ".join(x for x in [it.get("org"), it.get("url"), it.get("note")] if x))
            lines += [
                "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(d + dt.timedelta(days=1)).strftime('%Y%m%d')}",
                f"SUMMARY:{title}", f"DESCRIPTION:{desc}",
                "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{title}", "TRIGGER:-P7D", "END:VALARM",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(l) for l in lines) + "\r\n"


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    out, cur = [], line
    while len(cur.encode("utf-8")) > 74:
        cut = 74
        while len(cur[:cut].encode("utf-8")) > 74:
            cut -= 1
        out.append(cur[:cut])
        cur = " " + cur[cut:]
    out.append(cur)
    return "\r\n".join(out)


# ---------------------------------------------------------------- résumé reader
# Reads the plain text of a résumé (taken out of the PDF or Word file in the browser) and
# suggests profile fields. Nothing leaves the device here; main.js only fills empty fields
# and the student checks everything before saving.
MAJOR_WORDS: list[tuple[str, str]] = [
    ("machine learning", "AI and machine learning"), ("artificial intelligence", "AI and machine learning"),
    ("data science", "Computer science"), ("computer science", "Computer science"), ("computer engineering", "Electrical engineering"),
    ("software", "Computer science"), ("information technology", "Computer science"), ("information systems", "Computer science"),
    ("cybersecurity", "Computer science"), ("computing", "Computer science"), ("robotics", "Robotics"),
    ("electrical", "Electrical engineering"), ("mechanical", "Mechanical and aerospace"), ("aerospace", "Mechanical and aerospace"),
    ("civil", "Civil and environmental eng."), ("environmental engineering", "Civil and environmental eng."),
    ("biomedical", "Biomedical and health"), ("nursing", "Biomedical and health"), ("public health", "Biomedical and health"),
    ("kinesiology", "Biomedical and health"), ("pharm", "Biomedical and health"), ("health", "Biomedical and health"),
    ("biochem", "Chemistry"), ("chemistry", "Chemistry"), ("chemical", "Chemistry"), ("materials", "Materials science"),
    ("physics", "Physics and astronomy"), ("astronomy", "Physics and astronomy"), ("statistics", "Mathematics and statistics"),
    ("mathematics", "Mathematics and statistics"), ("math", "Mathematics and statistics"), ("biology", "Biology"),
    ("microbiology", "Biology"), ("neuroscience", "Biology"), ("ecology", "Ecology and environment"),
    ("environmental science", "Ecology and environment"), ("geology", "Earth and ocean sciences"), ("geoscience", "Earth and ocean sciences"),
    ("marine", "Earth and ocean sciences"), ("ocean", "Earth and ocean sciences"), ("agri", "Agriculture and food"),
    ("food science", "Agriculture and food"), ("energy", "Energy"), ("psychology", "Psychology and social sciences"),
    ("sociology", "Psychology and social sciences"), ("economics", "Psychology and social sciences"),
    ("political science", "Psychology and social sciences"), ("education", "Education research"), ("engineering", "Mechanical and aerospace"),
]
_HEADERS = {
    "skills": r"(technical |core |relevant |key )?skills( (and|&) (tools|technologies|interests))?|technologies|tools|technical proficienc(y|ies)|languages (and|&) tools",
    "projects": r"(academic |personal |selected |relevant |technical )?projects?",
    "interests": r"(research )?interests?|areas of interest",
    "education": r"education|academic background",
    "other": r"(work |professional |research |relevant |leadership )?experience|employment|work history|leadership|activities|"
             r"involvement|honors|awards|certifications?|publications|volunteer(ing)?|summary|profile|objective|coursework|"
             r"relevant coursework|references|contact",
}
_SCHOOL = re.compile(r"[^|•·,;\t]*\b(university|college|institute of technology|institute|polytechnic|community college)\b[^|•·,;\t]*", re.I)
_GRAD = re.compile(r"(?:expected|anticipated|graduat\w*|class of|exp\.?)\D{0,20}((?:19|20)\d\d)|(?:may|june|december|dec|august|aug|spring|fall|summer|winter)\s+((?:20)\d\d)", re.I)
_NOISE = re.compile(r"\S+@\S+|https?://\S+|www\.\S+|\+?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}")


def _header(line: str) -> str | None:
    t = re.sub(r"[^a-z& ]", "", line.lower()).strip()
    if not t or len(t) > 40:
        return None
    for name, pat in _HEADERS.items():
        if re.fullmatch(pat, t):
            return name
    return None


def _sections(lines: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"top": []}
    cur = "top"
    for ln in lines:
        h = _header(ln)
        if h:
            cur = h
            out.setdefault(cur, [])
            continue
        out.setdefault(cur, []).append(ln)
    return out


def _class_year(text: str, today: dt.date) -> str:
    low = text.lower()
    for word, year in (("sophomore", "Sophomore"), ("junior", "Junior"), ("senior", "Senior"), ("freshman", "Freshman"), ("first-year", "Freshman")):
        if re.search(rf"\b{word}\b", low):
            return year
    if re.search(r"\b(associate of|associate's|a\.a\.|a\.s\.|community college)\b", low):
        return "Community college"
    if re.search(r"\b(m\.s\.|master of|ph\.?d|doctor of)\b", low) and not re.search(r"\b(b\.s\.|b\.a\.|bachelor)", low):
        return "Graduate"
    years = [int(a or b) for a, b in _GRAD.findall(text)]
    years = [y for y in years if today.year - 1 <= y <= today.year + 6]
    if not years:
        return ""
    left = max(years) - (today.year + 1 if today.month >= 7 else today.year)
    return {0: "Senior", 1: "Junior", 2: "Sophomore"}.get(left, "Freshman" if left >= 3 else "")


def _major(text: str, allowed: list[str]) -> str:
    low = text.lower()
    degree = re.search(r"(bachelor[^\n]{0,80}|\bb\.?\s?s\.?\b[^\n]{0,80}|\bb\.?\s?a\.?\b[^\n]{0,80}|major[^\n]{0,80}|associate[^\n]{0,80})", low)
    for scope in ([degree.group(0)] if degree else []) + [low]:
        for word, field in MAJOR_WORDS:
            if word in scope and (not allowed or field in allowed):
                return field
    return ""


def _clean_item(s: str) -> str:
    return re.sub(r"^[\-–•·*●▪◦\s]+", "", s).strip(" .;")


def resume(args: dict) -> dict:
    text = _NOISE.sub(" ", str(args.get("text") or ""))[:30000]
    allowed = list(args.get("fields") or [])
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    sec = _sections(lines)
    out = {"name": "", "school": "", "year": "", "major": "", "gpa": "", "skills": "", "interests": "", "project": ""}

    for ln in sec.get("top", [])[:6] or lines[:3]:
        words = ln.replace(",", " ").split()
        if 2 <= len(words) <= 4 and not re.search(r"\d|@|résumé|resume|curriculum", ln, re.I) and all(w[:1].isupper() for w in words):
            out["name"] = ln.title() if ln.isupper() else ln
            break

    edu = " \n".join(sec.get("education", [])) or "\n".join(lines[:25])
    m = _SCHOOL.search(edu) or _SCHOOL.search(text)
    if m:
        out["school"] = re.sub(r"\s+", " ", m.group(0)).strip(" -–—()")[:120]
    out["year"] = _class_year(edu, _today(args))
    out["major"] = _major(edu, allowed)
    g = re.search(r"\bgpa\b[^0-9]{0,15}([0-4]\.\d{1,2})(?:\s*/\s*4(?:\.0+)?)?", text, re.I)
    if g and 0 < float(g.group(1)) <= 4.0:
        out["gpa"] = g.group(1)

    skills: list[str] = []
    for ln in sec.get("skills", []):
        ln = re.sub(r"^[^:]{1,30}:\s*", "", _clean_item(ln))
        for part in re.split(r"[,;|•·]| {2,}", ln):
            part = part.strip(" .")
            if 1 < len(part) <= 40 and part.lower() not in {s.lower() for s in skills}:
                skills.append(part)
    out["skills"] = ", ".join(skills[:20])

    out["interests"] = ", ".join(_clean_item(x) for x in sec.get("interests", [])[:3])[:300]

    proj = [_clean_item(x) for x in sec.get("projects", [])]
    proj = [x for x in proj if x]
    if proj:
        title = re.sub(r"\s*[\(|–—-]?\s*((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s*)?(19|20)\d\d.*$", "", proj[0], flags=re.I).strip(" |–—-")
        detail = next((x for x in proj[1:3] if re.match(r"(built|developed|designed|created|made|wrote|implemented|trained|analyzed)\b", x, re.I)), "")
        out["project"] = (detail or title)[:200]
    return out


# ---------------------------------------------------------------- bridge
FUNCS = {f.__name__: f for f in (load, programs, program, listing, professors, professor, email, tracker, ics, resume)}


def call(name: str, args_json: str) -> str:
    try:
        return json.dumps({"ok": FUNCS[name](json.loads(args_json or "{}"))})
    except Exception as err:  # shown to the user as a friendly message
        return json.dumps({"error": f"{type(err).__name__}: {err}"})
