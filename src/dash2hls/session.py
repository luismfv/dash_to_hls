"""Stream session handling for DASH to HLS conversion."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from pathlib import Path
from typing import Deque, Optional

import aiohttp

from .dash_parser import DashManifest, DashParser, DashRepresentation, DashSegment
from .decryptor import DecryptionError, build_decryptor
from .downloader import SegmentDownloader
from .hls_writer import HLSWriter
from .models import StreamConfig, StreamInfo, StreamStatus

logger = logging.getLogger(__name__)


class StreamSession:
    """Manages the end-to-end lifecycle of a DASH to HLS stream."""

    def __init__(self, stream_id: str, config: StreamConfig, base_output_dir: Path) -> None:
        self.id = stream_id
        self.config = config
        self.output_dir = config.output_dir or (base_output_dir / stream_id)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.status: StreamStatus = StreamStatus.INITIALIZING
        self.error: Optional[str] = None
        self.is_live: bool = True

        self._decryptor = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._representation: Optional[DashRepresentation] = None
        self._hls_writer: Optional[HLSWriter] = None
        self._init_written = False

        self._history_limit = self.config.history_size or 128
        self._processed_numbers: Deque[int] = deque()
        self._processed_set: set[int] = set()
        self._last_sequence: Optional[int] = None

    async def start(self) -> None:
        """Start background processing."""
        if self._task and not self._task.done():
            return

        try:
            self._decryptor = build_decryptor(
                key=self.config.key,
                kid=self.config.kid,
                key_map=self.config.key_map,
                mp4decrypt_path=self.config.mp4decrypt_path,
                disable=not (self.config.key or self.config.key_map),
            )
        except Exception as exc:  # pragma: no cover - configuration stage
            self.status = StreamStatus.ERROR
            self.error = f"Failed to initialise decryptor: {exc}"
            logger.exception("Decryptor initialisation failed for stream %s", self.id)
            raise

        self._stop_event.clear()
        self.status = StreamStatus.STARTING
        self._task = asyncio.create_task(self._run_loop(), name=f"dash2hls-{self.id}")

    async def stop(self) -> None:
        """Stop background processing."""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status = StreamStatus.STOPPED

    def info(self) -> StreamInfo:
        """Return current information for this session."""
        resolution = None
        if self._representation and self._representation.width and self._representation.height:
            resolution = (self._representation.width, self._representation.height)

        return StreamInfo(
            stream_id=self.id,
            mpd_url=self.config.mpd_url,
            status=self.status,
            hls_url=f"/hls/{self.id}/master.m3u8",
            output_dir=self.output_dir,
            is_live=self.is_live,
            representation_id=self._representation.id if self._representation else None,
            bandwidth=self._representation.bandwidth if self._representation else None,
            codecs=self._representation.codecs if self._representation else None,
            resolution=resolution,
            error=self.error,
            label=self.config.label,
            last_sequence=self._last_sequence,
        )

    async def _run_loop(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
        headers = self.config.headers or {}

        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            downloader = SegmentDownloader(session)

            while not self._stop_event.is_set():
                try:
                    mpd_text = await downloader.download_text(self.config.mpd_url)
                except Exception as exc:
                    self._record_error(f"Failed to download MPD: {exc}")
                    await self._sleep(self.config.poll_interval)
                    continue

                try:
                    manifest = DashParser.parse(mpd_text, self.config.mpd_url)
                except Exception as exc:
                    self._record_error(f"Failed to parse MPD: {exc}")
                    await self._sleep(self.config.poll_interval)
                    continue

                self.is_live = manifest.is_live

                if self._hls_writer is None:
                    self._hls_writer = HLSWriter(
                        self.output_dir,
                        is_live=self.is_live,
                        window_size=self.config.window_size,
                    )

                representation = self._select_representation(manifest)
                if representation is None:
                    self._record_error("No matching representation in manifest")
                    await self._sleep(self.config.poll_interval)
                    continue

                self._representation = representation

                try:
                    await self._ensure_initialisation(downloader, representation)
                    new_segments = self._collect_new_segments(representation.segments)
                    if new_segments:
                        await self._process_segments(downloader, representation, new_segments)
                        self.status = StreamStatus.RUNNING
                    else:
                        logger.debug("No new segments for stream %s", self.id)

                    if not manifest.is_live and representation.segments:
                        last_manifest_sequence = representation.segments[-1].number
                        if (
                            last_manifest_sequence is not None
                            and self._last_sequence is not None
                            and self._last_sequence >= last_manifest_sequence
                        ):
                            if self._hls_writer:
                                self._hls_writer.finalize()
                            self.status = StreamStatus.COMPLETED
                            logger.info("Stream %s completed", self.id)
                            return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._record_error(str(exc))

                poll = manifest.min_update_period or self.config.poll_interval
                await self._sleep(poll)

        if self.status not in (StreamStatus.ERROR, StreamStatus.COMPLETED):
            self.status = StreamStatus.STOPPED

    async def _ensure_initialisation(self, downloader: SegmentDownloader, representation: DashRepresentation) -> None:
        if self._init_written:
            return

        logger.info("Downloading init segment for stream %s", self.id)
        init_payload = await downloader.download(representation.init_url)
        decrypted = await self._decrypt_segment(init_payload, representation.default_kid)
        if not self._hls_writer:
            raise RuntimeError("HLS writer not initialised")
        self._hls_writer.write_init(decrypted)

        resolution = None
        if representation.width and representation.height:
            resolution = (representation.width, representation.height)

        self._hls_writer.write_master_playlist(
            bandwidth=representation.bandwidth,
            codecs=representation.codecs,
            resolution=resolution,
            audio=not representation.is_video,
        )

        self._init_written = True
        logger.info("Init segment written for stream %s", self.id)

    async def _process_segments(
        self,
        downloader: SegmentDownloader,
        representation: DashRepresentation,
        segments: list[DashSegment],
    ) -> None:
        for segment in segments:
            if self._stop_event.is_set():
                break

            payload = await downloader.download(segment.url)
            decrypted = await self._decrypt_segment(payload, representation.default_kid)

            if not self._hls_writer:
                raise RuntimeError("HLS writer not initialised")

            self._hls_writer.add_segment(segment.number, segment.duration, decrypted)
            self._mark_processed(segment.number)
            self._last_sequence = segment.number

            logger.debug("Processed segment %s for stream %s", segment.number, self.id)

    def _collect_new_segments(self, segments: list[DashSegment]) -> list[DashSegment]:
        fresh: list[DashSegment] = []
        for segment in segments:
            number = segment.number
            if number is None:
                continue
            if number in self._processed_set:
                continue
            if self._last_sequence is not None and number <= self._last_sequence:
                continue
            fresh.append(segment)
        return fresh

    def _mark_processed(self, number: int) -> None:
        if number in self._processed_set:
            return
        self._processed_set.add(number)
        self._processed_numbers.append(number)

        while len(self._processed_numbers) > self._history_limit:
            oldest = self._processed_numbers.popleft()
            self._processed_set.discard(oldest)

    def _select_representation(self, manifest: DashManifest) -> Optional[DashRepresentation]:
        if self.config.representation_id:
            for rep in manifest.representations:
                if rep.id == self.config.representation_id:
                    return rep
            return None

        video_reps = [rep for rep in manifest.representations if rep.is_video]
        if video_reps:
            return max(video_reps, key=lambda rep: rep.bandwidth)

        if manifest.representations:
            return max(manifest.representations, key=lambda rep: rep.bandwidth)

        return None

    async def _decrypt_segment(self, payload: bytes, kid: Optional[str]) -> bytes:
        if not self._decryptor:
            raise RuntimeError("Decryptor not initialised")
        try:
            return await self._decryptor.decrypt_segment(payload, kid=kid)
        except DecryptionError as exc:
            logger.error("Decryption failed for stream %s: %s", self.id, exc)
            raise

    def _record_error(self, message: str) -> None:
        self.error = message
        self.status = StreamStatus.ERROR
        logger.error("Stream %s error: %s", self.id, message)

    async def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
