"""Command-line interface for dash2hls."""

from __future__ import annotations

import asyncio
import sys

import aiohttp
import click


async def make_request(method: str, url: str, **kwargs):
    """Make an async HTTP request."""
    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, **kwargs) as response:
            response.raise_for_status()
            return await response.json()


@click.group()
def cli():
    """DASH to HLS converter CLI."""
    pass


@cli.command()
@click.option("--mpd-url", required=True, help="URL of the DASH MPD manifest")
@click.option("--key", help="Decryption key (hex string)")
@click.option("--kid", help="Key ID (hex string)")
@click.option(
    "--key-map",
    multiple=True,
    help="Provide multiple keys as KID:KEY (hex). Repeat for multiple entries.",
)
@click.option("--representation-id", help="Specific representation ID to process")
@click.option("--label", help="Human-friendly label for the stream")
@click.option("--poll-interval", type=float, help="Seconds between MPD refreshes (live)")
@click.option("--window-size", type=int, help="Number of segments to keep in live playlist")
@click.option("--history-size", type=int, help="Segment history size for deduplication")
@click.option("--mp4decrypt-path", help="Path to the mp4decrypt executable")
@click.option("--header", multiple=True, help="Additional HTTP header as Name:Value")
@click.option("--output-dir", help="Custom output directory for this stream")
@click.option("--server", default="http://localhost:8000", help="Server URL")
def add_stream(
    mpd_url,
    key,
    kid,
    key_map,
    representation_id,
    label,
    poll_interval,
    window_size,
    history_size,
    mp4decrypt_path,
    header,
    output_dir,
    server,
):
    """Add a new stream to convert."""
    payload = {
        "mpd_url": mpd_url,
    }

    if key:
        payload["key"] = key
    if kid:
        payload["kid"] = kid

    if key_map:
        key_map_dict = {}
        for item in key_map:
            if ":" not in item:
                raise click.BadParameter("--key-map entries must be in the form KID:KEY")
            km_kid, km_key = item.split(":", 1)
            key_map_dict[km_kid.strip()] = km_key.strip()
        payload["key_map"] = key_map_dict

    if representation_id:
        payload["representation_id"] = representation_id
    if label:
        payload["label"] = label
    if poll_interval is not None:
        payload["poll_interval"] = poll_interval
    if window_size is not None:
        payload["window_size"] = window_size
    if history_size is not None:
        payload["history_size"] = history_size
    if mp4decrypt_path:
        payload["mp4decrypt_path"] = mp4decrypt_path
    if output_dir:
        payload["output_dir"] = output_dir

    if header:
        headers = {}
        for header_entry in header:
            if ":" not in header_entry:
                raise click.BadParameter("Headers must be in the form Name:Value")
            name, value = header_entry.split(":", 1)
            headers[name.strip()] = value.strip()
        payload["headers"] = headers

    async def _run():
        try:
            result = await make_request("POST", f"{server}/streams", json=payload)
            click.echo("Stream added successfully!")
            click.echo(f"Stream ID: {result['stream_id']}")
            click.echo(f"HLS URL: {server}{result['hls_url']}")
            click.echo(f"Status: {result['status']}")
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    asyncio.run(_run())


@cli.command()
@click.option("--stream-id", required=True, help="Stream ID to remove")
@click.option("--server", default="http://localhost:8000", help="Server URL")
def remove_stream(stream_id, server):
    """Remove a stream."""
    async def _run():
        try:
            await make_request("DELETE", f"{server}/streams/{stream_id}")
            click.echo(f"Stream {stream_id} removed successfully!")
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
    
    asyncio.run(_run())


@cli.command()
@click.option("--server", default="http://localhost:8000", help="Server URL")
def list_streams(server):
    """List all active streams."""
    async def _run():
        try:
            result = await make_request("GET", f"{server}/streams")
            streams = result.get("streams", [])

            if not streams:
                click.echo("No active streams")
                return

            click.echo(f"Found {len(streams)} stream(s):")
            click.echo()

            for stream in streams:
                click.echo(f"Stream ID: {stream['stream_id']}")
                click.echo(f"  MPD URL: {stream['mpd_url']}")
                click.echo(f"  Status: {stream['status']}")
                click.echo(f"  HLS URL: {server}{stream['hls_url']}")
                if stream.get("is_live") is not None:
                    click.echo(f"  Live: {stream['is_live']}")
                if stream.get("representation_id"):
                    click.echo(f"  Representation: {stream['representation_id']}")
                if stream.get("bandwidth"):
                    click.echo(f"  Bandwidth: {stream['bandwidth']} bps")
                if stream.get("codecs"):
                    click.echo(f"  Codecs: {stream['codecs']}")
                if stream.get("resolution"):
                    width, height = stream["resolution"]
                    click.echo(f"  Resolution: {width}x{height}")
                if stream.get("label"):
                    click.echo(f"  Label: {stream['label']}")
                if stream.get("last_sequence") is not None:
                    click.echo(f"  Last Sequence: {stream['last_sequence']}")
                if stream.get("error"):
                    click.echo(f"  Error: {stream['error']}")
                click.echo()
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    asyncio.run(_run())


@cli.command()
@click.option("--stream-id", required=True, help="Stream ID to check")
@click.option("--server", default="http://localhost:8000", help="Server URL")
def get_stream(stream_id, server):
    """Get information about a specific stream."""
    async def _run():
        try:
            stream = await make_request("GET", f"{server}/streams/{stream_id}")

            click.echo(f"Stream ID: {stream['stream_id']}")
            click.echo(f"MPD URL: {stream['mpd_url']}")
            click.echo(f"Status: {stream['status']}")
            click.echo(f"HLS URL: {server}{stream['hls_url']}")
            if stream.get("is_live") is not None:
                click.echo(f"Live: {stream['is_live']}")
            if stream.get("representation_id"):
                click.echo(f"Representation: {stream['representation_id']}")
            if stream.get("bandwidth"):
                click.echo(f"Bandwidth: {stream['bandwidth']} bps")
            if stream.get("codecs"):
                click.echo(f"Codecs: {stream['codecs']}")
            if stream.get("resolution"):
                width, height = stream["resolution"]
                click.echo(f"Resolution: {width}x{height}")
            if stream.get("label"):
                click.echo(f"Label: {stream['label']}")
            if stream.get("last_sequence") is not None:
                click.echo(f"Last Sequence: {stream['last_sequence']}")
            if stream.get("error"):
                click.echo(f"Error: {stream['error']}")
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    asyncio.run(_run())


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
