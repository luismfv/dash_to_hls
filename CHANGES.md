# Changelog

## 2025-11-01 - Fixed mp4decrypt stdin/stdout compatibility issues

### Problem
The mp4decrypt decryption was failing with error: `ERROR: cannot open input file (-) -4`

This was caused by using stdin/stdout pipes (`-` arguments) which have poor or inconsistent support across different versions and platforms of mp4decrypt.

### Solution
Modified `Mp4DecryptBinary.decrypt_segment()` in `src/dash2hls/decryptor.py` to use temporary files instead:
- Creates a temporary directory for each decryption operation
- Writes encrypted data to a temp file
- Calls mp4decrypt with file paths (not pipes)
- Reads decrypted data from the output file
- Cleans up temporary files automatically

### Benefits
- **Better compatibility**: Works with all versions of mp4decrypt
- **More reliable**: No pipe synchronization issues
- **Better error handling**: Can validate output file existence and size
- **Industry standard**: Matches approach used by production tools like unshackle-dl

---

# Implementation Summary: Multi-Variant HLS with Real-Time Decryption

## Overview
This implementation adds three major features to the dash2hls project:

1. **mp4decrypt decryption** support with proper error handling
2. **Automatic highest quality video + audio selection** (no subtitles)
3. **Bootstrap-based web UI** for managing lives

## Changes Made

### 1. mp4decrypt Decryption Support (decryptor.py)
- **Implementation**: Uses temporary files for input/output to ensure compatibility
- **Process**: Encrypted data → temp file → mp4decrypt → decrypted file → bytes
- **Benefit**: Reliable decryption across all platforms and mp4decrypt versions

### 2. Multi-Variant HLS Support (hls_writer.py, hls_generator.py)
- **New Class**: `MultiVariantHLSWriter` manages separate video + audio variants
- **Master Playlist**: Generates proper HLS master playlist with `#EXT-X-MEDIA` entries for audio
- **Separate Tracks**: Video and audio are processed independently into separate directories
- **Benefit**: Better player compatibility, allows separate quality selection for audio/video

### 3. Automatic Highest Quality Selection (session.py)
- **Video**: Automatically selects the highest bandwidth video representation
- **Audio**: Automatically selects the highest bandwidth audio representation
- **Filtering**: Subtitle representations are excluded
- **Method**: `_select_representations()` returns tuple of (video, audio) representations

### 4. Independent Track Processing (session.py)
- **Separate State**: Each track (video/audio) maintains its own:
  - Processed segment set
  - Last sequence number
  - Segment history
- **Parallel Processing**: Video and audio segments are downloaded and decrypted independently
- **Method**: `_process_multivariant_segments()` handles both tracks

### 5. Bootstrap Web UI (server.py)
- **Route**: Root `/` now serves interactive web UI (API moved to `/api`)
- **Features**:
  - Real-time stream monitoring (auto-refresh every 5 seconds)
  - Add new lives with form (MPD URL, key, KID, etc.)
  - Remove lives with confirmation dialog
  - Display video/audio info separately (bandwidth, codecs, resolution, representation IDs)
  - Status badges with color coding (running=green, error=red, starting=blue)
- **Technology**: Pure HTML/CSS/JavaScript with Bootstrap 5.3.2 CDN

### 6. Enhanced API Responses
- **New Fields**:
  - `audio_representation_id`: ID of selected audio representation
  - `audio_bandwidth`: Audio track bandwidth
  - `audio_codecs`: Audio codec string
- **Updated Endpoints**: Both `/streams` (list) and `/streams/<id>` (get)

### 7. CLI Updates (cli.py)
- Added display of audio representation info in `list-streams` and `get-stream` commands
- Shows audio bandwidth, codecs, and representation ID

## Architecture Changes

### Before (Single Representation)
```
StreamSession → HLSWriter → Single variant
                ├── init.mp4
                └── segments...
```

### After (Multi-Variant)
```
StreamSession → MultiVariantHLSWriter
                ├── video (VariantState)
                │   ├── init.mp4
                │   └── segments...
                └── audio (VariantState)
                    ├── init.mp4
                    └── segments...
```

## Output Structure
Each stream now creates:
- `/master.m3u8` - Multi-variant master playlist
- `/index.m3u8` - Video media playlist
- `/init.mp4` - Video init segment
- `/segment_*.m4s` - Video segments
- `/audio/index.m3u8` - Audio media playlist
- `/audio/init.mp4` - Audio init segment
- `/audio/segment_*.m4s` - Audio segments

## Testing
- All existing functionality preserved
- New test file: `test_multivariant.py`
- Tests cover:
  - MultiVariantHLSWriter creation
  - Variant state management
  - Representation selection logic

## Backward Compatibility
- All existing API endpoints remain functional
- CLI commands unchanged (only enhanced)
- Configuration options unchanged
- Existing single-representation logic still works for edge cases

## Benefits
1. **Reliable decryption**: Uses file-based approach that works across all platforms
2. **Higher quality**: Always selects best video and audio
3. **Better UX**: Web UI makes management much easier
4. **Industry standard**: Multi-variant HLS is the proper way to do HLS streaming
5. **Flexible**: Players can select different audio tracks if multiple are available
