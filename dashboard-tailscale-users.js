// dashboard-tailscale-users.js: which Tailscale logins the web terminal lets in, read from ORCHESTRA_TAILSCALE_USERS or
// <data dir>/state/tailscale-users. The API's parser (api/src/lib/tailscale-identity.ts) is the full one, with roles;
// this is its allowlist half, and api/src/lib/tailscale-identity.test.ts runs both on the same fixtures so they agree:
// a login is a word with an "@" in it (after "#" comments are cut); role words (admin, member) and any other word are
// never logins; each login once.
"use strict";

function allowedLogins(raw) {
  const out = [];
  for (const line of String(raw || "").split(/\r?\n/)) {
    for (const tok of line.replace(/#.*$/, "").split(/[\s,]+/)) {
      const s = tok.trim().toLowerCase();
      if (s && s.includes("@") && s !== "admin" && s !== "member" && !out.includes(s)) out.push(s);
    }
  }
  return out;
}

module.exports = { allowedLogins };
