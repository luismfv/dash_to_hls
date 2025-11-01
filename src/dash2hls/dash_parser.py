"""Parse DASH MPD manifests and extract segment information."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse
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


@dataclass
class _ResolvedSegmentTemplate:
    """Helpers for resolved SegmentTemplate attributes."""

    initialization: Optional[str]
    media: Optional[str]
    timescale: int
    duration: Optional[int]
    start_number: int
    presentation_time_offset: int
    timeline: Optional[etree._Element]


class DashParser:
    """Parser for DASH MPD manifests."""

    DASH_NS = {
        "mpd": "urn:mpeg:dash:schema:mpd:2011",
        "cenc": "urn:mpeg:cenc:2013",
    }

    FALLBACK_SEGMENT_COUNT = 200
    MAX_TIMELINE_REPEAT = 30

    @staticmethod
    def parse(mpd_content: str, mpd_url: str) -> DashManifest:
        """Parse MPD manifest content."""
        root = etree.fromstring(mpd_content.encode("utf-8"))

        mpd_dir = DashParser._base_dir(mpd_url)
        manifest_base = DashParser._apply_base_url(mpd_dir, root)

        mpd_type = (root.get("type", "static") or "static").lower()
        is_live = mpd_type == "dynamic"

        duration_str = root.get("mediaPresentationDuration")
        media_duration = (
            DashParser._parse_duration(duration_str) if duration_str else None
        )

        min_update_str = root.get("minimumUpdatePeriod")
        min_update = (
            DashParser._parse_duration(min_update_str) if min_update_str else None
        )

        representations: List[DashRepresentation] = []

        for period in root.findall("./mpd:Period", namespaces=DashParser.DASH_NS):
            period_duration = (
                DashParser._parse_duration(period.get("duration"))
                if period.get("duration")
                else None
            )
            period_base = DashParser._apply_base_url(manifest_base, period)

            for adaptation_set in period.findall(
                "./mpd:AdaptationSet", namespaces=DashParser.DASH_NS
            ):
                if DashParser._skip_adaptation_set(adaptation_set):
                    continue

                adaptation_base = DashParser._apply_base_url(period_base, adaptation_set)

                for representation in adaptation_set.findall(
                    "./mpd:Representation", namespaces=DashParser.DASH_NS
                ):
                    rep_id = representation.get("id") or ""
                    if not rep_id:
                        continue

                    rep_mime = representation.get("mimeType") or adaptation_set.get(
                        "mimeType", ""
                    )
                    rep_codecs = representation.get("codecs") or adaptation_set.get(
                        "codecs", ""
                    )
                    width = DashParser._maybe_int(representation.get("width"))
                    height = DashParser._maybe_int(representation.get("height"))
                    bandwidth = DashParser._safe_int(
                        representation.get("bandwidth"), default=0
                    )

                    is_video, is_audio = DashParser._classify_track(
                        adaptation_set, representation
                    )
                    if not is_video and not is_audio:
                        continue

                    default_kid = DashParser._resolve_default_kid(
                        adaptation_set, representation
                    )

                    rep_base = DashParser._apply_base_url(adaptation_base, representation)

                    template = DashParser._resolve_segment_template(
                        [root, period, adaptation_set, representation]
                    )
                    segment_list = DashParser._find_first_in_hierarchy(
                        [representation, adaptation_set, period, root], "SegmentList"
                    )
                    segment_base = DashParser._find_first_in_hierarchy(
                        [representation, adaptation_set, period, root], "SegmentBase"
                    )

                    init_url: str = ""
                    segments: List[DashSegment] = []

                    total_duration = period_duration or media_duration

                    if template and template.media:
                        init_url, segments = DashParser._parse_segment_template(
                            template,
                            rep_id=rep_id,
                            base_url=rep_base,
                            bandwidth=bandwidth,
                            total_duration=total_duration,
                            is_live=is_live,
                        )
                    elif segment_list is not None:
                        init_url, segments = DashParser._parse_segment_list(
                            segment_list, rep_base
                        )
                    elif segment_base is not None:
                        init_url, segments = DashParser._parse_segment_base(
                            segment_base, rep_base, total_duration
                        )
                    else:
                        # Representation without known segment addressing
                        continue

                    if not init_url or not segments:
                        continue

                    representations.append(
                        DashRepresentation(
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
                    )

        return DashManifest(
            base_url=manifest_base,
            media_presentation_duration=media_duration,
            representations=representations,
            is_live=is_live,
            min_update_period=min_update,
        )

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _base_dir(url: str) -> str:
        if url.endswith("/"):
            return url
        if "/" not in url:
            return url + "/"
        return url.rsplit("/", 1)[0] + "/"

    @staticmethod
    def _get_child(element: Optional[etree._Element], tag: str) -> Optional[etree._Element]:
        if element is None:
            return None
        return element.find(f"./mpd:{tag}", namespaces=DashParser.DASH_NS)

    @staticmethod
    def _apply_base_url(current_base: str, element: Optional[etree._Element]) -> str:
        if element is None:
            return current_base
        base_elem = DashParser._get_child(element, "BaseURL")
        if base_elem is None or not base_elem.text:
            return current_base
        return DashParser._resolve_url(current_base, base_elem.text.strip())

    @staticmethod
    def _resolve_url(base: str, relative: str) -> str:
        parsed = urlparse(relative)
        if parsed.scheme:
            return relative
        return urljoin(base, relative)

    @staticmethod
    def _safe_int(value: Optional[str], default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _maybe_int(value: Optional[str]) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _skip_adaptation_set(adaptation_set: etree._Element) -> bool:
        content_type = (adaptation_set.get("contentType") or "").lower()
        mime_type = (adaptation_set.get("mimeType") or "").lower()
        if content_type and content_type not in {"audio", "video"}:
            return True
        if any(text_key in mime_type for text_key in ("text", "ttml", "vtt", "srt")):
            return True
        return False

    @staticmethod
    def _classify_track(
        adaptation_set: etree._Element, representation: etree._Element
    ) -> tuple[bool, bool]:
        mime_candidates = [
            (representation.get("mimeType") or "").lower(),
            (adaptation_set.get("mimeType") or "").lower(),
        ]
        content_candidates = [
            (representation.get("contentType") or "").lower(),
            (adaptation_set.get("contentType") or "").lower(),
        ]

        is_video = any("video" in value for value in mime_candidates) or any(
            value == "video" for value in content_candidates
        )
        is_audio = any("audio" in value for value in mime_candidates) or any(
            value == "audio" for value in content_candidates
        )
        return is_video, is_audio

    @staticmethod
    def _resolve_segment_template(
        elements: List[Optional[etree._Element]],
    ) -> Optional[_ResolvedSegmentTemplate]:
        merged: dict[str, str] = {}
        timeline: Optional[etree._Element] = None

        for element in elements:
            if element is None:
                continue
            template = DashParser._get_child(element, "SegmentTemplate")
            if template is None:
                continue
            merged.update(template.attrib)
            timeline_candidate = DashParser._get_child(template, "SegmentTimeline")
            if timeline_candidate is not None:
                timeline = timeline_candidate

        if not merged and timeline is None:
            return None

        timescale = DashParser._safe_int(merged.get("timescale"), default=1)
        duration = DashParser._maybe_int(merged.get("duration"))
        start_number = DashParser._safe_int(merged.get("startNumber"), default=1)
        presentation_time_offset = DashParser._safe_int(
            merged.get("presentationTimeOffset"), default=0
        )

        return _ResolvedSegmentTemplate(
            initialization=merged.get("initialization"),
            media=merged.get("media"),
            timescale=timescale if timescale > 0 else 1,
            duration=duration,
            start_number=start_number,
            presentation_time_offset=presentation_time_offset,
            timeline=timeline,
        )

    @staticmethod
    def _find_first_in_hierarchy(
        elements: List[Optional[etree._Element]], tag: str
    ) -> Optional[etree._Element]:
        for element in elements:
            found = DashParser._get_child(element, tag)
            if found is not None:
                return found
        return None

    # ------------------------------------------------------------------
    # Segment parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_segment_template(
        template: _ResolvedSegmentTemplate,
        *,
        rep_id: str,
        base_url: str,
        bandwidth: int,
        total_duration: Optional[float],
        is_live: bool,
    ) -> tuple[str, List[DashSegment]]:
        init_url = ""
        if template.initialization:
            init_path = DashParser._fill_template(
                template.initialization,
                rep_id=rep_id,
                number=template.start_number,
                time=0,
                bandwidth=bandwidth,
            )
            if init_path:
                init_url = DashParser._resolve_url(base_url, init_path)

        segments: List[DashSegment] = []
        if not template.media:
            return init_url, segments

        if template.timeline is not None:
            segments = DashParser._parse_segment_timeline(
                template,
                rep_id=rep_id,
                base_url=base_url,
                bandwidth=bandwidth,
                is_live=is_live,
            )
        elif template.duration:
            duration_units = template.duration
            timescale = template.timescale or 1
            segment_duration = duration_units / timescale
            if total_duration and segment_duration > 0:
                estimate = math.ceil(total_duration / segment_duration)
                num_segments = max(1, estimate)
            else:
                num_segments = DashParser.FALLBACK_SEGMENT_COUNT

            time_cursor = template.presentation_time_offset
            for offset in range(num_segments):
                seg_number = template.start_number + offset
                media_path = DashParser._fill_template(
                    template.media,
                    rep_id=rep_id,
                    number=seg_number,
                    time=time_cursor,
                    bandwidth=bandwidth,
                )
                if not media_path:
                    break
                segments.append(
                    DashSegment(
                        url=DashParser._resolve_url(base_url, media_path),
                        duration=segment_duration,
                        number=seg_number,
                    )
                )
                time_cursor += duration_units

        return init_url, segments

    @staticmethod
    def _parse_segment_timeline(
        template: _ResolvedSegmentTemplate,
        *,
        rep_id: str,
        base_url: str,
        bandwidth: int,
        is_live: bool,
    ) -> List[DashSegment]:
        timeline = template.timeline
        if timeline is None or not template.media:
            return []

        segments: List[DashSegment] = []
        timescale = template.timescale or 1
        number = template.start_number
        current_time = template.presentation_time_offset
        last_duration = template.duration

        for s in timeline.findall("./mpd:S", namespaces=DashParser.DASH_NS):
            if s.get("t") is not None:
                current_time = DashParser._safe_int(s.get("t"), default=current_time)

            d_value = s.get("d")
            if d_value is not None:
                duration_units = DashParser._safe_int(d_value, default=0)
                last_duration = duration_units if duration_units > 0 else last_duration
            elif last_duration:
                duration_units = last_duration
            else:
                continue

            if duration_units <= 0:
                continue

            repeat = DashParser._safe_int(s.get("r"), default=0)
            if repeat < 0:
                repeat = DashParser.MAX_TIMELINE_REPEAT if is_live else 0

            for _ in range(repeat + 1):
                time_value = current_time - template.presentation_time_offset
                media_path = DashParser._fill_template(
                    template.media,
                    rep_id=rep_id,
                    number=number,
                    time=time_value,
                    bandwidth=bandwidth,
                )
                if not media_path:
                    break

                segments.append(
                    DashSegment(
                        url=DashParser._resolve_url(base_url, media_path),
                        duration=duration_units / timescale,
                        number=number,
                    )
                )
                number += 1
                current_time += duration_units

        return segments

    @staticmethod
    def _parse_segment_list(
        segment_list: etree._Element, base_url: str
    ) -> tuple[str, List[DashSegment]]:
        init_url = ""
        init_elem = DashParser._get_child(segment_list, "Initialization")
        if init_elem is not None and init_elem.get("sourceURL"):
            init_url = DashParser._resolve_url(base_url, init_elem.get("sourceURL"))

        timescale = DashParser._safe_int(segment_list.get("timescale"), default=1)
        default_duration_units = DashParser._maybe_int(segment_list.get("duration"))
        default_duration = (
            default_duration_units / timescale
            if default_duration_units and timescale
            else None
        )

        segments: List[DashSegment] = []
        start_number = DashParser._safe_int(
            segment_list.get("startNumber"), default=1
        )

        for idx, seg_elem in enumerate(
            segment_list.findall("./mpd:SegmentURL", namespaces=DashParser.DASH_NS)
        ):
            media_attr = seg_elem.get("media")
            if not media_attr:
                continue
            media_url = DashParser._resolve_url(base_url, media_attr)

            duration_units = DashParser._maybe_int(seg_elem.get("duration"))
            if duration_units and timescale:
                duration = duration_units / timescale
            else:
                duration = default_duration if default_duration is not None else 0.0

            segments.append(
                DashSegment(
                    url=media_url,
                    duration=duration,
                    number=start_number + idx,
                )
            )

        return init_url, segments

    @staticmethod
    def _parse_segment_base(
        segment_base: etree._Element,
        base_url: str,
        total_duration: Optional[float],
    ) -> tuple[str, List[DashSegment]]:
        init_url = ""
        init_elem = DashParser._get_child(segment_base, "Initialization")
        if init_elem is not None and init_elem.get("sourceURL"):
            init_url = DashParser._resolve_url(base_url, init_elem.get("sourceURL"))

        segments: List[DashSegment] = []
        if total_duration is not None:
            segments.append(
                DashSegment(url=base_url, duration=total_duration, number=1)
            )

        return init_url, segments

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fill_template(
        template: str,
        *,
        rep_id: str,
        number: int,
        time: int,
        bandwidth: int,
    ) -> str:
        if not template:
            return ""

        result = template.replace("$$", "\x00")
        result = result.replace("$RepresentationID$", rep_id)
        result = result.replace("$Number$", str(number))
        result = result.replace("$Time$", str(time))
        result = result.replace("$Bandwidth$", str(bandwidth))

        pattern = r"\$(\w+)%(0?)(\d*)([diouxX])\$"

        def replace(match: re.Match[str]) -> str:
            var_name, zero_flag, width_str, _ = match.groups()
            width = int(width_str) if width_str else 0
            zero_pad = zero_flag == "0"

            value_map = {
                "Number": number,
                "Time": time,
                "Bandwidth": bandwidth,
            }

            if var_name == "RepresentationID":
                return rep_id

            value = value_map.get(var_name)
            if value is None:
                return match.group(0)

            value_str = str(value)
            if width > 0:
                pad_char = "0" if zero_pad else " "
                value_str = value_str.rjust(width, pad_char)
            return value_str

        result = re.sub(pattern, replace, result)
        return result.replace("\x00", "$")

    @staticmethod
    def _resolve_default_kid(
        adaptation_set: etree._Element, representation: etree._Element
    ) -> Optional[str]:
        for element in (representation, adaptation_set):
            if element is None:
                continue

            direct = (
                element.get("{urn:mpeg:cenc:2013}default_KID")
                or element.get("cenc:default_KID")
                or element.get("default_KID")
            )
            if direct:
                return direct.replace("-", "").lower()

            for cp in element.findall(
                "./mpd:ContentProtection", namespaces=DashParser.DASH_NS
            ):
                kid = (
                    cp.get("{urn:mpeg:cenc:2013}default_KID")
                    or cp.get("cenc:default_KID")
                    or cp.get("default_KID")
                )
                if kid:
                    return kid.replace("-", "").lower()
        return None

    @staticmethod
    def _parse_duration(duration_str: str) -> float:
        if not duration_str:
            return 0.0

        pattern = (
            r"P"
            r"(?:(?P<years>\d+)Y)?"
            r"(?:(?P<months>\d+)M)?"
            r"(?:(?P<days>\d+)D)?"
            r"(?:T"
            r"(?:(?P<hours>\d+)H)?"
            r"(?:(?P<minutes>\d+)M)?"
            r"(?:(?P<seconds>[\d.]+)S)?"
            r")?"
        )
        match = re.fullmatch(pattern, duration_str)
        if not match:
            # Fallback to PT... format
            alt_pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?"
            alt_match = re.fullmatch(alt_pattern, duration_str)
            if not alt_match:
                return 0.0
            hours = int(alt_match.group(1) or 0)
            minutes = int(alt_match.group(2) or 0)
            seconds = float(alt_match.group(3) or 0)
            return hours * 3600 + minutes * 60 + seconds

        years = int(match.group("years") or 0)
        months = int(match.group("months") or 0)
        days = int(match.group("days") or 0)
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes") or 0)
        seconds = float(match.group("seconds") or 0)

        total_days = years * 365 + months * 30 + days
        return total_days * 86400 + hours * 3600 + minutes * 60 + seconds
