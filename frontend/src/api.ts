const API_BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";
const BASE = API_BASE || "/api";

export async function uploadFiles(
  excel: File | null,
  invoices: File[],
  rulesFormat: File | null = null,
  grokApiKey: string = "",
  ocrBackend: "auto" | "grok" | "mock" = "auto"
): Promise<{ job_id: string }> {
  const fd = new FormData();
  if (excel) fd.append("excel", excel);
  if (rulesFormat) fd.append("rules_format", rulesFormat);
  fd.append("grok_api_key", grokApiKey);
  fd.append("ocr_backend", ocrBackend);
  invoices.forEach((f) => fd.append("invoices", f));
  const r = await fetch(`${BASE}/upload`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function executeJob(jobId: string): Promise<void> {
  const r = await fetch(`${BASE}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId }),
  });
  if (!r.ok) throw new Error(await r.text());
  const body = (await r.json()) as { started?: boolean; reason?: string };
  if (body.started === false) {
    throw new Error(body.reason || "Workflow did not start (job may already be running or finished).");
  }
}

export async function getStatus(jobId: string) {
  const r = await fetch(`${BASE}/status/${jobId}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getExceptions(jobId: string) {
  const r = await fetch(`${BASE}/exceptions/${jobId}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function logsWebSocketUrl(jobId: string): string {
  if (API_BASE) {
    const wsBase = API_BASE.replace(/^http/, "ws");
    return `${wsBase}/ws/logs/${jobId}`;
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/logs/${jobId}`;
}
