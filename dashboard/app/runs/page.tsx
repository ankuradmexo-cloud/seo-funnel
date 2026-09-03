"use client";
import { useEffect, useState } from "react";
import { api, Run, SiteOverview } from "@/lib/api";
import { StatusBadge, WebsitePicker, fmt } from "@/components/ui";

function duration(r: Run) {
  if (!r.finished_at) return "running…";
  const secs = (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000;
  return secs < 90 ? `${Math.round(secs)}s` : `${Math.round(secs / 60)}m`;
}

export default function RunsPage() {
  const [websites, setWebsites] = useState<SiteOverview[]>([]);
  const [site, setSite] = useState<number | "">("");
  const [rows, setRows] = useState<Run[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.websites().then(setWebsites).catch((e) => setErr(String(e))); }, []);
  useEffect(() => {
    api.runs(site || undefined).then(setRows).catch((e) => setErr(String(e)));
  }, [site]);

  const names = new Map(websites.map((w) => [w.website_id, w.name]));

  return (
    <>
      <h1>Runs</h1>
      <p className="sub">Every pipeline execution, including the ones that produced nothing.</p>
      {err && <div className="err">{err}</div>}

      <div className="panel">
        <WebsitePicker websites={websites} value={site} onChange={setSite} />
      </div>

      <div className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Run</th><th>Website</th><th>Status</th>
                <th className="nowrap">Candidates</th><th className="nowrap">Approved</th>
                <th>Duration</th><th className="nowrap">Started</th><th>Note</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.run_id}>
                  <td className="nowrap">#{r.run_id}</td>
                  <td className="muted">{names.get(r.website_id) ?? r.website_id}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td>{r.candidates_found}</td>
                  <td><b>{r.shortlisted_count}</b></td>
                  <td className="muted">{duration(r)}</td>
                  <td className="muted nowrap">{fmt(r.started_at)}</td>
                  <td className="muted" style={{ maxWidth: 320 }}>{r.error_message ?? "—"}</td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={8} className="empty">No runs yet</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
