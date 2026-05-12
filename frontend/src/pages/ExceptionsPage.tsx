import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getExceptions } from "../api";

type Exc = {
  code?: string;
  message?: string;
  severity?: string;
  field?: string;
  document?: string;
  agent?: string;
};

export function ExceptionsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [data, setData] = useState<{
    exceptions: Exc[];
    email_draft: { subject?: string; body?: string };
    workflow_stopped: boolean;
    failed_agent: string | null;
    documents: string[];
  } | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!jobId) return;
    getExceptions(jobId)
      .then(setData)
      .catch(console.error);
  }, [jobId]);

  if (!jobId) return <p className="text-red-400">Missing job</p>;
  if (!data) return <p className="text-slate-400">Loading…</p>;

  const critical = (data.exceptions || []).filter(
    (e) => (e.severity || "").toLowerCase() === "critical"
  );

  async function approve() {
    await fetch(`/api/exceptions/${jobId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    });
    alert("Recorded (stub)");
  }

  async function sendEmail() {
    const r = await fetch(`/api/exceptions/${jobId}/email`, { method: "POST" });
    const j = await r.json();
    alert(j.status + ": " + (j.draft || ""));
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-white">Exception handling</h1>
        <Link to={`/run/${jobId}`} className="text-sm text-emerald-400 hover:underline">
          ← Back to flow
        </Link>
      </div>

      {data.workflow_stopped && (
        <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-red-200">
          Workflow stopped
          {data.failed_agent ? (
            <>
              {" "}
              at agent <strong>{data.failed_agent}</strong>
            </>
          ) : null}
          .
        </div>
      )}

      <section>
        <h2 className="mb-2 text-lg font-medium text-slate-200">Error summary</h2>
        <ul className="space-y-2">
          {(data.exceptions || []).map((e, i) => (
            <li
              key={i}
              className={`rounded-lg border px-3 py-2 text-sm ${
                (e.severity || "").toLowerCase() === "critical"
                  ? "border-red-800 bg-red-950/30 text-red-100"
                  : "border-slate-700 bg-slate-900/50 text-slate-200"
              }`}
            >
              <span className="font-mono text-xs text-slate-400">{e.code}</span> {e.message}
              {e.field ? (
                <span className="ml-2 text-xs text-slate-500">field: {e.field}</span>
              ) : null}
            </li>
          ))}
          {!data.exceptions?.length && <li className="text-slate-500">No exceptions recorded.</li>}
        </ul>
      </section>

      <section>
        <h2 className="mb-2 text-lg font-medium text-slate-200">Email draft</h2>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
          <p className="text-sm font-medium text-emerald-300">{data.email_draft?.subject}</p>
          <pre className="mt-2 whitespace-pre-wrap text-sm text-slate-300">{data.email_draft?.body}</pre>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-lg font-medium text-slate-200">Supporting documents</h2>
        <ul className="list-inside list-disc text-sm text-slate-400">
          {data.documents?.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      </section>

      <div className="flex flex-wrap gap-3">
        <div className="flex flex-1 gap-2">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Approval note"
            className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={approve}
            className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-600"
          >
            Approve manually
          </button>
        </div>
        <button
          type="button"
          onClick={sendEmail}
          className="rounded-lg border border-amber-600 px-4 py-2 text-sm font-medium text-amber-200 hover:bg-amber-950/50"
        >
          Send email to approver
        </button>
      </div>

      {critical.length > 0 && (
        <p className="text-xs text-slate-500">
          {critical.length} critical validation(s) require resolution before payment release.
        </p>
      )}
    </div>
  );
}
