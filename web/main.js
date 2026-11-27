// The page: drawing, taps and saving to this device. Ranking, the professor finder, the email
// writer, tracker reminders and the calendar file are Python (compass.py) running in Pyodide.
"use strict";

let py = null;
let INFO = null;
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ storage (this device only)
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem("rc." + key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem("rc." + key, JSON.stringify(value)); } catch { /* private mode */ } },
};
let profile = store.get("profile", {});
let saved = store.get("saved", []);          // tracker items
let emailTarget = null;
let pLimit = 30;

const saveProfile = () => { store.set("profile", profile); showMe(); };
const saveTracker = () => { store.set("saved", saved); renderTracker(); };

// ------------------------------------------------------------------ Python bridge
function call(name, args = {}) {
  const out = JSON.parse(py.globals.get("call")(name, JSON.stringify(args)));
  if ("error" in out) throw new Error(out.error);
  return out.ok;
}

async function start() {
  try {
    py = await loadPyodide({ indexURL: new URL("pyodide/", location.href).href });
    $("loadText").textContent = "Loading this week's opportunities…";
    const get = (u) => fetch(u, { cache: "no-cache" }).then((r) => { if (!r.ok) throw new Error(u + " missing"); return r.text(); });
    const [code, listings, professors] = await Promise.all([get("compass.py"), get("data/listings.json"), get("data/professors.json")]);
    py.FS.writeFile("/home/pyodide/compass.py", code);
    py.runPython("from compass import call");
    INFO = call("load", { listings, professors });
  } catch (err) {
    $("loading").classList.add("failed");
    $("loadText").textContent = "Sorry, Python could not start in this browser: " + err.message;
    return;
  }
  $("loading").hidden = true;
  $("app").hidden = false;
  setup();
}

// ------------------------------------------------------------------ helpers
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : String(kid));
  return e;
}
const ext = (href, text, cls = "") => el("a", { href, target: "_blank", rel: "noopener", class: cls }, text);

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 2600);
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
function debounce(fn, ms = 200) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function fillSelect(sel, values, keepFirst = true) {
  const first = keepFirst ? sel.options[0] : null;
  sel.innerHTML = "";
  if (first) sel.append(first);
  for (const v of values) sel.append(el("option", { value: v }, v));
}
const KIND_LABEL = { university: "University-run program", reu: "NSF REU summer site", program: "National program", federal: "Federal student job", submitted: "Community submitted" };

// ------------------------------------------------------------------ setup
function setup() {
  fillSelect($("pField"), INFO.fields);
  fillSelect($("rField"), INFO.fields);
  fillSelect($("pState"), INFO.states);
  fillSelect($("rState"), INFO.states);
  $("updated").textContent = INFO.updated ? `Updated ${fmtDate(INFO.updated)}.` : "";

  const sources = INFO.sources || {};
  const reu = sources["NSF REU Sites"]?.count || 0;
  $("stats").replaceChildren(
    stat(INFO.listings, "open opportunities"),
    stat(INFO.professors, "professors with active NSF grants"),
    stat(INFO.hbcu_listings, "HBCU programs and HBCU hosts"),
    stat(INFO.intl_listings, "open to international students"),
  );

  $("dirs").replaceChildren(...INFO.directories.map((d) => el("div", { class: "dir" }, ext(d.url, d.name), el("span", { class: "small" }, d.what))));

  // tabs
  for (const b of $("tabs").querySelectorAll("button")) b.addEventListener("click", () => showTab(b.dataset.tab));

  // programs
  for (const id of ["pField", "pState", "pKind", "pSort", "pEligible", "pHbcu", "pIntl"]) $(id).addEventListener("change", () => { pLimit = 30; renderPrograms(); });
  $("pq").addEventListener("input", debounce(() => { pLimit = 30; renderPrograms(); }));
  $("pMore").addEventListener("click", () => { pLimit += 30; renderPrograms(); });

  // professors
  for (const id of ["rField", "rState", "rHbcu"]) $(id).addEventListener("change", renderProfs);
  for (const id of ["rq", "rSchool"]) $(id).addEventListener("input", debounce(renderProfs, 250));

  // email
  for (const id of ["eWhy", "eWhen"]) $(id).addEventListener("input", debounce(renderEmail, 150));
  $("eKind").addEventListener("input", () => { aiDraft = false; $("tweakRow").hidden = true; renderEmail(); });
  setupAi();
  setupSubmit();
  $("eCopy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(`Subject: ${$("eSubject").value}\n\n${$("eBody").value}`); toast("Copied. Paste it into your email."); }
    catch { $("eBody").select(); toast("Select all and copy."); }
  });
  for (const id of ["eSubject", "eBody"]) $(id).addEventListener("input", updateMailto);
  $("eProfileLink").addEventListener("click", (e) => { e.preventDefault(); openProfile(); });

  // tracker
  $("tAdd").addEventListener("click", () => openItem(null));
  $("tIcs").addEventListener("click", downloadIcs);
  $("tExport").addEventListener("click", () => download("research-compass-backup.json", JSON.stringify({ profile, saved }, null, 2), "application/json"));
  $("tImport").addEventListener("change", importBackup);

  // profile
  $("meBtn").addEventListener("click", openProfile);
  setupProfileForm();
  setupItemForm();

  showMe();
  renderPrograms();
  renderProfs();
  renderEmail();
  renderTracker();
  const sharedId = location.hash.startsWith("#p=") ? decodeURIComponent(location.hash.slice(3)) : "";
  showTab(sharedId ? "programs" : location.hash.slice(1) || "programs");
  if (sharedId) showShared(sharedId);
  if (!profile.major) setTimeout(openProfile, 400);
}

function stat(n, label) { return el("div", { class: "stat" }, el("b", {}, Number(n || 0).toLocaleString()), el("span", {}, label)); }

function showTab(name) {
  if (!$("t-" + name)) name = "programs";
  for (const b of $("tabs").querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const s of document.querySelectorAll(".panel")) s.hidden = s.id !== "t-" + name;
  history.replaceState(null, "", "#" + name);
}

function showMe() {
  $("meLabel").textContent = profile.major ? `${profile.name || "You"} · ${profile.major} · ✏️ Edit` : "Set up your profile";
}

// ------------------------------------------------------------------ sharing
// Each program has its own small page (p/<slug>/, made by scripts/share_pages.py) with a link preview.
// The shared link opens that page on Research Compass first, then the official page.
function shareSlug(id) {
  return String(id).toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, 80) || "program";
}
function shareUrl(r) {
  return new URL(`p/${shareSlug(r.id)}/`, location.href.split("#")[0]).href;
}
function toggleShare(btn, r) {
  const open = btn.parentElement.nextElementSibling;
  if (open && open.classList.contains("sharemenu")) { open.remove(); return; }
  const url = shareUrl(r);
  const text = `${r.title} (${r.org}): paid research opportunity on Research Compass`;
  const menu = el("div", { class: "row sharemenu" },
    ext(`https://wa.me/?text=${encodeURIComponent(text + "\n" + url)}`, "💬 WhatsApp", "ghost btn"),
    el("button", { class: "ghost", onclick: async (e) => {
      try { await navigator.clipboard.writeText(url); toast("Link copied. Paste it anywhere."); }
      catch {
        const box = el("input", { value: url, readonly: true, "aria-label": "Link to share" });
        e.currentTarget.replaceWith(box);
        box.select();
        toast("Select the link and copy it.");
      }
    } }, "🔗 Copy link"),
    el("a", { class: "ghost btn", href: `mailto:?subject=${encodeURIComponent(r.title)}&body=${encodeURIComponent(text + "\n\n" + url)}` }, "✉️ Email"),
    navigator.share ? el("button", { class: "ghost", onclick: () => navigator.share({ title: r.title, text, url }).catch(() => {}) }, "📱 More apps") : null,
  );
  btn.parentElement.after(menu);
}
function showShared(id) {
  const r = call("program", { id, profile });
  const box = $("pShared");
  if (!r.id) { toast("That shared opportunity isn't listed anymore."); return; }
  box.replaceChildren(
    el("div", { class: "block-head" }, el("h2", {}, "Shared with you"),
      el("button", { class: "ghost", type: "button", onclick: () => { box.hidden = true; } }, "✕ Close")),
    programCard(r));
  box.hidden = false;
  box.scrollIntoView({ block: "start" });
}

// ------------------------------------------------------------------ programs
function renderPrograms() {
  const filters = {
    q: $("pq").value, field: $("pField").value, state: $("pState").value, kind: $("pKind").value,
    sort: $("pSort").value, eligible: $("pEligible").checked, paid: !!profile.paid_only, limit: pLimit,
    hbcu: $("pHbcu").checked, intl: $("pIntl").checked,
  };
  const res = call("programs", { profile, filters });
  $("pCount").textContent = res.total ? `${res.total.toLocaleString()} match${res.total === 1 ? "" : "es"}${profile.major ? ", ranked for you" : ". Set up your profile to rank them for you"}.` : "";
  $("pList").replaceChildren(...(res.items.length ? res.items.map(programCard) : [empty("No programs match. Try fewer filters.")]));
  $("pMore").hidden = res.total <= res.items.length;
}

function empty(text) { return el("div", { class: "empty" }, text); }

function scoreRing(score) {
  const cls = score >= 70 ? "hi" : score >= 45 ? "mid" : "lo";
  return el("div", { class: "score " + cls, title: "How well it fits your profile (not your chance of being accepted). See \"How the match score works\"." }, el("b", {}, score), el("span", {}, "match"));
}

function programCard(r) {
  const isSaved = saved.some((s) => s.id === r.id);
  const where = [r.org, [r.city, r.state].filter(Boolean).join(", ")].filter(Boolean).join(" · ");
  const deadline = r.deadline
    ? el("span", { class: "pill " + (r.days_left != null && r.days_left <= 14 ? "warn" : "") }, `⏰ Deadline ${fmtDate(r.deadline)}${r.days_left != null ? ` (${r.days_left} days)` : ""}`)
    : el("span", { class: "pill" }, "⏰ Deadline: see official page");
  return el("article", { class: "card item" },
    profile.major ? scoreRing(r.score) : null,
    el("div", { class: "body" },
      el("div", { class: "kind" }, KIND_LABEL[r.kind] || r.source),
      el("h3", {}, r.title),
      el("div", { class: "where" }, where),
      el("p", { class: "summary" }, r.summary),
      el("div", { class: "pills" },
        deadline,
        r.paid ? el("span", { class: "pill good" }, "💵 " + (r.pay || "Paid")) : null,
        intlPill(r),
        r.hbcu_focus ? el("span", { class: "pill hbcu" }, "🎓 For HBCU students") : r.hbcu_host ? el("span", { class: "pill hbcu" }, "🎓 Hosted at an HBCU") : null,
        ...(r.fields || []).slice(0, 3).map((f) => el("span", { class: "pill soft" }, f)),
      ),
      r.reasons?.length ? el("div", { class: "why" }, "✓ " + r.reasons.join(" · ")) : null,
      r.warnings?.length ? el("div", { class: "warnline" }, "⚠ " + r.warnings.join(" · ")) : null,
      el("div", { class: "row" },
        ext(r.url, r.kind === "reu" && !r.has_site_link ? "NSF award page ↗" : "Official page ↗", "primary btn"),
        r.kind === "reu" && !r.has_site_link
          ? ext(`https://www.google.com/search?q=${encodeURIComponent(`${r.org} REU ${r.title}`)}`, "Find the program site ↗", "ghost btn") : null,
        el("button", { class: "ghost", onclick: () => toggleSave(r) }, isSaved ? "★ Saved" : "☆ Save to tracker"),
        el("button", { class: "ghost", onclick: () => draftFor({ type: "program", ...r }) }, "✉️ Draft email"),
        el("button", { class: "ghost", "aria-haspopup": "true", onclick: (e) => toggleShare(e.currentTarget, r) }, "↗ Share"),
      ),
      el("div", { class: "src small" }, `Source: ${r.source}${r.checked ? ", checked " + fmtDate(r.checked) : ""}`),
    ),
  );
}

function intlPill(r) {
  const note = r.international_note || r.citizenship || "";
  if (r.international === "yes") return el("span", { class: "pill good", title: note }, "🌍 Open to international students");
  if (r.international === "no") return el("span", { class: "pill", title: note }, "🇺🇸 US citizens / permanent residents");
  return el("span", { class: "pill soft", title: note }, "🌍 International: check eligibility");
}

function toggleSave(r) {
  const i = saved.findIndex((s) => s.id === r.id);
  if (i >= 0) { saved.splice(i, 1); toast("Removed from your tracker."); }
  else {
    saved.push({ id: r.id, title: r.title, org: r.org, url: r.url, deadline: r.deadline || "", stage: "Interested", note: "" });
    toast("Saved. Find it in 📋 Tracker.");
  }
  saveTracker();
  renderPrograms();
}

// ------------------------------------------------------------------ professors
function renderProfs() {
  if (!INFO.professors) {
    $("rCount").textContent = "";
    $("rList").replaceChildren(empty("The professor list is filled by the weekly update from NSF's public award data. It will appear here after the first run."));
    return;
  }
  const filters = { q: $("rq").value, field: $("rField").value, state: $("rState").value, school: $("rSchool").value, hbcu: $("rHbcu").checked };
  const hasQuery = filters.q || filters.field || filters.state || filters.school || filters.hbcu || profile.interests || profile.major;
  if (!hasQuery) {
    $("rCount").textContent = "";
    $("rList").replaceChildren(empty("Type what you'd like to work on, or set up your profile, and we'll match you with researchers."));
    return;
  }
  const res = call("professors", { profile, filters });
  $("rCount").textContent = `${res.total.toLocaleString()} researcher${res.total === 1 ? "" : "s"} found${res.total > res.items.length ? `, showing the best ${res.items.length}` : ""}.`;
  $("rList").replaceChildren(...(res.items.length ? res.items.map(profCard) : [empty("No one matched. Try broader words, like “robotics” or “ecology”.")]));
}

function profCard(p) {
  const id = "prof-" + p.id;
  const isSaved = saved.some((s) => s.id === id);
  const search = `https://www.google.com/search?q=${encodeURIComponent(`${p.name} ${p.school} faculty`)}`;
  return el("article", { class: "card item" },
    scoreRing(p.score),
    el("div", { class: "body" },
      el("div", { class: "kind" }, "Researcher with an active NSF grant"),
      el("h3", {}, p.name),
      el("div", { class: "where" }, `${p.school} · ${[p.city, p.state].filter(Boolean).join(", ")} `, p.hbcu ? el("span", { class: "pill hbcu" }, "🎓 HBCU") : null),
      ...p.grants.map((g) => el("div", { class: "grant" },
        ext(g.url, g.title), el("span", { class: "small" }, g.ends ? ` · funded through ${fmtDate(g.ends)}` : ""),
        el("p", { class: "summary" }, g.summary))),
      el("div", { class: "pills" }, ...(p.fields || []).slice(0, 4).map((f) => el("span", { class: "pill soft" }, f))),
      p.reasons?.length ? el("div", { class: "why" }, "✓ " + p.reasons.join(" · ")) : null,
      el("div", { class: "row" },
        ext(search, "Find their faculty page ↗", "primary btn"),
        el("button", { class: "ghost", onclick: () => draftFor({ type: "professor", ...p }) }, "✉️ Draft email"),
        el("button", { class: "ghost", onclick: () => {
          const i = saved.findIndex((s) => s.id === id);
          if (i >= 0) saved.splice(i, 1);
          else saved.push({ id, title: `Research with ${p.name}`, org: p.school, url: p.grants[0]?.url || "", deadline: "", stage: "Interested", note: "" });
          saveTracker(); renderProfs(); toast(i >= 0 ? "Removed from your tracker." : "Saved. Find it in 📋 Tracker.");
        } }, isSaved ? "★ Saved" : "☆ Save to tracker"),
      ),
    ),
  );
}

// ------------------------------------------------------------------ email
// ------------------------------------------------------------------ AI writer (optional)
const AI_URL = (window.COMPASS_CONFIG && window.COMPASS_CONFIG.aiEndpoint) || "";
let aiDraft = false;   // true once the AI wrote the current draft, so typing doesn't replace it

// ------------------------------------------------------------------ paste-a-link submissions
const SUBMIT_URL = (window.COMPASS_CONFIG && window.COMPASS_CONFIG.submitEndpoint) || (AI_URL ? AI_URL.replace(/\/api\/draft$/, "/api/submit") : "");

function setupSubmit() {
  const form = $("subForm");
  $("footSubmit").addEventListener("click", (e) => {
    e.preventDefault();
    showTab("programs");
    $("submitCard").scrollIntoView({ behavior: "smooth", block: "center" });
    if (!form.hidden) setTimeout(() => $("subUrl").focus({ preventScroll: true }), 400);
  });
  if (!SUBMIT_URL) { form.hidden = true; $("subGithub").textContent = "Submit it with our form (GitHub account)."; return; }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = $("subStatus");
    const btn = $("subBtn");
    btn.disabled = true;
    status.className = "small";
    status.textContent = "Reading the page… this takes about 10 to 30 seconds.";
    try {
      const res = await fetch(SUBMIT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: $("subUrl").value, note: $("subNote").value, website: $("subWebsite").value }),
      });
      const out = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(out.error || "Something went wrong. Please try again later.");
      status.className = "small ok";
      status.textContent = out.unread
        ? "Thanks! That site doesn't let us read it automatically, so a person will open the link and add the details. It will appear here once it's approved."
        : `Thanks! We got ${out.title ? `“${out.title}”` : "your link"}. It will appear here once it's been checked and approved.`;
      form.reset();
    } catch (err) {
      status.className = "small err";
      status.textContent = err.message === "Failed to fetch" ? "We couldn't reach the server. Check your connection and try again." : err.message;
    } finally {
      btn.disabled = false;
    }
  });
}

function setupAi() {
  if (!AI_URL) return;
  for (const id of ["aiNote", "aiRow", "eInstrWrap"]) $(id).hidden = false;
  $("aiWrite").addEventListener("click", () => aiWrite());
  for (const b of $("tweakRow").querySelectorAll("button")) {
    b.addEventListener("click", () => aiWrite(b.dataset.tweak === "new" ? "" : b.dataset.tweak, b.dataset.tweak !== "new"));
  }
}

function slimTarget(t) {
  if (!t) return { type: "program" };
  const keep = ["type", "title", "org", "summary", "citizenship", "deadline", "contact", "name", "school"];
  const out = Object.fromEntries(keep.filter((k) => t[k]).map((k) => [k, t[k]]));
  if (t.grants) out.grants = t.grants.slice(0, 1).map((g) => ({ title: g.title, summary: g.summary }));
  return out;
}

async function aiWrite(tweak = "", revise = false) {
  const btns = [$("aiWrite"), ...$("tweakRow").querySelectorAll("button")];
  btns.forEach((b) => { b.disabled = true; });
  $("aiStatus").textContent = "✨ Writing…";
  try {
    const res = await fetch(AI_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile, target: slimTarget(emailTarget), kind: $("eKind").value, why: $("eWhy").value, when: $("eWhen").value,
        instruction: $("eInstr").value, tweak, previous: revise ? $("eBody").value : "",
      }),
    });
    const out = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(out.error || "The AI writer is unavailable right now.");
    $("eSubject").value = out.subject;
    $("eBody").value = out.body;
    aiDraft = true;
    const words = (out.body.match(/\S+/g) || []).length;
    $("eWords").textContent = `${words} words · written with AI`;
    $("eTips").replaceChildren(...[...(out.notes || []), "Read it once out loud and change anything that doesn't sound like you."].map((t) => el("li", {}, t)));
    $("tweakRow").hidden = false;
    $("aiStatus").textContent = "";
    updateMailto();
  } catch (err) {
    $("aiStatus").textContent = err.message + " The quick template below still works.";
  } finally {
    btns.forEach((b) => { b.disabled = false; });
  }
}

function draftFor(target) {
  emailTarget = target;
  $("eKind").value = "intro";
  aiDraft = false;
  $("tweakRow").hidden = true;
  showTab("email");
  renderEmail();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderEmail() {
  if (aiDraft) return;
  const t = emailTarget;
  $("eTarget").textContent = t
    ? `Writing to: ${t.type === "professor" ? `${t.name}, ${t.school}` : `${t.title} (${t.org})`}`
    : "Pick a program or professor with “✉️ Draft email”, or write a general one.";
  const out = call("email", { profile, target: t || { type: "program" }, kind: $("eKind").value, why: $("eWhy").value, when: $("eWhen").value });
  $("eSubject").value = out.subject;
  $("eBody").value = out.body;
  $("eWords").textContent = `${out.words} words`;
  $("eTips").replaceChildren(...out.tips.map((tip) => el("li", {}, tip)));
  updateMailto();
}
function updateMailto() {
  $("eMail").href = `mailto:?subject=${encodeURIComponent($("eSubject").value)}&body=${encodeURIComponent($("eBody").value)}`;
}

// ------------------------------------------------------------------ tracker
function renderTracker() {
  if (!py) return;
  const t = call("tracker", { items: saved });
  const badge = $("dueBadge");
  badge.hidden = !t.due_soon;
  badge.textContent = t.due_soon;
  $("tStages").replaceChildren(...t.stages.filter((s) => t.counts[s]).map((s) => el("span", { class: "pill soft" }, `${s}: ${t.counts[s]}`)));
  $("tList").replaceChildren(...(t.items.length ? t.items.map(trackRow) : [empty("Nothing saved yet. Tap “☆ Save to tracker” on any program or professor.")]));
}

function trackRow(it) {
  const stageSel = el("select", { "aria-label": "Stage", onchange: (e) => {
    const s = saved.find((x) => x.id === it.id);
    s.stage = e.target.value;
    if (s.stage === "Emailed" && !s.emailed_on) s.emailed_on = new Date().toISOString().slice(0, 10);
    saveTracker();
  } }, ...INFO_STAGES().map((s) => el("option", { value: s, selected: s === it.stage }, s)));
  return el("article", { class: "card track" + (it.urgent ? " urgent" : "") + (it.closed ? " closed" : "") },
    el("div", { class: "body" },
      el("h3", {}, it.url ? ext(it.url, it.title) : it.title),
      el("div", { class: "where" }, [it.org, it.deadline ? `Deadline ${fmtDate(it.deadline)}${it.days_left != null && it.days_left >= 0 ? ` (${it.days_left} days left)` : it.days_left < 0 ? " (passed)" : ""}` : ""].filter(Boolean).join(" · ")),
      it.nudge ? el("div", { class: "warnline" }, "🔔 " + it.nudge) : null,
      it.note ? el("p", { class: "small" }, it.note) : null,
    ),
    el("div", { class: "side" }, stageSel, el("button", { class: "ghost", onclick: () => openItem(it.id) }, "Edit")),
  );
}
const INFO_STAGES = () => ["Interested", "Emailed", "Applied", "Interview", "Offer", "Declined", "Not selected"];

function setupItemForm() {
  const f = $("itemForm");
  fillSelect(f.stage, INFO_STAGES(), false);
  $("itemDlg").addEventListener("close", () => {
    const v = $("itemDlg").returnValue;
    const id = f.dataset.id;
    if (v === "delete") { saved = saved.filter((s) => s.id !== id); saveTracker(); renderPrograms(); return; }
    if (v !== "save") return;
    const data = Object.fromEntries(new FormData(f).entries());
    const existing = saved.find((s) => s.id === id);
    if (existing) Object.assign(existing, data);
    else saved.push({ id: "own-" + Date.now(), ...data });
    saveTracker();
  });
}
function openItem(id) {
  const f = $("itemForm");
  const it = saved.find((s) => s.id === id) || { stage: "Interested" };
  f.dataset.id = id || "";
  for (const k of ["title", "org", "url", "deadline", "stage", "emailed_on", "follow_up", "note"]) f[k].value = it[k] || (k === "stage" ? "Interested" : "");
  $("itemTitle").textContent = id ? "Edit application" : "Track an application";
  $("itemDelete").hidden = !id;
  $("itemDlg").showModal();
}

function download(name, text, type) {
  const a = el("a", { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  document.body.append(a); a.click(); a.remove();
}
function downloadIcs() {
  const items = saved.filter((s) => s.deadline || s.follow_up);
  if (!items.length) { toast("Add a deadline or reminder date to something first."); return; }
  download("research-deadlines.ics", call("ics", { items }), "text/calendar");
  toast("Open the file to add it to your calendar.");
}
async function importBackup(e) {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (Array.isArray(data.saved)) saved = data.saved;
    if (data.profile && typeof data.profile === "object") profile = data.profile;
    saveProfile(); saveTracker(); renderPrograms(); renderProfs(); renderEmail();
    toast("Restored your backup.");
  } catch { toast("That file isn't a Research Compass backup."); }
  e.target.value = "";
}

// ------------------------------------------------------------------ profile
function setupProfileForm() {
  const f = $("profileForm");
  fillSelect(f.year, ["", ...INFO.years], false);
  f.year.options[0].textContent = "Choose…";
  fillSelect(f.major, ["", ...INFO.fields], false);
  f.major.options[0].textContent = "Choose…";
  setupResume();
  setupClearData();
  $("profile").addEventListener("close", () => {
    if ($("profile").returnValue !== "save") return;
    const data = Object.fromEntries(new FormData(f).entries());
    profile = {
      ...data,
      paid_only: f.paid_only.checked,
      hbcu: f.hbcu.checked,
      fields: [...$("pfFields").querySelectorAll("button[aria-pressed=true]")].map((b) => b.dataset.v),
      states: (data.states || "").toUpperCase().split(/[^A-Z]+/).filter((s) => s.length === 2),
    };
    saveProfile();
    renderPrograms(); renderProfs(); renderEmail();
    toast("Profile saved. Programs are now ranked for you.");
  });
}
function openProfile() {
  const f = $("profileForm");
  for (const k of ["name", "school", "year", "major", "gpa", "interests", "skills", "project", "citizen"]) f[k].value = profile[k] || "";
  f.states.value = (profile.states || []).join(", ");
  f.paid_only.checked = !!profile.paid_only;
  f.hbcu.checked = !!profile.hbcu;
  const chosen = new Set(profile.fields || []);
  $("pfFields").replaceChildren(...INFO.fields.map((v) => el("button", {
    type: "button", class: "pill toggle", "data-v": v, "aria-pressed": String(chosen.has(v)),
    onclick: (e) => e.currentTarget.setAttribute("aria-pressed", String(e.currentTarget.getAttribute("aria-pressed") !== "true")),
  }, v)));
  for (const node of f.querySelectorAll(".filled")) node.classList.remove("filled");
  $("profile").returnValue = "";
  $("profile").showModal();
}

// ------------------------------------------------------------------ résumé → profile
// The file is read here in the browser. Python (compass.resume) suggests fields; if the AI
// writer is set up and the student allows it, Gemini reads the text too (after emails, phone
// numbers and links are removed). Only empty fields are filled, and the student reviews them.
const RESUME_KEYS = { name: "name", school: "college", year: "year", major: "major", gpa: "GPA", skills: "skills", interests: "interests", project: "project" };

function setupClearData() {
  const btn = $("pfClear");
  let armed = false;
  btn.addEventListener("click", () => {
    if (!armed) {
      armed = true;
      btn.textContent = "Tap again to erase your profile and tracker";
      setTimeout(() => { armed = false; btn.textContent = "🗑️ Clear my data"; }, 5000);
      return;
    }
    try { for (const k of Object.keys(localStorage)) if (k.startsWith("rc.")) localStorage.removeItem(k); } catch { /* nothing stored */ }
    profile = {};
    saved = [];
    location.reload();
  });
}

function setupResume() {
  if (AI_URL) $("pfResumeAiWrap").hidden = false;
  $("pfResume").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) await fillFromResume(file);
  });
  $("profileForm").addEventListener("input", (e) => e.target.classList.remove("filled"));
}

async function fillFromResume(file) {
  const status = $("pfResumeStatus");
  const btn = $("pfResumeBtn");
  if (file.size > 8 * 1024 * 1024) { status.textContent = "That file is too big (over 8 MB). Try a smaller PDF or a Word file."; return; }
  btn.classList.add("busy");
  status.textContent = "📄 Reading your résumé…";
  try {
    const text = (await resumeText(file)).trim();
    if (text.length < 40) {
      status.textContent = "We couldn't find any text in that file. If it's a scanned image, try a Word (.docx) or text version.";
      return;
    }
    let found = call("resume", { text, fields: INFO.fields });
    let usedAi = false;
    if (AI_URL && $("pfResumeAi").checked) {
      status.textContent = "✨ The AI is reading your résumé…";
      try {
        const res = await fetch(AI_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "resume", text: scrubPersonal(text).slice(0, 12000), years: INFO.years, fields: INFO.fields, today: new Date().toISOString().slice(0, 10) }),
        });
        const ai = await res.json().catch(() => ({}));
        if (res.ok) {
          const local = found;
          found = Object.fromEntries(Object.keys(RESUME_KEYS).map((k) => [k, (ai[k] || "").trim() || local[k] || ""]));
          // The class year is worked out exactly from the graduation date on this device; prefer that.
          if (local.year) found.year = local.year;
          if (found.name && found.name === found.name.toUpperCase()) found.name = found.name.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
          usedAi = true;
        }
      } catch { /* keep the on-device result */ }
    }
    const f = $("profileForm");
    const filled = [];
    const differs = [];   // fields you already filled that the résumé says differently
    for (const [key, label] of Object.entries(RESUME_KEYS)) {
      const input = f[key];
      const value = (found[key] || "").trim();
      if (!value) continue;
      if (input.tagName === "SELECT" && ![...input.options].some((o) => o.value === value)) continue;
      const current = (input.value || "").trim();
      if (!current) {
        input.value = value;
        input.classList.add("filled");
        filled.push(label);
      } else if (current.toLowerCase() !== value.toLowerCase()) {
        differs.push({ key, label, value });
      }
    }
    const parts = [];
    if (filled.length) parts.push(`Filled in ${filled.join(", ")}${usedAi ? " (read with AI)" : ""}.`);
    if (differs.length) parts.push(`Your résumé also has different ${differs.map((d) => d.label).join(", ")} from what's in your profile now.`);
    if (!parts.length) parts.push("Your résumé matches your profile; nothing to change.");
    parts.push("Highlighted fields came from your résumé: check them, edit anything, then save. Close without saving to undo.");
    status.replaceChildren(document.createTextNode(parts.join(" ")));
    if (differs.length) {
      const replace = el("button", { type: "button", class: "ghost", onclick: () => {
        for (const d of differs) { f[d.key].value = d.value; f[d.key].classList.add("filled"); }
        replace.remove();
        status.firstChild.textContent = `Updated ${differs.map((d) => d.label).join(", ")} from your résumé. Check the highlighted fields, then save. Close without saving to undo.`;
      } }, `Use the résumé's ${differs.length === 1 ? differs[0].label : differs.length + " fields"}`);
      status.append(document.createElement("br"), replace);
    }
  } catch (err) {
    status.textContent = err.message || "We couldn't read that file. Try a PDF, Word (.docx) or text file.";
  } finally {
    btn.classList.remove("busy");
  }
}

function scrubPersonal(text) {
  return text
    .replace(/\S+@\S+/g, " ")
    .replace(/(https?:\/\/|www\.)\S+/gi, " ")
    .replace(/\b(linkedin|github)\.com\/\S*/gi, " ")
    .replace(/\+?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}/g, " ");
}

async function resumeText(file) {
  const name = file.name.toLowerCase();
  if (name.endsWith(".pdf") || file.type === "application/pdf") return pdfText(file);
  if (name.endsWith(".docx")) return docxText(file);
  if (name.endsWith(".doc")) throw new Error("Old Word files (.doc) can't be read. In Word, choose Save As → .docx or PDF, then try again.");
  return file.text();
}

async function pdfText(file) {
  const pdfjs = await import(new URL("vendor/pdfjs/pdf.min.mjs", location.href).href);
  pdfjs.GlobalWorkerOptions.workerSrc = new URL("vendor/pdfjs/pdf.worker.min.mjs", location.href).href;
  const doc = await pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()), isEvalSupported: false }).promise;
  const lines = [];
  for (let i = 1; i <= Math.min(doc.numPages, 5); i++) {
    const content = await (await doc.getPage(i)).getTextContent();
    let line = "";
    let lastY = null;
    for (const item of content.items) {
      const y = item.transform ? Math.round(item.transform[5]) : lastY;
      if (lastY !== null && y !== null && Math.abs(y - lastY) > 2 && line.trim()) { lines.push(line); line = ""; }
      line += item.str;
      if (item.hasEOL) { lines.push(line); line = ""; }
      lastY = y;
    }
    if (line.trim()) lines.push(line);
  }
  return lines.join("\n");
}

// A .docx is a zip file; the text is in word/document.xml.
async function docxText(file) {
  const buf = new Uint8Array(await file.arrayBuffer());
  const view = new DataView(buf.buffer);
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 66000); i--) {
    if (view.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("That Word file looks damaged. Try saving it again or use a PDF.");
  const count = view.getUint16(eocd + 10, true);
  let p = view.getUint32(eocd + 16, true);
  for (let n = 0; n < count && p + 46 <= buf.length; n++) {
    if (view.getUint32(p, true) !== 0x02014b50) break;
    const method = view.getUint16(p + 10, true);
    const size = view.getUint32(p + 20, true);
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    const local = view.getUint32(p + 42, true);
    const entry = new TextDecoder().decode(buf.subarray(p + 46, p + 46 + nameLen));
    if (entry === "word/document.xml") {
      const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
      const data = buf.subarray(start, start + size);
      let xmlBytes = data;
      if (method === 8) {
        const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
        xmlBytes = new Uint8Array(await new Response(stream).arrayBuffer());
      } else if (method !== 0) throw new Error("That Word file uses a format we can't read. Try a PDF.");
      const xml = new DOMParser().parseFromString(new TextDecoder().decode(xmlBytes), "application/xml");
      const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
      return [...xml.getElementsByTagNameNS(W, "p")].map((para) =>
        [...para.getElementsByTagNameNS(W, "*")].map((node) => (node.localName === "t" ? node.textContent : node.localName === "tab" ? "\t" : "")).join("")).join("\n");
    }
    p += 46 + nameLen + extraLen + commentLen;
  }
  throw new Error("We couldn't find the text in that Word file. Try a PDF.");
}

start();
