"""高嶺のなでしこ公式サイトからスケジュールを取得し、メンバー別の一覧ファイルを作成する。

使い方:
    python fetch_schedule.py                       # 今日以降の予定
    python fetch_schedule.py --start 2026-01-01    # 期間指定

開始日より前の予定は既存の CSV から引き継ぎ、終わった予定には「済」を付ける。
カレンダーアプリ用の iCalendar（.ics）と、お知らせ記事から読み取った申込・締切の一覧も同時に出力する。
"""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE_URL = "https://takanenonadeshiko.jp"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
JST = timezone(timedelta(hours=9))

# (フルネーム, 姓, 名) — 公式サイト /members/ 掲載順
MEMBERS = [
    ("城月菜央", "城月", "菜央"),
    ("涼海すう", "涼海", "すう"),
    ("橋本桃呼", "橋本", "桃呼"),
    ("葉月紗蘭", "葉月", "紗蘭"),
    ("東山恵里沙", "東山", "恵里沙"),
    ("日向端ひな", "日向端", "ひな"),
    ("松本ももな", "松本", "ももな"),
    ("籾山ひめり", "籾山", "ひめり"),
]
GROUP = "グループ（全員）"

CATEGORY_LABELS = {
    "live": "ライブ／イベント",
    "media": "メディア",
    "birth": "誕生日",
    "others": "その他",
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (takaneko-fan/schedule)"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8")


def fetch_events(start, end):
    query = urllib.parse.urlencode(
        {"action": "eventorganiser-fullcal", "start": start.isoformat(), "end": end.isoformat()}
    )
    return json.loads(fetch(f"{AJAX_URL}?{query}"))


def html_to_lines(fragment):
    text = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")
    return [line.strip() for line in text.split("\n") if line.strip()]


def fetch_detail(url):
    """イベント詳細ページの「▼見出し」ごとの本文を dict で返す。"""
    page = fetch(url)
    m = re.search(r'<div class="entry-content">(.*?)<nav class="pagination-single', page, re.S)
    if not m:
        return {}
    body = re.sub(r'<div class="eventorganiser-event-meta">.*?<div style="clear:both"></div>\s*<hr>\s*</div>',
                  "", m.group(1), flags=re.S)
    sections, current = {"本文": []}, "本文"
    for line in html_to_lines(body):
        if line.startswith("▼"):
            head, _, rest = line[1:].partition(" ")
            current = head.strip()
            sections.setdefault(current, [])
            if rest.strip():
                sections[current].append(rest.strip())
        else:
            sections[current].append(line)
    return {k: v for k, v in sections.items() if v}


MEMBER_LINE_RE = r"^[＜<【\[]?(実施|出演|参加)メンバー"
PART_RE = re.compile(r"^(第?[一二三四五1-9１-９]部|[①-⑨])")
DATE_LINE_RE = re.compile(r"^\d{4}年\s*\d{1,2}月\s*\d{1,2}日\s*[(（][^)）]+[)）]\s*")


def find_part_times(lines):
    """「1部 12:00 START」「第一部[OPEN/START] 15:00/16:00」形式の時間を拾う。"""
    parts = [re.sub(r"\s+", " ", ln) for ln in lines
             if PART_RE.match(ln) and re.search(r"\d{1,2}[:：]\d{2}", ln)]
    if not parts:
        # 部の表記がなく「12:30 START（優先エリア入場12:10）」が並ぶ形式
        parts = [re.sub(r"\s+", " ", ln) for ln in lines
                 if re.match(r"^\d{1,2}[:：]\d{2}\s*START", ln, re.I)]
    if len(parts) > 3:
        parts = parts[:1] + ["…"] + parts[-1:]
    return " ／ ".join(parts)


def find_venue(detail):
    venue = detail.get("会場", []) + detail.get("場所", [])
    if venue:
        return " / ".join(venue)
    body = detail.get("本文", [])
    for i, line in enumerate(body[:-1]):
        if re.fullmatch(r"[【■◆]?\s*会場\s*[】：:]?", line):
            return body[i + 1]  # 「【会場】」→ 会場行
    for i, line in enumerate(body):
        if not DATE_LINE_RE.match(line):
            continue
        rest = DATE_LINE_RE.sub("", line).strip()
        if any(w in rest for w in ("発売", "販売", "受付", "まで")):
            continue  # 「2026年11月18日(水)発売」は会場ではない
        if rest and not re.search(r"\d{1,2}[:：]\d{2}", rest):
            return rest  # 「2026年12月24日(木) ヒューリックホール東京」
        if i > 0 and "会場" in body[i - 1] and i + 1 < len(body):
            return body[i + 1]  # 「【開催日時・会場】」→ 日付行 → 会場行
    return ""


def find_times(lines):
    text = " ".join(lines)
    times = {}
    for key in ("OPEN", "START", "END"):
        m = re.search(rf"{key}\s*[:：]?\s*(\d{{1,2}}[:：]\d{{2}})", text, re.I)
        if m:
            times[key] = m.group(1).replace("：", ":")
    if not times:
        m = re.search(r"(\d{1,2}[:：]\d{2})\s*[〜～~\-－]\s*(\d{1,2}[:：]\d{2})", text)
        if m:
            times["START"], times["END"] = (t.replace("：", ":") for t in m.groups())
    return times


def detect_members(title, detail):
    """タイトルの「✿姓」やフルネーム、出演メンバー欄から該当メンバーを判定する。"""
    found = []
    marker = title.split("✿", 1)[1] if "✿" in title else ""
    member_text = " ".join(
        " ".join(v) for k, v in detail.items() if "メンバー" in k
    ) + " ".join(
        ln for ln in detail.get("本文", []) if re.match(MEMBER_LINE_RE, ln)
    )
    for full, family, given in MEMBERS:
        if (full in title.replace(" ", "")
                or family in marker
                or family in member_text or given in member_text):
            found.append(full)
    return found


def build_rows(events, with_detail=True):
    rows = []
    for i, ev in enumerate(events):
        title = html.unescape(ev["title"]).strip()
        detail = {}
        if with_detail:
            try:
                detail = fetch_detail(ev["url"])
            except Exception as e:  # noqa: BLE001 — 1件の失敗で全体を止めない
                print(f"  ! 詳細取得失敗: {title} ({e})")
            time.sleep(0.3)
        members = detect_members(title, detail)
        times = find_times(detail.get("日時", []) + detail.get("時間", []))
        if not times:
            times = find_times([ln for ln in detail.get("本文", []) if "OPEN" in ln and "START" in ln][:1])
        # 全メンバーが対象なら個別扱いせずグループ扱いにする
        if len(members) == len(MEMBERS):
            members = []
        rows.append({
            "date": ev["start"][:10],
            "weekday": "月火水木金土日"[date.fromisoformat(ev["start"][:10]).weekday()],
            "open": times.get("OPEN", ""),
            "start": times.get("START", ""),
            "end": times.get("END", ""),
            "title": title,
            "category": " / ".join(CATEGORY_LABELS.get(c, c) for c in ev.get("category", [])),
            "members": "、".join(members) if members else GROUP,
            "time_note": "" if times else find_part_times(detail.get("本文", [])),
            "venue": find_venue(detail),
            "url": ev["url"],
            # 参加メモの材料（CSV には出力しない）
            "source_text": "\n".join(f"[{k}]\n" + "\n".join(v) for k, v in detail.items()),
        })
        print(f"[{i + 1}/{len(events)}] {rows[-1]['date']} {title} -> {rows[-1]['members']}")
    return rows


# ---- お知らせ記事（カレンダー未掲載の予定）からの抽出 ----

POST_DATE_RE = re.compile(
    r"^[■◆●・\s【]*(?:(\d{4})年\s*)?(\d{1,2})月\s*(\d{1,2})日\s*[(（]([^)）]{1,6})[)）]】?\s*"
)
ANY_DATE_RE = re.compile(r"\d{1,2}月\s*\d{1,2}日|\d{1,2}/\d{1,2}")
# 予定ではなく販売・受付・締切などの告知行
NON_EVENT_WORDS = ("発売", "販売", "受付", "締切", "締め切", "まで", "当落", "入金", "発表", "お届け",
                   "受け取り", "受取", "取り置き", "以降", "応募", "抽選", "予約", "公開", "解禁")


def is_non_event(text):
    # 「発売記念」はイベント名の一部なので販売告知とはみなさない
    return any(w in text.replace("発売記念", "") for w in NON_EVENT_WORDS)


def fetch_posts(since):
    posts, page = [], 1
    while True:
        query = urllib.parse.urlencode({
            "per_page": 100, "page": page, "after": f"{since.isoformat()}T00:00:00",
            "_fields": "date,title,link,content",
        })
        batch = json.loads(fetch(f"{BASE_URL}/wp-json/wp/v2/posts?{query}"))
        posts += batch
        if len(batch) < 100:
            return posts
        page += 1


def infer_date(year, month, day, published):
    """年の記載がない日付は、記事の公開日以降で最も近い年とみなす。"""
    if year:
        return date(int(year), int(month), int(day))
    y = published.year
    d = date(y, int(month), int(day))
    return d if d >= published - timedelta(days=31) else date(y + 1, int(month), int(day))


def clean_post_title(title):
    title = re.sub(r"^【[^】]*】", "", title)
    title = re.sub(r"\s*(\d+月)?スケジュール公開！?$|\s*(開催|詳細)?決定！?$", "", title)
    return re.sub(r"^✿|✿$|のお知らせ✿?$", "", title).strip() or title


def extract_post_items(post):
    """記事から (日付, タイトル, 付随行) の予定候補を取り出す。"""
    published = date.fromisoformat(post["date"][:10])
    title = html.unescape(re.sub(r"<[^>]+>", "", post["title"]["rendered"])).strip()
    lines = html_to_lines(post["content"]["rendered"])
    items = []

    # 1) タイトルが【M月D日(曜)】で始まる告知記事
    m = POST_DATE_RE.match(title)
    if title.startswith("【") and m and not is_non_event(title):
        items.append({
            "date": infer_date(*m.groups()[:3], published),
            "title": clean_post_title(title),
            "context": lines,
        })

    for i, line in enumerate(lines):
        m = POST_DATE_RE.match(line)
        if not m:
            continue
        rest = line[m.end():].strip()
        if len(ANY_DATE_RE.findall(line)) > 1 or is_non_event(line):
            continue
        # 付随行: 次の日付行までの数行
        context = []
        for nxt in lines[i + 1:i + 12]:
            if POST_DATE_RE.match(nxt):
                break
            context.append(nxt)
        d = infer_date(*m.groups()[:3], published)
        if rest.startswith(("に", "より", "から", "は", "の")):
            # 「2026年11月18日(水)にニューアルバムのリリースが決定」のような文章
            items.append({"date": d, "title": clean_post_title(title), "context": []})
        elif re.sub(r"[\d:：~〜～\-\s]", "", rest):
            # 2) 「10月11日(日)　ミニライブ&グループ特典会@イオンモール幕張新都心」形式
            items.append({"date": d, "title": rest, "context": [], "post_title": clean_post_title(title)})
        elif context and re.match(MEMBER_LINE_RE, context[0]):
            # 3) 日付行の直後に「実施メンバー：…」が続く形式（SPACE MAKE など）
            items.append({"date": d, "title": clean_post_title(title), "context": context})
    for it in items:
        it["url"] = post["link"]
        it["post_text"] = " ".join(lines)
    return items


def post_item_to_row(item):
    ctx = item["context"]
    detail = {"本文": ctx, "メンバー": [ln for ln in ctx if re.match(MEMBER_LINE_RE, ln)]}
    text = item["title"]
    members = detect_members(text, detail)
    if len(members) == len(MEMBERS):
        members = []
    times = find_times([ln for ln in ctx if "OPEN" in ln and "START" in ln][:1])
    venue = find_venue(detail)
    title = text
    at = re.split(r"[@＠]", text, maxsplit=1)
    if len(at) == 2 and not venue:
        venue = at[1].strip()
    if item.get("post_title") and item["post_title"] not in title:
        title = f"{item['post_title']}：{title}"
    return {
        "date": item["date"].isoformat(),
        "weekday": "月火水木金土日"[item["date"].weekday()],
        "open": times.get("OPEN", ""),
        "start": times.get("START", ""),
        "end": times.get("END", ""),
        "time_note": "" if times else find_part_times(ctx),
        "title": title,
        "category": "お知らせ記事",
        "members": "、".join(members) if members else GROUP,
        "venue": venue,
        "url": item["url"],
        "post_text": item["post_text"],
    }


# ---- 公式 X（@takanenofficial）の告知（公式サイト未掲載の予定の補完） ----

X_API_URL = "https://api.x.com/2"
X_USER_ID = "1518855666177568769"  # @takanenofficial（メンバー個人のアカウントは対象外）
# 前回読んだ最新の投稿 ID（同じ投稿を読み直して課金されないため）。
# Actions の checkout で消えないよう、リポジトリの外（実行ユーザーのホーム）に置く
X_STATE_PATH = os.environ.get("X_STATE_PATH") or os.path.expanduser("~/.local/state/takaneko-fan/x_state.json")
X_FIRST_DAYS = 7                   # 初回（x_state.json がないとき）にさかのぼる日数
X_MAX_PAGES = 3                    # 1 回の実行で読む上限（100 件 × ページ数、1 件 $0.005）
X_DATE_RE = re.compile(r"(?:(\d{4})[年/.])?(\d{1,2})(?:月\s*|/)(\d{1,2})日?\s*[(（]([^)）]{1,6})[)）]")
# 日付を書かずに「明日」「このあと」の予定を告知する投稿（タグ・語 → 投稿日からの日数）
X_RELATIVE_WORDS = {"#あしたのたかねこ": 1, "このあと": 0, "本日": 0}
X_MARK_RE = re.compile(r"^[📍⏰🔗🎫🛒🎤🎥🎧📱💬]")
X_NIGHT_HOUR = 5  # この時刻（日本時間）より前の「#あしたのたかねこ」は前日の夜の投稿とみなす
X_EVENTS_PATH = "x_events.json"  # 読み取った予定（投稿は一度しか読まないので保存する。本文は保存しない）
X_CATEGORY = "公式X"
X_NAME_MIN = 8  # 『』内の名前と公式の予定名がこの文字数以上一致すれば同じ予定（グループ名 7 文字だけでは一致させない）


def strip_emoji(text):
    """絵文字と、絵文字をつなぐ記号（ZWJ・異体字セレクタ）を外す。
    Python が知らない新しい絵文字は未定義（Cn）になるので、それも外す。"""
    return "".join(c for c in text if unicodedata.category(c) not in ("So", "Cn") and c not in "\u200d\ufe0f")


def fetch_x_posts(token, since_id=None, days=X_FIRST_DAYS):
    """公式 X の新しい投稿（リポスト・返信を除く）を古い順に返す。since_id がなければ days 日前から。"""
    params = {"max_results": 100, "exclude": "retweets,replies", "tweet.fields": "created_at,note_tweet"}
    if since_id:
        params["since_id"] = since_id
    else:
        start = datetime.now(timezone.utc) - timedelta(days=days)
        params["start_time"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    tweets, newest = [], since_id
    for _ in range(X_MAX_PAGES):
        req = urllib.request.Request(f"{X_API_URL}/users/{X_USER_ID}/tweets?{urllib.parse.urlencode(params)}",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as res:
            body = json.load(res)
        tweets += body.get("data", [])
        if "next_token" not in body["meta"]:
            break
        params["pagination_token"] = body["meta"]["next_token"]
    if tweets:
        newest = max((t["id"] for t in tweets), key=int)
    return sorted(tweets, key=lambda t: int(t["id"])), newest


def x_lines(tweet):
    text = tweet.get("note_tweet", {}).get("text") or tweet["text"]  # 長文の投稿は note_tweet に全文がある
    text = re.sub(r"https://t\.co/\w+", "", html.unescape(text))
    return [ln.strip() for ln in text.splitlines()]


def x_title(lines):
    """見出しの行をつないで予定名にする（グループ名のタグ・# は外す）。"""
    text = " ".join(ln for ln in lines if ln and not X_MARK_RE.match(ln))
    text = re.sub(r"#(高嶺のなでしこ|たかねこ)(?=\s|$)", "", strip_emoji(text)).replace("#", "")
    text = re.sub(r"\s+", " ", re.sub(r"([『「])\s+|\s+([』」])", r"\1\2", text)).strip()
    m = re.fullmatch(r"[『「]([^』」]+)[』」]", text)  # 全体が『』で囲まれていれば外す
    return m.group(1) if m else text


def x_headline(lines):
    """投稿の見出し: 『』「」で囲まれた名前、なければ飾りでない最初の行。"""
    m = re.search(r"[『「]\s*([^』」]+?)\s*[』」]", " ".join(lines))
    if m:
        return x_title([m.group(1)])
    for ln in lines:
        if ln.startswith(("＼", "／")):
            continue  # 「＼オフィシャル先行受付スタート／」のような呼びかけ
        if re.search(r"[\w぀-ヿ一-鿿]{2}", re.sub(r"#\S+", "", ln)):
            return x_title([ln])
    return ""


def x_details(lines):
    """📍会場・時間を拾う。"""
    venue = next((ln.lstrip("📍").strip() for ln in lines if ln.startswith("📍")), "")
    lines = [strip_emoji(ln).strip() for ln in lines if not X_MARK_RE.match(ln) or ln.startswith("⏰")]
    # 「1部」と時間が別の行に書かれていればつなぐ
    for i in range(len(lines) - 1, 0, -1):
        if (PART_RE.match(lines[i - 1]) and not re.search(r"\d[:：]\d", lines[i - 1])
                and re.match(r"\d{1,2}[:：]\d{2}", lines[i])):
            lines[i - 1:i + 1] = [f"{lines[i - 1]} {lines[i]}"]
    times = find_times([ln for ln in lines if "OPEN" in ln and "START" in ln][:1])
    note = "" if times else find_part_times(lines)
    if not times and not note:
        times = find_times(lines)
        m = None if times else re.search(r"(\d{1,2}[:：]\d{2})\s*[〜～~]", " ".join(lines))
        if m:
            times["START"] = m.group(1).replace("：", ":")  # 「19:00~」のように終了の書かれていない時間
    return venue, times, note


def extract_x_candidates(tweet):
    """投稿から予定候補を取り出す。sure=False は抽出に自信がないもの（実行ログに出すだけ）。"""
    posted = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00")).astimezone(JST)
    published = posted.date()
    lines = x_lines(tweet)
    headline = x_headline(lines)
    url = f"https://x.com/takanenofficial/status/{tweet['id']}"
    joined = " ".join(lines)
    # 公式の予定名との照合用に『』内の名前と、出演メンバー（本文にフルネームがあれば）を残す
    names = list(dict.fromkeys(x_title([n]) for n in re.findall(r"『\s*([^』]+?)\s*』", joined)))
    members = [full for full, _, _ in MEMBERS if full in joined.replace(" ", "")]
    common = {"url": url, "post_id": tweet["id"], "posted": posted.isoformat(timespec="minutes"),
              "names": names, "members": "、".join(members) if 0 < len(members) < len(MEMBERS) else GROUP}
    found, label = [], ""
    for i, line in enumerate(lines):
        m = X_DATE_RE.search(line)
        if not m:
            if line.startswith("🗓"):
                label = line  # 「🗓️発売日」のような見出し。続く日付行の意味を表す
            continue
        rest = line[m.end():].strip()
        if (len(X_DATE_RE.findall(line)) > 1 or is_non_event(line) or is_non_event(label)
                or rest.startswith("~") or re.search(r"[\w぀-ヿ一-鿿]", line[:m.start()].lstrip("🗓️"))):
            continue  # 期間（〜）、発売日・受付などの告知、「観覧申し込み(10/26(月)…)」のような文中の日付
        d = infer_date(*m.groups()[:3], published)
        if d < published:
            continue
        # 日付行の下、空行または次の日付行までが 1 件分
        block = []
        for nxt in lines[i + 1:]:
            if not nxt or X_DATE_RE.search(nxt):
                break
            block.append(nxt)
        only_time = re.fullmatch(r"[\d:：~〜～\-\s]*", rest)
        venue, times, note = x_details(block + ([rest] if only_time else []))
        # 日付行の直前の段落に『』があればそれを優先（お知らせと出演情報が 1 投稿に並ぶ場合）
        end = i
        while end > 0 and not lines[end - 1]:
            end -= 1
        start = max((k + 1 for k in range(end) if not lines[k]), default=0)
        near = re.search(r"『\s*([^』]+?)\s*』", " ".join(lines[start:end]))
        title = (x_title([rest]) if not only_time
                 else x_title(block[:1]) or (x_title([near.group(1)]) if near else headline))
        found.append({**common, "date": d, "title": title, "venue": venue, "times": times, "time_note": note,
                      "today": False,
                      "sure": line.startswith("🗓") and bool(only_time) and bool(venue or times or note)})
    if not found:
        text = " ".join(lines)
        offset = next((n for w, n in X_RELATIVE_WORDS.items() if w in text), None)
        # 「#あしたのたかねこ」などの行がある段落の、次の段落が予定名
        paras = re.split(r"\n{2,}", "\n".join(lines).strip())
        k = next((k for k, p in enumerate(paras) if any(w in p for w in X_RELATIVE_WORDS)), None)
        title = x_title(paras[k + 1].splitlines()) if k is not None and k + 1 < len(paras) else ""
        # 販売・受付の告知かは予定名の段落で判断する（本文の「受付終了」などで捨てないため）
        # ライブ後のお礼投稿の「このあと物販」は、その日の予定の続きなので拾わない
        if offset is not None and not is_non_event(title or headline) and "ありがとうございました" not in text:
            base = published - timedelta(days=1) if offset and posted.hour < X_NIGHT_HOUR else published
            venue, times, note = x_details(lines)
            # 「このあと」「本日」は当日の告知。メンバーの今いる場所を公開しないよう予定一覧には入れない
            found.append({**common, "date": base + timedelta(days=offset), "title": title or headline,
                          "venue": venue, "times": times, "time_note": note, "today": offset == 0,
                          "sure": bool(venue or times or note)})
    return found


def load_x_state(path=X_STATE_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def x_same_venue(a, b):
    """X と公式で会場が同じか。「汐留シオサイト」と「汐留シオサイト地下歩道 特設ステージ」のように
    詳しさが違うことが多いので、どちらかがもう一方を含めば同じ会場とみなす。"""
    a, b = normalize(a), normalize(b)
    return len(a) >= 4 and len(b) >= 4 and (a in b or b in a)


def x_listed(c, rows):
    """公式サイトの予定に同じものがあるか（実行ログの目安。統合は merge_post_rows が行う）。"""
    return any(r["date"] == c["date"].isoformat()
               and (same_event(c, r) or x_same_venue(c["venue"], r["venue"])
                    or any(shares_phrase(r["title"], n, X_NAME_MIN) for n in c["names"]))
               for r in rows)


def load_x_events(path=X_EVENTS_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_x_events(events, today, path=X_EVENTS_PATH):
    """終わった予定（過去の予定は schedule.csv から引き継ぐ）を除いて保存する。変化がなければ書き直さない。"""
    events = sorted((e for e in events if e["date"] >= today.isoformat()), key=lambda e: (e["date"], e["post_id"]))
    text = json.dumps(events, ensure_ascii=False, indent=1) + "\n"
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def x_event(c):
    """保存する形にする（本文は含めない）。"""
    return {"date": c["date"].isoformat(), "title": c["title"], "venue": c["venue"],
            "open": c["times"].get("OPEN", ""), "start": c["times"].get("START", ""), "end": c["times"].get("END", ""),
            "time_note": c["time_note"], "members": c["members"], "names": c["names"],
            "url": c["url"], "post_id": c["post_id"], "posted": c["posted"]}


def x_event_to_row(e):
    d = date.fromisoformat(e["date"])
    return {**{k: e[k] for k in ("date", "open", "start", "end", "time_note", "title", "members", "venue", "url")},
            "weekday": "月火水木金土日"[d.weekday()], "category": X_CATEGORY, "names": e["names"]}


def update_x_events(rows, today, recheck_days=None):
    """公式 X の新しい投稿から予定を読み取って x_events.json に足し、保存済みの予定を行データで返す。

    実行ログには読み取った候補すべてを、公式サイト（カレンダー・お知らせ記事）に同じ予定があるかを付けて出す。
    予定一覧に入れるのは ◎（日付・会場か時間が読めたもの）で、当日の告知（「このあと」「本日」）は除く。
    recheck_days を指定すると、前回の続きではなくその日数分をさかのぼって読み直す（前回の続きの記録は変えない）。
    取得に失敗しても、保存済みの予定は返す。
    """
    events = load_x_events()
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        print("X: X_BEARER_TOKEN が未設定のため新しい投稿は確認しません")
    else:
        try:
            events = read_x_posts(token, rows, events, recheck_days)
        except Exception as e:  # noqa: BLE001 — 補助ソースなので失敗しても保存済みの予定で続行
            print(f"  ! X 取得失敗: {e}")
    save_x_events(events, today)
    return [x_event_to_row(e) for e in events if e["date"] >= today.isoformat()]


def read_x_posts(token, rows, events, recheck_days):
    state = {} if recheck_days else load_x_state()
    tweets, newest = fetch_x_posts(token, state.get("since_id"), recheck_days or X_FIRST_DAYS)
    print(f"X（@takanenofficial）: {len(tweets)} 件の{'投稿を読み直し' if recheck_days else '新しい投稿を確認'}")
    official = [r for r in rows if not r["category"].startswith("Eventernote")]
    counts = {"◎": 0, "◎ 掲載済み": 0, "?": 0}
    for t in tweets:
        for c in extract_x_candidates(t):
            d = c["date"]
            listed = x_listed(c, official)
            time = "/".join(f"{k} {v}" for k, v in c["times"].items()) or c["time_note"]
            extra = "  ".join(s for s in (time, f"＠{c['venue']}" if c["venue"] else "") if s)
            mark = ("?（要確認・公開しない）" if not c["sure"] else "◎（当日の告知・公開しない）" if c["today"] else "◎")
            status = "公式に掲載済み" if listed else "公式に未掲載"
            print(f"  {mark} {d.isoformat()}({'月火水木金土日'[d.weekday()]}) {c['title']}  {extra}  [{status}]  {c['url']}")
            counts["◎" if c["sure"] else "?"] += 1
            counts["◎ 掲載済み"] += c["sure"] and listed
            if c["sure"] and not c["today"]:
                # 同じ投稿・同じ日付の予定は読み直した内容で置き換える
                events = [e for e in events if (e["post_id"], e["date"]) != (c["post_id"], d.isoformat())]
                events.append(x_event(c))
    print(f"  → ◎ {counts['◎']} 件（うち公式に掲載済み {counts['◎ 掲載済み']} 件） / ? {counts['?']} 件")
    if not recheck_days and newest and newest != state.get("since_id"):
        os.makedirs(os.path.dirname(X_STATE_PATH), exist_ok=True)
        with open(X_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"since_id": newest}, f)
            f.write("\n")
    return events


# ---- Eventernote（公式 X などで告知され、公式サイト未掲載の出演情報の補完） ----

EVENTERNOTE_URL = ("https://www.eventernote.com/actors/"
                   "%E9%AB%98%E5%B6%BA%E3%81%AE%E3%81%AA%E3%81%A7%E3%81%97%E3%81%93/67857/events")


def fetch_eventernote_rows(start, end):
    """Eventernote（ファン有志のイベント DB）の出演一覧を行データにする。

    公式 X はログインなしでは取得できないため、X 告知分の補完として使う。
    """
    rows = []
    page = fetch(EVENTERNOTE_URL)
    for block in re.findall(r'<li class="clearfix[^"]*">(.*?)</li>\s*(?=<li class="clearfix|</ul>)', page, re.S):
        m = re.search(r'<p class="day\d">(\d{4}-\d{2}-\d{2})', block)
        t = re.search(r'<h4><a href="(/events/\d+)">(.*?)</a>', block, re.S)
        if not (m and t):
            continue
        d = date.fromisoformat(m.group(1))
        if not start <= d < end:
            continue
        v = re.search(r'会場:\s*<a[^>]*>(.*?)</a>', block, re.S)
        rows.append({
            "date": d.isoformat(),
            "weekday": "月火水木金土日"[d.weekday()],
            "open": "", "start": "", "end": "", "time_note": "",
            "title": html.unescape(t.group(2)).strip(),
            "category": "Eventernote（非公式）",
            "members": GROUP,
            "venue": html.unescape(v.group(1)).strip() if v else "",
            "url": f"https://www.eventernote.com{t.group(1)}",
            "post_text": "",
        })
    return rows


def normalize(text):
    return re.sub(r"[\s✿『』「」【】()（）/／・&＆@＠:：、。!！-]", "", text).lower()


def shares_phrase(a, b, n=4):
    a, b = normalize(a), normalize(b)
    return any(a[i:i + n] in b for i in range(len(a) - n + 1))


TBD_VENUE_RE = re.compile(r"某所|後日|未定|調整中")


def venue_from_post(post):
    """記事タイトルの「… at 会場名 当日販売のお知らせ」や本文の「会場：…」から会場名を取り出す。"""
    title = html.unescape(re.sub(r"<[^>]+>", "", post["title"]["rendered"])).strip()
    candidates = []
    m = re.search(r"(?:^|[^A-Za-z])at\s*(.+)$", title)
    if m:
        candidates.append(re.split(r"\s*(?:当日|のお知らせ|お知らせ|開催|詳細|決定|変更|【|[(（])", m.group(1))[0])
    lines = html_to_lines(post["content"]["rendered"])
    for i, line in enumerate(lines):
        # 「【会場】…」「会場：…」の形だけ（「会場CD販売ブースにて…」のような文は除く）
        m = re.match(r"^[◆■●・\s]*(?:[【\[<＜](?:会場|場所)[】\]>＞]|(?:会場|場所)\s*[:：])\s*(.*)$", line)
        if m:
            candidates.append(m.group(1) or (lines[i + 1] if i + 1 < len(lines) else ""))
    for v in candidates:
        v = re.sub(r"\s+", " ", v).strip()
        if v and not TBD_VENUE_RE.search(v):
            return v
    return ""


def dated_posts(rows, posts):
    """タイトルが【M月D日(曜)】で始まる記事と、日付とイベント名が一致する予定の組（新しい記事から順に）。"""
    for post in sorted(posts, key=lambda p: p["date"], reverse=True):
        title = html.unescape(re.sub(r"<[^>]+>", "", post["title"]["rendered"])).strip()
        m = POST_DATE_RE.match(title)
        if not (title.startswith("【") and m):
            continue
        day = infer_date(*m.groups()[:3], date.fromisoformat(post["date"][:10])).isoformat()
        targets = [r for r in rows if r["date"] == day
                   # どのタイトルにも付く「高嶺のなでしこ」以外で共通の語句がある予定だけ
                   and shares_phrase(r["title"].replace("高嶺のなでしこ", ""), title.replace("高嶺のなでしこ", ""))]
        yield post, targets


def fill_tbd_venues(rows, posts):
    """会場が「東京都内某所（詳細は後日発表）」の予定に、後から出た記事の会場名を入れる（新しい記事を優先）。"""
    for post, targets in dated_posts(rows, posts):
        targets = [r for r in targets if TBD_VENUE_RE.search(r["venue"])]
        venue = venue_from_post(post) if targets else ""
        for r in targets if venue else []:
            print(f"  会場を補完: {r['date']} {r['venue']} → {venue}")
            r["venue"] = venue
            r["venue_url"] = post["link"]  # 情報元（url）とは別の記事から補ったので出どころを残す


def link_detail_posts(rows, posts):
    """公式カレンダーのイベントページが「詳細は後日発表」のままで、詳細がお知らせ記事で出ている予定は、
    情報元（url）をその記事にする（新しい記事を優先）。公式がイベントページを更新すれば元に戻る。
    カレンダーの UID・参加メモのキャッシュが変わらないよう、元のイベントページは event_url に残す。"""
    for post, targets in dated_posts(rows, posts):
        for r in targets:
            # イベントページの本文が「詳細は後日発表！」くらいしかない予定だけ（詳しい本文の一部に出てくる「後日発表」は除く）
            text = r.get("source_text", "")
            if "event_url" in r or "後日発表" not in text or len(text) > 100:
                continue
            print(f"  情報元を記事に変更: {r['date']} {r['title'][:40]} → {post['link']}")
            r["event_url"], r["url"] = r["url"], post["link"]
            if r.get("venue_url") == r["url"]:
                r["venue_url"] = ""  # 会場も同じ記事から補ったなら、情報元のリンクだけで足りる


def is_release(row):
    """「…2026年11月18日(水)リリース」のような発売日の行か。"""
    return row["title"].rstrip().endswith(("リリース", "発売"))


def merge_post_rows(rows, post_rows):
    """カレンダーと同じ予定は統合し、カレンダーにないものだけ追加する。"""
    added = []
    for pr in post_rows:
        # 発売日とその日のリリースイベントは別の予定として残す
        same_day = [r for r in rows + added if r["date"] == pr["date"] and is_release(r) == is_release(pr)]
        dups = [r for r in same_day
                if shares_phrase(pr["title"].split("：")[-1] + pr["venue"], r["title"] + r["venue"])
                or shares_phrase(r["title"], pr["title"])
                # 記事本文にカレンダーのタイトルがそのまま出てくる（例: SPACE MAKE）
                or len(normalize(r["title"])) >= 6
                and normalize(r["title"]) in normalize(pr.get("post_text", ""))
                # X の投稿の『』内の名前が予定名に含まれる（「個別握手会／東京都内某所」と
                # 「『A World in Our Colors』リリース記念個別イベント／大崎ブライトコアホール」）
                or any(shares_phrase(r["title"], n, X_NAME_MIN) for n in pr.get("names", []))
                or pr["category"] == X_CATEGORY and x_same_venue(pr["venue"], r["venue"])]
        if pr["category"] == X_CATEGORY and len(dups) > 1:
            # 取り違えないよう、統合先が 1 件に決まらない X の予定は追加も統合もしない
            print(f"  ! X の予定を統合せず（同じ日の候補が複数）: {pr['date']} {pr['title']} {pr['url']}")
            continue
        dup = dups[0] if dups else None
        if dup and pr["category"] == X_CATEGORY:
            # 公式の予定に置き換わっても X の予定の ID を引き継げるよう、元の投稿を残す
            dup.setdefault("x_urls", []).append(pr["url"])
            if dup["category"] == X_CATEGORY:
                # 同じ予定を告知した別の投稿: 先の投稿で空いている項目を後の投稿で埋める
                for key in ("open", "start", "end", "time_note", "venue"):
                    dup[key] = dup[key] or pr[key]
                continue
        if dup:
            # 記事側に日別の担当メンバーがあればそちらを優先
            if dup["members"] == GROUP and pr["members"] != GROUP:
                dup["members"] = pr["members"]
            for key in ("venue", "time_note"):
                if not dup[key] and not (dup["open"] or dup["start"]):
                    dup[key] = pr[key]
            # 記事の統合順に左右されないよう、同じ会場ならより詳しい表記を採用
            if (pr["venue"] and dup["venue"] and len(pr["venue"]) > len(dup["venue"])
                    and normalize(dup["venue"]) in normalize(pr["venue"])):
                dup["venue"] = pr["venue"]
            continue
        added.append(pr)
        print(f"  + 追加（{pr['category']}）: {pr['date']} {pr['title']} -> {pr['members']}")
    return rows + added


def sort_rows(rows):
    rows.sort(key=lambda r: (r["date"], r["start"] or r["open"] or "99:99", r["title"]))
    return rows


def time_label(row):
    if row["time_note"]:
        return row["time_note"]
    if row["start"] and row["open"]:
        return f"OPEN {row['open']} / START {row['start']}"
    if row["start"]:
        return f"{row['start']}〜{row['end']}" if row["end"] else row["start"]
    return row["open"] and f"OPEN {row['open']}"


def md_escape(text):
    return text.replace("|", "｜").replace("\n", " ")


def md_table(rows, show_members):
    head = "| 済 | 日付 | 時間 | タイトル | 会場 |" + (" 出演 |" if show_members else "")
    sep = "|:-:|---|---|---|---|" + ("---|" if show_members else "")
    lines = [head, sep]
    for r in rows:
        cells = [
            "✅" if r["done"] else "",
            f"{r['date']}({r['weekday']})",
            time_label(r) or "",
            f"[{md_escape(r['title'])}]({r['url']})",
            f"[{md_escape(r['venue'])}]({r['venue_url']})" if r.get("venue_url") else md_escape(r["venue"]),
        ]
        if show_members:
            cells.append(r["members"])
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_markdown(rows, path):
    group_rows = [r for r in rows if r["members"] == GROUP]
    out = [
        "# 高嶺のなでしこ メンバー別スケジュール一覧",
        "",
        f"- 取得元: {BASE_URL}/schedule/ （カレンダー）、{BASE_URL}/news/ （お知らせ記事）、公式X @takanenofficial （公式サイト未掲載の告知）、Eventernote （公式X等の告知の補完・非公式）",
        "- 掲載範囲: 過去の予定（✅ 済）と、取得した日から約1年先までの予定",
        f"- 件数: {len(rows)} 件（うち済 {sum(1 for r in rows if r['done'])} 件）",
        "",
        "「済」に ✅ が付いている予定は終了済みです。開始日より前の予定は前回までの取得結果を引き継いでいます。",
        "",
        "「グループ（全員）」はタイトル等に個別メンバーの記載がないイベントです。"
        "各メンバーの一覧にはグループ出演分も含めています。"
        "カテゴリが「お知らせ記事」の予定は、カレンダー未掲載でお知らせ記事にのみ書かれているものです。"
        "「公式X」は公式 X の告知から読み取った、公式サイトにまだ載っていない予定です。"
        "「Eventernote（非公式）」はファン有志のイベント DB にのみ載っている予定で、公式サイトには未掲載です。",
        "",
        "## 目次",
        "",
        f"- [{GROUP}](#{re.sub(r'[（）]', '', GROUP)})",
    ]
    out += [f"- [{full}](#{full})" for full, _, _ in MEMBERS]
    if any(r.get("memo") and not r["done"] for r in rows):
        out.append("- [参加メモ](#参加メモ)")
    out += ["- [全予定（日付順）](#全予定日付順)", "", f"## {GROUP}", "",
            md_table(group_rows, show_members=False) if group_rows else "予定なし", ""]
    for full, _, _ in MEMBERS:
        personal = [r for r in rows if full in r["members"]]
        mine = [r for r in rows if full in r["members"] or r["members"] == GROUP]
        out += [f"## {full}", "", f"個別 {len(personal)} 件 ／ グループ含む合計 {len(mine)} 件", ""]
        if personal:
            out += ["### 個別スケジュール", "", md_table(personal, show_members=False), ""]
        out += ["### 全スケジュール（グループ含む）", "",
                md_table(mine, show_members=False) if mine else "予定なし", ""]
    memo_rows = [r for r in rows if r.get("memo") and not r["done"]]
    if memo_rows:
        out += ["## 参加メモ", "", "これからの予定のうち、公式の案内がある予定の要点を AI でまとめたものです。", ""]
        for r in memo_rows:
            out += [f"### {r['date']}({r['weekday']}) [{md_escape(r['title'])}]({r['url']})", ""]
            out += [f"- {ln}" for ln in r["memo"].split("\n")[:-1]] + ["", f"> {MEMO_FOOTER}", ""]
    out += ["## 全予定（日付順）", "", md_table(rows, show_members=True), ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def write_csv(rows, path):
    fields = ["done", "date", "weekday", "open", "start", "end", "time_note", "title", "category", "members", "venue", "url", "venue_url", "memo", "uid"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---- 申込・締切（お知らせ記事の「受付期間」などの見出しから読み取る） ----

# (見出しの正規表現, 期間の開始の種別, 期間の終了の種別)。日時が 1 つだけなら開始の種別を使う。
DEADLINE_LABELS = [
    (r"\d回目受付|(?:受付|応募|抽選予約販売|先着販売|販売)期間", "受付開始", "受付締切"),
    (r"受付締め?切り?|申請締切|締切", "受付締切", None),
    (r"当落・?入金期間|抽選結果確認/入金期間", "当落発表", "入金締切"),
    (r"抽選結果(?:発表)?|当選連絡", "当落発表", None),
    (r"当選分お支払い?期限|支払期日|支払締め?切り?", "入金締切", None),
    (r"チケット一般発売", "一般発売", None),
    (r"販売開始", "受付開始", None),
]
DEADLINE_LABEL_RE = re.compile("|".join(f"({p})" for p, _, _ in DEADLINE_LABELS))
DEADLINE_DT_RE = re.compile(
    r"(?:(\d{4})[年/]\s*)?(\d{1,2})[月/]\s*(\d{1,2})日?\s*(?:[(（][^)）]{1,6}[)）])?\s*"
    r"(?:(\d{1,2})(?:[:：]\s?(\d{2})|時(?:(\d{2})分)?))?"
)
DEADLINE_KINDS = ["受付開始", "受付締切", "当落発表", "入金締切", "一般発売"]
# 見出しに使える短い行（【…】・■…・「バーチャル銀行振込」のような支払方法名）
HEADING_RE = re.compile(r"^(?:【([^】]{2,30})】|[■◆★✿]+\s*([^■◆✿。]{2,30}?)\s*[■◆✿]*|([^\s。、:：()（）]{2,15}))$")
# 受付の区分を表す見出しだけを使う（「〇その他ご注意」「指定席」のような見出しは使わない）
HEADING_WORDS_RE = re.compile(r"受付|先行|抽選|予約|販売|撮影会|お話し会|サイン会|握手会|振込|ペイ|カード|コンビニ|決済")
# 支払方法ごとの締切（「バーチャル銀行振込」の受付締め切りなど）は、販売期間の終わりと重なるので載せない
PAYMENT_RE = re.compile(r"振込|ペイ|カード|コンビニ|決済")


def find_deadline_dates(text, published):
    out = []
    for m in DEADLINE_DT_RE.finditer(text):
        y, mo, d, h, mi, mi2 = m.groups()
        if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
            continue
        hm = f"{int(h):02d}:{mi or mi2 or '00'}" if h is not None and int(h) <= 24 else ""
        out.append((infer_date(y, mo, d, published), hm))
    return out


def find_heading(lines, i, published):
    """i 行目の見出し行より前にある、受付の区分を表す見出し（FC先行受付・支払方法など）。"""
    for line in reversed(lines[max(0, i - 10):i]):
        if DEADLINE_LABEL_RE.search(line) or find_deadline_dates(line, published):
            continue
        m = HEADING_RE.match(line)
        # 記号のない短い行は「バーチャル銀行振込」のような支払方法名のときだけ見出しとみなす
        if m and not re.search(r"方法|リンク|URL|こちら|注意", line) and (
                PAYMENT_RE.search(line) if m.group(3) else HEADING_WORDS_RE.search(line)):
            return next(g for g in m.groups() if g).strip()
    return ""


def extract_deadlines(post, skipped=None):
    """記事から申込・締切の予定（dict のリスト）を取り出す。
    記事の公開日より前の日付（2次受付の記事に残った1次受付の日程や記載ミス）は skipped に入れる。"""
    published = date.fromisoformat(post["date"][:10])
    title = html.unescape(re.sub(r"<[^>]+>", "", post["title"]["rendered"])).strip()
    lines = [unicodedata.normalize("NFKC", ln) for ln in html_to_lines(post["content"]["rendered"])]
    # 「高嶺のなでしこ」はどの予定にも付くので省き、空白の違いで別の予定にならないよう揃える
    event = unicodedata.normalize("NFKC", clean_post_title(title))
    event = re.sub(r"\s*([『「』」])\s*", r"\1", event)
    event = re.sub(r"(?<=[^\x00-\x7f])at\s+", " at ", event)  # 「イベントat 会場」→「イベント at 会場」
    event = re.sub(r"^高嶺のなでしこ\s*", "", re.sub(r"\s+", " ", event).strip()) or event
    # 「【10月24日(土)】…」のような記事は、同じ名前の別の回と区別できるよう開催日を付ける
    m = POST_DATE_RE.match(title)
    if title.startswith("【") and m:
        held = infer_date(*m.groups()[:3], published)
        event = f"{held.month}/{held.day} {event}"
    found = []
    for i, line in enumerate(lines):
        m = DEADLINE_LABEL_RE.search(line)
        # 店舗の電話受付時間や会場での当日販売は対象外
        if not m or re.search(r"受付時間|当日|会場", line):
            continue
        _, first_kind, last_kind = DEADLINE_LABELS[m.lastindex - 1]
        text = line[m.end():]
        dates = find_deadline_dates(text, published)
        if not dates and i + 1 < len(lines):
            text = lines[i + 1]
            dates = find_deadline_dates(text, published)
        if not dates:
            continue
        # 販売開始は LINE・オンラインでの販売開始だけ（イベント会場での販売は除く）
        if m.group(0) == "販売開始" and not re.search(r"LINE|オンライン|WEB|Web|web|ストア|サイト", text):
            continue
        heading = find_heading(lines, i, published)
        if PAYMENT_RE.search(heading):
            continue
        detail = heading or m.group(0)
        note = re.sub(r"^[\s:：>＞】\]]+", "", text).strip()
        pairs = [(dates[0], first_kind)]
        if last_kind and len(dates) > 1:
            pairs.append((dates[-1], last_kind))
        elif last_kind and re.search(r"^[^~〜～-]*まで", text):
            pairs = [(dates[0], last_kind)]  # 「〇月〇日 23:59まで」だけの期間は終わりの日時
        for (day, hm), kind in pairs:
            if day < published:
                if skipped is not None:
                    skipped.append((published, f"{day} {kind} {event}（{note[:40]}）"))
                continue
            found.append({"date": day.isoformat(), "weekday": "月火水木金土日"[day.weekday()], "time": hm,
                          "kind": kind, "event": event, "detail": detail, "note": note, "url": post["link"]})
    return found


def collect_deadlines(posts, end, today):
    """全記事の申込・締切を、同じもの（種別・日時・イベントが同じ）をまとめて日付順に返す。"""
    seen, rows, skipped = set(), [], []
    for p in sorted(posts, key=lambda p: p["date"], reverse=True):  # 新しい記事の記載を優先
        for r in extract_deadlines(p, skipped):
            key = (r["kind"], r["date"], r["time"], r["event"])
            if r["date"] < end.isoformat() and key not in seen:
                seen.add(key)
                rows.append(r)
    rows.sort(key=lambda r: (r["date"], r["time"] or "99:99", DEADLINE_KINDS.index(r["kind"]), r["event"]))
    if skipped:
        print(f"  記事の公開日より前の日付 {len(skipped)} 件を除外")
        # 最近の記事のものは公式の記載ミスかもしれないので内容も出す
        for published, text in skipped:
            if published >= today - timedelta(days=30):
                print(f"  ! 記載ミスの可能性: {text}")
    return rows


def deadline_title(row):
    return f"【{row['kind']}】{row['event']}" + (f"（{row['detail']}）" if row["detail"] else "")


def write_deadlines_markdown(rows, path):
    out = [
        "# 高嶺のなでしこ 申込・締切一覧",
        "",
        f"- 取得元: {BASE_URL}/news/ （お知らせ記事の「受付期間」「抽選結果」などの記載）",
        f"- 件数: {len(rows)} 件（うち済 {sum(1 for r in rows if r['done'])} 件）",
        "",
        "お知らせ記事から自動で読み取ったものです。読み取りの誤りや記事に書かれていない受付（FC サイト・"
        "プレイガイドのみの告知など）もあるので、申し込む前に必ず情報元の記事・受付ページを確認してください。",
        "",
        "| 済 | 日付 | 時刻 | 種別 | イベント | 区分 | 記事の記載 |",
        "|:-:|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append("| " + " | ".join([
            "✅" if r["done"] else "", f"{r['date']}({r['weekday']})", r["time"], r["kind"],
            f"[{md_escape(r['event'])}]({r['url']})", md_escape(r["detail"]), md_escape(r["note"]),
        ]) + " |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def write_deadlines_csv(rows, path):
    fields = ["done", "date", "weekday", "time", "kind", "event", "detail", "note", "url"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---- 参加メモ（公式の案内を AI で要約。API キーがなければ作らない） ----

# .env に OPENAI_API_KEY があれば OpenAI、なければ GEMINI_API_KEY で Gemini を使う
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MEMO_MODELS = {"openai": "gpt-5-mini", "gemini": "gemini-3.1-flash-lite"}
# 100 万トークンあたりの料金（米ドル）: (入力, キャッシュされた入力, 出力)。値上げ・値下げがあれば直す
MEMO_PRICES = {"gpt-5-mini": (0.25, 0.025, 2.00), "gemini-3.1-flash-lite": (0.25, 0.025, 1.50)}
USD_JPY = 150  # 料金を円で表示するときの目安のレート
MEMO_PROMPT_VERSION = 3   # 指示文を変えたら上げる（全メモを作り直す）
MEMO_MAX_CALLS = 30       # 1 回の実行で AI を呼ぶ上限（不具合で大量に呼ばないため）
MEMO_SOURCE_LIMIT = 30000  # AI に渡す文章の上限（文字数）
# (キー, 表示名)。この順でメモに並べる
MEMO_FIELDS = [
    ("venue", "📍 会場"), ("flow", "🕙 当日の流れ"), ("parts", "👥 部とメンバー"),
    ("join", "🎫 参加方法・当日券"), ("price", "💴 料金"), ("payment", "💳 支払い"),
    ("items", "🎒 持ち物"), ("rules", "📸 ルール"), ("notes", "⚠️ 注意"), ("other", "ℹ️ その他"),
]
MEMO_FOOTER = "🤖 公式の案内を AI が要約したものです。必ず情報元で確認してください。"
# 個人情報の取り扱い・問い合わせ先など、どのイベントにも付く定型文
MEMO_BOILERPLATE_RE = re.compile(
    r"個人情報|第三者|反社会|安全管理措置|お申込者様|当社|ドメイン設定|受信設定|お問い?合わせ|相談室|"
    r"^\[TEL\]|^\[受付時間\]|^https?://\S+$")
MEMO_INSTRUCTION = """\
あなたはアイドルグループ「高嶺のなでしこ」のイベントに参加するファン向けに、公式の案内文から
当日に必要なことだけを短くまとめるアシスタントです。

規則:
- 案内文に書かれている事実だけを使う。書かれていないことは推測せず、その項目は空文字にする。
- 「記載なし」「未発表」「後日発表」「要確認」のように、書かれていないことを説明する文は書かない（空文字にする）。
- 日時・時刻・金額・枚数・締切は案内文の通りに正確に書く。
- 各項目は 80 文字程度まで（parts と rules は 150 文字程度まで）。箇条書きにしたい場合は「／」で区切る。
- 案内文を長く引用せず、要点だけを自分の言葉でまとめる。同じ内容を複数の項目に書かない。
- parts は同じメンバー構成の部をまとめる（例:「1・3・5部（11:30/14:30/16:50）：城月・東山・日向端・松本」）。メンバーは姓だけでよい。
- URL は書かない。ただし外部イベントで主催者サイトを案内する場合だけ、そのトップページの URL を書く（takanenonadeshiko.jp は主催者サイトではない）。
- 個人情報の取り扱い、問い合わせ先、一般的な禁止事項の羅列は省く。参加者が特に気をつけるべき点だけを notes に書く。
- 出演時間・入場ルールなどが主催者のサイトにしか書かれていない外部イベントは、other に「詳細は主催者サイト（URL）で確認」と書く。
- 開催済みの販売や受付（締切が過ぎたもの）は書かなくてよい。

項目:
- venue: 会場名と住所
- flow: 当日の時系列（販売開始・開場・開演・各部の開始など）
- parts: 部ごとのメンバーや時間
- join: 参加方法、参加券の入手方法、当日券・当日販売の有無と場所
- price: 料金（券種・商品と金額）
- payment: 支払い方法
- items: 持ち物（本人確認書類・スマホ・マスクなど）
- rules: 撮影・握手・トークなどのルール
- notes: 特に注意すること
- other: その他（オンラインのアプリ、主催者サイトの案内、商品の受け取り方法など）
"""


def load_env(path=".env"):
    """.env の KEY=VALUE を環境変数に読み込む（すでに設定されていれば上書きしない）。"""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and not key.startswith("#"):
                    os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


def post_title(post):
    return html.unescape(re.sub(r"<[^>]+>", "", post["title"]["rendered"])).strip()


def related_posts(row, posts):
    """予定に関係するお知らせ記事（情報元そのもの、または同じ日付・同じイベント名の記事）。"""
    day = date.fromisoformat(row["date"])
    found = []
    for p in posts:
        title = post_title(p)
        if p["link"] in (row["url"], row.get("venue_url")):
            found.append(p)
            continue
        m = POST_DATE_RE.match(title)
        published = date.fromisoformat(p["date"][:10])
        if title.startswith("【") and m:
            # タイトルに開催日がある記事は、その日の予定の記事
            same_day = infer_date(*m.groups()[:3], published) == day
        else:
            # 本文に開催日が書かれている（「2026年11月18日(水)発売」のような発売日の記載は除く）
            same_day = re.search(rf"{day.year}年{day.month}月{day.day}日(?!\s*[(（][^)）]*[)）]\s*(発売|リリース))",
                                 unicodedata.normalize("NFKC", html.unescape(p["content"]["rendered"])))
        if same_day and shares_phrase(row["title"].replace("高嶺のなでしこ", ""), title.replace("高嶺のなでしこ", ""), n=5):
            found.append(p)
    return sorted(found, key=lambda p: p["date"])


def memo_source(row, posts):
    """AI に渡す文章（詳細ページ＋関係する記事。定型文は除く）。"""
    parts = []
    if row.get("source_text"):
        parts.append(f"# イベントページ {row.get('event_url', row['url'])}\n{row['source_text']}")
    for p in related_posts(row, posts):
        lines = [unicodedata.normalize("NFKC", ln) for ln in html_to_lines(p["content"]["rendered"])]
        body = "\n".join(ln for ln in lines if not MEMO_BOILERPLATE_RE.search(ln))
        parts.append(f"# お知らせ記事（{p['date'][:10]} 公開）{post_title(p)}\n{p['link']}\n{body}")
    return "\n\n".join(parts)[:MEMO_SOURCE_LIMIT]


def memo_prompt(row, source):
    return (f"イベント: {row['title']}\n日付: {row['date']}（{row['weekday']}）\n会場: {row['venue']}\n\n"
            f"--- 公式の案内 ---\n{source}")


def post_json(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=180) as res:
        return json.load(res)


def call_openai(model, key, row, source):
    """(メモの dict, (入力トークン, 出力トークン)) を返す。"""
    schema = {"type": "object", "additionalProperties": False,
              "properties": {k: {"type": "string"} for k, _ in MEMO_FIELDS},
              "required": [k for k, _ in MEMO_FIELDS]}
    data = post_json(OPENAI_URL, {
        "model": model,
        "messages": [{"role": "system", "content": MEMO_INSTRUCTION},
                     {"role": "user", "content": memo_prompt(row, source)}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "memo", "strict": True, "schema": schema}},
        "reasoning_effort": "low",
    }, {"Authorization": f"Bearer {key}"})
    usage = data.get("usage", {})
    memo = json.loads(data["choices"][0]["message"]["content"])
    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    return memo, (usage.get("prompt_tokens", 0), cached, usage.get("completion_tokens", 0))


def call_gemini(model, key, row, source):
    """(メモの dict, (入力トークン, 出力トークン)) を返す。"""
    schema = {"type": "OBJECT", "properties": {k: {"type": "STRING"} for k, _ in MEMO_FIELDS},
              "required": [k for k, _ in MEMO_FIELDS]}
    data = post_json(GEMINI_URL.format(model=model), {
        "systemInstruction": {"parts": [{"text": MEMO_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": memo_prompt(row, source)}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                             "responseSchema": schema},
    }, {"x-goog-api-key": key})
    usage = data.get("usageMetadata", {})
    memo = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    return memo, (usage.get("promptTokenCount", 0), usage.get("cachedContentTokenCount", 0),
                  usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0))


def memo_provider():
    """(名前, API キー, 呼び出し関数)。キーがなければ (None, "", None)。"""
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", os.environ["OPENAI_API_KEY"], call_openai
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini", os.environ["GEMINI_API_KEY"], call_gemini
    return None, "", None


MEMO_EMPTY_RE = re.compile(r"^(詳細は?)?(後日発表|未発表|未定|記載なし|不明|要確認)")
MEMO_NOTE_RE = re.compile(r"記載なし|記載がない|記載されていない|明記されていない")


def clean_memo(memo):
    """AI の出力を整える（改行をまとめ、公式サイトの URL と「後日発表」だけの項目を除く）。"""
    out = {}
    for k, _ in MEMO_FIELDS:
        v = re.sub(r"\s*\n\s*", "／", (memo.get(k) or "").strip())
        v = re.sub(r"[（(]?\s*https?://(www\.)?takanenonadeshiko\.jp\S*?\s*[)）]?(?=[／\s]|$)", "", v).strip(" ／")
        # 「／」区切りの一部が「記載なし」などの説明なら、その部分だけ除く
        v = "／".join(x for x in v.split("／") if not MEMO_NOTE_RE.search(x))
        # URL を除いた結果「詳細は主催者サイト（URL）で確認」だけが残ったものも除く
        out[k] = "" if MEMO_EMPTY_RE.match(v) or ("URL" in v and "http" not in v) else v
    return out


def memo_text(memo):
    lines = [f"{label}: {memo[k]}" for k, label in MEMO_FIELDS if memo.get(k)]
    return "\n".join(lines + [MEMO_FOOTER]) if lines else ""


def add_memos(rows, posts, today, cache_path="memos.json"):
    """これからの予定に参加メモ（row["memo"]）を付ける。材料の文章が変わっていなければ前回のメモを使う。
    今回 AI で作った予定を (row, "新規" または "更新") のリストで返す。"""
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except FileNotFoundError:
        cache = {}
    provider, key, call = memo_provider()
    model = MEMO_MODELS.get(provider, "")
    calls, tokens, kept, created = 0, [0, 0, 0], {}, []  # tokens: 入力・うちキャッシュ・出力
    for r in rows:
        if (r["date"] < today.isoformat() or r["category"].startswith(("Eventernote", "誕生日", X_CATEGORY))
                or is_release(r)):  # 発売日の行は参加する予定ではない
            continue
        source = memo_source(r, posts)
        # 日時と会場くらいしか書かれていない、または「詳細は後日発表」だけの予定（見出しと URL の行は数えない）
        content = len(re.sub(r"(?m)^(#.*|https?://\S+)$", "", source).strip())
        if content < 80 or "後日発表" in source and content < 600:
            continue
        cid = f"{r['date']}|{r.get('event_url', r['url'])}"
        digest = hashlib.sha1(f"{MEMO_PROMPT_VERSION}|{model}|{source}".encode()).hexdigest()
        entry = cache.get(cid)
        if not (entry and entry["hash"] == digest) and key and calls < MEMO_MAX_CALLS:
            try:
                memo, usage = call(model, key, r, source)
                calls += 1
                tokens = [t + u for t, u in zip(tokens, usage)]
                memo = clean_memo(memo)
                created.append((r, "更新" if entry else "新規"))
                entry = {"hash": digest, "model": model, "generated": today.isoformat(), "memo": memo}
                print(f"  参加メモを作成: {r['date']} {r['title'][:40]}")
                time.sleep(1)
            except Exception as e:  # noqa: BLE001 — メモは補助的な情報なので失敗しても続行（前回のメモを使う）
                print(f"  ! 参加メモの作成失敗: {r['date']} {r['title'][:40]} ({e})")
        if entry:
            kept[cid] = entry
            r["memo"] = memo_text(clean_memo(entry["memo"]))
    # 過去の予定のメモは CSV に残るので、キャッシュからは消す
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    if not key:
        print("  （OPENAI_API_KEY・GEMINI_API_KEY がないため、参加メモは新しく作りません）")
    print(f"参加メモ: {len(kept)} 件（今回 {model or 'AI'} で作成 {calls} 件、"
          f"入力 {tokens[0]:,}（うちキャッシュ {tokens[1]:,}）/ 出力 {tokens[2]:,} トークン）")
    if key and not calls:
        print("AI 利用料: なし（今回は AI を呼んでいません）")
    if calls:
        price = MEMO_PRICES.get(model)
        if price:
            usd = ((tokens[0] - tokens[1]) * price[0] + tokens[1] * price[1] + tokens[2] * price[2]) / 1_000_000
            print(f"AI 利用料（概算）: ${usd:.4f}（約 {usd * USD_JPY:.1f} 円、1 ドル {USD_JPY} 円で換算）")
        else:
            print(f"AI 利用料: {model} の料金が MEMO_PRICES にないため計算できません")
    return created


# ---- iCalendar（Mac / iPhone のカレンダー照会用） ----

DEFAULT_DURATION = timedelta(hours=2)  # 終了時刻の記載がない予定の長さ
TIME_RE = r"(\d{1,2})[:：](\d{2})"


def to_datetime(day, hm):
    h, m = (int(x) for x in re.match(TIME_RE, hm).groups())
    return datetime.combine(day, datetime.min.time()) + timedelta(hours=h, minutes=m)


def event_period(row):
    """(開始, 終了) を返す。時刻が分からなければ None（終日扱い）。"""
    day = date.fromisoformat(row["date"])
    if row["start"] or row["open"]:
        start = to_datetime(day, row["start"] or row["open"])
        end = to_datetime(day, row["end"]) if row["end"] else start + DEFAULT_DURATION
        return start, end if end > start else end + timedelta(days=1)
    note = row["time_note"]
    if not note:
        return None
    # 部ごとの時間: 最初の部の開始〜最後の部の終了
    ranges = re.findall(rf"{TIME_RE}\s*[-~〜～－]\s*{TIME_RE}", note)
    if ranges:
        start = to_datetime(day, "%s:%s" % ranges[0][:2])
        end = to_datetime(day, "%s:%s" % ranges[-1][2:])
        return start, end if end > start else end + timedelta(days=1)
    # 「1部 12:30 START（優先エリア入場12:10）」「[OPEN/START] 15:00/16:00」は START の時刻を使う
    note = re.sub(r"[（(][^）)]*[）)]", "", note)
    note = re.sub(rf"{TIME_RE}\s*/\s*({TIME_RE})", r"\3", note)
    times = re.findall(TIME_RE, note)
    if not times:
        return None
    return to_datetime(day, "%s:%s" % times[0]), to_datetime(day, "%s:%s" % times[-1]) + DEFAULT_DURATION


def ics_escape(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", "").replace("\n", "\\n"))


def ics_fold(line):
    """1 行 75 オクテット以内に折り返す（マルチバイト文字の途中では切らない）。"""
    out, cur = [], ""
    for ch in line:
        limit = 75 if not out else 74  # 継続行は先頭の空白 1 文字分短くする
        if len((cur + ch).encode("utf-8")) > limit:
            out.append(cur)
            cur = ""
        cur += ch
    out.append(cur)
    return "\r\n ".join(out)


def ics_summary(row):
    title = re.sub(r"\s+", " ", row["title"]).strip()
    if row["members"] != GROUP:
        names = [last for full, last, _ in MEMBERS if full in row["members"]]
        if not all(n in title for n in names):
            title = f"【{'・'.join(names)}】{title}"
    if row["category"].startswith("Eventernote"):
        title += "（非公式）"
    return title


def ics_description(row):
    lines = []
    if time_label(row):
        lines.append(f"時間: {time_label(row)}")
    lines += [f"出演: {row['members']}", f"カテゴリ: {row['category']}", f"情報元: {row['url']}"]
    if row.get("venue_url"):
        lines.append(f"会場の情報元: {row['venue_url']}")
    if row.get("memo"):
        lines += ["", "【参加メモ】", row["memo"]]
    if row["category"].startswith("Eventernote"):
        lines.append("※公式サイト未掲載（Eventernote の情報）")
    return "\n".join(lines)


DEADLINE_DURATION = timedelta(minutes=30)  # 申込・締切の予定の長さ


def ics_period_lines(period, day):
    if period:
        return [f"DTSTART;TZID=Asia/Tokyo:{period[0]:%Y%m%dT%H%M%S}",
                f"DTEND;TZID=Asia/Tokyo:{period[1]:%Y%m%dT%H%M%S}"]
    return [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]


def write_calendar(path, name, events):
    """events は VEVENT の中身（BEGIN/END・DTSTAMP 以外）の行リストのリスト。
    name が None なら照会用のカレンダー名・更新間隔を付けない（1 件ずつ取り込む用）。
    DTSTAMP 以外が前回と同じなら書き直さない（実行時刻だけの差分を出さないため）。書き直したら True。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//takaneko-data//fetch_schedule.py//JA",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    if name:
        out += [f"X-WR-CALNAME:{name}",
                "X-WR-TIMEZONE:Asia/Tokyo",
                "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
                "X-PUBLISHED-TTL:PT6H"]
    out += [
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Tokyo",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0900",
        "TZOFFSETTO:+0900",
        "TZNAME:JST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for ev in events:
        out += ["BEGIN:VEVENT", ev[0], f"DTSTAMP:{stamp}", *ev[1:], "END:VEVENT"]
    out.append("END:VCALENDAR")
    text = "".join(ics_fold(line) + "\r\n" for line in out)

    def without_stamp(s):
        return [line for line in s.split("\r\n") if not line.startswith("DTSTAMP:")]
    try:
        with open(path, encoding="utf-8", newline="") as f:
            if without_stamp(f.read()) == without_stamp(text):
                return False
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return True


def same_event(a, b):
    """同じ日の 2 つの予定が、情報元は違っても同じ予定に見えるか（タイトルか会場が近い）。"""
    return (shares_phrase(a["title"], b["title"])
            or bool(a["venue"] and b["venue"]) and normalize(a["venue"]) == normalize(b["venue"]))


def assign_uids(rows, prev_rows=()):
    """予定ごとの ID（iCalendar の UID・Web 版の「行く」の印・1 件ずつの .ics のファイル名）を r["uid"] に入れる。

    ID は日付と情報元から作るが、情報元が変わった予定（Eventernote → 公式カレンダーなど）は
    前回の schedule.csv の ID を引き継ぐ。カレンダーの予定が作り直されたり「行く」の印が外れたりしないように。"""
    seen = set()
    for r in rows:
        if r.get("uid"):  # 前回の CSV から引き継いだ過去の予定
            seen.add(r["uid"])
    for r in rows:
        if r.get("uid"):
            continue
        uid = hashlib.sha1(f"{r['date']}|{r.get('event_url', r['url'])}".encode()).hexdigest()[:16]
        while uid in seen:  # 同じ日・同じ情報元の予定が複数ある場合
            uid = hashlib.sha1((uid + r["title"]).encode()).hexdigest()[:16]
        seen.add(uid)
        r["uid"] = uid

    # 前回なかった ID の予定に、前回あって今回消えた同じ日の予定の ID を引き継ぐ。
    # 取り違えないよう、どちらから見ても候補が 1 件だけのときに限る。
    prev_uids = {p["uid"] for p in prev_rows if p.get("uid")}
    new_rows = [r for r in rows if r["uid"] not in prev_uids]
    gone = [p for p in prev_rows if p.get("uid") and p["uid"] not in seen]
    def inherits(p, r):
        # 公式の予定にまとまった X の予定は、予定名・会場が違っても元の投稿で同じ予定と分かる
        return p["date"] == r["date"] and (same_event(p, r) or p["url"] in r.get("x_urls", ()))

    for r in new_rows:
        cands = [p for p in gone if inherits(p, r)]
        if not cands:
            continue
        rivals = [x for x in new_rows if inherits(cands[0], x)]
        if len(cands) > 1 or len(rivals) > 1:
            print(f"  ! ID を引き継がず（候補が複数）: {r['date']} {r['title']}")
            continue
        p = cands[0]
        print(f"  = ID を引き継ぎ: {r['date']} {r['title']}（{p['url']} → {r['url']}）")
        r["uid"] = p["uid"]
        gone.remove(p)


def ics_event(r, suffix="takaneko-data"):
    ev = [f"UID:{r['uid']}@{suffix}", *ics_period_lines(event_period(r), date.fromisoformat(r["date"])),
          f"SUMMARY:{ics_escape(ics_summary(r))}"]
    if r["venue"]:
        ev.append(f"LOCATION:{ics_escape(r['venue'])}")
    ev += [f"DESCRIPTION:{ics_escape(ics_description(r))}",
           f"URL:{r['url']}",
           "STATUS:" + ("TENTATIVE" if r["category"].startswith("Eventernote") else "CONFIRMED"),
           "TRANSP:TRANSPARENT"]  # 個人の予定の「空き時間」を塞がない
    return ev


def write_ics(rows, path):
    return write_calendar(path, "高嶺のなでしこ", [ics_event(r) for r in rows])


def write_event_files(rows, folder):
    """これからの予定を 1 件ずつ folder/<uid>.ics に書き出す（Web 版の「カレンダーに追加」用）。
    照会カレンダーと同じ予定として扱われないよう UID を変え、終わった・消えた予定のファイルは消す。
    書き直した・消したファイルの数を返す。"""
    os.makedirs(folder, exist_ok=True)
    keep, changed = set(), 0
    for r in rows:
        if r["done"]:
            continue
        name = f"{r['uid']}.ics"
        keep.add(name)
        changed += write_calendar(os.path.join(folder, name), None, [ics_event(r, "takaneko-data-mine")])
    for name in os.listdir(folder):
        if name.endswith(".ics") and name not in keep:
            os.remove(os.path.join(folder, name))
            changed += 1
    return changed


def write_deadlines_ics(rows, path):
    events = []
    for r in rows:
        key = f"{r['kind']}|{r['date']}|{r['time']}|{r['event']}"
        uid = hashlib.sha1(key.encode()).hexdigest()[:16]
        day = date.fromisoformat(r["date"])
        period = None
        if r["time"]:
            t = to_datetime(day, r["time"])
            # 締切は締切時刻で終わるように、それ以外はその時刻から始まるように置く
            period = (t - DEADLINE_DURATION, t) if r["kind"].endswith("締切") else (t, t + DEADLINE_DURATION)
        desc = [f"種別: {r['kind']}", f"イベント: {r['event']}"]
        if r["detail"]:
            desc.append(f"区分: {r['detail']}")
        desc += [f"記事の記載: {r['note']}", f"情報元: {r['url']}",
                 "※お知らせ記事から自動で読み取った情報です。申し込む前に必ず受付ページを確認してください。"]
        events.append([f"UID:{uid}@takaneko-data-deadlines", *ics_period_lines(period, day),
                       f"SUMMARY:{ics_escape(deadline_title(r))}",
                       f"DESCRIPTION:{ics_escape(chr(10).join(desc))}",
                       f"URL:{r['url']}", "STATUS:CONFIRMED", "TRANSP:TRANSPARENT"])
    return write_calendar(path, "高嶺のなでしこ 申込・締切", events)


def load_prev_rows(path):
    """前回出力した CSV の予定を読み込む（初回は空）。"""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


# ---- 安全策（取得失敗で予定を消したまま出力しないため） ----

DROP_RATIO = 0.3  # 前回あった予定のうち、この割合以上が消えたら止める
DROP_MIN = 5      # ただし消えた件数がこれ未満なら止めない


def check_drop(prev_rows, rows, start, end, n_events):
    """取得結果が前回より不自然に少なければ、理由を返す（問題なければ None）。"""
    if n_events == 0:
        return "公式カレンダーから 1 件も取得できませんでした"
    # Eventernote は補助ソースで取得失敗も許しているので比較しない
    prev = [r for r in prev_rows
            if start.isoformat() <= r["date"] < end.isoformat() and not r["category"].startswith("Eventernote")]
    keys = {(r["date"], u) for r in rows for u in (r["url"], r.get("event_url"), *r.get("x_urls", ()))}
    removed = [r for r in prev if (r["date"], r["url"]) not in keys]
    if len(removed) >= DROP_MIN and len(removed) >= len(prev) * DROP_RATIO:
        lines = [f"  - {r['date']} {r['title']}" for r in removed]
        return (f"前回あった予定 {len(prev)} 件のうち {len(removed)} 件が見つかりません\n" + "\n".join(lines))
    return None


def row_label(r):
    note = f" ［{r['category']}］" if r["category"].startswith(("Eventernote", "お知らせ記事", X_CATEGORY)) else ""
    title = re.sub(r"\s+", " ", r["title"]).strip()
    return f"{r['date']}({r['weekday']}) {title}{note}"


def print_changes(prev_rows, rows, memo_created):
    """前回の CSV になかった予定と、今回 AI で参加メモを作った予定を最後にまとめて表示する。"""
    prev_uids = {r["uid"] for r in prev_rows if r.get("uid")}
    added = [r for r in rows if r["uid"] not in prev_uids]
    print("\n=== 今回の変更 ===")
    print(f"追加された予定: {len(added)} 件")
    for r in added:
        print(f"  + {row_label(r)}")
    print(f"参加メモを作成した予定: {len(memo_created)} 件")
    for r, kind in memo_created:
        print(f"  ✎ {row_label(r)}（{kind}）")


def main():
    today = datetime.now(JST).date()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, default=today, help="開始日 (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, default=today + timedelta(days=366),
                        help="終了日・この日を含まない (YYYY-MM-DD)")
    parser.add_argument("--no-detail", action="store_true", help="詳細ページを取得しない（時間・会場なし）")
    parser.add_argument("--post-days", type=int, default=365,
                        help="何日前までのお知らせ記事を確認するか")
    parser.add_argument("--md", default="schedule.md")
    parser.add_argument("--csv", default="schedule.csv")
    parser.add_argument("--ics", default="schedule.ics")
    parser.add_argument("--deadlines", default="deadlines",
                        help="申込・締切の出力先（拡張子なし。.md / .csv / .ics を出力）")
    parser.add_argument("--force", action="store_true",
                        help="予定が大きく減っていても出力する（安全策のチェックを飛ばす）")
    parser.add_argument("--no-memo", action="store_true", help="参加メモを作らない（前回のメモも付けない）")
    parser.add_argument("--x-recheck", type=int, metavar="DAYS",
                        help="公式 X を DAYS 日前から読み直す（読み取った予定は保存する。前回の続きの記録は変えない）")
    args = parser.parse_args()
    load_env()

    # API は開始日当日のイベントを含めないため 1 日前から取得して絞り込む
    events = [ev for ev in fetch_events(args.start - timedelta(days=1), args.end)
              if args.start.isoformat() <= ev["start"][:10] < args.end.isoformat()]
    print(f"{len(events)} 件のイベントを取得")
    rows = build_rows(events, with_detail=not args.no_detail)

    posts = fetch_posts(args.start - timedelta(days=args.post_days))
    print(f"{len(posts)} 件のお知らせ記事を確認")
    post_rows = [post_item_to_row(it) for p in posts for it in extract_post_items(p)
                 if args.start <= it["date"] < args.end]
    # 同じ予定が複数記事に書かれている場合があるので、古い記事→新しい記事の順で統合
    rows = merge_post_rows(rows, sorted(post_rows, key=lambda r: r["date"]))
    # 公式 X の予定は古い投稿から順に統合する（ID の元になる情報元が、後の投稿で変わらないように）
    x_rows = [r for r in update_x_events(rows, today, args.x_recheck)
              if args.start.isoformat() <= r["date"] < args.end.isoformat()]
    rows = merge_post_rows(rows, sorted(x_rows, key=lambda r: (r["date"], int(r["url"].rsplit("/", 1)[1]))))
    try:
        rows = merge_post_rows(rows, fetch_eventernote_rows(args.start, args.end))
    except Exception as e:  # noqa: BLE001 — 補助ソースなので失敗しても続行
        print(f"  ! Eventernote 取得失敗: {e}")
    fill_tbd_venues(rows, posts)
    link_detail_posts(rows, posts)
    prev = load_prev_rows(args.csv)
    problem = None if args.force else check_drop(prev, rows, args.start, args.end, len(events))
    if problem:
        raise SystemExit(f"中止: {problem}\n"
                         "ファイルは書き換えていません。取得に問題がなければ --force を付けて実行してください。")
    past = [r for r in prev if r["date"] < args.start.isoformat()]
    print(f"{len(past)} 件の過去の予定を引き継ぎ")
    rows = sort_rows(past + rows)
    memo_created = []
    if not args.no_memo:
        memo_created = add_memos(rows, posts, today)
    for r in rows:
        r["done"] = "済" if r["date"] < today.isoformat() else ""
    assign_uids(rows, prev)
    write_markdown(rows, args.md)
    write_csv(rows, args.csv)
    ics_note = "" if write_ics(rows, args.ics) else "（予定に変更なし・書き直さず）"
    print(f"出力: {args.md}, {args.csv}, {args.ics}{ics_note}")
    events_dir = os.path.join(os.path.dirname(args.ics), "events")
    n = write_event_files(rows, events_dir)
    print(f"1 件ずつの .ics: {events_dir}/（{n} 件を更新）")

    deadlines = collect_deadlines(posts, args.end, today)
    for r in deadlines:
        r["done"] = "済" if r["date"] < today.isoformat() else ""
    base = args.deadlines
    write_deadlines_markdown(deadlines, f"{base}.md")
    write_deadlines_csv(deadlines, f"{base}.csv")
    ics_note = "" if write_deadlines_ics(deadlines, f"{base}.ics") else "（変更なし・書き直さず）"
    print(f"{len(deadlines)} 件の申込・締切 → 出力: {base}.md, {base}.csv, {base}.ics{ics_note}")
    print_changes(prev, rows, memo_created)


if __name__ == "__main__":
    main()
