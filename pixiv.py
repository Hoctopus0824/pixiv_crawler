#!/usr/bin/env python3

import os
import sys
import time
import requests
from io import BytesIO
from PIL import Image
from gppt import GetPixivToken
from pixivpy3 import AppPixivAPI

TOKEN_FILE = "token.txt"

def get_refresh_token(headless=True, username=None, password=None):
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            tok = f.read().strip()
            if tok:
                return tok

    g = GetPixivToken(headless=headless, username=username, password=password)
    res = g.login()
    rt = res["refresh_token"]
    with open(TOKEN_FILE, "w") as f:
        f.write(rt)
    return rt

def crawl_by_tag(tag, save_dir="downloads", max_items=50, headless=True, username=None, password=None, exclude_tags=None):
    if exclude_tags is None:
        exclude_tags = []

    rt = get_refresh_token(headless, username, password)
    api = AppPixivAPI()
    api.auth(refresh_token=rt)

    os.makedirs(save_dir, exist_ok=True)

    target_count = max_items * 2
    collected = []

    # 검색 페이지네이션 기본 (최대 60개씩 가져오므로 필요한 만큼 반복)
    next_qs = None
    while len(collected) < target_count:
        if next_qs:
            json_res = api.search_illust(
                tag,
                search_target="partial_match_for_tags",
                sort="date_desc",
                **next_qs
            )
        else:
            json_res = api.search_illust(
                tag,
                search_target="partial_match_for_tags",
                sort="date_desc"
            )
        illusts = json_res.illusts

        if not illusts:
            break

        for illust in illusts:
            if len(collected) >= target_count:
                break

            illust_tags = [t.name for t in illust.tags]
            if any(ex_tag in illust_tags for ex_tag in exclude_tags):
                continue
            collected.append(illust)

        # 다음 페이지 쿼리 설정
        if hasattr(json_res, 'next_url') and json_res.next_url:
            # next_url 쿼리만 추출
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(json_res.next_url)
            next_qs = {k: v[0] for k,v in parse_qs(parsed.query).items()}
        else:
            break

    # 좋아요 수로 내림차순 정렬
    collected.sort(key=lambda x: x.total_bookmarks, reverse=True)

    headers = {
        "Referer": "https://www.pixiv.net",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/115.0.0.0 Safari/537.36"
        )
    }

    count = 0
    for illust in collected[:max_items]:
        # 좋아요 수 제한 (예시: 0 이상)
        if illust.total_bookmarks < 0:
            continue

        url = getattr(getattr(illust, "meta_single_page", None), "original_image_url", None)
        if not url:
            url = illust.image_urls.large

        print(f"[{count+1}] Downloading and converting to PNG: {url}")

        resp = requests.get(url, headers=headers)
        if resp.status_code == 403:
            print(f"⚠️ 403 Forbidden error for {url}, skipping...")
            continue

        img = Image.open(BytesIO(resp.content))

        path = os.path.join(save_dir, f"{illust.id}.png")
        img.save(path, format="PNG")

        count += 1
        time.sleep(0.1)

    print(f"✅ Completed: {count} PNG images saved to '{save_dir}'.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pixiv_crawler.py <해시태그> [max_count]")
        sys.exit(1)
    tag = sys.argv[1]
    maxn = int(sys.argv[2]) if len(sys.argv) >= 3 else 30

    exclude_tags = ['R-18', 'AI', 'ai_generated', 'AI 그림', 'aiart', 'ai_art', '人工知能']

    crawl_by_tag(
        tag,
        save_dir=tag + "_imgs",
        max_items=maxn,
        headless=True,
        username=os.getenv("PIXIV_ID"),
        password=os.getenv("PIXIV_PW"),
        exclude_tags=exclude_tags
    )
