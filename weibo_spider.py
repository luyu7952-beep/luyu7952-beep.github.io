#!/usr/bin/env python3
"""
Collect Weibo posts from a SPECIFIC USER or public search.
Optimized Version for Ken - Supports User-Specific Search
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import requests


DEFAULT_KEYWORDS = ["人工智能", "AI"]

MOBILE_SEARCH_API = "https://m.weibo.cn/api/container/getIndex"
MOBILE_EXTEND_API = "https://m.weibo.cn/statuses/extend"
MOBILE_POST_URL = "https://m.weibo.cn/detail/{mid}"
REQUEST_TIMEOUT = 10


@dataclass
class WeiboPost:
    keyword: str
    post_id: str
    mid: str
    link: str
    created_at: str
    author_id: str
    author_name: str
    followers_count: int | None
    reposts_count: int
    comments_count: int
    likes_count: int
    topics: str
    original_weibo: str
    image_urls: str
    video_urls: str
    source: str


def build_session(cookie: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E7%A7%91%E6%8A%80",
            "MWeibo-Pwa": "1",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    if cookie:
        session.headers["Cookie"] = cookie
        match = re.search(r"XSRF-TOKEN=([^;]+)", cookie)
        if match:
            session.headers["X-XSRF-TOKEN"] = match.group(1)
    return session


def clean_text(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", raw_html)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def extract_topics(raw_html: str | None) -> list[str]:
    if not raw_html:
        return []
    topics = re.findall(r"#([^#<>\n\r]{1,80})#", html.unescape(raw_html))
    return dedupe([topic.strip() for topic in topics if topic.strip()])


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def to_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return 0
    multipliers = {"万": 10000, "亿": 100000000}
    for unit, multiplier in multipliers.items():
        if text.endswith(unit):
            try:
                return int(float(text[:-1]) * multiplier)
            except ValueError:
                return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def get_nested(data: dict[str, Any], path: list[str], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def extract_images(mblog: dict[str, Any]) -> list[str]:
    images: list[str] = []
    for pic in mblog.get("pics") or []:
        if not isinstance(pic, dict):
            continue
        url = (
            get_nested(pic, ["large", "url"])
            or get_nested(pic, ["original", "url"])
            or pic.get("url")
            or pic.get("pid")
        )
        if isinstance(url, str):
            if url.startswith("//"):
                url = "https:" + url
            images.append(url)
    return dedupe(images)


def extract_videos(mblog: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    page_info = mblog.get("page_info") or {}
    media_info = page_info.get("media_info") or {}
    candidates = [
        media_info.get("stream_url_hd"),
        media_info.get("stream_url"),
        media_info.get("mp4_hd_url"),
        media_info.get("mp4_sd_url"),
        media_info.get("h5_url"),
        page_info.get("page_url"),
    ]
    for item in candidates:
        if isinstance(item, str) and item:
            if item.startswith("//"):
                item = "https:" + item
            urls.append(item)
    return dedupe(urls)


def fetch_long_text(session: requests.Session, mid: str) -> str:
    if not mid:
        return ""
    response = session.get(MOBILE_EXTEND_API, params={"id": mid}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    return str(get_nested(payload, ["data", "longTextContent"], "") or "")


def enrich_long_texts(
    session: requests.Session,
    mblog: dict[str, Any],
    sleep_seconds: float,
) -> None:
    targets = [mblog]
    retweeted = mblog.get("retweeted_status")
    if isinstance(retweeted, dict):
        targets.append(retweeted)

    for target in targets:
        if not target.get("isLongText"):
            continue
        mid = str(target.get("mid") or target.get("id") or "")
        try:
            long_text = fetch_long_text(session, mid)
        except (requests.RequestException, ValueError):
            continue
        if long_text:
            target["text"] = long_text
            time.sleep(sleep_seconds)


def normalize_post(keyword: str, mblog: dict[str, Any]) -> WeiboPost | None:
    post_id = str(mblog.get("id") or "")
    mid = str(mblog.get("mid") or post_id)
    if not post_id and not mid:
        return None

    user = mblog.get("user") or {}
    raw_text = mblog.get("text") or ""
    retweeted = mblog.get("retweeted_status") or {}
    original_raw_text = retweeted.get("text") or raw_text
    all_topics = extract_topics(raw_text) + extract_topics(retweeted.get("text"))
    image_urls = extract_images(mblog) + extract_images(retweeted)
    video_urls = extract_videos(mblog) + extract_videos(retweeted)

    return WeiboPost(
        keyword=keyword,
        post_id=post_id,
        mid=mid,
        link=MOBILE_POST_URL.format(mid=mid),
        created_at=str(mblog.get("created_at") or ""),
        author_id=str(user.get("id") or ""),
        author_name=str(user.get("screen_name") or ""),
        followers_count=to_int(user.get("followers_count")) if user else None,
        reposts_count=to_int(mblog.get("reposts_count")),
        comments_count=to_int(mblog.get("comments_count")),
        likes_count=to_int(mblog.get("attitudes_count")),
        topics="; ".join(dedupe(all_topics)),
        original_weibo=clean_text(original_raw_text),
        image_urls="; ".join(dedupe(image_urls)),
        video_urls="; ".join(dedupe(video_urls)),
        source="m.weibo.cn",
    )


def parse_cards(
    session: requests.Session,
    keyword: str,
    payload: dict[str, Any],
    sleep_seconds: float,
) -> list[WeiboPost]:
    cards = get_nested(payload, ["data", "cards"], [])
    posts: list[WeiboPost] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        candidate_mblogs: list[dict[str, Any]] = []
        if isinstance(card.get("mblog"), dict):
            candidate_mblogs.append(card["mblog"])
        for group_item in card.get("card_group") or []:
            if isinstance(group_item, dict) and isinstance(group_item.get("mblog"), dict):
                candidate_mblogs.append(group_item["mblog"])
        for mblog in candidate_mblogs:
            enrich_long_texts(session, mblog, sleep_seconds)
            post = normalize_post(keyword, mblog)
            if post:
                posts.append(post)
    return posts


def fetch_keyword(
    session: requests.Session,
    keyword: str,
    pages: int,
    sleep_seconds: float,
    uid: str | None = None,
) -> list[WeiboPost]:
    if uid:
        containerid = f"230413{uid}_-_WEIBO_SECOND_PROFILE_WEIBO_ORI"
        params = {
            "containerid": containerid,
            "page_type": "03",
            "page": 1,
            "q": keyword,
        }
    else:
        containerid = f"100103type=1&q={quote(keyword)}"
        params = {
            "containerid": containerid,
            "page_type": "searchall",
            "page": 1,
        }
        
    collected: list[WeiboPost] = []
    for page in range(1, pages + 1):
        params["page"] = page
        url = f"{MOBILE_SEARCH_API}?{urlencode(params)}"
        
        print(f"  正在下载第 {page}/{pages} 页...", end="", flush=True)
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        posts = parse_cards(session, keyword, payload, sleep_seconds)
        print(f" 成功解析出 {len(posts)} 条微博")
        if not posts:
            break
        collected.extend(posts)
        time.sleep(sleep_seconds)
    return collected


def collect(
    keywords: list[str],
    pages: int,
    sleep_seconds: float,
    cookie: str | None,
    uid: str | None = None,
) -> list[WeiboPost]:
    session = build_session(cookie)
    rows: list[WeiboPost] = []
    seen: set[str] = set()
    for keyword in keywords:
        if uid:
            print(f"\n🎯 正在目标账户【UID:{uid}】中精准搜索关键词: 【{keyword}】")
        else:
            print(f"\n🚀 开始全局采集关键词: 【{keyword}】")
            
        try:
            posts = fetch_keyword(session, keyword, pages, sleep_seconds, uid)
        except Exception as exc:
            print(f"\n[WARN] keyword={keyword} 发生错误: {exc}")
            continue

        for post in posts:
            key = post.mid or post.post_id
            if key not in seen:
                seen.add(key)
                rows.append(post)
    return rows


def write_csv(rows: list[WeiboPost], path: str) -> None:
    fieldnames = list(WeiboPost.__dataclass_fields__.keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_json(rows: list[WeiboPost], path: str) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump([asdict(row) for row in rows], file, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="爬取微博科技类新闻并导出 CSV")
    parser.add_argument("-k", "--keywords", nargs="*", default=None)
    parser.add_argument("-p", "--pages", type=int, default=3, help="每个关键词采集页数")
    parser.add_argument("-s", "--sleep", type=float, default=1.5, help="请求间隔秒数")
    parser.add_argument("--cookie", default=None, help="从浏览器复制的微博 Cookie")
    # 新增 --uid 参数
    parser.add_argument("--uid", default=None, help="可选：指定搜索的微博用户UID")
    parser.add_argument(
        "--csv",
        default=f"weibo_user_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        help="CSV 输出文件名",
    )
    parser.add_argument("--json", default=None, help="可选：JSON 输出文件名")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    keywords = args.keywords
    if not keywords:
        user_input = input("请输入你想搜索的关键词（多个词用空格隔开）：").strip()
        keywords = [k.strip() for k in user_input.split() if k.strip()] if user_input else DEFAULT_KEYWORDS

    rows = collect(keywords, args.pages, args.sleep, args.cookie, args.uid)
    
    if not rows:
        print("\n❌ 未采集到任何有效数据，请检查该用户是否发过包含此关键词的微博，或 Cookie 是否过期。")
        return
        
    write_csv(rows, args.csv)
    if args.json:
        write_json(rows, args.json)
    print(f"\n✨ 大功告成！在该账户中共精准筛选出 {len(rows)} 条数据。")
    print(f"📊 CSV 文件已保存至当前目录: {args.csv}")
    if args.json:
        print(f"JSON 文件已保存至当前目录: {args.json}")


if __name__ == "__main__":
    main()
