

# [TikTokDownloader](https://t.me/IWillMakeYouWatchTikTokByThisBot): Telegram bot for TikTok

## Description

This bot will send you a video from a TikTok.

Just share a link to the chat (no need to mention the bot)

## Thanks to

Built on top of [aiogram](https://github.com/aiogram/aiogram)

# Installation

## Env

(*REQUIRED*)

- API_TOKEN - Bot token from BotFather 

(*OPTIONAL*)
- USER_ID - To give access only to specific user id (default: empty = all users)
- SENTRY_DSN - To send unhandled exceptions to Sentry (default: empty = no reports)
- ENVIRONMENT - Sentry environment variable (default: Local)
- USER_AGENT - To override user-agent used to download videos (default: random every time)


## Local

```bash
$ git clone https://github.com/preckrasno/tiktok-downloader
$ cd tiktok-downloader
$ echo "API_TOKEN=foo:bar" >> .env
$ sudo apt update
$ sudo apt install python3-pip
$ sudo apt install python3.10-venv
$ chmod a+x start-tiktok-downloader.sh
$ ./start-tiktok-downloader.sh
```


