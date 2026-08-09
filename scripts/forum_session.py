"""Authenticated session helper for the Thunder Bay Fishing board (ADR-042 forum access).

Topic bodies are guest-blocked (HTTP 403, IPS4 permission check). The board owner gave verbal
permission for this project to read the archive; this module holds the session logic so the
credentials live in exactly one place and never touch the repo.

CREDENTIALS — never committed, never logged, never printed:
    export TBF_USER='...' TBF_PASS='...'
  or put those two lines in .forum_creds (gitignored) and `source .forum_creds`.

    python scripts/forum_session.py          # verify login + that a topic body is readable

Crawl discipline (deliberate, because this runs as a logged-in member and any abuse would land
on a real person's account): one request per 3 s, an honest User-Agent identifying the project,
and a hard stop on the first sign of throttling.
"""
from __future__ import annotations

import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = "https://fishingboard.thunderbayfishing.com"
UA = "tbay-fishcast/1.0 (non-commercial fishing-forecast research; contact via site admin)"
DELAY_S = 3.0
_last = [0.0]


def _throttle():
    dt = time.time() - _last[0]
    if dt < DELAY_S:
        time.sleep(DELAY_S - dt)
    _last[0] = time.time()


def build_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()),
        urllib.request.HTTPRedirectHandler())


def get(op, url: str) -> tuple[int, str]:
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with op.open(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def login(op) -> bool:
    """IPS4 login: fetch the form for its CSRF key, then post credentials."""
    user, pw = os.environ.get("TBF_USER"), os.environ.get("TBF_PASS")
    if not user or not pw:
        print("TBF_USER / TBF_PASS not set — see this module's docstring")
        return False
    code, html = get(op, f"{BASE}/index.php?/login/")
    if code != 200:
        print(f"login form unreachable (HTTP {code})")
        return False
    csrf = re.search(r'name="csrfKey"\s+value="([^"]+)"', html)
    ref = re.search(r'name="ref"\s+value="([^"]*)"', html)
    data = {"auth": user, "password": pw, "remember_me": "1", "_processLogin": "usernamepassword"}
    if csrf:
        data["csrfKey"] = csrf.group(1)
    if ref:
        data["ref"] = ref.group(1)
    _throttle()
    req = urllib.request.Request(
        f"{BASE}/index.php?/login/", data=urllib.parse.urlencode(data).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
                 "Referer": f"{BASE}/index.php?/login/"})
    try:
        with op.open(req, timeout=45) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        print(f"login POST failed (HTTP {e.code})")
        return False
    return "X-IPS-LoggedIn" in str(body) or "sign_out" in body or "logout" in body.lower()


def main() -> int:
    op = build_opener()
    if not login(op):
        print("LOGIN FAILED — credentials wrong, or the form changed")
        return 1
    print("login OK")
    # prove a topic BODY is now readable (this is the exact thing that 403'd as a guest)
    code, html = get(op, f"{BASE}/index.php?/topic/27555-upper-shebandowan-fishing/")
    readable = code == 200 and "do not have permission" not in html
    print(f"topic body: HTTP {code} -> {'READABLE' if readable else 'STILL BLOCKED'}")
    if readable:
        posts = len(re.findall(r'<time[^>]+datetime="', html))
        print(f"  {posts} timestamped posts visible — mining can proceed")
    return 0 if readable else 2


if __name__ == "__main__":
    raise SystemExit(main())
