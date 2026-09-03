"use client";
import { useEffect, useState } from "react";
import { api, SiteOverview } from "@/lib/api";
import { StatusBadge } from "@/components/ui";

export default function SettingsPage() {
  const [websites, setWebsites] = useState<SiteOverview[]>([]);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState<number | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () =>
    api.websites().then((ws) => {
      setWebsites(ws);
      setDrafts(Object.fromEntries(ws.map((w) => [w.website_id, w.category])));
    }).catch((e) => setErr(String(e)));

  useEffect(() => { load(); }, []);

  async function saveCategory(id: number) {
    setSaving(id); setErr(null);
    try {
      await api.updateWebsite(id, { category: drafts[id] });
      await load();
      setSaved(id);
      setTimeout(() => setSaved(null), 2500);
    } catch (e) { setErr(String(e)); } finally { setSaving(null); }
  }

  async function toggleActive(w: SiteOverview) {
    setSaving(w.website_id); setErr(null);
    try {
      await api.updateWebsite(w.website_id, { active: !w.active });
      await load();
    } catch (e) { setErr(String(e)); } finally { setSaving(null); }
  }

  return (
    <>
      <h1>Settings</h1>
      <p className="sub">
        Category drives what niche discovery generates next — it only affects future
        niches, existing ones are unchanged.
      </p>
      {err && <div className="err">{err}</div>}

      {websites.map((w) => {
        const dirty = drafts[w.website_id] !== w.category;
        return (
          <div className="panel" key={w.website_id}>
            <div className="spread" style={{ marginBottom: 12 }}>
              <div className="row">
                <h2 style={{ margin: 0 }}>{w.name}</h2>
                <StatusBadge status={w.active ? "active" : "exhausted"} />
              </div>
              <button onClick={() => toggleActive(w)} disabled={saving === w.website_id}
                      className={w.active ? "danger" : ""}>
                {w.active ? "Pause this site" : "Resume this site"}
              </button>
            </div>

            <label className="muted" style={{ display: "block", marginBottom: 6 }}>Category</label>
            <textarea
              value={drafts[w.website_id] ?? ""}
              onChange={(e) => setDrafts({ ...drafts, [w.website_id]: e.target.value })}
            />
            <div className="row" style={{ marginTop: 10 }}>
              <button className="primary" onClick={() => saveCategory(w.website_id)}
                      disabled={!dirty || saving === w.website_id}>
                {saving === w.website_id ? "Saving…" : "Save category"}
              </button>
              {dirty && <span className="muted">Unsaved changes</span>}
              {saved === w.website_id && <span className="badge b-green">Saved</span>}
            </div>
          </div>
        );
      })}
      {websites.length === 0 && <div className="panel empty">No websites configured</div>}
    </>
  );
}
