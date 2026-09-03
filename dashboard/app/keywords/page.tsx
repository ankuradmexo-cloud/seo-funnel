"use client";
import { useEffect, useState } from "react";
import { api, Keyword, SiteOverview } from "@/lib/api";
import { StatusBadge, WebsitePicker, fmt } from "@/components/ui";

const STATUSES = ["shortlisted", "queued", "published", "judged", "validated", "deduped"];

export default function KeywordsPage() {
  const [websites, setWebsites] = useState<SiteOverview[]>([]);
  const [site, setSite] = useState<number | "">("");
  // Default to the shortlist - approved keywords are the actual product,
  // everything else is pipeline exhaust.
  const [status, setStatus] = useState("shortlisted");
  const [rows, setRows] = useState<Keyword[]>([]);
  const [open, setOpen] = useState<Keyword | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { api.websites().then(setWebsites).catch((e) => setErr(String(e))); }, []);

  useEffect(() => {
    setLoading(true);
    api.keywords({ website_id: site || undefined, status: status || undefined, limit: 200 })
      .then(setRows).catch((e) => setErr(String(e))).finally(() => setLoading(false));
  }, [site, status]);

  return (
    <>
      <h1>Keywords</h1>
      <p className="sub">Approved keywords and where each one sits in the publishing flow.</p>
      {err && <div className="err">{err}</div>}

      <div className="panel">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <WebsitePicker websites={websites} value={site} onChange={setSite} />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <span className="muted">{loading ? "Loading…" : `${rows.length} keywords`}</span>
        </div>
      </div>

      <div className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Keyword</th><th>Niche</th><th>Status</th>
                <th className="nowrap">Volume</th><th className="nowrap">Difficulty</th>
                <th>Score</th><th>Intents</th><th className="nowrap">Updated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((k) => (
                <tr key={k.keyword_id} onClick={() => setOpen(k)} style={{ cursor: "pointer" }}>
                  <td>{k.keyword}</td>
                  <td className="muted">{k.niches?.name ?? "—"}</td>
                  <td><StatusBadge status={k.status} /></td>
                  <td>{k.search_volume ?? "—"}</td>
                  <td>{k.difficulty ?? "—"}</td>
                  <td>{k.judge_score ?? "—"}</td>
                  <td className="muted">{(k.intents ?? []).join(", ") || "—"}</td>
                  <td className="muted nowrap">{fmt(k.last_updated)}</td>
                </tr>
              ))}
              {!loading && rows.length === 0 && (
                <tr><td colSpan={8} className="empty">No keywords match these filters</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {open && (
        <>
          <div className="drawer-backdrop" onClick={() => setOpen(null)} />
          <aside className="drawer">
            <div className="spread">
              <h2 style={{ margin: 0 }}>{open.keyword}</h2>
              <button onClick={() => setOpen(null)}>Close</button>
            </div>

            <h2>Metrics</h2>
            <div className="row-label"><span>Status</span><span><StatusBadge status={open.status} /></span></div>
            <div className="row-label"><span>Niche</span><span>{open.niches?.name ?? "—"}</span></div>
            <div className="row-label"><span>Search volume</span><span>{open.search_volume ?? "—"}</span></div>
            <div className="row-label"><span>Difficulty (0-100)</span><span>{open.difficulty ?? "—"}</span></div>
            <div className="row-label"><span>CPC</span><span>{open.cpc ?? "—"}</span></div>
            <div className="row-label"><span>Competition (0-1)</span><span>{open.competition ?? "—"}</span></div>
            <div className="row-label"><span>Judge score</span><span>{open.judge_score ?? "—"}</span></div>
            <div className="row-label"><span>Intents</span><span>{(open.intents ?? []).join(", ") || "—"}</span></div>
            <div className="row-label"><span>Intent cluster</span><span>{open.intent_cluster ?? "—"}</span></div>
            <div className="row-label"><span>From run</span><span>{open.run_id ? `#${open.run_id}` : "—"}</span></div>

            {open.judge_rationale && (
              <>
                <h2>Why the judge approved it</h2>
                <p className="muted" style={{ marginTop: 0, lineHeight: 1.55 }}>{open.judge_rationale}</p>
              </>
            )}

            {open.history_trend && Object.keys(open.history_trend).length > 0 && (
              <>
                <h2>12-month volume trend</h2>
                <div className="trend">
                  {(() => {
                    const entries = Object.entries(open.history_trend!).sort();
                    const max = Math.max(...entries.map(([, v]) => v), 1);
                    return entries.map(([m, v]) => (
                      <i key={m} title={`${m.slice(0, 7)}: ${v}`}
                         style={{ height: `${Math.max(3, (v / max) * 62)}px` }} />
                    ));
                  })()}
                </div>
                <p className="muted" style={{ fontSize: 12 }}>
                  {Object.keys(open.history_trend).sort()[0]?.slice(0, 7)} &rarr;{" "}
                  {Object.keys(open.history_trend).sort().slice(-1)[0]?.slice(0, 7)}
                </p>
              </>
            )}
          </aside>
        </>
      )}
    </>
  );
}
