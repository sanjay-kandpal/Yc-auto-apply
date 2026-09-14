import {
  canSendOtp,
  clearOtp,
  hasAuthKv,
  makeResetCookieValue,
  normalizePassword,
  normalizeUsername,
  parsePurpose,
  randomOtp,
  readResetCookie,
  recordOtpSend,
  recoveryEmailOk,
  RESET_COOKIE,
  RESET_TTL_SEC,
  savePassword,
  saveUsername,
  storeOtp,
  verifyOtp,
} from "./auth.js";
import {
  esc,
  ghHeaders,
  githubError,
  page,
  redirect,
  setCookie,
} from "./common.js";

const SENT_NOTICE =
  "If that address is registered, a code will be sent. GitHub Actions can take 30s–2min.";

function titleFor(purpose) {
  return purpose === "name" ? "Forgot name" : "Forgot password";
}

function backLink() {
  return `<p class="muted"><a href="/resumes">Back to login</a></p>`;
}

function emailPage(purpose, error = "", notice = "") {
  const err = error ? `<p class="error">${esc(error)}</p>` : "";
  const ok = notice ? `<p class="ok">${esc(notice)}</p>` : "";
  return page(
    titleFor(purpose),
    `<h1>${esc(titleFor(purpose))}</h1>
     ${err}
     ${ok}
     <p class="muted">Enter the recovery email. A 6-digit code is emailed through GitHub Actions and can take 30s–2min.</p>
     <form method="post" action="/resumes/forgot">
       <input type="hidden" name="purpose" value="${esc(purpose)}"/>
       <label for="email">Email</label>
       <input id="email" name="email" type="email" autocomplete="email" required/>
       <div class="row"><button type="submit">Send code</button></div>
     </form>
     ${backLink()}`
  );
}

function otpPage(purpose, error = "", notice = "") {
  const err = error ? `<p class="error">${esc(error)}</p>` : "";
  const ok = notice ? `<p class="ok">${esc(notice)}</p>` : "";
  return page(
    "Enter code",
    `<h1>Enter the 6-digit code</h1>
     ${err}
     ${ok}
     <p class="muted">Check Gmail for the code. It expires in 10 minutes.</p>
     <form method="post" action="/resumes/otp">
       <input type="hidden" name="purpose" value="${esc(purpose)}"/>
       <label for="code">Code</label>
       <input id="code" name="code" type="text" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" required/>
       <div class="row"><button type="submit">Verify</button></div>
     </form>
     ${backLink()}`
  );
}

function resetPage(purpose, error = "", extraHeaders = {}) {
  const err = error ? `<p class="error">${esc(error)}</p>` : "";
  const fields =
    purpose === "name"
      ? `<label for="username">New name</label>
         <input id="username" name="username" type="text" autocomplete="username" maxlength="64" required/>`
      : `<label for="password">New password</label>
         <input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required/>
         <label for="confirm">Confirm password</label>
         <input id="confirm" name="confirm" type="password" autocomplete="new-password" minlength="8" required/>`;
  return page(
    purpose === "name" ? "Set new name" : "Set new password",
    `<h1>${purpose === "name" ? "Set a new name" : "Set a new password"}</h1>
     ${err}
     <form method="post" action="/resumes/reset">
       <input type="hidden" name="purpose" value="${esc(purpose)}"/>
       ${fields}
       <div class="row"><button type="submit">Save</button></div>
     </form>
     ${backLink()}`,
    200,
    extraHeaders
  );
}

function otpErrorMessage(reason) {
  if (reason === "locked") return "Too many incorrect codes. Request a new one.";
  if (reason === "expired") return "That code is invalid or expired. Request a new one.";
  return "That code is incorrect.";
}

async function dispatchOtp(env, purpose, otp) {
  const res = await fetch(
    `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/dispatches`,
    {
      method: "POST",
      headers: { ...ghHeaders(env), "content-type": "application/json" },
      body: JSON.stringify({
        event_type: "resume_otp",
        client_payload: { purpose, otp },
      }),
    }
  );
  if (res.ok) return "";
  return githubError(res.status, await res.text());
}

async function handleForgotPost(request, env) {
  const form = await request.formData();
  const purpose = parsePurpose(form.get("purpose"));
  if (!purpose) return emailPage("password", "Unknown reset type.");
  if (!recoveryEmailOk(env, form.get("email"))) {
    return otpPage(purpose, "", SENT_NOTICE);
  }
  if (!hasAuthKv(env) || !env.APPROVAL_HMAC_SECRET) {
    return emailPage(purpose, "Auth storage is not configured (RESUME_AUTH KV / secrets).");
  }
  const limit = await canSendOtp(env);
  if (!limit.ok) return otpPage(purpose, "", SENT_NOTICE);
  const otp = randomOtp();
  await storeOtp(env, purpose, otp, env.APPROVAL_HMAC_SECRET);
  const dispatchError = await dispatchOtp(env, purpose, otp);
  if (dispatchError) {
    await clearOtp(env);
    return emailPage(purpose, `Could not send code. ${dispatchError}`);
  }
  await recordOtpSend(env, limit.times);
  return otpPage(purpose, "", SENT_NOTICE);
}

async function handleOtpPost(request, env) {
  const form = await request.formData();
  const purpose = parsePurpose(form.get("purpose"));
  const code = String(form.get("code") || "").trim();
  if (!purpose) return emailPage("password", "Unknown reset type.");
  if (!hasAuthKv(env) || !env.APPROVAL_HMAC_SECRET) {
    return otpPage(purpose, "Auth storage is not configured (RESUME_AUTH KV / secrets).");
  }
  if (!/^\d{6}$/.test(code)) {
    return otpPage(purpose, "Enter the 6-digit code.");
  }
  const result = await verifyOtp(env, purpose, code, env.APPROVAL_HMAC_SECRET);
  if (!result.ok) {
    return otpPage(purpose, otpErrorMessage(result.reason));
  }
  const token = await makeResetCookieValue(purpose, env.APPROVAL_HMAC_SECRET);
  return resetPage(purpose, "", {
    "Set-Cookie": setCookie(token, false, { name: RESET_COOKIE, maxAge: RESET_TTL_SEC }),
  });
}

async function handleResetPost(request, env) {
  const purpose = await readResetCookie(request, env);
  const form = await request.formData();
  const formPurpose = parsePurpose(form.get("purpose"));
  if (!purpose || purpose !== formPurpose) {
    return emailPage(formPurpose || "password", "Reset session expired. Request a new code.");
  }
  if (!hasAuthKv(env) || !env.APPROVAL_HMAC_SECRET) {
    return resetPage(purpose, "Auth storage is not configured (RESUME_AUTH KV / secrets).");
  }
  if (purpose === "name") {
    const username = normalizeUsername(form.get("username"));
    if (!username) {
      return resetPage(purpose, "Name must be 1–64 characters with no line breaks.");
    }
    await saveUsername(env, username);
  } else {
    const password = normalizePassword(form.get("password"));
    const confirm = String(form.get("confirm") || "");
    if (!password) {
      return resetPage(purpose, "Password must be 8–128 characters with no line breaks.");
    }
    if (password !== confirm) {
      return resetPage(purpose, "Passwords do not match.");
    }
    await savePassword(env, password);
  }
  const headers = new Headers();
  headers.append("Set-Cookie", setCookie("", true, { name: RESET_COOKIE }));
  return redirect(`/resumes?updated=${purpose}`, headers);
}

export async function handleForgot(request, env) {
  const path = new URL(request.url).pathname.replace(/\/+$/, "") || "/";
  if (path === "/resumes/forgot-password" && request.method === "GET") {
    return emailPage("password");
  }
  if (path === "/resumes/forgot-name" && request.method === "GET") {
    return emailPage("name");
  }
  if (path === "/resumes/forgot" && request.method === "POST") {
    return handleForgotPost(request, env);
  }
  if (path === "/resumes/otp" && request.method === "POST") {
    return handleOtpPost(request, env);
  }
  if (path === "/resumes/reset" && request.method === "GET") {
    const purpose = await readResetCookie(request, env);
    if (!purpose) return redirect("/resumes");
    return resetPage(purpose);
  }
  if (path === "/resumes/reset" && request.method === "POST") {
    return handleResetPost(request, env);
  }
  return page("Not found", "<h1>Not found</h1>", 404);
}
