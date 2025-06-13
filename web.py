from flask import Flask, request, render_template_string, redirect, url_for, send_file
import threading
import os
import time
import requests
from io import BytesIO
from PIL import Image
from gppt import GetPixivToken
from pixivpy3 import AppPixivAPI
from urllib.parse import urlparse, parse_qs
import zipfile

app = Flask(__name__)

TOKEN_FILE = "token.txt"
STATIC_FOLDER = os.path.join(app.root_path, "static")
DOWNLOAD_FOLDER = os.path.join(STATIC_FOLDER, "downloads")
status_messages = []

def add_status(msg):
    status_messages.append(msg)
    if len(status_messages) > 50:
        status_messages.pop(0)

def log_access(ip, tags):
    with open("access.log", "a", encoding="utf-8") as f:
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        tag_str = ", ".join(tags)
        f.write(f"{now} - {ip} - {tag_str}\n")

def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        ip = request.headers.get("X-Forwarded-For").split(",")[0].strip()
    else:
        ip = request.remote_addr
    return ip

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

def crawl_by_tag(tags, save_dir, max_items, exclude_tags, username, password):
    try:
        add_status(f"\n⭐️ 시작: 태그={tags}, 최대 다운로드={max_items}")
        rt = get_refresh_token(True, username, password)
        api = AppPixivAPI()
        api.auth(refresh_token=rt)

        os.makedirs(save_dir, exist_ok=True)

        target_count = max_items * 5
        collected = []
        next_qs = {}

        for tag in tags:
            while len(collected) < target_count:
                query = {
                    "word": tag,
                    "search_target": "partial_match_for_tags",
                    "sort": "date_desc",
                    **next_qs
                }
                json_res = api.search_illust(**query)

                illusts = json_res.illusts
                if not illusts:
                    break

                for illust in illusts:
                    if len(collected) >= target_count:
                        break
                    if illust.id in [i.id for i in collected]:
                        continue

                    illust_tags = [t.name for t in illust.tags]

                    if any(ex_tag in illust_tags for ex_tag in exclude_tags):
                        continue

                    collected.append(illust)

                if hasattr(json_res, 'next_url') and json_res.next_url:
                    parsed = urlparse(json_res.next_url)
                    next_qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                else:
                    break

        collected.sort(key=lambda x: x.total_bookmarks, reverse=True)
        headers = {"Referer": "https://www.pixiv.net", "User-Agent": "Mozilla/5.0"}

        count = 0
        for illust in collected[:max_items]:
            url = getattr(getattr(illust, "meta_single_page", None), "original_image_url", None) or illust.image_urls.large
            add_status(f"[{count+1}] 다운로드: {url}")

            try:
                resp = requests.get(url, headers=headers)
                img = Image.open(BytesIO(resp.content))
                path = os.path.join(save_dir, f"{illust.id}.png")
                img.save(path, format="PNG")
                count += 1
                time.sleep(0.1)
            except Exception as e:
                add_status(f"  ⚠️ 오류: {e}")

        add_status(f"✅ 완료: {count}개 저장됨 → {save_dir}")
    except Exception as e:
        add_status(f"❌ 에러 발생: {e}")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        user_ip = get_client_ip()
        all_tags_raw = request.form.get("tags", "")
        all_tags = [tag.strip() for tag in all_tags_raw.split(",") if tag.strip()]

        log_access(user_ip, all_tags)

        max_items = int(request.form.get("max_items", "30"))
        exclude_tags_raw = request.form.get("exclude_tags", "")
        exclude_tags = [t.strip() for t in exclude_tags_raw.split(",") if t.strip()]
        save_dir = os.path.join(DOWNLOAD_FOLDER, str(int(time.time())))

        username = request.form.get("username")
        password = request.form.get("password")

        threading.Thread(target=crawl_by_tag, args=(all_tags, save_dir, max_items, exclude_tags, username, password)).start()
        return redirect(url_for("status", folder=os.path.basename(save_dir)))

    return render_template_string("""
    <html>
        <head><title>Pixiv 크롤러</title></head>
        <body>
            <h2>Pixiv 일러스트 크롤러</h2>
            <form method="post">
                <label>검색/포함 태그 (콤마 구분):<br><input type="text" name="tags" size="60" required></label><br><br>
                <label>제외할 태그 (콤마 구분):<br><textarea name="exclude_tags" rows="2" cols="60">R-18, AI, ai_generated, AI 그림, aiart, ai_art, 人工知能</textarea></label><br><br>
                <label>최대 다운로드 개수: <input type="number" name="max_items" value="30" min="1"></label><br><br>
                <label>Pixiv 아이디: <input type="text" name="username" required></label><br>
                <label>Pixiv 비밀번호: <input type="password" name="password" required></label><br><br>
                <button type="submit">크롤링 시작</button>
            </form>
        </body>
    </html>
    """)

@app.route("/status")
def status():
    folder = request.args.get("folder")
    image_tags = ""
    target_folder = os.path.join(DOWNLOAD_FOLDER, folder) if folder else None
    if target_folder and os.path.isdir(target_folder):
        files = sorted([f for f in os.listdir(target_folder) if f.endswith(".png")])[:5]
        for f in files:
            img_path = f"/static/downloads/{folder}/{f}"
            image_tags += f'<img src="{img_path}" width="200" style="margin:5px">'
        # ZIP 다운로드 링크 추가
        zip_url = url_for("download_zip", folder=folder)
        download_link = f'<br><a href="{zip_url}">📥 ZIP 파일로 다운로드</a>'
    else:
        download_link = ""

    msgs = "<br>".join(status_messages[-30:])
    return f"""
    <html>
        <head><title>크롤러 상태</title><meta http-equiv="refresh" content="5"></head>
        <body>
            <h2>진행 상태</h2>
            <div style='white-space: pre-line; font-family: monospace;'>{msgs}</div>
            <hr>
            <h3>미리보기 (최대 5개)</h3>
            <div>{image_tags}</div>
            {download_link}
            <br><a href="/">뒤로</a>
        </body>
    </html>
    """

@app.route("/download/<folder>")
def download_zip(folder):
    target_folder = os.path.join(DOWNLOAD_FOLDER, folder)
    if not os.path.isdir(target_folder):
        return "존재하지 않는 폴더입니다.", 404

    # 메모리 내 ZIP 생성
    memory_file = BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for filename in os.listdir(target_folder):
            if filename.endswith(".png"):
                filepath = os.path.join(target_folder, filename)
                zipf.write(filepath, arcname=filename)
    memory_file.seek(0)

    return send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f"pixiv_download_{folder}.zip"
    )

if __name__ == "__main__":
    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
    app.run(debug=True, port=5000)
