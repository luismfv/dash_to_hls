"""HTTP server for DASH to HLS conversion API."""

from __future__ import annotations

import logging
from pathlib import Path

from quart import Quart, abort, jsonify, request, send_from_directory

from .manager import StreamManager
from .models import StreamConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = Quart(__name__)
manager = StreamManager(base_output_dir=Path("output"))


@app.route("/")
async def index():
    """Root endpoint with API info."""
    return jsonify({
        "service": "dash2hls",
        "version": "0.1.0",
        "endpoints": {
            "streams": "/streams",
            "hls": "/hls/<stream_id>/<path:filename>",
        }
    })


@app.route("/streams", methods=["GET"])
async def list_streams():
    """List all active streams."""
    streams = await manager.list_streams()
    return jsonify({
        "streams": [
            {
                "stream_id": s.stream_id,
                "mpd_url": s.mpd_url,
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                "hls_url": s.hls_url,
                "is_live": s.is_live,
                "representation_id": s.representation_id,
                "bandwidth": s.bandwidth,
                "codecs": s.codecs,
                "resolution": s.resolution,
                "error": s.error,
                "label": s.label,
                "last_sequence": s.last_sequence,
            }
            for s in streams
        ]
    })


@app.route("/streams", methods=["POST"])
async def add_stream():
    """Add a new stream to convert."""
    data = await request.get_json()

    if not data or "mpd_url" not in data:
        return jsonify({"error": "mpd_url is required"}), 400

    key_map = data.get("key_map") or data.get("keys")
    headers = data.get("headers")
    output_dir_value = data.get("output_dir")

    config = StreamConfig(
        mpd_url=data["mpd_url"],
        key=data.get("key"),
        kid=data.get("kid"),
        key_map=key_map,
        mp4decrypt_path=data.get("mp4decrypt_path"),
        representation_id=data.get("representation_id"),
        label=data.get("label"),
        poll_interval=float(data.get("poll_interval", 4.0)),
        window_size=int(data.get("window_size", 6)),
        history_size=int(data.get("history_size", 128)),
        headers=headers if isinstance(headers, dict) else None,
        output_dir=Path(output_dir_value) if output_dir_value else None,
    )

    try:
        stream_id = await manager.add_stream(config)
        return jsonify({
            "stream_id": stream_id,
            "hls_url": f"/hls/{stream_id}/master.m3u8",
            "status": "starting",
        }), 201
    except Exception as exc:
        logging.exception("Failed to add stream")
        return jsonify({"error": str(exc)}), 500


@app.route("/streams/<stream_id>", methods=["GET"])
async def get_stream(stream_id: str):
    """Get information about a specific stream."""
    info = await manager.get_stream_info(stream_id)

    if not info:
        return jsonify({"error": "Stream not found"}), 404

    return jsonify({
        "stream_id": info.stream_id,
        "mpd_url": info.mpd_url,
        "status": info.status.value if hasattr(info.status, "value") else str(info.status),
        "hls_url": info.hls_url,
        "is_live": info.is_live,
        "representation_id": info.representation_id,
        "bandwidth": info.bandwidth,
        "codecs": info.codecs,
        "resolution": info.resolution,
        "error": info.error,
        "label": info.label,
        "last_sequence": info.last_sequence,
    })


@app.route("/streams/<stream_id>", methods=["DELETE"])
async def remove_stream(stream_id: str):
    """Remove a stream."""
    removed = await manager.remove_stream(stream_id)
    
    if not removed:
        return jsonify({"error": "Stream not found"}), 404
    
    return jsonify({"message": "Stream removed"}), 200


@app.route("/hls/<stream_id>/<path:filename>")
async def serve_hls(stream_id: str, filename: str):
    """Serve HLS files (playlists and segments)."""
    output_path = manager.get_output_path(stream_id)

    if not output_path or not output_path.exists():
        abort(404, "Stream not found")

    requested_path = (output_path / filename).resolve()
    output_root = output_path.resolve()

    try:
        requested_path.relative_to(output_root)
    except ValueError:
        abort(404, "File not found")

    if not requested_path.exists() or not requested_path.is_file():
        abort(404, "File not found")

    if requested_path.suffix == ".m3u8":
        mimetype = "application/vnd.apple.mpegurl"
    elif requested_path.suffix in {".ts", ".m4s", ".mp4"}:
        mimetype = "video/mp4"
    else:
        mimetype = "application/octet-stream"

    relative = requested_path.relative_to(output_root)

    return await send_from_directory(
        output_root,
        str(relative),
        mimetype=mimetype,
    )


if __name__ == "__main__":
    import sys
    
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    app.run(host="0.0.0.0", port=port)
