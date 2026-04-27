# bot/overlay.py

import asyncio
import logging
import os
import re
import shutil
import tempfile
from typing import Optional


# Watermark size presets — each value is the divisor used in FFmpeg drawtext
# `fontsize=h/N` (so larger N = smaller text). Bounds chosen so the smallest
# remains legible on 720p video and the largest does not dominate the frame.
WATERMARK_PRESETS: dict[str, int] = {
    "tiny": 70,
    "small": 54,
    "medium": 43,
    "large": 35,
    "xl": 29,
}
DEFAULT_WATERMARK_SIZE = "small"

# UI metadata for the size presets (kept next to the divisors so they stay in sync).
WATERMARK_SIZE_LABELS_UA: dict[str, str] = {
    "tiny": "Крихітна",
    "small": "Маленька",
    "medium": "Середня",
    "large": "Велика",
    "xl": "Дуже велика",
}
WATERMARK_SIZE_LABELS_SHORT: dict[str, str] = {
    "tiny": "XS",
    "small": "S",
    "medium": "M",
    "large": "L",
    "xl": "XL",
}


def _escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
    )


_SHOWINFO_PTS_RE = re.compile(r"pts_time:([\d.]+)")
_SHOWINFO_ISKEY_RE = re.compile(r"iskey:(\d)")


async def _probe_duration(path: str) -> Optional[float]:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nokey=1:noprint_wrappers=1",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        logging.warning(
            f"ffprobe failed ({proc.returncode}): "
            f"{err.decode(errors='replace')[:300]}"
        )
        return None
    try:
        return float(out.decode().strip())
    except (ValueError, UnicodeDecodeError):
        logging.warning("ffprobe returned non-numeric duration")
        return None


async def _detect_outro_start(path: str, duration: float) -> Optional[float]:
    """Find the TikTok outro boundary in ``path``.

    TikTok concatenates the outro onto the user's clip and inserts a keyframe
    at the splice point; the frame itself is a strong scene change (the
    outro's first frame is visually unrelated to the content). We run ffmpeg
    with the scene filter + showinfo, then pick the latest keyframe scene
    change that falls into the expected tail window (2.0–5.5s from end).
    Returns None if nothing matches.
    """
    if duration < 4.0:
        return None
    tail_min = duration - 5.5
    tail_max = duration - 2.0

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "info", "-y",
        "-i", path,
        "-vf", "select='gt(scene,0.3)',showinfo",
        "-an", "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        return None

    best: Optional[float] = None
    for line in err.decode(errors="replace").splitlines():
        if "showinfo" not in line:
            continue
        mk = _SHOWINFO_ISKEY_RE.search(line)
        if not mk or mk.group(1) != "1":
            continue
        mt = _SHOWINFO_PTS_RE.search(line)
        if not mt:
            continue
        t = float(mt.group(1))
        if tail_min <= t <= tail_max:
            if best is None or t > best:
                best = t
    return best


async def _cut_reencode(path: str, to_seconds: float) -> Optional[bytes]:
    """Frame-accurate cut via libx264 re-encode. Writes to a temp file (not a
    pipe) so we can use ``+faststart`` without ``frag_keyframe`` — the latter
    would extend the last fragment up to the next keyframe and pull the outro
    back in."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_out:
        out_path = tmp_out.name
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-y",
            "-i", path,
            "-to", f"{to_seconds:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-f", "mp4", out_path,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            logging.warning(
                f"ffmpeg re-encode trim failed ({proc.returncode}): "
                f"{err.decode(errors='replace')[:300]}"
            )
            return None
        with open(out_path, "rb") as f:
            data = f.read()
        if not data:
            return None
        return data
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


async def strip_tiktok_outro(video_bytes: bytes,
                             fallback_seconds: float = 0.0) -> bytes:
    """Strip TikTok's auto-generated outro from a method-1 (downloadAddr) MP4.

    Locates the real outro boundary via scene/keyframe detection and cuts one
    frame before it with a libx264 re-encode (exact). If detection fails and
    ``fallback_seconds > 0``, trims that many seconds off the tail as a last
    resort — but that over-trims no-outro videos, so the default is 0 (leave
    video untouched when unsure).
    """
    if not video_bytes:
        return video_bytes
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return video_bytes

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(video_bytes)
        tmp_path = tmp.name

    try:
        duration = await _probe_duration(tmp_path)
        if duration is None:
            return video_bytes

        outro_start = await _detect_outro_start(tmp_path, duration)
        if outro_start is not None:
            # Cut ~1 frame before the outro's first keyframe to exclude it.
            target = max(0.1, outro_start - 0.04)
            out = await _cut_reencode(tmp_path, target)
            if out:
                logging.info(
                    f"outro stripped (detected at {outro_start:.2f}s): "
                    f"{duration:.2f}s -> {target:.2f}s "
                    f"(bytes {len(video_bytes)} -> {len(out)})"
                )
                return out
            logging.warning("detected outro cut failed, falling back")

        if fallback_seconds <= 0:
            logging.info(
                f"no outro detected in {duration:.2f}s video — leaving untouched"
            )
            return video_bytes

        target = duration - fallback_seconds
        if target <= 0.1:
            logging.info(
                f"skip outro trim: duration={duration:.2f}s "
                f"<= trim={fallback_seconds:.2f}s"
            )
            return video_bytes

        out = await _cut_reencode(tmp_path, target)
        if not out:
            return video_bytes
        logging.info(
            f"outro stripped (fallback {fallback_seconds:.1f}s): "
            f"{duration:.2f}s -> {target:.2f}s "
            f"(bytes {len(video_bytes)} -> {len(out)})"
        )
        return out
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def add_author_overlay(video_bytes: bytes, author: str,
                              size: str = DEFAULT_WATERMARK_SIZE) -> bytes:
    if not author or not shutil.which("ffmpeg"):
        return video_bytes

    divisor = WATERMARK_PRESETS.get(size, WATERMARK_PRESETS[DEFAULT_WATERMARK_SIZE])
    text = _escape_drawtext(f"@{author}")
    vf = (
        f"drawtext=text='{text}'"
        ":fontcolor=white@0.85"
        f":fontsize=h/{divisor}"
        ":borderw=2:bordercolor=black@0.6"
        ":x=w-tw-20:y=h-th-20"
    )
    cmd = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "mp4", "-i", "pipe:0",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "frag_keyframe+empty_moov+faststart",
        "-f", "mp4", "pipe:1",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(input=video_bytes)
    if proc.returncode != 0 or not out:
        logging.warning(f"ffmpeg overlay failed ({proc.returncode}): {err.decode(errors='replace')[:500]}")
        return video_bytes
    return out
