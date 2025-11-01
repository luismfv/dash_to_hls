#!/usr/bin/env python3
"""Test DASH manifest parsing with SegmentTemplate addressing."""

import sys

from dash2hls.dash_parser import DashParser


SAMPLE_MPD = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<MPD xmlns=\"urn:mpeg:dash:schema:mpd:2011\"
     xmlns:cenc=\"urn:mpeg:cenc:2013\"
     mediaPresentationDuration=\"PT0H0M8.000S\">
  <Period duration=\"PT0H0M8.000S\">
    <AdaptationSet mimeType=\"video/mp4\" segmentAlignment=\"true\">
      <SegmentTemplate timescale=\"24\" media=\"video/$Number%02d$.m4s\"
                        initialization=\"video/init.mp4\" startNumber=\"1\"
                        duration=\"96\" />
      <Representation id=\"video-main\" codecs=\"avc1.4d401e\"
                      bandwidth=\"800000\" width=\"1280\" height=\"720\">
        <ContentProtection schemeIdUri=\"urn:mpeg:dash:mp4protection:2011\"
                            value=\"cenc\"
                            cenc:default_KID=\"11111111-2222-3333-4444-555555555555\" />
      </Representation>
    </AdaptationSet>
    <AdaptationSet mimeType=\"audio/mp4\" segmentAlignment=\"true\">
      <SegmentTemplate timescale=\"48000\" media=\"audio/$Number%02d$.m4s\"
                        initialization=\"audio/init.mp4\" startNumber=\"5\"
                        duration=\"192000\" />
      <Representation id=\"audio-main\" codecs=\"mp4a.40.2\" bandwidth=\"128000\">
        <ContentProtection schemeIdUri=\"urn:mpeg:dash:mp4protection:2011\"
                            value=\"cenc\"
                            cenc:default_KID=\"66666666-7777-8888-9999-aaaaaaaaaaaa\" />
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


def test_segment_template_parsing() -> bool:
    try:
        manifest = DashParser.parse(SAMPLE_MPD, "https://example.com/manifest.mpd")
        assert len(manifest.representations) == 2

        video = next(rep for rep in manifest.representations if rep.is_video)
        audio = next(rep for rep in manifest.representations if rep.is_audio)

        assert video.init_url == "https://example.com/video/init.mp4"
        assert audio.init_url == "https://example.com/audio/init.mp4"
        assert video.segments[0].url == "https://example.com/video/01.m4s"
        assert video.segments[0].number == 1
        assert audio.segments[0].url == "https://example.com/audio/05.m4s"
        assert audio.segments[0].number == 5
        assert abs(video.segments[0].duration - 4.0) < 1e-6

        print("\u2713 DashParser SegmentTemplate parsing test passed")
        return True
    except Exception as exc:
        print(f"\u2717 DashParser SegmentTemplate parsing test failed: {exc}")
        return False


if __name__ == "__main__":
    sys.exit(0 if test_segment_template_parsing() else 1)
