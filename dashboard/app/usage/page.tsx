"use client";
import { useEffect, useState } from "react";
import { api, UsageEndpoint, UsageTotal } from "@/lib/api";

const money = (v: number | null) => (v == null ? "—" : `$${v.toFixed(4)}`);
const num = (v: number | null | undefined) =>
  v == null ? "—" : Number(v).toLocaleString();

export default function UsagePage() {
  const [totals, setTotals] = useState<UsageTotal[]>([]);
  const [byEndpoint, setByEndpoint] = useState<UsageEndpoint[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.usage()
      .then((d) => { setTotals(d.totals); setByEndpoint(d.by_endpoint); })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const grandTotal = totals.reduce((a, t) => a + (t.total_cost_usd ?? 0), 0);
  const perRun = totals.reduce((a, t) => a + (t.avg_cost_per_run_usd ?? 0), 0);

  return (
    <>
      <h1>API usage</h1>
      <p className="sub">
        Credits are recorded per run. Scrappa bills per request, SE Ranking bills
        100 flat for demand validation and 10 per returned keyword for questions,
        and DeepSeek bills by token rather than credits.
      </p>
      {err && <div className="err">{err}</div>}

      <div className="panel">
        <div className="stats-row">
          <div className="stat"><b>{money(grandTotal)}</b><span>total spend (credit APIs)</span></div>
          <div className="stat"><b>{money(perRun)}</b><span>avg cost per run</span></div>
          <div className="stat"><b>{totals[0]?.runs ?? 0}</b><span>runs recorded</span></div>
        </div>
      </div>

      <div className="panel">
        <h2>By provider</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Provider</th><th>Runs</th><th>Calls</th>
                <th className="nowrap">Avg calls/run</th><th>Credits</th>
                <th className="nowrap">Avg credits/run</th><th>Tokens</th>
                <th className="nowrap">Total cost</th><th className="nowrap">Cost/run</th>
              </tr>
            </thead>
            <tbody>
              {totals.map((t) => (
                <tr key={t.provider}>
                  <td><b>{t.provider}</b></td>
                  <td>{t.runs}</td>
                  <td>{num(t.total_calls)}</td>
                  <td>{num(t.avg_calls_per_run)}</td>
                  <td>{t.provider === "deepseek" ? "—" : num(t.total_credits)}</td>
                  <td>{t.provider === "deepseek" ? "—" : num(t.avg_credits_per_run)}</td>
                  <td>{t.total_tokens ? num(t.total_tokens) : "—"}</td>
                  <td>{money(t.total_cost_usd)}</td>
                  <td>{money(t.avg_cost_per_run_usd)}</td>
                </tr>
              ))}
              {!loading && totals.length === 0 && (
                <tr><td colSpan={9} className="empty">
                  No usage recorded yet — run the pipeline once and it will appear here
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <h2>By endpoint</h2>
        <p className="muted" style={{ marginTop: -6 }}>
          Where the credits actually go — useful for deciding which cost dial to turn.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Provider</th><th>Endpoint</th><th>Calls</th>
                <th>Credits</th><th className="nowrap">Credits/run</th><th>Tokens</th>
              </tr>
            </thead>
            <tbody>
              {byEndpoint.map((e) => (
                <tr key={`${e.provider}-${e.endpoint}`}>
                  <td className="muted">{e.provider}</td>
                  <td>{e.endpoint}</td>
                  <td>{num(e.total_calls)}</td>
                  <td>{e.provider === "deepseek" ? "—" : num(e.total_credits)}</td>
                  <td>{e.provider === "deepseek" || !e.runs ? "—"
                       : num(Math.round(Number(e.total_credits) / e.runs))}</td>
                  <td>{e.total_tokens ? num(e.total_tokens) : "—"}</td>
                </tr>
              ))}
              {!loading && byEndpoint.length === 0 && (
                <tr><td colSpan={6} className="empty">No usage recorded yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
