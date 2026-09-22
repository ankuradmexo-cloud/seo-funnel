"use client";
import { useEffect, useRef, useState } from "react";
import { api, OffpageChannel, OffpageJob, OffpageOpportunity } from "@/lib/api";

const OPPORTUNITY_STATUSES: OffpageOpportunity["status"][] = ["new", "contacted", "replied", "won", "rejected"];

// Social/forum is intentionally left out of this list - Reddit's Data
// Access Request for this app was rejected, and there's no other real
// data source wired up (Quora has no public search API), so the channel
// has no way to find anything right now. The backend (tools/offpage_research.py,
// the /offpage/social route) is untouched - re-add an entry here if a real
// source becomes available later.
const CHANNELS: { key: OffpageChannel; label: string; triggerLabel: string; blurb: string }[] = [
  {
    key: "directory",
    label: "Directories",
    triggerLabel: "Find directory opportunities",
    blurb: "Real directory/listing sites in this site's categories, with a drafted listing blurb - submission stays manual.",
  },
  {
    key: "guest_post",
    label: "Guest posts",
    triggerLabel: "Find guest post opportunities",
    blurb: "Sites accepting outside contributions in this site's categories, with a drafted pitch email referencing a real published article.",
  },
];

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // clipboard API unavailable - nothing to do, the textarea below is still selectable
        }
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function ChannelPanel({ websiteId, channel }: { websiteId: number; channel: OffpageChannel }) {
  const meta = CHANNELS.find((c) => c.key === channel)!;
  const [job, setJob] = useState<OffpageJob | null>(null);
  const [rows, setRows] = useState<OffpageOpportunity[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [openDraft, setOpenDraft] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setJob(null);
    setErr(null);
    setOpenDraft(null);
    api.offpageOpportunities(websiteId, channel).then(setRows).catch(() => {});
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [websiteId, channel]);

  async function start() {
    setStarting(true);
    setErr(null);
    try {
      const { job_id } = await api.triggerOffpageResearch(websiteId, channel);
      const j = await api.getOffpageJob(job_id);
      setJob(j);
      pollRef.current = setInterval(async () => {
        const polled = await api.getOffpageJob(job_id);
        setJob(polled);
        if (polled.status !== "running") {
          if (pollRef.current) clearInterval(pollRef.current);
          if (polled.status === "success") {
            api.offpageOpportunities(websiteId, channel).then(setRows).catch(() => {});
          }
        }
      }, 5000);
    } catch (e) {
      setErr(String(e));
    } finally {
      setStarting(false);
    }
  }

  async function setStatus(opportunityId: number, status: OffpageOpportunity["status"]) {
    try {
      const updated = await api.setOffpageOpportunityStatus(opportunityId, status);
      setRows((r) => r.map((row) => (row.opportunity_id === opportunityId ? updated : row)));
    } catch (e) {
      setErr(String(e));
    }
  }

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>{meta.blurb}</p>
      {err && <div className="err">{err}</div>}
      <div className="row" style={{ marginBottom: 12 }}>
        <button className="primary" onClick={start} disabled={starting || job?.status === "running"}>
          {job?.status === "running" ? "Running…" : starting ? "Starting…" : meta.triggerLabel}
        </button>
        {job?.status === "failed" && (
          <span className="err" style={{ padding: "4px 10px" }}>Failed: {job.error_message}</span>
        )}
      </div>

      {rows.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Target</th><th>Signal</th><th>Draft</th><th>Status</th></tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.opportunity_id}>
                  <td>
                    {o.target_url ? (
                      <a href={o.target_url} target="_blank" rel="noreferrer">{o.title || o.target_domain}</a>
                    ) : (o.title || "—")}
                  </td>
                  <td className="muted" style={{ maxWidth: 280 }}>{o.signal_summary || "—"}</td>
                  <td>
                    {o.outreach_draft ? (
                      <button onClick={() => setOpenDraft(openDraft === o.opportunity_id ? null : o.opportunity_id)}>
                        {openDraft === o.opportunity_id ? "Hide" : "View draft"}
                      </button>
                    ) : "—"}
                  </td>
                  <td>
                    <select value={o.status} onChange={(e) => setStatus(o.opportunity_id, e.target.value as OffpageOpportunity["status"])}>
                      {OPPORTUNITY_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        job?.status !== "running" && <p className="muted">No opportunities found yet for this site.</p>
      )}

      {rows.map((o) =>
        openDraft === o.opportunity_id && o.outreach_draft ? (
          <div className="panel" key={`draft-${o.opportunity_id}`} style={{ marginTop: 12 }}>
            <div className="spread">
              <strong>{o.title || o.target_domain}</strong>
              <CopyButton text={o.outreach_draft} />
            </div>
            <textarea
              readOnly
              value={o.outreach_draft}
              rows={8}
              style={{ width: "100%", marginTop: 8, fontFamily: "inherit" }}
            />
          </div>
        ) : null
      )}
    </>
  );
}

export default function OffPageOpportunities({ websiteId }: { websiteId: number }) {
  const [tab, setTab] = useState<OffpageChannel>("directory");

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        {CHANNELS.map((c) => (
          <button
            key={c.key}
            className={tab === c.key ? "primary" : ""}
            onClick={() => setTab(c.key)}
          >
            {c.label}
          </button>
        ))}
      </div>
      <ChannelPanel websiteId={websiteId} channel={tab} />
    </>
  );
}
