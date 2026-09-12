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
.pager { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 16px 0; }
.pager button { min-width: 36px; padding: 6px 10px; }
.pager button.active { background: #222; color: #fff; border-color: #222; }
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
function pageWindow(page, totalPages, width) {
  if (totalPages < 1) return [];
  const block = Math.floor((Math.max(1, page) - 1) / width);
  const start = block * width + 1;
  const end = Math.min(totalPages, start + width - 1);
  const pages = [];
  for (let i = start; i <= end; i++) pages.push(i);
  return pages;
}
function listQuery(page) {
  const params = new URLSearchParams();
  params.set("page", String(page));
  const status = document.getElementById("status").value;
  const q = document.getElementById("q").value.trim();
  const sort = document.getElementById("sort").value;
  if (status) params.set("status", status);
  if (q) params.set("q", q);
  if (document.getElementById("errors").checked) params.set("errors", "1");
  if (sort && sort !== "discovered") params.set("sort", sort);
  return params;
}
function renderChips(data) {
  const order = ["discovered", "drafted", "pending_approval", "submitted", "failed", "rejected"];
  const counts = data.counts || {};
  const chips = order.map((status) => '<span class="chip">' + esc(status) + ": " + (counts[status] || 0) + "</span>");
  Object.keys(counts).sort().forEach((status) => {
    if (!order.includes(status)) chips.push('<span class="chip">' + esc(status) + ": " + counts[status] + "</span>");
  });
  const cap = data.daily_cap == null ? "?" : data.daily_cap;
  const pages = data.total_pages || 0;
  const size = data.page_size || 10;
  chips.push('<span class="chip">submitted today: ' + (data.submitted_today || 0) + " / " + cap + "</span>");
  chips.push('<span class="chip">jobs: ' + (data.job_count || 0) + "</span>");
  chips.push('<span class="chip">' + pages + " pages · " + size + " per page</span>");
  return chips.join("");
}
function renderPager(data) {
  const page = data.page || 1;
  const totalPages = data.total_pages || 0;
  const pager = document.getElementById("pager");
  if (totalPages < 1) {
    pager.innerHTML = "";
    pager.hidden = true;
    return;
  }
  pager.hidden = false;
  const buttons = pageWindow(page, totalPages, 10).map((n) =>
    "<button type=\\"button\\" data-page=\\"" + n + "\\"" + (n === page ? " class=\\"active\\"" : "") + ">" + n + "</button>"
  ).join("");
  pager.innerHTML = "<button type=\\"button\\" data-page=\\"prev\\"" + (page <= 1 ? " disabled" : "") + ">Prev</button>" +
    buttons +
    "<button type=\\"button\\" data-page=\\"next\\"" + (page >= totalPages ? " disabled" : "") + ">Next</button>" +
    "<span class=\\"muted\\">Page " + page + " of " + totalPages + "</span>";
}
function renderRows(jobs) {
  document.getElementById("rows").innerHTML = (jobs || []).map((job) => {
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
function renderDetail(payload) {
  const job = payload.job;
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
async function fetchJson(qs) {
  const res = await fetch("/resumes/jobs.json?" + qs.toString(), { credentials: "same-origin", cache: "no-store" });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.error || "Could not load jobs.json");
  return payload;
}
function syncListUrl(params) {
  const next = params.toString();
  const url = next ? "/resumes/jobs?" + next : "/resumes/jobs";
  history.replaceState(null, "", url);
}
async function loadList(page) {
  const params = listQuery(page);
  const errEl = document.getElementById("list-error");
  errEl.hidden = true;
  document.getElementById("rows").innerHTML = "<tr><td colspan=\\"9\\">Loading…</td></tr>";
  const data = await fetchJson(params);
  document.getElementById("note").textContent = data.missing
    ? "No snapshot yet. Wait for the next scan/submit, or run python src/dashboard.py and commit data/jobs.json."
    : "Snapshot " + (data.exported_at || "unknown") + " (last committed scan/submit). Read-only.";
  document.getElementById("chips").innerHTML = renderChips(data);
  document.getElementById("count").textContent = (data.jobs || []).length + " on this page · " + (data.total || 0) + " match";
  renderRows(data.jobs);
  renderPager(data);
  syncListUrl(params);
  return data;
}
async function main() {
  const params = new URLSearchParams(location.search);
  const id = params.get("id");
  try {
    if (id) {
      const payload = await fetchJson(new URLSearchParams({ id: id }));
      document.getElementById("note").textContent = payload.exported_at
        ? "Snapshot " + payload.exported_at + " (last committed scan/submit). Read-only."
        : "";
      renderDetail(payload);
      return;
    }
    document.getElementById("filters").hidden = false;
    if (params.get("status")) document.getElementById("status").value = params.get("status");
    if (params.get("q")) document.getElementById("q").value = params.get("q");
    if (params.get("sort")) document.getElementById("sort").value = params.get("sort");
    document.getElementById("errors").checked = params.get("errors") === "1";
    let current = Number(params.get("page") || 1) || 1;
    let searchTimer = 0;
    const reload = (page) => loadList(page).then((data) => { current = data.page || 1; }).catch((err) => {
      const errEl = document.getElementById("list-error");
      errEl.textContent = err.message;
      errEl.hidden = false;
    });
    ["status", "sort", "errors"].forEach((name) => {
      document.getElementById(name).addEventListener("change", () => reload(1));
    });
    document.getElementById("q").addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => reload(1), 300);
    });
    document.getElementById("pager").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-page]");
      if (!btn || btn.disabled) return;
      const target = btn.getAttribute("data-page");
      if (target === "prev") reload(current - 1);
      else if (target === "next") reload(current + 1);
      else reload(Number(target));
    });
    await reload(current);
  } catch (err) {
    document.getElementById("app").innerHTML = "<p class=\\"error\\">" + esc(err.message) + "</p>";
  }
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
     <p id="list-error" class="error" hidden></p>
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
       <nav id="pager" class="pager" hidden></nav>
     </div>
     ${logoutForm()}
     <script>${VIEWER_JS}</script>`,
    200,
    {},
    JOBS_CSS
  );
}
