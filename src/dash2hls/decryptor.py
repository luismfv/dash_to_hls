"""Decryption helpers for DASH segments."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Dict, Optional, Protocol

logger = logging.getLogger(__name__)


class DecryptionError(RuntimeError):
    """Raised when a segment cannot be decrypted."""


class Decryptor(Protocol):
    """Interface for decrypting DASH segments."""

    async def decrypt_segment(self, data: bytes, *, kid: Optional[str] = None) -> bytes:
        """Decrypt a segment payload."""


@dataclass
class PlaintextDecryptor:
    """Pass-through decryptor for unencrypted content."""

    async def decrypt_segment(self, data: bytes, *, kid: Optional[str] = None) -> bytes:
        return data


class Mp4DecryptBinary(Decryptor):
    """Decrypt segments by invoking the external `mp4decrypt` binary."""

    def __init__(self, key_map: Dict[str, str], executable: str = "mp4decrypt") -> None:
        if not key_map:
            raise ValueError("key_map must contain at least one entry")

        normalized = {self._normalize_kid(k): self._normalize_key(v) for k, v in key_map.items()}
        self.key_map = normalized
        self.executable = executable

        if shutil.which(self.executable) is None:
            raise FileNotFoundError(
                f"Could not find '{self.executable}' in PATH. Install Bento4 or provide the full path."
            )

    @staticmethod
    def _normalize_kid(kid: str) -> str:
        return kid.replace("-", "").lower()

    @staticmethod
    def _normalize_key(key: str) -> str:
        key = key.strip().lower()
        if key.startswith("0x"):
            key = key[2:]
        if len(key) not in (32, 64):  # 16 or 32 bytes
            raise ValueError("Keys must be 16 or 32 bytes expressed in hexadecimal characters")
        return key

    async def decrypt_segment(self, data: bytes, *, kid: Optional[str] = None) -> bytes:
        # Validate input data
        if not data:
            raise DecryptionError("No data provided for decryption")
        
        if len(data) < 8:  # MP4 files should have at least 8 bytes for the header
            raise DecryptionError(f"Data too small for MP4 file: {len(data)} bytes")

        if kid:
            kid = self._normalize_kid(kid)
            if kid not in self.key_map:
                # If the requested kid is missing but a single key exists, fall back to that key
                if len(self.key_map) == 1:
                    kid = next(iter(self.key_map))
                else:
                    raise DecryptionError(f"No key registered for KID {kid}")
        else:
            kid = next(iter(self.key_map))

        key = self.key_map[kid]

        # Try stdin/stdout first, fallback to temp files if that fails
        try:
            return await self._decrypt_via_stdin(data, kid, key)
        except DecryptionError as exc:
            error_str = str(exc)
            # Check for specific stdin-related errors
            if "cannot open input file" in error_str or "cannot stat '-'" in error_str:
                # Fallback to temp file method if stdin fails
                logger.warning(f"Stdin method failed, falling back to temp file method: {exc}")
                return await self._decrypt_via_tempfile(data, kid, key)
            raise

    async def _decrypt_via_stdin(self, data: bytes, kid: str, key: str) -> bytes:
        """Decrypt using stdin/stdout pipes."""
        command = [
            self.executable,
            "--key",
            f"{kid}:{key}",
            "-",
            "-",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate(input=data)
        except Exception as exc:
            raise DecryptionError(f"Failed to execute mp4decrypt: {exc}")

        if process.returncode != 0:
            stderr_text = stderr.decode(errors='ignore').strip()
            stdout_text = stdout.decode(errors='ignore').strip()
            
            raise DecryptionError(
                f"mp4decrypt failed (exit code {process.returncode}).\n"
                f"Input data size: {len(data)} bytes\n"
                f"First 100 bytes (hex): {data[:100].hex()}\n"
                f"STDOUT: {stdout_text}\n"
                f"STDERR: {stderr_text}"
            )

        if not stdout:
            stderr_text = stderr.decode(errors="ignore").strip()
            raise DecryptionError(
                f"mp4decrypt produced no output. Input data size: {len(data)} bytes" + 
                (f". STDERR: {stderr_text}" if stderr_text else "")
            )

        return stdout

    async def _decrypt_via_tempfile(self, data: bytes, kid: str, key: str) -> bytes:
        """Decrypt using temporary files as fallback."""
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as input_file:
            input_file.write(data)
            input_path = input_file.name
        
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as output_file:
            output_path = output_file.name

        try:
            command = [
                self.executable,
                "--key",
                f"{kid}:{key}",
                input_path,
                output_path,
            ]

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                stderr_text = stderr.decode(errors='ignore').strip()
                stdout_text = stdout.decode(errors='ignore').strip()
                
                raise DecryptionError(
                    f"mp4decrypt failed with temp file method (exit code {process.returncode}).\n"
                    f"Input file: {input_path}\n"
                    f"Output file: {output_path}\n"
                    f"Input data size: {len(data)} bytes\n"
                    f"STDOUT: {stdout_text}\n"
                    f"STDERR: {stderr_text}"
                )

            # Read the decrypted data from the output file
            try:
                with open(output_path, 'rb') as f:
                    decrypted_data = f.read()
                
                if not decrypted_data:
                    raise DecryptionError("mp4decrypt produced empty output file")
                
                return decrypted_data
            except Exception as exc:
                raise DecryptionError(f"Failed to read decrypted output file: {exc}")

        finally:
            # Clean up temporary files
            try:
                os.unlink(input_path)
                os.unlink(output_path)
            except Exception:
                pass


def build_decryptor(
    *,
    key: Optional[str] = None,
    kid: Optional[str] = None,
    key_map: Optional[Dict[str, str]] = None,
    mp4decrypt_path: Optional[str] = None,
    disable: bool = False,
) -> Decryptor:
    """Factory for decryptor instances."""
    if disable or (not key and not key_map):
        return PlaintextDecryptor()

    mp4decrypt_path = mp4decrypt_path or "mp4decrypt"

    if key_map is None:
        if not key:
            raise ValueError("Either key or key_map must be supplied")
        if not kid:
            raise ValueError("A key_id (KID) must be provided alongside the key")
        key_map = {kid: key}

    return Mp4DecryptBinary(key_map=key_map, executable=mp4decrypt_path)
