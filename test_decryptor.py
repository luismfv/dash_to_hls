#!/usr/bin/env python3
"""Test the mp4decrypt wrapper behaviour."""

import asyncio
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


FAKE_KID = "0123456789abcdef0123456789abcdef"
FAKE_KEY = "00112233445566778899aabbccddeeff"


def _create_fake_mp4decrypt(directory: Path) -> Path:
    """Create a fake mp4decrypt executable that copies input to output."""
    script_path = directory / "mp4decrypt"
    script_path.write_text(
        "#!/bin/sh\n"
        "# arguments: --key kid:key input output\n"
        "if [ \"$#\" -ne 4 ]; then\n"
        "  echo 'unexpected arguments' >&2\n"
        "  exit 1\n"
        "fi\n"
        "cat \"$3\" > \"$4\"\n"
    )
    os.chmod(script_path, 0o755)
    return script_path


async def _run_decryptor(fake_binary: Path) -> bytes:
    from dash2hls.decryptor import Mp4DecryptBinary

    decryptor = Mp4DecryptBinary(key_map={FAKE_KID: FAKE_KEY}, executable=str(fake_binary))
    payload = b"encrypted payload"
    return await decryptor.decrypt_segment(payload, kid=FAKE_KID)


def test_mp4decrypt_wrapper() -> bool:
    try:
        with TemporaryDirectory() as tmpdir:
            fake_binary = _create_fake_mp4decrypt(Path(tmpdir))
            result = asyncio.run(_run_decryptor(fake_binary))
            assert result == b"encrypted payload"
        print("\u2713 Mp4DecryptBinary file-based decryption test passed")
        return True
    except Exception as exc:
        print(f"\u2717 Mp4DecryptBinary test failed: {exc}")
        return False


if __name__ == "__main__":
    sys.exit(0 if test_mp4decrypt_wrapper() else 1)
