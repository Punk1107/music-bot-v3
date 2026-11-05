# -*- coding: utf-8 -*-
"""podcast/parser.py — Native Podcast RSS XML Parser (Pure Python Standard Library)."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional
import xml.etree.ElementTree as ET

import aiohttp

logger = logging.getLogger(__name__)

# Standard namespaces used in podcast RSS feeds
_ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


@dataclass
class PodcastEpisode:
    title: str
    audio_url: str
    duration: int = 0  # seconds
    pub_date: str = ""
    description: str = ""


@dataclass
class PodcastShow:
    title: str
    description: str
    image_url: Optional[str]
    episodes: list[PodcastEpisode]


def _parse_itunes_duration(duration_str: str) -> int:
    """Parse iTunes duration (seconds, MM:SS, or HH:MM:SS) into total seconds."""
    if not duration_str:
        return 0
    raw = duration_str.strip()
    if ":" in raw:
        parts = raw.split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            return 0
    try:
        return int(float(raw))
    except ValueError:
        return 0


def parse_podcast_xml(xml_content: str) -> Optional[PodcastShow]:
    """Parse podcast RSS XML text using stdlib xml.etree.ElementTree."""
    try:
        root = ET.fromstring(xml_content)
    except Exception as exc:
        logger.warning("Failed to parse podcast XML: %s", exc)
        return None

    channel = root.find("channel")
    if channel is None:
        return None

    # Extract show title & description
    show_title = getattr(channel.find("title"), "text", None) or "Unknown Podcast"
    show_desc = getattr(channel.find("description"), "text", None) or ""

    # Extract show artwork
    image_url: Optional[str] = None
    itunes_img = channel.find(f"{_ITUNES_NS}image")
    if itunes_img is not None and "href" in itunes_img.attrib:
        image_url = itunes_img.attrib["href"]
    else:
        img_node = channel.find("image")
        if img_node is not None:
            url_node = img_node.find("url")
            if url_node is not None and url_node.text:
                image_url = url_node.text.strip()

    episodes: list[PodcastEpisode] = []

    for item in channel.findall("item"):
        title_node = item.find("title")
        title = title_node.text.strip() if title_node is not None and title_node.text else "Untitled Episode"

        # Find audio enclosure
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        audio_url = enclosure.attrib.get("url")
        if not audio_url:
            continue

        # Duration
        dur_node = item.find(f"{_ITUNES_NS}duration")
        duration = _parse_itunes_duration(dur_node.text) if dur_node is not None and dur_node.text else 0

        # Pub Date
        pub_node = item.find("pubDate")
        pub_date = pub_node.text.strip() if pub_node is not None and pub_node.text else ""

        # Description
        desc_node = item.find("description")
        desc = desc_node.text.strip() if desc_node is not None and desc_node.text else ""

        episodes.append(
            PodcastEpisode(
                title=title,
                audio_url=audio_url,
                duration=duration,
                pub_date=pub_date,
                description=desc,
            )
        )

    return PodcastShow(
        title=show_title,
        description=show_desc,
        image_url=image_url,
        episodes=episodes,
    )


async def fetch_and_parse_podcast(
    session: aiohttp.ClientSession, url: str
) -> Optional[PodcastShow]:
    """Fetch RSS XML from URL and parse into a PodcastShow instance."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MusicBot/5.0 PodcastParser",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    timeout = aiohttp.ClientTimeout(total=15.0)
    try:
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                logger.warning("Podcast fetch returned HTTP %d for %s", resp.status, url)
                return None
            xml_text = await resp.text()
            return parse_podcast_xml(xml_text)
    except Exception as exc:
        logger.error("Podcast fetch exception for %s: %s", url, exc)
        return None

