# 🧭 Research Compass

**Live site:** https://research-compass.studentlabs.me/ (also at https://knarf24.github.io/research-compass/)

**The problem:** paid undergraduate research in the US is spread over hundreds of campus pages, agency sites and
directories. Students don't know where to look, which programs they actually qualify for, or how to approach a
professor, and deadlines slip by.

**The app:** set up a short profile (major, year, interests, skills) and Research Compass ranks open research
opportunities for you, explains why each one fits, finds professors with funded research in your area, drafts the
cold email, and tracks every deadline. Free, no sign-up, and your data never leaves your browser.

- **🧭 Programs:** university-run summer research programs, NSF REU summer sites at US campuses, national lab and
  agency programs (DOE, NASA, NIST, NOAA and more), federal student jobs, and reviewed community submissions. Each one gets a match score, the reasons it fits,
  and warnings (for example "US citizens only" or "deadline in 5 days"). Filter by field, state and type, or show only
  the ones you qualify for.
- **🔬 Professor finder:** researchers at US universities with an **active NSF grant**, matched to what you want to
  work on using the words in their grant summaries. Each links to the public NSF award page and helps you find
  their official faculty page. No emails are collected.
- **🎓 HBCU students:** programs made for HBCU students and programs hosted at HBCUs (matched against the
  Department of Education HBCU list) are badged, can be filtered, and rank first when you say you attend an HBCU.
- **🌍 International students:** every listing says whether international students can apply (yes, no, or check).
  Programs open to them rank first; US-citizens-only programs are clearly flagged.
- **✉️ AI email writer:** Google Gemini (free tier) writes a short, specific email from your profile and the program or professor's
  real project, with a different structure each time, and can make it shorter, warmer, more formal or more
  confident. The API key stays in a small Vercel function (`ai-proxy/`). Without it, a built-in template writer
  is used.
- **📄 Résumé fill:** upload a PDF, Word or text résumé while setting up your profile and it fills in the empty
  fields (name, college, year, major, skills, interests, a project) for you to check and edit. The file is read in
  your browser and never uploaded; if the AI writer is on and you allow it, the text (without email, phone and links)
  is read by Gemini for better results.
- **📋 Tracker:** save programs and professors, move them through stages (Interested → Emailed → Applied →
  Interview → Offer), get reminders ("emailed 9 days ago, send a follow-up"), and download every deadline to
  your calendar with a 7-day alert. Back up and restore as a file.

The ranking, professor matching, email writer, reminders and calendar file are **Python** (`web/compass.py`,
standard library only) running **inside your browser** with [Pyodide](https://pyodide.org). It is hosted free on
GitHub Pages and works offline after the first visit.

## Where the data comes from, and how it stays fresh

Every Monday a GitHub Action (`.github/workflows/update-and-deploy.yml`) runs `scripts/fetch_data.py`, saves the
new data in `web/data/`, and republishes the site. Nothing has to be done by hand.

| Source | What it adds | How |
|---|---|---|
| [NSF Award Search API](https://resources.research.gov/common/webapi/awardapisearch-v1.htm) | Active NSF **REU Site** awards (each is a paid summer program at a campus) | Public API, no key |
| NSF Award Search API | Professors with an active, recent NSF research grant, for the professor finder | Public API, no key |
| [USAJOBS API](https://developer.usajobs.gov/) | Federal jobs open to students (the "student" hiring path) | Free key; **optional** |
| `data/university_programs.json` | University-run summer research programs, including ones made for HBCU students | Checked by hand each term |
| `data/curated_programs.json` | National programs we check by hand each term, with the date checked | Edited in the repo |
| `data/hbcus.json` | The Department of Education HBCU list, used to badge HBCU hosts | Edited in the repo |
| `data/submitted.json` | Listings people send in with the issue form, after review | Edited in the repo |

- Listings whose deadline (or award end date) has passed are removed each week.
- If a source is down, the last good copy of that source is kept, so one bad week never empties the site.
- Directories such as Pathways to Science and Zintellect are **linked, not copied**.

### Rules this project follows

- Link to official pages and write our own short summaries. NSF award text is public US government data.
- No logos, and a clear "not affiliated" note.
- No bulk collection of professors' emails or other personal details; we link to official faculty pages instead.
- No scraping of sites whose terms forbid it, and no LinkedIn data.
- Community submissions are reviewed before they go live.

## Turning on the AI email writer

See `ai-proxy/README.md`: a free Gemini API key from Google AI Studio (no billing), a Vercel project with Root
Directory `ai-proxy` and `GEMINI_API_KEY` set, then put its URL in `web/config.js` as `aiEndpoint`.

## Turning on USAJOBS (optional)

1. Request a free key at https://developer.usajobs.gov/apirequest/.
2. In this repo: **Settings → Secrets and variables → Actions → New repository secret**. Add `USAJOBS_API_KEY`
   (the key) and `USAJOBS_EMAIL` (the email you signed up with).
3. Run the workflow from the **Actions** tab, or wait for Monday.

## Community submissions, and how they're verified

Anyone can send an opportunity with **➕ Submit an opportunity** (a GitHub issue form, free account needed).

1. **Automatic checks** (`scripts/submissions.py`, run by `.github/workflows/check-submission.yml`) comment on the issue
   within a minute: the official link opens, it's on a .edu or .gov site (other sites are flagged), the page names the
   program, a "paid" claim is backed by the page, the deadline is a real future date, it isn't already listed, and no
   personal emails or phone numbers were pasted in. The submitter can edit the issue and the checks run again.
2. **A person approves it.** The repository owner opens the official page, confirms it's real and current, then adds
   the `approved` label. Only the owner's label counts.
3. **Publishing is automatic:** the listing is added to `data/submitted.json` (emails and phone numbers removed, the
   date checked recorded), the issue is closed, and the site rebuilds. Deadlines that pass drop off automatically.

## Run it yourself

```sh
python tests/test_compass.py     # tests (no network needed)
python scripts/fetch_data.py     # refresh web/data/ from the live sources
sh build_site.sh && python -m http.server -d _site 8000
```

Research Compass is an independent student project. It is not affiliated with or endorsed by NSF, any agency,
university or company listed. Always confirm details on the official page before applying.

MIT licensed. Built by Frank Ncube.
