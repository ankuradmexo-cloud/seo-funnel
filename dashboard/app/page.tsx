"use client";
import { useEffect, useState } from "react";
import { api, CreditStatus, Overview } from "@/lib/api";
import { Progress, Stat, StatusBadge, fmt } from "@/components/ui";

export default function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [credits, setCredits] = useState<CreditStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => api.overview().then(setData).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
    // Balance reads are free account-metadata calls - no credits are spent
    // showing this, and a failed read must not blank the page.
    api.credits().then(setCredits).catch(() => setCredits(null));
  }, []);

  async function toggleAutomation() {
    if (!data) return;
    setBusy(true);
    try {
      await api.setAutomation(!data.automation_enabled);
      await load();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  if (err) return <div className="err">{err}</div>;
  if (!data) return <p className="muted">Loading…</p>;

  const totals = data.websites.reduce(
    (a, w) => ({
      awaiting: a.awaiting + w.awaiting_publish,
      queued: a.queued + w.in_progress,
      published: a.published + w.published,
      today: a.today + w.shortlisted_last_24h,
    }),
    { awaiting: 0, queued: 0, published: 0, today: 0 }
  );
  const targetAll = data.daily_target_per_site * data.websites.filter((w) => w.active).length;

  return (
    <>
      <h1>Overview</h1>
      <p className="sub">All websites at a glance.</p>

      {credits && !credits.ok && (
        <div className="panel blocked">
          <h2 style={{ marginBottom: 4 }}>Credits not sufficient</h2>
          <p className="muted" style={{ margin: "0 0 12px" }}>
            Runs will not start until this is topped up. The check happens before
            any provider is called, so nothing is being spent in the meantime.
          </p>
          <div className="credit-rows">
            {credits.providers.filter((p) => !p.ok).map((p) => (
              <div key={p.provider} className="credit-row">
                <b>{p.provider}</b>
                <span>
                  {p.unit === "USD" ? `$${(p.remaining ?? 0).toFixed(2)}` :
                    `${(p.remaining ?? 0).toLocaleString()} credits`} left
                  {p.required != null && ` · needs ~${p.unit === "USD"
                    ? `$${p.required.toFixed(2)}` : p.required.toLocaleString()}`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="panel spread">
        <div>
          <h2 style={{ marginBottom: 4 }}>Automation</h2>
          <span className="muted">
            {data.automation_enabled
              ? "Scheduled runs are active."
              : "Paused — scheduled runs exit immediately without spending API credits."}
          </span>
        </div>
        <div className="row">
          {credits?.ok && (
            <span className="credit-strip">
              {credits.providers.map((p) => (
                <span key={p.provider} title={p.detail ?? ""}>
                  {p.provider} {p.unit === "USD"
                    ? `$${(p.remaining ?? 0).toFixed(2)}`
                    : (p.remaining ?? 0).toLocaleString()}
                </span>
              ))}
            </span>
          )}
          <StatusBadge status={data.automation_enabled ? "active" : "exhausted"} />
          <button className={data.automation_enabled ? "danger" : "primary"}
                  onClick={toggleAutomation} disabled={busy}>
            {busy ? "…" : data.automation_enabled ? "Pause automation" : "Resume automation"}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="stats-row">
          <Stat label="shortlisted last 24h" value={`${totals.today} / ${targetAll}`} />
          <Stat label="awaiting publish" value={totals.awaiting} />
          <Stat label="picked up by n8n" value={totals.queued} />
          <Stat label="published" value={totals.published} />
          <Stat label="active websites" value={data.websites.filter((w) => w.active).length} />
        </div>
      </div>

      <div className="grid">
        {data.websites.map((w) => (
          <div className="panel" key={w.website_id}>
            <div className="spread" style={{ marginBottom: 10 }}>
              <h2 style={{ margin: 0 }}>{w.name}</h2>
              <StatusBadge status={w.active ? "active" : "exhausted"} />
            </div>

            <div style={{ marginBottom: 12 }}>
              <span className="muted">Today&apos;s target</span>
              <Progress value={w.shortlisted_last_24h} target={w.daily_target} />
            </div>

            <div className="stats-row" style={{ marginBottom: 12 }}>
              <Stat label="awaiting publish" value={w.awaiting_publish} />
              <Stat label="queued" value={w.in_progress} />
              <Stat label="published" value={w.published} />
              <Stat label="active niches" value={w.niches.active} />
            </div>

            <div className="muted" style={{ fontSize: 12.5 }}>
              {w.last_run ? (
                <>
                  Last run <StatusBadge status={w.last_run.status} />{" "}
                  {fmt(w.last_run.finished_at ?? w.last_run.started_at)} ·{" "}
                  {w.last_run.candidates_found} candidates · {w.last_run.shortlisted_count} approved
                  {w.last_run.error_message && (
                    <div style={{ marginTop: 6 }} className="badge b-amber">{w.last_run.error_message}</div>
                  )}
                </>
              ) : "No runs yet"}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
