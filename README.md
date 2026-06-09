# Acurite Weather Logger

This project uses `rtl_433` to decode the 433 MHz radio traffic from an Acurite weather sensor and a Python logger to store the decoded weather data in SQLite and optional CSV files.

## Why this design

The SDR demodulation and Acurite protocol decoding are already solved well by `rtl_433`. Re-implementing that decoder in pure Python would be slower, harder to validate, and much less reliable. The custom part here is the Python logging system.

## Requirements

- Miniconda or Anaconda
- Nooelec NESDR Mini R820T plugged in
- A Windows build of `rtl_433`

## 1. Create the Conda environment

```powershell
conda env create -f environment.yml
conda activate acurite-weather
```

## 2. Install `rtl_433` on Windows

`rtl_433` is not available from `conda-forge` for this Windows setup, so install a Windows binary separately.

Recommended approach:

1. Download the latest Windows release assets from the `rtl_433` GitHub releases page.
2. Extract the archive somewhere stable, for example `C:\tools\rtl_433`.
3. Confirm the executable works:

```powershell
C:\tools\rtl_433\rtl_433.exe -h
```

If Windows does not recognize the SDR dongle correctly, install the USB driver with Zadig and bind the device to `WinUSB`.

## 3. Run a quick receive test

```powershell
C:\tools\rtl_433\rtl_433.exe -f 433920000 -M time:iso:utc -F json
```

Let that run until your outdoor sensor transmits. You should see JSON lines.

## 4. Start the Python logger

```powershell
python -m acurite_logger --rtl433-path C:\tools\rtl_433\rtl_433.exe --db-path data\weather.db --csv-path logs\weather.csv
```

The logger will:

- launch `rtl_433`
- read newline-delimited JSON events
- keep only Acurite-related events
- normalize commonly used weather fields
- store every matched event in SQLite
- optionally append a flattened record to CSV

## Useful options

```powershell
python -m acurite_logger --help
```

Common examples:

```powershell
python -m acurite_logger --rtl433-path C:\tools\rtl_433\rtl_433.exe --metric
python -m acurite_logger --rtl433-path C:\tools\rtl_433\rtl_433.exe --frequency 433920000 --device 0
python -m acurite_logger --rtl433-path C:\tools\rtl_433\rtl_433.exe --extra-arg=-R --extra-arg=40
```

## Database schema

SQLite database file: `data/weather.db`

Table: `weather_observations`

Stored columns include:

- `observed_at`
- `protocol`
- `model`
- `sensor_id`
- `channel`
- `temperature_c`
- `temperature_f`
- `humidity`
- `wind_avg_km_h`
- `wind_avg_mi_h`
- `wind_dir_deg`
- `rain_mm`
- `rain_in`
- `battery_ok`
- `message_type`
- `raw_json`

## Notes on model coverage

`rtl_433` supports several Acurite decoders, including the Acurite weather station family. Exact emitted fields vary by sensor model and by `rtl_433` version, so this logger stores the raw JSON as well as normalized columns.
