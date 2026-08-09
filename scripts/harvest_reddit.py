"""Harvest Thunder Bay fishing talk out of Reddit's public archive (ADR-042 source).

WHY ARCTIC SHIFT AND NOT REDDIT.COM: reddit.com/robots.txt is `User-agent: * / Disallow: /`, and
the .json endpoints now answer a network-security block page, so crawling reddit.com directly is
both disallowed and broken. Arctic Shift is the public research archive (the Pushshift successor
pointed at by r/reddit4researchers); its own robots.txt is `Disallow:` — everything allowed — and
it exposes full history through a documented JSON API. That is the clean channel.

SCOPE: the ENTIRE subreddit, not a thread. r/ThunderBay is 394,683 comments and 28,271 posts back
to 2010-11-17, and the fishing talk is scattered across thousands of unrelated threads (a salmon
report lands in a weather thread as often as in a fishing one), so anything less than a full sweep
misses most of it. Posts and comments are swept as separate streams.

EFFICIENCY — three decisions, in order of how much they buy:

  1. NO full-text search. The archive's comment-body search times out server-side ("Timeout. Maybe
     slow down a bit") — that index is too expensive to query. Chronological enumeration is cheap
     and reliable, so we page through and keyword-filter LOCALLY, in-process, for free.
  2. WORK-STEALING TIME WINDOWS, not one sequential cursor. The range is cut into ~monthly windows
     pushed onto a queue that N workers drain. Reddit volume is wildly uneven (r/ThunderBay in 2011
     is a rounding error next to 2021), so a fixed per-worker split would leave three workers idle
     while one ground through the dense years; with a queue, whoever finishes first takes the next
     window and the imbalance disappears without needing to know the density up front.
  3. GLOBAL RATE LIMIT, not per-worker. Parallelism is for latency hiding, never for hitting a
     volunteer-run host harder: a single shared token bucket caps TOTAL request rate no matter how
     many workers run. 4 workers under a 3 req/s ceiling turns a ~1.7 h sequential crawl into
     ~20 min while issuing no more requests per second than a careful single-threaded client.

Every window is independent, so the checkpoint is exact: an interrupted run resumes at window
granularity, and later runs are incremental (only the trailing window is re-swept).

STAGE 1 ONLY. Deterministic; writes CANDIDATES, not ledger rows. Free text like "got a couple last
weekend" needs judgement to become a dated observation, and that judgement belongs to the research
Routine (ADR-042), never to the heartbeat (ADR-001). Output lands in
knowledge/observations/candidates/ for the Routine to drain.

    python scripts/harvest_reddit.py                       # resume everything
    python scripts/harvest_reddit.py --sub ThunderBay --workers 4
    python scripts/harvest_reddit.py --status
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "knowledge" / "observations" / "candidates"
CKPT = OUT_DIR / "reddit_checkpoint.json"

API = "https://arctic-shift.photon-reddit.com/api"
UA = "tbay-fishcast/1.0 (research; +https://github.com/richardrob123/Tbay-fishcast)"
PAGE = 100              # hard API cap: "'limit' must be between 1 and 100"
RATE_PER_S = 3.0        # GLOBAL ceiling across all workers
WORKERS = 4
WINDOW_DAYS = 30
MAX_TRIES = 5

ENUM_SUBS = ("ThunderBay", "northernontario", "ontariofishing")
# r/Ontario (8.8 M comments) and r/Fishing (3.4 M) are ~20x the volume for a sliver of the local
# signal, so they get cheap targeted title search instead of a full sweep.
SEARCH_SUBS = ("Ontario", "Fishing", "flyfishing", "troutfishing")
SEARCH_TERMS = ("thunder bay", "nipigon", "lake superior steelhead", "superior coaster",
                "kaministiquia", "current river")

# Local filter — deliberately GENEROUS. This stage decides only "is this worth a model's
# attention"; a false positive costs a few hundred bytes, a false negative loses the observation
# permanently. Precision is extraction's job.
SPECIES_RE = re.compile(
    r"\b(steelhead|rainbow trout|rainbows?|chinook|king salmon|coho|pink salmon|humpy|humpies|"
    r"salmon|lake trout|lakers?|brook trout|brookies?|speckled trout|specks?|coaster|"
    r"splake|whitefish|smelt|herring|walleye|pike)\b", re.I)
# A species word alone is not enough — r/ThunderBay discusses salmon at the grocery store — so a
# hit must also carry fishing or local-water context.
CONTEXT_RE = re.compile(
    r"\b(fish(ing|ed|erman)?|caught|catch(ing)?|angler|spawn(ing)?|run(ning|s)?|bite|biting|"
    r"trolling|casting|jig(ging)?|fly rod|spoon|spawn sac|roe|limit|creel|derby|stock(ed|ing)?|"
    r"river mouth|shore|pier|breakwall|net(ting)?|dipping)\b", re.I)
PLACE_RE = re.compile(
    r"\b(current river|mcintyre|neebing|kaministiquia|kam river|mission river|mcvicar|"
    r"marina park|silver harbour|mackenzie|sturgeon bay|hurkett|black bay|nipigon|"
    r"cloud bay|pigeon river|wolf river|thunder bay|sibley|pass lake|loch lomond|"
    r"chippewa|boulevard lake|dog lake|whitefish lake)\b", re.I)


class RateLimiter:
    """One shared token bucket. Workers block here, so TOTAL request rate is bounded regardless of
    how many threads are running — parallelism buys latency, never extra load on the host."""

    def __init__(self, per_s: float):
        self._min_gap = 1.0 / per_s
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._min_gap
        if wait:
            time.sleep(wait)


LIMITER = RateLimiter(RATE_PER_S)
_write_lock = threading.Lock()
_ckpt_lock = threading.Lock()


def _get(path: str, **params):
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    for attempt in range(MAX_TRIES):
        LIMITER.acquire()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                body = json.loads(r.read())
            if body.get("error"):
                raise RuntimeError(body["error"])
            return body.get("data") or []
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError,
                ValueError, OSError) as e:
            if attempt == MAX_TRIES - 1:
                raise
            # the host's own "slow down" signal — back off hard rather than retrying at rate
            time.sleep(min(60, 2 ** attempt * 3))
            del e
    return []


def _relevant(text: str) -> bool:
    if not text or len(text) < 12:
        return False
    if not SPECIES_RE.search(text):
        return False
    return bool(CONTEXT_RE.search(text) or PLACE_RE.search(text))


def _permalink(sub: str, row: dict, stream: str) -> str:
    if stream != "comments":
        return f"https://www.reddit.com/r/{sub}/comments/{row.get('id')}/"
    link = str(row.get("link_id") or "").replace("t3_", "")
    return f"https://www.reddit.com/r/{sub}/comments/{link}/_/{row.get('id')}/"


def _row_out(sub: str, stream: str, r: dict, body_field: str, extra: dict | None = None) -> dict:
    d = {
        "sub": sub, "stream": stream, "id": r.get("id"),
        "created_utc": r["created_utc"],
        "date": datetime.fromtimestamp(r["created_utc"], timezone.utc).date().isoformat(),
        "author": r.get("author"), "score": r.get("score"),
        "title": r.get("title"), "text": (r.get(body_field) or "")[:2000],
        "url": _permalink(sub, r, stream),
    }
    if extra:
        d.update(extra)
    return d


def _load_ckpt() -> dict:
    if CKPT.exists():
        try:
            return json.loads(CKPT.read_text())
        except ValueError:
            pass
    return {}


def _save_ckpt(c: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CKPT.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=1, sort_keys=True) + "\n")
    tmp.replace(CKPT)          # atomic: a kill mid-write must not corrupt the checkpoint


def _sub_meta(sub: str) -> dict:
    rows = _get("subreddits/search", subreddit=sub, limit=1)
    if not rows:
        raise RuntimeError(f"subreddit {sub} not in the archive")
    return rows[0]["_meta"]


def _windows(start_ts: int, end_ts: int, days: int) -> list[tuple[int, int]]:
    step = days * 86400
    return [(t, min(t + step, end_ts)) for t in range(start_ts, end_ts, step)]


def _sweep_window(sub: str, stream: str, lo: int, hi: int, fh, counters: dict) -> None:
    """Page one time window to exhaustion. Independent of every other window."""
    body_field = "selftext" if stream == "posts" else "body"
    fields = ("created_utc," + body_field + ",id,author,score"
              + (",title" if stream == "posts" else ",link_id"))
    cursor = lo
    while True:
        rows = _get(f"{stream}/search", subreddit=sub, limit=PAGE, sort="asc",
                    after=str(cursor), before=str(hi), fields=fields)
        if not rows:
            return
        keep = []
        for r in rows:
            text = " ".join(filter(None, [r.get("title"), r.get(body_field)]))
            if _relevant(text):
                keep.append(_row_out(sub, stream, r, body_field))
        if keep:
            with _write_lock:
                for d in keep:
                    fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                fh.flush()
        with _ckpt_lock:
            counters["scanned"] += len(rows)
            counters["kept"] += len(keep)
        newest = max(r["created_utc"] for r in rows)
        cursor = newest if newest > cursor else cursor + 1
        if len(rows) < PAGE or cursor >= hi:
            return


def enumerate_sub(sub: str, stream: str, ckpt: dict, workers: int) -> None:
    key = f"{sub}:{stream}"
    state = ckpt.setdefault(key, {"done_windows": [], "scanned": 0, "kept": 0})
    meta = _sub_meta(sub)
    first = meta["earliest_comment"] if stream == "comments" else meta["earliest_post"]
    now = int(time.time())
    wins = _windows(int(first), now, WINDOW_DAYS)
    done = set(tuple(w) for w in state["done_windows"])
    # the trailing window is always re-swept: it was still filling when we last saw it
    todo = [w for w in wins if tuple(w) not in done or w is wins[-1]]
    if not todo:
        print(f"  {key}: complete ({state['scanned']:,} scanned, {state['kept']} kept)")
        return
    total = meta.get("num_comments" if stream == "comments" else "num_posts", 0)
    # request cost is ~1 per window (the floor, even for empty ones) + 1 per 100 rows
    est = len(todo) + total // PAGE
    print(f"  {key}: {len(todo)}/{len(wins)} windows to sweep, {total:,} rows in the archive "
          f"(~{est:,} requests, ~{est / RATE_PER_S / 60:.0f} min at {RATE_PER_S:g}/s)")

    q: queue.Queue = queue.Queue()
    for w in todo:
        q.put(w)
    counters = {"scanned": 0, "kept": 0, "windows": 0}
    out_path = OUT_DIR / f"reddit_{sub}_{stream}.jsonl"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    def worker():
        with out_path.open("a") as fh:
            while True:
                try:
                    lo, hi = q.get_nowait()
                except queue.Empty:
                    return
                try:
                    _sweep_window(sub, stream, lo, hi, fh, counters)
                    with _ckpt_lock:
                        state["done_windows"].append([lo, hi])
                        counters["windows"] += 1
                        n = counters["windows"]
                    if n % 10 == 0:
                        d = datetime.fromtimestamp(hi, timezone.utc).date()
                        print(f"    {key}: {n}/{len(todo)} windows, "
                              f"{counters['scanned']:,} scanned, {counters['kept']} kept (~{d})")
                        with _ckpt_lock:
                            _save_ckpt(ckpt)
                except Exception as e:  # noqa: BLE001 — a dead window must not kill the sweep
                    errors.append(f"{datetime.fromtimestamp(lo, timezone.utc).date()}: {e}")
                finally:
                    q.task_done()

    ts = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    state["scanned"] += counters["scanned"]
    state["kept"] += counters["kept"]
    state["done_windows"] = sorted({tuple(w) for w in state["done_windows"]})
    state["done_windows"] = [list(w) for w in state["done_windows"]]
    _save_ckpt(ckpt)
    print(f"  {key}: +{counters['scanned']:,} scanned, +{counters['kept']} candidates"
          + (f", {len(errors)} window(s) failed" if errors else ""))
    for e in errors[:3]:
        print(f"      ! {e[:100]}")


def search_sub(sub: str, ckpt: dict) -> None:
    state = ckpt.setdefault(f"{sub}:search", {"ids": [], "kept": 0})
    known = set(state["ids"])
    out_path = OUT_DIR / f"reddit_{sub}_search.jsonl"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    kept = 0
    with out_path.open("a") as fh:
        for t in SEARCH_TERMS:
            try:
                rows = _get("posts/search", subreddit=sub, query=t, limit=PAGE,
                            fields="created_utc,selftext,id,author,score,title")
            except Exception as e:  # noqa: BLE001
                print(f"    {sub}/{t!r}: failed ({str(e)[:60]})")
                continue
            for r in rows:
                if r.get("id") in known:
                    continue
                known.add(r.get("id"))
                text = " ".join(filter(None, [r.get("title"), r.get("selftext")]))
                if _relevant(text):
                    fh.write(json.dumps(_row_out(sub, "posts", r, "selftext",
                                                 {"matched_query": t}), ensure_ascii=False) + "\n")
                    kept += 1
    state["ids"] = sorted(known)
    state["kept"] = state.get("kept", 0) + kept
    _save_ckpt(ckpt)
    print(f"  {sub}:search: +{kept} candidates ({len(known)} posts seen)")


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub", action="append")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--rate", type=float, default=RATE_PER_S, help="global req/s ceiling")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)

    global LIMITER
    LIMITER = RateLimiter(a.rate)
    ckpt = _load_ckpt()

    if a.status:
        if not ckpt:
            print("no checkpoint yet")
            return 0
        for k, v in sorted(ckpt.items()):
            if "ids" in v:
                print(f"  {k:32s} search  {v.get('kept', 0):>5} kept")
            else:
                print(f"  {k:32s} {v['scanned']:>9,} scanned  {v.get('kept', 0):>5} kept  "
                      f"{len(v.get('done_windows', []))} windows done")
        return 0

    t0 = time.time()
    for sub in [s for s in ENUM_SUBS if not a.sub or s in a.sub]:
        for stream in ("posts", "comments"):
            try:
                enumerate_sub(sub, stream, ckpt, a.workers)
            except Exception as e:  # noqa: BLE001
                print(f"  {sub}:{stream}: FAILED ({str(e)[:90]}) — checkpoint kept")
                _save_ckpt(ckpt)
    for sub in [s for s in SEARCH_SUBS if not a.sub or s in a.sub]:
        try:
            search_sub(sub, ckpt)
        except Exception as e:  # noqa: BLE001
            print(f"  {sub}:search: FAILED ({str(e)[:90]})")
    _save_ckpt(ckpt)
    print(f"done in {(time.time() - t0) / 60:.1f} min -> {OUT_DIR}")
    print("candidates are STAGE 1: the research Routine turns them into ledger rows (ADR-042)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
