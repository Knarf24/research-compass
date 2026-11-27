// Research Compass AI email writer: a small Vercel serverless function.
//
// The public site (GitHub Pages) cannot hide an API key, so the key lives here as the
// ANTHROPIC_API_KEY environment variable in Vercel. The site sends the student's profile,
// the program or professor, and what kind of email they want; this function asks Claude
// for a short, specific draft and returns {subject, body, notes}.
//
// Guard rails (cost and abuse):
//   * Only the Research Compass site may call it (CORS allow-list, ALLOWED_ORIGINS).
//   * Inputs are trimmed to fixed sizes, so one request can't run up a big bill.
//   * A per-visitor limit (PER_IP_PER_HOUR) and a per-instance daily cap (DAILY_LIMIT).
//     These are best effort; the hard cap is the monthly spend limit you set in the
//     Anthropic Console, which no request can exceed.
import Anthropic from "@anthropic-ai/sdk";

const MODEL = "claude-opus-5-5";
const ALLOWED = (process.env.ALLOWED_ORIGINS || "https://knarf24.github.io")
  .split(",").map((s) => s.trim()).filter(Boolean);
const PER_IP_PER_HOUR = Number(process.env.PER_IP_PER_HOUR || 15);
const DAILY_LIMIT = Number(process.env.DAILY_LIMIT || 400);

const client = new Anthropic(); // reads ANTHROPIC_API_KEY
const hits = new Map(); // ip -> [timestamps]
let day = "", today = 0;

const KINDS = {
  intro: "a first email",
  question: "a short email asking the program one specific question the student could not find on the website",
  follow_up: "a polite follow-up to a first email that got no reply after about a week",
  thank_you: "a thank-you after a conversation or meeting",
};
const TWEAKS = {
  shorter: "Make it noticeably shorter: 70 to 100 words.",
  warmer: "Make the tone a little warmer and more personal, still professional.",
  formal: "Make the tone more formal.",
  confident: "Make it more confident and direct, without overclaiming.",
};
// Different ways in, so two students never get the same shape of email.
const OPENINGS = [
  "Open with the specific part of their work that caught the student's attention.",
  "Open with one sentence about who the student is, then connect it straight to their work.",
  "Open with the student's concrete reason for writing (the thing they want to learn or build).",
  "Open by naming the student's most relevant experience and why it led them here.",
];

const SYSTEM = `You write cold emails for US undergraduates contacting research programs and professors.

Write like a thoughtful student, not like a template:
- Short: 90 to 150 words in the body unless told otherwise. Three short paragraphs at most.
- Specific: mention one concrete detail from the program or the professor's grant summary, in your own words. Never paste the summary back.
- Use only facts given about the student. Never invent courses, projects, GPAs, publications, awards or skills. If something important is missing, write around it; do not add placeholders unless the student's name is missing (then use [Your name]).
- Never claim the student read a paper unless they said so.
- One clear ask at the end, easy to say yes to (a short call, whether they take undergraduates this summer, or the student's question).
- Plain, natural English. No clichés: no "I hope this email finds you well", "I am writing to express", "I am passionate about", "cutting-edge", "esteemed", "delve", "I would be honored". No em dashes. No bullet points.
- The subject line is specific and under 10 words.
- Sign off with the student's name and school only.
- If the student is an international student and the program is for US citizens or permanent residents only, don't hide it: the email should ask whether there is a way for an international student to take part.
Return JSON only.`;

const SCHEMA = {
  type: "object",
  properties: {
    subject: { type: "string" },
    body: { type: "string" },
    notes: { type: "array", items: { type: "string" }, description: "Up to 3 short tips for the student about this email (what to check or add before sending)." },
  },
  required: ["subject", "body", "notes"],
  additionalProperties: false,
};

const clip = (v, n) => (typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, n) : "");

function cors(req, res) {
  const origin = req.headers.origin || "";
  if (ALLOWED.includes(origin)) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
    res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    res.setHeader("Access-Control-Max-Age", "86400");
  }
  return ALLOWED.includes(origin);
}

function limited(ip) {
  const now = Date.now();
  const d = new Date().toISOString().slice(0, 10);
  if (d !== day) { day = d; today = 0; hits.clear(); }
  if (today >= DAILY_LIMIT) return "The email writer has reached today's limit. Please try again tomorrow.";
  const recent = (hits.get(ip) || []).filter((t) => now - t < 3600_000);
  if (recent.length >= PER_IP_PER_HOUR) return "You've drafted a lot of emails this hour. Please try again a bit later.";
  recent.push(now);
  hits.set(ip, recent);
  today += 1;
  return null;
}

function buildPrompt(input) {
  const p = input.profile || {};
  const t = input.target || {};
  const kind = KINDS[input.kind] ? input.kind : "intro";
  const student = [
    ["Name", clip(p.name, 80)], ["School", clip(p.school, 120)], ["Year", clip(p.year, 40)],
    ["Major", clip(p.major, 80)], ["Interests", clip(p.interests, 300)], ["Skills", clip(p.skills, 300)],
    ["A project or class they're proud of", clip(p.project, 300)],
    ["International student", p.citizen === "no" ? "yes" : p.citizen === "yes" ? "no" : "not said"],
    ["Goes to an HBCU", p.hbcu ? "yes" : ""],
  ].filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join("\n");

  let target;
  if (t.type === "professor") {
    const g = (t.grants || [])[0] || {};
    target = [
      `Writing to: Professor ${clip(t.name, 100)}, ${clip(t.school, 150)}`,
      `Their active NSF-funded project: "${clip(g.title, 250)}"`,
      `Project summary (public NSF text): ${clip(g.summary, 700)}`,
    ].join("\n");
  } else {
    target = [
      `Writing to: the ${clip(t.title, 200)} program, ${clip(t.org, 200)}`,
      t.contact ? `Program director: ${clip(t.contact, 100)}` : "",
      `About the program: ${clip(t.summary, 700)}`,
      t.citizenship ? `Who can apply: ${clip(t.citizenship, 120)}` : "",
      t.deadline ? `Deadline: ${clip(t.deadline, 20)}` : "",
    ].filter(Boolean).join("\n");
  }

  const why = clip(input.why, 400);
  const when = clip(input.when, 60) || "this summer";
  const tweak = TWEAKS[input.tweak] || "";
  const custom = clip(input.instruction, 300);
  const opening = OPENINGS[Math.floor(Math.random() * OPENINGS.length)];
  const previous = clip(input.previous, 1500);

  const what = kind === "intro"
    ? (t.type === "professor"
      ? "a first email asking whether the professor takes undergraduate researchers, connected to their funded project"
      : "a first email to the program showing genuine, specific interest and asking one useful question that isn't answered on its website")
    : KINDS[kind];
  return `Write ${what}.

THE STUDENT
${student || "(no profile given)"}
Why they're interested, in their own words: ${why || "(not given; infer a modest, honest reason from their major and interests)"}
When they could start: ${when}

THE RECIPIENT
${target}

STYLE FOR THIS DRAFT
${opening}
${tweak}
${custom ? `The student also asked: ${custom}` : ""}
${previous ? `\nRevise this earlier draft rather than starting over:\n${previous}` : ""}`.trim();
}

export default async function handler(req, res) {
  const allowed = cors(req, res);
  if (req.method === "OPTIONS") return res.status(allowed ? 204 : 403).end();
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST." });
  if (!allowed) return res.status(403).json({ error: "This endpoint only serves Research Compass." });
  if (!process.env.ANTHROPIC_API_KEY) return res.status(503).json({ error: "The AI writer isn't set up yet." });

  const ip = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "unknown";
  const stop = limited(ip);
  if (stop) return res.status(429).json({ error: stop });

  let input = req.body;
  if (typeof input === "string") { try { input = JSON.parse(input); } catch { input = null; } }
  if (!input || typeof input !== "object") return res.status(400).json({ error: "Bad request." });

  try {
    const response = await client.beta.messages.create({
      model: MODEL,
      max_tokens: 4000,
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      output_config: { effort: "low", format: { type: "json_schema", schema: SCHEMA } },
      system: SYSTEM,
      messages: [{ role: "user", content: buildPrompt(input) }],
    });
    if (response.stop_reason === "refusal") {
      return res.status(422).json({ error: "The AI couldn't write this one. Try rewording your 'why' line." });
    }
    const text = response.content.filter((b) => b.type === "text").map((b) => b.text).join("");
    const out = JSON.parse(text);
    return res.status(200).json({
      subject: String(out.subject || "").slice(0, 200),
      body: String(out.body || "").slice(0, 4000),
      notes: Array.isArray(out.notes) ? out.notes.slice(0, 3).map((n) => String(n).slice(0, 300)) : [],
    });
  } catch (error) {
    if (error instanceof Anthropic.RateLimitError) {
      return res.status(429).json({ error: "The AI is busy right now. Please try again in a minute." });
    }
    if (error instanceof Anthropic.AuthenticationError) {
      console.error("Anthropic key rejected");
      return res.status(503).json({ error: "The AI writer isn't available right now." });
    }
    if (error instanceof Anthropic.APIError) {
      console.error(`Anthropic API error ${error.status}: ${error.message}`);
      return res.status(502).json({ error: "The AI writer had a problem. Please try again." });
    }
    console.error(error);
    return res.status(500).json({ error: "Something went wrong drafting the email." });
  }
}

export { buildPrompt }; // for tests
