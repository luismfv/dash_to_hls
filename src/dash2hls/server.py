"""HTTP server for DASH to HLS conversion API."""

from __future__ import annotations

import logging
from pathlib import Path

from quart import Quart, abort, jsonify, request, send_from_directory, render_template_string

from .manager import StreamManager
from .models import StreamConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = Quart(__name__)
manager = StreamManager(base_output_dir=Path("output"))

WEB_UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>dash2hls Live Streams</title>
    <link
      href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css"
      rel="stylesheet"
      integrity="sha384-TmxaWN7rmddQDzznFnHqs1qZ2vVaodIZ6Tn6PvxI6Bfq5lHppZArYrusS4X+hQVX"
      crossorigin="anonymous"
    />
    <style>
      body {
        padding-top: 2rem;
        padding-bottom: 3rem;
      }
      .form-section {
        background: #f8f9fa;
        border-radius: 0.75rem;
        padding: 1.5rem;
        margin-bottom: 2rem;
      }
      .status-running {
        color: #198754;
        font-weight: 600;
      }
      .status-error {
        color: #dc3545;
        font-weight: 600;
      }
      .status-starting {
        color: #0d6efd;
        font-weight: 600;
      }
    </style>
  </head>
  <body>
    <div class="container">
      <div class="mb-4 text-center">
        <h1 class="display-6">dash2hls Live Stream Manager</h1>
        <p class="text-muted">Monitor and manage real-time decrypted DASH &rarr; HLS lives.</p>
      </div>

      <section class="form-section">
        <h2 class="h5 mb-3">Add a Live</h2>
        <form id="add-form" class="row g-3">
          <div class="col-12">
            <label for="mpd_url" class="form-label">MPD URL *</label>
            <input type="url" class="form-control" id="mpd_url" name="mpd_url" required />
          </div>
          <div class="col-md-4">
            <label for="label" class="form-label">Label</label>
            <input type="text" class="form-control" id="label" name="label" placeholder="Optional name" />
          </div>
          <div class="col-md-4">
            <label for="key" class="form-label">Decryption Key</label>
            <input type="text" class="form-control" id="key" name="key" placeholder="32/64 hex chars" />
          </div>
          <div class="col-md-4">
            <label for="kid" class="form-label">KID</label>
            <input type="text" class="form-control" id="kid" name="kid" placeholder="Key ID" />
          </div>
          <div class="col-md-4">
            <label for="mp4decrypt_path" class="form-label">mp4decrypt Path</label>
            <input type="text" class="form-control" id="mp4decrypt_path" name="mp4decrypt_path" placeholder="Optional override" />
          </div>
          <div class="col-md-4">
            <label for="poll_interval" class="form-label">Poll Interval (s)</label>
            <input type="number" step="0.1" class="form-control" id="poll_interval" name="poll_interval" value="4.0" />
          </div>
          <div class="col-md-4">
            <label for="window_size" class="form-label">Live Window Size</label>
            <input type="number" class="form-control" id="window_size" name="window_size" value="6" />
          </div>
          <div class="col-12 text-end">
            <button type="submit" class="btn btn-primary">Add Live</button>
          </div>
        </form>
        <div id="form-alert" class="alert mt-3 d-none" role="alert"></div>
      </section>

      <section>
        <div class="d-flex justify-content-between align-items-center mb-3">
          <h2 class="h5 mb-0">Current Lives</h2>
          <button id="refresh-button" class="btn btn-outline-secondary btn-sm">Refresh</button>
        </div>
        <div class="table-responsive">
          <table class="table table-striped align-middle">
            <thead>
              <tr>
                <th scope="col">Label</th>
                <th scope="col">Stream ID</th>
                <th scope="col">Status</th>
                <th scope="col">Video</th>
                <th scope="col">Audio</th>
                <th scope="col">Last Sequence</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody id="streams-body">
              <tr>
                <td colspan="7" class="text-center text-muted">Loading…</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <script>
      const streamsBody = document.getElementById('streams-body');
      const form = document.getElementById('add-form');
      const refreshButton = document.getElementById('refresh-button');
      const alertBox = document.getElementById('form-alert');

      function resetAlert() {
        alertBox.classList.add('d-none');
        alertBox.classList.remove('alert-success', 'alert-danger');
        alertBox.textContent = '';
      }

      function showAlert(message, type = 'success') {
        alertBox.classList.remove('d-none');
        alertBox.classList.toggle('alert-success', type === 'success');
        alertBox.classList.toggle('alert-danger', type !== 'success');
        alertBox.textContent = message;
      }

      function renderStatus(status) {
        const cls = status === 'running' ? 'status-running' : status === 'error' ? 'status-error' : 'status-starting';
        return `<span class="${cls}">${status}</span>`;
      }

      function renderBitrate(bps) {
        if (!bps || bps <= 0) return '<span class="text-muted">n/a</span>';
        const mbps = (bps / 1000000).toFixed(2);
        return `${mbps} Mbps`;
      }

      function renderResolution(resolution) {
        if (!resolution || resolution.length !== 2) return ''; 
        return `${resolution[0]}x${resolution[1]}`;
      }

      async function fetchStreams() {
        resetAlert();
        try {
          const response = await fetch('/streams');
          const payload = await response.json();
          const streams = payload.streams || [];

          if (!streams.length) {
            streamsBody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No lives yet. Add one above.</td></tr>';
            return;
          }

          const rows = streams.map((stream) => {
            const videoInfo = [];
            if (stream.bandwidth) {
              videoInfo.push(renderBitrate(stream.bandwidth));
            }
            if (stream.resolution) {
              videoInfo.push(renderResolution(stream.resolution));
            }
            if (stream.codecs) {
              videoInfo.push(`<small class="text-muted">${stream.codecs}</small>`);
            }
            if (stream.representation_id) {
              videoInfo.push(`<small class="text-muted">ID: ${stream.representation_id}</small>`);
            }

            const audioInfo = [];
            if (stream.audio_bandwidth) {
              audioInfo.push(renderBitrate(stream.audio_bandwidth));
            }
            if (stream.audio_codecs) {
              audioInfo.push(`<small class="text-muted">${stream.audio_codecs}</small>`);
            }
            if (stream.audio_representation_id) {
              audioInfo.push(`<small class="text-muted">ID: ${stream.audio_representation_id}</small>`);
            }

            return `
              <tr>
                <td>${stream.label || '<span class="text-muted">(unnamed)</span>'}</td>
                <td class="text-break">${stream.stream_id}</td>
                <td>${renderStatus(stream.status)}</td>
                <td>${videoInfo.join('<br />') || '<span class="text-muted">n/a</span>'}</td>
                <td>${audioInfo.join('<br />') || '<span class="text-muted">n/a</span>'}</td>
                <td>${stream.last_sequence ?? '<span class="text-muted">—</span>'}</td>
                <td>
                  <div class="btn-group btn-group-sm" role="group">
                    <a class="btn btn-outline-primary" href="${stream.hls_url}" target="_blank">HLS</a>
                    <button class="btn btn-outline-danger" data-remove="${stream.stream_id}">Remove</button>
                  </div>
                </td>
              </tr>
            `;
          });

          streamsBody.innerHTML = rows.join('');
        } catch (error) {
          console.error(error);
          streamsBody.innerHTML = '<tr><td colspan="7" class="text-center text-danger">Failed to load streams</td></tr>';
        }
      }

      streamsBody.addEventListener('click', async (event) => {
        const button = event.target.closest('button[data-remove]');
        if (!button) return;

        const streamId = button.getAttribute('data-remove');
        if (!streamId) return;

        if (!confirm('Remove this live?')) {
          return;
        }

        try {
          const response = await fetch(`/streams/${streamId}`, { method: 'DELETE' });
          if (!response.ok) {
            const payload = await response.json();
            throw new Error(payload.error || 'Failed to remove stream');
          }
          showAlert('Live removed successfully.', 'success');
          fetchStreams();
        } catch (error) {
          showAlert(error.message, 'danger');
        }
      });

      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        resetAlert();

        const formData = new FormData(form);
        const payload = {};

        for (const [key, value] of formData.entries()) {
          if (!value) continue;
          if (key === 'poll_interval' || key === 'window_size') {
            const parsed = parseFloat(value);
            if (!Number.isNaN(parsed)) {
              payload[key] = parsed;
            }
          } else {
            payload[key] = value.trim();
          }
        }

        try {
          const response = await fetch('/streams', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });

          if (!response.ok) {
            const errorPayload = await response.json();
            throw new Error(errorPayload.error || 'Failed to add live');
          }

          form.reset();
          showAlert('Live added successfully. It will appear below momentarily.', 'success');
          fetchStreams();
        } catch (error) {
          showAlert(error.message, 'danger');
        }
      });

      refreshButton.addEventListener('click', fetchStreams);

      fetchStreams();
      setInterval(fetchStreams, 5000);
    </script>
  </body>
</html>
"""


@app.route("/")
async def index():
    """Root endpoint with web UI."""
    return await render_template_string(WEB_UI_TEMPLATE)


@app.route("/api")
async def api_info():
    """API endpoint with API info."""
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
                "audio_representation_id": getattr(s, "audio_representation_id", None),
                "audio_bandwidth": getattr(s, "audio_bandwidth", None),
                "audio_codecs": getattr(s, "audio_codecs", None),
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
        "audio_representation_id": getattr(info, "audio_representation_id", None),
        "audio_bandwidth": getattr(info, "audio_bandwidth", None),
        "audio_codecs": getattr(info, "audio_codecs", None),
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
