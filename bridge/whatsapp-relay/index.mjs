// Facai WhatsApp relay bridge — cloned from the AutoClaw (zwork) embedded
// WhatsApp stack: @whiskeysockets/baileys 6.7.24 driving a personal WhatsApp
// account. UNOFFICIAL integration; the operator runs it at their own risk.
// Facai Cloud never speaks the WhatsApp protocol itself — this bridge does:
//
//   inbound:  WhatsApp text -> POST /api/v1/channels/relay/webhook (HMAC-signed)
//   outbound: poll POST /api/v1/channels/relay/send -> deliver -> ack ids
//
// 24/7 posture: auth state persisted on disk (scan once), automatic
// reconnect with backoff on any drop, outbox polling survives network
// errors, and the process is meant to run under systemd Restart=always.

import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import {
  default as makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
} from "@whiskeysockets/baileys";
import pino from "pino";
import qrcode from "qrcode-terminal";

const HERE = dirname(fileURLToPath(import.meta.url));

// --- configuration (env only; the shared secret never touches disk) -------
const FACAI_BASE = (process.env.FACAI_BASE_URL || "https://whstapp.lol").replace(/\/+$/, "");
const SECRET = process.env.RELAY_SHARED_SECRET || "";
const AUTH_DIR = process.env.WA_AUTH_DIR || join(HERE, "wa-auth");
const POLL_MS = Math.max(2000, parseInt(process.env.WA_POLL_MS || "5000", 10));
const PAIR_PHONE = (process.env.WA_PAIR_PHONE || "").replace(/\D/g, ""); // optional: pairing-code login
const LOG = pino({ level: process.env.WA_LOG_LEVEL || "info" });
// Outbox poll backoff: exponential from 3x POLL_MS, capped (see pollLoop).
const POLL_BACKOFF_CAP_MS = Math.max(
  POLL_MS * 3,
  parseInt(process.env.WA_POLL_BACKOFF_MAX_MS || "60000", 10),
);

// --- pairing-code host admin API (per-owner hosted sessions) ----------------
// FACAI_WHATSAPP_BRIDGE_URL (Facai side) points here. The listener speaks the
// SAME HMAC scheme the relay channel uses (sha256 over "<ts>.<body>" with the
// shared RELAY_SHARED_SECRET) — no second secret exists. Default bind is the
// loopback interface; a remote Facai needs WA_HOST_BIND=0.0.0.0 behind a
// firewall/ACL limited to the Facai host (see README).
const HOST_PORT = parseInt(process.env.WA_HOST_PORT || "8790", 10);
const HOST_BIND = process.env.WA_HOST_BIND || "127.0.0.1";
const HOST_ENABLED = HOST_PORT > 0;
const HOST_DIR = join(AUTH_DIR, "hosting");
const HOST_MAX_SKEW_SECONDS = 300;
// How long POST /host/pair waits for Baileys to mint the code before answering
// PAIRING_WAIT (the customer then polls; the code arrives via /host/session).
const PAIR_WAIT_MS = Math.max(2000, parseInt(process.env.WA_HOST_PAIR_WAIT_MS || "10000", 10));
// WhatsApp pairing codes are single-use and short-lived; a code older than
// this is reported as absent so the customer re-requests instead of typing a
// dead code.
const CODE_TTL_MS = Math.max(10000, parseInt(process.env.WA_HOST_CODE_TTL_MS || "90000", 10));
// owner ids are Facai usernames: the charset Facai's auth enforces, which is
// also path-safe under HOST_DIR (no separators, no "..").
const OWNER_RE = /^[A-Za-z0-9_-]{3,32}$/;
const PHONE_RE = /^[0-9]{7,15}$/; // E.164 digits without the leading "+"

// --- Baileys internal logger ------------------------------------------------
// Baileys logs first-login query/presence timeouts ("Timed Out" nested inside
// "unexpected error in 'init queries'", plus fetchProps as part of that same
// init-query batch) at ERROR level. They are internally retried and are NOT
// connection failures: real drops/status codes arrive via `connection.update`
// below and are still logged and reconnected. We hand Baileys a logger that
// downgrades only these known-benign phrases to DEBUG (invisible at the default
// level, still available with WA_LOG_LEVEL=debug) and forwards every other
// line at its original level, so genuine errors are never masked.
const BAILEYS_NOISE = [
  /unexpected error in 'init queries'/i,
  /unexpected error in 'presence update requests'/i,
];

function makeBaileysLogger(level) {
  const base = pino({ level });
  const isNoise = (obj, msg) => {
    const text = typeof obj === "string" ? obj : String(msg ?? "");
    const err = obj && typeof obj === "object" ? obj.err : undefined;
    const errText = err ? String(err.message || err) : "";
    return BAILEYS_NOISE.some((re) => re.test(text) || re.test(errText));
  };
  const wrap = (inst) => {
    const forward = (method) => (obj, ...rest) =>
      isNoise(obj, rest[0])
        ? inst.debug(obj, ...rest) // downgraded, not dropped
        : inst[method](obj, ...rest);
    return {
      get level() { return inst.level; },   // Baileys reads logger.level
      set level(v) { inst.level = v; },
      trace: forward("trace"),
      debug: forward("debug"),
      info: forward("info"),
      warn: forward("warn"),
      error: forward("error"),
      fatal: forward("fatal"),
      isLevelEnabled: (l) => inst.isLevelEnabled(l),
      child: (bindings) => wrap(inst.child(bindings)),
    };
  };
  return wrap(base);
}

// Operator can silence Baileys harder (or verbosely) without touching bridge logs.
const BAILEYS_LOG = makeBaileysLogger(
  process.env.WA_BAILEYS_LOG_LEVEL || process.env.WA_LOG_LEVEL || "info",
);

if (!SECRET) {
  LOG.fatal("RELAY_SHARED_SECRET is not set — refusing to run (would relay nothing).");
  process.exit(1);
}
mkdirSync(AUTH_DIR, { recursive: true });

// --- relay HMAC scheme: sha256 hex over "<timestamp>.<body>" ---------------
function signedHeaders(body) {
  const ts = Math.floor(Date.now() / 1000).toString();
  const digest = createHmac("sha256", SECRET).update(ts + "." + body).digest("hex");
  return {
    "Content-Type": "application/json",
    "X-Facai-Signature": "sha256=" + digest,
    "X-Facai-Timestamp": ts,
  };
}

async function facaiPost(path, payload) {
  const body = JSON.stringify(payload ?? {});
  const res = await fetch(FACAI_BASE + path, {
    method: "POST",
    headers: signedHeaders(body),
    body,
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* non-JSON error page */ }
  if (!res.ok) {
    throw new Error(`facai ${path} -> HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  return json;
}

// --- WhatsApp socket --------------------------------------------------------
let sock = null;
let online = false;
let sendingQueue = Promise.resolve();

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  let version;
  try { ({ version } = await fetchLatestBaileysVersion()); }
  catch (err) { LOG.warn({ err: String(err) }, "version fetch failed; using baked default"); }

  sock = makeWASocket({
    version,
    logger: BAILEYS_LOG, // filtered: init-query noise downgraded, real errors passed through
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, BAILEYS_LOG),
    },
    syncFullHistory: false,
    markOnlineOnConnect: true,
    browser: ["Facai Cloud", "Chrome", "1.0.0"], // desktop linkage -> device-linked session
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      LOG.warn("Scan this QR with WhatsApp (设置 -> 已链接的设备 -> 链接设备):");
      qrcode.generate(qr, { small: true });
      // The raw QR payload is a live link credential: it must never sit in a
      // world-readable file (10-agent audit, 2026-10-04). Terminal only.
      if (PAIR_PHONE && !existsSync(join(AUTH_DIR, "creds.json"))) {
        try {
          const code = await sock.requestPairingCode(PAIR_PHONE);
          LOG.warn(`Pairing code for +${PAIR_PHONE}: ${code}`);
        } catch (err) { LOG.error({ err: String(err) }, "pairing code failed"); }
      }
    }

    if (connection === "open") {
      online = true;
      LOG.info(`WhatsApp linked as ${sock.user?.id} — relay loop active (${FACAI_BASE})`);
    }

    if (connection === "close") {
      online = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;
      LOG.warn({ code }, loggedOut ? "logged out — re-scan required" : "connection closed; reconnecting");
      if (loggedOut) {
        // Keep creds so the operator can inspect them; Baileys will demand a
        // fresh QR on next start. Just restart the socket loop.
      }
      setTimeout(startSocket, loggedOut ? 15000 : 3000); // 24/7: always come back
    }
  });

  sock.ev.on("messages.upsert", async (upsert) => {
    // Whole handler is guarded so a malformed payload can never surface as an
    // unhandled rejection; each message is additionally isolated below so one
    // bad message cannot stop the rest of the batch.
    try {
      const { messages, type } = upsert || {};
      if (type !== "notify") return; // only live incoming, no history replay
      if (!Array.isArray(messages)) return;
      for (const msg of messages) {
        try {
          if (!msg || msg.key?.fromMe) continue;
          const jid = msg.key?.remoteJid;
          if (!jid || jid.endsWith("@broadcast") || jid.endsWith("@newsletter")) continue;
          const text = extractText(msg.message);
          if (!text || !text.trim()) continue; // media/stickers are not executables
          const body = JSON.stringify({
            external_id: msg.key.id,
            sender: jid,
            text,
            kind: "text",
            raw: { timestamp: msg.messageTimestamp },
          });
          await facaiPost("/api/v1/channels/relay/webhook", JSON.parse(body));
          LOG.info({ from: jid, id: msg.key.id, chars: text.length }, "inbound forwarded");
        } catch (err) {
          // Never let one bad message kill the bridge; Facai dedupes by
          // external_id so a later retry of the same id cannot double-create.
          LOG.error({ err: String(err), id: msg?.key?.id }, "inbound forward failed");
        }
      }
    } catch (err) {
      // e.g. decode/iteration failure before the per-message loop.
      LOG.error({ err: String(err) }, "messages.upsert handler failed; batch skipped");
    }
  });
}

// --- hosted pairing sessions (pairing-code host, per-owner) -----------------
// Each customer (owner) gets their OWN Baileys session under
// wa-auth/hosting/<owner>/ — the same per-owner isolation Facai keeps for its
// hosted credentials. Hosted sessions exist to COMPLETE PAIRING (Baileys
// requestPairingCode) and to report link status to Facai over the signed
// admin API below. Message relay for hosted sessions is deliberately NOT
// wired in this wave: their inbound messages are dropped with an honest log,
// so customer traffic can never leak into the station's relay channel until
// per-owner routing exists.

const hostSessions = new Map(); // owner -> session

function hostSession(owner) {
  let session = hostSessions.get(owner);
  if (!session) {
    session = {
      owner,
      dir: join(HOST_DIR, owner),
      linked: false,
      phone: null,        // linked account's phone, from the JID on open
      pendingPhone: null, // the phone a pair request asked to link
      code: null,         // last minted pairing code (single-use, short-lived)
      codeAt: 0,
      sock: null,
      sockGen: 0,         // incremented per socket; stale sockets never act
      stopping: false,
      waiters: [],        // pair-request resolvers woken on code or open
    };
    hostSessions.set(owner, session);
  }
  return session;
}

function resolveWaiters(session) {
  for (const wake of session.waiters.splice(0)) wake();
}

function sessionPayload(session) {
  if (!session) return { state: "NONE" };
  if (session.linked) return { state: "LINKED", phone: session.phone };
  const codeFresh = Boolean(session.code) && Date.now() - session.codeAt < CODE_TTL_MS;
  return { state: "PAIRING", code: codeFresh ? session.code : null,
           phone: session.pendingPhone };
}

async function startHostSocket(session) {
  const gen = ++session.sockGen;
  session.stopping = false;
  const { state, saveCreds } = await useMultiFileAuthState(session.dir);
  let version;
  try { ({ version } = await fetchLatestBaileysVersion()); }
  catch (err) { LOG.warn({ owner: session.owner, err: String(err) }, "hosted session version fetch failed; using baked default"); }

  const sock = makeWASocket({
    version,
    logger: BAILEYS_LOG,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, BAILEYS_LOG),
    },
    syncFullHistory: false,
    markOnlineOnConnect: true,
    browser: ["Facai Cloud", "Chrome", "1.0.0"], // desktop linkage -> device-linked session
  });
  session.sock = sock;
  session.linked = false;

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    if (gen !== session.sockGen) return; // this socket was replaced; hands off
    const { connection, lastDisconnect, qr } = update;

    if (qr && session.pendingPhone && !existsSync(join(session.dir, "creds.json"))) {
      try {
        const code = await sock.requestPairingCode(session.pendingPhone);
        session.code = String(code || "").trim();
        session.codeAt = Date.now();
        // The code belongs to the CUSTOMER and is delivered through Facai's
        // API; it is never logged here (same discipline as the QR payload).
        LOG.warn({ owner: session.owner, chars: session.code.length },
                 "hosted pairing code minted");
        resolveWaiters(session);
      } catch (err) {
        LOG.error({ owner: session.owner, err: String(err) }, "hosted pairing code failed");
      }
    }

    if (connection === "open") {
      session.linked = true;
      session.phone = String(sock.user?.id || "").split("@")[0] || null;
      session.code = null;
      session.pendingPhone = null;
      LOG.info({ owner: session.owner, phone: session.phone }, "hosted WhatsApp session linked");
      resolveWaiters(session);
    }

    if (connection === "close") {
      if (gen !== session.sockGen || session.stopping) return;
      session.linked = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;
      session.sock = null;
      LOG.warn({ owner: session.owner, code },
               loggedOut ? "hosted session logged out; fresh pairing required"
                         : "hosted session closed; reconnecting");
      // 24/7 for the customer too: always come back (a logged-out session
      // restarts and waits for a fresh pair request, since its creds are dead).
      setTimeout(() => {
        if (!session.stopping && gen === session.sockGen && !session.sock) {
          startHostSocket(session).catch((err) =>
            LOG.error({ owner: session.owner, err: String(err) }, "hosted session restart failed"));
        }
      }, loggedOut ? 15000 : 3000);
    }
  });

  sock.ev.on("messages.upsert", (upsert) => {
    // Fail-closed by design: hosted message routing is a later wave, and a
    // customer's chats must never surface in the station's relay channel.
    const dropped = Array.isArray(upsert?.messages) ? upsert.messages.length : 0;
    if (dropped) LOG.debug({ owner: session.owner, dropped },
                           "hosted message relay not wired; dropped");
  });
}

async function hostPair(owner, phone) {
  const session = hostSession(owner);
  if (session.linked) return { state: "LINKED", phone: session.phone };

  if (existsSync(join(session.dir, "creds.json"))) {
    // A stored (registered) session reconnects to the number it was created
    // with; minting a code for a DIFFERENT number would be a lie. Report the
    // relink and let Facai compare the number once the session opens.
    if (!session.sock) {
      await startHostSocket(session).catch((err) =>
        LOG.error({ owner, err: String(err) }, "hosted session restart failed"));
    }
    return { state: "RELINKING", phone: session.phone };
  }

  // Fresh pairing: every pair request (re)starts the socket so a stale or
  // expired code can never be answered twice — each request mints fresh.
  session.pendingPhone = phone;
  session.code = null;
  session.codeAt = 0;
  const stale = session.sock;
  session.sock = null;
  session.sockGen += 1; // retire the old socket's handlers
  if (stale) { try { stale.end(); } catch { /* already dead */ } }
  await startHostSocket(session);

  const gotSomething = await new Promise((resolve) => {
    const timer = setTimeout(() => done(false), PAIR_WAIT_MS);
    function done(value) { clearTimeout(timer); resolve(value); }
    session.waiters.push(() => done(true));
  });
  void gotSomething;
  if (session.linked) return { state: "LINKED", phone: session.phone };
  if (session.code) return { state: "PAIRING_CODE", code: session.code, phone };
  return { state: "PAIRING_WAIT", phone };
}

async function hostLogout(owner) {
  const session = hostSessions.get(owner);
  if (!session) {
    // No live session object (e.g. this bridge restarted without recovery
    // material): still wipe any leftover auth dir so a stale session can
    // never silently relink the customer's number.
    rmSync(join(HOST_DIR, owner), { recursive: true, force: true });
    return { state: "NONE" };
  }
  session.stopping = true;
  session.pendingPhone = null;
  session.code = null;
  session.codeAt = 0;
  const sock = session.sock;
  if (sock) {
    try {
      await sock.logout(); // server-side unpair: the device leaves the customer's WhatsApp
    } catch (err) {
      LOG.warn({ owner, err: String(err) }, "hosted logout failed; ending socket locally");
      try { sock.end(); } catch { /* already dead */ }
    }
  }
  hostSessions.delete(owner);
  // This IS the hosted-channel revoke: a leftover auth dir would let the
  // bridge silently relink the customer's number later, so the per-owner
  // Baileys credentials are wiped on logout.
  rmSync(session.dir, { recursive: true, force: true });
  LOG.info({ owner }, "hosted session logged out; credentials wiped");
  return { state: "LOGGED_OUT" };
}

function recoverHostSessions() {
  // Bridge restarts must not silently drop customer links: every stored
  // (registered) hosted session comes back online from its own auth dir.
  if (!HOST_ENABLED) return;
  let entries;
  try { entries = readdirSync(HOST_DIR, { withFileTypes: true }); }
  catch { return; } // no hosting dir yet — nothing to recover
  for (const entry of entries) {
    if (!entry.isDirectory() || !OWNER_RE.test(entry.name)) continue;
    if (!existsSync(join(HOST_DIR, entry.name, "creds.json"))) continue;
    const session = hostSession(entry.name);
    startHostSocket(session).catch((err) =>
      LOG.error({ owner: entry.name, err: String(err) }, "hosted session recovery failed"));
  }
}

// --- hosted admin API: signed with the relay shared secret ------------------
// Same scheme as Facai's relay endpoints: X-Facai-Signature: sha256=<hex> over
// "<timestamp>.<body>" + X-Facai-Timestamp inside a bounded skew window.

function verifyHostSignature(req, rawBody) {
  const sig = String(req.headers["x-facai-signature"] || "");
  const ts = String(req.headers["x-facai-timestamp"] || "");
  const stamped = parseInt(ts, 10);
  if (!sig || !Number.isFinite(stamped)) return false;
  if (Math.abs(Date.now() / 1000 - stamped) > HOST_MAX_SKEW_SECONDS) return false;
  const digest = createHmac("sha256", SECRET).update(ts + "." + rawBody).digest("hex");
  const supplied = sig.startsWith("sha256=") ? sig.slice(7) : "";
  if (supplied.length !== digest.length) return false;
  try { return timingSafeEqual(Buffer.from(supplied), Buffer.from(digest)); }
  catch { return false; }
}

function sendJson(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
}

function parseJsonBody(raw) {
  if (!raw) return {};
  try { return JSON.parse(raw); } catch { return null; }
}

const hostServer = createServer((req, res) => {
  const chunks = [];
  let size = 0;
  req.on("data", (chunk) => {
    size += chunk.length;
    if (size <= 65536) chunks.push(chunk);
  });
  req.on("end", async () => {
    try {
      if (size > 65536) return sendJson(res, 413, { error: "body too large" });
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!verifyHostSignature(req, raw)) return sendJson(res, 403, { error: "bad signature" });
      const url = new URL(req.url || "/", "http://localhost");

      if (req.method === "POST" && url.pathname === "/host/pair") {
        const body = parseJsonBody(raw);
        if (body === null) return sendJson(res, 400, { error: "body must be valid JSON" });
        const owner = typeof body.owner === "string" ? body.owner : "";
        const phone = typeof body.phone === "string" ? body.phone.replace(/\D/g, "") : "";
        if (!OWNER_RE.test(owner) || !PHONE_RE.test(phone)) {
          return sendJson(res, 400, { error: "invalid owner or phone" });
        }
        return sendJson(res, 200, await hostPair(owner, phone));
      }

      if (req.method === "POST" && url.pathname === "/host/logout") {
        const body = parseJsonBody(raw);
        if (body === null) return sendJson(res, 400, { error: "body must be valid JSON" });
        const owner = typeof body.owner === "string" ? body.owner : "";
        if (!OWNER_RE.test(owner)) return sendJson(res, 400, { error: "invalid owner" });
        return sendJson(res, 200, await hostLogout(owner));
      }

      if (req.method === "GET" && url.pathname === "/host/session") {
        const owner = url.searchParams.get("owner") || "";
        if (!OWNER_RE.test(owner)) return sendJson(res, 400, { error: "invalid owner" });
        const session = hostSessions.get(owner);
        return sendJson(res, 200, sessionPayload(session));
      }

      return sendJson(res, 404, { error: "not found" });
    } catch (err) {
      LOG.error({ err: String(err) }, "host admin request failed");
      return sendJson(res, 500, { error: "internal error" });
    }
  });
});

if (HOST_ENABLED) {
  hostServer.on("error", (err) => {
    // A dead admin listener must not take the whole relay bridge down (the
    // station link keeps running); the failure is loud in the journal.
    LOG.error({ err: String(err) }, "host admin api failed");
  });
  hostServer.listen(HOST_PORT, HOST_BIND, () => {
    LOG.info(`host admin api listening on ${HOST_BIND}:${HOST_PORT}`);
  });
} else {
  LOG.info("host admin api disabled (WA_HOST_PORT=0); pairing-code host unavailable");
}

function extractText(message) {
  if (!message) return "";
  return message.conversation
    || message.extendedTextMessage?.text
    || message.imageMessage?.caption
    || message.videoMessage?.caption
    || message.listResponseMessage?.title
    || "";
}

// --- outbound: poll the durable outbox, deliver, ack ------------------------
const pendingAcks = new Set(); // ids delivered to WhatsApp, not yet acked by Facai

async function pollOnce() {
  const body = JSON.stringify({ ack_ids: [...pendingAcks] });
  const res = await facaiPost("/api/v1/channels/relay/send", JSON.parse(body));
  for (const id of res.acked || []) pendingAcks.delete(id);

  for (const item of res.messages || []) {
    await sendingQueue.then(() => deliver(item));
  }
}

async function deliver(item) {
  const jid = toJid(item.recipient || item.sender);
  if (!jid) {
    LOG.warn({ id: item.id }, "outbox item without a routable recipient — acking to drop it");
    pendingAcks.add(item.id);
    return;
  }
  if (!online || !sock) {
    LOG.warn({ id: item.id }, "socket offline; will retry after reconnect (kept QUEUED upstream)");
    return; // not acked: Facai keeps it QUEUED (at-least-once)
  }
  try {
    await sock.sendMessage(jid, { text: item.text });
    pendingAcks.add(item.id);
    LOG.info({ id: item.id, to: jid, chars: (item.text || "").length }, "outbound delivered");
  } catch (err) {
    LOG.error({ err: String(err), id: item.id }, "send failed; left QUEUED for retry");
  }
}

function toJid(recipient) {
  const raw = (recipient || "").trim();
  if (!raw) return "";
  if (raw.includes("@")) return raw;             // already a JID (e.g. from sender)
  const digits = raw.replace(/\D/g, "");
  return digits ? digits + "@s.whatsapp.net" : "";
}

async function pollLoop() {
  let failures = 0; // consecutive failed poll cycles (reset on success)
  for (;;) {
    try {
      await pollOnce();
      if (failures) LOG.info({ after: failures }, "poll recovered");
      failures = 0;
    } catch (err) {
      failures += 1;
      // Exponential backoff from 3x POLL_MS, capped: avoids spinning while the
      // upstream/socket is offline for a long stretch, but still recovers fast
      // from a one-off blip.
      const delayMs = Math.min(POLL_MS * 3 * 2 ** (failures - 1), POLL_BACKOFF_CAP_MS);
      LOG.warn(
        { err: String(err).slice(0, 200), failures, retry_in_ms: delayMs },
        "poll cycle failed; backing off",
      );
      await new Promise((r) => setTimeout(r, delayMs));
      continue;
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

// --- process supervision -----------------------------------------------------
process.on("unhandledRejection", (err) => LOG.error({ err: String(err) }, "unhandled rejection"));
process.on("uncaughtException", (err) => {
  LOG.fatal({ err: String(err) }, "uncaught exception — exiting for systemd restart");
  process.exit(1);
});

const sha = createHash("sha256").update(SECRET).digest("hex").slice(0, 8);
LOG.info(`bridge starting; secret fingerprint sha256:${sha}…; auth dir ${AUTH_DIR}`);
startSocket().catch((err) => {
  LOG.fatal({ err: String(err) }, "socket start failed — exiting for systemd restart");
  process.exit(1);
});
recoverHostSessions();
pollLoop();
