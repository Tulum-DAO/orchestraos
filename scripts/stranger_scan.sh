#!/bin/bash
# stranger-test scan — the scan the secret scanners cannot do.
#
# Secret scanners answer "is a credential in here?". This answers a different
# question: "does this tell a stranger WHO this is, or WHERE to look?" A repo can
# be 100% secret-clean and still publish a real person's name, a customer you work
# for, a live approval id, or a sentence saying which file a token sits in.
#
# Run it before you make a repo public, and in your release checklist. Edit the two
# lists marked <add your own> with your own names and customers.
# Separate from the four secret scans. Usage: stranger_scan.sh <repo-dir>
R="${1:?repo dir}"; cd "$R" || exit 1
ex=':!node_modules :!*.lock :!dist :!.git'
run() { printf "%-34s %s\n" "$1" "$(git grep -nIE "$2" -- . $ex 2>/dev/null | grep -vE 'HACKATHON_ISSUES.md|CHANGELOG' | wc -l)"; }
echo "== stranger-test scan: $R @ $(git rev-parse --short HEAD) =="
run "approval/questionnaire ids"  'apr_[0-9a-f]{6,}|qnr_[0-9a-f]{6,}'
run "decision ids"                'DEC-[0-9]{6,}'
run "message ids"                 'msg_[0-9a-f]{8}_[0-9]{4,}'
run "incident ids"                'ra_[0-9a-f]{8}'
run "session uuids"               '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
run "agent/seat names (informational)"                  '\b(pm-[a-z]+)\b'
run "operator/personal names"        '\b(Shaw|<add your own>)\b'

run "client/customer names"          '\b(acme-corp|<add your own>)\b'
run "private paths"               '/home/[a-z]+|/Users/[A-Za-z]+'
run "hosts / tailnet / LAN ips"   'srv[0-9]{6,}|tail[0-9a-z]+\.ts\.net|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[0-9]+'
run "credential-location prose"   '(token|key|secret|password)[^.\n]{0,40}(in (clear|plain)text|in your crontab|in the crontab|lives in|stored in|sits in)'
