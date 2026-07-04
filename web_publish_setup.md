# Publish Static Weather Page

This is the short operational note for the publish side of the project.

The full rebuild and setup flow now lives in [pi_setup.md](pi_setup.md).

Use this file only for the GitHub Pages-specific details that are distinct from the logger and reboot setup.

## GitHub Pages Settings

The live GitHub Pages site is published from the `PleasantStreetWeather` repository, not from `AcuriteWeatherRadio`.

If you keep the current two-repo setup, add the GitHub Actions workflow to `PleasantStreetWeather` at `.github/workflows/deploy.yml` and switch that repository's Pages source to `GitHub Actions`.

Use this workflow in `PleasantStreetWeather`:

```yaml
name: Deploy Weather Page

on:
  push:
    branches:
      - main

concurrency:
  group: pages
  cancel-in-progress: true

permissions:
  contents: read
  pages: write
  id-token: write

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Pages
        uses: actions/configure-pages@v5

      - name: Upload Artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: .

      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

Why this repo should not get that workflow as-is:

- the Pi currently pushes generated `index.html` and `data.json` into `PleasantStreetWeather`
- GitHub Pages serves the live site from `PleasantStreetWeather`
- adding the workflow to `AcuriteWeatherRadio` would not change deployment collisions for the live site

## Publish Flow

The page generator reads merged snapshot rows from `weather_snapshots` when available, so the site shows one complete row per interval instead of alternating raw bursts.

The logger repo is responsible for:

1. writing raw packets to `weather_observations`
2. writing merged 5-minute snapshots to `weather_snapshots`
3. generating `index.html` and `data.json`
4. committing and pushing updates to the `PleasantStreetWeather` repo

With the recommended GitHub Actions setup, step 4 stays the same. The difference is that each push to `PleasantStreetWeather` triggers an Actions-based Pages deploy with `concurrency` protection, so a new publish cancels an older in-flight Pages deployment instead of colliding with it.

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
