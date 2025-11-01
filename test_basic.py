#!/usr/bin/env python3
"""Basic smoke test for dash2hls package imports."""

import sys


def test_imports():
    """Test that all modules can be imported."""
    try:
        from dash2hls import StreamManager, StreamConfig, StreamInfo
        from dash2hls.dash_parser import DashParser
        from dash2hls.downloader import SegmentDownloader
        from dash2hls.decryptor import build_decryptor, PlaintextDecryptor
        from dash2hls.hls_generator import HlsGenerator
        from dash2hls.hls_writer import HLSWriter
        from dash2hls.models import StreamStatus
        from dash2hls.session import StreamSession
        print("✓ All imports successful")
        return True
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False


def test_basic_creation():
    """Test that basic objects can be created."""
    try:
        from dash2hls import StreamManager
        from pathlib import Path
        
        manager = StreamManager(base_output_dir=Path("/tmp/test_output"))
        print("✓ StreamManager created successfully")
        return True
    except Exception as e:
        print(f"✗ Creation failed: {e}")
        return False


if __name__ == "__main__":
    success = test_imports() and test_basic_creation()
    sys.exit(0 if success else 1)
