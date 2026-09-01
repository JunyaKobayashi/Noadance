#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NOA講師データ生成スクリプト。

NOAの公開スケジュールAPIと講師ページから instructors.json を生成する。
GitHub Actions から週次で実行される。

標準ライブラリのみで動作し、Python 3.9 と 3.12 の両方で動く必要がある
(ローカルが3.9、CIが3.12のため)。
"""

import concurrent.futures
import datetime
import html as html_mod
import json
import os
import re
import statistics
import time
import urllib.request

# 講師ページのフッター。ここ以降は全ページ共通の定型文なので捨てる。
FOOTER_MARKER = "当サイトは、NOAダンスアカデミー"

# セクション本文の終端。前から順に適用し、最初に見つかった位置で打ち切る。
# "DANCE MOVIE" は "MOVIE" より先に置くこと。順序を変えると "DANCE " が残る。
BOILERPLATE_MARKERS = [
    "DANCE MOVIE",
    "同じジャンルのインストラクター",
    "このジャンルのダンサー",
    "LESSON STUDIO",
    "MOVIE",
    "LESSON ",
    "STUDIO ",
]

KEIREKI_ENDS = ["MESSAGE", "SCHEDULE", "スケジュール", "関連"]
MESSAGE_ENDS = ["経歴", "SCHEDULE", "スケジュール", "関連"]

VIDEO_RE = re.compile(r"youtube\.com/embed/([A-Za-z0-9_-]{8,})")


def _visible_text(raw_html):
    """HTMLから可視テキストだけを取り出し、フッター以降を捨てる。"""
    without_code = re.sub(
        r"(?s)<(script|style)[^>]*>.*?</\1>", " ", raw_html)
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", without_code))
    text = re.sub(r"[ \t]+", " ", text)
    cut = text.find(FOOTER_MARKER)
    if cut > 0:
        text = text[:cut]
    return text


def _section(body, start_marker, end_markers):
    """見出しから次の見出し・定型文までを本文として切り出す。"""
    start = body.find(start_marker)
    if start < 0:
        return ""
    segment = body[start + len(start_marker):]
    for marker in list(end_markers) + BOILERPLATE_MARKERS:
        found = segment.find(marker)
        if found >= 0:
            segment = segment[:found]
    return re.sub(r"\s+", " ", segment).strip().lstrip(
        "】【》〕］]：:・-–—　 ").strip()


def parse_profile(raw_html):
    """講師ページのHTMLから経歴・MESSAGE・動画IDを取り出す。

    欠損している項目は空文字・空リストになる。見出しが存在しても本文が
    空の場合に次セクションの定型文を拾わないことが要件(設計書6.2)。
    """
    videos = []
    for video_id in VIDEO_RE.findall(raw_html):
        if video_id not in videos:
            videos.append(video_id)
    body = _visible_text(raw_html)
    return {
        "keireki": _section(body, "経歴", KEIREKI_ENDS),
        "message": _section(body, "MESSAGE & MOVIE", MESSAGE_ENDS),
        "videos": videos,
    }


MAX_WEEKS = 8


def normalize_studio(tenpo_name):
    """店舗名から部屋番号を落とす。"新宿 2F" -> "新宿"。"""
    return str(tenpo_name).split()[0] if str(tenpo_name).strip() else ""


def is_golden(rec):
    """好枠(土日、または平日19時台〜21時台)かどうか。"""
    weekday = str(rec.get("LESSON_WEEKDAY", ""))
    if weekday in ("0", "6"):
        return True
    time_from = str(rec.get("LESSON_TIME_FROM", "0000"))
    try:
        hour = int(time_from[:2])
    except ValueError:
        return False
    return 19 <= hour < 22


def aggregate_week(records):
    """1週間ぶんのレッスンから講師ごとの観測値を作る。

    代講は koma に数えず daiko に分ける。代講のみの講師も名簿に残す。
    """
    seen_seq = set()
    out = {}
    for rec in records:
        seq = rec.get("SEQ")
        if seq in seen_seq:
            continue
        seen_seq.add(seq)
        code = rec.get("INSTRUCTOR_CODE")
        if not code:
            continue
        entry = out.get(code)
        if entry is None:
            entry = {
                "name": rec.get("NICKNAME") or "",
                "img": rec.get("INSTRUCTOR_IMG") or "",
                "url": rec.get("URL") or "",
                "urls": [],
                "koma": 0, "gold": 0, "daiko": 0,
                "studios": [], "genres": [], "levels": [],
            }
            out[code] = entry
        url = rec.get("URL")
        if url and url not in entry["urls"]:
            entry["urls"].append(url)
        for key, field in (("studios", "TENPO_NAME"),
                           ("genres", "GENRE_SUB_NAME"),
                           ("levels", "LEVEL_NAME")):
            value = rec.get(field) or ""
            if key == "studios":
                value = normalize_studio(value)
            if value and value not in entry[key]:
                entry[key].append(value)
        if rec.get("Y_DAIKOU_FLG"):
            entry["daiko"] += 1
        else:
            entry["koma"] += 1
            if is_golden(rec):
                entry["gold"] += 1
    for entry in out.values():
        for key in ("studios", "genres", "levels"):
            entry[key] = sorted(entry[key])
    return out


def merge_history(previous, week_key, observations, max_weeks=MAX_WEEKS):
    """今週の観測値を既存の履歴に統合する。

    koma と gold は直近 max_weeks 週の中央値を採る。履歴はその講師が
    初めて名簿に現れた週から始まるので、在籍前の週で不当に沈まない。
    max_weeks 週連続で0コマの講師は名簿から外す。
    """
    weeks = list(previous.get("weeks", []))
    same_week = bool(weeks) and weeks[-1] == week_key
    if same_week:
        weeks.pop()
    weeks.append(week_key)
    weeks = weeks[-max_weeks:]

    prev_instructors = previous.get("instructors", {})
    codes = set(prev_instructors.keys()) | set(observations.keys())
    instructors = {}
    for code in sorted(codes):
        prev = prev_instructors.get(code, {})
        obs = observations.get(code)
        koma_weeks = list(prev.get("koma_weeks", []))
        gold_weeks = list(prev.get("gold_weeks", []))
        if same_week and koma_weeks:
            koma_weeks.pop()
        if same_week and gold_weeks:
            gold_weeks.pop()
        koma_weeks.append(obs["koma"] if obs else 0)
        gold_weeks.append(obs["gold"] if obs else 0)
        koma_weeks = koma_weeks[-max_weeks:]
        gold_weeks = gold_weeks[-max_weeks:]

        if len(koma_weeks) >= max_weeks and not any(koma_weeks):
            continue  # 8週連続で登板なし。退職とみなして名簿から外す

        if obs:
            studios, genres, levels = obs["studios"], obs["genres"], obs["levels"]
            name = obs["name"] or prev.get("name", "")
            img = obs["img"] or prev.get("img", "")
            url = obs["url"] or prev.get("url", "")
            urls = obs["urls"] or list(prev.get("urls", []))
        else:
            studios = list(prev.get("studios", []))
            genres = list(prev.get("genres", []))
            levels = list(prev.get("levels", []))
            name = prev.get("name", "")
            img = prev.get("img", "")
            url = prev.get("url", "")
            urls = list(prev.get("urls", []))

        entry = {
            "name": name, "img": img, "url": url, "urls": urls,
            "koma": int(statistics.median(koma_weeks)),
            "gold": int(statistics.median(gold_weeks)),
            "koma_weeks": koma_weeks,
            "gold_weeks": gold_weeks,
            "daiko": obs["daiko"] if obs else 0,
            "studios": studios, "genres": genres, "levels": levels,
            "tenpo": len(studios),
        }
        entry["keireki"] = prev.get("keireki", "")
        entry["message"] = prev.get("message", "")
        entry["videos"] = list(prev.get("videos", []))
        instructors[code] = entry
    return weeks, instructors


# 大分類。3要素目はリクエストの genre パラメータに渡すコード。
# レスポンスの GENRE_CODE とは別の体系なので取り違えないこと。
GENRE_CATEGORIES = [
    ("hiphop", "HIP-HOP", ["01", "18"]),
    ("rhythm", "リズムトレーニング", ["09"]),
    ("jazz", "JAZZ", ["03", "15"]),
    ("locksoul", "LOCK・SOUL", ["10"]),
    ("house", "HOUSE", ["02"]),
    ("pop", "POP", ["07"]),
    ("others", "OTHERS", ["06"]),
    # 以下2つはアプリのUIには無いが、講師の稼働量を正しく数えるため取得する。
    # アプリ側の genreKeyOf は未知の大分類を others に落とすので表示は壊れない。
    ("punking", "PUNKING・FREESTYLE", ["08"]),
    ("kids", "KIDS", ["04"]),
]

# 全14店舗。設計書7.4のコード表。
ALL_LOCATIONS = ["1", "2", "3", "4", "5", "6", "7", "8",
                 "10", "11", "12", "13", "14", "15"]

# 回帰検知の許容範囲(設計書6.3)。実測値を基準にしている。
THRESHOLDS = [
    ("lessons", "週次総レッスン数", 800, 1500),
    ("instructors", "名簿の講師数", 450, 800),
    ("with_regular", "レギュラーを持つ講師数", 400, 750),
    ("genre_codes", "genre_mapの細分類コード数", 25, 70),
]
PROFILE_THRESHOLDS = [
    ("keireki_pct", "経歴あり(%)", 25, 50),
    ("message_pct", "MESSAGEあり(%)", 12, 32),
    ("video_pct", "動画あり(%)", 65, 90),
]


class GenreMapConflict(Exception):
    """1つの細分類コードが複数の大分類に現れた。対応表が一意に定まらない。"""


def derive_genre_map(tagged):
    """大分類ごとの取得結果から GENRE_CODE -> 大分類キー の対応表を作る。"""
    out = {}
    for category, records in tagged.items():
        for rec in records:
            code = str(rec.get("GENRE_CODE") or "")
            if not code:
                continue
            existing = out.get(code)
            if existing is not None and existing != category:
                raise GenreMapConflict(
                    "細分類コード %s が %s と %s の両方に出現した"
                    % (code, existing, category))
            out[code] = category
    return out


def check_thresholds(stats):
    """既知の実測レンジから外れた指標を洗い出す。空リストなら正常。"""
    violations = []
    checks = list(THRESHOLDS)
    if stats.get("profiles_refreshed"):
        checks += PROFILE_THRESHOLDS
    for key, label, low, high in checks:
        value = stats.get(key)
        if value is None or not (low <= value <= high):
            violations.append(
                "%s が想定範囲外です: %s (許容 %s〜%s)" % (label, value, low, high))
    return violations


JST = datetime.timezone(datetime.timedelta(hours=9))

API_URL = "https://r8t00r3qx7.execute-api.ap-northeast-1.amazonaws.com/PRD"
OUTPUT_PATH = "instructors.json"
PROFILE_REFRESH_DAYS = 28
FETCH_WORKERS = 6
FETCH_DELAY_SEC = 0.15
USER_AGENT = "Mozilla/5.0 (compatible; noa-schedule-liff/1.0)"


def _post_json(url, payload, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _records_from_response(payload):
    out = []
    for day in payload.get("body", {}).get("Items", []):
        for block in day.get("time_list", []):
            for rec in block.get("record", []):
                out.append(rec)
    return out


def fetch_schedule(genre_codes):
    """全14店舗ぶんの週次スケジュールを1リクエストで取る。

    is_month は必ず True。False は全件0件を返す(設計書5.1)。
    """
    payload = _post_json(API_URL, {
        "location": ALL_LOCATIONS, "genre": genre_codes,
        "brand": "1", "is_month": True})
    return _records_from_response(payload)


def collect_tagged(pairs):
    """(大分類キー, レコード列) の並びを、全レコードの和集合と内訳に畳む。

    重複排除は**大分類ごとに独立して**行う。全体で1つの集合にすると、
    同じレッスンが2つの大分類から返ってきたときに2つ目が黙って捨てられ、
    derive_genre_map の GenreMapConflict が発火しなくなる。対応表が
    一意に定まらない状況こそ検知したいので、カテゴリをまたいだ重複は
    残して次段に渡す。
    """
    tagged = {}
    by_seq = {}
    for key, records in pairs:
        bucket = tagged.setdefault(key, [])
        seen = set(rec.get("SEQ") for rec in bucket)
        for rec in records:
            seq = rec.get("SEQ")
            if seq not in seen:
                seen.add(seq)
                bucket.append(rec)
            if seq not in by_seq:
                by_seq[seq] = rec
    return list(by_seq.values()), tagged


def merge_profile(entry, parsed):
    """1講師ぶんのプロフィールを既存の値に統合する(破壊的)。

    2ジャンルを担当する講師はジャンルごとに別ページを持つため、複数回
    呼ばれる。経歴とMESSAGEは**長い方**を残し、動画IDは和集合を採る。
    後から取得したページで上書きしてはならない(空のページが勝つため)。
    通信を含まないのでテストできる。
    """
    if len(parsed["keireki"]) > len(entry.get("keireki", "")):
        entry["keireki"] = parsed["keireki"]
    if len(parsed["message"]) > len(entry.get("message", "")):
        entry["message"] = parsed["message"]
    videos = list(entry.get("videos", []))
    for video_id in parsed["videos"]:
        if video_id not in videos:
            videos.append(video_id)
    entry["videos"] = videos


def fetch_all_schedule():
    """大分類ごとに1回ずつ、計9リクエストで全レッスンを取る。

    大分類ごとに分けるのは、返ってきたレコードがどの大分類の要求に
    よるものかを記録して genre_map を導出するため。レスポンスの
    GENRE_CODE だけでは大分類が判別できない(設計書6.5)。
    9回の和集合が全レッスンになる(実測1125件)。
    """
    pairs = []
    for key, _, codes in GENRE_CATEGORIES:
        pairs.append((key, fetch_schedule(codes)))
    return collect_tagged(pairs)


def fetch_profile(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        raw = urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        # 失敗時こそ間隔を空ける。相手が不調なときに連打しないため
        time.sleep(FETCH_DELAY_SEC)
        return None
    time.sleep(FETCH_DELAY_SEC)
    return parse_profile(raw.decode("utf-8", "replace"))


def fetch_profiles(instructors):
    """講師ごとにプロフィールを取得して instructors に書き戻す。

    2ジャンル以上を担当する講師は複数のURLを持つ。経歴とMESSAGEは
    最も長いものを、動画IDは和集合を採る(設計書6.2)。

    今回の取得結果は前回値とは別のdictに積み上げ、取得できた講師だけを
    上書きする。前回値を種にして比較すると、短くなった経歴や削除された
    動画が「前回より短い/前回に無い」という理由で常に負けてしまい、
    追記オンリーになってしまうため(過去の不具合)。取得に失敗した講師は
    このdictに現れないので前回の値がそのまま残る。

    戻り値は今回プロフィールを取得できた講師数。0件なら全滅であり、
    呼び出し側は profiles_generated_at を更新すべきではない。
    """
    jobs = []
    for code, entry in instructors.items():
        for url in entry.get("urls") or ([entry["url"]] if entry.get("url") else []):
            jobs.append((code, url))

    def work(job):
        return job[0], fetch_profile(job[1])

    fresh = {}
    with concurrent.futures.ThreadPoolExecutor(FETCH_WORKERS) as pool:
        for code, parsed in pool.map(work, jobs):
            if not parsed:
                continue
            blank = {"keireki": "", "message": "", "videos": []}
            merge_profile(fresh.setdefault(code, blank), parsed)
    # 取得できた講師だけ差し替える。失敗した講師は前回の値を保つ
    for code, values in fresh.items():
        instructors[code].update(values)
    return len(fresh)


def _load_previous(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _needs_profile_refresh(previous, now):
    stamp = previous.get("profiles_generated_at")
    if not stamp:
        return True
    try:
        last = datetime.datetime.strptime(stamp[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return (now.date() - last).days >= PROFILE_REFRESH_DAYS


def _week_key(now):
    monday = now - datetime.timedelta(days=now.weekday())
    return monday.strftime("%Y%m%d")


def _percent(count, total):
    return int(round(100.0 * count / total)) if total else 0


def _without_timestamps(doc):
    """generated_at / profiles_generated_at を除いた比較用のコピーを返す。

    タイムスタンプ以外に差分が無ければ書き出しをスキップするために使う
    (設計書: 空コミットを積まない)。
    """
    return dict((k, v) for k, v in doc.items()
                if k not in ("generated_at", "profiles_generated_at"))


def main():
    now = datetime.datetime.now(JST)
    previous = _load_previous(OUTPUT_PATH)
    refresh_profiles = _needs_profile_refresh(previous, now)

    print("スケジュールを取得中(大分類ごとに9リクエスト)...")
    records, tagged = fetch_all_schedule()
    print("  レッスン %d件" % len(records))

    genre_map = derive_genre_map(tagged)
    print("  細分類コード %d件" % len(genre_map))

    observations = aggregate_week(records)
    weeks, instructors = merge_history(previous, _week_key(now), observations)
    print("  講師 %d名" % len(instructors))

    profiles_fetched = 0
    if refresh_profiles:
        print("プロフィールを取得中(%d件)..." % len(instructors))
        profiles_fetched = fetch_profiles(instructors)
        if profiles_fetched == 0:
            print("警告: プロフィールが1件も取得できませんでした。"
                  "profiles_generated_at は更新せず、前回の値を引き継ぎます")
    else:
        print("プロフィールは前回の結果を引き継ぎます")

    total = len(instructors)
    stats = {
        "lessons": len(records),
        "instructors": len(instructors),
        "with_regular": sum(1 for e in instructors.values() if e["koma"] > 0),
        "keireki_pct": _percent(
            sum(1 for e in instructors.values() if len(e.get("keireki", "")) > 30), total),
        "message_pct": _percent(
            sum(1 for e in instructors.values() if len(e.get("message", "")) > 15), total),
        "video_pct": _percent(
            sum(1 for e in instructors.values() if e.get("videos")), total),
        "genre_codes": len(genre_map),
        "profiles_refreshed": refresh_profiles,
    }
    print("統計: %s" % stats)

    violations = check_thresholds(stats)
    if violations:
        print("\n取得結果が想定範囲から外れています。JSONは更新しません:")
        for line in violations:
            print("  - %s" % line)
        return 1

    output = {
        "generated_at": now.isoformat(timespec="seconds"),
        "profiles_generated_at": (
            now.isoformat(timespec="seconds") if profiles_fetched
            else previous.get("profiles_generated_at")),
        "weeks": weeks,
        "source_lessons": len(records),
        "genre_map": genre_map,
        "instructors": instructors,
    }

    if previous and _without_timestamps(previous) == _without_timestamps(output):
        print("\nデータに変更がないため %s は更新しません" % OUTPUT_PATH)
        return 0

    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print("\n%s を書き出しました" % OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
