import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { ReactFlowProvider } from "reactflow";
import { AgentFlow } from "../flow/AgentFlow";
import { StageOutputTabs } from "../components/StageOutputTabs";
import { executeJob, getStatus, logsWebSocketUrl } from "../api";

const ORDER = ["supervisor", "extraction", "screening", "validation", "matching", "exception", "email"] as const;

/** Each agent stays highlighted at least this long (ms), even when events arrive instantly from cache. */
const AGENT_MIN_DISPLAY_MS = Math.max(
  2000,
  Number(import.meta.env.VITE_AGENT_MIN_MS) || 2000
);

type StatusPayload = {
  job_id: string;
  phase: string;
  current_agent: string | null;
  agents: { name: string; status: string }[];
  logs: string[];
  workflow_stopped: boolean;
  failed_agent: string | null;
  stage_outputs?: Record<string, unknown>;
};

export function AgentsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const autoRun =
    (location.state as { autoRun?: boolean } | null)?.autoRun === true || searchParams.get("run") === "1";
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [visualAgents, setVisualAgents] = useState<{ name: string; status: string }[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [wsError, setWsError] = useState<string | null>(null);
  const [executeErr, setExecuteErr] = useState<string | null>(null);
  const started = useRef(false);
  const agentQueueRef = useRef<string[]>([]);
  const processingRef = useRef(false);
  const queueTickRef = useRef<number | null>(null);
  /** Job finished on server; wait until queued agent playback finishes before showing all done. */
  const pendingCompleteRef = useRef(false);

  function applyCompleteVisual() {
    pendingCompleteRef.current = false;
    processingRef.current = false;
    clearQueueTick();
    agentQueueRef.current = [];
    setVisualAgents(ORDER.map((name) => ({ name, status: "done" })));
  }

  function clearQueueTick() {
    if (queueTickRef.current != null) {
      window.clearTimeout(queueTickRef.current);
      queueTickRef.current = null;
    }
  }

  useEffect(() => {
    started.current = false;
    setExecuteErr(null);
  }, [jobId]);

  function processAgentQueue() {
    if (processingRef.current) return;
    processingRef.current = true;
    const tick = () => {
      const next = agentQueueRef.current.shift();
      if (!next) {
        processingRef.current = false;
        clearQueueTick();
        if (pendingCompleteRef.current) {
          applyCompleteVisual();
        }
        return;
      }
      setVisualAgents((prev) => buildFromCurrent(next, prev));
      clearQueueTick();
      queueTickRef.current = window.setTimeout(tick, AGENT_MIN_DISPLAY_MS);
    };
    tick();
  }

  function buildFromCurrent(current: string | null, prev: { name: string; status: string }[]) {
    const prevMap = new Map(prev.map((a) => [a.name, a.status]));
    return ORDER.map((name) => {
      if (name === current) return { name, status: "running" };
      const was = prevMap.get(name);
      if (was === "running" || was === "done") return { name, status: "done" };
      return { name, status: "waiting" };
    });
  }

  useEffect(() => {
    if (!jobId) return;

    setVisualAgents([]);
    agentQueueRef.current = [];
    processingRef.current = false;
    pendingCompleteRef.current = false;
    clearQueueTick();

    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(logsWebSocketUrl(jobId));
      ws.onopen = () => setWsError(null);
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === "log" && msg.message) {
            setLogs((L) => [...L, msg.message]);
          } else if (msg.type === "agent" && msg.agent) {
            const name = String(msg.agent);
            if (!ORDER.includes(name as (typeof ORDER)[number])) return;
            const q = agentQueueRef.current;
            if (q.length && q[q.length - 1] === name) return;
            q.push(name);
            processAgentQueue();
          } else if (msg.type === "complete") {
            pendingCompleteRef.current = true;
            if (!processingRef.current && agentQueueRef.current.length === 0) {
              applyCompleteVisual();
            }
          } else if (msg.type === "error") {
            pendingCompleteRef.current = false;
            agentQueueRef.current = [];
            processingRef.current = false;
            clearQueueTick();
          }
        } catch {
          /* ignore */
        }
      };
      ws.onerror = () =>
        setWsError(
          "Live log stream failed (WebSocket). Start the API on port 8000 and keep this page on the Vite dev URL (e.g. http://127.0.0.1:5173). Logs still refresh via polling."
        );
      ws.onclose = (ev) => {
        if (ev.code === 1008) {
          setWsError("This job is no longer in memory (restart the API after upload?). Run the flow again from Upload.");
        }
      };
    } catch {
      setWsError("Could not open WebSocket");
    }

    const poll = window.setInterval(async () => {
      try {
        const s = await getStatus(jobId);
        setStatus(s as StatusPayload);
        if (s.logs?.length) setLogs(s.logs);
        // Do not setVisualAgents from polling: that used a stale closure (!visualAgents.length
        // was always true) and overwrote the 2s-per-agent queue every 600ms. Fallback for the
        // diagram is status?.agents when visualAgents is empty (see AgentFlow agents prop).
      } catch {
        /* ignore */
      }
    }, 600);

    if (autoRun && !started.current) {
      started.current = true;
      executeJob(jobId)
        .then(() => setExecuteErr(null))
        .catch((ex: unknown) => {
          const msg =
            ex instanceof TypeError
              ? "Cannot reach the API (port 8000). Start the backend and use http://127.0.0.1:5173 for the UI."
              : ex instanceof Error
                ? ex.message
                : "Failed to start workflow";
          setExecuteErr(msg);
          console.error(ex);
        });
    }

    return () => {
      clearInterval(poll);
      clearQueueTick();
      agentQueueRef.current = [];
      processingRef.current = false;
      pendingCompleteRef.current = false;
      ws?.close();
    };
  }, [jobId, autoRun]);

  if (!jobId) return <p className="text-red-400">Missing job</p>;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-white">Agents in action</h1>
          <p className="text-sm text-slate-400">
            Job <code className="text-emerald-400">{jobId}</code> — phase:{" "}
            <span className="text-slate-200">{status?.phase ?? "…"}</span>
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded-lg border border-slate-600 px-3 py-1.5 text-sm hover:bg-slate-800"
            onClick={() => {
              if (!jobId) return;
              setExecuteErr(null);
              executeJob(jobId).catch((ex: unknown) => {
                setExecuteErr(ex instanceof Error ? ex.message : "Execute failed");
                console.error(ex);
              });
            }}
          >
            Run again
          </button>
          <Link
            to={`/exceptions/${jobId}`}
            className="rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-500"
          >
            Exceptions
          </Link>
        </div>
      </div>

      {executeErr && (
        <p className="rounded-lg border border-red-900/80 bg-red-950/50 px-4 py-3 text-sm text-red-200" role="alert">
          {executeErr}
        </p>
      )}
      {wsError && <p className="text-sm text-amber-400">{wsError}</p>}

      <ReactFlowProvider>
        <AgentFlow agents={visualAgents.length ? visualAgents : status?.agents ?? []} />
      </ReactFlowProvider>

      {status?.phase === "running" && (
        <p className="text-xs text-amber-200/90">
          Each agent stays highlighted for at least {AGENT_MIN_DISPLAY_MS / 1000}s (including cached runs) before the next. Structured outputs fill in when the run completes.
        </p>
      )}

      <StageOutputTabs
        stageOutputs={(status?.stage_outputs as Record<string, unknown>) ?? {}}
        focusStage={status?.workflow_stopped ? status?.failed_agent : null}
      />

      <div>
        <h2 className="mb-2 text-lg font-medium text-slate-200">Live logs</h2>
        <pre className="max-h-64 overflow-auto rounded-lg border border-slate-800 bg-black/40 p-4 text-xs text-emerald-200/90">
          {(logs.length ? logs : status?.logs ?? []).join("\n") || "Waiting for activity…"}
        </pre>
      </div>
    </div>
  );
}
