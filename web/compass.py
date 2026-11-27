"""Research Compass: the thinking part, in Python, running in the browser with Pyodide.

The page (main.js) only draws and saves. Everything here is plain standard-library Python:

  * load            read the weekly data files
  * programs        rank programs for a student's profile, with the reasons for each match
  * professors      find researchers with active NSF grants that match the student's interests
  * email           draft a cold email (or follow-up / thank-you) from the profile and the target
  * tracker         deadlines, days left and reminders for the student's saved applications
  * ics             a calendar file with every deadline, for Google/Apple/Outlook calendars

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
def _score_listing(r: dict, profile: dict, today: dt.date) -> tuple[int, list[str], list[str]]:
    """Return (score 0-100, reasons it fits, warnings)."""
    reasons, warnings = [], []
    score = 0.0
    mine = _profile_fields(profile)
    fields = r.get("fields", [])
    hit = [f for f in mine if f in fields]
    if mine:
        if profile.get("major") in fields:
            score += 45
            reasons.append(f"Matches your major ({profile['major']})")
        elif hit:
            score += 32
            reasons.append(f"Matches your interest in {hit[0]}")
        elif not fields:
            score += 12
    else:
        score += 25

    words = _words(profile.get("interests", ""))
    if words:
        found = sorted({w for w in words if _stem(w) in r["_text"]})
        if found:
            score += min(15, 5 * len(found))
            reasons.append("Mentions " + ", ".join(found[:3]))

    year = profile.get("year")
    years = r.get("years") or []
    if year and years:
        if year in years:
            score += 15
        else:
            score -= 25
            warnings.append(f"Usually for {', '.join(years)} students")
    else:
        score += 8

    if r.get("paid"):
        score += 8
        if profile.get("paid_only"):
            reasons.append("Paid")

    intl = r.get("international") or ("no" if CITIZEN_ONLY.search(r.get("citizenship") or "") else "check")
    if profile.get("citizen") == "no":
        if intl == "yes":
            score += 22
            reasons.append("Open to international students")
        elif intl == "no":
            score -= 45
            warnings.append("Requires US citizenship or permanent residency")
        else:
            warnings.append("Check whether international students can apply")

    hbcu = r.get("hbcu_focus") or r.get("hbcu_host")
    if hbcu and profile.get("hbcu"):
        score += 18
        reasons.append("Made for HBCU students" if r.get("hbcu_focus") else "Hosted at an HBCU")

    st = r.get("state")
    pref = profile.get("states") or []
    if pref and st:
        if st in pref:
            score += 12
            reasons.append(f"In {st}, a state you picked")
    elif not st:
        score += 4

    left = _days_left(r.get("deadline"), today)
    if left is not None:
        if 0 <= left <= 14:
            warnings.append(f"Deadline in {left} day{'s' if left != 1 else ''}")
        elif left > 14:
            score += 5
    return max(0, min(100, round(score))), reasons, warnings


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
        score, reasons, warnings = _score_listing(r, profile, today)
        if f.get("eligible") and any(w.startswith(("Requires", "Usually")) for w in warnings):
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


# ---------------------------------------------------------------- bridge
FUNCS = {f.__name__: f for f in (load, programs, listing, professors, professor, email, tracker, ics)}


def call(name: str, args_json: str) -> str:
    try:
        return json.dumps({"ok": FUNCS[name](json.loads(args_json or "{}"))})
    except Exception as err:  # shown to the user as a friendly message
        return json.dumps({"error": f"{type(err).__name__}: {err}"})
