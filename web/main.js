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
const KIND_LABEL = { reu: "NSF REU summer site", program: "National program", federal: "Federal student job", submitted: "Community submitted" };

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
    stat(reu, "NSF REU summer sites"),
    stat(INFO.professors, "professors with active NSF grants"),
    stat(INFO.schools, "universities"),
  );

  $("dirs").replaceChildren(...INFO.directories.map((d) => el("div", { class: "dir" }, ext(d.url, d.name), el("span", { class: "small" }, d.what))));

  // tabs
  for (const b of $("tabs").querySelectorAll("button")) b.addEventListener("click", () => showTab(b.dataset.tab));

  // programs
  for (const id of ["pField", "pState", "pKind", "pSort", "pEligible"]) $(id).addEventListener("change", () => { pLimit = 30; renderPrograms(); });
  $("pq").addEventListener("input", debounce(() => { pLimit = 30; renderPrograms(); }));
  $("pMore").addEventListener("click", () => { pLimit += 30; renderPrograms(); });

  // professors
  for (const id of ["rField", "rState"]) $(id).addEventListener("change", renderProfs);
  for (const id of ["rq", "rSchool"]) $(id).addEventListener("input", debounce(renderProfs, 250));

  // email
  for (const id of ["eKind", "eWhy", "eWhen"]) $(id).addEventListener("input", debounce(renderEmail, 150));
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
  showTab(location.hash.slice(1) || "programs");
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
  $("meLabel").textContent = profile.major ? `${profile.name || "You"} · ${profile.major}` : "Set up your profile";
}

// ------------------------------------------------------------------ programs
function renderPrograms() {
  const filters = {
    q: $("pq").value, field: $("pField").value, state: $("pState").value, kind: $("pKind").value,
    sort: $("pSort").value, eligible: $("pEligible").checked, paid: !!profile.paid_only, limit: pLimit,
  };
  const res = call("programs", { profile, filters });
  $("pCount").textContent = res.total ? `${res.total.toLocaleString()} match${res.total === 1 ? "" : "es"}${profile.major ? ", ranked for you" : ". Set up your profile to rank them for you"}.` : "";
  $("pList").replaceChildren(...(res.items.length ? res.items.map(programCard) : [empty("No programs match. Try fewer filters.")]));
  $("pMore").hidden = res.total <= res.items.length;
}

function empty(text) { return el("div", { class: "empty" }, text); }

function scoreRing(score) {
  const cls = score >= 70 ? "hi" : score >= 45 ? "mid" : "lo";
  return el("div", { class: "score " + cls, title: "How well it fits your profile" }, el("b", {}, score), el("span", {}, "match"));
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
        r.citizenship ? el("span", { class: "pill" }, "🪪 " + r.citizenship) : null,
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
      ),
      el("div", { class: "src small" }, `Source: ${r.source}${r.checked ? ", checked " + fmtDate(r.checked) : ""}`),
    ),
  );
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
  const filters = { q: $("rq").value, field: $("rField").value, state: $("rState").value, school: $("rSchool").value };
  const hasQuery = filters.q || filters.field || filters.state || filters.school || profile.interests || profile.major;
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
      el("div", { class: "where" }, `${p.school} · ${[p.city, p.state].filter(Boolean).join(", ")}`),
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
function draftFor(target) {
  emailTarget = target;
  $("eKind").value = "intro";
  showTab("email");
  renderEmail();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderEmail() {
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
  $("profile").addEventListener("close", () => {
    if ($("profile").returnValue !== "save") return;
    const data = Object.fromEntries(new FormData(f).entries());
    profile = {
      ...data,
      paid_only: f.paid_only.checked,
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
  for (const k of ["name", "school", "year", "major", "interests", "skills", "project", "citizen"]) f[k].value = profile[k] || "";
  f.states.value = (profile.states || []).join(", ");
  f.paid_only.checked = !!profile.paid_only;
  const chosen = new Set(profile.fields || []);
  $("pfFields").replaceChildren(...INFO.fields.map((v) => el("button", {
    type: "button", class: "pill toggle", "data-v": v, "aria-pressed": String(chosen.has(v)),
    onclick: (e) => e.currentTarget.setAttribute("aria-pressed", String(e.currentTarget.getAttribute("aria-pressed") !== "true")),
  }, v)));
  $("profile").returnValue = "";
  $("profile").showModal();
}

start();
