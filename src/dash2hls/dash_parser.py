"""Parse DASH MPD manifests and extract segment information."""

import math
import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin
from lxml import etree


@dataclass
class DashSegment:
    """Represents a single DASH segment."""
    url: str
    duration: float
    number: int


@dataclass
class DashRepresentation:
    """Represents a DASH representation (quality level)."""
    id: str
    bandwidth: int
    codecs: str
    mime_type: str
    width: Optional[int]
    height: Optional[int]
    init_url: str
    segments: List[DashSegment]
    is_video: bool
    is_audio: bool
    default_kid: Optional[str]


@dataclass
class DashManifest:
    """Parsed DASH manifest information."""
    base_url: str
    media_presentation_duration: Optional[float]
    representations: List[DashRepresentation]
    is_live: bool
    min_update_period: Optional[float]


class DashParser:
    """Parser for DASH MPD manifests."""

    DASH_NS = {
        'mpd': 'urn:mpeg:dash:schema:mpd:2011',
        'cenc': 'urn:mpeg:cenc:2013',
    }

    @staticmethod
    def parse(mpd_content: str, mpd_url: str) -> DashManifest:
        """
        Parse MPD manifest content.
        
        Args:
            mpd_content: XML content of the MPD
            mpd_url: URL of the MPD (for resolving relative URLs)
            
        Returns:
            Parsed DashManifest object
        """
        root = etree.fromstring(mpd_content.encode('utf-8'))
        
        base_url = mpd_url.rsplit('/', 1)[0] + '/'
        base_url_elem = root.find('.//mpd:BaseURL', namespaces=DashParser.DASH_NS)
        if base_url_elem is not None and base_url_elem.text:
            base_url = urljoin(base_url, base_url_elem.text.strip())

        mpd_type = root.get('type', 'static').lower()
        is_live = mpd_type == 'dynamic'

        duration_str = root.get('mediaPresentationDuration')
        media_duration = DashParser._parse_duration(duration_str) if duration_str else None

        min_update_str = root.get('minimumUpdatePeriod')
        min_update = DashParser._parse_duration(min_update_str) if min_update_str else None

        representations = []
        
        for period in root.findall('.//mpd:Period', namespaces=DashParser.DASH_NS):
            period_duration = DashParser._parse_duration(period.get('duration')) if period.get('duration') else None

            for adaptation_set in period.findall('.//mpd:AdaptationSet', namespaces=DashParser.DASH_NS):
                mime_type = adaptation_set.get('mimeType', '')
                codecs = adaptation_set.get('codecs', '')
                
                is_video = 'video' in mime_type
                is_audio = 'audio' in mime_type
                
                for representation in adaptation_set.findall('.//mpd:Representation', namespaces=DashParser.DASH_NS):
                    rep_id = representation.get('id')
                    bandwidth = int(representation.get('bandwidth', 0))

                    rep_mime = representation.get('mimeType', mime_type)
                    rep_codecs = representation.get('codecs', codecs)
                    width = representation.get('width')
                    height = representation.get('height')

                    if width:
                        width = int(width)
                    if height:
                        height = int(height)

                    default_kid = DashParser._resolve_default_kid(adaptation_set, representation)

                    segment_template = representation.find('.//mpd:SegmentTemplate', namespaces=DashParser.DASH_NS)
                    if segment_template is None:
                        segment_template = adaptation_set.find('.//mpd:SegmentTemplate', namespaces=DashParser.DASH_NS)

                    if segment_template is not None:
                        init_url, segments = DashParser._parse_segment_template(
                            segment_template,
                            rep_id,
                            base_url,
                            bandwidth,
                            total_duration=period_duration or media_duration,
                        )
                    else:
                        segment_list = representation.find('.//mpd:SegmentList', namespaces=DashParser.DASH_NS)
                        if segment_list is None:
                            segment_list = adaptation_set.find('.//mpd:SegmentList', namespaces=DashParser.DASH_NS)

                        if segment_list is not None:
                            init_url, segments = DashParser._parse_segment_list(segment_list, base_url)
                        else:
                            continue

                    rep_obj = DashRepresentation(
                        id=rep_id,
                        bandwidth=bandwidth,
                        codecs=rep_codecs,
                        mime_type=rep_mime,
                        width=width,
                        height=height,
                        init_url=init_url,
                        segments=segments,
                        is_video=is_video,
                        is_audio=is_audio,
                        default_kid=default_kid,
                    )
                    representations.append(rep_obj)

        return DashManifest(
            base_url=base_url,
            media_presentation_duration=media_duration,
            representations=representations,
            is_live=is_live,
            min_update_period=min_update
        )

    @staticmethod
    def _resolve_default_kid(adaptation_set, representation) -> Optional[str]:
        """Extract default_KID from ContentProtection elements."""
        for element in (representation, adaptation_set):
            if element is None:
                continue

            direct = (
                element.get('{urn:mpeg:cenc:2013}default_KID')
                or element.get('cenc:default_KID')
                or element.get('default_KID')
            )
            if direct:
                return direct.replace('-', '').lower()

            for cp in element.findall('./mpd:ContentProtection', namespaces=DashParser.DASH_NS):
                kid = (
                    cp.get('{urn:mpeg:cenc:2013}default_KID')
                    or cp.get('cenc:default_KID')
                    or cp.get('default_KID')
                )
                if kid:
                    return kid.replace('-', '').lower()

        return None

    @staticmethod
    def _parse_segment_template(
        template, rep_id: str, base_url: str, bandwidth: int, total_duration: Optional[float] = None
    ) -> tuple:
        """Parse SegmentTemplate element."""
        init_template = template.get('initialization', '')
        media_template = template.get('media', '')

        timescale = int(template.get('timescale', 1))
        duration = template.get('duration')
        duration = int(duration) if duration is not None else None
        start_number = int(template.get('startNumber', 1))

        init_url = DashParser._fill_template(init_template, rep_id, start_number, 0, bandwidth)
        init_url = urljoin(base_url, init_url)

        segments: List[DashSegment] = []

        timeline = template.find('.//mpd:SegmentTimeline', namespaces=DashParser.DASH_NS)
        if timeline is not None:
            segments = DashParser._parse_segment_timeline(
                timeline,
                media_template,
                rep_id,
                base_url,
                bandwidth,
                timescale,
                start_number,
            )
        elif duration is not None and duration > 0:
            segment_duration = duration / timescale
            if total_duration and segment_duration > 0:
                num_segments = max(1, math.ceil(total_duration / segment_duration))
            else:
                num_segments = 200

            for i in range(num_segments):
                seg_num = start_number + i
                seg_url = DashParser._fill_template(media_template, rep_id, seg_num, 0, bandwidth)
                seg_url = urljoin(base_url, seg_url)

                segments.append(
                    DashSegment(
                        url=seg_url,
                        duration=segment_duration,
                        number=seg_num,
                    )
                )

        return init_url, segments

    @staticmethod
    def _parse_segment_timeline(timeline, media_template: str, rep_id: str, base_url: str, bandwidth: int, timescale: int, start_number: int) -> List[DashSegment]:
        """Parse SegmentTimeline element."""
        segments = []
        time = 0
        number = start_number

        for s in timeline.findall('.//mpd:S', namespaces=DashParser.DASH_NS):
            t = s.get('t')
            if t is not None:
                time = int(t)

            d = int(s.get('d', 0))
            r = int(s.get('r', 0))

            for _ in range(r + 1):
                seg_url = DashParser._fill_template(media_template, rep_id, number, time, bandwidth)
                seg_url = urljoin(base_url, seg_url)

                segments.append(
                    DashSegment(
                        url=seg_url,
                        duration=d / timescale,
                        number=number,
                    )
                )

                time += d
                number += 1

        return segments

    @staticmethod
    def _parse_segment_list(segment_list, base_url: str) -> tuple:
        """Parse SegmentList element."""
        init_elem = segment_list.find('.//mpd:Initialization', namespaces=DashParser.DASH_NS)
        init_url = urljoin(base_url, init_elem.get('sourceURL', '')) if init_elem is not None else ''
        
        segments = []
        duration = float(segment_list.get('duration', 1.0))
        
        for idx, seg_url_elem in enumerate(segment_list.findall('.//mpd:SegmentURL', namespaces=DashParser.DASH_NS)):
            media_url = seg_url_elem.get('media', '')
            media_url = urljoin(base_url, media_url)
            
            segments.append(DashSegment(
                url=media_url,
                duration=duration,
                number=idx + 1
            ))
        
        return init_url, segments

    @staticmethod
    def _fill_template(template: str, rep_id: str, number: int, time: int, bandwidth: int) -> str:
        """Fill in template variables."""
        result = template
        result = result.replace('$RepresentationID$', rep_id)
        result = result.replace('$Number$', str(number))
        result = result.replace('$Time$', str(time))
        result = result.replace('$Bandwidth$', str(bandwidth))
        
        format_pattern = r'\$(\w+)%0(\d+)d\$'
        
        def replace_format(match):
            var_name = match.group(1)
            width = int(match.group(2))
            
            if var_name == 'Number':
                return str(number).zfill(width)
            elif var_name == 'Time':
                return str(time).zfill(width)
            else:
                return match.group(0)
        
        result = re.sub(format_pattern, replace_format, result)
        
        return result

    @staticmethod
    def _parse_duration(duration_str: str) -> float:
        """Parse ISO 8601 duration string to seconds."""
        if not duration_str:
            return 0.0
        
        pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?'
        match = re.match(pattern, duration_str)
        
        if not match:
            return 0.0
        
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = float(match.group(3) or 0)
        
        return hours * 3600 + minutes * 60 + seconds
