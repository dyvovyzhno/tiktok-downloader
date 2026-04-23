# bot/api/tiktok.py

import asyncio
import json
import logging
import random
import string
from datetime import datetime
from functools import wraps
from dataclasses import dataclass
from typing import AsyncIterator, Optional
import httpx
from aiogram.types import Message
from bot.overlay import strip_tiktok_outro
from settings import OUTRO_TRIM_SECONDS, USER_AGENT

class Retrying(Exception):
    pass


@dataclass
class TikTokVideo:
    content: bytes
    author: Optional[str] = None
    has_watermark: bool = False

def retries(times: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(times):
                try:
                    return await func(*args, **kwargs)
                except Retrying as ex:
                    logging.warning(f"Retrying attempt {attempt + 1} of {times} failed: {str(ex)}")
                    last_exception = ex
                    await asyncio.sleep(0.5 * (attempt + 1))
            logging.warning("All retry attempts failed.")
            raise last_exception
        return wrapper
    return decorator

class TikTokAPI:

    TIKWM_ENDPOINT = "https://www.tikwm.com/api/"
    UNIVERSAL_DATA_MARKER = (
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
    )

    def __init__(self, headers=None):
        self.headers = headers or {}
        self.link = 'tiktok.com'

    async def handle_message(self, message: Message) -> AsyncIterator[TikTokVideo]:
        urls = self._extract_urls_from_message(message)
        for url in urls:
            video = await self.download_video(url)
            yield video

    def _extract_urls_from_message(self, message: Message):
        entries = (message.text[e.offset:e.offset + e.length] for e in message.entities)
        return map(
            lambda u: u if u.startswith('http') else f'https://{u}',
            filter(lambda e: self.link in e, entries)
        )

    def _parse_universal_data(self, response_text: str) -> dict:
        """Extract itemStruct from a TikTok page's UNIVERSAL_DATA script tag."""
        start = response_text.find(self.UNIVERSAL_DATA_MARKER)
        if start == -1:
            raise Retrying("No UNIVERSAL_DATA script tag (page blocked or changed)")
        start += len(self.UNIVERSAL_DATA_MARKER)
        end = response_text.find('</script>', start)
        if end == -1:
            raise Retrying("Malformed UNIVERSAL_DATA script tag")

        try:
            data = json.loads(response_text[start:end])
        except json.JSONDecodeError:
            raise Retrying("Failed to parse UNIVERSAL_DATA JSON")

        video_detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
        sc = video_detail.get("statusCode", 0)
        if sc != 0:
            status_msg = video_detail.get("statusMsg", "") or "?"
            raise Retrying(
                f"TikTok statusCode={sc} ({status_msg}) — "
                f"video likely private/deleted/region-locked"
            )

        item = video_detail.get("itemInfo", {}).get("itemStruct")
        if not item:
            keys = list(video_detail.keys())[:5]
            raise Retrying(
                f"No itemInfo in video-detail (keys: {keys}) — "
                f"likely private/deleted/blocked video"
            )
        return item

    async def _primary_method(self, client, url):
        """Fetch TikTok's built-in watermarked MP4.

        Two strategies, in order:
          1. Parse the web page's UNIVERSAL_DATA and use ``downloadAddr`` (the
             watermarked variant TikTok itself serves). Zero external deps.
          2. Fall back to tikwm.com which signs the mobile API for us.

        The watermark is baked into the file by TikTok's CDN, so no ffmpeg
        overlay is needed downstream. Raises Retrying on total failure so the
        caller can fall through to the non-watermarked secondary path.
        """
        try:
            return await self._primary_via_web(client, url)
        except Retrying as e:
            logging.info(f"method 1 (tiktok web downloadAddr): failed — {e}, trying method 2")
        return await self._primary_via_tikwm(client, url)

    async def _primary_via_web(self, client, url):
        logging.info("method 1 (tiktok web downloadAddr): trying")
        response = await client.get(url, headers=self._user_agent)
        if response.status_code != 200:
            raise Retrying(f"page status {response.status_code}")

        item = self._parse_universal_data(response.text)
        author = (item.get("author") or {}).get("uniqueId")
        download_addr = (item.get("video") or {}).get("downloadAddr")
        if not download_addr:
            raise Retrying("downloadAddr missing in UNIVERSAL_DATA")

        cdn_headers = {"Referer": "https://www.tiktok.com/", **self._user_agent}
        video = await client.get(download_addr, headers=cdn_headers)
        if video.status_code != 200 or not video.content:
            raise Retrying(
                f"downloadAddr fetch failed "
                f"(status={video.status_code}, bytes={len(video.content)})"
            )
        logging.info(
            f"method 1 (tiktok web downloadAddr): ok — "
            f"bytes={len(video.content)}, author={author}"
        )
        # TikTok bakes an auto-generated outro (~4s) into downloadAddr MP4s.
        content = await strip_tiktok_outro(video.content, OUTRO_TRIM_SECONDS)
        return TikTokVideo(content=content, author=author, has_watermark=True)

    async def _primary_via_tikwm(self, client, url):
        logging.info("method 2 (tikwm wmplay): trying")
        try:
            resp = await client.post(self.TIKWM_ENDPOINT, data={"url": url, "hd": "1"})
        except httpx.HTTPError as e:
            raise Retrying(f"tikwm request failed: {e}")

        if resp.status_code != 200:
            raise Retrying(f"tikwm api status {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            raise Retrying("tikwm non-JSON response")

        if payload.get("code") != 0:
            raise Retrying(f"tikwm code={payload.get('code')} msg={payload.get('msg')}")

        data = payload.get("data") or {}
        wm_url = data.get("wmplay")
        if not wm_url:
            raise Retrying("tikwm wmplay missing")
        # tikwm returns the same URL for wmplay and play when TikTok has no
        # separate watermarked variant — the file is unwatermarked despite the
        # field name. Detect this so we fall through to secondary + overlay.
        if wm_url == data.get("play"):
            raise Retrying("tikwm wmplay equals play (no watermarked variant)")

        author = (data.get("author") or {}).get("unique_id")
        cdn_headers = {"Referer": "https://www.tiktok.com/", **self._user_agent}
        video = await client.get(wm_url, headers=cdn_headers)
        if video.status_code != 200 or not video.content:
            raise Retrying(
                f"tikwm wmplay fetch failed "
                f"(status={video.status_code}, bytes={len(video.content)})"
            )
        logging.info(
            f"method 2 (tikwm wmplay): ok — "
            f"bytes={len(video.content)}, author={author}"
        )
        return TikTokVideo(content=video.content, author=author, has_watermark=True)

    async def _secondary_method(self, client, url):
        logging.info("method 3 (tiktok web no-watermark): trying")
        response = await client.get(url, headers=self._user_agent)
        if response.status_code != 200:
            raise Retrying("Invalid response status code")

        item = self._parse_universal_data(response.text)
        author = (item.get("author", {}) or {}).get("uniqueId")
        video = item.get("video", {})
        cdn_headers = {"Referer": "https://www.tiktok.com/", **self._user_agent}
        for addr_key in ("playAddr", "downloadAddr"):
            download_link = video.get(addr_key)
            if not download_link:
                continue
            video_response = await client.get(download_link, headers=cdn_headers)
            if video_response.status_code != 200 or not video_response.content:
                continue
            logging.info(
                f"method 3 (tiktok web no-watermark): ok — "
                f"addr={addr_key}, bytes={len(video_response.content)}, author={author}"
            )
            return TikTokVideo(content=video_response.content, author=author)

        raise Retrying("No working video link found")


    @retries(times=3)
    async def download_video(self, url: str, prefer_watermarked: bool = False) -> TikTokVideo:
        async with httpx.AsyncClient(headers=self.headers, timeout=30,
                                    cookies=self._tt_webid_v2, follow_redirects=True) as client:
            if prefer_watermarked:
                try:
                    return await self._primary_method(client, url)
                except Retrying as primary_error:
                    logging.info(
                        f"method 2 (tikwm wmplay): failed — {primary_error}, "
                        f"trying method 3"
                    )
            return await self._secondary_method(client, url)

    @property
    def _user_agent(self) -> dict:
        return {
            'User-Agent': USER_AGENT or (
                f"{''.join(random.choices(string.ascii_lowercase, k=random.randint(4,10)))}-"
                f"{''.join(random.choices(string.ascii_lowercase, k=random.randint(3,7)))}/"
                f"{random.randint(10, 300)} "
                f"({datetime.now().replace(microsecond=0).timestamp()})"
            )
        }

    @property
    def _tt_webid_v2(self):
        return {'tt_webid_v2': f"{random.randint(10 ** 18, (10 ** 19) - 1)}"}
