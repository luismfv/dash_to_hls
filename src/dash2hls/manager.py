"""Manager for multiple concurrent DASH to HLS streams."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from .models import StreamConfig, StreamInfo
from .session import StreamSession

logger = logging.getLogger(__name__)


class StreamManager:
    """Manages multiple DASH to HLS conversion streams."""

    def __init__(self, base_output_dir: Path = Path("output")) -> None:
        """
        Initialize the stream manager.

        Args:
            base_output_dir: Base directory for output files
        """
        self.base_output_dir = base_output_dir
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: Dict[str, StreamSession] = {}
        self._lock = asyncio.Lock()

    async def add_stream(self, config: StreamConfig) -> str:
        """
        Add a new DASH stream to convert.

        Args:
            config: Stream configuration

        Returns:
            Stream ID
        """
        stream_id = str(uuid4())

        async with self._lock:
            session = StreamSession(stream_id, config, self.base_output_dir)
            self._sessions[stream_id] = session
            await session.start()

        logger.info("Added stream %s from %s", stream_id, config.mpd_url)
        return stream_id

    async def remove_stream(self, stream_id: str) -> bool:
        """
        Remove and stop a stream.

        Args:
            stream_id: Stream ID to remove

        Returns:
            True if removed, False if not found
        """
        async with self._lock:
            session = self._sessions.pop(stream_id, None)
            if session:
                await session.stop()
                logger.info("Removed stream %s", stream_id)
                return True
            return False

    async def get_stream_info(self, stream_id: str) -> Optional[StreamInfo]:
        """
        Get information about a stream.

        Args:
            stream_id: Stream ID

        Returns:
            StreamInfo or None if not found
        """
        session = self._sessions.get(stream_id)
        return session.info() if session else None

    async def list_streams(self) -> List[StreamInfo]:
        """
        List all active streams.

        Returns:
            List of StreamInfo objects
        """
        result = []
        for stream_id in list(self._sessions.keys()):
            info = await self.get_stream_info(stream_id)
            if info:
                result.append(info)
        return result

    def get_output_path(self, stream_id: str) -> Optional[Path]:
        """Get the output directory for a stream."""
        session = self._sessions.get(stream_id)
        return session.output_dir if session else None
