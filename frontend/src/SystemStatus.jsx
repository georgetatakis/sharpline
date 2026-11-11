import { useEffect, useState } from "react";

import { API_URL } from "./api.js";

function StatusRow({ label, state }) {
  const styles = {
    loading: "bg-gray-100 text-gray-600",
    ok: "bg-green-100 text-green-800",
    error: "bg-red-100 text-red-800",
  };
  const text = { loading: "Checking…", ok: "OK", error: "Unreachable" };

  return (
    <div className="flex items-center justify-between py-3">
      <span className="font-medium text-gray-800">{label}</span>
      <span className={`rounded-full px-3 py-1 text-sm font-semibold ${styles[state]}`}>
        {text[state]}
      </span>
    </div>
  );
}

export default function SystemStatus() {
  const [backend, setBackend] = useState("loading");
  const [database, setDatabase] = useState("loading");

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then(async (res) => {
        // A 503 still means the API answered; only the database is down.
        setBackend("ok");
        const body = await res.json();
        setDatabase(body.database === "ok" ? "ok" : "error");
      })
      .catch(() => {
        setBackend("error");
        setDatabase("error");
      });
  }, []);

  return (
    <div className="mx-auto max-w-md rounded-xl bg-white p-6 shadow-sm">
      <p className="text-sm text-gray-500">System status</p>
      <div className="mt-2 divide-y divide-gray-100">
        <StatusRow label="Backend API" state={backend} />
        <StatusRow label="Database" state={database} />
      </div>
    </div>
  );
}
