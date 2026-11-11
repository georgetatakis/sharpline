import { useState } from "react";

import DataChecklist from "./DataChecklist.jsx";
import SystemStatus from "./SystemStatus.jsx";

const TABS = [
  { id: "checklist", label: "Data Checklist", Component: DataChecklist },
  { id: "status", label: "System Status", Component: SystemStatus },
];

export default function App() {
  const [tab, setTab] = useState("checklist");
  const { Component } = TABS.find((t) => t.id === tab);

  return (
    <main className="min-h-screen bg-gray-50 px-4 py-8">
      <div className="mx-auto max-w-3xl">
        <h1 className="text-2xl font-bold text-gray-900">Sharpline</h1>
        <nav role="tablist" className="mt-4 flex gap-1 border-b border-gray-200">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
                tab === t.id
                  ? "border-gray-900 text-gray-900"
                  : "border-transparent text-gray-500 hover:text-gray-800"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>
      <div className="mt-6">
        <Component />
      </div>
    </main>
  );
}
