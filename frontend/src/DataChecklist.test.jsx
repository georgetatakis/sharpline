import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DataChecklist from "./DataChecklist.jsx";

function item(overrides) {
  return {
    table_key: "team_offense_metrics",
    label: "Offense EPA",
    path: "A",
    required: true,
    status: "loaded",
    detail: "32 rows, FTN",
    snapshot: null,
    ...overrides,
  };
}

function checklist(overrides = {}) {
  return {
    season: 2026,
    week: 7,
    data_through_week: 6,
    feature_sources: "loaded",
    ready_for_game_model: false,
    blocking: ["personnel_usage"],
    counts: { loaded: 1, missing: 1, optional: 1 },
    groups: [
      {
        name: "Team stats",
        items: [
          item({}),
          item({ table_key: "personnel_usage", label: "Personnel groupings", status: "missing", detail: null }),
          item({ table_key: "proe_matchups", label: "PROE", required: false, status: "optional" }),
        ],
      },
      { name: "Betting lines", items: [] },
    ],
    ...overrides,
  };
}

function respondWith(body, ok = true, status = 200) {
  globalThis.fetch.mockResolvedValue({ ok, status, json: async () => body });
}

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DataChecklist", () => {
  it("shows each group, item status and what blocks the game model", async () => {
    respondWith(checklist());
    render(<DataChecklist />);

    const summary = await screen.findByRole("region", { name: "summary" });
    expect(summary).toHaveTextContent("Week 7 is missing 1 required table");
    expect(summary).toHaveTextContent("read data through week 6");
    expect(within(summary).getByText("personnel_usage")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Team stats" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Betting lines" })).toBeInTheDocument();
    const row = screen.getByText("Personnel groupings").closest("li");
    expect(within(row).getByText("missing")).toBeInTheDocument();
    expect(within(row).getByText("required")).toBeInTheDocument();
    const loaded = screen.getByText("Offense EPA").closest("li");
    expect(within(loaded).getByText("32 rows, FTN")).toBeInTheDocument();
  });

  it("says when the week is ready", async () => {
    respondWith(checklist({ ready_for_game_model: true, blocking: [] }));
    render(<DataChecklist />);

    expect(await screen.findByText("Week 7 is ready for the game model")).toBeInTheDocument();
  });

  it("warns when feature_sources.json is missing", async () => {
    respondWith(checklist({ feature_sources: "missing" }));
    render(<DataChecklist />);

    expect(await screen.findByText(/feature_sources\.json is missing/)).toBeInTheDocument();
  });

  it("asks the API again when the week changes", async () => {
    respondWith(checklist());
    render(<DataChecklist />);
    await screen.findByRole("region", { name: "summary" });

    fireEvent.change(screen.getByLabelText("Week"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("Season"), { target: { value: "2025" } });

    await waitFor(() =>
      expect(globalThis.fetch).toHaveBeenLastCalledWith(expect.stringContaining("/data/checklist?season=2025&week=8")),
    );
  });

  it("shows an error when the API fails", async () => {
    respondWith({}, false, 500);
    render(<DataChecklist />);

    expect(await screen.findByRole("alert")).toHaveTextContent("the API answered 500");
  });
});
