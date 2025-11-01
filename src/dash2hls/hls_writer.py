"""Helpers for writing HLS playlists and segments."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional

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
        audio_group: str | None = None,
    ) -> None:
        """Write or overwrite the master playlist for this stream."""
        variant = {
            "bandwidth": bandwidth,
            "uri": self.playlist_path.name,
        }
        if codecs:
            variant["codecs"] = codecs
        if resolution:
            variant["resolution"] = f"{resolution[0]}x{resolution[1]}"
        if audio_group:
            variant["audio_group"] = audio_group

        content = HlsGenerator.generate_master_playlist([variant])
        HlsGenerator.write_playlist(self.master_playlist_path, content)


@dataclass
class VariantState:
    """Internal metadata for a multi-variant track."""

    name: str
    track_type: str
    writer: HLSWriter
    bandwidth: int
    codecs: Optional[str]
    resolution: Optional[tuple[int, int]]
    init_written: bool = False

    def playlist_uri(self, base_output: Path) -> str:
        return self.writer.playlist_path.relative_to(base_output).as_posix()


class MultiVariantHLSWriter:
    """Manage multiple HLS playlists (e.g. video + audio) under one master."""

    def __init__(
        self,
        output_dir: Path,
        *,
        is_live: bool,
        window_size: int = 6,
    ) -> None:
        self.output_dir = output_dir
        self.is_live = is_live
        self.window_size = window_size
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._variants: Dict[str, VariantState] = {}
        self._finalized: bool = False

    @property
    def master_playlist_path(self) -> Path:
        return self.output_dir / "master.m3u8"

    def ensure_variant(
        self,
        name: str,
        *,
        track_type: str,
        bandwidth: int,
        codecs: Optional[str],
        resolution: Optional[tuple[int, int]] = None,
    ) -> VariantState:
        if name in self._variants:
            state = self._variants[name]
            state.bandwidth = bandwidth
            state.codecs = codecs
            state.resolution = resolution
            return state

        if track_type == "video" and name == "video":
            track_dir = self.output_dir
        else:
            track_dir = self.output_dir / name

        writer = HLSWriter(track_dir, is_live=self.is_live, window_size=self.window_size)
        state = VariantState(
            name=name,
            track_type=track_type,
            writer=writer,
            bandwidth=bandwidth,
            codecs=codecs,
            resolution=resolution,
        )
        self._variants[name] = state
        return state

    def write_init(self, name: str, payload: bytes) -> None:
        state = self._require_variant(name)
        state.writer.write_init(payload)
        state.init_written = True
        self._write_master_playlist()

    def add_segment(self, name: str, sequence: int, duration: float, payload: bytes) -> Path:
        state = self._require_variant(name)
        written_path = state.writer.add_segment(sequence, duration, payload)
        return written_path

    def finalize(self) -> None:
        for state in self._variants.values():
            state.writer.finalize()
        self._finalized = True
        self._write_master_playlist()

    def _require_variant(self, name: str) -> VariantState:
        if name not in self._variants:
            raise KeyError(f"Variant {name} not configured")
        return self._variants[name]

    def _write_master_playlist(self) -> None:
        active_variants = [state for state in self._variants.values() if state.init_written]
        if not active_variants:
            return

        audio_variants = [state for state in active_variants if state.track_type == "audio"]
        video_variants = [state for state in active_variants if state.track_type == "video"]

        media_entries = []
        if audio_variants:
            for audio_state in audio_variants:
                media_entries.append(
                    {
                        "type": "AUDIO",
                        "group_id": "audio",
                        "name": audio_state.name,
                        "uri": audio_state.playlist_uri(self.output_dir),
                        "default": True,
                        "autoselect": True,
                        "language": None,
                    }
                )

        variants_for_master = []
        if video_variants:
            audio_bandwidth_total = sum(state.bandwidth for state in audio_variants)
            audio_codecs = [state.codecs for state in audio_variants if state.codecs]

            for video_state in video_variants:
                total_bandwidth = video_state.bandwidth + audio_bandwidth_total
                codecs_parts = []
                if video_state.codecs:
                    codecs_parts.append(video_state.codecs)
                codecs_parts.extend([c for c in audio_codecs if c and c not in codecs_parts])
                codecs = ",".join(codecs_parts) if codecs_parts else None

                variant_entry = {
                    "bandwidth": total_bandwidth if total_bandwidth else video_state.bandwidth,
                    "uri": video_state.playlist_uri(self.output_dir),
                }
                if codecs:
                    variant_entry["codecs"] = codecs
                if video_state.resolution:
                    variant_entry["resolution"] = f"{video_state.resolution[0]}x{video_state.resolution[1]}"
                if audio_variants:
                    variant_entry["audio_group"] = "audio"
                variants_for_master.append(variant_entry)
        else:
            for audio_state in audio_variants:
                entry = {
                    "bandwidth": audio_state.bandwidth,
                    "uri": audio_state.playlist_uri(self.output_dir),
                }
                if audio_state.codecs:
                    entry["codecs"] = audio_state.codecs
                variants_for_master.append(entry)

        content = HlsGenerator.generate_master_playlist(variants_for_master, media_entries)
        HlsGenerator.write_playlist(self.master_playlist_path, content)
