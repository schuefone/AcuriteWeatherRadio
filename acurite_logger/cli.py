from __future__ import annotations

import argparse
import csv
import json
import shutil
import signal
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_PROTOCOLS = (10, 11, 40, 41, 55, 74, 163, 197)
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
CALIBRATION_YEAR = 2026
CALIBRATION_YTD_IN = 8.05


@dataclass
class Observation:
    observed_at: str
    protocol: int | None
    model: str | None
    sensor_id: str | None
    channel: str | None
    temperature_c: float | None
    temperature_f: float | None
    humidity: float | None
    wind_avg_km_h: float | None
    wind_avg_mi_h: float | None
    wind_dir_deg: float | None
    rain_mm: float | None
    rain_in: float | None
    rain_day_in: float | None
    rain_ytd_in: float | None
    battery_ok: int | None
    message_type: str | None
    raw_json: str


class WeatherDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS weather_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                protocol INTEGER,
                model TEXT,
                sensor_id TEXT,
                channel TEXT,
                temperature_c REAL,
                temperature_f REAL,
                humidity REAL,
                wind_avg_km_h REAL,
                wind_avg_mi_h REAL,
                wind_dir_deg REAL,
                rain_mm REAL,
                rain_in REAL,
                rain_day_in REAL,
                rain_ytd_in REAL,
                battery_ok INTEGER,
                message_type TEXT,
                raw_json TEXT NOT NULL
            )
            """
        )
        self._ensure_observation_columns()
        self._ensure_rain_tracking_tables()
        self.connection.commit()

    def _ensure_observation_columns(self) -> None:
        existing_cols = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(weather_observations)").fetchall()
        }
        if "rain_day_in" not in existing_cols:
            self.connection.execute(
                "ALTER TABLE weather_observations ADD COLUMN rain_day_in REAL"
            )
        if "rain_ytd_in" not in existing_cols:
            self.connection.execute(
                "ALTER TABLE weather_observations ADD COLUMN rain_ytd_in REAL"
            )

    def _ensure_rain_tracking_tables(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rain_calibration (
                year INTEGER PRIMARY KEY,
                offset_in REAL NOT NULL,
                calibrated_at TEXT NOT NULL,
                note TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rain_daily_baseline (
                date_local TEXT PRIMARY KEY,
                baseline_ytd_in REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def _parse_observed_at_utc(self, observed_at: str) -> datetime:
        parsed = datetime.fromisoformat(observed_at)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _local_date_and_year(self, observed_at: str) -> tuple[str, int]:
        try:
            ts_utc = self._parse_observed_at_utc(observed_at)
        except ValueError:
            ts_utc = datetime.now(tz=UTC)
        ts_local = ts_utc.astimezone(PACIFIC_TZ)
        return ts_local.date().isoformat(), ts_local.year

    def _get_or_create_calibration(self, year: int, rain_in: float, observed_at: str) -> float:
        row = self.connection.execute(
            "SELECT offset_in FROM rain_calibration WHERE year = ?",
            (year,),
        ).fetchone()
        if row is not None:
            return float(row[0])

        if year == CALIBRATION_YEAR:
            offset = CALIBRATION_YTD_IN - rain_in
            note = f"Calibrated from known display YTD={CALIBRATION_YTD_IN} in"
        else:
            offset = -rain_in
            note = "Initialized with zero YTD at first seen packet for year"

        self.connection.execute(
            """
            INSERT INTO rain_calibration (year, offset_in, calibrated_at, note)
            VALUES (?, ?, ?, ?)
            """,
            (year, offset, observed_at, note),
        )
        self.connection.commit()
        return offset

    def _get_or_create_daily_baseline(
        self, date_local: str, ytd_in: float, observed_at: str
    ) -> float:
        row = self.connection.execute(
            "SELECT baseline_ytd_in FROM rain_daily_baseline WHERE date_local = ?",
            (date_local,),
        ).fetchone()
        if row is not None:
            return float(row[0])

        self.connection.execute(
            """
            INSERT INTO rain_daily_baseline (date_local, baseline_ytd_in, updated_at)
            VALUES (?, ?, ?)
            """,
            (date_local, ytd_in, observed_at),
        )
        self.connection.commit()
        return ytd_in

    def apply_rain_totals(self, observation: Observation) -> Observation:
        if observation.rain_in is None:
            return observation

        rain_day_in, rain_ytd_in = self._compute_rain_totals(
            observed_at=observation.observed_at,
            rain_in=observation.rain_in,
        )

        observation.rain_ytd_in = round(rain_ytd_in, 3)
        observation.rain_day_in = round(rain_day_in, 3)
        return observation

    def _compute_rain_totals(self, observed_at: str, rain_in: float) -> tuple[float, float]:
        date_local, year = self._local_date_and_year(observed_at)
        offset = self._get_or_create_calibration(
            year=year,
            rain_in=rain_in,
            observed_at=observed_at,
        )
        rain_ytd_in = max(0.0, rain_in + offset)
        baseline = self._get_or_create_daily_baseline(
            date_local=date_local,
            ytd_in=rain_ytd_in,
            observed_at=observed_at,
        )
        rain_day_in = max(0.0, rain_ytd_in - baseline)
        return rain_day_in, rain_ytd_in

    def backfill_rain_totals(self) -> int:
        self.connection.execute("DELETE FROM rain_calibration")
        self.connection.execute("DELETE FROM rain_daily_baseline")

        rows = self.connection.execute(
            """
            SELECT id, observed_at, rain_in
            FROM weather_observations
            WHERE rain_in IS NOT NULL
            ORDER BY id ASC
            """
        ).fetchall()

        for row in rows:
            row_id = int(row[0])
            observed_at = str(row[1])
            rain_in = float(row[2])
            rain_day_in, rain_ytd_in = self._compute_rain_totals(
                observed_at=observed_at,
                rain_in=rain_in,
            )
            self.connection.execute(
                """
                UPDATE weather_observations
                SET rain_day_in = ?, rain_ytd_in = ?
                WHERE id = ?
                """,
                (round(rain_day_in, 3), round(rain_ytd_in, 3), row_id),
            )

        self.connection.commit()
        return len(rows)

    def insert(self, observation: Observation) -> None:
        self.connection.execute(
            """
            INSERT INTO weather_observations (
                observed_at,
                protocol,
                model,
                sensor_id,
                channel,
                temperature_c,
                temperature_f,
                humidity,
                wind_avg_km_h,
                wind_avg_mi_h,
                wind_dir_deg,
                rain_mm,
                rain_in,
                rain_day_in,
                rain_ytd_in,
                battery_ok,
                message_type,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.observed_at,
                observation.protocol,
                observation.model,
                observation.sensor_id,
                observation.channel,
                observation.temperature_c,
                observation.temperature_f,
                observation.humidity,
                observation.wind_avg_km_h,
                observation.wind_avg_mi_h,
                observation.wind_dir_deg,
                observation.rain_mm,
                observation.rain_in,
                observation.rain_day_in,
                observation.rain_ytd_in,
                observation.battery_ok,
                observation.message_type,
                observation.raw_json,
            ),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class CsvLogger:
    def __init__(self, csv_path: Path) -> None:
        self.csv_path = csv_path
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.fieldnames = list(Observation.__annotations__.keys())
        self._has_header = self.csv_path.exists() and self.csv_path.stat().st_size > 0

    def append(self, observation: Observation) -> None:
        with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames)
            if not self._has_header:
                writer.writeheader()
                self._has_header = True
            writer.writerow(asdict(observation))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch rtl_433, filter Acurite weather events, and log them."
    )
    parser.add_argument(
        "--rtl433-path",
        default="rtl_433",
        help="Path to rtl_433 executable. Defaults to rtl_433 on PATH.",
    )
    parser.add_argument(
        "--db-path",
        default="data/weather.db",
        help="SQLite database path.",
    )
    parser.add_argument(
        "--csv-path",
        default=None,
        help="Optional CSV log path.",
    )
    parser.add_argument(
        "--frequency",
        type=int,
        default=433920000,
        help="Receive frequency in Hz.",
    )
    parser.add_argument(
        "--device",
        default="0",
        help="RTL-SDR device selector passed to rtl_433.",
    )
    parser.add_argument(
        "--gain",
        default="0",
        help="Gain passed to rtl_433. Use 0 for auto.",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=250000,
        help="Sample rate in samples per second.",
    )
    parser.add_argument(
        "--metric",
        action="store_true",
        help="Ask rtl_433 to emit SI units when possible.",
    )
    parser.add_argument(
        "--protocol",
        type=int,
        action="append",
        default=None,
        help="Acurite protocol number to enable. Repeat as needed.",
    )
    parser.add_argument(
        "--extra-arg",
        action="append",
        default=[],
        help="Extra argument to pass through to rtl_433. Repeat as needed.",
    )
    parser.add_argument(
        "--backfill-rain-totals",
        action="store_true",
        help=(
            "Recompute rain_day_in and rain_ytd_in for existing rows in the database "
            "and exit."
        ),
    )
    return parser.parse_args(argv)


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int_bool(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "ok", "yes"}:
            return 1
        if normalized in {"0", "false", "low", "no"}:
            return 0
    return None


def pick_first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def looks_like_acurite(payload: dict[str, Any]) -> bool:
    model = str(payload.get("model", "")).lower()
    return "acurite" in model


def normalize_observation(payload: dict[str, Any]) -> Observation:
    observed_at = str(pick_first(payload, "time", "datetime", "timestamp") or now_iso())
    sensor_id = pick_first(payload, "id", "sensor_id")
    channel = pick_first(payload, "channel")
    message_type = pick_first(payload, "message_type", "subtype", "event")
    temperature_c = safe_float(pick_first(payload, "temperature_C", "temperature_c"))
    temperature_f = safe_float(pick_first(payload, "temperature_F", "temperature_f"))
    humidity = safe_float(pick_first(payload, "humidity"))
    wind_avg_km_h = safe_float(
        pick_first(payload, "wind_avg_km_h", "wind_speed_km_h", "wind_speed")
    )
    wind_avg_mi_h = safe_float(
        pick_first(payload, "wind_avg_mi_h", "wind_speed_mph", "wind_avg_mph")
    )
    wind_dir_deg = safe_float(pick_first(payload, "wind_dir_deg", "wind_dir"))
    rain_mm = safe_float(pick_first(payload, "rain_mm", "rain_total_mm", "rainfall_mm"))
    rain_in = safe_float(pick_first(payload, "rain_in", "rain_total_in", "rainfall_in"))

    return Observation(
        observed_at=observed_at,
        protocol=int(payload["protocol"]) if isinstance(payload.get("protocol"), int) else None,
        model=str(payload.get("model")) if payload.get("model") is not None else None,
        sensor_id=str(sensor_id) if sensor_id is not None else None,
        channel=str(channel) if channel is not None else None,
        temperature_c=temperature_c,
        temperature_f=temperature_f,
        humidity=humidity,
        wind_avg_km_h=wind_avg_km_h,
        wind_avg_mi_h=wind_avg_mi_h,
        wind_dir_deg=wind_dir_deg,
        rain_mm=rain_mm,
        rain_in=rain_in,
        rain_day_in=None,
        rain_ytd_in=None,
        battery_ok=safe_int_bool(pick_first(payload, "battery_ok", "battery")),
        message_type=str(message_type) if message_type is not None else None,
        raw_json=json.dumps(payload, sort_keys=True),
    )


def build_rtl433_command(args: argparse.Namespace) -> list[str]:
    protocols = args.protocol if args.protocol else list(DEFAULT_PROTOCOLS)
    command = [
        args.rtl433_path,
        "-d",
        str(args.device),
        "-f",
        str(args.frequency),
        "-g",
        str(args.gain),
        "-s",
        str(args.sample_rate),
        "-M",
        "time:iso:utc",
        "-F",
        "json",
    ]
    command.extend(["-C", "si"] if args.metric else ["-C", "customary"])
    for protocol in protocols:
        command.extend(["-R", str(protocol)])
    command.extend(args.extra_arg)
    return command


def resolve_executable(path_or_name: str) -> str | None:
    explicit_path = Path(path_or_name)
    if explicit_path.exists():
        return str(explicit_path)
    return shutil.which(path_or_name)


def stream_events(args: argparse.Namespace) -> int:
    executable = resolve_executable(args.rtl433_path)
    if executable is None:
        print(
            "rtl_433 executable not found. Pass --rtl433-path or add it to PATH.",
            file=sys.stderr,
        )
        return 2

    db = WeatherDatabase(Path(args.db_path))
    csv_logger = CsvLogger(Path(args.csv_path)) if args.csv_path else None
    command = build_rtl433_command(args)
    command[0] = executable

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def handle_signal(signum: int, _frame: Any) -> None:
        print(f"Received signal {signum}, stopping rtl_433...", file=sys.stderr)
        process.terminate()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                print(f"Skipping non-JSON output: {line}", file=sys.stderr)
                continue
            if not isinstance(payload, dict) or not looks_like_acurite(payload):
                continue

            observation = normalize_observation(payload)
            observation = db.apply_rain_totals(observation)
            db.insert(observation)
            if csv_logger is not None:
                csv_logger.append(observation)
            print(
                f"{observation.observed_at} model={observation.model} id={observation.sensor_id} temp_c={observation.temperature_c}",
                flush=True,
            )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        db.close()

    return process.returncode or 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.backfill_rain_totals:
        db = WeatherDatabase(Path(args.db_path))
        try:
            updated = db.backfill_rain_totals()
        finally:
            db.close()
        print(f"Backfilled rain totals for {updated} observation rows.")
        return 0

    return stream_events(args)