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
from .hls_writer import HLSWriter, MultiVariantHLSWriter
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
        self._video_representation: Optional[DashRepresentation] = None
        self._audio_representation: Optional[DashRepresentation] = None
        self._hls_writer: Optional[MultiVariantHLSWriter] = None

        self._history_limit = self.config.history_size or 128
        self._processed_numbers: dict[str, Deque[int]] = {}
        self._processed_set: dict[str, set[int]] = {}
        self._last_sequences: dict[str, Optional[int]] = {}

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
        if self._video_representation and self._video_representation.width and self._video_representation.height:
            resolution = (self._video_representation.width, self._video_representation.height)

        video_bandwidth = self._video_representation.bandwidth if self._video_representation else None
        audio_bandwidth = self._audio_representation.bandwidth if self._audio_representation else None

        video_codecs = self._video_representation.codecs if self._video_representation else None
        audio_codecs = self._audio_representation.codecs if self._audio_representation else None
        codecs = video_codecs or audio_codecs

        last_sequence = self._last_sequences.get("video")
        if last_sequence is None:
            last_sequence = self._last_sequences.get("audio")

        return StreamInfo(
            stream_id=self.id,
            mpd_url=self.config.mpd_url,
            status=self.status,
            hls_url=f"/hls/{self.id}/master.m3u8",
            output_dir=self.output_dir,
            is_live=self.is_live,
            representation_id=self._video_representation.id if self._video_representation else None,
            bandwidth=video_bandwidth,
            codecs=codecs,
            resolution=resolution,
            error=self.error,
            label=self.config.label,
            last_sequence=last_sequence,
            audio_representation_id=self._audio_representation.id if self._audio_representation else None,
            audio_bandwidth=audio_bandwidth,
            audio_codecs=audio_codecs,
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
                    self._hls_writer = MultiVariantHLSWriter(
                        self.output_dir,
                        is_live=self.is_live,
                        window_size=self.config.window_size,
                    )

                video_rep, audio_rep = self._select_representations(manifest)
                if video_rep is None and audio_rep is None:
                    self._record_error("No matching video or audio representation in manifest")
                    await self._sleep(self.config.poll_interval)
                    continue

                self._video_representation = video_rep
                self._audio_representation = audio_rep

                try:
                    await self._ensure_initialisation(downloader, video_rep, audio_rep)
                    
                    video_new_segments = []
                    audio_new_segments = []
                    
                    if video_rep:
                        video_new_segments = self._collect_new_segments(video_rep.segments, track="video")
                    if audio_rep:
                        audio_new_segments = self._collect_new_segments(audio_rep.segments, track="audio")
                    
                    if video_new_segments or audio_new_segments:
                        await self._process_multivariant_segments(
                            downloader, video_rep, audio_rep, video_new_segments, audio_new_segments
                        )
                        self.status = StreamStatus.RUNNING
                    else:
                        logger.debug("No new segments for stream %s", self.id)

                    if not manifest.is_live:
                        video_complete = True
                        audio_complete = True

                        video_last_seq = self._last_sequences.get("video")
                        audio_last_seq = self._last_sequences.get("audio")

                        if video_rep and video_rep.segments:
                            last_video_sequence = video_rep.segments[-1].number
                            if last_video_sequence is not None:
                                video_complete = (
                                    video_last_seq is not None and video_last_seq >= last_video_sequence
                                )

                        if audio_rep and audio_rep.segments:
                            last_audio_sequence = audio_rep.segments[-1].number
                            if last_audio_sequence is not None:
                                audio_complete = (
                                    audio_last_seq is not None and audio_last_seq >= last_audio_sequence
                                )

                        if video_complete and audio_complete:
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

    async def _ensure_initialisation(
        self,
        downloader: SegmentDownloader,
        video_representation: Optional[DashRepresentation],
        audio_representation: Optional[DashRepresentation],
    ) -> None:
        if not self._hls_writer:
            raise RuntimeError("HLS writer not initialised")

        if video_representation:
            resolution = None
            if video_representation.width and video_representation.height:
                resolution = (video_representation.width, video_representation.height)

            video_state = self._hls_writer.ensure_variant(
                "video",
                track_type="video",
                bandwidth=video_representation.bandwidth,
                codecs=video_representation.codecs,
                resolution=resolution,
            )

            if not video_state.init_written:
                logger.info("Downloading video init segment for stream %s", self.id)
                init_payload = await downloader.download(video_representation.init_url)
                decrypted = await self._decrypt_segment(init_payload, video_representation.default_kid)
                self._hls_writer.write_init("video", decrypted)
                logger.info("Video init segment written for stream %s", self.id)

        if audio_representation:
            audio_state = self._hls_writer.ensure_variant(
                "audio",
                track_type="audio",
                bandwidth=audio_representation.bandwidth,
                codecs=audio_representation.codecs,
            )

            if not audio_state.init_written:
                logger.info("Downloading audio init segment for stream %s", self.id)
                init_payload = await downloader.download(audio_representation.init_url)
                decrypted = await self._decrypt_segment(init_payload, audio_representation.default_kid)
                self._hls_writer.write_init("audio", decrypted)
                logger.info("Audio init segment written for stream %s", self.id)

    async def _process_multivariant_segments(
        self,
        downloader: SegmentDownloader,
        video_representation: Optional[DashRepresentation],
        audio_representation: Optional[DashRepresentation],
        video_segments: list[DashSegment],
        audio_segments: list[DashSegment],
    ) -> None:
        if video_representation and video_segments:
            await self._process_track_segments(
                track="video",
                downloader=downloader,
                representation=video_representation,
                segments=video_segments,
            )

        if audio_representation and audio_segments:
            await self._process_track_segments(
                track="audio",
                downloader=downloader,
                representation=audio_representation,
                segments=audio_segments,
            )

    async def _process_track_segments(
        self,
        track: str,
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

            self._hls_writer.add_segment(track, segment.number, segment.duration, decrypted)
            self._mark_processed(track, segment.number)
            self._last_sequences[track] = segment.number

            logger.debug("Processed %s segment %s for stream %s", track, segment.number, self.id)

    def _ensure_track_state(self, track: str) -> tuple[Deque[int], set[int]]:
        if track not in self._processed_numbers:
            self._processed_numbers[track] = deque()
            self._processed_set[track] = set()
            self._last_sequences[track] = None
        return self._processed_numbers[track], self._processed_set[track]

    def _collect_new_segments(self, segments: list[DashSegment], *, track: str) -> list[DashSegment]:
        numbers, processed_set = self._ensure_track_state(track)
        last_sequence = self._last_sequences.get(track)

        fresh: list[DashSegment] = []
        for segment in segments:
            number = segment.number
            if number is None:
                continue
            if number in processed_set:
                continue
            if last_sequence is not None and number <= last_sequence:
                continue
            fresh.append(segment)
        return fresh

    def _mark_processed(self, track: str, number: int) -> None:
        numbers, processed_set = self._ensure_track_state(track)
        if number in processed_set:
            return
        processed_set.add(number)
        numbers.append(number)

        while len(numbers) > self._history_limit:
            oldest = numbers.popleft()
            processed_set.discard(oldest)

    def _select_representations(
        self, manifest: DashManifest
    ) -> tuple[Optional[DashRepresentation], Optional[DashRepresentation]]:
        video_representation: Optional[DashRepresentation] = None
        audio_representation: Optional[DashRepresentation] = None

        if self.config.representation_id:
            for rep in manifest.representations:
                if rep.id == self.config.representation_id:
                    video_representation = rep
                    break
        else:
            video_reps = [rep for rep in manifest.representations if rep.is_video]
            if video_reps:
                video_representation = max(video_reps, key=lambda rep: rep.bandwidth or 0)

        audio_reps = [rep for rep in manifest.representations if rep.is_audio]
        if audio_reps:
            audio_representation = max(audio_reps, key=lambda rep: rep.bandwidth or 0)

        return video_representation, audio_representation

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
