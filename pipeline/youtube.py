"""YouTube Data API v3 upload with a fixed daily publish time.

The render can finish at any hour; the video still goes live at the same
wall-clock minute every day because it is uploaded private with a `publishAt`
timestamp and YouTube flips it public itself.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .config import Config, ConfigError, parse_hhmm
from .models import RenderResult

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"

# YouTube caps these; going over is a hard 400.
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 5000
MAX_TAG_BLOCK_CHARS = 460

# Don't schedule a publish that is about to happen — YouTube needs a moment.
MIN_LEAD_MINUTES = 15
RETRIABLE_STATUS = {500, 502, 503, 504}


class UploadError(RuntimeError):
    """Raised when a video cannot be uploaded."""


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def next_publish_time(
    local_time: str,
    tz: ZoneInfo,
    *,
    now: datetime | None = None,
    min_lead_minutes: int = MIN_LEAD_MINUTES,
) -> datetime:
    """The next occurrence of `local_time` in `tz`, as an aware UTC datetime.

    Today's slot is used when there is still enough lead time; otherwise the
    slot rolls to tomorrow, so a slow render never publishes at the wrong hour.
    """
    hour, minute = parse_hhmm(local_time, "publish time")
    now = (now or datetime.now(timezone.utc)).astimezone(tz)

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate < now + timedelta(minutes=min_lead_minutes):
        candidate += timedelta(days=1)
        # Re-apply the wall-clock time so a DST shift does not drag it an hour off.
        candidate = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate.astimezone(timezone.utc)


def to_rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def build_client(config: Config):
    """Build an authenticated YouTube client from the refresh-token secrets."""
    secrets = config.secrets
    credentials = Credentials(
        token=None,
        refresh_token=secrets.require("youtube_refresh_token", "YOUTUBE_REFRESH_TOKEN"),
        client_id=secrets.require("youtube_client_id", "YOUTUBE_CLIENT_ID"),
        client_secret=secrets.require("youtube_client_secret", "YOUTUBE_CLIENT_SECRET"),
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    try:
        credentials.refresh(Request())
    except Exception as exc:
        raise UploadError(
            f"Could not refresh the YouTube token: {exc}\n"
            "The usual cause is an OAuth consent screen still in 'Testing' — those refresh "
            "tokens expire after 7 days. Publish the app, then mint a new token with "
            "`python -m pipeline.cli auth`."
        ) from exc
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


# ---------------------------------------------------------------------------
# Metadata hygiene
# ---------------------------------------------------------------------------


def clamp_metadata(title: str, description: str, tags: list[str]) -> tuple[str, str, list[str]]:
    """Trim metadata to what the API will accept.

    Titles cannot contain `<` or `>` at all, and the tag list is capped by
    total characters rather than by count.
    """
    title = title.replace("<", "").replace(">", "").strip()
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rsplit(" ", 1)[0] + "…"

    description = description.replace("<", "").replace(">", "").strip()[:MAX_DESCRIPTION_CHARS]

    kept: list[str] = []
    budget = MAX_TAG_BLOCK_CHARS
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        # Tags with spaces are quoted by the API, which costs two extra chars.
        cost = len(tag) + (2 if " " in tag else 0) + 1
        if cost > budget:
            break
        kept.append(tag)
        budget -= cost
    return title or "Untitled", description, kept


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def upload_video(
    youtube,
    result: RenderResult,
    *,
    publish_at: datetime,
    category_id: str,
    made_for_kids: bool,
    default_tags: list[str],
    language: str = "en",
) -> str:
    """Upload one rendered cut, scheduled to go public at `publish_at`."""
    title, description, tags = clamp_metadata(
        result.script.title,
        result.script.description,
        list(dict.fromkeys([*result.script.tags, *default_tags])),
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(category_id),
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": {
            # publishAt is only honoured while the video is private.
            "privacyStatus": "private",
            "publishAt": to_rfc3339(publish_at),
            "selfDeclaredMadeForKids": bool(made_for_kids),
            "license": "youtube",
            "embeddable": True,
        },
    }

    media = MediaFileUpload(str(result.video_path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    log.info("Uploading %s (%.1f MB), publishing at %s",
             result.video_path.name, result.video_path.stat().st_size / 1e6, to_rfc3339(publish_at))

    response = _execute_resumable(request, label=result.kind)
    video_id = response.get("id")
    if not video_id:
        raise UploadError(f"Upload succeeded but returned no video id: {response}")
    log.info("Uploaded %s -> https://youtu.be/%s", result.kind, video_id)
    return video_id


def _execute_resumable(request, *, label: str, max_attempts: int = 6) -> dict:
    """Drive a resumable upload, retrying the transient failures."""
    response = None
    attempt = 0
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as exc:
            if exc.resp.status in RETRIABLE_STATUS and attempt < max_attempts:
                attempt += 1
                delay = min(60, 2**attempt) + random.random()
                log.warning("%s upload got HTTP %s; retry %d in %.1fs", label, exc.resp.status, attempt, delay)
                time.sleep(delay)
                continue
            raise UploadError(f"{label} upload failed: HTTP {exc.resp.status} {exc}") from exc
        except (ConnectionError, TimeoutError, OSError) as exc:
            if attempt >= max_attempts:
                raise UploadError(f"{label} upload failed after {attempt} retries: {exc}") from exc
            attempt += 1
            delay = min(60, 2**attempt) + random.random()
            log.warning("%s upload network error (%s); retry %d in %.1fs", label, exc, attempt, delay)
            time.sleep(delay)
    return response


def set_thumbnail(youtube, video_id: str, thumbnail: Path) -> bool:
    """Attach a custom thumbnail. Needs a verified channel; failure is not fatal."""
    try:
        youtube.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg")
        ).execute()
        log.info("Thumbnail set for %s", video_id)
        return True
    except HttpError as exc:
        log.warning(
            "Could not set the thumbnail for %s (%s). Custom thumbnails need a verified channel.",
            video_id, exc,
        )
        return False


def publish_result(config: Config, youtube, result: RenderResult) -> str:
    """Upload one cut and attach its thumbnail."""
    key = "publish.shorts_publish_at_local" if result.kind == "shorts" else "publish.publish_at_local"
    local_time = str(config.get(key) or config.require("publish.publish_at_local"))

    publish_at = next_publish_time(local_time, config.timezone)
    video_id = upload_video(
        youtube,
        result,
        publish_at=publish_at,
        category_id=str(config.get("publish.category_id", "27")),
        made_for_kids=bool(config.get("publish.made_for_kids", False)),
        default_tags=list(config.get("publish.default_tags", [])),
        language=str(config.get("channel.language", "en-US")).split("-")[0],
    )
    if result.thumbnail_path and result.thumbnail_path.exists():
        set_thumbnail(youtube, video_id, result.thumbnail_path)
    return video_id


# ---------------------------------------------------------------------------
# One-time local authorisation
# ---------------------------------------------------------------------------


def run_auth_flow(client_secret_file: Path) -> dict[str, str]:
    """Open a browser once and return the credentials to store as CI secrets."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secret_file.exists():
        raise ConfigError(f"OAuth client secret file not found: {client_secret_file}")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
    # access_type=offline + prompt=consent is what actually returns a refresh token.
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not credentials.refresh_token:
        raise ConfigError(
            "Google returned no refresh token. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run this again."
        )
    return {
        "YOUTUBE_CLIENT_ID": credentials.client_id,
        "YOUTUBE_CLIENT_SECRET": credentials.client_secret,
        "YOUTUBE_REFRESH_TOKEN": credentials.refresh_token,
    }
