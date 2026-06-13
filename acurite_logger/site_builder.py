from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo


PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


@dataclass
class WeatherSnapshot:
    generated_at: str
    observed_at: str | None
    model: str | None
    sensor_id: str | None
    channel: str | None
    message_type: str | None
    temperature_f: float | None
    temperature_c: float | None
    humidity: float | None
    wind_avg_mi_h: float | None
    wind_avg_km_h: float | None
    wind_dir_deg: float | None
    rain_in: float | None
    rain_mm: float | None
    battery_ok: int | None
    stale_minutes: int | None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a static weather page from weather_observations SQLite data."
    )
    parser.add_argument(
        "--db-path",
        default="data/weather.db",
        help="Path to the weather SQLite database.",
    )
    parser.add_argument(
        "--site-dir",
        default="../PleasantStreetWeather",
        help="Output directory for static site files (index.html and data.json).",
    )
    parser.add_argument(
        "--title",
        default="Pleasant Street Weather",
        help="Page title for the generated dashboard.",
    )
    parser.add_argument(
        "--station-label",
        default="Outdoor Sensor",
        help="Human-friendly station label shown in the page header.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=24,
        help="Number of recent observations to show in the table.",
    )
    return parser.parse_args(argv)


def _query_latest_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    cur = conn.execute(
        """
        SELECT *
        FROM weather_observations
        ORDER BY id DESC
        LIMIT 1
        """
    )
    return cur.fetchone()


def _query_latest_non_null(conn: sqlite3.Connection, column: str) -> sqlite3.Row | None:
    cur = conn.execute(
        f"""
        SELECT *
        FROM weather_observations
        WHERE {column} IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """
    )
    return cur.fetchone()


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            # rtl_433 in this project is configured for UTC timestamps.
            return parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return None


def _format_pacific(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(PACIFIC_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def _fmt_number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "--"
    return f"{value:.{digits}f}"


def _compass_from_degrees(value: float | None) -> str:
    if value is None:
        return "--"
    directions = [
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    ]
    idx = int((value + 11.25) // 22.5) % 16
    return directions[idx]


def _snapshot(conn: sqlite3.Connection) -> WeatherSnapshot:
    latest = _query_latest_row(conn)
    temp_row = _query_latest_non_null(conn, "temperature_f") or _query_latest_non_null(conn, "temperature_c")
    humidity_row = _query_latest_non_null(conn, "humidity")
    wind_row = _query_latest_non_null(conn, "wind_avg_mi_h") or _query_latest_non_null(conn, "wind_avg_km_h")
    wind_dir_row = _query_latest_non_null(conn, "wind_dir_deg")
    rain_row = _query_latest_non_null(conn, "rain_in") or _query_latest_non_null(conn, "rain_mm")

    generated = _format_pacific(datetime.now(tz=UTC).replace(microsecond=0)) or "--"

    if latest is None:
        return WeatherSnapshot(
            generated_at=generated,
            observed_at=None,
            model=None,
            sensor_id=None,
            channel=None,
            message_type=None,
            temperature_f=None,
            temperature_c=None,
            humidity=None,
            wind_avg_mi_h=None,
            wind_avg_km_h=None,
            wind_dir_deg=None,
            rain_in=None,
            rain_mm=None,
            battery_ok=None,
            stale_minutes=None,
        )

    observed_at_raw = latest["observed_at"]
    observed_ts = _parse_time(observed_at_raw)
    observed_at_display = _format_pacific(observed_ts)
    stale_minutes: int | None = None
    if observed_ts is not None:
        delta = datetime.now(tz=UTC) - observed_ts.astimezone(UTC)
        stale_minutes = max(0, int(delta.total_seconds() // 60))

    return WeatherSnapshot(
        generated_at=generated,
        observed_at=observed_at_display,
        model=str(latest["model"]) if latest["model"] is not None else None,
        sensor_id=str(latest["sensor_id"]) if latest["sensor_id"] is not None else None,
        channel=str(latest["channel"]) if latest["channel"] is not None else None,
        message_type=str(latest["message_type"]) if latest["message_type"] is not None else None,
        temperature_f=_to_float(temp_row["temperature_f"]) if temp_row is not None else None,
        temperature_c=_to_float(temp_row["temperature_c"]) if temp_row is not None else None,
        humidity=_to_float(humidity_row["humidity"]) if humidity_row is not None else None,
        wind_avg_mi_h=_to_float(wind_row["wind_avg_mi_h"]) if wind_row is not None else None,
        wind_avg_km_h=_to_float(wind_row["wind_avg_km_h"]) if wind_row is not None else None,
        wind_dir_deg=_to_float(wind_dir_row["wind_dir_deg"]) if wind_dir_row is not None else None,
        rain_in=_to_float(rain_row["rain_in"]) if rain_row is not None else None,
        rain_mm=_to_float(rain_row["rain_mm"]) if rain_row is not None else None,
        battery_ok=_to_int(latest["battery_ok"]),
        stale_minutes=stale_minutes,
    )


def _recent_rows(conn: sqlite3.Connection, max_rows: int) -> list[dict[str, object]]:
    cur = conn.execute(
        """
        SELECT
            observed_at,
            model,
            sensor_id,
            channel,
            message_type,
            temperature_f,
            temperature_c,
            humidity,
            wind_avg_mi_h,
            wind_avg_km_h,
            wind_dir_deg,
            rain_in,
            rain_mm,
            battery_ok
        FROM weather_observations
        ORDER BY id DESC
        LIMIT 300
        """
    )
    rows = cur.fetchall()

    deduped: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()
    for row in rows:
        key = (
            row["observed_at"],
            row["model"],
            row["sensor_id"],
            row["channel"],
            row["message_type"],
        )
        if key in seen:
            continue
        seen.add(key)
        row_dict = dict(row)
        row_dict["observed_at"] = _format_pacific(_parse_time(row_dict.get("observed_at")))
        deduped.append(row_dict)
        if len(deduped) >= max_rows:
            break
    return deduped


def _html(snapshot: WeatherSnapshot, recent: list[dict[str, object]], title: str, station_label: str) -> str:
    wind_cardinal = _compass_from_degrees(snapshot.wind_dir_deg)
    stale = "--" if snapshot.stale_minutes is None else str(snapshot.stale_minutes)
    battery = "UNKNOWN"
    if snapshot.battery_ok == 1:
        battery = "OK"
    elif snapshot.battery_ok == 0:
        battery = "LOW"

    cards = [
        ("Temperature", f"{_fmt_number(snapshot.temperature_f)} F / {_fmt_number(snapshot.temperature_c)} C"),
        ("Humidity", f"{_fmt_number(snapshot.humidity)} %"),
        ("Wind", f"{_fmt_number(snapshot.wind_avg_mi_h)} mph / {_fmt_number(snapshot.wind_avg_km_h)} km/h"),
        ("Wind Direction", f"{_fmt_number(snapshot.wind_dir_deg)} deg ({wind_cardinal})"),
        ("Rain Total", f"{_fmt_number(snapshot.rain_in, 2)} in / {_fmt_number(snapshot.rain_mm, 2)} mm"),
        ("Battery", battery),
    ]

    row_html = []
    for row in recent:
        row_html.append(
            "<tr>"
            f"<td>{escape(str(row.get('observed_at', '--')))}</td>"
            f"<td>{escape(str(row.get('message_type', '--')))}</td>"
            f"<td>{_fmt_number(_to_float(row.get('temperature_f')))}</td>"
            f"<td>{_fmt_number(_to_float(row.get('humidity')))}</td>"
            f"<td>{_fmt_number(_to_float(row.get('wind_avg_mi_h')))}</td>"
            f"<td>{_fmt_number(_to_float(row.get('wind_dir_deg')))}</td>"
            f"<td>{_fmt_number(_to_float(row.get('rain_in')), 2)}</td>"
            "</tr>"
        )

    cards_markup = "\n".join(
        f"<article class=\"card\"><h2>{escape(label)}</h2><p>{escape(value)}</p></article>"
        for label, value in cards
    )

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{escape(title)}</title>
  <meta http-equiv=\"refresh\" content=\"300\" />
  <style>
    :root {{
      --bg: #eef2f7;
      --surface: #ffffff;
      --ink: #152238;
      --muted: #4f6075;
      --accent: #146e8f;
      --accent-2: #0fa36b;
      --border: #cfdae6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at top right, #dceaf4, var(--bg) 55%);
    }}
    .wrap {{
      max-width: 1000px;
      margin: 0 auto;
      padding: 1.2rem;
    }}
    header {{
      background: linear-gradient(120deg, var(--accent), var(--accent-2));
      color: white;
      border-radius: 14px;
      padding: 1.2rem;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
    }}
    header h1 {{ margin: 0 0 0.35rem; font-size: 1.65rem; }}
    header p {{ margin: 0.15rem 0; opacity: 0.95; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 0.9rem;
      margin-top: 1rem;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 0.9rem;
    }}
    .card h2 {{
      margin: 0;
      font-size: 0.9rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .card p {{
      margin: 0.4rem 0 0;
      font-size: 1.2rem;
      font-weight: 600;
    }}
    .panel {{
      margin-top: 1rem;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
    }}
    .panel h2 {{
      margin: 0;
      padding: 0.8rem 0.9rem;
      border-bottom: 1px solid var(--border);
      font-size: 1rem;
    }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      text-align: left;
      padding: 0.6rem 0.9rem;
      border-bottom: 1px solid var(--border);
      font-size: 0.92rem;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    footer {{
      color: var(--muted);
      margin-top: 0.85rem;
      font-size: 0.85rem;
    }}
  </style>
</head>
<body>
  <main class=\"wrap\">
    <header>
      <h1>{escape(title)}</h1>
      <p>Station: {escape(station_label)}</p>
      <p>Sensor: {escape(snapshot.model or '--')} / ID {escape(snapshot.sensor_id or '--')} / Channel {escape(snapshot.channel or '--')}</p>
      <p>Last packet: {escape(snapshot.observed_at or '--')} (stale minutes: {escape(stale)})</p>
      <p>Page generated: {escape(snapshot.generated_at)}</p>
    </header>

    <section class=\"grid\">
      {cards_markup}
    </section>

    <section class=\"panel\">
      <h2>Recent Observations</h2>
      <div class=\"table-wrap\">
        <table>
          <thead>
            <tr>
              <th>Observed</th>
              <th>Type</th>
              <th>Temp F</th>
              <th>Humidity %</th>
              <th>Wind mph</th>
              <th>Direction deg</th>
              <th>Rain in</th>
            </tr>
          </thead>
          <tbody>
            {''.join(row_html) if row_html else '<tr><td colspan="7">No observations yet.</td></tr>'}
          </tbody>
        </table>
      </div>
    </section>

    <footer>
      Auto-refresh is enabled every 5 minutes. Data source: weather_observations table.
    </footer>
  </main>
</body>
</html>
"""


def build_site(db_path: Path, site_dir: Path, title: str, station_label: str, max_rows: int) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        snapshot = _snapshot(conn)
        recent = _recent_rows(conn, max_rows=max_rows)
    finally:
        conn.close()

    html = _html(snapshot, recent, title=title, station_label=station_label)
    (site_dir / "index.html").write_text(html, encoding="utf-8")
    (site_dir / "data.json").write_text(
        json.dumps(
            {
                "snapshot": asdict(snapshot),
                "recent": recent,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    build_site(
        db_path=Path(args.db_path),
        site_dir=Path(args.site_dir),
        title=args.title,
        station_label=args.station_label,
        max_rows=args.max_rows,
    )
    print(f"Generated static site in {args.site_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
