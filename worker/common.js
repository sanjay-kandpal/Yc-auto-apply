export const COOKIE = "yc_resume_auth";
export const SESSION_MSG = "resume-ui|dev";

export function b64url(bytes) {
  let bin = "";
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

export function b64urlDecode(text) {
  const padded = text + "=".repeat((4 - (text.length % 4)) % 4);
  const bin = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export async function hmacSign(message, secret) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  return b64url(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message)));
}

export function timingEqualBytes(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

export function timingEqualStr(a, b) {
  const enc = new TextEncoder();
  return timingEqualBytes(enc.encode(a), enc.encode(b));
}

export function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function page(title, body, status = 200, extraHeaders = {}, extraCss = "") {
  return new Response(
    `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>${esc(title)}</title>
  <style>
    body { font-family: sans-serif; margin: 24px; max-width: 960px; }
    label { display: block; margin: 12px 0 4px; font-weight: 600; }
    input[type=text], input[type=password], textarea { width: 100%; box-sizing: border-box; padding: 8px; }
    textarea { min-height: 220px; font-family: ui-monospace, Consolas, monospace; font-size: 13px; }
    .row { display: flex; gap: 12px; margin-top: 16px; align-items: center; }
    button { padding: 8px 16px; }
    .error { color: #a40000; }
    .ok { color: #0a7a0a; }
    .muted { color: #555; font-size: 14px; }
    .nav { margin: 0 0 16px; }
    ${extraCss}
  </style>
</head>
<body>${body}</body>
</html>`,
    { status, headers: { "content-type": "text/html; charset=utf-8", ...extraHeaders } }
  );
}

function cookieValue(request) {
  const header = request.headers.get("Cookie") || "";
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === COOKIE) return rest.join("=");
  }
  return "";
}

export function setCookie(token, clear = false) {
  const value = clear ? "" : token;
  const maxAge = clear ? 0 : 60 * 60 * 24 * 14;
  return `${COOKIE}=${value}; Path=/resumes; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}

export function redirect(location, headers = {}) {
  return new Response(null, { status: 303, headers: { Location: location, ...headers } });
}

export async function isAuthed(request, env) {
  const secret = env.APPROVAL_HMAC_SECRET;
  const token = cookieValue(request);
  if (!secret || !token) return false;
  const expected = await hmacSign(SESSION_MSG, secret);
  try {
    return timingEqualBytes(b64urlDecode(expected), b64urlDecode(token));
  } catch {
    return false;
  }
}

export function safeNext(value) {
  const raw = String(value || "").trim();
  if (!raw.startsWith("/resumes")) return "/resumes";
  if (raw.includes("://") || raw.startsWith("//") || raw.includes("\\")) return "/resumes";
  const path = raw.split("?")[0];
  if (path === "/resumes/login" || path === "/resumes/logout") return "/resumes";
  if (path !== "/resumes" && !path.startsWith("/resumes/")) return "/resumes";
  return raw;
}

export function loginPage(error = "", next = "/resumes") {
  const dest = safeNext(next);
  const err = error ? `<p class="error">${esc(error)}</p>` : "";
  return page(
    "Resume login",
    `<h1>Resume login</h1>
     ${err}
     <form method="post" action="/resumes/login">
       <input type="hidden" name="next" value="${esc(dest)}"/>
       <label for="username">Name</label>
       <input id="username" name="username" type="text" autocomplete="username" required/>
       <label for="password">Password</label>
       <input id="password" name="password" type="password" autocomplete="current-password" required/>
       <div class="row"><button type="submit">Log in</button></div>
     </form>`
  );
}

export function nav(active) {
  const resumes = active === "resumes" ? "<strong>Resumes</strong>" : '<a href="/resumes">Resumes</a>';
  const jobs = active === "jobs" ? "<strong>Jobs</strong>" : '<a href="/resumes/jobs">Jobs</a>';
  return `<p class="nav">${resumes} · ${jobs}</p>`;
}

export function logoutForm() {
  return `<form method="post" action="/resumes/logout" style="margin-top:12px">
    <button type="submit">Log out</button>
  </form>`;
}

export function ghHeaders(env) {
  return {
    Authorization: `Bearer ${env.GH_PAT_FOR_DISPATCH}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "yc-job-bot",
  };
}

export function decodeGithubContent(content) {
  const b64 = (content || "").replace(/\s/g, "");
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

export function githubError(status, detail) {
  if (status === 401 || status === 403) {
    return `GitHub auth failed (${status}). Check GH_PAT_FOR_DISPATCH repo access. ${detail}`;
  }
  if (status === 409) {
    return `GitHub conflict (409). Reload and save again. ${detail}`;
  }
  return `GitHub error (${status}). ${detail}`;
}
