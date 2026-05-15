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


# How far on either side of TikTok's reported content duration we look for the
# exact outro splice keyframe. The metadata is integer-rounded, so the true
# splice sits within ~1s of it; the margin covers that plus probe slack.
_OUTRO_SEARCH_MARGIN = 1.5


async def _detect_outro_splice(path: str,
                               content_duration: float) -> Optional[float]:
    """Refine the outro splice point near ``content_duration``.

    TikTok's metadata already tells us the real clip runs ~content_duration;
    the outro begins at that splice with a hard scene change and an inserted
    keyframe. We run ffmpeg's scene filter + showinfo and return the
    scene-change keyframe closest to content_duration (within
    ±_OUTRO_SEARCH_MARGIN). Returns None if none fall in that window.
    """
    search_min = content_duration - _OUTRO_SEARCH_MARGIN
    search_max = content_duration + _OUTRO_SEARCH_MARGIN

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
    best_dist: Optional[float] = None
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
        if not (search_min <= t <= search_max):
            continue
        dist = abs(t - content_duration)
        if best_dist is None or dist < best_dist:
            best, best_dist = t, dist
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


# A TikTok outro is a few seconds of branded animation baked onto the end of
# the downloadAddr MP4. We treat the file as carrying an outro only when it
# runs longer than TikTok's reported content duration by an amount inside this
# range — outside it the metadata is unreliable, so we leave the file alone
# rather than risk cutting real content.
OUTRO_MIN_SECONDS = 1.0
OUTRO_MAX_SECONDS = 8.0


async def strip_tiktok_outro(video_bytes: bytes,
                             content_duration: Optional[float]) -> bytes:
    """Strip TikTok's auto-generated outro from a method-1 (downloadAddr) MP4.

    ``content_duration`` is TikTok's own reported length of the real clip
    (from UNIVERSAL_DATA). The downloadAddr file is that clip plus a baked-in
    outro, so ``file_duration - content_duration`` is the outro length. We
    trim only when that difference lands in a plausible outro range, then use
    scene detection to refine TikTok's integer-rounded boundary to the exact
    splice keyframe. When unsure, the file is returned untouched.
    """
    if not video_bytes:
        return video_bytes
    if content_duration is None or content_duration <= 0:
        return video_bytes
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return video_bytes

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(video_bytes)
        tmp_path = tmp.name

    try:
        file_duration = await _probe_duration(tmp_path)
        if file_duration is None:
            return video_bytes

        excess = file_duration - content_duration
        if excess < OUTRO_MIN_SECONDS:
            logging.info(
                f"no outro: file {file_duration:.2f}s vs content "
                f"{content_duration:.2f}s — leaving untouched"
            )
            return video_bytes
        if excess > OUTRO_MAX_SECONDS:
            logging.info(
                f"implausible outro {excess:.2f}s (file {file_duration:.2f}s, "
                f"content {content_duration:.2f}s) — leaving untouched"
            )
            return video_bytes

        # Cut ~1 frame before the splice keyframe to exclude the outro; if no
        # keyframe is found, fall back to TikTok's reported content boundary.
        splice = await _detect_outro_splice(tmp_path, content_duration)
        target = max(0.1, splice - 0.04) if splice is not None else content_duration
        out = await _cut_reencode(tmp_path, target)
        if not out:
            logging.warning("outro cut failed — leaving untouched")
            return video_bytes

        splice_str = f"{splice:.2f}s" if splice is not None else "n/a"
        logging.info(
            f"outro stripped: {file_duration:.2f}s -> {target:.2f}s "
            f"(content {content_duration:.2f}s, splice {splice_str}, "
            f"bytes {len(video_bytes)} -> {len(out)})"
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
