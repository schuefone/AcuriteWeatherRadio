# Publish Static Weather Page From Raspberry Pi

This guide matches your folder layout on the Pi:

- `/home/schuelaw/GitHub/AcuriteWeatherRadio`
- `/home/schuelaw/GitHub/PleasantStreetWeather`

The flow is:

1. logger writes to `AcuriteWeatherRadio/data/weather.db`
2. static page generator reads that DB
3. generated files are written to `PleasantStreetWeather/index.html` and `PleasantStreetWeather/data.json`
4. commit and push updates to GitHub Pages repo

## 1. Verify GitHub Pages Settings

In your `PleasantStreetWeather` repository settings:

- Source: `Deploy from a branch`
- Branch: `main`
- Folder: `/(root)`

## 2. Commit New Generator Files In Logger Repo

On the Pi:

```bash
cd /home/schuelaw/GitHub/AcuriteWeatherRadio
git pull
```

You should have these files:

- `acurite_logger/site_builder.py`
- `scripts/publish_static_weather.sh`
- `web_publish_setup.md`

## 3. Quick One-Time Static Page Build Test

Activate Conda env first:

```bash
conda activate weather
```

Then run:

```bash
cd /home/schuelaw/GitHub/AcuriteWeatherRadio
python -m acurite_logger.site_builder \
  --db-path data/weather.db \
  --site-dir ../PleasantStreetWeather \
  --title "Pleasant Street Weather" \
  --station-label "Pleasant Street Outdoor Sensor"
```

Verify files were created in the web repo:

```bash
cd /home/schuelaw/GitHub/PleasantStreetWeather
ls -l index.html data.json
```

## 4. Publish Manually

From logger repo:

```bash
cd /home/schuelaw/GitHub/AcuriteWeatherRadio
chmod +x scripts/publish_static_weather.sh
./scripts/publish_static_weather.sh
```

That command will:

- generate `index.html` and `data.json`
- commit if files changed
- push to GitHub

## 5. Automate Every 5 Minutes With Cron

Edit crontab:

```bash
crontab -e
```

Add this line:

```cron
*/5 * * * * /bin/bash -lc 'source /home/schuelaw/miniconda3/etc/profile.d/conda.sh; conda activate weather; cd /home/schuelaw/GitHub/AcuriteWeatherRadio; ./scripts/publish_static_weather.sh >> /home/schuelaw/GitHub/AcuriteWeatherRadio/logs/publish.log 2>&1'
```

If your Miniconda install path is different, update the `source` path accordingly.

## 6. First External Access Test

After the first publish completes, open:

```text
https://<your-github-username>.github.io/PleasantStreetWeather/
```

The page may take a minute or two after initial settings change.

## 7. Helpful Checks

Tail cron publish log:

```bash
tail -f /home/schuelaw/GitHub/AcuriteWeatherRadio/logs/publish.log
```

Check latest weather rows in SQLite:

```bash
sqlite3 /home/schuelaw/GitHub/AcuriteWeatherRadio/data/weather.db "select observed_at, model, temperature_f, humidity, wind_avg_mi_h, rain_in from weather_observations order by id desc limit 10;"
```
