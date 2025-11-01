"""Helpers for writing HLS playlists and segments."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Iterable, List

from .hls_generator import HlsGenerator


@dataclass
class HLSSegment:
    """Metadata about a written HLS segment."""

    sequence: int
    duration: float
    filename: str


class HLSWriter:
    """Keeps an HLS playlist in sync with decrypted DASH segments."""

    def __init__(
        self,
        output_dir: Path,
        *,
        is_live: bool,
        window_size: int = 6,
    ) -> None:
        self.output_dir = output_dir
        self.is_live = is_live
        self.window_size = window_size if is_live else 0
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._segments: Deque[HLSSegment] = deque()
        self._target_duration: float = 1.0
        self._finalized: bool = False

    @property
    def playlist_path(self) -> Path:
        return self.output_dir / "index.m3u8"

    @property
    def master_playlist_path(self) -> Path:
        return self.output_dir / "master.m3u8"

    @property
    def init_path(self) -> Path:
        return self.output_dir / "init.mp4"

    def write_init(self, payload: bytes) -> None:
        """Persist the initialization segment."""
        self.init_path.write_bytes(payload)

    def add_segment(self, sequence: int, duration: float, payload: bytes) -> Path:
        """Write a media segment and update the playlist."""
        filename = f"segment_{sequence}.m4s"
        path = self.output_dir / filename
        path.write_bytes(payload)

        self._segments.append(HLSSegment(sequence=sequence, duration=duration, filename=filename))
        self._target_duration = max(self._target_duration, duration)

        if self.window_size:
            while len(self._segments) > self.window_size:
                old = self._segments.popleft()
                old_path = self.output_dir / old.filename
                if old_path.exists():
                    old_path.unlink()

        self._write_playlist()
        return path

    def finalize(self) -> None:
        """Mark the playlist as complete (for VOD)."""
        self._finalized = True
        self._write_playlist()

    def _write_playlist(self) -> None:
        if not self._segments:
            return

        segments_for_playlist = [
            {"duration": seg.duration, "uri": seg.filename} for seg in self._segments
        ]
        media_sequence = self._segments[0].sequence

        playlist_content = HlsGenerator.generate_media_playlist(
            segments=segments_for_playlist,
            target_duration=self._target_duration,
            sequence=media_sequence,
            is_live=self.is_live,
            end_list=self._finalized and not self.is_live,
        )
        HlsGenerator.write_playlist(self.playlist_path, playlist_content)

    def write_master_playlist(
        self,
        *,
        bandwidth: int,
        codecs: str | None = None,
        resolution: tuple[int, int] | None = None,
        audio: bool = False,
    ) -> None:
        """Write or overwrite the master playlist for this stream."""
        variant = {
            "bandwidth": bandwidth,
            "uri": self.playlist_path.name,
        }
        if codecs:
            variant["codecs"] = codecs
        if resolution and not audio:
            variant["resolution"] = f"{resolution[0]}x{resolution[1]}"

        content = HlsGenerator.generate_master_playlist([variant])
        HlsGenerator.write_playlist(self.master_playlist_path, content)
