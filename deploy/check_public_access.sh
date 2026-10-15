#!/usr/bin/env bash
# Verify (never assume) that Facai Cloud is publicly reachable over HTTPS.
# Usage: FACAI_ADMIN_PASSWORD=... ./deploy/check_public_access.sh [domain] [server_ip]
# The password is used only for the authenticated probe and is never printed.
set -uo pipefail

DOMAIN="${1:-whstapp.lol}"
SERVER_IP="${2:-77.221.148.118}"
FAIL=0

say() { printf '%s\n' "$*"; }
check() { if [ "$1" = 0 ]; then say "PASS: $2"; else say "FAIL: $2"; FAIL=1; fi; }

say "== DNS (resolver cache, informational) =="
resolved="$(dig +short "${DOMAIN}" A 2>/dev/null | tr '\n' ' ')"
www="$(dig +short "www.${DOMAIN}" A 2>/dev/null | tr '\n' ' ')"
say "A ${DOMAIN}    -> ${resolved:-<none>}"
say "A www.${DOMAIN} -> ${www:-<none>}"

# A cached resolver can show the new address while a NameSilo/dnsowl
# authoritative server still answers with an old one (or vice versa). The gate
# must therefore require EVERY authoritative answer to be exactly SERVER_IP,
# because Let's Encrypt validates against whichever server it reaches first.
only_server() {
  local got
  got="$(printf '%s\n' $1 | grep -E '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' | sort -u | tr '\n' ' ' | sed -E 's/ +$//')"
  [ "${got}" = "${SERVER_IP}" ]
}

say "== DNS (authoritative dnsowl) =="
for ns in ns1.dnsowl.com ns2.dnsowl.com ns3.dnsowl.com; do
  a="$(dig +short @"${ns}" "${DOMAIN}" A 2>/dev/null | tr '\n' ' ')"
  w="$(dig +short @"${ns}" "www.${DOMAIN}" A 2>/dev/null | tr '\n' ' ')"
  say "  @${ns} A ${DOMAIN} -> ${a:-<none>}"
  say "  @${ns} A www.${DOMAIN} -> ${w:-<none>}"
  if only_server "${a}"; then
    check 0 "authoritative ${ns} apex is exactly ${SERVER_IP}"
  else
    check 1 "authoritative ${ns} apex is not exactly ${SERVER_IP} (propagation pending)"
  fi
  if only_server "${w}"; then
    check 0 "authoritative ${ns} www is exactly ${SERVER_IP}"
  else
    check 1 "authoritative ${ns} www is not exactly ${SERVER_IP} (propagation pending)"
  fi
done

say "== TCP =="
if timeout 5 bash -c "cat < /dev/null > /dev/tcp/${SERVER_IP}/443" 2>/dev/null; then
  check 0 "port 443 reachable"
else
  check 1 "port 443 not reachable"
fi

say "== TLS =="
cert="$(echo | timeout 8 openssl s_client -servername "${DOMAIN}" -connect "${DOMAIN}:443" 2>/dev/null \
  | openssl x509 -noout -subject -enddate 2>/dev/null)"
if [ -n "${cert}" ]; then
  check 0 "TLS handshake ok (${cert//$'\n'/; })"
else
  check 1 "TLS handshake failed (certificate may not be issued yet)"
fi

base="https://${DOMAIN}"
say "== HTTP =="
health="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${base}/api/v1/health" || echo 000)"
[ "${health}" = "200" ] && check 0 "health 200" || check 1 "health returned ${health}"

anon="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${base}/api/v1/missions" || echo 000)"
case "${anon}" in
  401|403|303) check 0 "anonymous API access blocked (${anon})" ;;
  *) check 1 "anonymous API access NOT blocked (returned ${anon})" ;;
esac

if [ -n "${FACAI_ADMIN_PASSWORD:-}" ]; then
  auth="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -u "admin:${FACAI_ADMIN_PASSWORD}" \
    "${base}/api/v1/missions" || echo 000)"
  [ "${auth}" = "200" ] && check 0 "authenticated API access works" || check 1 "authenticated API returned ${auth}"
else
  say "SKIP: set FACAI_ADMIN_PASSWORD to probe authenticated access"
fi

say "== Summary =="
if [ "${FAIL}" = 0 ]; then
  say "PUBLIC ACCESS VERIFIED for ${DOMAIN}"
else
  say "NOT LIVE: one or more checks failed. Do not claim public availability."
fi
exit "${FAIL}"
