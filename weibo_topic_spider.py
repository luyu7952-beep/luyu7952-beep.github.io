#!/usr/bin/env python3
"""
Collect metrics for specified Weibo topic words.

The script focuses on topic-level dimensions:
- read count
- discussion count
- interaction count
- original post count
- hosts/moderators
- current/highest hot-search rank seen during this run

Some Weibo topic fields are not always exposed by public mobile pages. When a
topic page does not expose official totals, the script calculates interaction
and original counts from sampled posts under the topic.
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
from typing import Any, Iterable
from urllib.parse import quote, urlencode

import requests


MOBILE_CONTAINER_API = "https://m.weibo.cn/api/container/getIndex"
MOBILE_TOPIC_SEARCH_API = "https://m.weibo.cn/api/container/getIndex"
MOBILE_TOPIC_SEARCH_PAGE = "https://s.weibo.com/weibo"
HOT_SEARCH_API = "https://weibo.com/ajax/side/hotSearch"
HOT_SEARCH_PAGE = "https://s.weibo.com/top/summary"
REQUEST_TIMEOUT = 12


@dataclass
class TopicMetrics:
    topic: str
    topic_link: str
    containerid: str
    read_count: int | None
    discussion_count: int | None
    interaction_count: int | None
    original_count: int | None
    hosts: str
    current_hotsearch_rank: int | None
    highest_hotsearch_rank: int | None
    sampled_posts: int
    raw_stat_text: str
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
            "Referer": "https://s.weibo.com/top/summary",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    if cookie:
        session.headers["Cookie"] = cookie
        match = re.search(r"XSRF-TOKEN=([^;]+)", cookie)
        if match:
            session.headers["X-XSRF-TOKEN"] = match.group(1)
    return session


def clean_topic(topic: str) -> str:
    return topic.strip().strip("#").strip()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        item = clean_text(item)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def parse_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = clean_text(value)
    if not text or text in {"-", "--"}:
        return None
    text = text.replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([万亿]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    if unit == "万":
        number *= 10000
    elif unit == "亿":
        number *= 100000000
    return int(number)


def walk_values(data: Any) -> Iterable[Any]:
    if isinstance(data, dict):
        for value in data.values():
            yield value
            yield from walk_values(value)
    elif isinstance(data, list):
        for item in data:
            yield item
            yield from walk_values(item)


def all_text(data: Any) -> str:
    parts: list[str] = []
    for value in walk_values(data):
        if isinstance(value, (str, int, float)):
            parts.append(clean_text(value))
    return " ".join(part for part in parts if part)


def get_nested(data: dict[str, Any], path: list[str], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def find_first_count(text: str, labels: list[str]) -> int | None:
    joined = "|".join(re.escape(label) for label in labels)
    patterns = [
        rf"(?:{joined})\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?\s*[万亿]?)",
        rf"([0-9]+(?:\.[0-9]+)?\s*[万亿]?)\s*(?:{joined})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return parse_count(match.group(1))
    return None


def find_hosts(text: str) -> list[str]:
    candidates: list[str] = []
    for pattern in [
        r"(?:主持人|主持|版主|管理员)\s*[:：]?\s*(@?[\u4e00-\u9fa5A-Za-z0-9_\-·]{2,30})",
        r"@([\u4e00-\u9fa5A-Za-z0-9_\-·]{2,30})",
    ]:
        candidates.extend(re.findall(pattern, text))
    return dedupe(candidate if candidate.startswith("@") else f"@{candidate}" for candidate in candidates)


def find_containerids(data: Any, topic: str) -> list[str]:
    ids: list[str] = []
    topic = clean_topic(topic)
    for value in walk_values(data):
        if not isinstance(value, str):
            continue
        for match in re.findall(r"containerid=([A-Za-z0-9_\-=]+)", value):
            ids.append(match)
        if "100808" in value or "231522" in value:
            ids.extend(re.findall(r"\b((?:100808|231522)[A-Za-z0-9_\-=]*)", value))

    text = all_text(data)
    if topic and topic in text:
        ids.extend(re.findall(r"\b((?:100808|231522)[A-Za-z0-9_\-=]*)", text))
    return dedupe(ids)


def fetch_json(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_text(session: requests.Session, url: str, params: dict[str, Any]) -> str:
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def discover_topic_container(session: requests.Session, topic: str) -> tuple[str, list[dict[str, Any]]]:
    topic = clean_topic(topic)
    payloads: list[dict[str, Any]] = []
    candidates: list[str] = []

    search_containerids = [
        f"100103type=38&q={quote(topic)}",
        f"100103type=1&q={quote('#' + topic + '#')}",
        f"231522type=1&t=10&q={quote('#' + topic + '#')}",
    ]
    for containerid in search_containerids:
        try:
            payload = fetch_json(
                session,
                MOBILE_TOPIC_SEARCH_API,
                {"containerid": containerid, "page_type": "searchall", "page": 1},
            )
        except (requests.RequestException, ValueError):
            continue
        payloads.append(payload)
        candidates.extend(find_containerids(payload, topic))

    for candidate in candidates:
        if candidate.startswith("100808") or candidate.startswith("231522"):
            return candidate, payloads
    return "", payloads


def parse_topic_metrics_from_payloads(payloads: list[Any]) -> tuple[int | None, int | None, int | None, int | None, list[str], str]:
    text = " ".join(all_text(payload) for payload in payloads)
    read_count = find_first_count(text, ["阅读", "阅读量", "浏览", "浏览量"])
    discussion_count = find_first_count(text, ["讨论", "讨论量"])
    interaction_count = find_first_count(text, ["互动", "互动量"])
    original_count = find_first_count(text, ["原创", "原创量"])
    hosts = find_hosts(text)
    return read_count, discussion_count, interaction_count, original_count, hosts, text[:2000]


def extract_mblogs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    cards = get_nested(payload, ["data", "cards"], [])
    for card in cards:
        if not isinstance(card, dict):
            continue
        if isinstance(card.get("mblog"), dict):
            posts.append(card["mblog"])
        for item in card.get("card_group") or []:
            if isinstance(item, dict) and isinstance(item.get("mblog"), dict):
                posts.append(item["mblog"])
    return posts


def sample_topic_posts(
    session: requests.Session,
    topic: str,
    containerid: str,
    pages: int,
    sleep_seconds: float,
) -> tuple[int, int, int]:
    sampled = 0
    interaction_total = 0
    original_total = 0
    containers = [containerid] if containerid else []
    containers.append(f"100103type=1&q={quote('#' + clean_topic(topic) + '#')}")

    seen_posts: set[str] = set()
    for container in dedupe(containers):
        for page in range(1, pages + 1):
            params = {"containerid": container, "page": page}
            if container.startswith("100103"):
                params["page_type"] = "searchall"
            try:
                payload = fetch_json(session, MOBILE_CONTAINER_API, params)
            except (requests.RequestException, ValueError):
                break
            posts = extract_mblogs(payload)
            if not posts:
                break
            for post in posts:
                post_id = str(post.get("mid") or post.get("id") or "")
                if not post_id or post_id in seen_posts:
                    continue
                seen_posts.add(post_id)
                sampled += 1
                interaction_total += sum(
                    parse_count(post.get(key)) or 0
                    for key in ["reposts_count", "comments_count", "attitudes_count"]
                )
                if not post.get("retweeted_status"):
                    original_total += 1
            time.sleep(sleep_seconds)
    return sampled, interaction_total, original_total


def get_hotsearch_ranks(session: requests.Session) -> dict[str, int]:
    ranks: dict[str, int] = {}
    try:
        payload = fetch_json(session, HOT_SEARCH_API, {})
        items = get_nested(payload, ["data", "realtime"], []) or []
        for index, item in enumerate(items, start=1):
            if isinstance(item, dict):
                word = clean_topic(str(item.get("word") or item.get("note") or ""))
                if word:
                    ranks[word] = index
    except (requests.RequestException, ValueError):
        pass

    try:
        page = fetch_text(session, HOT_SEARCH_PAGE, {"cate": "realtimehot"})
        for index, word in re.findall(r'<td class="td-01 ranktop">(\d+)</td>.*?<a[^>]+>(.*?)</a>', page, flags=re.S):
            cleaned = clean_topic(clean_text(word))
            if cleaned:
                ranks[cleaned] = min(ranks.get(cleaned, int(index)), int(index))
    except requests.RequestException:
        pass
    return ranks


def collect_topic(
    session: requests.Session,
    topic: str,
    post_pages: int,
    sleep_seconds: float,
    hot_ranks: dict[str, int],
) -> TopicMetrics:
    topic = clean_topic(topic)
    containerid, discovery_payloads = discover_topic_container(session, topic)
    detail_payloads: list[Any] = list(discovery_payloads)

    if containerid:
        try:
            detail_payloads.append(fetch_json(session, MOBILE_CONTAINER_API, {"containerid": containerid}))
        except (requests.RequestException, ValueError):
            pass

    read_count, discussion_count, official_interaction, official_original, hosts, raw_text = (
        parse_topic_metrics_from_payloads(detail_payloads)
    )
    sampled_posts, sampled_interaction, sampled_original = sample_topic_posts(
        session, topic, containerid, post_pages, sleep_seconds
    )

    interaction_count = official_interaction if official_interaction is not None else sampled_interaction
    original_count = official_original if official_original is not None else sampled_original
    hot_rank = hot_ranks.get(topic)

    params = urlencode({"q": f"#{topic}#"})
    topic_link = f"{MOBILE_TOPIC_SEARCH_PAGE}?{params}"
    if containerid:
        topic_link = f"https://m.weibo.cn/p/index?containerid={quote(containerid)}"

    return TopicMetrics(
        topic=topic,
        topic_link=topic_link,
        containerid=containerid,
        read_count=read_count,
        discussion_count=discussion_count,
        interaction_count=interaction_count,
        original_count=original_count,
        hosts="; ".join(hosts),
        current_hotsearch_rank=hot_rank,
        highest_hotsearch_rank=hot_rank,
        sampled_posts=sampled_posts,
        raw_stat_text=raw_text,
        source="m.weibo.cn / s.weibo.com",
    )


def collect_topics(
    topics: list[str],
    post_pages: int,
    sleep_seconds: float,
    cookie: str | None,
    monitor_rounds: int,
    monitor_interval: float,
) -> list[TopicMetrics]:
    session = build_session(cookie)
    cleaned_topics = dedupe(clean_topic(topic) for topic in topics if clean_topic(topic))
    highest_ranks: dict[str, int] = {}
    latest_ranks: dict[str, int] = {}

    for round_index in range(1, monitor_rounds + 1):
        ranks = get_hotsearch_ranks(session)
        latest_ranks = ranks
        for topic in cleaned_topics:
            rank = ranks.get(topic)
            if rank is not None:
                highest_ranks[topic] = min(highest_ranks.get(topic, rank), rank)
        if round_index < monitor_rounds:
            print(f"热搜监测 {round_index}/{monitor_rounds} 完成，等待 {monitor_interval} 秒...")
            time.sleep(monitor_interval)

    rows: list[TopicMetrics] = []
    for topic in cleaned_topics:
        print(f"正在采集话题：#{topic}#")
        row = collect_topic(session, topic, post_pages, sleep_seconds, latest_ranks)
        row.highest_hotsearch_rank = highest_ranks.get(topic, row.current_hotsearch_rank)
        rows.append(row)
        time.sleep(sleep_seconds)
    return rows


def write_csv(rows: list[TopicMetrics], path: str) -> None:
    fieldnames = list(TopicMetrics.__dataclass_fields__.keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_json(rows: list[TopicMetrics], path: str) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump([asdict(row) for row in rows], file, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集指定微博话题词的数据维度")
    parser.add_argument("topics", nargs="*", help="话题词，可带或不带 #")
    parser.add_argument("-p", "--post-pages", type=int, default=3, help="每个话题抽样微博页数")
    parser.add_argument("-s", "--sleep", type=float, default=1.5, help="请求间隔秒数")
    parser.add_argument("--cookie", default=None, help="可选：从浏览器复制的微博 Cookie")
    parser.add_argument("--monitor-rounds", type=int, default=1, help="热搜监测轮数，用于记录本次运行看到的最高位置")
    parser.add_argument("--monitor-interval", type=float, default=60, help="热搜监测间隔秒数")
    parser.add_argument(
        "--csv",
        default=f"weibo_topic_metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        help="CSV 输出文件名",
    )
    parser.add_argument("--json", default=None, help="可选：JSON 输出文件名")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    topics = args.topics
    if not topics:
        user_input = input("请输入要采集的话题词（多个话题用空格隔开）：").strip()
        topics = [item for item in user_input.split() if item.strip()]
    if not topics:
        print("没有输入话题词，已退出。")
        return

    rows = collect_topics(
        topics=topics,
        post_pages=args.post_pages,
        sleep_seconds=args.sleep,
        cookie=args.cookie,
        monitor_rounds=max(1, args.monitor_rounds),
        monitor_interval=max(1, args.monitor_interval),
    )
    write_csv(rows, args.csv)
    if args.json:
        write_json(rows, args.json)
    print(f"完成：共 {len(rows)} 个话题，CSV 已保存到 {args.csv}")
    if args.json:
        print(f"JSON 已保存到 {args.json}")


if __name__ == "__main__":
    main()
