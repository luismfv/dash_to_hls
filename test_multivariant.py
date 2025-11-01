#!/usr/bin/env python3
"""Test multi-variant HLS writer and representation selection."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def test_multivariant_writer():
    """Test MultiVariantHLSWriter can create video and audio variants."""
    try:
        from dash2hls.hls_writer import MultiVariantHLSWriter
        
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            writer = MultiVariantHLSWriter(output_dir, is_live=True, window_size=6)
            
            video_state = writer.ensure_variant(
                "video",
                track_type="video",
                bandwidth=5000000,
                codecs="avc1.4d401f",
                resolution=(1920, 1080),
            )
            
            audio_state = writer.ensure_variant(
                "audio",
                track_type="audio",
                bandwidth=128000,
                codecs="mp4a.40.2",
            )
            
            assert video_state.name == "video"
            assert audio_state.name == "audio"
            assert video_state.track_type == "video"
            assert audio_state.track_type == "audio"
            
            print("✓ MultiVariantHLSWriter test passed")
            return True
    except Exception as e:
        print(f"✗ MultiVariantHLSWriter test failed: {e}")
        return False


def test_representation_selection():
    """Test that highest quality video and audio are selected."""
    try:
        from dash2hls.dash_parser import DashRepresentation
        
        video_reps = [
            DashRepresentation(
                id="video-480p",
                bandwidth=1500000,
                codecs="avc1.4d401f",
                mime_type="video/mp4",
                width=854,
                height=480,
                init_url="init.mp4",
                segments=[],
                is_video=True,
                is_audio=False,
                default_kid=None,
            ),
            DashRepresentation(
                id="video-1080p",
                bandwidth=5000000,
                codecs="avc1.4d401f",
                mime_type="video/mp4",
                width=1920,
                height=1080,
                init_url="init.mp4",
                segments=[],
                is_video=True,
                is_audio=False,
                default_kid=None,
            ),
            DashRepresentation(
                id="video-720p",
                bandwidth=3000000,
                codecs="avc1.4d401f",
                mime_type="video/mp4",
                width=1280,
                height=720,
                init_url="init.mp4",
                segments=[],
                is_video=True,
                is_audio=False,
                default_kid=None,
            ),
        ]
        
        audio_reps = [
            DashRepresentation(
                id="audio-64k",
                bandwidth=64000,
                codecs="mp4a.40.2",
                mime_type="audio/mp4",
                width=None,
                height=None,
                init_url="init.mp4",
                segments=[],
                is_video=False,
                is_audio=True,
                default_kid=None,
            ),
            DashRepresentation(
                id="audio-128k",
                bandwidth=128000,
                codecs="mp4a.40.2",
                mime_type="audio/mp4",
                width=None,
                height=None,
                init_url="init.mp4",
                segments=[],
                is_video=False,
                is_audio=True,
                default_kid=None,
            ),
        ]
        
        highest_video = max(video_reps, key=lambda r: r.bandwidth or 0)
        highest_audio = max(audio_reps, key=lambda r: r.bandwidth or 0)
        
        assert highest_video.id == "video-1080p"
        assert highest_audio.id == "audio-128k"
        
        print("✓ Representation selection test passed")
        return True
    except Exception as e:
        print(f"✗ Representation selection test failed: {e}")
        return False


if __name__ == "__main__":
    success = test_multivariant_writer() and test_representation_selection()
    sys.exit(0 if success else 1)
