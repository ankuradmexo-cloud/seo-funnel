"use client";

export function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "published" ? "b-green"
    : status === "shortlisted" ? "b-blue"
    : status === "queued" ? "b-amber"
    : status === "failed" ? "b-red"
    : status === "success" ? "b-green"
    : status === "running" ? "b-amber"
    : status === "active" ? "b-blue"
    : status === "exhausted" ? "b-grey"
    : "b-grey";
  return <span className={`badge ${cls}`}>{status}</span>;
}

export function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="stat">
      <b>{value}</b>
      <span>{label}</span>
    </div>
  );
}

export function Progress({ value, target }: { value: number; target: number }) {
  const pct = target > 0 ? Math.min(100, (value / target) * 100) : 0;
  return (
    <div className="row">
      <div className="bar"><i style={{ width: `${pct}%` }} /></div>
      <span className="muted">{value}/{target}</span>
    </div>
  );
}

export function fmt(iso: string | null | undefined) {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export function WebsitePicker({
  websites, value, onChange, allowAll = true,
}: {
  websites: { website_id: number; name: string }[];
  value: number | "";
  onChange: (v: number | "") => void;
  allowAll?: boolean;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
    >
      {allowAll && <option value="">All websites</option>}
      {websites.map((w) => (
        <option key={w.website_id} value={w.website_id}>{w.name}</option>
      ))}
    </select>
  );
}
