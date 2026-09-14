import {
  b64url,
  b64urlDecode,
  COOKIE,
  cookieNamed,
  hmacSign,
  timingEqualBytes,
  timingEqualStr,
} from "./common.js";

export const SEED_USER = "dev";
export const SEED_PASSWORD = "Sank@9876";
export const RESET_COOKIE = "yc_resume_reset";
export const OTP_TTL_SEC = 600;
export const RESET_TTL_SEC = 600;
export const OTP_MAX_SENDS = 3;
export const OTP_SEND_WINDOW_SEC = 15 * 60;
export const OTP_MAX_TRIES = 5;

const KEY_USER = "username";
const KEY_PASS = "password_hmac";
const KEY_NONCE = "session_nonce";
const KEY_OTP = "otp";
const KEY_SENDS = "otp_sends";

export function hasAuthKv(env) {
  return Boolean(env.RESUME_AUTH);
}

export function parsePurpose(value) {
  const purpose = String(value || "").trim();
  return purpose === "password" || purpose === "name" ? purpose : "";
}

async function kvGet(env, key) {
  if (!env.RESUME_AUTH) return null;
  return env.RESUME_AUTH.get(key);
}

async function kvPut(env, key, value, options) {
  if (!env.RESUME_AUTH) throw new Error("RESUME_AUTH KV is not bound");
  return env.RESUME_AUTH.put(key, value, options);
}

async function kvDelete(env, key) {
  if (!env.RESUME_AUTH) return;
  return env.RESUME_AUTH.delete(key);
}

export async function getUsername(env) {
  return (await kvGet(env, KEY_USER)) || SEED_USER;
}

async function getPasswordHmac(env) {
  const stored = await kvGet(env, KEY_PASS);
  if (stored) return stored;
  return hmacSign(SEED_PASSWORD, env.APPROVAL_HMAC_SECRET);
}

async function getNonce(env) {
  return (await kvGet(env, KEY_NONCE)) || "0";
}

function sessionMessage(username, nonce) {
  return `resume-ui|${username}|${nonce}`;
}

function randomId() {
  return b64url(crypto.getRandomValues(new Uint8Array(16)));
}

export async function sessionToken(env) {
  const username = await getUsername(env);
  const nonce = await getNonce(env);
  return hmacSign(sessionMessage(username, nonce), env.APPROVAL_HMAC_SECRET);
}

export async function isAuthed(request, env) {
  const secret = env.APPROVAL_HMAC_SECRET;
  const token = cookieNamed(request, COOKIE);
  if (!secret || !token) return false;
  const expected = await sessionToken(env);
  try {
    return timingEqualBytes(b64urlDecode(expected), b64urlDecode(token));
  } catch {
    return false;
  }
}

export async function credentialsMatch(env, user, pass) {
  const secret = env.APPROVAL_HMAC_SECRET;
  if (!secret) return false;
  const username = await getUsername(env);
  const expectedHmac = await getPasswordHmac(env);
  const gotHmac = await hmacSign(String(pass || ""), secret);
  const userOk = timingEqualStr(String(user || ""), username);
  const passOk = timingEqualStr(gotHmac, expectedHmac);
  return userOk && passOk;
}

export function recoveryEmailOk(env, typed) {
  const expected = String(env.RECOVERY_EMAIL || "").trim().toLowerCase();
  const got = String(typed || "").trim().toLowerCase();
  if (!expected || !got) return false;
  return timingEqualStr(got, expected);
}

export function randomOtp() {
  const digits = crypto.getRandomValues(new Uint8Array(6));
  let out = "";
  for (let i = 0; i < digits.length; i++) out += String(digits[i] % 10);
  return out;
}

export async function canSendOtp(env) {
  const now = Math.floor(Date.now() / 1000);
  const raw = await kvGet(env, KEY_SENDS);
  let times = [];
  try {
    times = raw ? JSON.parse(raw) : [];
  } catch {
    times = [];
  }
  if (!Array.isArray(times)) times = [];
  times = times.filter((t) => Number.isFinite(t) && now - t < OTP_SEND_WINDOW_SEC);
  return { ok: times.length < OTP_MAX_SENDS, times };
}

export async function recordOtpSend(env, times) {
  const now = Math.floor(Date.now() / 1000);
  const next = [...times, now];
  await kvPut(env, KEY_SENDS, JSON.stringify(next), { expirationTtl: OTP_SEND_WINDOW_SEC });
}

export async function storeOtp(env, purpose, otp, secret) {
  const hash = await hmacSign(`${purpose}|${otp}`, secret);
  const payload = JSON.stringify({ purpose, hash, tries: 0 });
  await kvPut(env, KEY_OTP, payload, { expirationTtl: OTP_TTL_SEC });
}

export async function clearOtp(env) {
  await kvDelete(env, KEY_OTP);
}

export async function verifyOtp(env, purpose, otp, secret) {
  const raw = await kvGet(env, KEY_OTP);
  if (!raw) return { ok: false, reason: "expired" };
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    await kvDelete(env, KEY_OTP);
    return { ok: false, reason: "expired" };
  }
  if (data.purpose !== purpose || !data.hash) {
    return { ok: false, reason: "expired" };
  }
  const tries = Number(data.tries) || 0;
  if (tries >= OTP_MAX_TRIES) {
    await kvDelete(env, KEY_OTP);
    return { ok: false, reason: "locked" };
  }
  const expected = await hmacSign(`${purpose}|${String(otp || "")}`, secret);
  if (!timingEqualStr(expected, data.hash)) {
    const nextTries = tries + 1;
    if (nextTries >= OTP_MAX_TRIES) {
      await kvDelete(env, KEY_OTP);
      return { ok: false, reason: "locked" };
    }
    await kvPut(env, KEY_OTP, JSON.stringify({ ...data, tries: nextTries }), {
      expirationTtl: OTP_TTL_SEC,
    });
    return { ok: false, reason: "mismatch" };
  }
  await kvDelete(env, KEY_OTP);
  return { ok: true };
}

export async function makeResetCookieValue(purpose, secret) {
  const exp = Math.floor(Date.now() / 1000) + RESET_TTL_SEC;
  const token = await hmacSign(`reset|${purpose}|${exp}`, secret);
  return `${purpose}.${exp}.${token}`;
}

export async function readResetCookie(request, env) {
  const secret = env.APPROVAL_HMAC_SECRET;
  const raw = cookieNamed(request, RESET_COOKIE);
  if (!secret || !raw) return "";
  const parts = raw.split(".");
  if (parts.length !== 3) return "";
  const [purpose, expStr, token] = parts;
  if (!parsePurpose(purpose) || !token) return "";
  const exp = Number(expStr);
  if (!Number.isFinite(exp) || exp < Math.floor(Date.now() / 1000)) return "";
  const expected = await hmacSign(`reset|${purpose}|${exp}`, secret);
  try {
    if (!timingEqualBytes(b64urlDecode(expected), b64urlDecode(token))) return "";
  } catch {
    return "";
  }
  return purpose;
}

export async function saveUsername(env, username) {
  await kvPut(env, KEY_USER, username);
  await kvPut(env, KEY_NONCE, randomId());
}

export async function savePassword(env, password) {
  const hmac = await hmacSign(password, env.APPROVAL_HMAC_SECRET);
  await kvPut(env, KEY_PASS, hmac);
  await kvPut(env, KEY_NONCE, randomId());
}

export function normalizeUsername(value) {
  const name = String(value || "").trim();
  if (!name || name.length > 64 || /[\r\n]/.test(name)) return "";
  return name;
}

export function normalizePassword(value) {
  const password = String(value || "");
  if (password.length < 8 || password.length > 128 || /[\r\n]/.test(password)) return "";
  return password;
}
