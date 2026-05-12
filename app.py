"""
Balance sheet KPI platform: three agents (extract → summarise → PDF) with on-screen traces.
Run from the project folder:  streamlit run app.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st

from agents.pipeline import run_pipeline
EXPORTS = PROJECT_ROOT / "balance_sheet_exports"


def main() -> None:
    st.set_page_config(
        page_title="Balance Sheet KPI Lab",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("Balance Sheet KPI Lab")
    st.caption(
        "Upload a balance sheet PDF. Three agents run in order: **Extract** → **Summarise** → **PDF**."
    )

    with st.sidebar:
        st.subheader("Options")
        llm_extract = st.checkbox("LLM-assisted extraction merge", value=False)
        llm_summary = st.checkbox("LLM-written summary", value=False)
        st.markdown("---")
        st.markdown("**Agent flow**")
        st.code(
            "PDF → Extract Agent → Summarise Agent → PDF Agent\n"
            f"Exports: {EXPORTS}",
            language="text",
        )

    up = st.file_uploader("Balance sheet PDF", type=["pdf"])

    if st.button("Run agents", type="primary", disabled=not up):
        if not up:
            st.warning("Upload a PDF first.")
            return
        suffix = Path(up.name).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(up.getvalue())
            tmp_path = tmp.name

        with st.spinner("Running pipeline…"):
            result = run_pipeline(
                tmp_path,
                use_llm_extract=llm_extract,
                use_llm_summary=llm_summary,
                write_pdf=True,
                pdf_output_dir=EXPORTS,
            )

        Path(tmp_path).unlink(missing_ok=True)

        st.session_state["last_result"] = {
            "kpi_rows": result.kpi_rows,
            "summary": result.summary_markdown,
            "traces": result.traces,
            "error": result.error,
            "pdf_path": str(result.pdf_path) if result.pdf_path else None,
        }

    res = st.session_state.get("last_result")
    if not res:
        st.info("Upload a PDF and click **Run agents**.")
        return

    if res.get("error"):
        st.error(res["error"])
        return

    st.divider()
    st.subheader("Agent interaction")
    for trace in res["traces"]:
        with st.expander(f"{trace.title} ({trace.agent_id})", expanded=True):
            st.code("\n".join(trace.lines), language="text")

    st.divider()
    st.subheader("KPI table")
    if res["kpi_rows"]:
        df = pd.DataFrame(res["kpi_rows"])
        col_cfg: dict[str, Any] = {}
        if "Growth %" in df.columns:
            col_cfg["Growth %"] = st.column_config.NumberColumn(
                "Growth %",
                help="Change vs prior period: (current − prior) ÷ |prior| × 100.",
                format="%.2f%%",
            )
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config=col_cfg or None,
        )
    else:
        st.warning("No KPI rows extracted.")

    st.divider()
    st.subheader("Performance insights")
    st.markdown(res["summary"] or "_No summary._")

    pdf_path = res.get("pdf_path")
    if pdf_path and Path(pdf_path).is_file():
        st.divider()
        st.subheader("PDF export")
        st.success(f"Saved to: `{pdf_path}`")
        with open(pdf_path, "rb") as f:
            st.download_button(
                "Download PDF",
                data=f.read(),
                file_name=Path(pdf_path).name,
                mime="application/pdf",
            )


if __name__ == "__main__":
    main()
