from datetime import datetime, timedelta, timezone

from src import alerts


def _setup(monkeypatch):
    state, sent = {}, []
    monkeypatch.setattr(alerts, "_load_state", lambda: state)
    monkeypatch.setattr(alerts, "_save_state", lambda s: None)
    monkeypatch.setattr(alerts, "send", lambda t, m: sent.append(t) or True)
    return state, sent


def test_alerts_once_then_resolves(monkeypatch):
    state, sent = _setup(monkeypatch)
    assert alerts.raise_issue("k", "scrappa balance", "empty") is True
    assert alerts.raise_issue("k", "scrappa balance", "empty") is False  # deduped
    assert len(sent) == 1
    assert alerts.resolve_issue("k") is True
    assert sent[-1].startswith("Resolved")
    assert alerts.resolve_issue("k") is False  # nothing open


def test_reminder_after_a_day_and_escalation(monkeypatch):
    state, sent = _setup(monkeypatch)
    alerts.raise_issue("k", "t", "m", "warn")
    assert alerts.raise_issue("k", "t", "m", "warn") is False
    assert alerts.raise_issue("k", "t", "m", "critical") is True  # got worse
    state["k"]["last_sent"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert alerts.raise_issue("k", "t", "m", "critical") is True  # daily reminder


def test_balance_issues():
    ok = {"provider": "scrappa", "ok": True, "remaining": 15788, "required": 530, "unit": "credits"}
    empty = {"provider": "deepseek", "ok": False, "remaining": 0.0, "required": 0.1, "unit": "USD",
             "detail": "$0.00 left, below the $0.10 floor"}
    low = {"provider": "x", "ok": True, "remaining": 900, "required": 530, "unit": "credits"}
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    expiring = {"provider": "seranking", "ok": True, "remaining": 100000, "required": 4600,
                "unit": "credits", "expires_at": soon}
    issues = alerts.balance_issues([ok, empty, low, expiring], "keyword")
    assert "keyword:balance:scrappa" not in issues
    assert issues["keyword:balance:deepseek"][2] == "critical"
    assert issues["keyword:balance:x"][2] == "warn"
    assert issues["keyword:balance:seranking"][2] == "critical"


def test_no_token_is_silent(monkeypatch):
    monkeypatch.delenv("INTERNAL_ALERT_TOKEN", raising=False)
    assert alerts.send("t", "m") is False
