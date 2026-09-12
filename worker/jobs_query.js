export const PAGE_SIZE = 10;
export const PAGE_BUTTONS = 10;

const LIST_OMIT = new Set(["jd_text", "draft_answer", "approval_token"]);

export function searchBlob(job) {
  return [job.id, job.company, job.role, job.error_message, job.resume_variant, job.sent_message, job.draft_answer]
    .map((value) => String(value || "").toLowerCase())
    .join(" ");
}

export function sortJobs(jobs, sort) {
  const copy = jobs.slice();
  copy.sort((a, b) => {
    if (sort === "score") return (Number(b.match_score) || 0) - (Number(a.match_score) || 0);
    if (sort === "submitted") return String(b.submitted_at || "").localeCompare(String(a.submitted_at || ""));
    if (sort === "decided") return String(b.decided_at || "").localeCompare(String(a.decided_at || ""));
    return String(b.discovered_at || "").localeCompare(String(a.discovered_at || ""));
  });
  return copy;
}

export function filterJobs(jobs, { status = "", q = "", errors = false } = {}) {
  const query = String(q || "").trim().toLowerCase();
  return jobs.filter((job) => {
    if (status && (job.status || "") !== status) return false;
    if (errors && !String(job.error_message || "").trim()) return false;
    if (query && !searchBlob(job).includes(query)) return false;
    return true;
  });
}

export function paginate(jobs, page, pageSize = PAGE_SIZE) {
  const total = jobs.length;
  const totalPages = Math.ceil(total / pageSize);
  if (total === 0) {
    return { page: 1, page_size: pageSize, total: 0, total_pages: 0, jobs: [] };
  }
  const current = Math.min(Math.max(1, Number(page) || 1), totalPages);
  const start = (current - 1) * pageSize;
  return {
    page: current,
    page_size: pageSize,
    total,
    total_pages: totalPages,
    jobs: jobs.slice(start, start + pageSize),
  };
}

export function listRow(job) {
  const row = {};
  for (const [key, value] of Object.entries(job || {})) {
    if (!LIST_OMIT.has(key)) row[key] = value;
  }
  return row;
}

export function pageWindow(page, totalPages, width = PAGE_BUTTONS) {
  if (totalPages < 1) return [];
  const block = Math.floor((Math.max(1, page) - 1) / width);
  const start = block * width + 1;
  const end = Math.min(totalPages, start + width - 1);
  const pages = [];
  for (let i = start; i <= end; i++) pages.push(i);
  return pages;
}

export function queryFromUrl(url) {
  const params = url.searchParams;
  return {
    id: (params.get("id") || "").trim(),
    page: Number(params.get("page") || 1),
    status: params.get("status") || "",
    q: params.get("q") || "",
    errors: ["1", "true", "on"].includes((params.get("errors") || "").toLowerCase()),
    sort: params.get("sort") || "discovered",
  };
}

export function buildListResponse(snapshot, query) {
  const filtered = sortJobs(
    filterJobs(snapshot.jobs || [], query),
    query.sort
  );
  const paged = paginate(filtered, query.page);
  return {
    missing: Boolean(snapshot.missing),
    exported_at: snapshot.exported_at || null,
    daily_cap: snapshot.daily_cap ?? 5,
    submitted_today: snapshot.submitted_today || 0,
    counts: snapshot.counts || {},
    job_count: (snapshot.jobs || []).length,
    page: paged.page,
    page_size: paged.page_size,
    total: paged.total,
    total_pages: paged.total_pages,
    jobs: paged.jobs.map(listRow),
  };
}
