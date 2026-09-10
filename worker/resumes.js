const RESUME_USER = "dev";
const RESUME_PASSWORD = "Sank@9876";
const COOKIE = "yc_resume_auth";
const SESSION_MSG = "resume-ui|dev";
const VARIANTS = ["fullstack", "backend", "frontend"];

function b64url(bytes) {
  let bin = "";
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function b64urlDecode(text) {
  const padded = text + "=".repeat((4 - (text.length % 4)) % 4);
  const bin = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function hmacSign(message, secret) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  return b64url(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message)));
}

function timingEqualBytes(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

function timingEqualStr(a, b) {
  const enc = new TextEncoder();
  return timingEqualBytes(enc.encode(a), enc.encode(b));
}

function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function page(title, body, status = 200, extraHeaders = {}) {
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

function setCookie(token, clear = false) {
  const value = clear ? "" : token;
  const maxAge = clear ? 0 : 60 * 60 * 24 * 14;
  return `${COOKIE}=${value}; Path=/resumes; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}

function redirect(location, headers = {}) {
  return new Response(null, { status: 303, headers: { Location: location, ...headers } });
}

async function isAuthed(request, env) {
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

function loginPage(error = "") {
  const err = error ? `<p class="error">${esc(error)}</p>` : "";
  return page(
    "Resume login",
    `<h1>Resume editor</h1>
     ${err}
     <form method="post" action="/resumes/login">
       <label for="username">Name</label>
       <input id="username" name="username" type="text" autocomplete="username" required/>
       <label for="password">Password</label>
       <input id="password" name="password" type="password" autocomplete="current-password" required/>
       <div class="row"><button type="submit">Log in</button></div>
     </form>`
  );
}

function editorPage(files, notice = "", error = "") {
  const msg = error
    ? `<p class="error">${esc(error)}</p>`
    : notice
      ? `<p class="ok">${esc(notice)}</p>`
      : "";
  const areas = VARIANTS.map((name) => {
    const file = files[name] || { text: "", sha: "" };
    return `<label for="${name}">${name}.txt</label>
      <input type="hidden" name="sha_${name}" value="${esc(file.sha)}"/>
      <textarea id="${name}" name="${name}" spellcheck="false">${esc(file.text)}</textarea>`;
  }).join("\n");
  return page(
    "Resume editor",
    `<h1>Resume editor</h1>
     <p class="muted">Saves commit <code>data/resumes/*.txt</code> on GitHub. The next scan uses this text.</p>
     ${msg}
     <form method="post" action="/resumes">
       ${areas}
       <div class="row">
         <button type="submit">Save</button>
       </div>
     </form>
     <form method="post" action="/resumes/logout" style="margin-top:12px">
       <button type="submit">Log out</button>
     </form>`
  );
}

function ghHeaders(env) {
  return {
    Authorization: `Bearer ${env.GH_PAT_FOR_DISPATCH}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "yc-job-bot",
  };
}

function contentsUrl(env, name) {
  return `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/contents/data/resumes/${name}.txt`;
}

function decodeGithubContent(content) {
  const b64 = (content || "").replace(/\s/g, "");
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

function encodeGithubContent(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function githubError(status, detail) {
  if (status === 401 || status === 403) {
    return `GitHub auth failed (${status}). Check GH_PAT_FOR_DISPATCH repo access. ${detail}`;
  }
  if (status === 409) {
    return `GitHub conflict (409). Reload and save again. ${detail}`;
  }
  return `GitHub error (${status}). ${detail}`;
}

async function loadResumes(env) {
  const files = {};
  for (const name of VARIANTS) {
    const res = await fetch(contentsUrl(env, name), { headers: ghHeaders(env) });
    const raw = await res.text();
    if (!res.ok) {
      throw new Error(githubError(res.status, raw));
    }
    const data = JSON.parse(raw);
    files[name] = { text: decodeGithubContent(data.content), sha: data.sha || "" };
  }
  return files;
}

async function saveResumes(env, form) {
  const errors = [];
  for (const name of VARIANTS) {
    const text = form.get(name);
    const sha = form.get(`sha_${name}`) || "";
    if (text === null) {
      errors.push(`${name}.txt missing from form`);
      continue;
    }
    const res = await fetch(contentsUrl(env, name), {
      method: "PUT",
      headers: { ...ghHeaders(env), "content-type": "application/json" },
      body: JSON.stringify({
        message: "update resumes from worker UI",
        content: encodeGithubContent(text),
        sha,
      }),
    });
    if (res.ok) continue;
    const detail = await res.text();
    if (res.status === 409 && /same/i.test(detail)) continue;
    errors.push(`${name}.txt: ${githubError(res.status, detail)}`);
  }
  return errors;
}

export async function handleResumes(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const authed = await isAuthed(request, env);

  if (path === "/resumes/login" && request.method === "POST") {
    const form = await request.formData();
    const user = String(form.get("username") || "");
    const pass = String(form.get("password") || "");
    const userOk = timingEqualStr(user, RESUME_USER);
    const passOk = timingEqualStr(pass, RESUME_PASSWORD);
    if (!userOk || !passOk || !env.APPROVAL_HMAC_SECRET) {
      return loginPage("Invalid name or password.");
    }
    const token = await hmacSign(SESSION_MSG, env.APPROVAL_HMAC_SECRET);
    return redirect("/resumes", { "Set-Cookie": setCookie(token) });
  }

  if (path === "/resumes/logout" && request.method === "POST") {
    return redirect("/resumes", { "Set-Cookie": setCookie("", true) });
  }

  if (!authed) {
    return loginPage();
  }

  if (path === "/resumes" && request.method === "POST") {
    const form = await request.formData();
    const errors = await saveResumes(env, form);
    if (errors.length) {
      return redirect(`/resumes?error=${encodeURIComponent(errors.join(" "))}`);
    }
    return redirect("/resumes?saved=1");
  }

  if (path !== "/resumes" || request.method !== "GET") {
    return page("Not found", "<h1>Not found</h1>", 404);
  }

  try {
    const files = await loadResumes(env);
    const error = url.searchParams.get("error") || "";
    const notice = url.searchParams.has("saved") ? "Saved. Next scan will use this text." : "";
    return editorPage(files, notice, error);
  } catch (exc) {
    return editorPage(
      Object.fromEntries(VARIANTS.map((name) => [name, { text: "", sha: "" }])),
      "",
      String(exc.message || exc)
    );
  }
}
