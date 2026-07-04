# Raspberry Pi Rebuild Setup Guide

This is the full rebuild checklist for a clean Raspberry Pi OS install.

Use this as the primary setup document. The shorter [web_publish_setup.md](web_publish_setup.md) note is just a publish-side reference and should not duplicate the rebuild steps.

It covers:

1. weather logger setup (`AcuriteWeatherRadio`)
2. static web publish setup (`PleasantStreetWeather`)
3. GitHub Pages and Git auth configuration
4. automatic startup on reboot using `systemd`
5. SDR driver conflict fixes (`usb_claim_interface error -6`)

## Expected Folder Layout

```text
/home/schuelaw/GitHub/AcuriteWeatherRadio
/home/schuelaw/GitHub/PleasantStreetWeather
```

## 1. Install Required OS Packages

```bash
sudo apt update
sudo apt install -y git wget bzip2 libusb-1.0-0 rtl-433 rtl-sdr sqlite3
```

## 2. Install Miniconda

Check architecture:

```bash
uname -m
```

If architecture is `aarch64`:

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

## 3. Clone Both Repositories

```bash
mkdir -p /home/schuelaw/GitHub
cd /home/schuelaw/GitHub
git clone git@github.com:schuefone/AcuriteWeatherRadio.git
git clone git@github.com:schuefone/PleasantStreetWeather.git
```

If already cloned:

```bash
cd /home/schuelaw/GitHub/AcuriteWeatherRadio
git pull
cd /home/schuelaw/GitHub/PleasantStreetWeather
git pull
```

## 4. Create Conda Environment `weather`

```bash
cd /home/schuelaw/GitHub/AcuriteWeatherRadio
conda env create -n weather -f environment.yml
conda activate weather
```

If env already exists:

```bash
conda env update -n weather -f environment.yml --prune
conda activate weather
```

Smoke test:

```bash
python -m acurite_logger --help
python -m acurite_logger.site_builder --help
```

## 5. Configure GitHub Pages

The live Pages site is deployed from the `PleasantStreetWeather` repository. Keep the current Pi push flow, but switch that repository from branch-based Pages deployment to GitHub Actions so new deploys can cancel older in-progress deploys.

In the `PleasantStreetWeather` repository:

1. Create `.github/workflows/deploy.yml` using the exact workflow shown in `web_publish_setup.md`.
2. In repository settings, set `Pages -> Source` to `GitHub Actions`.

Do not add this workflow to `AcuriteWeatherRadio` unless you also change the architecture so this repository becomes the one that receives and deploys the generated site files.

## 6. Configure Git Auth For Unattended Pushes

Use a deploy key scoped to `PleasantStreetWeather`.

Generate key on Pi (blank passphrase):

```bash
ssh-keygen -t ed25519 -C "pi-weather-deploy" -f ~/.ssh/pleasant_street_deploy
cat ~/.ssh/pleasant_street_deploy.pub
```

Add this public key at:

1. `PleasantStreetWeather` repo
2. Settings -> Deploy keys
3. Check `Allow write access`

Create SSH host alias:

```bash
cat >> ~/.ssh/config << 'EOF'
Host github-pleasant-street
    HostName github.com
    User git
    IdentityFile ~/.ssh/pleasant_street_deploy
EOF
chmod 600 ~/.ssh/config
```

Set `PleasantStreetWeather` remote to alias:

```bash
cd /home/schuelaw/GitHub/PleasantStreetWeather
git remote set-url origin git@github-pleasant-street:schuefone/PleasantStreetWeather.git
ssh -T git@github-pleasant-street
```

Set Git commit identity on Pi:

```bash
git config --global user.name "Albert Schuelaw"
git config --global user.email "schuelaw@whitman.edu"
```

## 7. First Logger Run And Data Check

The logger now does two things at once:

1. stores raw packets in `weather_observations`
2. writes merged 5-minute snapshots into `weather_snapshots`

The snapshot interval defaults to 300 seconds. If you ever want to change it, pass `--snapshot-interval-seconds` to the logger command or systemd unit.

Quick RF test:

```bash
rtl_433 -d 0 -f 433920000 -M time:iso:utc -F json -T 30
```

Run logger:

```bash
cd /home/schuelaw/GitHub/AcuriteWeatherRadio
mkdir -p data logs
python -m acurite_logger --rtl433-path rtl_433 --db-path data/weather.db --csv-path logs/weather.csv
```

Check database:

```bash
sqlite3 /home/schuelaw/GitHub/AcuriteWeatherRadio/data/weather.db ".tables"
sqlite3 /home/schuelaw/GitHub/AcuriteWeatherRadio/data/weather.db "select observed_at, model, temperature_f, humidity, wind_avg_mi_h, rain_in from weather_observations order by id desc limit 10;"
sqlite3 /home/schuelaw/GitHub/AcuriteWeatherRadio/data/weather.db "select observed_at, model, temperature_f, humidity, wind_avg_mi_h, rain_day_in, rain_ytd_in from weather_snapshots order by id desc limit 10;"
```

## 8. First Static Publish Test

```bash
cd /home/schuelaw/GitHub/AcuriteWeatherRadio
chmod +x scripts/publish_static_weather.sh
./scripts/publish_static_weather.sh
```

Check web repo:

```bash
cd /home/schuelaw/GitHub/PleasantStreetWeather
git log -1 --oneline
ls -l index.html data.json
```

Live URL:

```text
https://schuefone.github.io/PleasantStreetWeather/
```

## 9. Auto-Start On Reboot (Systemd)

Create logger service:

```bash
sudo tee /etc/systemd/system/acurite-logger.service >/dev/null <<'EOF'
[Unit]
Description=Acurite RTL433 Logger
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=schuelaw
WorkingDirectory=/home/schuelaw/GitHub/AcuriteWeatherRadio
Environment=PATH=/home/schuelaw/miniconda3/envs/weather/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/home/schuelaw/miniconda3/envs/weather/bin/python -m acurite_logger --rtl433-path rtl_433 --db-path /home/schuelaw/GitHub/AcuriteWeatherRadio/data/weather.db --csv-path /home/schuelaw/GitHub/AcuriteWeatherRadio/logs/weather.csv
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Create publish one-shot service:

```bash
sudo tee /etc/systemd/system/weather-publish.service >/dev/null <<'EOF'
[Unit]
Description=Publish static weather page to GitHub

[Service]
Type=oneshot
User=schuelaw
WorkingDirectory=/home/schuelaw/GitHub/AcuriteWeatherRadio
Environment=PATH=/home/schuelaw/miniconda3/envs/weather/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/bin/bash /home/schuelaw/GitHub/AcuriteWeatherRadio/scripts/publish_static_weather.sh
EOF
```

Create publish timer (every 5 min):

```bash
sudo tee /etc/systemd/system/weather-publish.timer >/dev/null <<'EOF'
[Unit]
Description=Run weather publish every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true
Unit=weather-publish.service

[Install]
WantedBy=timers.target
EOF
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now acurite-logger.service
sudo systemctl enable --now weather-publish.timer
```

Status checks:

```bash
systemctl is-enabled acurite-logger.service
systemctl status acurite-logger.service --no-pager
systemctl status weather-publish.timer --no-pager
journalctl -u acurite-logger.service -n 50 --no-pager
journalctl -u weather-publish.service -n 50 --no-pager
```

## 10. Optional Repo Maintenance: Monthly Git History Prune

If you want the generated website repo to stay one commit deep, add this optional monthly prune job. It rewrites the `PleasantStreetWeather` history to a single current-state commit and force-pushes it with lease protection.

Create the prune script:

```bash
mkdir -p /home/schuelaw/bin
cat > /home/schuelaw/bin/prune-weather-history.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

REPO="/home/schuelaw/GitHub/PleasantStreetWeather"
cd "$REPO"

git fetch origin main
git checkout main
git reset --hard origin/main
git clean -fdx

TMP_BRANCH="prune-$(date +%Y%m%d-%H%M%S)"
git checkout --orphan "$TMP_BRANCH"
git add -A
git commit -m "Weather site current state ($(date -u +'%Y-%m-%d %H:%M UTC'))"

git branch -M main
git push --force-with-lease origin main
EOF
chmod +x /home/schuelaw/bin/prune-weather-history.sh
```

Create the prune service:

```bash
sudo tee /etc/systemd/system/weather-history-prune.service >/dev/null <<'EOF'
[Unit]
Description=Monthly prune of PleasantStreetWeather git history

[Service]
Type=oneshot
User=schuelaw
ExecStartPre=/bin/systemctl stop weather-publish.timer
ExecStart=/bin/bash /home/schuelaw/bin/prune-weather-history.sh
ExecStartPost=/bin/systemctl start weather-publish.timer
EOF
```

Create the monthly timer:

```bash
sudo tee /etc/systemd/system/weather-history-prune.timer >/dev/null <<'EOF'
[Unit]
Description=Run weather history prune monthly

[Timer]
OnCalendar=monthly
Persistent=true
RandomizedDelaySec=20m
Unit=weather-history-prune.service

[Install]
WantedBy=timers.target
EOF
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now weather-history-prune.timer
systemctl status weather-history-prune.timer --no-pager
```

Optional manual test:

```bash
sudo systemctl start weather-history-prune.service
journalctl -u weather-history-prune.service -n 50 --no-pager
```

## 11. SDR Claim Conflict Fix (`usb_claim_interface error -6`)

Blacklist conflicting DVB modules:

```bash
sudo tee /etc/modprobe.d/blacklist-rtl-sdr.conf >/dev/null <<'EOF'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832_sdr
blacklist rtl2830
EOF
sudo update-initramfs -u
sudo reboot
```

After reboot, verify blacklist worked:

```bash
lsmod | grep -E "dvb_usb_rtl28xxu|rtl2832_sdr|rtl2830"
```

Expected: no output.

If the logger still fails after reboot, run:

```bash
sudo systemctl stop acurite-logger.service
sudo modprobe -r dvb_usb_rtl28xxu rtl2832_sdr rtl2830 dvb_usb_v2 dvb_core
sudo systemctl start acurite-logger.service
```

## 12. Rebuild Verification Checklist

After any OS reinstall, verify these in order:

1. `conda activate weather` works.
2. `python -m acurite_logger --help` works.
3. `acurite-logger.service` is `enabled` and `active`.
4. `weather-publish.timer` is `enabled` and `active`.
5. `./scripts/publish_static_weather.sh` pushes successfully.
6. live page loads at `https://schuefone.github.io/PleasantStreetWeather/`.
