import { logoutForm, nav, page } from "./common.js";

const JOBS_CSS = `
body { max-width: 1200px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.chip { background: #f4f4f4; border-radius: 999px; padding: 4px 10px; font-size: 13px; }
.filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin: 12px 0 16px; }
.filters label { margin: 0; }
.filters input, .filters select { width: auto; min-width: 160px; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid #ddd; padding: 8px; text-align: left; font-size: 13px; vertical-align: top; }
th { background: #f4f4f4; }
tr.failed { background: #fff2f2; }
tr.pending_approval { background: #fff8e6; }
tr.submitted { background: #f1faf1; }
tr.rejected { color: #666; }
.pre { white-space: pre-wrap; background: #f7f7f7; padding: 12px; border-radius: 6px; font-size: 13px; }
.msg { max-width: 260px; white-space: pre-wrap; }
.meta dt { font-weight: 600; margin-top: 10px; }
.meta dd { margin: 2px 0 0; }
`;

const VIEWER_JS = `
function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function text(value) {
  if (value == null || value === "") return "—";
  return String(value);
}
function clip(value, n) {
  const s = String(value == null ? "" : value).trim();
  if (!s) return "—";
  return s.length > n ? s.slice(0, n - 3) + "..." : s;
}
function searchBlob(job) {
  return [job.id, job.company, job.role, job.error_message, job.resume_variant, job.sent_message, job.draft_answer]
    .map((v) => String(v || "").toLowerCase()).join(" ");
}
function sortJobs(jobs, sort) {
  const copy = jobs.slice();
  copy.sort((a, b) => {
    if (sort === "score") return (Number(b.match_score) || 0) - (Number(a.match_score) || 0);
    if (sort === "submitted") return String(b.submitted_at || "").localeCompare(String(a.submitted_at || ""));
    if (sort === "decided") return String(b.decided_at || "").localeCompare(String(a.decided_at || ""));
    return String(b.discovered_at || "").localeCompare(String(a.discovered_at || ""));
  });
  return copy;
}
function renderChips(data) {
  const order = ["discovered", "drafted", "pending_approval", "submitted", "failed", "rejected"];
  const counts = data.counts || {};
  const chips = order.map((status) => '<span class="chip">' + esc(status) + ": " + (counts[status] || 0) + "</span>");
  Object.keys(counts).sort().forEach((status) => {
    if (!order.includes(status)) chips.push('<span class="chip">' + esc(status) + ": " + counts[status] + "</span>");
  });
  const cap = data.daily_cap == null ? "?" : data.daily_cap;
  chips.push('<span class="chip">submitted today: ' + (data.submitted_today || 0) + " / " + cap + "</span>");
  chips.push('<span class="chip">total: ' + (data.jobs || []).length + "</span>");
  return chips.join("");
}
function renderList(data) {
  const status = document.getElementById("status").value;
  const q = document.getElementById("q").value.trim().toLowerCase();
  const errorsOnly = document.getElementById("errors").checked;
  let rows = sortJobs(data.jobs || [], document.getElementById("sort").value);
  if (status) rows = rows.filter((j) => (j.status || "") === status);
  if (errorsOnly) rows = rows.filter((j) => String(j.error_message || "").trim());
  if (q) rows = rows.filter((j) => searchBlob(j).includes(q));
  document.getElementById("count").textContent = rows.length + " shown";
  document.getElementById("rows").innerHTML = rows.map((job) => {
    return "<tr class=\\"" + esc(job.status || "") + "\\"><td>" + esc(job.status) +
      "</td><td>" + esc(text(job.match_score)) +
      "</td><td><a href=\\"/resumes/jobs?id=" + encodeURIComponent(job.id || "") + "\\">" +
      esc(text(job.company)) + "</a></td><td>" + esc(text(job.role)) +
      "</td><td>" + esc(text(job.resume_variant)) +
      "</td><td class=\\"msg\\">" + esc(clip(job.sent_message, 80)) +
      "</td><td>" + esc(text(job.discovered_at)) +
      "</td><td>" + esc(clip(job.error_message, 80)) +
      "</td><td>" + (job.url ? "<a href=\\"" + esc(job.url) + "\\">listing</a>" : "—") + "</td></tr>";
  }).join("") || "<tr><td colspan=\\"9\\">No jobs match these filters.</td></tr>";
}
function renderDetail(data, id) {
  const job = (data.jobs || []).find((j) => j.id === id);
  const root = document.getElementById("app");
  if (!job) {
    root.innerHTML = "<p class=\\"error\\">Unknown job id.</p><p><a href=\\"/resumes/jobs\\">Back to jobs</a></p>";
    return;
  }
  const fields = [
    ["id", job.id], ["status", job.status], ["company", job.company], ["role", job.role],
    ["url", job.url], ["match_score", job.match_score], ["resume_variant", job.resume_variant],
    ["discovered_at", job.discovered_at], ["decided_at", job.decided_at],
    ["submitted_at", job.submitted_at], ["error_message", job.error_message],
    ["sent_message", job.sent_message],
  ];
  const dts = fields.map(([k, v]) => k === "url" && v
    ? "<dt>" + k + "</dt><dd><a href=\\"" + esc(v) + "\\">" + esc(v) + "</a></dd>"
    : "<dt>" + k + "</dt><dd>" + esc(text(v)) + "</dd>").join("");
  root.innerHTML = "<p><a href=\\"/resumes/jobs\\">Back to jobs</a></p>" +
    "<h2>" + esc(text(job.company)) + " — " + esc(text(job.role)) + "</h2>" +
    "<dl class=\\"meta\\">" + dts + "</dl>" +
    "<h3>Sent message</h3><div class=\\"pre\\">" + esc(text(job.sent_message)) + "</div>" +
    "<h3>Draft</h3><div class=\\"pre\\">" + esc(text(job.draft_answer)) + "</div>" +
    "<h3>Job description</h3><div class=\\"pre\\">" + esc(text(job.jd_text)) + "</div>";
}
async function main() {
  const id = new URLSearchParams(location.search).get("id");
  const res = await fetch("/resumes/jobs.json", { credentials: "same-origin", cache: "no-store" });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    document.getElementById("app").innerHTML =
      "<p class=\\"error\\">" + esc(payload.error || "Could not load jobs.json") + "</p>";
    return;
  }
  document.getElementById("note").textContent = payload.missing
    ? "No snapshot yet. Wait for the next scan/submit, or run python src/dashboard.py and commit data/jobs.json."
    : "Snapshot " + (payload.exported_at || "unknown") + " (last committed scan/submit). Read-only.";
  document.getElementById("chips").innerHTML = renderChips(payload);
  if (id) { renderDetail(payload, id); return; }
  document.getElementById("filters").hidden = false;
  const redraw = () => renderList(payload);
  ["status", "q", "errors", "sort"].forEach((name) => {
    document.getElementById(name).addEventListener("input", redraw);
    document.getElementById(name).addEventListener("change", redraw);
  });
  renderList(payload);
}
main();
`;

export function jobsPage() {
  return page(
    "Jobs",
    `${nav("jobs")}
     <h1>Jobs</h1>
     <p id="note" class="muted">Loading snapshot…</p>
     <div id="chips" class="chips"></div>
     <div id="filters" class="filters" hidden>
       <label>Status
         <select id="status">
           <option value="">all</option>
           <option>discovered</option>
           <option>drafted</option>
           <option>pending_approval</option>
           <option>submitted</option>
           <option>failed</option>
           <option>rejected</option>
         </select>
       </label>
       <label>Search
         <input id="q" type="text" placeholder="company, role, id, error, sent"/>
       </label>
       <label>Sort
         <select id="sort">
           <option value="discovered">discovered</option>
           <option value="score">score</option>
           <option value="decided">decided</option>
           <option value="submitted">submitted</option>
         </select>
       </label>
       <label class="row" style="margin:0">
         <input id="errors" type="checkbox"/>
         errors only
       </label>
       <span id="count" class="muted"></span>
     </div>
     <div id="app">
       <div class="table-wrap">
         <table>
           <thead>
             <tr>
               <th>status</th><th>score</th><th>company</th><th>role</th>
               <th>resume</th><th>sent</th><th>discovered</th><th>error</th><th>url</th>
             </tr>
           </thead>
           <tbody id="rows"><tr><td colspan="9">Loading…</td></tr></tbody>
         </table>
       </div>
     </div>
     ${logoutForm()}
     <script>${VIEWER_JS}</script>`,
    200,
    {},
    JOBS_CSS
  );
}
