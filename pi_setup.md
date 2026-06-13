# Raspberry Pi Setup And Test Guide

This guide walks through installing Miniconda on a Raspberry Pi, pulling this project from GitHub, creating the Conda environment named weather, and running a live test of the weather logger.

## 1. Install Required OS Packages

Run:

```bash
sudo apt update
sudo apt install -y git wget bzip2 libusb-1.0-0 rtl-433 rtl-sdr sqlite3
```

What these are for:
- git: clone and update this repository.
- rtl-433: decodes weather sensor RF packets.
- rtl-sdr: SDR diagnostics and utilities.
- sqlite3: quick database inspection.

## 2. Install Miniconda On Raspberry Pi

Check architecture:

```bash
uname -m
```

If the output is aarch64, install Miniconda:

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh -O ~/miniconda.sh
bash ~/miniconda.sh -b -p $HOME/miniconda3
eval "$($HOME/miniconda3/bin/conda shell.bash hook)"
conda init bash
source ~/.bashrc
```

Verify:

```bash
conda --version
```

## 3. Pull The Code From GitHub

Clone for first-time setup:

```bash
git clone <your-github-url>
cd AcuriteWeatherRadio
```

If the repo already exists locally:

```bash
cd ~/GitHub/AcuriteWeatherRadio
git pull
```

## 4. Create Conda Environment weather

From the repository root:

```bash
conda env create -n weather -f environment.yml
conda activate weather
```

If the environment already exists:

```bash
conda env update -n weather -f environment.yml --prune
conda activate weather
```

Smoke test the Python entrypoint:

```bash
python -m acurite_logger --help
```

## 5. Confirm SDR Reception Before Running Logger

With the SDR receiver plugged in:

```bash
rtl_433 -d 0 -f 433920000 -M time:iso:utc -F json -T 30
```

You should see JSON lines from Acurite bursts.

Optional SDR hardware check:

```bash
rtl_test -t
```

## 6. Run The Python Weather Logger

Recommended command (SQLite + CSV):

```bash
python -m acurite_logger --rtl433-path rtl_433 --db-path data/weather.db --csv-path logs/weather.csv
```

SQLite only:

```bash
python -m acurite_logger --rtl433-path rtl_433 --db-path data/weather.db
```

Short timed test (2 minutes):

```bash
python -m acurite_logger --rtl433-path rtl_433 --db-path data/weather.db --csv-path logs/weather.csv --extra-arg=-T --extra-arg=120
```

Notes:
- db-path is where the SQLite file is written.
- csv-path is optional. Omit it to disable CSV output.
- Parent directories are created automatically.

## 7. Verify Logged Data

Check latest rows in SQLite:

```bash
sqlite3 data/weather.db "select observed_at, model, sensor_id, temperature_f, humidity, wind_avg_mi_h, wind_dir_deg, rain_in, battery_ok from weather_observations order by id desc limit 10;"
```

Check the CSV file:

```bash
tail -n 10 logs/weather.csv
```

## 8. Troubleshooting

- Command not found for conda:
  - Open a new shell or run source ~/.bashrc.
- Command not found for rtl_433:
  - Ensure rtl-433 package installed: sudo apt install -y rtl-433.
- No data appears:
  - Run rtl_test -t to confirm SDR visibility.
  - Reposition antenna and wait through several transmit cycles.
- Duplicate rows in short bursts:
  - Normal behavior. Acurite sensors often repeat transmissions with sequence numbers.
