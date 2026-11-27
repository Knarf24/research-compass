"""Tests for the weekly data job and the in-browser logic. Standard library only.

Run:  python tests/test_compass.py
The network is never used: NSF and USAJOBS answers are faked below.
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "web"))
os.environ["COMPASS_TODAY"] = "2026-11-27"

import compass  # noqa: E402
import fetch_data  # noqa: E402
import submissions  # noqa: E402
import share_pages  # noqa: E402

REU = [
    {"id": "2400001", "title": "REU Site: Machine Learning for Robotics and Autonomous Systems", "awardeeName": "NORTH CAROLINA STATE UNIVERSITY",
     "awardeeCity": "RALEIGH", "awardeeStateCode": "NC", "piFirstName": "Ada", "piLastName": "Lovelace", "expDate": "08/31/2027",
     "abstractText": "This REU Site trains ten undergraduates each summer in machine learning for robots. Details at https://reu.example.edu/ml. This award reflects NSF's statutory mission and has been deemed worthy of support.",
     "fundProgramName": "RSCH EXPER FOR UNDERGRAD SITES"},
    {"id": "2400002", "title": "Collaborative Research: REU Site: Coastal Ocean Ecology", "awardeeName": "UNIVERSITY OF FLORIDA",
     "awardeeCity": "GAINESVILLE", "awardeeStateCode": "FL", "piFirstName": "Rachel", "piLastName": "Carson", "expDate": "06/30/2028",
     "abstractText": "Students study marine ecosystems and coastal ecology. Questions: r.carson@ufl.edu.", "fundProgramName": "RSCH EXPER FOR UNDERGRAD SITES"},
    {"id": "2400003", "title": "Conference: Undergraduate Research Symposium", "awardeeName": "SOME COLLEGE", "awardeeStateCode": "TX",
     "expDate": "06/30/2028", "abstractText": "A conference.", "fundProgramName": "OTHER"},
]
GRANTS = [
    {"id": "2300101", "title": "CAREER: Robust Deep Learning for Medical Imaging", "awardeeName": "DUKE UNIVERSITY", "awardeeCity": "DURHAM",
     "awardeeStateCode": "NC", "piFirstName": "Grace", "piLastName": "Hopper", "expDate": "05/31/2029",
     "abstractText": "This project develops deep learning methods for medical imaging and cancer detection."},
    {"id": "2300102", "title": "Soil Microbial Communities and Crop Yield", "awardeeName": "IOWA STATE UNIVERSITY", "awardeeCity": "AMES",
     "awardeeStateCode": "IA", "piFirstName": "George", "piLastName": "Carver", "expDate": "07/31/2028",
     "abstractText": "We study how soil microbes affect crop yield in agriculture."},
    {"id": "2300103", "title": "Workshop: Future of Computing", "awardeeName": "STANFORD UNIVERSITY", "awardeeStateCode": "CA",
     "piFirstName": "Alan", "piLastName": "Turing", "expDate": "07/31/2028", "abstractText": "A workshop."},
    {"id": "2300104", "title": "Graph Algorithms for Traffic Flow Prediction", "awardeeName": "Some Company LLC", "awardeeStateCode": "NC",
     "piFirstName": "Ed", "piLastName": "Dijkstra", "expDate": "07/31/2028", "abstractText": "Industry project."},
]
JOBS = {"SearchResult": {"SearchResultItems": [
    {"MatchedObjectId": "801", "MatchedObjectDescriptor": {
        "PositionTitle": "Student Trainee (Biological Science)", "PositionURI": "https://www.usajobs.gov/job/801",
        "OrganizationName": "Agricultural Research Service", "DepartmentName": "Department of Agriculture",
        "PositionLocation": [{"CityName": "Beltsville, Maryland", "CountrySubDivisionCode": "Maryland"}],
        "ApplicationCloseDate": "2026-12-15T23:59:59.997", "PositionRemuneration": [{"MinimumRange": "17.50", "MaximumRange": "22.00", "Description": "Per Hour"}],
        "JobCategory": [{"Name": "General Biological Science"}], "UserArea": {"Details": {"JobSummary": "Help with plant biology research in the lab."}}}},
    {"MatchedObjectId": "802", "MatchedObjectDescriptor": {
        "PositionTitle": "Old Job", "PositionURI": "https://www.usajobs.gov/job/802", "ApplicationCloseDate": "2026-10-01T00:00:00",
        "PositionLocation": [{}], "UserArea": {"Details": {}}}},
]}}


def fake_get_json(url, headers=None, tries=3):
    if "usajobs" in url:
        return JOBS
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert '"' not in url, "quotes must be percent-encoded (the live API returns 400 otherwise)"
    if q.get("offset", ["0"])[0] != "0":
        return {"response": {"award": []}}
    if q["keyword"][0] == '"REU Site"':
        return {"response": {"award": REU}}
    return {"response": {"award": GRANTS}}


class DataJob(unittest.TestCase):
    def setUp(self):
        fetch_data.get_json = fake_get_json
        fetch_data.PAUSE = 0
        self.tmp = Path(tempfile.mkdtemp())

    def build(self, **env):
        old = {k: os.environ.get(k) for k in env}
        os.environ.update({k: v for k, v in env.items() if v is not None})
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
        try:
            fetch_data.build(self.tmp)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return (json.loads((self.tmp / "listings.json").read_text()), json.loads((self.tmp / "professors.json").read_text()))

    def test_reu_sites_are_cleaned(self):
        listings, _ = self.build(USAJOBS_API_KEY=None, USAJOBS_EMAIL=None)
        reu = listings["by_source"]["NSF REU Sites"]
        self.assertEqual(len(reu), 2, "the conference award is not an REU site")
        first = reu[0]
        self.assertEqual(first["title"], "Machine Learning for Robotics and Autonomous Systems")
        self.assertEqual(first["org"], "North Carolina State University")
        self.assertEqual(first["city"], "Raleigh")
        self.assertEqual(first["url"], "https://reu.example.edu/ml", "uses the site's own link from the abstract")
        self.assertNotIn("statutory mission", first["summary"])
        self.assertIn("AI and machine learning", first["fields"])
        self.assertIn("Robotics", first["fields"])
        self.assertEqual(reu[1]["url"], "https://www.nsf.gov/awardsearch/showAward?AWD_ID=2400002")
        self.assertNotIn("@", json.dumps(listings), "personal emails are never republished")
        self.assertNotIn("Biology", first["fields"], "one passing word in an abstract doesn't tag a field")

    def test_usajobs_is_optional_and_expired_jobs_are_dropped(self):
        listings, _ = self.build(USAJOBS_API_KEY=None, USAJOBS_EMAIL=None)
        self.assertFalse(listings["sources"]["USAJOBS"]["ok"])
        self.assertIn("skipped", listings["sources"]["USAJOBS"]["note"])
        listings, _ = self.build(USAJOBS_API_KEY="k", USAJOBS_EMAIL="me@example.com")
        jobs = listings["by_source"]["USAJOBS"]
        self.assertEqual([j["id"] for j in jobs], ["usajobs-801"])
        self.assertEqual(jobs[0]["state"], "MD")
        self.assertEqual(jobs[0]["pay"], "$17.50–$22.00 per hour")
        self.assertIn("Biology", jobs[0]["fields"])

    def test_professors_skip_workshops_and_companies_and_never_store_emails(self):
        _, profs = self.build()
        names = sorted(p["name"] for p in profs["people"])
        self.assertEqual(names, ["George Carver", "Grace Hopper"])
        self.assertNotIn("@", json.dumps(profs))

    def test_failed_source_keeps_last_good_copy(self):
        self.build()
        def broken(url, headers=None, tries=3):
            raise RuntimeError("NSF is down")
        fetch_data.get_json = broken
        listings, profs = self.build()
        self.assertEqual(len(listings["by_source"]["NSF REU Sites"]), 2)
        self.assertTrue(listings["sources"]["NSF REU Sites"]["kept_previous"])
        self.assertEqual(len(profs["people"]), 2)

    def test_expired_items_are_removed(self):
        self.assertFalse(fetch_data.not_expired({"deadline": "2026-11-01"}, "2026-11-27"))
        self.assertTrue(fetch_data.not_expired({"deadline": None, "ends": "2027-01-01"}, "2026-11-27"))
        self.assertTrue(fetch_data.not_expired({}, "2026-11-27"))

    def test_curated_programs_file_is_valid(self):
        items = json.loads((ROOT / "data" / "curated_programs.json").read_text())
        for it in items:
            for key in ("id", "title", "org", "url", "summary", "checked"):
                self.assertTrue(it.get(key), f"{it.get('id')} is missing {key}")
            self.assertTrue(it["url"].startswith("https://"))


class Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fetch_data.get_json = fake_get_json
        fetch_data.PAUSE = 0
        tmp = Path(tempfile.mkdtemp())
        os.environ["USAJOBS_API_KEY"], os.environ["USAJOBS_EMAIL"] = "k", "me@example.com"
        fetch_data.build(tmp)
        cls.info = cls.call("load", {"listings": (tmp / "listings.json").read_text(), "professors": (tmp / "professors.json").read_text()})

    @staticmethod
    def call(name, args):
        out = json.loads(compass.call(name, json.dumps(args)))
        if "error" in out:
            raise AssertionError(out["error"])
        return out["ok"]

    def test_load_counts(self):
        self.assertGreaterEqual(self.info["listings"], 3 + 7)
        self.assertEqual(self.info["professors"], 2)
        self.assertIn("NC", self.info["states"])

    def test_ranking_follows_the_profile(self):
        me = {"major": "AI and machine learning", "year": "Junior", "interests": "robots", "citizen": "yes", "states": ["NC"]}
        res = self.call("programs", {"profile": me, "today": "2026-11-27"})
        top = res["items"][0]
        self.assertEqual(top["id"], "nsf-2400001")
        self.assertTrue(any("major" in r for r in top["reasons"]))
        bio = self.call("programs", {"profile": {"major": "Biology", "year": "Sophomore"}, "today": "2026-11-27"})
        self.assertNotEqual(bio["items"][0]["id"], "nsf-2400001")

    def test_citizenship_warning_and_eligible_filter(self):
        me = {"major": "Biology", "citizen": "no"}
        res = self.call("programs", {"profile": me, "filters": {"q": "coastal"}, "today": "2026-11-27"})
        self.assertTrue(res["items"][0]["warnings"])
        res = self.call("programs", {"profile": me, "filters": {"q": "coastal", "eligible": True}, "today": "2026-11-27"})
        self.assertEqual(res["total"], 0)

    def test_filters(self):
        res = self.call("programs", {"filters": {"state": "FL"}, "today": "2026-11-27"})
        self.assertEqual([i["id"] for i in res["items"]], ["nsf-2400002"])
        res = self.call("programs", {"filters": {"sort": "deadline"}, "today": "2026-11-27"})
        days = [i["days_left"] for i in res["items"] if i["days_left"] is not None]
        self.assertEqual(days, sorted(days), "soonest deadline first")
        self.assertIn("usajobs-801", [i["id"] for i in res["items"][:4]])

    def test_hbcu_priority(self):
        me = {"major": "Ecology and environment", "hbcu": True}
        res = self.call("programs", {"profile": me, "today": "2026-11-27"})
        top = res["items"][0]
        self.assertTrue(top.get("hbcu_focus") or top.get("hbcu_host"))
        self.assertTrue(any("HBCU" in r for r in top["reasons"]))
        only = self.call("programs", {"profile": {}, "filters": {"hbcu": True}, "today": "2026-11-27"})
        self.assertTrue(only["total"] >= 4 and all(i.get("hbcu_focus") or i.get("hbcu_host") for i in only["items"]))

    def test_international_students(self):
        me = {"major": "Biology", "citizen": "no"}
        res = self.call("programs", {"profile": me, "today": "2026-11-27"})
        top = res["items"][0]
        self.assertEqual(top["international"], "yes")
        self.assertTrue(any("international" in r.lower() for r in top["reasons"]))
        reu = next(i for i in res["items"] if i["kind"] == "reu")
        self.assertTrue(any("citizen" in w.lower() for w in reu["warnings"]))
        open_only = self.call("programs", {"profile": me, "filters": {"intl": True}, "today": "2026-11-27"})
        self.assertTrue(open_only["items"] and all(i["international"] == "yes" for i in open_only["items"]))

    def test_hbcu_hosts_are_recognised(self):
        self.assertTrue(fetch_data.is_hbcu("North Carolina Agricultural & Technical State University", "NC"))
        self.assertTrue(fetch_data.is_hbcu("Lincoln University", "PA"))
        self.assertFalse(fetch_data.is_hbcu("Lincoln University", "NE"))
        self.assertFalse(fetch_data.is_hbcu("Duke University", "NC"))

    def test_professor_finder(self):
        res = self.call("professors", {"profile": {"interests": "deep learning for cancer imaging"}, "filters": {}})
        self.assertEqual(res["items"][0]["name"], "Grace Hopper")
        self.assertTrue(res["items"][0]["matched"])
        res = self.call("professors", {"profile": {}, "filters": {"q": "soil"}})
        self.assertEqual([p["name"] for p in res["items"]], ["George Carver"])

    def test_email_for_professor(self):
        prof = self.call("professors", {"profile": {}, "filters": {"q": "imaging"}})["items"][0]
        me = {"name": "Sam Rivera", "year": "Junior", "major": "Computer science", "school": "Example College", "skills": "Python and PyTorch",
              "project": "Built a traffic forecasting app in Python"}
        out = self.call("email", {"profile": me, "target": dict(prof, type="professor"), "kind": "intro", "why": "I want to apply ML to health."})
        self.assertIn("Dear Dr. Hopper,", out["body"])
        self.assertIn("Robust Deep Learning for Medical Imaging", out["body"])
        self.assertNotIn("CAREER:", out["body"])
        self.assertNotIn("[", out["body"])
        self.assertLess(out["words"], 220)
        follow = self.call("email", {"profile": me, "target": dict(prof, type="professor"), "kind": "follow_up"})
        self.assertIn("follow up", follow["body"])

    def test_email_placeholders_flagged(self):
        out = self.call("email", {"profile": {}, "target": {"type": "program", "title": "SURF", "org": "NIST"}, "kind": "intro"})
        self.assertTrue(any("brackets" in t for t in out["tips"]))

    def test_tracker_and_calendar(self):
        items = [
            {"id": "a", "title": "SURF", "deadline": "2026-12-01", "stage": "Interested"},
            {"id": "b", "title": "Lab of Dr. Hopper", "stage": "Emailed", "emailed_on": "2026-11-10"},
            {"id": "c", "title": "Old", "deadline": "2027-03-01", "stage": "Not selected"},
        ]
        t = self.call("tracker", {"items": items, "today": "2026-11-27"})
        self.assertEqual(t["items"][0]["id"], "a")
        self.assertIn("Deadline in 4 days", t["items"][0]["nudge"])
        self.assertIn("follow-up", t["items"][1]["nudge"])
        self.assertTrue(t["items"][-1]["closed"])
        cal = self.call("ics", {"items": items})
        self.assertIn("BEGIN:VCALENDAR", cal)
        self.assertIn("DTSTART;VALUE=DATE:20261201", cal)
        self.assertIn("TRIGGER:-P7D", cal)
        self.assertTrue(all(len(l.encode()) <= 75 for l in cal.split("\r\n")))


RESUME = """JORDAN A. SMITH
Charlotte, NC | jordan@example.com | (704) 555-1234 | github.com/jsmith

EDUCATION
North Carolina A&T State University, Greensboro, NC
Bachelor of Science in Computer Science, Expected May 2028

PROJECTS
TrafficFlow | Python, scikit-learn  (Aug 2026)
- Built a traffic forecasting app that predicts congestion from sensor data

EXPERIENCE
IT Help Desk Assistant, Campus IT, 2025 - present

TECHNICAL SKILLS
Languages: Python, JavaScript, SQL
Tools: Git, Pandas | Excel
"""

FORM = """### Program or position name

Summer Robotics Research Program

### University, lab or organization

Example State University

### Official page (link)

https://robotics.example.edu/summer

### City and state

Raleigh, NC

### Application deadline

2027-02-15

### Is it paid?

Yes

### Can international students apply?

Not sure

### Is it made for HBCU students?

No

### Short description in your own words (2-3 sentences)

Ten weeks building field robots. Email me at prof@example.edu.

### Who can apply?

_No response_

### How do you know about it?

I found it online
"""


class ResumeAndSubmissionTests(unittest.TestCase):
    def test_resume_fills_profile_fields(self):
        out = compass.resume({"text": RESUME, "today": "2026-11-27", "fields": ["Computer science", "Biology"]})
        self.assertEqual(out["name"], "Jordan A. Smith")
        self.assertEqual(out["school"], "North Carolina A&T State University")
        self.assertEqual(out["year"], "Junior")          # graduates May 2028
        self.assertEqual(out["major"], "Computer science")
        self.assertIn("Python", out["skills"])
        self.assertIn("Excel", out["skills"])
        self.assertTrue(out["project"].startswith("Built a traffic forecasting app"))
        self.assertNotIn("@", json.dumps(out))

    def test_resume_handles_empty_text(self):
        out = compass.resume({"text": "", "today": "2026-11-27"})
        self.assertEqual(set(out.values()), {""})

    def test_submission_form_checks_and_listing(self):
        form = submissions.parse_form(FORM)
        self.assertEqual(form["title"], "Summer Robotics Research Program")
        self.assertEqual(form["eligibility"], "")
        page = "<html><h1>Summer Robotics Research Program</h1><p>Stipend: $6,000</p></html>"
        fake = lambda url: (200, url, page.lower())  # noqa: E731
        checks = submissions.run_checks(form, compass.dt.date(2026, 11, 27), fetch=fake)
        icons = dict((m, i) for i, m in checks)
        self.assertEqual(icons["The official page opens."], "✅")
        self.assertEqual(icons["The page mentions the program by name."], "✅")
        self.assertTrue(any("email address" in m for _, m in checks))
        self.assertFalse(any(i == "❌" for i, _ in checks))
        dead = submissions.run_checks({**form, "deadline": "2026-01-01"}, compass.dt.date(2026, 11, 27), fetch=lambda u: (404, u, ""))
        self.assertEqual(sum(1 for i, _ in dead if i == "❌"), 2)
        item = submissions.to_listing(form, 12, compass.dt.date(2026, 11, 27))
        self.assertEqual(item["state"], "NC")
        self.assertEqual(item["deadline"], "2027-02-15")
        self.assertTrue(item["paid"])
        self.assertEqual(item["international"], "check")
        self.assertNotIn("@", item["summary"])
        self.assertIn("Robotics", item["fields"])


class MatchScoreTests(unittest.TestCase):
    def setUp(self):
        self.r = {"title": "SURF Engineering", "summary": "Summer lab research for students interested in an engineering PhD. GPA 3.0 or higher.",
                  "fields": ["Computer science"], "years": ["Sophomore", "Junior"], "international": "yes", "paid": True,
                  "deadline": "2027-02-01", "state": "CA"}
        self.r["_text"] = (self.r["title"] + " " + self.r["summary"] + " computer science").lower()
        self.today = compass.dt.date(2026, 11, 27)

    def score(self, **profile):
        base = {"major": "Computer science", "year": "Junior", "citizen": "no", "interests": "engineering"}
        base.update(profile)
        return compass._score_listing(self.r, base, self.today)

    def test_unknown_gpa_caps_below_100(self):
        s, reasons, warnings, blocked = self.score()
        self.assertLessEqual(s, 90)
        self.assertFalse(blocked)
        self.assertTrue(any("GPA of 3.0" in w for w in warnings))
        self.assertTrue(any("PhD" in w for w in warnings))

    def test_meeting_everything_can_reach_100(self):
        s, reasons, warnings, blocked = self.score(gpa="3.4", interests="engineering, lab research")
        self.assertEqual(s, 100)
        self.assertIn("Meets the 3.0 GPA minimum", reasons)

    def test_unmet_requirements_cap_at_40_and_block(self):
        for prof in ({"gpa": "2.5"}, {"gpa": "3.4", "year": "Senior"}):
            s, _, warnings, blocked = self.score(**prof)
            self.assertLessEqual(s, 40)
            self.assertTrue(blocked)
        self.r["international"] = "no"
        s, _, warnings, blocked = self.score(gpa="3.4")
        self.assertTrue(blocked)
        self.assertIn("Requires US citizenship or permanent residency", warnings)

    def test_interest_phrases(self):
        _, reasons, _, _ = self.score(interests="machine learning, engineering", gpa="3.4")
        self.assertIn("Mentions engineering", reasons)
        self.assertFalse(any("machine" in r for r in reasons))

    def test_resume_reads_gpa(self):
        self.assertEqual(compass.resume({"text": RESUME + "\nGPA: 3.62/4.0", "today": "2026-11-27"})["gpa"], "3.62")


class ShareTests(unittest.TestCase):
    def test_share_page(self):
        self.assertEqual(share_pages.slug("NSF-2548170"), "nsf-2548170")
        self.assertEqual(share_pages.slug("usajobs:12 34"), "usajobs-12-34")
        r = {"id": "stanford-surf", "kind": "university", "title": "SURF <Engineering>", "org": "Stanford", "city": "Stanford", "state": "CA",
             "summary": "Summer research.", "url": "https://engineering.stanford.edu/SURF", "deadline": "2027-02-01", "paid": True,
             "international": "yes", "fields": ["Computer science"], "source": "University programs", "checked": "2026-11-27"}
        html = share_pages.page(r, "2026-11-27")
        self.assertIn('<meta property="og:title" content="SURF &lt;Engineering&gt;">', html)
        self.assertIn("https://knarf24.github.io/research-compass/p/stanford-surf/", html)
        self.assertIn('href="https://engineering.stanford.edu/SURF"', html)
        self.assertIn("../../#p=stanford-surf", html)
        self.assertIn("Deadline Feb 1, 2027", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
