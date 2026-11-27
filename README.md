# 🧭 Research Compass

**Live site:** https://knarf24.github.io/research-compass/

**The problem:** paid undergraduate research in the US is spread over hundreds of campus pages, agency sites and
directories. Students don't know where to look, which programs they actually qualify for, or how to approach a
professor, and deadlines slip by.

**The app:** set up a short profile (major, year, interests, skills) and Research Compass ranks open research
opportunities for you, explains why each one fits, finds professors with funded research in your area, drafts the
cold email, and tracks every deadline. Free, no sign-up, and your data never leaves your browser.

- **🧭 Programs:** NSF REU summer sites at US campuses, national lab and agency programs (DOE, NASA, NIST, NOAA and
  more), federal student jobs, and reviewed community submissions. Each one gets a match score, the reasons it fits,
  and warnings (for example "US citizens only" or "deadline in 5 days"). Filter by field, state and type, or show only
  the ones you qualify for.
- **🔬 Professor finder:** researchers at US universities with an **active NSF grant**, matched to what you want to
  work on using the words in their grant summaries. Each links to the public NSF award page and helps you find
  their official faculty page. No emails are collected.
- **✉️ Email writer:** a first email, a question, a follow-up or a thank-you, written from your profile and the
  program or professor's real project title, with tips (length, placeholders, what to attach).
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
| `data/curated_programs.json` | National programs we check by hand each term, with the date checked | Edited in the repo |
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

## Turning on USAJOBS (optional)

1. Request a free key at https://developer.usajobs.gov/apirequest/.
2. In this repo: **Settings → Secrets and variables → Actions → New repository secret**. Add `USAJOBS_API_KEY`
   (the key) and `USAJOBS_EMAIL` (the email you signed up with).
3. Run the workflow from the **Actions** tab, or wait for Monday.

## Approving a submitted listing

People submit through **Issues → New issue → Submit a research opportunity**. To publish one, check the official
page, then add an entry to `data/submitted.json` with the same fields as `data/curated_programs.json`
(`kind: "submitted"`), and close the issue. It goes live on the next run.

## Run it yourself

```sh
python tests/test_compass.py     # tests (no network needed)
python scripts/fetch_data.py     # refresh web/data/ from the live sources
sh build_site.sh && python -m http.server -d _site 8000
```

Research Compass is an independent student project. It is not affiliated with or endorsed by NSF, any agency,
university or company listed. Always confirm details on the official page before applying.

MIT licensed. Built by Frank Ncube.
