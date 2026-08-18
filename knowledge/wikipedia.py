from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser
import json
import re
import ssl
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import certifi

API_HOST = "api.wikimedia.org"
ARTICLE_HOST = "en.wikipedia.org"
API_ROOT = f"https://{API_HOST}/core/v1/wikipedia/en"
USER_AGENT = "Elise/1.1 (https://github.com/JustARandomNJ/elise-local-assistant) Python-urllib/3.14"
MAX_RESPONSE_BYTES = 8_000_000
REJECTED_NAMESPACES = {"special","talk","user","user talk","wikipedia","wikipedia talk","file","file talk","mediawiki","mediawiki talk","template","template talk","help","help talk","category","category talk","portal","portal talk","draft","draft talk","timedtext","module","media"}


@dataclass(frozen=True)
class WikipediaPage:
    title: str
    revision_id: str | None
    html: str
    source_url: str
    retrieved_at: str
    license_name: str
    license_url: str


class WikipediaClient:
    def __init__(self, delay_seconds: float = 1.0, max_response_bytes: int = MAX_RESPONSE_BYTES, retries: int = 3, opener: Callable[..., object] | None = None, sleeper: Callable[[float],None] = time.sleep) -> None:
        self.delay_seconds=max(0.0,delay_seconds); self.max_response_bytes=max_response_bytes; self.retries=max(0,retries)
        # Trust configuration and identity are application-owned. Neither is
        # accepted from article titles, model output, or other caller input.
        self.ssl_context=ssl.create_default_context(cafile=certifi.where())
        self.opener=opener or build_opener(HTTPSHandler(context=self.ssl_context),_WikimediaRedirectHandler()).open
        self.sleeper=sleeper; self._last_request=0.0
    def fetch_page(self, title: str) -> WikipediaPage:
        safe=validate_title(title); encoded=quote(safe.replace(" ","_"), safe="")
        html, headers=self._get(f"{API_ROOT}/page/{encoded}/html")
        revision=headers.get("etag","").strip('W/"') or headers.get("content-revision-id")
        from datetime import datetime, timezone
        return WikipediaPage(safe,revision,html.decode("utf-8","replace"),f"https://{ARTICLE_HOST}/wiki/{encoded}",datetime.now(timezone.utc).isoformat(),"Creative Commons Attribution-ShareAlike 4.0", "https://creativecommons.org/licenses/by-sa/4.0/")
    def search_titles(self, query: str, limit: int=20) -> list[str]:
        payload,_=self._get(f"{API_ROOT}/search/page?q={quote(query)}&limit={min(max(limit,1),100)}")
        return [validate_title(item["title"]) for item in json.loads(payload).get("pages",[]) if isinstance(item,dict) and item.get("title")]
    def fetch_vital_seed(self) -> str:
        # Fixed Wikimedia-maintained list: callers cannot replace this endpoint.
        encoded=quote("Wikipedia:Vital_articles/Level/4", safe="")
        payload,_=self._get(f"{API_ROOT}/page/{encoded}/html")
        return payload.decode("utf-8","replace")
    def _get(self,url: str) -> tuple[bytes,Message]:
        parsed=urlsplit(url)
        if parsed.scheme!="https" or parsed.hostname!=API_HOST or parsed.port is not None: raise ValueError("Untrusted Wikimedia endpoint")
        for attempt in range(self.retries+1):
            wait=self.delay_seconds-(time.monotonic()-self._last_request)
            if wait>0: self.sleeper(wait)
            request=Request(url,headers={"User-Agent":USER_AGENT,"Accept":"text/html, application/json","Accept-Encoding":"identity"})
            try:
                response=self.opener(request,timeout=30); self._last_request=time.monotonic()
                length=response.headers.get("Content-Length")
                if length and int(length)>self.max_response_bytes: raise ValueError("Wikimedia response exceeds configured size limit")
                data=response.read(self.max_response_bytes+1)
                if len(data)>self.max_response_bytes: raise ValueError("Wikimedia response exceeds configured size limit")
                return data,response.headers
            except HTTPError as error:
                self._last_request=time.monotonic()
                if error.code in {400,401,403,404}: raise
                if attempt>=self.retries or error.code not in {429,500,502,503,504}: raise
                retry=error.headers.get("Retry-After") if error.headers else None
                self.sleeper(min(float(retry) if retry and retry.isdigit() else 2**attempt,60.0))
            except URLError:
                self._last_request=time.monotonic()
                if attempt>=self.retries: raise
                self.sleeper(min(2**attempt,30))
        raise RuntimeError("unreachable")


class _WikimediaRedirectHandler(HTTPRedirectHandler):
    """Allow redirects only within the fixed Wikimedia API origin."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed=urlsplit(newurl)
        if parsed.scheme!="https" or parsed.hostname!=API_HOST or parsed.port is not None:
            raise HTTPError(newurl,code,"Wikimedia redirect left the trusted API origin",headers,fp)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def validate_title(title: str) -> str:
    value=" ".join(unquote(title).replace("_"," ").split()).strip()
    if not value or len(value)>300 or any(c in value for c in "#?\x00\r\n"): raise ValueError("Invalid Wikipedia article title")
    prefix=value.split(":",1)[0].casefold()
    if ":" in value and prefix in REJECTED_NAMESPACES: raise ValueError("Non-article Wikipedia namespace")
    return value


class SeedLinkParser(HTMLParser):
    def __init__(self) -> None: super().__init__(); self.titles=[]
    def handle_starttag(self,tag,attrs):
        if tag!="a": return
        href=dict(attrs).get("href") or ""
        title=title_from_seed_link(href)
        if title and title.casefold() not in {x.casefold() for x in self.titles}: self.titles.append(title)


def title_from_seed_link(href: str) -> str | None:
    if href.startswith("#"): return None
    parsed=urlsplit(href)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not parsed.path.startswith("/wiki/"): return None
    try: return validate_title(parsed.path[len("/wiki/"):])
    except ValueError: return None


def extract_seed_titles(html: str, limit: int|None=None) -> list[str]:
    parser=SeedLinkParser(); parser.feed(html)
    return parser.titles[:limit]
