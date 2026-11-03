#!/usr/bin/env bash
# End-to-end check of the CardDAV source against a throwaway Nextcloud
# (nextcloud:35-apache on SQLite): address books, search by name and date,
# show, export, pseudonymise straight from the address book, set-up command.
# Everything is synthetic. Requirements: docker, curl, uv. Usage: tests/e2e/nextcloud.sh [--keep]
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
NAME=pseudonymed-e2e
PORT=${PORT:-8097}
B="http://localhost:$PORT"
PASS='Sprechstunde-2026!'
OUT=$(mktemp -d)
KEEP=${1:-}

fail() { echo "FAIL: $*" >&2; exit 1; }
occ() { docker exec -e OC_PASS="${OC_PASS:-}" -u www-data "$NAME" php occ "$@"; }
cli() { (cd "$ROOT" && uv run --quiet pseudonymed "$@"); }
# Capture first, grep afterwards: grep -q would close the pipe early and make the CLI fail with SIGPIPE.
run() { cli "$@" > "$OUT/last.txt" 2>&1 || { cat "$OUT/last.txt" >&2; return 1; }; cat "$OUT/last.txt"; }
last() { grep -q -- "$1" "$OUT/last.txt"; }
cleanup() {
	[ "$KEEP" = "--keep" ] || docker rm -f -v "$NAME" >/dev/null 2>&1 || true
	rm -rf "$OUT"
}
trap cleanup EXIT

echo "# starting Nextcloud"
docker rm -f -v "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "$PORT:80" \
	-e SQLITE_DATABASE=nextcloud -e NEXTCLOUD_ADMIN_USER=admin -e NEXTCLOUD_ADMIN_PASSWORD=adminadmin \
	-e NEXTCLOUD_TRUSTED_DOMAINS=localhost nextcloud:35-apache >/dev/null
until curl -sf "$B/status.php" 2>/dev/null | grep -q '"installed":true'; do sleep 3; done
sleep 3
OC_PASS="$PASS" occ user:add --password-from-env dr >/dev/null
occ dav:create-addressbook dr patienten >/dev/null
occ dav:create-addressbook dr privat >/dev/null

echo "# synthetic cards and a synthetic report"
(cd "$ROOT" && uv run --quiet python - "$OUT" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, ".")
from tests.synthetisch import erzeuge_befund, synthetischer_patient, vcard_aus_patient
out = Path(sys.argv[1])
for seed, g, uid in ((7, "m", "uid-a"), (11, "w", "uid-b"), (23, "w", "uid-c")):
    p = synthetischer_patient(seed, g)
    (out / f"{uid}.vcf").write_text(vcard_aus_patient(p, uid), encoding="utf-8")
    (out / f"{uid}.name").write_text(p.nachname)
erzeuge_befund(out / "befund.pdf", synthetischer_patient(7, "m"))
PY
)
for f in uid-a uid-b uid-c; do
	code=$(curl -s -o /dev/null -w '%{http_code}' -u "dr:$PASS" -X PUT \
		-H 'Content-Type: text/vcard; charset=utf-8' --data-binary "@$OUT/$f.vcf" \
		"$B/remote.php/dav/addressbooks/users/dr/patienten/$f.vcf")
	[ "$code" = 201 ] || fail "PUT $f returned $code"
done
A=$(cat "$OUT/uid-a.name"); C=$(cat "$OUT/uid-c.name")
export PSEUDONYMED_CARDDAV_URL="$B" PSEUDONYMED_CARDDAV_BENUTZER=dr
export PSEUDONYMED_CARDDAV_ADRESSBUCH=patienten PSEUDONYMED_CARDDAV_PASSWORT="$PASS"

echo "# address books"
run kontakte adressbuecher
last patienten && last privat || fail "address books missing"

echo "# search"
run kontakte suchen "$A" --speicher "$OUT/s.sqlite"
[ "$(grep -c . "$OUT/last.txt")" = 1 ] || fail "expected exactly one hit for $A"
run kontakte suchen 1977 --speicher "$OUT/s.sqlite" >/dev/null
last "$C" || fail "search by year failed"
kurz=$(printf '%s' "$A" | cut -c1-3 | tr '[:upper:]' '[:lower:]')
run kontakte suchen "$kurz 1959" --speicher "$OUT/s.sqlite" >/dev/null
last "$A" || fail "name+year search failed"
if cli kontakte suchen Zyxwv --speicher "$OUT/s.sqlite" >/dev/null 2>&1; then fail "no-hit search must exit 1"; fi

echo "# show and export"
run kontakte zeigen "$A" >/dev/null
last "Versichertennummer" || fail "KVNR from NOTE missing"
run kontakte export "$A" -o "$OUT/patient.json"
grep -q '"nachname"' "$OUT/patient.json" || fail "export incomplete"

echo "# pseudonymise straight from the address book"
run pseudonymisieren "$OUT/befund.pdf" --kontakt "$A" --speicher "$OUT/s.sqlite"
last "Gegenprobe: bestanden" || fail "check did not pass"
[ -f "$OUT/befund_pseudonym.pdf" ] || fail "no output PDF"
if grep -qi "$A" "$OUT/befund_pseudonym.txt"; then fail "surname survived in the text file"; fi
run kontakte suchen "$A" --speicher "$OUT/s.sqlite"
last "Patient A" || fail "letter not shown"

echo "# set-up command (password from the environment, no keyring)"
run kontakte einrichten --url "$B" --benutzer dr --adressbuch patienten \
	--konfig "$OUT/konfig.toml" --kein-schluesselbund
last "gespeichert" || fail "einrichten failed"
if grep -q "$PASS" "$OUT/konfig.toml"; then fail "password written to config"; fi
unset PSEUDONYMED_CARDDAV_URL PSEUDONYMED_CARDDAV_BENUTZER PSEUDONYMED_CARDDAV_ADRESSBUCH
run kontakte adressbuecher --konfig "$OUT/konfig.toml" >/dev/null
last patienten || fail "config file not used"

version=$(curl -s "$B/status.php" | python3 -c 'import json,sys; print(json.load(sys.stdin)["versionstring"])')
echo "PASS: CardDAV end-to-end against Nextcloud $version"
