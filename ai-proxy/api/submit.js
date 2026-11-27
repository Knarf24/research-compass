// Research Compass "paste a link" submissions.
//
// A visitor pastes the link to a research opportunity on the site. This function:
//   1. checks the link (a public https web page, not a private or local address),
//   2. reads the page and asks Gemini (free tier) to pull out the program details,
//   3. opens a "[Listing]" issue in the GitHub repository with those details.
// From there the existing GitHub workflow runs its automatic checks, and nothing is
// published until the owner adds the "approved" label. Visitors need no GitHub account.
//
// Settings (Vercel environment variables):
//   GEMINI_API_KEY        the same free key the email writer uses
//   GITHUB_ISSUES_TOKEN   fine-grained token for Knarf24/research-compass, Issues: read and write only
//   NTFY_TOPIC            optional: phone notification for each new submission (ntfy.sh topic)
//   SUBMIT_REPO           optional, default Knarf24/research-compass
//
// Abuse protection: origin allow-list, a hidden "website" field bots fill in (honeypot),
// 3 submissions per visitor per hour and 30 per day per instance, URL checks, page size
// and time limits, and the AI must agree the page is a research or internship opportunity.
import { lookup } from "node:dns/promises";
import net from "node:net";

const MODEL = process.env.GEMINI_MODEL || "gemini-flash-latest";
const BACKUP_MODEL = process.env.GEMINI_BACKUP_MODEL || "gemini-flash-lite-latest";
const REPO = process.env.SUBMIT_REPO || "Knarf24/research-compass";
const ALLOWED = (process.env.ALLOWED_ORIGINS || "https://research-compass.studentlabs.me,https://knarf24.github.io")
  .split(",").map((s) => s.trim()).filter(Boolean);
const PER_IP_PER_HOUR = Number(process.env.SUBMIT_PER_IP_PER_HOUR || 3);
const DAILY_LIMIT = Number(process.env.SUBMIT_DAILY_LIMIT || 30);
const UA = "ResearchCompass/1.0 (+https://github.com/Knarf24/research-compass)";

const hits = new Map();
let day = "", today = 0;

const clip = (v, n) => (typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, n) : "");

function cors(req, res) {
  const origin = req.headers.origin || "";
  const ok = ALLOWED.includes(origin);
  if (ok) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
    res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    res.setHeader("Access-Control-Max-Age", "86400");
  }
  return ok;
}

function limited(ip) {
  const now = Date.now();
  const d = new Date().toISOString().slice(0, 10);
  if (d !== day) { day = d; today = 0; hits.clear(); }
  if (today >= DAILY_LIMIT) return "We've had a lot of submissions today. Please try again tomorrow.";
  const recent = (hits.get(ip) || []).filter((t) => now - t < 3600_000);
  if (recent.length >= PER_IP_PER_HOUR) return "Thanks for all the links! Please wait an hour before sending more.";
  recent.push(now);
  hits.set(ip, recent);
  today += 1;
  return null;
}

// ---- link checks: a public web page only (no local or private network addresses)
function privateIp(ip) {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split(".").map(Number);
    return a === 10 || a === 127 || a === 0 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || a >= 224;
  }
  const x = ip.toLowerCase();
  return x === "::1" || x === "::" || x.startsWith("fc") || x.startsWith("fd") || x.startsWith("fe80") ||
    (x.startsWith("::ffff:") && privateIp(x.slice(7)));
}

async function checkUrl(raw) {
  let url;
  try { url = new URL(String(raw || "").trim()); } catch { return { error: "That doesn't look like a web link. Paste the full address, starting with https://" }; }
  if (!["https:", "http:"].includes(url.protocol)) return { error: "Please paste a normal web link (https://…)." };
  if (url.username || url.password || (url.port && !["80", "443"].includes(url.port))) return { error: "Please paste a normal web link (https://…)." };
  const host = url.hostname.toLowerCase();
  if (!host.includes(".") || net.isIP(host) || host.endsWith(".local") || host.endsWith(".internal") || host === "localhost") {
    return { error: "Please paste the link to a public web page." };
  }
  if (/(^|\.)(linkedin\.com|facebook\.com|instagram\.com|tiktok\.com|x\.com|twitter\.com)$/.test(host)) {
    return { error: "Please link to the program's official page (for example the university's website), not a social media post." };
  }
  try {
    const addrs = await lookup(host, { all: true });
    if (!addrs.length || addrs.some((a) => privateIp(a.address))) return { error: "Please paste the link to a public web page." };
  } catch { return { error: "We couldn't find that website. Check the link and try again." }; }
  return { url };
}

async function readPage(url) {
  let current = url;
  for (let hop = 0; hop < 4; hop++) {
    const r = await fetch(current, { redirect: "manual", headers: { "User-Agent": UA, Accept: "text/html,text/plain;q=0.9" }, signal: AbortSignal.timeout(10000) });
    if (r.status >= 300 && r.status < 400 && r.headers.get("location")) {
      const next = new URL(r.headers.get("location"), current);
      const ok = await checkUrl(next.href);
      if (ok.error) throw new Error("redirect");
      current = ok.url;
      continue;
    }
    if (!r.ok) return { status: r.status, url: current.href, text: "" };
    const type = r.headers.get("content-type") || "";
    if (!/text\/html|text\/plain|xhtml/.test(type)) return { status: 415, url: current.href, text: "" };
    const reader = r.body.getReader();
    const chunks = [];
    let size = 0;
    while (size < 1_500_000) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      size += value.length;
    }
    reader.cancel().catch(() => {});
    const html = new TextDecoder().decode(Buffer.concat(chunks.map((c) => Buffer.from(c))));
    const title = (html.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [, ""])[1];
    const text = html
      .replace(/<(script|style|noscript|svg|nav|footer)[\s\S]*?<\/\1>/gi, " ")
      .replace(/<br\s*\/?>|<\/(p|div|li|h\d|tr)>/gi, "\n")
      .replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&#39;|&rsquo;/g, "'").replace(/&quot;/g, '"')
      .replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n").trim();
    return { status: 200, url: current.href, text: `${clip(title, 200)}\n${text}`.slice(0, 15000) };
  }
  throw new Error("redirects");
}

// ---- Gemini: pull out the details
const SYSTEM = `You read a web page that someone says is a research opportunity for US undergraduate students, and fill in a listing.
Use only what the page says. Never invent dates, pay, eligibility or names. Use "" when the page doesn't say.
- is_opportunity: true only if the page describes a specific research program, research internship, fellowship or lab position that undergraduates can apply to. false for general news, a lab's home page with no opening, ads, or anything unrelated.
- reason: one short sentence explaining is_opportunity.
- title: the program's name. org: the university, lab, agency or company running it.
- location: "City, ST" (two-letter state) or "Remote", or "".
- deadline: the next application deadline as YYYY-MM-DD if a full date is given, else "".
- paid: "Yes" if a stipend, salary or wage is mentioned, "No" if it says unpaid, else "Not sure".
- international: "Yes" if international students can apply, "No" if limited to US citizens or permanent residents, else "Not sure".
- hbcu: "Yes" only if it is specifically for HBCU students, else "No".
- eligibility: who can apply, in under 20 words, or "".
- summary: 2 or 3 sentences in your own words describing the opportunity. No personal emails or phone numbers.
Return JSON only.`;
const S = { type: "STRING" };
const SCHEMA = {
  type: "OBJECT",
  properties: { is_opportunity: { type: "BOOLEAN" }, reason: S, title: S, org: S, location: S, deadline: S, paid: S, international: S, hbcu: S, eligibility: S, summary: S },
  required: ["is_opportunity", "reason", "title", "org", "location", "deadline", "paid", "international", "hbcu", "eligibility", "summary"],
};

async function extract(page) {
  const body = (thinking) => JSON.stringify({
    systemInstruction: { parts: [{ text: SYSTEM }] },
    contents: [{ role: "user", parts: [{ text: `Today's date: ${new Date().toISOString().slice(0, 10)}\nPage address: ${page.url}\n\nPAGE TEXT:\n${page.text}` }] }],
    generationConfig: { temperature: 0.1, maxOutputTokens: 3000, responseMimeType: "application/json", responseSchema: SCHEMA,
      ...(thinking ? { thinkingConfig: { thinkingLevel: "low" } } : {}) },
  });
  const call = async (model, ms) => {
    const go = (thinking) => fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
      method: "POST", headers: { "Content-Type": "application/json", "x-goog-api-key": process.env.GEMINI_API_KEY },
      body: body(thinking), signal: AbortSignal.timeout(ms),
    });
    let r = await go(true);
    if (r.status === 400 && /thinking/i.test(await r.clone().text())) r = await go(false);
    return r;
  };
  let r;
  try { r = await call(MODEL, 20000); } catch { r = { ok: false, status: 504 }; }
  if (!r.ok && [429, 500, 503, 504].includes(r.status)) r = await call(BACKUP_MODEL, 25000);
  if (!r.ok) throw new Error(`gemini ${r.status}`);
  const data = await r.json();
  const parts = (((data.candidates || [])[0] || {}).content || {}).parts || [];
  return JSON.parse(parts.filter((p) => typeof p.text === "string" && !p.thought).map((p) => p.text).join(""));
}

// ---- GitHub issue in the same shape as the issue form, so the checks and approval work unchanged
const EMAIL = /[\w.+-]+@[\w-]+(\.[\w-]+)+/g;
const PHONE = /\+?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}/g;
const tidy = (v, n) => clip(String(v || ""), n).replace(EMAIL, "").replace(PHONE, "").replace(/[#<>`]/g, "").trim();
const pick = (v, options, fallback) => (options.includes(v) ? v : fallback);

function issueBody(info, url, note) {
  const field = (label, value) => `### ${label}\n\n${value || "_No response_"}\n`;
  return [
    "_Sent through the Research Compass website. The details below were read from the page by AI: check them against the official page before adding the `approved` label._\n",
    field("Program or position name", tidy(info.title, 200)),
    field("University, lab or organization", tidy(info.org, 200)),
    field("Official page (link)", url),
    field("City and state", tidy(info.location, 80)),
    field("Application deadline", /^\d{4}-\d{2}-\d{2}$/.test(info.deadline || "") ? info.deadline : ""),
    field("Is it paid?", pick(info.paid, ["Yes", "No", "Not sure"], "Not sure")),
    field("Can international students apply?", pick(info.international, ["Yes", "No", "Not sure"], "Not sure")),
    field("Is it made for HBCU students?", pick(info.hbcu, ["Yes", "No"], "No")),
    field("Short description in your own words (2-3 sentences)", tidy(info.summary, 600)),
    field("Who can apply?", tidy(info.eligibility, 200)),
    field("How do you know about it?", "Pasted on the website (details read by AI)"),
    note ? `### Note from the person who sent it\n\n${tidy(note, 300)}\n` : "",
  ].join("\n");
}

async function openIssue(title, body) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/issues`, {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.GITHUB_ISSUES_TOKEN}`, Accept: "application/vnd.github+json", "User-Agent": UA, "Content-Type": "application/json" },
    body: JSON.stringify({ title, body, labels: ["listing"] }),
    signal: AbortSignal.timeout(15000),
  });
  if (!r.ok) throw new Error(`github ${r.status}`);
  return (await r.json()).html_url;
}

async function notify(title, link) {
  // Returns what happened, so the site's check can tell whether the phone alert went out.
  const topic = (process.env.NTFY_TOPIC || "").trim();
  if (!topic) return "off";
  try {
    const r = await fetch(`https://ntfy.sh/${encodeURIComponent(topic)}`, {
      method: "POST", body: `New Research Compass submission waiting for approval: ${title}`,
      headers: { Title: "Research Compass submission", Click: link, Tags: "compass", "User-Agent": UA }, signal: AbortSignal.timeout(8000),
    });
    if (!r.ok) console.error(`ntfy answered ${r.status}`);
    return r.ok ? "sent" : `failed ${r.status}`;
  } catch (e) {
    console.error("ntfy failed:", e && e.name);
    return "failed";
  }
}

export default async function handler(req, res) {
  const allowed = cors(req, res);
  if (req.method === "OPTIONS") return res.status(allowed ? 204 : 403).end();
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST." });
  if (!allowed) return res.status(403).json({ error: "This endpoint only serves Research Compass." });
  if (!process.env.GEMINI_API_KEY || !process.env.GITHUB_ISSUES_TOKEN) {
    return res.status(503).json({ error: "Submitting by link isn't switched on yet." });
  }

  let input = req.body;
  if (typeof input === "string") { try { input = JSON.parse(input); } catch { input = null; } }
  if (!input || typeof input !== "object") return res.status(400).json({ error: "Bad request." });
  // Honeypot: people never see the "website" field; bots fill it in. Pretend it worked.
  if (clip(input.website, 200)) return res.status(200).json({ ok: true, title: "" });

  const checked = await checkUrl(input.url);
  if (checked.error) return res.status(400).json({ error: checked.error });

  const ip = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "unknown";
  const stop = limited(ip);
  if (stop) return res.status(429).json({ error: stop });

  let page;
  try { page = await readPage(checked.url); } catch { return res.status(400).json({ error: "We couldn't open that page. Check the link and try again." }); }
  if (page.status === 404 || page.status === 410) return res.status(400).json({ error: "That page doesn't exist (error 404). Check the link." });
  // Some sites block automated readers (403) or only show their content with JavaScript. The link is
  // still worth a look, so it goes to the review queue with the details left for the owner to fill in.
  if (page.status !== 200 || page.text.length < 150) {
    const host = checked.url.hostname;
    const blank = { title: `Link from ${host}`, org: "", location: "", deadline: "", paid: "Not sure", international: "Not sure", hbcu: "No",
      eligibility: "", summary: `The page couldn't be read automatically (${page.status !== 200 ? `the site answered ${page.status}` : "no readable text"}). Open it and fill in the details before approving.` };
    try {
      const link = await openIssue(`[Listing] Link from ${host} (needs details)`, issueBody(blank, page.url, input.note));
      const notified = await notify(`a link from ${host} (needs details)`, link);
      return res.status(200).json({ ok: true, title: "", notified, unread: true });
    } catch (e) {
      console.error("issue failed:", e && e.message);
      return res.status(502).json({ error: "We couldn't save your submission. Please try again later." });
    }
  }

  let info;
  try { info = await extract(page); } catch (e) {
    console.error("extract failed:", e && e.message);
    return res.status(502).json({ error: "We couldn't read that page right now. Please try again in a minute." });
  }
  if (!info || !info.is_opportunity) {
    return res.status(422).json({ error: `That page doesn't look like a research opportunity students can apply to${info && info.reason ? ` (${clip(info.reason, 160)})` : ""}. Try the program's official information page.` });
  }

  const title = tidy(info.title, 120) || "Research opportunity";
  try {
    const link = await openIssue(`[Listing] ${title}`, issueBody(info, page.url, input.note));
    const notified = await notify(title, link);
    return res.status(200).json({ ok: true, title, org: tidy(info.org, 120), notified });
  } catch (e) {
    console.error("issue failed:", e && e.message);
    return res.status(502).json({ error: "We read the page but couldn't save your submission. Please try again later." });
  }
}

export { checkUrl, issueBody, privateIp }; // for tests
