# Publish Static Weather Page

This is the short operational note for the publish side of the project.

The full rebuild and setup flow now lives in [pi_setup.md](pi_setup.md).

Use this file only for the GitHub Pages-specific details that are distinct from the logger and reboot setup.

## GitHub Pages Settings

In the `PleasantStreetWeather` repository settings:

- Source: `Deploy from a branch`
- Branch: `main`
- Folder: `/(root)`

## Publish Flow

The page generator reads merged snapshot rows from `weather_snapshots` when available, so the site shows one complete row per interval instead of alternating raw bursts.

The logger repo is responsible for:

1. writing raw packets to `weather_observations`
2. writing merged 5-minute snapshots to `weather_snapshots`
3. generating `index.html` and `data.json`
4. committing and pushing updates to the `PleasantStreetWeather` repo

## Manual Publish

From the logger repo on the Pi:

```bash
cd /home/schuelaw/GitHub/AcuriteWeatherRadio
./scripts/publish_static_weather.sh
```

## Timed Publish

If you want to verify the timer or cron job, the relevant command still runs from the logger repo and pushes to `PleasantStreetWeather` every 5 minutes by default.

See [pi_setup.md](pi_setup.md) for the full `systemd` and automation setup.

If you also want the generated repo to be periodically squashed back to a single current-state commit, the monthly prune setup is documented in [pi_setup.md](pi_setup.md) as well.
