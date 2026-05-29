from __future__ import annotations

import re


CHART_LEVELS = {"EZ", "HD", "IN", "AT", "Legacy", "SP"}

IMAGE_FILES = {
    "high": "Illustration.jpg",
    "low": "IllustrationLowRes.jpg",
    "blur": "IllustrationBlur.jpg",
}

TRACK_RE = re.compile(r"^Assets/Tracks/([^/]+)/([^/]+)$")
