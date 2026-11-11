// NFL weeks run Tuesday to Monday, starting the day after Labor Day (first Monday in September).
// Used only to pick sensible defaults for the season and week pickers.

function laborDay(year) {
  const d = new Date(year, 8, 1);
  while (d.getDay() !== 1) d.setDate(d.getDate() + 1);
  return d;
}

export function defaultSeason(today = new Date()) {
  // January and February games belong to the season that started the previous fall.
  return today.getMonth() < 3 ? today.getFullYear() - 1 : today.getFullYear();
}

export function defaultWeek(today = new Date()) {
  const season = defaultSeason(today);
  const start = laborDay(season);
  start.setDate(start.getDate() + 1);
  const days = Math.floor((today - start) / 86400000);
  if (days < 0) return 1;
  return Math.min(Math.floor(days / 7) + 1, 22);
}
