"""Generate HLS playlists from decrypted segments."""

from pathlib import Path
from typing import List, Optional


class HlsGenerator:
    """Generates HLS master and media playlists."""

    @staticmethod
    def generate_master_playlist(variants: List[dict]) -> str:
        """
        Generate HLS master playlist (#EXTM3U).
        
        Args:
            variants: List of variant stream info dicts with keys:
                - bandwidth: int
                - resolution: str (e.g., "1920x1080")
                - codecs: str
                - uri: str (relative path to media playlist)
                
        Returns:
            Master playlist content as string
        """
        lines = ["#EXTM3U", "#EXT-X-VERSION:7"]

        for variant in variants:
            bandwidth = variant.get("bandwidth", 0)
            resolution = variant.get("resolution")
            codecs = variant.get("codecs", "")
            uri = variant.get("uri", "")

            attrs = [f"BANDWIDTH={bandwidth}"]
            if resolution:
                attrs.append(f"RESOLUTION={resolution}")
            if codecs:
                attrs.append(f'CODECS="{codecs}"')

            attrs_str = ",".join(attrs)
            lines.append(f"#EXT-X-STREAM-INF:{attrs_str}")
            lines.append(uri)

        return "\n".join(lines) + "\n"

    @staticmethod
    def generate_media_playlist(
        segments: List[dict],
        target_duration: float,
        sequence: int = 0,
        is_live: bool = False,
        end_list: bool = True,
    ) -> str:
        """
        Generate HLS media playlist.
        
        Args:
            segments: List of segment info dicts with keys:
                - duration: float (in seconds)
                - uri: str (relative path to segment file)
            target_duration: Target duration in seconds
            sequence: Media sequence number
            is_live: Whether this is a live stream
            end_list: Whether to add EXT-X-ENDLIST tag
            
        Returns:
            Media playlist content as string
        """
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:7",
            f"#EXT-X-TARGETDURATION:{int(target_duration + 0.5)}",
            f"#EXT-X-MEDIA-SEQUENCE:{sequence}",
        ]

        if not is_live:
            lines.append("#EXT-X-PLAYLIST-TYPE:VOD")

        lines.append("#EXT-X-MAP:URI=\"init.mp4\"")

        for segment in segments:
            duration = segment.get("duration", 0.0)
            uri = segment.get("uri", "")
            lines.append(f"#EXTINF:{duration:.6f},")
            lines.append(uri)

        if end_list:
            lines.append("#EXT-X-ENDLIST")

        return "\n".join(lines) + "\n"

    @staticmethod
    def write_playlist(path: Path, content: str) -> None:
        """
        Write playlist content to file.
        
        Args:
            path: Output file path
            content: Playlist content
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
