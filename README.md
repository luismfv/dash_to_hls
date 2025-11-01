# DASH to HLS Backend

A high-performance Python backend that converts MPEG-DASH streams to HLS format with real-time CENC decryption support. Built to handle multiple concurrent streams with an async-first architecture.

## Features

- 📺 **Parse MPD manifests** and track media segments in real time
- 🔐 **CENC decryption** using the mp4decrypt binary (Bento4) with provided keys
- 🎬 **Generates fMP4-based HLS playlists** from decrypted segments
- ⚡ **Concurrently manage multiple DASH streams** with async I/O
- 🌐 **HTTP REST API** powered by Quart and served by Hypercorn
- 💻 **CLI tooling** for easy stream management
- 🔄 **Support for both VOD and live streams**
- 🎯 **Representation selection** by bandwidth, resolution, or ID

## Prerequisites

To use decryption features, you need to install [Bento4](https://www.bento4.com/):

```bash
# macOS
brew install bento4

# Ubuntu/Debian
apt-get install bento4

# Or download from https://www.bento4.com/downloads/
```

## Installation

This project uses [uv](https://github.com/astral-sh/uv) for Python package management:

```bash
# Install dependencies
uv sync

# Or run commands directly with uv
uv run hypercorn dash2hls.server:app
```

## Running the Server

Start the server on default port 8000:

```bash
uv run hypercorn dash2hls.server:app
```

Or specify a custom host and port:

```bash
uv run hypercorn dash2hls.server:app --bind 0.0.0.0:8080
```

## CLI Commands

### Add a Stream

Add a new DASH stream to convert:

```bash
# Basic usage (no decryption)
uv run dash2hls add-stream --mpd-url https://example.com/manifest.mpd

# With decryption key
uv run dash2hls add-stream \
  --mpd-url https://example.com/manifest.mpd \
  --key 0123456789abcdef0123456789abcdef \
  --kid 00000000000000000000000000000000

# Specify server URL
uv run dash2hls add-stream \
  --mpd-url https://example.com/manifest.mpd \
  --server http://localhost:8080
```

### List Streams

View all active streams:

```bash
uv run dash2hls list-streams
```

### Get Stream Info

Get details about a specific stream:

```bash
uv run dash2hls get-stream --stream-id <uuid>
```

### Remove a Stream

Stop and remove a stream:

```bash
uv run dash2hls remove-stream --stream-id <uuid>
```

## API Endpoints

### `GET /`
Get API information and available endpoints.

### `GET /streams`
List all active streams with their status and metadata.

### `POST /streams`
Start converting a new DASH stream.

**Request body:**
```json
{
  "mpd_url": "https://example.com/manifest.mpd",
  "key": "0123456789abcdef0123456789abcdef",
  "kid": "00000000000000000000000000000000",
  "representation_id": "video-1080p",
  "label": "My Stream",
  "poll_interval": 4.0,
  "window_size": 6
}
```

**Response:**
```json
{
  "stream_id": "uuid",
  "hls_url": "/hls/uuid/master.m3u8",
  "status": "starting"
}
```

### `GET /streams/<stream_id>`
Get information about a specific stream.

### `DELETE /streams/<stream_id>`
Stop and remove a stream.

### `GET /hls/<stream_id>/<path:filename>`
Serve HLS playlists and segments.

**Examples:**
- `GET /hls/<stream_id>/master.m3u8` - Master playlist
- `GET /hls/<stream_id>/index.m3u8` - Media playlist
- `GET /hls/<stream_id>/init.mp4` - Initialization segment
- `GET /hls/<stream_id>/segment_0.m4s` - Media segment

## Configuration Options

When adding a stream via API, you can configure:

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `mpd_url` | string | **Required.** URL of the DASH MPD manifest | - |
| `key` | string | Decryption key in hex format (32 chars) | None |
| `kid` | string | Key ID in hex format (32 chars) | None |
| `key_map` | object | Map of KID to key for multiple keys | None |
| `mp4decrypt_path` | string | Path to mp4decrypt binary | `mp4decrypt` |
| `representation_id` | string | Specific representation to process | Auto-select |
| `label` | string | Human-readable label for the stream | None |
| `poll_interval` | float | Seconds between MPD updates (for live) | 4.0 |
| `window_size` | int | Number of segments to keep (live only) | 6 |
| `history_size` | int | Max processed segment tracking | 128 |
| `headers` | object | Custom HTTP headers for requests | None |
| `output_dir` | string | Custom output directory path | Auto-generated |

## Architecture

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │ HTTP
       ▼
┌─────────────────┐
│  Quart Server   │
│  (HTTP API)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ StreamManager   │ ─────► Multiple concurrent streams
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ StreamSession   │ ─────► Per-stream lifecycle
└────────┬────────┘
         │
         ├──► DashParser: Parse MPD manifests
         ├──► SegmentDownloader: Async HTTP downloads
         ├──► Decryptor: CENC decryption (mp4decrypt)
         ├──► HLSWriter: Generate playlists
         └──► Output: Write segments to disk
```

## Output Structure

Each stream creates its own directory under `output/`:

```
output/
└── <stream-id>/
    ├── master.m3u8          # Master playlist
    ├── index.m3u8           # Media playlist
    ├── init.mp4             # Initialization segment
    ├── segment_0.m4s        # Media segments
    ├── segment_1.m4s
    └── ...
```

## Development

Run the server in development mode:

```bash
uv run hypercorn dash2hls.server:app --reload
```

## License

This project is provided as-is for educational and development purposes.
