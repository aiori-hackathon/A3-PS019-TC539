import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useEffect } from "react";
import { useApp } from "./state/AppState";
import { Notice } from "./components/ui";
import Overview from "./pages/Overview";
import Upload from "./pages/Upload";
import Sources from "./pages/Sources";
import Scan from "./pages/Scan";
import Findings from "./pages/Findings";
import Metadata from "./pages/Metadata";
import Evaluation from "./pages/Evaluation";
import Help from "./pages/Help";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/upload", label: "Upload" },
  { to: "/sources", label: "Sources" },
  { to: "/scan", label: "Scan" },
  { to: "/findings", label: "Findings" },
  { to: "/metadata", label: "Metadata" },
  { to: "/evaluation", label: "Evaluation" },
  { to: "/help", label: "Help" },
];

function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#0B1F3A" />
      <path d="M8 11h16M8 16h16M8 21h9" stroke="#2DD4BF" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}

export default function App() {
  const { workspace, scan, config, configError, refreshWorkspace } = useApp();
  const loc = useLocation();
  useEffect(() => { void refreshWorkspace(); }, [loc.pathname, refreshWorkspace]);
  const busy = scan?.state === "running" || scan?.state === "queued";
  const files = workspace?.exists ? workspace.files.length : 0;
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <div className="topbar">
        <div className="topbar-in">
          <NavLink to="/" className="brand"><Logo />FieldLedger</NavLink>
          <nav className="nav" aria-label="Main">
            {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.end}>{n.label}</NavLink>)}
          </nav>
          <div className="ws-chip" title="Files in the temporary workspace">
            <span className={`pulse ${busy ? "busy" : files > 0 ? "on" : ""}`} />
            {busy ? "Scanning" : files > 0 ? `${files} file${files === 1 ? "" : "s"} in workspace` : "Workspace empty"}
          </div>
        </div>
      </div>
      <main id="main">
        {configError && <Notice kind="error" title="Cannot reach the server">{configError}</Notice>}
        {!config && !configError && null}
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/scan" element={<Scan />} />
          <Route path="/findings" element={<Findings />} />
          <Route path="/metadata" element={<Metadata />} />
          <Route path="/evaluation" element={<Evaluation />} />
          <Route path="/help" element={<Help />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
