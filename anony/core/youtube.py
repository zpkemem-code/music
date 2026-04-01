import os
import re
import yt_dlp
import random
import asyncio
import aiohttp
from pathlib import Path
from urllib.parse import urlparse
from youtubesearchpython.future import Playlist, VideosSearch

from anony import logger, config
from anony.helpers import Track, utils

class YouTube:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.cookies = []
        self.checked = False
        self.cookie_dir = "anony/cookies"
        self.warned = False
        self.api_url = str(getattr(config, "API_URL", "")).rstrip("/")
        self.api_timeout = 60
        self.api_retries = 3
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )
        self.iregex = re.compile(
            r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)"
            r"(?!/(watch\?v=[A-Za-z0-9_-]{11}|shorts/[A-Za-z0-9_-]{11}"
            r"|playlist\?list=PL[A-Za-z0-9_-]+|[A-Za-z0-9_-]{11}))\S*"
        )

    def get_cookies(self):
        if not self.checked:
            if os.path.isdir(self.cookie_dir):
                for file in os.listdir(self.cookie_dir):
                    if file.endswith(".txt"):
                        self.cookies.append(f"{self.cookie_dir}/{file}")
            self.checked = True
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; downloads might fail.")
            return None
        return random.choice(self.cookies)

    async def save_cookies(self, urls: list[str]) -> None:
        logger.info("Saving cookies from urls...")
        async with aiohttp.ClientSession() as session:
            for url in urls:
                name = url.split("/")[-1]
                link = "https://batbin.me/raw/" + name
                async with session.get(link) as resp:
                    resp.raise_for_status()
                    with open(f"{self.cookie_dir}/{name}.txt", "wb") as fw:
                        fw.write(await resp.read())
        logger.info(f"Cookies saved in {self.cookie_dir}.")

    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))

    def invalid(self, url: str) -> bool:
        return bool(re.match(self.iregex, url))

    async def search(self, query: str, m_id: int, video: bool = False) -> Track | None:
        try:
            _search = VideosSearch(query, limit=1)
            results = await _search.next()
        except Exception:
            return None
        if results and results.get("result"):
            data = results["result"][0]
            return Track(
                id=data.get("id"),
                channel_name=data.get("channel", {}).get("name"),
                duration=data.get("duration"),
                duration_sec=utils.to_seconds(data.get("duration")),
                message_id=m_id,
                title=(data.get("title") or "")[:25],
                thumbnail=(data.get("thumbnails", [{}])[-1].get("url") or "").split("?")[0],
                url=data.get("link"),
                view_count=data.get("viewCount", {}).get("short"),
                video=video,
            )
        return None
    async def playlist(self, limit: int, user: str, url: str, video: bool) -> list[Track | None]:
        tracks = []
        try:
            plist = await Playlist.get(url)
            videos = plist.get("videos", []) or []
            while len(videos) < limit and plist.get("hasMoreVideos"):
                plist = await Playlist.getNextVideos(plist)
                if not plist:
                    break
                videos.extend(plist.get("videos", []) or [])
            for data in videos[:limit]:
                thumbs = data.get("thumbnails") or [{}]
                link = data.get("link") or f"{self.base}{data.get('id')}"
                track = Track(
                    id=data.get("id"),
                    channel_name=data.get("channel", {}).get("name", ""),
                    duration=data.get("duration"),
                    duration_sec=utils.to_seconds(data.get("duration")),
                    title=(data.get("title") or "")[:25],
                    thumbnail=(thumbs[-1].get("url") or "").split("?")[0],
                    url=link.split("&list=")[0],
                    user=user,
                    view_count="",
                    video=video,
                )
                tracks.append(track)
        except Exception:
            pass
        return tracks

    async def _api_download(self, video_id: str, video: bool = False) -> str | None:
        if not self.api_url:
            return None
        endpoints = []
        if video:
            endpoints = [
                f"{self.api_url}/download?id={video_id}&format=1080",
                f"{self.api_url}/download?url={self.base}{video_id}&format=1080",
            ]
        else:
            endpoints = [
                f"{self.api_url}/mp3?id={video_id}",
                f"{self.api_url}/download?id={video_id}&format=m4a",
                f"{self.api_url}/download?url={self.base}{video_id}&format=m4a",
            ]
        timeout = aiohttp.ClientTimeout(total=self.api_timeout)
        for endpoint in endpoints:
            for _ in range(self.api_retries):
                try:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(endpoint) as resp:
                            resp.raise_for_status()
                            data = await resp.json()

                        link = (
                            data.get("downloadUrl")
                            or data.get("download_url")
                            or data.get("url")
                        )
                        if not link:
                            continue
                        ext = Path(urlparse(link).path).suffix
                        if not ext:
                            ext = ".mp4" if video else ".m4a"
                        filename = f"downloads/{video_id}{ext}"
                        if Path(filename).exists():
                            return filename
                        async with session.get(link) as dl:
                            dl.raise_for_status()
                            with open(filename, "wb") as fw:
                                async for chunk in dl.content.iter_chunked(1024 * 1024):
                                    fw.write(chunk)
                        if Path(filename).exists():
                            return filename
                except Exception as ex:
                    logger.warning("API download failed: %s", ex)
        return None

    async def download(self, video_id: str, video: bool = False) -> str | None:
        for ext in ("mp4", "webm", "mp3", "m4a"):
            cached = f"downloads/{video_id}.{ext}"
            if Path(cached).exists():
                return cached
        api_file = await self._api_download(video_id, video)
        if api_file:
            return api_file
        url = self.base + video_id
        ext = "mp4" if video else "webm"
        filename = f"downloads/{video_id}.{ext}"
        cookie = self.get_cookies()
        base_opts = {
            "outtmpl": "downloads/%(id)s.%(ext)s",
            "quiet": True,
            "noplaylist": True,
            "geo_bypass": True,
            "no_warnings": True,
            "overwrites": False,
            "nocheckcertificate": True,
            "js_runtimes": {"deno": {}},
            "remote_components": ["ejs:github"],
        }

        if cookie:
            base_opts["cookiefile"] = cookie
        if video:
            ydl_opts = {
                **base_opts,
                "format": "(bestvideo[height<=?1080][ext=mp4])+(bestaudio[ext=m4a]/bestaudio)/best[ext=mp4]/best",
                "merge_output_format": "mp4",
            }
        else:
            ydl_opts = {
                **base_opts,
                "format": "bestaudio[ext=webm][acodec=opus]/bestaudio/best",
            }

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([url])
                except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError):
                    return None
                except Exception as ex:
                    logger.warning("Download failed: %s", ex)
                    return None
            for x in ("webm", "mp3", "m4a", "mp4"):
                file = f"downloads/{video_id}.{x}"
                if Path(file).exists():
                    return file
            return filename if Path(filename).exists() else None
        return await asyncio.to_thread(_download)
