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
<html lang="en" class="dark">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>dash2hls Live Streams</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
      tailwind.config = {
        darkMode: 'class',
        theme: {
          extend: {
            colors: {
              dark: {
                bg: '#0f172a',
                card: '#1e293b',
                hover: '#334155',
                border: '#334155',
              }
            }
          }
        }
      }
    </script>
    <style>
      @keyframes fadeIn {
        from { opacity: 0; transform: translateY(-10px); }
        to { opacity: 1; transform: translateY(0); }
      }
      .animate-fade-in {
        animation: fadeIn 0.3s ease-in-out;
      }
    </style>
  </head>
  <body class="bg-dark-bg text-gray-100 min-h-screen">
    <div class="container mx-auto px-4 sm:px-6 lg:px-8 py-8 max-w-7xl">
      <!-- Header -->
      <div class="mb-8 text-center">
        <h1 class="text-4xl font-bold mb-2 bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
          dash2hls
        </h1>
        <p class="text-gray-400 text-lg">Monitor and manage real-time DASH → HLS streams</p>
      </div>

      <!-- Add Stream Form -->
      <div class="bg-dark-card rounded-lg shadow-xl p-6 mb-8 border border-dark-border">
        <h2 class="text-2xl font-semibold mb-6 text-gray-100">Add New Stream</h2>
        <form id="add-form" class="space-y-4">
          <div class="grid grid-cols-1 gap-4">
            <div>
              <label for="mpd_url" class="block text-sm font-medium text-gray-300 mb-2">
                MPD URL <span class="text-red-400">*</span>
              </label>
              <input 
                type="url" 
                id="mpd_url" 
                name="mpd_url" 
                required 
                placeholder="https://example.com/manifest.mpd"
                class="w-full px-4 py-2.5 bg-dark-hover border border-dark-border rounded-lg text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              />
            </div>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div>
              <label for="label" class="block text-sm font-medium text-gray-300 mb-2">Label</label>
              <input 
                type="text" 
                id="label" 
                name="label" 
                placeholder="Optional name"
                class="w-full px-4 py-2.5 bg-dark-hover border border-dark-border rounded-lg text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              />
            </div>

            <div>
              <label for="decryption_key" class="block text-sm font-medium text-gray-300 mb-2">
                Decryption Key
                <span class="text-gray-500 text-xs ml-1">(KID:KEY)</span>
              </label>
              <input 
                type="text" 
                id="decryption_key" 
                name="decryption_key" 
                placeholder="kid:key"
                class="w-full px-4 py-2.5 bg-dark-hover border border-dark-border rounded-lg text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition font-mono text-sm"
              />
            </div>

            <div>
              <label for="mp4decrypt_path" class="block text-sm font-medium text-gray-300 mb-2">
                mp4decrypt Path
              </label>
              <input 
                type="text" 
                id="mp4decrypt_path" 
                name="mp4decrypt_path" 
                placeholder="Optional override"
                class="w-full px-4 py-2.5 bg-dark-hover border border-dark-border rounded-lg text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              />
            </div>

            <div>
              <label for="poll_interval" class="block text-sm font-medium text-gray-300 mb-2">
                Poll Interval (s)
              </label>
              <input 
                type="number" 
                step="0.1" 
                id="poll_interval" 
                name="poll_interval" 
                value="4.0"
                class="w-full px-4 py-2.5 bg-dark-hover border border-dark-border rounded-lg text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              />
            </div>

            <div>
              <label for="window_size" class="block text-sm font-medium text-gray-300 mb-2">
                Live Window Size
              </label>
              <input 
                type="number" 
                id="window_size" 
                name="window_size" 
                value="6"
                class="w-full px-4 py-2.5 bg-dark-hover border border-dark-border rounded-lg text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              />
            </div>
          </div>

          <div class="flex justify-end pt-2">
            <button 
              type="submit" 
              class="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition duration-150 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-dark-bg"
            >
              Add Stream
            </button>
          </div>
        </form>

        <div id="form-alert" class="hidden mt-4 p-4 rounded-lg animate-fade-in"></div>
      </div>

      <!-- Streams List -->
      <div class="bg-dark-card rounded-lg shadow-xl border border-dark-border overflow-hidden">
        <div class="p-6 border-b border-dark-border flex justify-between items-center">
          <h2 class="text-2xl font-semibold text-gray-100">Active Streams</h2>
          <button 
            id="refresh-button" 
            class="px-4 py-2 bg-dark-hover hover:bg-dark-border text-gray-300 rounded-lg transition duration-150 ease-in-out text-sm font-medium"
          >
            ↻ Refresh
          </button>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full">
            <thead class="bg-dark-hover">
              <tr class="text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
                <th class="px-6 py-3">Label</th>
                <th class="px-6 py-3">Stream ID</th>
                <th class="px-6 py-3">Status</th>
                <th class="px-6 py-3">Video</th>
                <th class="px-6 py-3">Audio</th>
                <th class="px-6 py-3">Last Seq</th>
                <th class="px-6 py-3">Actions</th>
              </tr>
            </thead>
            <tbody id="streams-body" class="divide-y divide-dark-border">
              <tr>
                <td colspan="7" class="px-6 py-8 text-center text-gray-500">
                  <div class="flex flex-col items-center">
                    <svg class="w-12 h-12 mb-4 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z"></path>
                    </svg>
                    <p>Loading streams...</p>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <script>
      const streamsBody = document.getElementById('streams-body');
      const form = document.getElementById('add-form');
      const refreshButton = document.getElementById('refresh-button');
      const alertBox = document.getElementById('form-alert');

      function resetAlert() {
        alertBox.classList.add('hidden');
        alertBox.className = 'hidden mt-4 p-4 rounded-lg animate-fade-in';
        alertBox.textContent = '';
      }

      function showAlert(message, type = 'success') {
        alertBox.classList.remove('hidden');
        if (type === 'success') {
          alertBox.className = 'mt-4 p-4 rounded-lg animate-fade-in bg-green-900/50 border border-green-700 text-green-100';
        } else {
          alertBox.className = 'mt-4 p-4 rounded-lg animate-fade-in bg-red-900/50 border border-red-700 text-red-100';
        }
        alertBox.textContent = message;
      }

      function renderStatus(status) {
        const colors = {
          'running': 'bg-green-900/50 text-green-400 border-green-700',
          'error': 'bg-red-900/50 text-red-400 border-red-700',
          'starting': 'bg-blue-900/50 text-blue-400 border-blue-700',
          'initializing': 'bg-yellow-900/50 text-yellow-400 border-yellow-700'
        };
        const colorClass = colors[status] || colors['initializing'];
        return `<span class="inline-block px-3 py-1 text-xs font-semibold rounded-full border ${colorClass}">${status}</span>`;
      }

      function renderBitrate(bps) {
        if (!bps || bps <= 0) return '<span class="text-gray-600">n/a</span>';
        const mbps = (bps / 1000000).toFixed(2);
        return `<span class="text-gray-300">${mbps} Mbps</span>`;
      }

      function renderResolution(resolution) {
        if (!resolution || resolution.length !== 2) return ''; 
        return `<span class="text-gray-300">${resolution[0]}×${resolution[1]}</span>`;
      }

      async function fetchStreams() {
        resetAlert();
        try {
          const response = await fetch('/streams');
          const payload = await response.json();
          const streams = payload.streams || [];

          if (!streams.length) {
            streamsBody.innerHTML = `
              <tr>
                <td colspan="7" class="px-6 py-8 text-center text-gray-500">
                  <div class="flex flex-col items-center">
                    <svg class="w-12 h-12 mb-4 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"></path>
                    </svg>
                    <p>No streams yet. Add one above to get started.</p>
                  </div>
                </td>
              </tr>
            `;
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
              videoInfo.push(`<span class="text-xs text-gray-500">${stream.codecs}</span>`);
            }
            if (stream.representation_id) {
              videoInfo.push(`<span class="text-xs text-gray-600">ID: ${stream.representation_id}</span>`);
            }

            const audioInfo = [];
            if (stream.audio_bandwidth) {
              audioInfo.push(renderBitrate(stream.audio_bandwidth));
            }
            if (stream.audio_codecs) {
              audioInfo.push(`<span class="text-xs text-gray-500">${stream.audio_codecs}</span>`);
            }
            if (stream.audio_representation_id) {
              audioInfo.push(`<span class="text-xs text-gray-600">ID: ${stream.audio_representation_id}</span>`);
            }

            return `
              <tr class="hover:bg-dark-hover transition">
                <td class="px-6 py-4 text-sm">${stream.label || '<span class="text-gray-600">(unnamed)</span>'}</td>
                <td class="px-6 py-4 text-sm font-mono text-gray-400 max-w-xs truncate">${stream.stream_id}</td>
                <td class="px-6 py-4 text-sm">${renderStatus(stream.status)}</td>
                <td class="px-6 py-4 text-sm">
                  <div class="flex flex-col space-y-1">
                    ${videoInfo.join('<br />') || '<span class="text-gray-600">n/a</span>'}
                  </div>
                </td>
                <td class="px-6 py-4 text-sm">
                  <div class="flex flex-col space-y-1">
                    ${audioInfo.join('<br />') || '<span class="text-gray-600">n/a</span>'}
                  </div>
                </td>
                <td class="px-6 py-4 text-sm text-gray-400">${stream.last_sequence ?? '—'}</td>
                <td class="px-6 py-4 text-sm">
                  <div class="flex space-x-2">
                    <a 
                      href="${stream.hls_url}" 
                      target="_blank" 
                      class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium rounded transition"
                    >
                      HLS
                    </a>
                    <button 
                      data-remove="${stream.stream_id}"
                      class="px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white text-xs font-medium rounded transition"
                    >
                      Remove
                    </button>
                  </div>
                </td>
              </tr>
            `;
          });

          streamsBody.innerHTML = rows.join('');
        } catch (error) {
          console.error(error);
          streamsBody.innerHTML = `
            <tr>
              <td colspan="7" class="px-6 py-8 text-center text-red-400">
                <div class="flex flex-col items-center">
                  <svg class="w-12 h-12 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                  </svg>
                  <p>Failed to load streams</p>
                </div>
              </td>
            </tr>
          `;
        }
      }

      streamsBody.addEventListener('click', async (event) => {
        const button = event.target.closest('button[data-remove]');
        if (!button) return;

        const streamId = button.getAttribute('data-remove');
        if (!streamId) return;

        if (!confirm('Are you sure you want to remove this stream?')) {
          return;
        }

        try {
          const response = await fetch(`/streams/${streamId}`, { method: 'DELETE' });
          if (!response.ok) {
            const payload = await response.json();
            throw new Error(payload.error || 'Failed to remove stream');
          }
          showAlert('Stream removed successfully.', 'success');
          fetchStreams();
        } catch (error) {
          showAlert(error.message, 'error');
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
          } else if (key === 'decryption_key') {
            const trimmed = value.trim();
            if (trimmed) {
              const separatorIndex = trimmed.indexOf(':');
              if (separatorIndex === -1) {
                showAlert('Decryption key must be provided as KID:KEY.', 'error');
                return;
              }
              const kidPart = trimmed.slice(0, separatorIndex).trim();
              const keyPart = trimmed.slice(separatorIndex + 1).trim();
              if (!kidPart || !keyPart) {
                showAlert('Both KID and KEY values are required when providing a decryption key.', 'error');
                return;
              }
              payload['kid'] = kidPart;
              payload['key'] = keyPart;
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
            throw new Error(errorPayload.error || 'Failed to add stream');
          }

          form.reset();
          showAlert('Stream added successfully! It will appear below momentarily.', 'success');
          fetchStreams();
        } catch (error) {
          showAlert(error.message, 'error');
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
