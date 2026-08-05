"""Deterministic heartbeat — the daily driver (ADR-001: NO LLM in this loop).

Runs on cron (GitHub Actions, 4×/day after the LSOFS cycles). For each Superior-shore
station it computes the reachability forecast (nowcast + 5-day) and:
  * prints a verdict-first brief (CLAUDE.md rule 13) with data-age (rule 5: staleness
    is loud),
  * pushes an ntfy notification ONLY when something changed — a spot newly opens a
    reachable window, or an open one closes — so the phone isn't pinged every run.

State (last reachable-now per station) is a small JSON persisted between runs
(HEARTBEAT_STATE, default .heartbeat_state.json). In CI it's kept warm by actions/cache;
a cache miss just risks one duplicate alert, never a wrong one.

Env: NTFY_TOPIC (push target; unset = dry-run print only), HEARTBEAT_ISSUE (YYYY-MM-DD
override), HEARTBEAT_STATE (state path), NTFY_SERVER (default https://ntfy.sh).

    python scripts/heartbeat.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import forecast_window as fw  # noqa: E402
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import bias_live  # noqa: E402
from tbay_fishcast.features.forecast import summarize  # noqa: E402

STATE = Path(os.environ.get("HEARTBEAT_STATE", ".heartbeat_state.json"))
STALE_H = 18  # forecast issue older than this (vs now) => flag stale


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _issue() -> date:
    env = os.environ.get("HEARTBEAT_ISSUE")
    if env:
        return date.fromisoformat(env)
    return _now().date()  # cron runs after the t12z cycle posts; missing leads are skipped


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def push_ntfy(title: str, body: str, tags: str, priority: str = "default") -> bool:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"[dry-run: would push] {title}\n{body}")
        return False
    # `or` (not a default arg): CI passes NTFY_SERVER="" for an undefined var, and
    # os.environ.get would return that empty string, breaking the URL host.
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    # HTTP headers must be latin-1: strip emoji / normalise the em-dash so Title+Tags
    # encode (emoji rides in the Tags shortcodes and the UTF-8 body instead).
    def _hdr(s: str) -> str:
        return (s.replace("—", "-").replace("’", "'")
                .encode("latin-1", "ignore").decode("latin-1").strip())
    req = urllib.request.Request(
        f"{server}/{topic}", data=body.encode("utf-8"), method="POST",
        headers={"Title": _hdr(title), "Tags": _hdr(tags), "Priority": priority})
    try:
        urllib.request.urlopen(req, timeout=20)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"ntfy push failed: {e}")
        return False


def main(argv) -> int:
    cfg = load_config()
    issue = _issue()
    central, lo, hi, _, n = bias_live.pooled_subsurface_bias(cfg, issue)
    bias = (central, lo, hi, n)

    prev = _load_state()
    lines, state, transitions = [], {}, []
    for s in cfg.shore_stations:
        points, windows, meta = fw.forecast_spot(cfg, s.lat, s.lon, s.name, s.lsofs_node, issue, bias)
        if points is None:
            lines.append(f"• {s.name}: no bathymetry ({meta['bathy']})")
            continue
        now_reach = points[0].reachable if points else False
        verdict = summarize(points, windows)
        flag = "🎣" if now_reach else "—"
        lines.append(f"{flag} {s.name}: {verdict}")
        state[s.id] = now_reach
        was = prev.get(s.id)
        if was is not None and was != now_reach:
            transitions.append((s.name, now_reach, verdict))
        elif was is None and now_reach:
            transitions.append((s.name, True, verdict))  # first-ever, and it's open

    # upwelling-wind probability from the ensemble (best-effort confidence layer)
    upw_line = ""
    try:
        from tbay_fishcast.ingest import wind_forecast
        from tbay_fishcast.features import upwelling
        w = wind_forecast.fetch_ensemble_wind(forecast_days=5)
        prob = upwelling.upwelling_probability(w["time"], w["members"])
        if prob:
            peak_day, peak_p = max(prob.items(), key=lambda kv: kv[1])
            upw_line = (f"wind: peak {peak_p:.0%} chance of upwelling-favorable wind "
                        f"({peak_day:%a}) — {'new cold water likely' if peak_p >= 0.4 else 'no new upwelling; today’s cold water will relax'}")
    except Exception:  # noqa: BLE001
        pass

    # data age / staleness (rule 5)
    age_h = (_now() - datetime(issue.year, issue.month, issue.day, 12, tzinfo=timezone.utc)).total_seconds() / 3600
    stale = age_h > STALE_H
    header = (f"Thunder Bay laker forecast — issue {issue} t12z "
              f"({age_h:.0f} h old{' ⚠️ STALE' if stale else ''})")
    brief = header + "\n" + "\n".join(lines)
    if upw_line:
        brief += "\n" + upw_line
    print(brief)

    # notify only on a change (or a fresh open)
    if transitions:
        opened = [t for t in transitions if t[1]]
        closed = [t for t in transitions if not t[1]]
        # emoji rides in ntfy Tags shortcodes (headers are latin-1; no raw emoji there)
        title = (", ".join(t[0] for t in opened) + " open" if opened
                 else "Window closed: " + ", ".join(t[0] for t in closed))
        body = "\n".join(f"{t[0]}: {t[2]}" for t in transitions)
        if stale:
            body += f"\n⚠️ data {age_h:.0f} h old"
        tags = "fishing_pole_and_fish" if opened else "wind_blowing_face"
        push_ntfy(title, body, tags=tags, priority="high" if opened else "default")
    else:
        print("(no change since last run — no push)")

    STATE.write_text(json.dumps(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
