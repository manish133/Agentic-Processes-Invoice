# Balance sheet KPI extraction and reporting
_Exported on 4/18/2026 at 18:25:26 GMT+5:30 from Cursor (3.1.15)_

---

**User**

Need a platform where i can upload balance sheet and extract the KPIs in a tabular with values. Also be able to see a summary of insights on balance sheet performance. Also, I need the summary to be generated into a pdf file and saved in Agent Project folder. I need 3 agents to do this - extract agent, summarise agent and the pdf converter agent. Need to see the modular code for each and show the agent interaction on screen in that platform.
Add a % column of growth in the table.


**Cursor**


Exploring the project structure and existing balance-sheet code to build a modular three-agent platform with UI, KPI extraction, insights, PDF export, and visible agent interaction.




Building a Streamlit platform with three modular agents (extract, summarise, PDF), a KPI table, an insights summary, and an on-screen agent interaction log. Implementing the modules and app.




Fixing a bug in the equity-ratio summary logic and adding the Streamlit app.




Here’s what was added and how to use it.

### What you have

1. **`agents/extract_agent.py` — Extract Agent**  
   Wraps your existing `extract_metrics_from_pdf` flow: ingest PDF → locate balance sheet page → parse KPIs. Emits a `BalanceSheetSnapshot`, builds KPI rows via `agents/kpis.py`, and logs each step to an `AgentTrace`.

2. **`agents/summarise_agent.py` — Summarise Agent**  
   Builds a markdown insight summary from the snapshot (YoY %, leverage lines, caveats). If **LLM-written summary** is on and `OPENAI_API_KEY` is set, it uses the OpenAI API; otherwise it stays rule-based.

3. **`agents/pdf_agent.py` — PDF Converter Agent**  
   Uses **ReportLab** to write the summary plus the KPI table into a PDF under  
   **`c:\Users\moumi\OneDrive\Desktop\Agent Project\balance_sheet_exports\`**  
   (folder is created automatically; filenames look like `balance_sheet_summary_YYYYMMDD_HHMMSS.pdf`).

4. **`agents/pipeline.py`**  
   Runs the three agents in order and returns traces for the UI.

5. **`app.py`** — Streamlit UI  
   PDF upload, **Run agents**, expandable **Agent interaction** logs (one section per agent), **KPI table**, **Performance insights**, success path + **Download PDF** for the file saved in the project.

### How to run

From the Agent Project folder:

```bash
streamlit run app.py
```

If `streamlit` isn’t on your PATH, use the full path to the script under your Python `Scripts` folder, or `python -m streamlit run app.py`.

### Options (sidebar)

- **LLM-assisted extraction merge** — maps to your existing optional merge in `balance_sheet_agent` (needs API key).  
- **LLM-written summary** — richer narrative when the key is set.

### Dependencies

`requirements.txt` now includes `streamlit` and `reportlab` (install with `pip install -r requirements.txt`).

The three agents stay separate modules; the UI only orchestrates them through `run_pipeline()` and shows their traces so you can see the interaction on screen. If you want a different export location or a live step-by-step “chat” style log, say what you prefer and we can adjust the UI.

---

**User**

Whatever is needed you only do 

---

**Cursor**


Auditing the project for gaps and fixing only what's necessary for it to run.




Adding a minimal `sys.path` fix so the app imports reliably when Streamlit is launched from a different working directory.




No other gaps showed up in a quick pass. The only change made was necessary: at the top of `app.py`, the project root is inserted on `sys.path` before importing `agents`, so imports still work if you start Streamlit from another folder (e.g. `streamlit run "...\app.py"`). Nothing else was added.

---

**User**

Give me the final link to access this tool

---

**Cursor**


There isn’t a fixed public link until you host the app somewhere. **On your machine**, the app is whatever URL Streamlit prints when it starts.

**Default (most common):**

**http://localhost:8501**

Start the server from your project folder:

```bash
python -m streamlit run app.py
```

If port 8501 is busy, Streamlit picks another (e.g. **http://localhost:8502**) and shows it in the terminal—that line is the link you use.

To share it over your **LAN** (same Wi‑Fi), run with:

```bash
python -m streamlit run app.py --server.address 0.0.0.0
```

Then open **http://\<your-PC-LAN-IP\>:8501** from another device (firewall permitting).

A **public** link (e.g. Streamlit Community Cloud, Azure, etc.) only exists after you deploy; the project as-is is local-only via `localhost`.

---

**User**

I need to deploy this on my local machine on this laptop, give me the steps 

---

**Cursor**


Here’s a minimal **local “deploy” on this laptop** (run the app yourself; no cloud needed).

### 1. Open a terminal in the project folder

PowerShell:

```powershell
cd "c:\Users\moumi\OneDrive\Desktop\Agent Project"
```

### 2. (Recommended) Use a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If activation is blocked, run once (as Administrator is not always required):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again.

### 3. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Start the app

```powershell
python -m streamlit run app.py
```

### 5. Open it in the browser

- Streamlit will show a URL, usually **`http://localhost:8501`**
- Open that link in Chrome / Edge / Firefox

Leave the terminal window open while you use the app. Stop the server with **Ctrl+C** in that terminal.

### Optional checks

- **Firewall:** first run may prompt for network access; allow for private networks only if you want.
- **Port in use:** if 8501 is busy, Streamlit will pick another port and print the correct link in the terminal—use that one.
- **PDF output folder:** reports go under  
  `c:\Users\moumi\OneDrive\Desktop\Agent Project\balance_sheet_exports\`

That’s full local deployment on this machine: install deps, run Streamlit, use the `localhost` URL it prints.

