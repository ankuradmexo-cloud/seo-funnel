"use client";
import { useEffect, useState } from "react";
import { api, SiteOverview } from "@/lib/api";
import { StatusBadge } from "@/components/ui";

type WpDraft = {
  domain: string;
  wp_base_url: string;
  wp_username: string;
  wp_app_password: string;
  seo_plugin: "yoast" | "rankmath" | "none";
  articles_per_day: string;
};

function wpDraftFrom(w: SiteOverview): WpDraft {
  return {
    domain: w.domain ?? "",
    wp_base_url: w.wp_base_url ?? "",
    wp_username: w.wp_username ?? "",
    wp_app_password: w.wp_app_password ?? "",
    seo_plugin: w.seo_plugin ?? "none",
    articles_per_day: String(w.articles_per_day ?? 2),
  };
}

export default function SettingsPage() {
  const [websites, setWebsites] = useState<SiteOverview[]>([]);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [wpDrafts, setWpDrafts] = useState<Record<number, WpDraft>>({});
  const [saving, setSaving] = useState<number | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [articleAutomation, setArticleAutomationState] = useState<boolean | null>(null);
  const [serankingKeyState, setSerankingKeyState] = useState<{ override_set: boolean; masked: string | null } | null>(null);
  const [serankingKeyInput, setSerankingKeyInput] = useState("");
  const [serankingKeySaving, setSerankingKeySaving] = useState(false);

  const load = () => {
    api.websites().then((ws) => {
      setWebsites(ws);
      setDrafts(Object.fromEntries(ws.map((w) => [w.website_id, w.category])));
      setWpDrafts(Object.fromEntries(ws.map((w) => [w.website_id, wpDraftFrom(w)])));
    }).catch((e) => setErr(String(e)));
    api.articleAutomation().then((r) => setArticleAutomationState(r.enabled)).catch(() => {});
    api.getSerankingApiKey().then(setSerankingKeyState).catch(() => {});
  };

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

  async function toggleArticleAutomation(w: SiteOverview) {
    setSaving(w.website_id); setErr(null);
    try {
      await api.updateWebsite(w.website_id, { article_automation_enabled: !w.article_automation_enabled });
      await load();
    } catch (e) { setErr(String(e)); } finally { setSaving(null); }
  }

  async function saveWordpress(id: number) {
    setSaving(id); setErr(null);
    try {
      const d = wpDrafts[id];
      await api.updateWebsite(id, {
        domain: d.domain,
        wp_base_url: d.wp_base_url,
        wp_username: d.wp_username,
        wp_app_password: d.wp_app_password,
        seo_plugin: d.seo_plugin,
        articles_per_day: parseInt(d.articles_per_day, 10) || 1,
      });
      await load();
      setSaved(id);
      setTimeout(() => setSaved(null), 2500);
    } catch (e) { setErr(String(e)); } finally { setSaving(null); }
  }

  async function toggleGlobalArticleAutomation() {
    if (articleAutomation === null) return;
    try {
      const r = await api.setArticleAutomation(!articleAutomation);
      setArticleAutomationState(r.enabled);
    } catch (e) { setErr(String(e)); }
  }

  async function saveSerankingKey() {
    if (!serankingKeyInput.trim()) return;
    setSerankingKeySaving(true); setErr(null);
    try {
      const r = await api.setSerankingApiKey(serankingKeyInput.trim());
      setSerankingKeyState(r);
      setSerankingKeyInput("");
    } catch (e) { setErr(String(e)); } finally { setSerankingKeySaving(false); }
  }

  async function clearSerankingKey() {
    setSerankingKeySaving(true); setErr(null);
    try {
      const r = await api.clearSerankingApiKey();
      setSerankingKeyState(r);
    } catch (e) { setErr(String(e)); } finally { setSerankingKeySaving(false); }
  }

  function wpDirty(w: SiteOverview): boolean {
    const d = wpDrafts[w.website_id];
    if (!d) return false;
    return (
      d.domain !== (w.domain ?? "") ||
      d.wp_base_url !== (w.wp_base_url ?? "") ||
      d.wp_username !== (w.wp_username ?? "") ||
      d.wp_app_password !== (w.wp_app_password ?? "") ||
      d.seo_plugin !== (w.seo_plugin ?? "none") ||
      d.articles_per_day !== String(w.articles_per_day ?? 2)
    );
  }

  return (
    <>
      <h1>Settings</h1>
      <p className="sub">
        Category drives what niche discovery generates next — it only affects future
        niches, existing ones are unchanged.
      </p>
      {err && <div className="err">{err}</div>}

      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="spread">
          <div>
            <h2 style={{ margin: 0 }}>Article automation (all sites)</h2>
            <p className="muted" style={{ margin: "4px 0 0" }}>
              Global kill switch for the article pipeline&apos;s daily scheduler — independent
              of keyword shortlisting&apos;s own pause below/on each site.
            </p>
          </div>
          <button
            onClick={toggleGlobalArticleAutomation}
            disabled={articleAutomation === null}
            className={articleAutomation ? "danger" : "primary"}
          >
            {articleAutomation === null ? "…" : articleAutomation ? "Pause article automation" : "Resume article automation"}
          </button>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 20 }}>
        <h2 style={{ margin: 0 }}>SE Ranking API key</h2>
        <p className="muted" style={{ margin: "4px 0 12px" }}>
          Overrides the SERANKING_API_KEY environment variable without touching Render/GitHub
          secrets or redeploying - picked up fresh on the very next run. Clearing it falls back
          to whatever&apos;s set in this service&apos;s own environment.
        </p>
        <div className="row" style={{ marginBottom: 10 }}>
          <span className="muted">
            {serankingKeyState === null
              ? "…"
              : serankingKeyState.override_set
                ? <>Override active: <b>{serankingKeyState.masked}</b></>
                : "Using the environment variable (no override set)"}
          </span>
          {serankingKeyState?.override_set && (
            <button onClick={clearSerankingKey} disabled={serankingKeySaving} className="danger">
              Clear override
            </button>
          )}
        </div>
        <div className="row">
          <input
            type="password"
            placeholder="New SE Ranking API key"
            value={serankingKeyInput}
            onChange={(e) => setSerankingKeyInput(e.target.value)}
            style={{ flex: 1 }}
          />
          <button onClick={saveSerankingKey} disabled={serankingKeySaving || !serankingKeyInput.trim()} className="primary">
            {serankingKeySaving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {websites.map((w) => {
        const dirty = drafts[w.website_id] !== w.category;
        const wp = wpDrafts[w.website_id];
        return (
          <div className="panel" key={w.website_id}>
            <div className="spread" style={{ marginBottom: 12 }}>
              <div className="row">
                <h2 style={{ margin: 0 }}>{w.name}</h2>
                <StatusBadge status={w.active ? "active" : "exhausted"} />
              </div>
              <button onClick={() => toggleActive(w)} disabled={saving === w.website_id}
                      className={w.active ? "danger" : ""}>
                {w.active ? "Pause keyword shortlisting" : "Resume keyword shortlisting"}
              </button>
            </div>

            <label className="muted" style={{ display: "block", marginBottom: 6 }}>Category</label>
            <textarea
              value={drafts[w.website_id] ?? ""}
              onChange={(e) => setDrafts({ ...drafts, [w.website_id]: e.target.value })}
            />
            <div className="row" style={{ marginTop: 10, marginBottom: 24 }}>
              <button className="primary" onClick={() => saveCategory(w.website_id)}
                      disabled={!dirty || saving === w.website_id}>
                {saving === w.website_id ? "Saving…" : "Save category"}
              </button>
              {dirty && <span className="muted">Unsaved changes</span>}
              {saved === w.website_id && <span className="badge b-green">Saved</span>}
            </div>

            <div className="spread" style={{ marginBottom: 12, borderTop: "1px solid #e5e5e0", paddingTop: 16 }}>
              <h3 style={{ margin: 0 }}>Article publishing (WordPress)</h3>
              <button onClick={() => toggleArticleAutomation(w)} disabled={saving === w.website_id}
                      className={w.article_automation_enabled ? "danger" : ""}>
                {w.article_automation_enabled ? "Pause articles for this site" : "Resume articles for this site"}
              </button>
            </div>

            {wp && (
              <>
                <div className="grid2">
                  <div>
                    <label className="muted">Domain</label>
                    <input value={wp.domain} onChange={(e) => setWpDrafts({ ...wpDrafts, [w.website_id]: { ...wp, domain: e.target.value } })} />
                  </div>
                  <div>
                    <label className="muted">WordPress base URL</label>
                    <input value={wp.wp_base_url} placeholder="https://example.com"
                           onChange={(e) => setWpDrafts({ ...wpDrafts, [w.website_id]: { ...wp, wp_base_url: e.target.value } })} />
                  </div>
                  <div>
                    <label className="muted">WP username</label>
                    <input value={wp.wp_username} onChange={(e) => setWpDrafts({ ...wpDrafts, [w.website_id]: { ...wp, wp_username: e.target.value } })} />
                  </div>
                  <div>
                    <label className="muted">Application password</label>
                    <input type="password" value={wp.wp_app_password}
                           onChange={(e) => setWpDrafts({ ...wpDrafts, [w.website_id]: { ...wp, wp_app_password: e.target.value } })} />
                  </div>
                  <div>
                    <label className="muted">SEO plugin</label>
                    <select value={wp.seo_plugin}
                            onChange={(e) => setWpDrafts({ ...wpDrafts, [w.website_id]: { ...wp, seo_plugin: e.target.value as WpDraft["seo_plugin"] } })}>
                      <option value="none">None (native excerpt only)</option>
                      <option value="yoast">Yoast SEO</option>
                      <option value="rankmath">RankMath</option>
                    </select>
                  </div>
                  <div>
                    <label className="muted">Articles per day</label>
                    <input type="number" min={0} value={wp.articles_per_day}
                           onChange={(e) => setWpDrafts({ ...wpDrafts, [w.website_id]: { ...wp, articles_per_day: e.target.value } })} />
                  </div>
                </div>
                <div className="row" style={{ marginTop: 10 }}>
                  <button className="primary" onClick={() => saveWordpress(w.website_id)}
                          disabled={!wpDirty(w) || saving === w.website_id}>
                    {saving === w.website_id ? "Saving…" : "Save WordPress settings"}
                  </button>
                  {wpDirty(w) && <span className="muted">Unsaved changes</span>}
                  {saved === w.website_id && <span className="badge b-green">Saved</span>}
                </div>
              </>
            )}
          </div>
        );
      })}
      {websites.length === 0 && <div className="panel empty">No websites configured</div>}
    </>
  );
}
