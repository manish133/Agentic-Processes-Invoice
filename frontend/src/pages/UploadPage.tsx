import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { uploadFiles } from "../api";

export function UploadPage() {
  const nav = useNavigate();
  const [excel, setExcel] = useState<File | null>(null);
  const [rulesFormat, setRulesFormat] = useState<File | null>(null);
  const [invoices, setInvoices] = useState<File[]>([]);
  const [grokApiKey, setGrokApiKey] = useState("");
  const [useGrok, setUseGrok] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!invoices.length) {
      setErr("Add at least one invoice PDF or image.");
      return;
    }
    setLoading(true);
    try {
      const backend: "auto" | "grok" | "mock" = useGrok ? "grok" : "mock";
      const { job_id } = await uploadFiles(excel, invoices, rulesFormat, grokApiKey, backend);
      // `run=1` survives reloads / hard navigation where `location.state` is dropped.
      nav(`/run/${job_id}?run=1`, { state: { autoRun: true } });
      // Fallback: if router transition is blocked for any reason, force navigation.
      window.setTimeout(() => {
        if (!window.location.pathname.includes(`/run/${job_id}`)) {
          window.location.assign(`${window.location.origin}/run/${job_id}?run=1`);
        }
      }, 120);
    } catch (ex: unknown) {
      const msg =
        ex instanceof TypeError
          ? "Cannot reach the API. Start the backend on port 8000 and open this app from Vite (http://127.0.0.1:5173), not a file:// or static server without /api proxy."
          : ex instanceof Error
            ? ex.message
            : "Upload failed";
      setErr(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl">
      <h1 className="mb-2 text-2xl font-semibold text-white">Upload documents</h1>
      <p className="mb-6 text-sm text-slate-400">
        Attach one Excel workbook (masters), optional <strong className="text-slate-300">Rules and Format</strong>{" "}
        workbook, and one or more invoice PDFs or images. If you omit masters Excel, a built-in sample is used. If
        you omit rules Excel, the bundled <code className="text-slate-500">Rules and Format</code> template is used.
      </p>
      <form id="upload-form" onSubmit={onSubmit} className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6">
        <div>
          <label className="mb-1 block text-sm text-slate-300">Excel master (optional)</label>
          <input
            type="file"
            accept=".xlsx,.xls"
            onChange={(e) => setExcel(e.target.files?.[0] ?? null)}
            className="w-full text-sm text-slate-300 file:mr-3 file:rounded file:border-0 file:bg-emerald-600 file:px-3 file:py-1.5 file:text-white"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-slate-300">Rules and Format (optional)</label>
          <input
            type="file"
            accept=".xlsx,.xls"
            onChange={(e) => setRulesFormat(e.target.files?.[0] ?? null)}
            className="w-full text-sm text-slate-300 file:mr-3 file:rounded file:border-0 file:bg-slate-700 file:px-3 file:py-1.5 file:text-white"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-slate-300">OCR engine</label>
          <div className="flex items-center gap-3 text-sm text-slate-300">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={useGrok} onChange={(e) => setUseGrok(e.target.checked)} />
              Use GROK OCR
            </label>
          </div>
        </div>
        {useGrok && (
          <div>
            <label className="mb-1 block text-sm text-slate-300">GROK API key (optional per run)</label>
            <input
              type="password"
              value={grokApiKey}
              onChange={(e) => setGrokApiKey(e.target.value)}
              placeholder="xai-... or gsk-..."
              autoComplete="off"
              className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500"
            />
            <p className="mt-1 text-xs text-slate-500">
              Paste your key here, or set server env <code>GROK_API_KEY</code> (xAI) or <code>GROQ_API_KEY</code>{" "}
              (Groq, <code>gsk_...</code>). Groq keys are auto-routed to api.groq.com.
            </p>
          </div>
        )}
        <div>
          <label className="mb-1 block text-sm text-slate-300">Invoices (multiple)</label>
          <input
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.webp"
            onChange={(e) => setInvoices(Array.from(e.target.files || []))}
            className="w-full text-sm text-slate-300 file:mr-3 file:rounded file:border-0 file:bg-slate-600 file:px-3 file:py-1.5 file:text-white"
          />
        </div>
        {err && (
          <p className="text-sm text-red-400" role="alert">
            {err}
          </p>
        )}
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-emerald-600 py-2.5 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {loading ? "Uploading…" : "Upload & continue"}
        </button>
      </form>
    </div>
  );
}
