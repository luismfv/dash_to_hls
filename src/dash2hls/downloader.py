"""Async downloader for DASH segments."""

import aiohttp
from typing import Optional
from pathlib import Path


class SegmentDownloader:
    """Asynchronous segment downloader."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        """
        Initialize downloader.
        
        Args:
            session: Optional aiohttp session. If None, a new one will be created.
        """
        self.session = session
        self._own_session = session is None

    async def __aenter__(self):
        if self._own_session:
            self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._own_session and self.session:
            await self.session.close()

    async def download(self, url: str, headers: Optional[dict] = None) -> bytes:
        """
        Download a URL and return its content.
        
        Args:
            url: URL to download
            headers: Optional HTTP headers
            
        Returns:
            Downloaded content as bytes
        """
        if self.session is None:
            raise RuntimeError("Session not initialized. Use 'async with' context manager.")

        async with self.session.get(url, headers=headers) as response:
            response.raise_for_status()
            return await response.read()

    async def download_to_file(self, url: str, output_path: Path, headers: Optional[dict] = None) -> None:
        """
        Download a URL and save to file.
        
        Args:
            url: URL to download
            output_path: Path to save the file
            headers: Optional HTTP headers
        """
        content = await self.download(url, headers)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)

    async def download_text(self, url: str, headers: Optional[dict] = None) -> str:
        """
        Download a URL and return its content as text.
        
        Args:
            url: URL to download
            headers: Optional HTTP headers
            
        Returns:
            Downloaded content as string
        """
        if self.session is None:
            raise RuntimeError("Session not initialized. Use 'async with' context manager.")

        async with self.session.get(url, headers=headers) as response:
            response.raise_for_status()
            return await response.text()
