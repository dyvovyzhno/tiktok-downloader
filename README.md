# TikTok Downloader — Telegram Bot

Telegram bot that downloads TikTok videos. Share a link in any chat where the bot is present — it will reply with the video. No commands needed.

## Table of Contents

- [Features](#features)
- [Environment Variables](#environment-variables)
  - [Required](#required)
  - [Optional](#optional)
- [Supabase Setup](#supabase-setup)
- [Installation](#installation)
  - [Deploy Script Flags](#deploy-script-flags)
- [Local Development](#local-development)
- [Built With](#built-with)

## Features

- Automatic TikTok link detection (works in private chats, groups and channels)
- Author watermark overlay (optional, user chooses per video)
- Download queue with configurable concurrency (default: 2 parallel downloads)
- Anonymous usage analytics via Supabase
- Admin commands: `/stats`, `/broadcast`
- OpenTelemetry metrics export (Dash0 / Grafana Cloud)
- PyInstaller binary packaging for easy deployment

## Environment Variables

Create a `.env` file in the project root.

### Required

| Variable | Description |
|----------|-------------|
| `API_TOKEN` | Telegram bot token from [@BotFather](https://t.me/BotFather) |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_ID` | `0` | Telegram user ID for `/stats` and `/broadcast` access |
| `ANALYTICS_EXCLUDE_IDS` | | Comma-separated Telegram user IDs to exclude from analytics |
| `SUPABASE_URL` | | Supabase project URL (e.g. `https://xxx.supabase.co`) |
| `SUPABASE_KEY` | | Supabase `service_role` key |
| `SENTRY_DSN` | | Sentry DSN for error tracking |
| `ENVIRONMENT` | `Local` | Sentry environment tag |
| `USER_AGENT` | random | Override User-Agent for TikTok requests |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Max parallel video downloads |
| `OTEL_ENDPOINT` | | OpenTelemetry gRPC endpoint |
| `OTEL_AUTH_TOKEN` | | OpenTelemetry auth token |
| `OTEL_SERVICE_NAME` | `tiktok-downloader` | OpenTelemetry service name |

## Supabase Setup

If you want cloud analytics, create a Supabase project and run `supabase_schema.sql` in the SQL Editor. Use the `service_role` key (Settings → API) as `SUPABASE_KEY`.

## Installation

```bash
git clone https://github.com/preckrasno/tiktok-downloader
cd tiktok-downloader
echo "API_TOKEN=your_token_here" >> .env
chmod a+x start-tiktok-downloader.sh
./start-tiktok-downloader.sh
```

The script creates a venv, installs dependencies, builds a PyInstaller binary and starts the bot. It also sets up a cron job to keep the bot alive.

### Deploy Script Flags

| Flag | Description |
|------|-------------|
| (none) | Keep-alive: start the bot if not running |
| `-d` | Deploy: `git pull`, rebuild, restart |
| `-r` | Hard reset: wipe venv/dist, rebuild from scratch |
| `-s` | Stop the bot |
| `-h` | Show help |

## Local Development

For day-to-day development, skip the PyInstaller build and run `main.py` directly — it picks up code changes on the next restart without a rebuild.

Use a **separate Telegram bot token** for local runs so you don't intercept updates intended for production. Create one with [@BotFather](https://t.me/BotFather) and put it in `.env` as `API_TOKEN`.

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python main.py
```

Stop with `Ctrl+C`. Logs go to stdout.

## Built With

- [aiogram](https://github.com/aiogram/aiogram) 2.19 — Telegram Bot framework
- [httpx](https://github.com/encode/httpx) — HTTP client (TikTok API + Supabase)
- [ffmpeg](https://ffmpeg.org/) — Video watermark overlay (must be installed on the server)
