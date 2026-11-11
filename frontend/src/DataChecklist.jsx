import { useEffect, useState } from "react";

import { API_URL } from "./api.js";
import { defaultSeason, defaultWeek } from "./nflWeek.js";

const STATUS_STYLES = {
  loaded: "bg-green-100 text-green-800",
  partial: "bg-amber-100 text-amber-800",
  stale: "bg-orange-100 text-orange-800",
  missing: "bg-red-100 text-red-800",
  rejected: "bg-red-200 text-red-900",
  optional: "bg-gray-100 text-gray-600",
};

// What to do about each status, shown in the legend.
const STATUS_HELP = {
  loaded: "in the database for this week",
  partial: "loaded, but some rows were flagged",
  stale: "nflverse stops before last week; download again",
  missing: "nothing loaded for this week",
  rejected: "the import failed; see the run log",
  optional: "not needed yet",
};

function StatusBadge({ status }) {
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${STATUS_STYLES[status] ?? STATUS_STYLES.missing}`}>
      {status}
    </span>
  );
}

function Summary({ data }) {
  const ready = data.ready_for_game_model;
  return (
    <section
      aria-label="summary"
      className={`rounded-xl p-4 ${ready ? "bg-green-50 ring-1 ring-green-200" : "bg-red-50 ring-1 ring-red-200"}`}
    >
      <p className={`font-semibold ${ready ? "text-green-900" : "text-red-900"}`}>
        {ready
          ? `Week ${data.week} is ready for the game model`
          : `Week ${data.week} is missing ${data.blocking.length} required ${data.blocking.length === 1 ? "table" : "tables"}`}
      </p>
      <p className="mt-1 text-sm text-gray-600">
        Predictions for week {data.week} read data through week {data.data_through_week}.
      </p>
      {data.feature_sources === "missing" && (
        <p className="mt-2 text-sm text-amber-800">
          feature_sources.json is missing, so every screenshot table counts as required. Run the overlap test.
        </p>
      )}
      {!ready && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {data.blocking.map((key) => (
            <li key={key} className="rounded bg-white px-2 py-0.5 font-mono text-xs text-red-800 ring-1 ring-red-200">
              {key}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap gap-3 text-sm text-gray-700">
        {Object.entries(data.counts).map(([status, n]) => (
          <span key={status} className="flex items-center gap-1.5">
            <StatusBadge status={status} /> {n}
          </span>
        ))}
      </div>
    </section>
  );
}

function Group({ group }) {
  return (
    <section className="rounded-xl bg-white shadow-sm">
      <h2 className="border-b border-gray-100 px-4 py-3 font-semibold text-gray-900">{group.name}</h2>
      <ul className="divide-y divide-gray-100">
        {group.items.map((item) => (
          <li key={item.table_key} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-start sm:gap-4">
            <div className="w-24 shrink-0">
              <StatusBadge status={item.status} />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-gray-900">
                {item.label}
                {item.required && <span className="ml-2 text-xs font-normal text-gray-500">required</span>}
              </p>
              <p className="break-words font-mono text-xs text-gray-500">
                {item.table_key} · path {item.path}
              </p>
              {item.detail && <p className="mt-1 break-words text-sm text-gray-600">{item.detail}</p>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function DataChecklist() {
  const [season, setSeason] = useState(() => defaultSeason());
  const [week, setWeek] = useState(() => defaultWeek());
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetch(`${API_URL}/data/checklist?season=${season}&week=${week}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`the API answered ${res.status}`);
        const data = await res.json();
        if (!cancelled) setState({ status: "ok", data });
      })
      .catch((err) => {
        if (!cancelled) setState({ status: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [season, week]);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col text-sm text-gray-600">
          Season
          <input
            type="number"
            min="2000"
            value={season}
            onChange={(e) => e.target.value && setSeason(Number(e.target.value))}
            className="mt-1 w-28 rounded-md border border-gray-300 bg-white px-2 py-1.5 text-gray-900"
          />
        </label>
        <label className="flex flex-col text-sm text-gray-600">
          Week
          <select
            value={week}
            onChange={(e) => setWeek(Number(e.target.value))}
            className="mt-1 w-24 rounded-md border border-gray-300 bg-white px-2 py-1.5 text-gray-900"
          >
            {Array.from({ length: 22 }, (_, i) => i + 1).map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
      </div>

      {state.status === "loading" && <p className="text-sm text-gray-500">Loading checklist…</p>}
      {state.status === "error" && (
        <p role="alert" className="rounded-xl bg-red-50 p-4 text-sm text-red-800">
          Couldn't load the checklist: {state.message}.
        </p>
      )}
      {state.status === "ok" && (
        <>
          <Summary data={state.data} />
          {state.data.groups.map((group) => (
            <Group key={group.name} group={group} />
          ))}
          <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-sm text-gray-600 sm:grid-cols-2">
            {Object.entries(STATUS_HELP).map(([status, help]) => (
              <div key={status} className="flex items-center gap-2">
                <dt>
                  <StatusBadge status={status} />
                </dt>
                <dd>{help}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </div>
  );
}
