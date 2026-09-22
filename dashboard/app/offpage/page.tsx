"use client";
import { useEffect, useState } from "react";
import { api, SiteOverview } from "@/lib/api";
import { WebsitePicker } from "@/components/ui";
import OffPageOpportunities from "@/components/OffPageOpportunities";

export default function OffPagePage() {
  const [websites, setWebsites] = useState<SiteOverview[]>([]);
  const [site, setSite] = useState<number | "">("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.websites()
      .then((ws) => {
        setWebsites(ws);
        if (ws.length > 0) setSite(ws[0].website_id);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  return (
    <>
      <h1>Off-Page SEO</h1>
      <p className="sub">
        Research + drafted outreach for backlinks, directories, and guest posts. Nothing here
        sends an email or posts anywhere on its own - every draft is for your team to review and
        send/post themselves.
      </p>
      {err && <div className="err">{err}</div>}

      <div className="panel">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <WebsitePicker websites={websites} value={site} onChange={setSite} allowAll={false} />
        </div>
      </div>

      {site !== "" && (
        <div className="panel">
          <OffPageOpportunities websiteId={site} />
        </div>
      )}
    </>
  );
}
