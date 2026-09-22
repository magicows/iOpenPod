"""Search Tidal for videos using tiddl's TidalAPI.

Returns plain ``TidalVideoResult`` dataclasses — no tiddl types leak out of
this module, so the panel and other callers don't need to import tiddl directly.
"""

import logging
from dataclasses import dataclass

from pydantic_core import ValidationError

log = logging.getLogger(__name__)


@dataclass
class TidalVideoResult:
    video_id: int
    title: str
    artist: str
    duration_s: int
    quality: str            # e.g. "MP4_1080P"
    image_id: str | None = None
    image_path: str | None = None  # Tidal CDN path fragment for thumbnail


def _video_to_result(video) -> TidalVideoResult:
    """Convert a tiddl ``Video`` model instance to our plain dataclass."""
    # Artist name — prefer .artist (single), fall back to .artists list
    artist_name = ""
    if getattr(video, "artist", None):
        artist_name = getattr(video.artist, "name", "")
    if not artist_name and getattr(video, "artists", None):
        artist_name = ", ".join(
            getattr(a, "name", "") for a in video.artists
            if getattr(a, "name", "")
        )

    return TidalVideoResult(
        video_id=video.id,
        title=video.title,
        artist=artist_name,
        duration_s=int(video.duration),
        quality=getattr(video, "quality", "") or "",
        image_id=getattr(video, "imageId", None),
        image_path=getattr(video, "imagePath", None),
    )


def search_videos(
    query: str,
    api=None,
    limit: int = 25,
) -> list[TidalVideoResult]:
    """Search Tidal for videos matching *query*.

    Args:
        query:  Free-text search string.
        api:    A ``TidalAPI`` instance. If ``None``, one is created from stored auth.
        limit:  Approximate max results (Tidal may return fewer).

    Returns:
        List of ``TidalVideoResult``. Empty on auth failure or API error.
    """
    if api is None:
        from .auth import get_api
        api = get_api()
    if api is None:
        log.warning("search_videos: no authenticated TidalAPI available")
        return []

    try:
        results = api.get_search(query)
    except ValidationError as e:
        # tiddl's Pydantic models are outdated — fall back to raw API parsing
        log.warning("Tidal search validation failed (tiddl models outdated), using raw API: %s", e)
        return _search_videos_raw(query, api, limit)
    except Exception:
        log.exception("Tidal search failed for query: %r", query)
        return []

    # tiddl's Search model has a .videos attribute (Search.Videos with .items list)
    videos_container = getattr(results, "videos", None)
    if videos_container is None:
        log.debug("Tidal search returned no videos container for query: %r", query)
        return []

    items = getattr(videos_container, "items", []) or []
    out = [_video_to_result(v) for v in items]

    # Tidal search also includes videos in the TopHit — skip those to avoid
    # duplication; the items list is sufficient for our purposes.
    log.debug("Tidal search for %r returned %d video(s)", query, len(out))
    return out[:limit]


def _search_videos_raw(
    query: str,
    api,
    limit: int = 25,
) -> list[TidalVideoResult]:
    """Fallback: search using raw HTTP request when Pydantic validation fails.
    
    This bypasses tiddl's outdated data models and works directly with the JSON response.
    """
    try:
        # Access tiddl's HTTP client directly
        # API_URL is a module constant in tiddl.core.api.client
        from tiddl.core.api.client import API_URL
        
        response = api.client.session.get(
            f"{API_URL}/search",
            params={
                "query": query,
                "limit": limit,
                "offset": 0,
                "types": "VIDEOS",
                "countryCode": api.country_code,
            },
        )
        response.raise_for_status()
        data = response.json()
        
        # Extract videos from raw response
        videos = data.get("videos", {}).get("items", [])
        if not videos:
            log.debug("Raw Tidal search returned no videos for query: %r", query)
            return []
        
        results = []
        for v in videos[:limit]:
            # Extract artist name from artists array
            artist_name = ""
            artists = v.get("artists", [])
            if artists:
                artist_name = ", ".join(a.get("name", "") for a in artists if a.get("name"))
            
            # Fallback to single artist field if available
            if not artist_name and v.get("artist"):
                artist_name = v.get("artist", {}).get("name", "")
            
            results.append(TidalVideoResult(
                video_id=v["id"],
                title=v.get("title", ""),
                artist=artist_name,
                duration_s=int(v.get("duration", 0)),
                quality=v.get("quality", "") or "",
                image_id=v.get("imageId"),
                image_path=v.get("imagePath"),
            ))
        
        log.debug("Raw Tidal search for %r returned %d video(s)", query, len(results))
        return results
        
    except Exception:
        log.exception("Raw Tidal search failed for query: %r", query)
        return []


def get_artist_videos(
    artist_id: int,
    api=None,
    limit: int = 50,
    offset: int = 0,
) -> list[TidalVideoResult]:
    """Return videos for a Tidal artist (for future artist-browse feature).

    Not used by the panel yet, but available for extension.
    """
    if api is None:
        from .auth import get_api
        api = get_api()
    if api is None:
        return []

    try:
        result = api.get_artist_videos(artist_id=artist_id, limit=limit, offset=offset)
        items = getattr(result, "items", []) or []
        return [_video_to_result(v) for v in items]
    except Exception:
        log.exception("get_artist_videos failed for artist_id=%s", artist_id)
        return []

