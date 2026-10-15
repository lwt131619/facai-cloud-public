# Facai Cloud deployment and public access

This document describes how to place Facai Cloud behind authenticated HTTPS on the
owner's VPS, and how to **verify** rather than assume that public access works.

**Public access is LIVE and verified (2026-10-01).** The authoritative DNS gate
passes, the current code was deployed to the VPS, a Let's Encrypt certificate for
`whstapp.lol` and `www.whstapp.lol` was issued, and
`deploy/check_public_access.sh` exited 0 (authoritative DNS, TCP 443, TLS,
`health 200`, anonymous `401`, authenticated `200`). Over HTTPS the login flow,
mission pipeline and SSE stream were exercised successfully. The historical DNS
diagnosis is retained below for the record; see
[DNS status on NameSilo / dnsowl](#dns-status-on-namesilo--dnsowl).

## Topology

```
browser (PC / phone)
   │  HTTPS 443
   ▼
nginx  ──(TLS termination, security headers, SSE)──►  127.0.0.1:8765
                                                        uvicorn / facai.api:app
                                                        │
                                                        └── SQLite + mission workspaces under /opt/facai-cloud/data
```

The application binds to loopback only. nginx terminates TLS and forwards
`X-Forwarded-Proto`, so the session cookie is marked `Secure`.

## Files

| File | Purpose |
| --- | --- |
| `deploy/systemd/facai-cloud.service` | Hardened systemd unit, loopback bind, loads `/etc/facai-cloud/facai.env` plus `/etc/facai-cloud/providers/*.env`. |
| `deploy/nginx/facai-cloud.http.conf` | **First-install bootstrap**: HTTP-01 ACME challenge only, no certificate, app not proxied over plaintext. |
| `deploy/nginx/facai-cloud.conf` | TLS server: HTTP→HTTPS redirect, ACME challenge, TLS, HSTS (no `includeSubDomains`), SSE location. Needs a certificate to exist. |
| `deploy/facai.env.example` | Application env **names** only (login/runtime); never commit values. |
| `deploy/providers/deepseek.env.example` | Per-service provider credential template; mounted at `/etc/facai-cloud/providers/deepseek.env`. |
| `deploy/deploy.sh` | Idempotent `install` / `update` / `cert` / `opencode` / `backup` / `rollback` / `status` / `logs`. |
| `deploy/install-opencode.sh` | Installs the pinned, SHA-256-verified, self-contained OpenCode CLI to `/usr/local/bin/opencode` (no Node/npm needed). |
| `deploy/check_public_access.sh` | DNS (cached **and** authoritative)/TCP/TLS/HTTP gate; exits non-zero until verified. |
| `deploy/check_public_access.ps1` | Same gate runnable from the owner's Windows host. |
| `deploy/validate_config.sh` | Static + `bash -n` + optional `nginx -t` validation of this deployment plane. |

## Secrets policy

Credentials are **classified by service** and read from the environment only:

- `/etc/facai-cloud/facai.env` (mode `0640`, `root:facai`) holds application
  login/runtime settings — `FACAI_ADMIN_PASSWORD`, `FACAI_DATA_DIR`,
  `FACAI_COOKIE_SECURE`, `FACAI_TRUST_PROXY`.
- `/etc/facai-cloud/providers/<service>.env` (mode `0640`, `root:facai`, inside a
  `0750` `root:facai` directory) holds one model credential per upstream. The
  DeepSeek and Anthropic keys belong in their corresponding `deepseek.env` and
  `anthropic.env` files.
- The systemd unit loads both: `EnvironmentFile=/etc/facai-cloud/facai.env` and
  `EnvironmentFile=-/etc/facai-cloud/providers/*.env` (the `-` lets the unit start
  before any provider file exists). The gateway resolves `api_key_env` (e.g.
  `DEEPSEEK_API_KEY`) from that environment.
- Registry entries store only `api_key_env` names; raw keys are rejected by the API.
- No secret is written to the database, cookies (sessions store a hash only), logs,
  Git, or the deployment scripts. Database backups therefore contain no raw
  credentials. A code backup (`app-<stamp>.tar.gz`) excludes `/etc`, and the
  separate config backup `env-<stamp>.tar.gz` is `0600 root` because it does
  contain secrets.

Unused provider credentials (Tavily, Bocha, Agnes and Pollinations) are
deliberately **not** migrated or referenced anywhere in this deployment.

## Install

```bash
git clone <repo> /opt/facai-cloud && cd /opt/facai-cloud   # or rsync the checkout
sudo FACAI_DOMAIN=whstapp.lol ./deploy/deploy.sh install
sudoedit /etc/facai-cloud/facai.env                      # set FACAI_ADMIN_PASSWORD
sudoedit /etc/facai-cloud/providers/deepseek.env         # set DEEPSEEK_API_KEY
sudo systemctl restart facai-cloud.service
sudo ./deploy/deploy.sh status           # health should answer 200 on loopback
```

`install` is certificate-aware and happens in two phases so that `nginx -t` can
never reference a missing certificate:

1. **No certificate yet** — the HTTP bootstrap
   (`deploy/nginx/facai-cloud.http.conf`) is installed. It serves
   `/.well-known/acme-challenge/` from `/var/www/certbot` and returns `503` for
   everything else, so the app is never exposed over plaintext HTTP.
2. **After `cert`** — certbot has written the certificate files, so `deploy.sh`
   swaps in the TLS server (`deploy/nginx/facai-cloud.conf`), re-runs `nginx -t`
   and reloads. Running `install` again when a certificate already exists installs
   the TLS config directly.

## Public launch gate (order matters)

1. **DNS first — at the authoritative servers, not a cached resolver.** The `@`
   and `www` A records must answer with the VPS public IP `77.221.148.118` from
   **every** NameSilo `dnsowl` nameserver. A single public resolver (e.g. Google)
   can show the new address while an authoritative server still serves the old
   one, so query the authorities directly:
   ```bash
   for ns in ns1.dnsowl.com ns2.dnsowl.com ns3.dnsowl.com; do
     echo "@$ns apex: $(dig +short @$ns whstapp.lol A | tr '\n' ' ')"
     echo "@$ns www:  $(dig +short @$ns www.whstapp.lol A | tr '\n' ' ')"
   done
   ```
   Every line must be exactly `77.221.148.118`. If any server still returns other
   addresses, **stop**: do not request certificates and do not announce the
   service. `deploy/check_public_access.{sh,ps1}` encodes this same authoritative
   check and exits non-zero until it passes. *This gate currently passes
   (verified 2026-10-01); public access is still blocked by VPS access, not DNS.*
2. **TLS.** Only once DNS matches:
   ```bash
   sudo FACAI_EMAIL=you@example.com FACAI_DOMAIN=whstapp.lol ./deploy/deploy.sh cert
   ```
   `cert` requests the certificate against the HTTP bootstrap webroot, then, only
   after the files exist, installs the TLS server and reloads nginx. HSTS is sent
   as `max-age=63072000` **without** `includeSubDomains`: the owner has not
   confirmed that every subdomain already serves HTTPS, and `includeSubDomains`
   would make browsers refuse those subdomains over HTTP.
3. **Verify** the whole chain (anonymous access blocked, authenticated access works):
   ```bash
   FACAI_ADMIN_PASSWORD=... ./deploy/check_public_access.sh whstapp.lol 77.221.148.118
   ```
   On Windows:
   ```powershell
   $env:FACAI_ADMIN_PASSWORD='...'; .\deploy\check_public_access.ps1 -Domain whstapp.lol -ServerIp 77.221.148.118
   ```
   The script prints `NOT LIVE` and exits non-zero until every check passes.

## DNS status on NameSilo / dnsowl

**Resolved (2026-10-01).** The previously partial propagation completed: direct
queries to `ns1.dnsowl.com`, `ns2.dnsowl.com` and `ns3.dnsowl.com` return exactly
`77.221.148.118` for both `whstapp.lol` and `www.whstapp.lol`, so the authoritative
DNS gate above passes. The application was then deployed, the certificate issued
and public access verified as shown at the top of this document; public access is
`LIVE`. The diagnosis below is kept for the record.

**Historical symptom.** The NameSilo panel shows `@`, `www` and `facai` all pointing at
`77.221.148.118`, and the domain uses the default NameSilo nameservers
(`ns1/ns2/ns3.dnsowl.com`), yet direct queries to the authoritative servers still
return the old addresses for the apex. This is not a local browser cache.

**How it was measured.** The three nameservers resolve to six IPv4 addresses
(Cloudflare-fronted anycast), and each was queried directly:

| Nameserver | IPv4 addresses |
| --- | --- |
| `ns1.dnsowl.com` | `162.159.27.173`, `162.159.26.136` |
| `ns2.dnsowl.com` | `162.159.27.130`, `162.159.26.49` |
| `ns3.dnsowl.com` | `162.159.26.234`, `162.159.27.98` |

Observed at the time of writing:

- **apex `whstapp.lol`** — the six servers predominantly answered the **old** set
  `{172.232.24.161, 172.232.24.235, 172.234.25.42}` (TTL ~3603); repeated queries
  to the *same* IPv4 occasionally answered the new `77.221.148.118` instead.
- **`www.whstapp.lol`** — five of the six answered `77.221.148.118`
  (TTL 3600), while `162.159.26.49` answered the old set; repeated queries flapped.
- **`facai.whstapp.lol`** — all six answered `77.221.148.118`.
- The zone **SOA serial was identical on every server** (`1790852254`) even though
  the apex answers differed.
- Public resolvers disagreed: Google `8.8.8.8`, Quad9 `9.9.9.9` and OpenDNS
  returned the new address, while Cloudflare `1.1.1.1` returned the old apex and
  old `www`.
- There is **no DS record** at the `.lol` registry, so DNSSEC is not enabled and is
  not the cause.

**Conclusion.** The panel configuration is correct and the authoritative servers
are indeed `dnsowl`, but the update has only **partially propagated across
NameSilo's anycast `dnsowl` fleet**. The apex is the least propagated; `www` is
mostly (not entirely) updated; the `facai` subdomain is fully updated. Because two
authoritative servers return different data under the *same* SOA serial, this is
an authoritative-side push/synchronisation lag, not a client or browser cache, and
not something the owner can fix from the DNS panel. Any single public resolver
(including Google) can misleadingly show the new address.

**Re-check the gate** (this is what the checker automates):

```bash
for ns in ns1.dnsowl.com ns2.dnsowl.com ns3.dnsowl.com; do
  echo "@$ns apex: $(dig +short @$ns whstapp.lol A | tr '\n' ' ')"
  echo "@$ns www:  $(dig +short @$ns www.whstapp.lol A | tr '\n' ' ')"
done
```

Every line must be exactly `77.221.148.118`. The public-access checker requires
*every* authoritative answer to equal the VPS IP and reports `NOT LIVE` until the
whole chain (DNS, TCP 443, TLS, HTTP, auth) passes.

If the apex is ever old again after roughly 24 h, open a NameSilo support ticket
(contact form or live chat) with the exact output above, the shared SOA serial
`1790852254`, and the note that authoritative nodes disagree at the same serial;
ask them to re-push/resync the zone. Avoid repeatedly re-saving the record, which
only resets the propagation clock. Do not use `includeSubDomains` HSTS while
subdomains are not all confirmed HTTPS, and do not announce the service as live
until the gate passes.

References: NameSilo
[DNS Troubleshooting](https://www.namesilo.com/support/v2/articles/domain-manager/dns-troubleshooting)
and
[DNS Propagation Explained](https://www.namesilo.com/blog/en/dns/dns-propagation-explained-why-your-domain-changes-take-time-2026-guide).

## Authentication

- Browsers: `GET /login` form → `POST /api/v1/auth/login` sets an `HttpOnly`,
  `SameSite=Strict`, `Secure` cookie. Unsafe API calls require the `X-CSRF-Token`
  header returned by login / `/api/v1/auth/session`.
- Scripts: HTTP Basic (`admin:<FACAI_ADMIN_PASSWORD>`) remains accepted.
- Failed logins are throttled per client address (`FACAI_LOGIN_MAX_FAILURES`,
  `FACAI_LOGIN_WINDOW`). Sessions are opaque tokens; only their SHA-256 hash is
  stored, and they expire after `FACAI_SESSION_TTL` seconds.

## Update and rollback

- Update: `sudo ./deploy/deploy.sh update` (backs up, re-syncs code, reinstalls
  deps, restarts).
- Rollback code: `sudo ./deploy/deploy.sh rollback` restores the most recent code
  snapshot; pass an explicit archive to restore an older one.
  `sudo ./deploy/deploy.sh backup` takes a snapshot without changing anything.
- Rollback database: stop the service, then restore the pre-restore snapshot kept by
  `facai.backup` (see below), and restart.

### Classified provider credentials and rollback

`install` and `update` classify credentials per service and are safe to re-run:

1. `backup_env_dir` snapshots the whole `/etc/facai-cloud` tree to
   `/var/backups/facai-cloud/env-<UTC>.tar.gz` (mode `0600 root`, plus an
   `env-latest.tar.gz` symlink) **before** anything is changed.
2. `install_provider_env_files` creates `/etc/facai-cloud/providers` (`0750`,
   `root:facai`) and seeds one `0640` file per committed
   `deploy/providers/*.env.example`, never overwriting a value already present.
3. `classify_deepseek_env` moves `DEEPSEEK_API_KEY` **once** out of
   `/etc/facai-cloud/facai.env` into `providers/deepseek.env` using
   permission-restricted temp files and atomic `mv`; the value is never printed.
   Only the actively-used DeepSeek key is migrated; unused providers are untouched.

To revert the split, restore the pre-change archive and restart:
`tar -xzf /var/backups/facai-cloud/env-latest.tar.gz -C / && systemctl restart facai-cloud.service`.
Because the gateway reads credentials by `api_key_env` name, code rollback
(`deploy.sh rollback`) keeps working with either layout.

**Verified live (2026-10-01).** The split was migrated on the VPS `77.221.148.118`
via `deploy.sh update`: `/etc/facai-cloud/providers` is `0750 root:facai`,
`providers/deepseek.env` and `facai.env` are `0640 root:facai`, and `facai.env` no
longer contains `DEEPSEEK_API_KEY`. `systemctl show` lists both `EnvironmentFile`s
and the running process environment matches the provider file value.
`deploy/check_public_access.sh whstapp.lol 77.221.148.118` exits `0` (`health 200`,
anonymous `401`, authenticated `200`), and a real DeepSeek call over HTTPS through
`POST /api/v1/missions/80587244520747ddb11f639e2746bce5/model/complete` returned
`MODEL_COMPLETED` (provider `DeepSeek`, upstream HTTP `200`). A byte scan found the
key in no repository file, no SQLite table, and none of the journal / nginx / auth
logs. Unused provider credentials were not touched.

### Why a plain `rsync --delete` was unsafe here

`/opt/facai-cloud` holds more than the checkout: the live SQLite database, mission
workspaces/previews and any owner files all live under the same tree. A blanket
`rsync -a --delete SRC/ /opt/facai-cloud/` would delete everything that is not in
the source checkout. `sync_code` therefore:

1. takes a timestamped backup first
   (`/var/backups/facai-cloud/app-<UTC>.tar.gz`, plus an `app-latest.tar.gz`
   symlink, pruned to `BACKUP_KEEP` = 10 by default; override `BACKUP_DIR` /
   `BACKUP_KEEP`);
2. copies and deletes only the managed paths (`facai/`, `tests/`, `docs/`,
   `deploy/` and the known top-level files), using
   `--exclude '/*'` so every other top-level entry is excluded — and rsync never
   deletes excluded destination files;
3. keeps `data/` and `.venv/` protected with explicit `P` filters and re-creates
   `data/` with `facai:facai` ownership.

Put owner data in `data/` or any other non-managed top-level directory. Files
placed *inside* `facai/`, `tests/`, `docs/` or `deploy/` are treated as part of the
deploy-managed code.

## OpenCode CLI

The OpenCode adapter runs a self-contained binary (no Node/npm on the host):

```bash
sudo ./deploy/deploy.sh opencode       # install/upgrade the pinned, verified CLI
```

`install-opencode.sh` downloads a single pinned release
(`anomalyco/opencode`, default `v1.18.32`, `opencode-linux-x64.tar.gz`), verifies its
SHA-256 before installing to `/usr/local/bin/opencode`, and is a no-op if that version
is already present. Override with `OPENCODE_VERSION` / `OPENCODE_SHA256` for a
deliberate upgrade. The service authenticates OpenCode from the environment
(`DEEPSEEK_API_KEY` in `/etc/facai-cloud/providers/deepseek.env`); no credential is
written to a config file, passed on a command line or logged. The unit sets
`HOME=/opt/facai-cloud/data/opencode/home` because OpenCode writes its own store under
`$HOME/.local`, and the hardened service may only write inside the data directory.

### Validating the deployment plane

```bash
bash deploy/validate_config.sh     # bash -n + static checks + nginx -t (if installed)
python -m pytest tests/test_deploy_config.py -q
```

## Backup and restore drill

Take a verified snapshot and rehearse a restore into a throwaway directory:

```bash
python -m facai.backup create  --data-dir /opt/facai-cloud/data
python -m facai.backup drill   --data-dir /opt/facai-cloud/data
```

`drill` creates a backup, verifies integrity, restores it into a temporary
directory, and compares mission/event/registry counts. It never modifies the live
database. See `README.md` for the full backup/restore/verify commands.

## Mobile verification

The console is checked at phone and tablet viewports (no horizontal overflow,
vertical pipeline, visible tap targets, sticky status bar) with
`python -m pytest tests/test_mobile_ui.py`. See `docs/UI_SPEC.md` for the layout
contract.
