"""Dataclasses and enums for dash2hls runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional


class StreamStatus(str, Enum):
    """Lifecycle status of a DASH -> HLS session."""

    INITIALIZING = "initializing"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class StreamConfig:
    """Configuration for a stream session."""

    mpd_url: str
    key: Optional[str] = None
    kid: Optional[str] = None
    key_map: Optional[Dict[str, str]] = None
    mp4decrypt_path: Optional[str] = None
    representation_id: Optional[str] = None
    label: Optional[str] = None
    poll_interval: float = 4.0
    window_size: int = 6
    history_size: int = 64
    output_dir: Optional[Path] = None
    headers: Dict[str, str] | None = None


@dataclass
class StreamInfo:
    """Information about a running or completed stream."""

    stream_id: str
    mpd_url: str
    status: StreamStatus
    hls_url: str
    output_dir: Path
    is_live: bool
    representation_id: Optional[str] = None
    bandwidth: Optional[int] = None
    codecs: Optional[str] = None
    resolution: Optional[tuple[int, int]] = None
    error: Optional[str] = None
    label: Optional[str] = None
    last_sequence: Optional[int] = None
    audio_representation_id: Optional[str] = None
    audio_bandwidth: Optional[int] = None
    audio_codecs: Optional[str] = None
