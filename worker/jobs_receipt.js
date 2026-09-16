export const RECEIPT_VIEWER_JS = `
function tracked(value) {
  if (value == null || value === "") return "not tracked for this application";
  return String(value);
}
function parseJsonField(value) {
  if (value == null || value === "") return null;
  if (typeof value === "object") return value;
  try { return JSON.parse(value); } catch (err) { return null; }
}
function renderScoreSection(job) {
  const breakdown = parseJsonField(job.match_breakdown);
  if (!breakdown) {
    return "<h3>Score breakdown</h3><p class=\\"muted\\">not tracked for this application</p>";
  }
  const hard = breakdown.hard_filters || {};
  const filterRows = ["skip_keyword", "remote_or_india", "role_keyword", "eligibility"].map((key) => {
    const ok = hard[key];
    return "<li>" + esc(key) + ": " + (ok === true ? "pass" : ok === false ? "fail" : "—") + "</li>";
  }).join("");
  const passed = hard.passed === true ? "pass" : hard.passed === false ? "fail" : "—";
  const reason = breakdown.filter_reason ? " · reason: " + esc(breakdown.filter_reason) : "";
  const scores = breakdown.variant_scores || {};
  const bars = Object.keys(scores).map((name) => {
    const combined = Number(scores[name].combined) || 0;
    const width = Math.max(0, Math.min(100, combined));
    return "<div class=\\"bar-row\\"><span>" + esc(name) + "</span>" +
      "<span class=\\"bar\\"><span style=\\"width:" + width + "%\\"></span></span>" +
      "<span>" + esc(String(combined)) + "</span></div>";
  }).join("");
  const terms = (breakdown.top_keywords || []).map((item) => {
    const term = item && item.term != null ? item.term : item;
    const weight = item && item.weight != null ? " (" + item.weight + ")" : "";
    return "<li>" + esc(String(term)) + esc(String(weight)) + "</li>";
  }).join("");
  return "<h3>Score breakdown</h3>" +
    "<p class=\\"muted\\">weights " + esc(breakdown.weights_version || "v1") +
    " (0.7 cosine + 0.3 overlap). Filters " + passed + reason + "</p>" +
    "<ul>" + filterRows + "</ul>" +
    (bars ? "<div class=\\"bars\\">" + bars + "</div>" : "<p class=\\"muted\\">No variant scores (filtered out).</p>") +
    "<p>Winner cosine " + esc(text(breakdown.cosine_score)) +
    " · overlap " + esc(text(breakdown.keyword_overlap_score)) +
    " · final " + esc(text(breakdown.final_score)) + "</p>" +
    "<h4>Top overlapping terms</h4>" +
    (terms ? "<ul>" + terms + "</ul>" : "<p class=\\"muted\\">None.</p>");
}
function renderResumeSection(payload) {
  const job = payload.job;
  const resume = payload.resume;
  const live = payload.live_resume_hash;
  const hash = job.resume_version_hash || "";
  let note = "";
  if (hash && live && hash !== live) {
    note = "<p class=\\"muted\\">This may differ from your current resume.</p>";
  }
  if (!resume || !resume.content) {
    return "<h3>Resume used</h3><p class=\\"muted\\">" + esc(tracked(hash || null)) + "</p>" + note;
  }
  return "<h3>Resume used</h3>" +
    "<p>variant " + esc(text(resume.variant || job.resume_variant)) +
    " · hash " + esc(hash.slice(0, 12)) + "…</p>" + note +
    "<div class=\\"pre\\">" + esc(resume.content) + "</div>";
}
function renderTimeline(job) {
  const steps = [
    ["discovered", job.discovered_at],
    ["drafted", job.drafted_at],
    ["decided", job.decided_at],
    ["submitted", job.submitted_at],
  ].filter((pair) => pair[1]);
  const items = steps.map((pair) => "<li><strong>" + esc(pair[0]) + "</strong> — " + esc(formatIst(pair[1])) + "</li>").join("");
  const confirm = parseJsonField(job.confirmation_signal);
  let confirmHtml = "<p class=\\"muted\\">not tracked for this application</p>";
  if (confirm) {
    confirmHtml = "<p>Send confirmation: <strong>" + (confirm.ok ? "yes" : "no") + "</strong>" +
      (confirm.url ? ' · <a href="' + esc(confirm.url) + '">page</a>' : "") + "</p>" +
      (confirm.text ? "<p class=\\"muted\\">" + esc(confirm.text) + "</p>" : "");
  }
  const run = job.github_run_id
    ? "<p class=\\"muted\\">Actions run " + esc(job.github_run_id) + "</p>"
    : "";
  return "<h3>Timeline</h3>" +
    (items ? "<ol class=\\"timeline\\">" + items + "</ol>" : "<p class=\\"muted\\">not tracked for this application</p>") +
    confirmHtml + run;
}
`;
