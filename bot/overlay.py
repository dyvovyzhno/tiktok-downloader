# bot/overlay.py

import asyncio
import logging
import shutil


def _escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
    )


async def add_author_overlay(video_bytes: bytes, author: str) -> bytes:
    if not author or not shutil.which("ffmpeg"):
        return video_bytes

    text = _escape_drawtext(f"@{author}")
    vf = (
        f"drawtext=text='{text}'"
        ":fontcolor=white@0.85"
        ":fontsize=h/27"
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
