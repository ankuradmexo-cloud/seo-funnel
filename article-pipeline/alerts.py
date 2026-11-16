"""Alerts to the office internal tool (POST /api/notify, Bearer auth).

Every problem is identified by a `key` and alerted ONCE when it appears, then
again as a "resolved" message when it clears - not on every scheduled run. An
unresolved problem is repeated at most once a day as a reminder. Open problems
are remembered in system_config.alert_state so GitHub Actions runs (which share
no memory) agree on what has already been reported.

Alerting is best-effort by design: no token, an unreachable tool or a Supabase
hiccup is printed and swallowed, never raised, so a broken alert channel can
never stop a pipeline run.

Config (env, never committed):
  INTERNAL_ALERT_TOKEN  bearer secret for the tool (alerts are skipped without it)
  INTERNAL_ALERT_URL    optional override of the endpoint below

NOTE: this is a copy of src/alerts.py that differs only in the two state
functions (article-pipeline runs as its own cwd-relative package) - keep in sync.
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

DEFAULT_URL = "https://workspace.admexo.us/api/notify"
STATE_KEY = "alert_state"
REMINDER = timedelta(hours=24)
TITLE_PREFIX = "[SEO Funnel] "
_RANK = {"warn": 1, "critical": 2}

# Balances: warn while a run is still affordable but close to not being.
LOW_BALANCE_FACTOR = 2.0
EXPIRY_WARN_DAYS = 7
EXPIRY_CRITICAL_DAYS = 2


# --- state backend (the only part that differs between the two copies) ------

def _load_state() -> dict:
    from clients import db_client as db
    return db.get_config(STATE_KEY, {}) or {}


def _save_state(state: dict) -> None:
    from clients import db_client as db
    db.set_config(STATE_KEY, state)


# --- sending ------------------------------------------------------------------

def send(title: str, message: str) -> bool:
    token = (os.environ.get("INTERNAL_ALERT_TOKEN") or "").strip()
    if not token:
        print(f"alerts: INTERNAL_ALERT_TOKEN not set - not sending: {title}", file=sys.stderr)
        return False
    url = (os.environ.get("INTERNAL_ALERT_URL") or DEFAULT_URL).strip()
    try:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"title": TITLE_PREFIX + title, "message": message},
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 - alerting must never break a run
        print(f"alerts: could not send {title!r}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_load() -> Optional[dict]:
    try:
        return _load_state()
    except Exception as e:  # noqa: BLE001
        print(f"alerts: could not read alert state: {e}", file=sys.stderr)
        return None


def _safe_save(state: dict) -> None:
    try:
        _save_state(state)
    except Exception as e:  # noqa: BLE001
        print(f"alerts: could not save alert state: {e}", file=sys.stderr)


# --- raise / resolve ----------------------------------------------------------

def raise_issue(key: str, title: str, message: str, level: str = "critical") -> bool:
    """Alert that `key` is a problem. Sends on first sight, when it gets worse
    (warn -> critical), and as a daily reminder; otherwise stays quiet."""
    state = _safe_load()
    if state is None:
        return False
    prev = state.get(key)
    now = _now()
    if prev:
        worse = _RANK.get(level, 0) > _RANK.get(prev.get("level"), 0)
        try:
            due = now - datetime.fromisoformat(prev["last_sent"]) >= REMINDER
        except Exception:  # noqa: BLE001
            due = True
        if not (worse or due):
            if prev.get("level") != level:  # de-escalated: remember, don't re-alert
                prev["level"] = level
                _safe_save(state)
            return False
    icon = "CRITICAL" if level == "critical" else "WARNING"
    if not send(f"{icon}: {title}", message):
        return False
    state[key] = {
        "title": title, "level": level, "last_sent": now.isoformat(),
        "since": (prev or {}).get("since", now.isoformat()),
    }
    _safe_save(state)
    return True


def resolve_issue(key: str) -> bool:
    """Tell the tool a previously-alerted problem is gone. No-op if `key` was
    never alerted."""
    state = _safe_load()
    if not state or key not in state:
        return False
    prev = state.pop(key)
    send(f"Resolved: {prev.get('title', key)}", f"No longer a problem (first reported {prev.get('since', '?')}).")
    _safe_save(state)
    return True


def sync_issues(prefix: str, issues: dict[str, tuple[str, str, str]]) -> None:
    """issues: {key: (title, message, level)} for everything wrong right now
    within `prefix`. Raises each; resolves any open `prefix` alert not in it."""
    for key, (title, message, level) in issues.items():
        raise_issue(key, title, message, level)
    state = _safe_load() or {}
    for key in [k for k in state if k.startswith(prefix) and k not in issues]:
        resolve_issue(key)


# --- provider balances ----------------------------------------------------------

def _days_until(expires) -> Optional[float]:
    if expires in (None, ""):
        return None
    try:
        if isinstance(expires, (int, float)):
            dt = datetime.fromtimestamp(float(expires), tz=timezone.utc)
        else:
            s = str(expires).strip()
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                dt = datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return (dt - _now()).total_seconds() / 86400
    except Exception:  # noqa: BLE001
        return None


def balance_issues(providers: list[dict], scope: str) -> dict[str, tuple[str, str, str]]:
    """Turn provider balance dicts (ProviderBalance.model_dump()) into issues.
    One issue per provider, worst problem wins."""
    issues: dict[str, tuple[str, str, str]] = {}
    for b in providers:
        name = b["provider"]
        unit = b.get("unit") or "credits"
        remaining, required = b.get("remaining"), b.get("required")
        fmt = (lambda v: f"${v:,.2f}") if unit == "USD" else (lambda v: f"{v:,.0f} credits")
        level, problems = None, []

        if b.get("checked", True) and not b.get("ok", True):
            level = "critical"
            problems.append(b.get("detail") or "balance check failed")
        elif b.get("checked", True) and remaining is not None and required and remaining < required * LOW_BALANCE_FACTOR:
            level = "warn"
            problems.append(f"running low: {fmt(remaining)} left, a run needs ~{fmt(required)}")

        days = _days_until(b.get("expires_at"))
        if days is not None and days <= EXPIRY_WARN_DAYS and (remaining or 0) > 0:
            problems.append(
                f"{fmt(remaining)} expire in {max(days, 0):.0f} day(s) ({str(b.get('expires_at'))[:10]})"
                if remaining is not None else f"expires in {max(days, 0):.0f} day(s)"
            )
            if days <= EXPIRY_CRITICAL_DAYS:
                level = "critical"
            elif level is None:
                level = "warn"

        if level:
            issues[f"{scope}:balance:{name}"] = (f"{name} balance", f"{name}: " + "; ".join(problems), level)
    return issues


def sync_balances(providers: list[dict], scope: str) -> None:
    """Alert on low/empty/expiring provider balances and resolve cleared ones.
    A provider whose balance endpoint was unreachable is left as it was."""
    reachable = [p for p in providers if p.get("checked", True)]
    issues = balance_issues(reachable, scope)
    prefix = f"{scope}:balance:"
    for key, (title, message, level) in issues.items():
        raise_issue(key, title, message, level)
    state = _safe_load() or {}
    seen = {p["provider"] for p in reachable}
    for key in [k for k in state if k.startswith(prefix) and k not in issues and k[len(prefix):] in seen]:
        resolve_issue(key)
