"""高嶺のなでしこ公式サイトからスケジュールを取得し、メンバー別の一覧ファイルを作成する。

使い方:
    python fetch_schedule.py                       # 今日以降の予定
    python fetch_schedule.py --start 2026-01-01    # 期間指定

開始日より前の予定は既存の CSV から引き継ぎ、終わった予定には「済」を付ける。
"""

import argparse
import csv
import html
import json
import re
import time
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
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (takaneko-data)"})
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


def merge_post_rows(rows, post_rows):
    """カレンダーと同じ予定は統合し、カレンダーにないものだけ追加する。"""
    added = []
    for pr in post_rows:
        same_day = [r for r in rows + added if r["date"] == pr["date"]]
        dup = next((r for r in same_day
                    if shares_phrase(pr["title"].split("：")[-1] + pr["venue"], r["title"] + r["venue"])
                    or shares_phrase(r["title"], pr["title"])
                    # 記事本文にカレンダーのタイトルがそのまま出てくる（例: SPACE MAKE）
                    or len(normalize(r["title"])) >= 6
                    and normalize(r["title"]) in normalize(pr["post_text"])), None)
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
            md_escape(r["venue"]),
        ]
        if show_members:
            cells.append(r["members"])
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_markdown(rows, path, start, end):
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    group_rows = [r for r in rows if r["members"] == GROUP]
    out = [
        "# 高嶺のなでしこ メンバー別スケジュール一覧",
        "",
        f"- 取得元: {BASE_URL}/schedule/ （カレンダー）、{BASE_URL}/news/ （お知らせ記事）、Eventernote （公式X等の告知の補完・非公式）",
        f"- 対象期間: {start} 〜 {end - timedelta(days=1)}",
        f"- 取得日時: {now} (JST)",
        f"- 件数: {len(rows)} 件（うち済 {sum(1 for r in rows if r['done'])} 件）",
        "",
        "「済」に ✅ が付いている予定は終了済みです。開始日より前の予定は前回までの取得結果を引き継いでいます。",
        "",
        "「グループ（全員）」はタイトル等に個別メンバーの記載がないイベントです。"
        "各メンバーの一覧にはグループ出演分も含めています。"
        "カテゴリが「お知らせ記事」の予定は、カレンダー未掲載でお知らせ記事にのみ書かれているものです。"
        "「Eventernote（非公式）」はファン有志のイベント DB にのみ載っている予定で、公式サイトには未掲載です。",
        "",
        "## 目次",
        "",
        f"- [{GROUP}](#{re.sub(r'[（）]', '', GROUP)})",
    ]
    out += [f"- [{full}](#{full})" for full, _, _ in MEMBERS]
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
    out += ["## 全予定（日付順）", "", md_table(rows, show_members=True), ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def write_csv(rows, path):
    fields = ["done", "date", "weekday", "open", "start", "end", "time_note", "title", "category", "members", "venue", "url"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_past_rows(path, before):
    """前回出力した CSV から、指定日より前の予定を読み込む。"""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [r for r in csv.DictReader(f) if r["date"] < before.isoformat()]
    except FileNotFoundError:
        return []


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
    args = parser.parse_args()

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
    try:
        rows = merge_post_rows(rows, fetch_eventernote_rows(args.start, args.end))
    except Exception as e:  # noqa: BLE001 — 補助ソースなので失敗しても続行
        print(f"  ! Eventernote 取得失敗: {e}")
    past = load_past_rows(args.csv, args.start)
    print(f"{len(past)} 件の過去の予定を引き継ぎ")
    rows = sort_rows(past + rows)
    for r in rows:
        r["done"] = "済" if r["date"] < today.isoformat() else ""
    write_markdown(rows, args.md, args.start, args.end)
    write_csv(rows, args.csv)
    print(f"出力: {args.md}, {args.csv}")


if __name__ == "__main__":
    main()
