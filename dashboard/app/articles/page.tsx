"use client";
import { useEffect, useState } from "react";
import { api, PublishedArticle, SiteOverview } from "@/lib/api";
import { StatusBadge, WebsitePicker, fmt } from "@/components/ui";
import BacklinkCandidatesPanel from "@/components/BacklinkCandidates";

export default function ArticlesPage() {
  const [websites, setWebsites] = useState<SiteOverview[]>([]);
  const [site, setSite] = useState<number | "">("");
  const [rows, setRows] = useState<PublishedArticle[]>([]);
  const [open, setOpen] = useState<PublishedArticle | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { api.websites().then(setWebsites).catch((e) => setErr(String(e))); }, []);

  useEffect(() => {
    setLoading(true);
    api.articles(site || undefined).then(setRows).catch((e) => setErr(String(e))).finally(() => setLoading(false));
  }, [site]);

  return (
    <>
      <h1>Articles</h1>
      <p className="sub">Every article the pipeline has actually published to WordPress, with its quality gate verdict and real cost.</p>
      {err && <div className="err">{err}</div>}

      <div className="panel">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <WebsitePicker websites={websites} value={site} onChange={setSite} />
          <span className="muted">{loading ? "Loading…" : `${rows.length} article(s)`}</span>
        </div>
      </div>

      <div className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Title</th><th>Keyword</th><th>Quality gate</th>
                <th className="nowrap">Cost</th><th className="nowrap">Published</th><th>Link</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.article_id} onClick={() => setOpen(a)} style={{ cursor: "pointer" }}>
                  <td>{a.title}</td>
                  <td className="muted">{a.keywords?.keyword ?? "—"}</td>
                  <td>{a.quality_gate_verdict ? <StatusBadge status={a.quality_gate_verdict} /> : <span className="muted">—</span>}</td>
                  <td>{a.cost_usd != null ? `$${a.cost_usd.toFixed(4)}` : "—"}</td>
                  <td className="muted nowrap">{fmt(a.published_at)}</td>
                  <td>
                    {a.wp_post_url ? (
                      <a href={a.wp_post_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>View live →</a>
                    ) : "—"}
                  </td>
                </tr>
              ))}
              {!loading && rows.length === 0 && (
                <tr><td colSpan={6} className="empty">No articles published yet</td></tr>
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
              <h2 style={{ margin: 0 }}>{open.title}</h2>
              <button onClick={() => setOpen(null)}>Close</button>
            </div>

            <h2>Details</h2>
            <div className="row-label"><span>Keyword</span><span>{open.keywords?.keyword ?? "—"}</span></div>
            <div className="row-label"><span>Quality gate</span><span>{open.quality_gate_verdict ? <StatusBadge status={open.quality_gate_verdict} /> : "—"}</span></div>
            <div className="row-label"><span>Cost</span><span>{open.cost_usd != null ? `$${open.cost_usd.toFixed(4)}` : "—"}</span></div>
            <div className="row-label"><span>Published</span><span>{fmt(open.published_at)}</span></div>
            <div className="row-label">
              <span>WordPress</span>
              <span>{open.wp_post_url ? <a href={open.wp_post_url} target="_blank" rel="noreferrer">View live →</a> : "—"}</span>
            </div>
            {open.hero_image_url && (
              <img src={open.hero_image_url} alt="" style={{ width: "100%", borderRadius: 10, margin: "12px 0" }} />
            )}

            {open.keyword_id != null && <BacklinkCandidatesPanel keywordId={open.keyword_id} />}
          </aside>
        </>
      )}
    </>
  );
}
