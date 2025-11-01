"""Decryption helpers for DASH segments."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import Dict, Optional, Protocol


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

        command = [
            self.executable,
            "--key",
            f"{kid}:{key}",
            "-",
            "-",
        ]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(input=data)

        if process.returncode != 0:
            raise DecryptionError(
                f"mp4decrypt failed (exit code {process.returncode}).\n"
                f"STDOUT: {stdout.decode(errors='ignore')}\n"
                f"STDERR: {stderr.decode(errors='ignore')}"
            )

        if not stdout:
            stderr_text = stderr.decode(errors="ignore")
            raise DecryptionError(
                "mp4decrypt produced no output" + (f". STDERR: {stderr_text}" if stderr_text else "")
            )

        return stdout


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
