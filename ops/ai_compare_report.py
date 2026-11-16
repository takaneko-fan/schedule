#!/usr/bin/env python3
"""ルールと AI の読み取りの比べ合わせ（ai_compare.jsonl）を集計する。

定期の更新を動かしている VPS で実行する（記録は VPS の ~/.local/state/takaneko-fan/ にたまる）。
項目ごとの一致率と、食い違い・片方だけの例、AI だけが読めた投稿の一覧を出す。

使い方: python3 ops/ai_compare_report.py [--path 記録ファイル] [--since YYYY-MM-DD] [--examples N]
"""

import argparse
import collections
import json
import os

FIELDS = {"title": "予定名", "venue": "会場", "start": "開演時刻", "members": "出演メンバー"}
DEFAULT_PATH = os.environ.get("AI_COMPARE_PATH") or os.path.expanduser("~/.local/state/takaneko-fan/ai_compare.jsonl")


def load(path, since):
    entries = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if since and e["posted"][:10] < since:
                continue
            entries[(e["post_id"], e["kind"])] = e  # 同じ投稿を読み直した場合は最後の記録を使う
    return list(entries.values())


def report(entries, examples=10):
    lines = []
    compared = [e for e in entries if e["kind"] == "比較"]
    ai_only = [e for e in entries if e["kind"] == "AI のみ"]
    lines.append(f"投稿: {len(entries)} 件（両方で読んだ {len(compared)} 件、AI だけが読めた {len(ai_only)} 件）")

    pairs = [p for e in compared for p in e["pairs"]]
    both = [p for p in pairs if p["rule"] and p["ai"]]
    lines.append(f"予定: {len(pairs)} 件（日付が両方にある {len(both)} 件、ルールのみ "
                 f"{sum(1 for p in pairs if p['ai'] is None)} 件、AI のみ {sum(1 for p in pairs if p['rule'] is None)} 件）")
    lines.append("")
    lines.append("項目ごとの判定（日付が両方にある予定）")
    for field, label in FIELDS.items():
        counts = collections.Counter(p["fields"].get(field, "") for p in both)
        judged = counts["一致"] + counts["食い違い"]
        rate = f"{counts['一致'] / judged:.0%}" if judged else "-"
        lines.append(f"  {label}: 一致 {counts['一致']} / 食い違い {counts['食い違い']} / 片方だけ {counts['片方だけ']}"
                     f" / どちらもなし {counts['どちらもなし']}（一致率 {rate}）")

    def show(p):
        r, a = p["rule"] or {}, p["ai"] or {}
        return [f"    ルール: {r.get('title', '-')} / {r.get('venue', '') or '会場なし'} / {r.get('start', '') or '時刻なし'}",
                f"    AI    : {a.get('title', '-')} / {a.get('venue', '') or '会場なし'} / {a.get('start', '') or '時刻なし'}"]

    for field, label in FIELDS.items():
        diffs = [(e, p) for e in compared for p in e["pairs"]
                 if p["rule"] and p["ai"] and p["fields"].get(field) in ("食い違い", "片方だけ")]
        if diffs:
            lines += ["", f"{label}が食い違った・片方だけの例（{len(diffs)} 件中 {min(len(diffs), examples)} 件）"]
            for e, p in diffs[:examples]:
                lines.append(f"  {p['date']} {p['fields'][field]} {e['url']}")
                lines += show(p)
    missing = [(e, p) for e in compared for p in e["pairs"] if not (p["rule"] and p["ai"])]
    if missing:
        lines += ["", f"日付が片方にしかない例（{len(missing)} 件中 {min(len(missing), examples)} 件）"]
        for e, p in missing[:examples]:
            lines.append(f"  {p['date']} {'ルールのみ' if p['ai'] is None else 'AI のみ'} {e['url']}")
            lines += show(p)
    if ai_only:
        lines += ["", f"AI だけが読めた投稿（{len(ai_only)} 件中 {min(len(ai_only), examples)} 件。採用した結果が正しいか確かめる）"]
        for e in ai_only[:examples]:
            for p in e["pairs"]:
                a = p["ai"] or {}
                lines.append(f"  {p['date']} {a.get('title', '-')} / {a.get('venue', '') or '会場なし'}"
                             f" / {a.get('start', '') or '時刻なし'} {e['url']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", default=DEFAULT_PATH, help="記録ファイル（既定: ~/.local/state/takaneko-fan/ai_compare.jsonl）")
    parser.add_argument("--since", help="この日以降の投稿だけ（YYYY-MM-DD）")
    parser.add_argument("--examples", type=int, default=10, help="例を出す件数（既定: 10）")
    args = parser.parse_args()
    if not os.path.exists(args.path):
        raise SystemExit(f"記録ファイルがありません: {args.path}")
    print(report(load(args.path, args.since), args.examples))


if __name__ == "__main__":
    main()
