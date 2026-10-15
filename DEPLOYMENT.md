# Deployment Guide — trading-bot

The platform is a Python server (`run_platform.py`) with a background loop that
pulls live market data. There is no build step. Requirements: Python 3.10+ and
`pip install ccxt`. Everything runs in demo/simulated mode — nothing you host
can place a real order.

## Option 1 — Your own computer (30 seconds)

```bash
cd trading-bot
pip install ccxt
python3 run_platform.py --port 8787
# open http://127.0.0.1:8787/
```

The dashboard runs in your browser while the terminal stays open.

## Option 2 — Always-on home machine, viewed from your phone/laptop

Run it on any always-on machine (old laptop, Raspberry Pi, mini PC):

```bash
cd trading-bot && python3 run_platform.py --port 8787
```

Reach it through an SSH tunnel from your viewing device:

```bash
ssh -L 8787:localhost:8787 you@your-machine
# then open http://localhost:8787 on your device
```

## Option 3 — Cloud VPS (a few dollars a month)

1. Rent a small VPS (Hetzner / DigitalOcean / AWS t3.nano).
2. Copy the project over and run it as a service:

```ini
# /etc/systemd/system/trading-bot.service
[Unit]
Description=trading-bot demo platform
After=network.target

[Service]
WorkingDirectory=/opt/trading-bot
ExecStart=/usr/bin/python3 run_platform.py --port 8787
Restart=always
User=trading

[Install]
WantedBy=multi-user.target
```

3. Access via SSH tunnel (same as Option 2) or a reverse proxy (Caddy/nginx)
   in front WITH authentication.

## Security note — the localhost bind is deliberate

`run_platform.py` REFUSES to bind anything except `127.0.0.1`:

```
REFUSED: this demo server binds 127.0.0.1 only.
```

That guard is on purpose. Do not expose this server directly to the internet.
If you truly need public access, put real authentication in front of it
(Caddy with basic auth / Cloudflare Access) and only then change the bind —
that change should be made deliberately, not by accident.

## What does NOT work

- **GitHub Pages / Netlify / Vercel (static hosting):** cannot run the Python
  trading loop. However, the generated reports (`reports/dashboard.html`,
  `reports/portfolio-growth.svg`) are static files and CAN be published on
  GitHub Pages as a read-only results page.

## Recommended path

Start with Option 1 today. If you want it always-on, use a small VPS + SSH
tunnel (Option 3). Remember: demo/simulated only — hosting changes nothing
about that.
