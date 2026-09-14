from __future__ import annotations

import re


CHART_LEVELS = {"EZ", "HD", "IN", "AT", "Legacy", "SP"}

TRACK_RE = re.compile(r"^Assets/Tracks/([^/]+)/([^/]+)$")
