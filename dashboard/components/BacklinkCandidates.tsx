"use client";
import { Fragment, useEffect, useRef, useState } from "react";
import { api, BacklinkCandidate, BacklinkGapJob } from "@/lib/api";

const CANDIDATE_STATUSES: BacklinkCandidate["status"][] = ["new", "contacted", "replied", "linked", "rejected"];

/** Shared between the Keywords drawer and the Articles drawer - both know
 * a keyword_id and want the same trigger/poll/list/status-update flow, no
 * reason to duplicate it. */
export default function BacklinkCandidatesPanel({ keywordId }: { keywordId: number }) {
  const [job, setJob] = useState<BacklinkGapJob | null>(null);
  const [candidates, setCandidates] = useState<BacklinkCandidate[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [drafting, setDrafting] = useState<number | null>(null);
  const [openDraft, setOpenDraft] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setJob(null);
    setErr(null);
    setCandidates([]);
    api.backlinkCandidates(keywordId).then(setCandidates).catch(() => {});
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [keywordId]);

  async function start() {
    setStarting(true);
    setErr(null);
    try {
      const { job_id } = await api.triggerBacklinkGap(keywordId);
      const j = await api.getBacklinkJob(job_id);
      setJob(j);
      pollRef.current = setInterval(async () => {
        const polled = await api.getBacklinkJob(job_id);
        setJob(polled);
        if (polled.status !== "running") {
          if (pollRef.current) clearInterval(pollRef.current);
          if (polled.status === "success") {
            api.backlinkCandidates(keywordId).then(setCandidates).catch(() => {});
          }
        }
      }, 5000);
    } catch (e) {
      setErr(String(e));
    } finally {
      setStarting(false);
    }
  }

  async function setStatus(candidateId: number, status: BacklinkCandidate["status"]) {
    try {
      const updated = await api.setBacklinkCandidateStatus(candidateId, status);
      setCandidates((rows) => rows.map((r) => (r.candidate_id === candidateId ? updated : r)));
    } catch (e) {
      setErr(String(e));
    }
  }

  async function draftOutreach(candidateId: number) {
    setDrafting(candidateId);
    setErr(null);
    try {
      const updated = await api.draftBacklinkOutreach(candidateId);
      setCandidates((rows) => rows.map((r) => (r.candidate_id === candidateId ? updated : r)));
      setOpenDraft(candidateId);
    } catch (e) {
      setErr(String(e));
    } finally {
      setDrafting(null);
    }
  }

  return (
    <>
      <h2>Backlink candidates</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Domains that already link to sites competing for this keyword, filtered for real
        authority, no spam patterns, and topical relevance - starting points for outreach,
        not something this app sends on its own.
      </p>
      {err && <div className="err">{err}</div>}
      <div className="row" style={{ marginBottom: 12 }}>
        <button className="primary" onClick={start} disabled={starting || job?.status === "running"}>
          {job?.status === "running" ? "Running… (a couple of minutes)" : starting ? "Starting…" : "Find backlink candidates"}
        </button>
        {job?.status === "failed" && (
          <span className="err" style={{ padding: "4px 10px" }}>Failed: {job.error_message}</span>
        )}
      </div>

      {candidates.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Domain</th><th>Rank</th><th>Links to</th><th>Sample</th><th>Draft</th><th>Status</th></tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <Fragment key={c.candidate_id}>
                  <tr>
                    <td><a href={`https://${c.referring_domain}`} target="_blank" rel="noreferrer">{c.referring_domain}</a></td>
                    <td>{c.domain_inlink_rank ?? "—"}</td>
                    <td>{c.competitors_linked_count ?? "—"}</td>
                    <td className="muted" style={{ maxWidth: 260 }}>
                      {c.sample_links[0] && (
                        <a href={c.sample_links[0].from_page} target="_blank" rel="noreferrer" title={c.sample_links[0].anchor ?? ""}>
                          {c.sample_links[0].from_page_title || c.sample_links[0].from_page}
                        </a>
                      )}
                    </td>
                    <td>
                      {c.outreach_draft ? (
                        <button onClick={() => setOpenDraft(openDraft === c.candidate_id ? null : c.candidate_id)}>
                          {openDraft === c.candidate_id ? "Hide" : "View draft"}
                        </button>
                      ) : (
                        <button onClick={() => draftOutreach(c.candidate_id)} disabled={drafting === c.candidate_id}>
                          {drafting === c.candidate_id ? "Drafting…" : "Draft outreach"}
                        </button>
                      )}
                    </td>
                    <td>
                      <select value={c.status} onChange={(e) => setStatus(c.candidate_id, e.target.value as BacklinkCandidate["status"])}>
                        {CANDIDATE_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </td>
                  </tr>
                  {openDraft === c.candidate_id && c.outreach_draft && (
                    <tr>
                      <td colSpan={6}>
                        <div className="spread">
                          <strong>Draft to {c.referring_domain}</strong>
                          <button
                            onClick={() => navigator.clipboard?.writeText(c.outreach_draft || "").catch(() => {})}
                          >
                            Copy
                          </button>
                        </div>
                        <textarea
                          readOnly
                          value={c.outreach_draft}
                          rows={6}
                          style={{ width: "100%", marginTop: 8, fontFamily: "inherit" }}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        job?.status !== "running" && <p className="muted">No candidates found yet for this keyword.</p>
      )}
    </>
  );
}
