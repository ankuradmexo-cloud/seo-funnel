"use client";
import { useEffect, useState } from "react";
import { api, Niche, SiteOverview } from "@/lib/api";
import { StatusBadge, WebsitePicker, fmt } from "@/components/ui";

export default function NichesPage() {
  const [websites, setWebsites] = useState<SiteOverview[]>([]);
  const [site, setSite] = useState<number | "">("");
  const [rows, setRows] = useState<Niche[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.websites().then((ws) => {
      setWebsites(ws);
      if (ws.length && site === "") setSite(ws[0].website_id);
    }).catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    if (site === "") return;
    api.niches(site).then(setRows).catch((e) => setErr(String(e)));
  }, [site]);

  return (
    <>
      <h1>Niches</h1>
      <p className="sub">
        Rotation state. Runs pick whichever active niche has waited longest;
        a niche is retired after two consecutive runs with no approved keyword.
      </p>
      {err && <div className="err">{err}</div>}

      <div className="panel">
        <WebsitePicker websites={websites} value={site} onChange={setSite} allowAll={false} />
      </div>

      <div className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Niche</th><th>Status</th><th>Source</th>
                <th className="nowrap">Times processed</th><th className="nowrap">Last processed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((n) => (
                <tr key={n.niche_id}>
                  <td>{n.name}</td>
                  <td><StatusBadge status={n.status} /></td>
                  <td className="muted">{n.source}</td>
                  <td>{n.times_processed}</td>
                  <td className="muted nowrap">{fmt(n.last_processed_at)}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={5} className="empty">No niches yet — the first run will generate them</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
