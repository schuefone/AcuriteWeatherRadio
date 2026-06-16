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
import threading


DEFAULT_PROTOCOLS = (10, 11, 40, 41, 55, 74, 163, 197)
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
CALIBRATION_YEAR = 2026
CALIBRATION_YTD_IN = 8.05
MAX_RAIN_DELTA_PER_PACKET_IN = 0.2


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


@dataclass
class ObservationState:
    observed_at: str | None = None
    protocol: int | None = None
    model: str | None = None
    sensor_id: str | None = None
    channel: str | None = None
    temperature_c: float | None = None
    temperature_f: float | None = None
    humidity: float | None = None
    wind_avg_km_h: float | None = None
    wind_avg_mi_h: float | None = None
    wind_dir_deg: float | None = None
    rain_mm: float | None = None
    rain_in: float | None = None
    rain_day_in: float | None = None
    rain_ytd_in: float | None = None
    battery_ok: int | None = None
    message_type: str | None = None
    raw_json: str | None = None

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def has_data(self) -> bool:
        with self._lock:
            return any(
                value is not None
                for field_name, value in asdict(self).items()
                if field_name != "raw_json"
            )

    def merge(self, observation: Observation) -> None:
        with self._lock:
            for field_name, value in asdict(observation).items():
                if value is not None:
                    setattr(self, field_name, value)

    def build_snapshot(self, snapshot_at: str) -> Observation | None:
        with self._lock:
            if not any(
                value is not None
                for field_name, value in asdict(self).items()
                if field_name != "raw_json"
            ):
                return None

            state_payload = {
                key: value
                for key, value in asdict(self).items()
                if key != "raw_json"
            }
            raw_json = json.dumps(
                {
                    "snapshot_at": snapshot_at,
                    "latest_packet_at": self.observed_at,
                    "state": state_payload,
                    "latest_packet_raw": self.raw_json,
                },
                sort_keys=True,
            )
            return Observation(
                observed_at=snapshot_at,
                protocol=self.protocol,
                model=self.model,
                sensor_id=self.sensor_id,
                channel=self.channel,
                temperature_c=self.temperature_c,
                temperature_f=self.temperature_f,
                humidity=self.humidity,
                wind_avg_km_h=self.wind_avg_km_h,
                wind_avg_mi_h=self.wind_avg_mi_h,
                wind_dir_deg=self.wind_dir_deg,
                rain_mm=self.rain_mm,
                rain_in=self.rain_in,
                rain_day_in=self.rain_day_in,
                rain_ytd_in=self.rain_ytd_in,
                battery_ok=self.battery_ok,
                message_type=self.message_type,
                raw_json=raw_json,
            )


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
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS weather_snapshots (
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
            CREATE TABLE IF NOT EXISTS rain_rollup_state (
                year INTEGER PRIMARY KEY,
                last_rain_in REAL,
                last_ytd_in REAL NOT NULL,
                updated_at TEXT NOT NULL
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

    def _get_or_create_rollup_state(self, year: int, observed_at: str) -> tuple[float | None, float]:
        row = self.connection.execute(
            "SELECT last_rain_in, last_ytd_in FROM rain_rollup_state WHERE year = ?",
            (year,),
        ).fetchone()
        if row is not None:
            last_rain_in = _to_float_or_none(row[0])
            last_ytd_in = float(row[1])
            if year == CALIBRATION_YEAR and last_ytd_in < CALIBRATION_YTD_IN:
                # Keep the configured starting YTD floor for the calibration year.
                last_ytd_in = CALIBRATION_YTD_IN
                self.connection.execute(
                    """
                    UPDATE rain_rollup_state
                    SET last_ytd_in = ?, updated_at = ?
                    WHERE year = ?
                    """,
                    (last_ytd_in, observed_at, year),
                )
                self.connection.commit()
            return last_rain_in, last_ytd_in

        initial_ytd = CALIBRATION_YTD_IN if year == CALIBRATION_YEAR else 0.0

        self.connection.execute(
            """
            INSERT INTO rain_rollup_state (year, last_rain_in, last_ytd_in, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (year, None, initial_ytd, observed_at),
        )
        self.connection.commit()
        return None, initial_ytd

    def _update_rollup_state(self, year: int, rain_in: float, rain_ytd_in: float, observed_at: str) -> None:
        self.connection.execute(
            """
            UPDATE rain_rollup_state
            SET last_rain_in = ?, last_ytd_in = ?, updated_at = ?
            WHERE year = ?
            """,
            (rain_in, rain_ytd_in, observed_at, year),
        )
        self.connection.commit()

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

    def _update_daily_baseline(self, date_local: str, ytd_in: float, observed_at: str) -> None:
        self.connection.execute(
            """
            UPDATE rain_daily_baseline
            SET baseline_ytd_in = ?, updated_at = ?
            WHERE date_local = ?
            """,
            (ytd_in, observed_at, date_local),
        )
        self.connection.commit()

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
        last_rain_in, last_ytd_in = self._get_or_create_rollup_state(
            year=year,
            observed_at=observed_at,
        )
        if last_rain_in is None:
            delta_in = 0.0
        elif rain_in >= last_rain_in:
            delta_in = rain_in - last_rain_in
            if delta_in > MAX_RAIN_DELTA_PER_PACKET_IN:
                # Treat implausibly large single-packet jumps as maintenance disturbance.
                delta_in = 0.0
        else:
            # Sensor counter reset (power cycle/battery pull): keep accumulating by new counter value.
            delta_in = rain_in

        rain_ytd_in = max(0.0, last_ytd_in + max(0.0, delta_in))
        self._update_rollup_state(
            year=year,
            rain_in=rain_in,
            rain_ytd_in=rain_ytd_in,
            observed_at=observed_at,
        )

        baseline = self._get_or_create_daily_baseline(
            date_local=date_local,
            ytd_in=rain_ytd_in,
            observed_at=observed_at,
        )
        if baseline > rain_ytd_in:
            baseline = rain_ytd_in
            self._update_daily_baseline(
                date_local=date_local,
                ytd_in=baseline,
                observed_at=observed_at,
            )

        rain_day_in = max(0.0, rain_ytd_in - baseline)
        return rain_day_in, rain_ytd_in

    def _reset_rain_tracking_state(self) -> None:
        self.connection.execute("DELETE FROM rain_rollup_state")
        self.connection.execute("DELETE FROM rain_daily_baseline")
        self.connection.commit()

    def _backfill_table_rain_totals(self, table_name: str) -> int:
        rows = self.connection.execute(
            f"""
            SELECT id, observed_at, rain_in
            FROM {table_name}
            WHERE rain_in IS NOT NULL
            ORDER BY observed_at ASC, id ASC
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
                f"""
                UPDATE {table_name}
                SET rain_day_in = ?, rain_ytd_in = ?
                WHERE id = ?
                """,
                (round(rain_day_in, 3), round(rain_ytd_in, 3), row_id),
            )

        self.connection.commit()
        return len(rows)

    def backfill_rain_totals(self) -> int:
        self._reset_rain_tracking_state()
        observation_total = self._backfill_table_rain_totals("weather_observations")
        snapshot_total = self._backfill_snapshot_rain_totals_from_observations()
        self._reset_rain_tracking_state()
        return observation_total + snapshot_total

    def _backfill_snapshot_rain_totals_from_observations(self) -> int:
        snapshots = self.connection.execute(
            """
            SELECT id, observed_at
            FROM weather_snapshots
            ORDER BY observed_at ASC, id ASC
            """
        ).fetchall()

        updates = 0
        for snapshot_row in snapshots:
            snapshot_id = int(snapshot_row[0])
            observed_at = str(snapshot_row[1])
            source = self.connection.execute(
                """
                SELECT rain_day_in, rain_ytd_in
                FROM weather_observations
                WHERE rain_day_in IS NOT NULL
                  AND rain_ytd_in IS NOT NULL
                  AND observed_at <= ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (observed_at,),
            ).fetchone()
            if source is None:
                continue

            self.connection.execute(
                """
                UPDATE weather_snapshots
                SET rain_day_in = ?, rain_ytd_in = ?
                WHERE id = ?
                """,
                (round(float(source[0]), 3), round(float(source[1]), 3), snapshot_id),
            )
            updates += 1

        self.connection.commit()
        return updates

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

    def insert_snapshot(self, observation: Observation) -> None:
        self.connection.execute(
            """
            INSERT INTO weather_snapshots (
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
    parser.add_argument(
        "--snapshot-interval-seconds",
        type=int,
        default=300,
        help="Seconds between merged snapshot writes to the snapshot table.",
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


def _to_float_or_none(value: Any) -> float | None:
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


def _build_snapshot(db: WeatherDatabase, state: ObservationState) -> Observation | None:
    snapshot_at = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    snapshot = state.build_snapshot(snapshot_at)
    if snapshot is None:
        return None
    db.insert_snapshot(snapshot)
    return snapshot


def _snapshot_writer_loop(
    stop_event: threading.Event,
    db_path: Path,
    state: ObservationState,
    interval_seconds: int,
) -> None:
    db = WeatherDatabase(db_path)
    try:
        while not stop_event.wait(interval_seconds):
            try:
                _build_snapshot(db, state)
            except Exception as exc:  # pragma: no cover - defensive logging
                print(f"Snapshot write failed: {exc}", file=sys.stderr)
    finally:
        db.close()


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

    db_path = Path(args.db_path)
    db = WeatherDatabase(db_path)
    csv_logger = CsvLogger(Path(args.csv_path)) if args.csv_path else None
    state = ObservationState()
    stop_event = threading.Event()
    snapshot_thread = threading.Thread(
        target=_snapshot_writer_loop,
        args=(stop_event, db_path, state, args.snapshot_interval_seconds),
        daemon=True,
        name="weather-snapshot-writer",
    )
    snapshot_thread.start()
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
            state.merge(observation)
            if csv_logger is not None:
                csv_logger.append(observation)
            print(
                f"{observation.observed_at} model={observation.model} id={observation.sensor_id} temp_c={observation.temperature_c}",
                flush=True,
            )
    finally:
        stop_event.set()
        snapshot_thread.join(timeout=5)
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
        print(f"Backfilled rain totals for {updated} rows across observations and snapshots.")
        return 0

    return stream_events(args)