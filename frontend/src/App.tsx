import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AgentsPage } from "./pages/AgentsPage";
import { ExceptionsPage } from "./pages/ExceptionsPage";
import { UploadPage } from "./pages/UploadPage";

function UploadNavLink() {
  const { pathname } = useLocation();
  return (
    <NavLink
      to="/"
      className={({ isActive }) =>
        isActive ? "text-emerald-300" : "text-slate-400 hover:text-slate-200"
      }
      onClick={(e) => {
        // On "/", the router does not navigate again — scroll so the click still does something visible.
        if (pathname === "/") {
          e.preventDefault();
          document.getElementById("upload-form")?.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }}
    >
      Upload
    </NavLink>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <div className="text-lg font-semibold tracking-tight text-emerald-400">
            QSR Invoice Hub
          </div>
          <nav className="flex gap-4 text-sm">
            <UploadNavLink />
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/run/:jobId" element={<AgentsPage />} />
          <Route path="/exceptions/:jobId" element={<ExceptionsPage />} />
        </Routes>
      </main>
    </div>
  );
}
