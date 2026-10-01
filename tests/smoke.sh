#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

output=$(./astra 2>&1 || true)
printf '%s\n' "$output" | grep -q '^Astra 4\.0 '
printf '%s\n' "$output" | grep -q '^Usage:'

cat > /tmp/astra-smoke-$$.lua <<'EOF'
if type(astra) ~= "table" then error("astra module is unavailable") end
if type(json) ~= "table" then error("json module is unavailable") end
astra.exit()
EOF

trap 'rm -f /tmp/astra-smoke-$$.lua' EXIT HUP INT TERM
./astra /tmp/astra-smoke-$$.lua
