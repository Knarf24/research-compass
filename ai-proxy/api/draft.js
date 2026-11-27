// Research Compass AI email writer: a small Vercel serverless function.
//
// The public site (GitHub Pages) cannot hide an API key, so the key lives here as the
// GEMINI_API_KEY environment variable in Vercel. The site sends the student's profile,
// the program or professor, and what kind of email they want; this function asks Google
// Gemini (free tier) for a short, specific draft and returns {subject, body, notes}.
//
// Cost: the key comes from Google AI Studio's free tier, with no billing turned on, so
// there is nothing to pay. When the free quota is used up Google simply refuses requests
// until it resets, and the site falls back to the built-in template writer.
// Note: on the free tier Google may use what is sent to improve its products, so the
// site tells students not to type anything private.
//
// Guard rails (abuse and staying inside the free quota):
//   * Only the Research Compass site may call it (CORS allow-list, ALLOWED_ORIGINS).
//   * Inputs are trimmed to fixed sizes.
//   * A per-visitor limit (PER_IP_PER_HOUR) and a daily cap (DAILY_LIMIT, per instance).

const MODEL = process.env.GEMINI_MODEL || "gemini-flash-latest";
// Used when the main model is overloaded (503) or out of free quota (429).
const BACKUP_MODEL = process.env.GEMINI_BACKUP_MODEL || "gemini-flash-lite-latest";
const ALLOWED = (process.env.ALLOWED_ORIGINS || "https://research-compass.studentlabs.me,https://knarf24.github.io")
  .split(",").map((s) => s.trim()).filter(Boolean);
const PER_IP_PER_HOUR = Number(process.env.PER_IP_PER_HOUR || 15);
const DAILY_LIMIT = Number(process.env.DAILY_LIMIT || 200);

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
- Short: 90 to 150 words in the body unless told otherwise. Two or three short paragraphs, separated by a blank line; greeting and sign-off on their own lines.
- Specific: mention one concrete detail from the program or the professor's grant summary, in your own words. Never paste the summary back.
- Use only facts given about the student. Never invent courses, projects, GPAs, publications, awards or skills. If something important is missing, write around it; do not add placeholders unless the student's name is missing (then use [Your name]).
- Never claim the student read a paper unless they said so.
- One clear ask at the end, easy to say yes to (a short call, whether they take undergraduates this summer, or the student's question).
- Plain, natural English. No clichés: no "I hope this email finds you well", "I am writing to express", "I am passionate about", "cutting-edge", "esteemed", "delve", "I would be honored". No em dashes. No bullet points.
- The subject line is specific and under 10 words.
- Sign off with the student's name and school only.
- If the student is an international student and the program is for US citizens or permanent residents only, don't hide it: the email should ask whether there is a way for an international student to take part.
Return JSON only.`;

// Gemini's response schema format (an OpenAPI subset).
const SCHEMA = {
  type: "OBJECT",
  properties: {
    subject: { type: "STRING" },
    body: { type: "STRING" },
    notes: { type: "ARRAY", items: { type: "STRING" }, description: "Up to 3 short tips for the student about this email (what to check or add before sending)." },
  },
  required: ["subject", "body", "notes"],
  propertyOrdering: ["subject", "body", "notes"],
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

// ---- Résumé reading (mode "resume"): turns résumé text into profile fields.
// The browser removes emails, phone numbers and links before sending.
const RESUME_SYSTEM = `You read a US college student's résumé and fill in their Research Compass profile.
Use only what the résumé says. Never guess or invent. Leave a field as an empty string when the résumé doesn't support it.
- name: the student's full name.
- school: the college or university they attend now (not high school).
- year: one of the allowed class years, worked out from the expected graduation date and today's date, or "" if unclear.
- major: the closest allowed field for their degree, or "".
- skills: a comma-separated list of their most relevant technical and lab skills, at most 15, most important first.
- interests: one short phrase of research interests in their own words if stated; otherwise infer a modest phrase from their projects and coursework (at most 12 words).
- project: one sentence (at most 25 words) describing the project or experience they would most likely mention to a professor, starting with a verb like "Built".
Return JSON only.`;

function resumeRequest(input) {
  const list = (v) => (Array.isArray(v) ? v.slice(0, 40).map((x) => clip(String(x), 60)).filter(Boolean) : []);
  const years = list(input.years);
  const fields = list(input.fields);
  const str = { type: "STRING" };
  const schema = {
    type: "OBJECT",
    properties: {
      name: str, school: str,
      year: { type: "STRING", description: "Exactly one of the allowed class years, or an empty string." },
      major: { type: "STRING", description: "Exactly one of the allowed fields, or an empty string." },
      skills: str, interests: str, project: str,
    },
    required: ["name", "school", "year", "major", "skills", "interests", "project"],
  };
  const text = String(input.text || "").slice(0, 12000);
  const today = clip(input.today, 10) || new Date().toISOString().slice(0, 10);
  const prompt = `Today's date: ${today}\nAllowed class years: ${years.join(" | ")}\nAllowed fields: ${fields.join(" | ")}\n\nRÉSUMÉ:\n${text}`;
  return { system: RESUME_SYSTEM, prompt, schema, temperature: 0.2 };
}

function cleanResume(out, input) {
  const keys = ["name", "school", "year", "major", "skills", "interests", "project"];
  const clean = Object.fromEntries(keys.map((k) => [k, String(out[k] || "").trim().slice(0, k === "skills" ? 400 : 250)]));
  // Only keep a class year or field the page actually offers.
  if (Array.isArray(input.years) && !input.years.includes(clean.year)) clean.year = "";
  if (Array.isArray(input.fields) && !input.fields.includes(clean.major)) clean.major = "";
  return clean;
}

export default async function handler(req, res) {
  const allowed = cors(req, res);
  if (req.method === "OPTIONS") return res.status(allowed ? 204 : 403).end();
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST." });
  if (!allowed) return res.status(403).json({ error: "This endpoint only serves Research Compass." });
  if (!process.env.GEMINI_API_KEY) return res.status(503).json({ error: "The AI writer isn't set up yet." });

  const ip = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "unknown";
  const stop = limited(ip);
  if (stop) return res.status(429).json({ error: stop });

  let input = req.body;
  if (typeof input === "string") { try { input = JSON.parse(input); } catch { input = null; } }
  if (!input || typeof input !== "object") return res.status(400).json({ error: "Bad request." });

  // Thinking makes Gemini much slower; keep it low. Older models reject thinkingLevel,
  // so on a 400 that mentions thinking we retry once without it.
  const isResume = input.mode === "resume";
  const job = isResume
    ? resumeRequest(input)
    : { system: SYSTEM, prompt: buildPrompt(input), schema: SCHEMA, temperature: 0.9 };

  const callGemini = (thinking, model = MODEL, ms = 20000) => fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": process.env.GEMINI_API_KEY },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: job.system }] },
      contents: [{ role: "user", parts: [{ text: job.prompt }] }],
      generationConfig: {
        temperature: job.temperature,
        maxOutputTokens: 4000,
        responseMimeType: "application/json",
        responseSchema: job.schema,
        ...(thinking ? { thinkingConfig: { thinkingLevel: "low" } } : {}),
      },
    }),
    signal: AbortSignal.timeout(ms),
  });

  let r;
  try {
    const attempt = async (model, ms) => {
      let resp = await callGemini(true, model, ms);
      if (resp.status === 400) {
        const text = await resp.text();
        resp = /thinking/i.test(text) ? await callGemini(false, model, ms) : new Response(text, { status: 400 });
      }
      return resp;
    };
    // The main model gets 20 seconds; if it is slow, overloaded or out of quota,
    // the lighter backup model gets 30 more (the function may run 60 seconds).
    try {
      r = await attempt(MODEL, 20000);
    } catch (error) {
      if (!(error && (error.name === "TimeoutError" || error.name === "AbortError"))) throw error;
      r = new Response("", { status: 504 });
    }
    if ([429, 500, 503, 504].includes(r.status) && BACKUP_MODEL && BACKUP_MODEL !== MODEL) {
      console.error(`Gemini ${MODEL} returned ${r.status}; trying ${BACKUP_MODEL}`);
      r = await attempt(BACKUP_MODEL, 30000);
    }
  } catch (error) {
    const timedOut = error && (error.name === "TimeoutError" || error.name === "AbortError");
    console.error("Gemini request failed:", error && error.name);
    return res.status(504).json({ error: "The AI took too long. Please try again.", detail: timedOut ? "timeout" : "network" });
  }

  if (r.status === 429) {
    return res.status(429).json({ error: "The free AI writer is busy or out of today's quota. Try again later, or use the template writer." });
  }
  if (r.status === 400 || r.status === 401 || r.status === 403) {
    const why = (await r.text()).slice(0, 300);
    console.error(`Gemini rejected the request (${r.status}): ${why}`);
    const m = why.match(/"message":\s*"([^"]{0,160})/);
    return res.status(503).json({ error: "The AI writer isn't available right now.", detail: `gemini ${r.status}${m ? ": " + m[1] : ""}` });
  }
  if (!r.ok) {
    console.error(`Gemini error ${r.status}`);
    return res.status(502).json({ error: r.status === 503 ? "The free AI is overloaded right now. Please try again in a minute." : "The AI writer had a problem. Please try again.", detail: `gemini ${r.status}` });
  }

  try {
    const data = await r.json();
    const cand = (data.candidates || [])[0];
    const text = cand && cand.content && Array.isArray(cand.content.parts)
      ? cand.content.parts.filter((p) => typeof p.text === "string" && !p.thought).map((p) => p.text).join("")
      : "";
    if (!text) {
      return res.status(422).json({ error: isResume ? "The AI couldn't read this résumé." : "The AI couldn't write this one. Try rewording your 'why' line." });
    }
    const out = JSON.parse(text);
    if (isResume) return res.status(200).json(cleanResume(out, input));
    return res.status(200).json({
      subject: String(out.subject || "").slice(0, 200),
      body: String(out.body || "").slice(0, 4000),
      notes: Array.isArray(out.notes) ? out.notes.slice(0, 3).map((n) => String(n).slice(0, 300)) : [],
    });
  } catch (error) {
    console.error("Could not read Gemini's reply:", error && error.message);
    return res.status(502).json({ error: "The AI writer had a problem. Please try again." });
  }
}

export { buildPrompt, resumeRequest }; // for tests
