import { useEffect, useState } from "react";

type ExtractionPayload = {
  table?: { document?: string; field?: string; value?: unknown; confidence?: number | string }[];
  documents?: unknown[];
  engine?: Record<string, number>;
  note?: string;
};

type RulesPayload = {
  columns?: string[];
  rows?: (string | number | boolean)[][];
  three_way_summary?: unknown[];
  po_totals?: Record<string, number>;
  note?: string;
};

type ExceptionsPayload = {
  rows?: {
    code?: string;
    severity?: string;
    message?: string;
    agent?: string;
    field?: string;
    document?: string;
  }[];
  total_issues?: number;
  critical?: boolean;
  failed_stage?: string | null;
};

export function StageOutputTabs(props: {
  stageOutputs: Record<string, unknown>;
  focusStage?: string | null;
}) {
  const { stageOutputs, focusStage } = props;
  const extraction = stageOutputs?.extraction as ExtractionPayload | undefined;
  const rules = stageOutputs?.rules_compliance as RulesPayload | undefined;
  const exceptions = stageOutputs?.exceptions_panel as ExceptionsPayload | undefined;

  const [tab, setTab] = useState<"extraction" | "rules" | "exceptions">("extraction");

  useEffect(() => {
    if (focusStage === "validation" || focusStage === "matching" || focusStage === "screening") {
      setTab("rules");
    }
    if (focusStage === "exception") {
      setTab("exceptions");
    }
  }, [focusStage]);

  const tabBtn = (id: typeof tab, label: string) => (
    <button
      type="button"
      onClick={() => setTab(id)}
      className={`rounded-t-lg px-4 py-2 text-sm font-medium ${
        tab === id ? "bg-slate-800 text-emerald-300" : "bg-slate-900/60 text-slate-400 hover:text-slate-200"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="mt-6 space-y-2">
      <h2 className="text-lg font-medium text-slate-200">Outputs</h2>
      <div className="flex flex-wrap gap-1 border-b border-slate-800">
        {tabBtn("extraction", "Extraction")}
        {tabBtn("rules", "Screening · Validation · Matching (rules)")}
        {tabBtn("exceptions", "Exceptions")}
      </div>

      <div className="rounded-b-lg border border-t-0 border-slate-800 bg-slate-900/40 p-4">
        {tab === "extraction" && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              Extracted fields and line items with mock OCR confidence scores.
            </p>
            {extraction?.engine && (
              <p className="text-xs text-slate-300">
                OCR engine used:{" "}
                {Object.entries(extraction.engine)
                  .map(([k, v]) => `${k} (${v})`)
                  .join(", ")}
              </p>
            )}
            {extraction?.note && <p className="text-xs text-slate-400">{extraction.note}</p>}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-slate-400">
                    <th className="py-2 pr-3 font-medium">Document</th>
                    <th className="py-2 pr-3 font-medium">Field / Line</th>
                    <th className="py-2 pr-3 font-medium">Value</th>
                    <th className="py-2 font-medium">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {(extraction?.table || []).map((row, i) => (
                    <tr key={i} className="border-b border-slate-800/80 text-slate-200">
                      <td className="py-1.5 pr-3 align-top text-xs text-slate-400">{row.document}</td>
                      <td className="py-1.5 pr-3 align-top font-mono text-xs">{row.field}</td>
                      <td className="py-1.5 pr-3 align-top">{String(row.value ?? "")}</td>
                      <td className="py-1.5 align-top text-emerald-400/90">
                        {row.confidence !== "" && row.confidence !== undefined ? String(row.confidence) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!extraction?.table?.length && (
                <p className="text-sm text-slate-500">No extraction data yet (run the job to completion).</p>
              )}
            </div>
          </div>
        )}

        {tab === "rules" && (
          <div className="space-y-4">
            <p className="text-xs text-slate-500">
              Same structure as your <strong className="text-slate-300">Rules and Format</strong> workbook: each
              rule shows <span className="text-emerald-400">OK</span> or{" "}
              <span className="text-red-400">NOT OK</span> after evaluation against masters + invoices.
            </p>
            {rules?.note && <p className="text-xs text-amber-300">{rules.note}</p>}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-slate-400">
                    {(rules?.columns || ["#", "Rule Description", "Validation Logic", "Agent Type", "Result", "Notes"]).map(
                      (c) => (
                        <th key={c} className="py-2 pr-2 font-medium">
                          {c}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody>
                  {(rules?.rows || []).map((row, i) => {
                    const resultCell = row[4];
                    const ok = String(resultCell).toUpperCase() === "OK";
                    return (
                      <tr key={i} className="border-b border-slate-800/80">
                        {row.map((cell, j) => (
                          <td
                            key={j}
                            className={`py-1.5 pr-2 align-top ${
                              j === 4 ? (ok ? "font-semibold text-emerald-400" : "font-semibold text-red-400") : "text-slate-200"
                            }`}
                          >
                            {String(cell ?? "")}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!rules?.rows?.length && (
                <p className="text-sm text-slate-500">No rule results yet.</p>
              )}
            </div>
            {rules?.three_way_summary && Array.isArray(rules.three_way_summary) && rules.three_way_summary.length > 0 && (
              <div>
                <h3 className="mb-2 text-sm font-medium text-slate-300">3-way match summary</h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-700 text-left text-slate-400">
                        <th className="py-1 pr-2">Document</th>
                        <th className="py-1 pr-2">PO</th>
                        <th className="py-1 pr-2">PO amount</th>
                        <th className="py-1 pr-2">Invoice total</th>
                        <th className="py-1 pr-2">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(rules.three_way_summary as Record<string, unknown>[]).map((tw, i) => (
                        <tr key={i} className="border-b border-slate-800 text-slate-200">
                          <td className="py-1 pr-2">{String(tw.document ?? "")}</td>
                          <td className="py-1 pr-2">{String(tw.po_number ?? "")}</td>
                          <td className="py-1 pr-2">{String(tw.po_amount ?? "")}</td>
                          <td className="py-1 pr-2">{String(tw.invoice_total ?? "")}</td>
                          <td className="py-1 pr-2">{String(tw.three_way_status ?? "")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === "exceptions" && (
          <div className="space-y-2">
            {exceptions?.critical && (
              <p className="text-sm text-red-300">
                Critical failure{exceptions.failed_stage ? ` near stage: ${exceptions.failed_stage}` : ""}.
              </p>
            )}
            <p className="text-xs text-slate-500">Pipeline exceptions and validation messages.</p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-slate-400">
                    <th className="py-2 pr-2">Code</th>
                    <th className="py-2 pr-2">Severity</th>
                    <th className="py-2 pr-2">Agent</th>
                    <th className="py-2 pr-2">Field</th>
                    <th className="py-2">Message</th>
                  </tr>
                </thead>
                <tbody>
                  {(exceptions?.rows || []).map((row, i) => (
                    <tr key={i} className="border-b border-slate-800/80 text-slate-200">
                      <td className="py-1.5 pr-2 font-mono text-xs">{row.code}</td>
                      <td className="py-1.5 pr-2">{row.severity}</td>
                      <td className="py-1.5 pr-2">{row.agent}</td>
                      <td className="py-1.5 pr-2">{row.field}</td>
                      <td className="py-1.5 text-xs">{row.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!exceptions?.rows?.length && (
                <p className="text-sm text-slate-500">No exceptions recorded.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
