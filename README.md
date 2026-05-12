# QSR multi-agent invoice processing

Production-style demo: **FastAPI** + **LangGraph** multi-agent pipeline, **SQLite** job store, **Pandas** Excel masters, **React (Vite)** UI with **React Flow** and **Tailwind**.

## Layout

- `backend/` — API, LangGraph agents, validation services, mock OCR / GST
- `frontend/` — Upload, live agent diagram, exception screen
- Optional local input folder: `Invoice Processing/` (place your PDFs/images + Excel here, then upload via the UI)

## Prerequisites

- Python 3.11+ (3.14 tested)
- Node.js 18+ and npm (for the frontend)

## Backend

```powershell
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

- REST: `http://127.0.0.1:8000`
- OpenAPI: `http://127.0.0.1:8000/docs`
- On first run, `backend/data/sample_masters.xlsx` is created (PO / Vendor / GRN / Historical sheets).

### API summary

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/upload` | Multipart: `excel` (optional), `invoices` (files) |
| POST | `/execute` | JSON `{ "job_id": "..." }` starts the LangGraph run |
| GET | `/status/{job_id}` | Agent states + logs |
| GET | `/exceptions/{job_id}` | Exceptions + email draft + document paths |
| WS | `/ws/logs/{job_id}` | Live log / agent events |
| POST | `/exceptions/{job_id}/approve` | Manual approval stub |
| POST | `/exceptions/{job_id}/email` | Email stub |

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The Vite dev server proxies `/api` and `/ws` to the backend (`vite.config.ts`).

### Screens

1. **Upload** — Select Excel (optional) + invoices; continues to the run view and starts execution.
2. **Agents in action** — React Flow graph (Supervisor → … → Email), live logs, WebSocket + polling.
3. **Exceptions** — Error list, email draft, document paths, Approve / Send email stubs.

## Agents (LangGraph)

1. **Supervisor** — Initializes state  
2. **Extraction** — GROK OCR (optional) with mock OCR fallback  
3. **Screening** — Mandatory fields  
4. **Validation** — GST (mock API), vendor, PO, dates, duplicates, tax/total, stamp, currency, delay warning  
5. **Matching** — PO amount, cumulative on PO, GRN qty ±2%, unit price ±2%, 3-way checks  
6. **Exception** — Aggregates issues; sets `critical_stop` and `failed_agent`  
7. **Email** — Subject/body draft for approvers  

## OCR configuration

- **Default**: mock OCR (`pdfplumber` + regex; deterministic image fallback).  
- **GROK enabled**: set `GROK_API_KEY` to enable GROK extraction automatically.  
- Optional env vars:
  - `OCR_BACKEND=grok|auto|mock` (default behaves like `auto`)
  - `GROK_MODEL` (default `grok-2-vision-latest`)
  - `GROK_BASE_URL` (default `https://api.x.ai/v1`)
  - `GROK_TIMEOUT_SEC` (default `45`)

### Mock behaviour

- **GST**: `verify_gst_sync()` in `services/gst_service.py` (deterministic “active” GSTIN).  

## Regenerating demo Excel

Delete `backend/data/sample_masters.xlsx` and restart the API, or call `build_sample_excel` / `ensure_demo_excel` from `services/excel_loader.py` and `services/mock_loader.py`.

## License

Internal / demo use; extend SMTP, real OCR, and PostgreSQL for production.
