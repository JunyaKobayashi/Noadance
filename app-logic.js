/* NOA今日のスケジュール — 設定値と純粋関数
 *
 * index.html から <script src> で読み込まれ、tests/app-logic.test.mjs から
 * node:vm で評価される。そのため DOM (document / window / liff) には
 * 一切触れないこと。構文は index.html に合わせて ES5 に限定する。
 */

var API_URL = "https://r8t00r3qx7.execute-api.ap-northeast-1.amazonaws.com/PRD";

/* 取得対象。ここの genre は「大分類」コードであり、
 * レスポンスの GENRE_CODE(細分類)とは別の体系である。 */
var GENRES = [
  "01", "18", // HIP-HOP
  "09",       // リズムトレーニング
  "03", "15", // JAZZ
  "10",       // LOCK・SOUL
  "02",       // HOUSE
  "07",       // POP
  "06"        // OTHERS
];

/* 店舗の定義。key は TENPO_NAME と照合する名前かつチップの表示名、
 * code は本編7.4のリクエスト用店舗コード。店舗を足すときはここに1行足し、
 * index.html に色をライト・ダークで1つずつ足せばよい。 */
var STUDIOS = [
  { key: "中目黒",   code: "7",  color: "var(--studio-nakameguro)" },
  { key: "都立大",   code: "10", color: "var(--studio-toritsudai)" },
  { key: "恵比寿",   code: "2",  color: "var(--studio-ebisu)" },
  { key: "新宿",     code: "8",  color: "var(--studio-shinjuku)" },
  { key: "原宿",     code: "12", color: "var(--studio-harajuku)" },
  { key: "赤坂",     code: "11", color: "var(--studio-akasaka)" },
  { key: "秋葉原",   code: "1",  color: "var(--studio-akihabara)" },
  { key: "御茶ノ水", code: "14", color: "var(--studio-ochanomizu)" },
  { key: "池袋",     code: "4",  color: "var(--studio-ikebukuro)" }
];

/* 取得対象。STUDIOS から導出し、店舗の追加を1箇所で済ませる。
 * ここの code はリクエストの location であり、レスポンスの
 * GENRE_CODE とも、大分類の genre コードとも無関係である。 */
var LOCATIONS = STUDIOS.map(function (s) { return s.code; });

var GENRE_GROUPS = [
  { key: "hiphop", label: "HIP-HOP" },
  { key: "rhythm", label: "リズムトレーニング" },
  { key: "jazz", label: "JAZZ" },
  { key: "locksoul", label: "LOCK・SOUL" },
  { key: "house", label: "HOUSE" },
  { key: "pop", label: "POP" },
  { key: "others", label: "OTHERS" }
];

/* レスポンスの GENRE_CODE(細分類) -> 大分類キー。
 * 大分類ごとにAPIを叩いて実測した対応表(設計書6.5)。
 * NOAが細分類を追加した場合、genreKeyOf は othersに分類する。ただし
 * othersは起動時デフォルトでOFFのため、そのレッスンはユーザーがOTHERSチップを
 * 手動でONにするまで画面に表示されない。 */
var GENRE_MAP = {
  "01": "hiphop", "02": "hiphop", "03": "hiphop",
  "10": "hiphop", "26": "hiphop", "98": "hiphop",
  "04": "rhythm",
  "06": "jazz", "08": "jazz", "11": "jazz", "28": "jazz",
  "31": "jazz", "32": "jazz", "78": "jazz", "83": "jazz",
  "87": "jazz", "97": "jazz", "101": "jazz", "102": "jazz",
  "13": "locksoul", "14": "locksoul", "34": "locksoul",
  "15": "house",
  "17": "pop", "40": "pop",
  "16": "others", "19": "others", "77": "others", "193": "others"
};

/* 起動時にONにする絞り込み。残りはOFFで、チップをタップして追加できる。 */
var DEFAULT_STUDIOS = ["中目黒", "都立大", "恵比寿"];
var DEFAULT_GENRES = ["hiphop", "rhythm"];

function studioKey(tenpoName) {
  return String(tenpoName).split(/\s+/)[0];
}

function genreKeyOf(genreCode) {
  var key = String(genreCode);
  return Object.prototype.hasOwnProperty.call(GENRE_MAP, key) ? GENRE_MAP[key] : "others";
}

/* instructors.json の中身。起動時ではなく、初めて必要になった時に読み込む。
 * 484KBあるため、毎日使う「今日のスケジュール」の表示を待たせない。 */
var INSTRUCTORS = null;
var INSTRUCTORS_GENERATED_AT = "";

/* 読み込んだデータをアプリに反映する。妥当なデータなら true を返す。
 *
 * genre_map はアプリが取得する9店舗の範囲では現れない細分類コードも含む
 * (99=ジャズヒップホップ、112/113=リズムトレーニングなど)。静的な GENRE_MAP に
 * 統合することで、店舗を増やしたときに others へ落ちるのを防ぐ。 */
function applyInstructorData(data) {
  if (!data || typeof data !== "object") return false;
  var instructors = data.instructors;
  if (!instructors || typeof instructors !== "object" ||
      Object.prototype.toString.call(instructors) === "[object Array]") {
    return false;
  }
  var map = data.genre_map;
  if (map && typeof map === "object") {
    for (var code in map) {
      if (Object.prototype.hasOwnProperty.call(map, code)) {
        GENRE_MAP[String(code)] = map[code];
      }
    }
  }
  INSTRUCTORS_GENERATED_AT = data.generated_at || "";
  INSTRUCTORS = instructors;
  return true;
}

function instructorOf(code) {
  if (!INSTRUCTORS || code == null) return null;
  var key = String(code);
  return Object.prototype.hasOwnProperty.call(INSTRUCTORS, key)
    ? INSTRUCTORS[key] : null;
}

/* APIレスポンスが「HTTP 200 だが中身が空」かどうかを判定する。
 * NOAのAPIは条件によっては構造上正常な空JSONを返すため、
 * これをエラーとして扱わないと「レッスンなし」と誤表示される。 */
function isEmptySchedule(json) {
  var items = json && json.body && json.body.Items;
  if (!items || !items.length) return true;
  return items.every(function (day) {
    if (!day.time_list || !day.time_list.length) return true;
    return day.time_list.every(function (block) {
      return !block.record || !block.record.length;
    });
  });
}

var KEIREKI_MIN = 30;
var MESSAGE_MIN = 15;
var INFO_VIDEO_MAX = 3;

/* 人気度は合成スコアにせず実数のまま出す(設計書3.2)。
 * 中央値1.5コマ・週5コマ以上は2%という偏った分布なので、実数で十分に差が見える。 */
function popularityBadges(entry) {
  if (!entry) return [];
  var koma = entry.koma || 0;
  if (!koma) return [(entry.daiko || 0) > 0 ? "今週は代講のみ" : "今週は担当なし"];
  var out = ["週" + koma + "コマ", (entry.tenpo || 0) + "店舗"];
  if (entry.gold) out.push("好枠" + entry.gold);
  return out;
}

function hasProfile(entry) {
  if (!entry) return false;
  return (entry.keireki || "").length > KEIREKI_MIN ||
         (entry.message || "").length > MESSAGE_MIN ||
         ((entry.videos || []).length > 0);
}

/* 「情報が多い順」の並べ替えに使う補助的な指標。人気度とは別物。 */
function infoScore(entry) {
  if (!entry) return 0;
  var score = 0;
  if ((entry.keireki || "").length > KEIREKI_MIN) score += 1;
  if ((entry.message || "").length > MESSAGE_MIN) score += 1;
  score += Math.min((entry.videos || []).length, INFO_VIDEO_MAX);
  return score;
}

/* 恣意的な重み付けを避けるため、合成スコアではなく辞書式に比較する。 */
function instructorSortKey(entry) {
  if (!entry) return [0, 0, 0];
  return [-(entry.koma || 0), -(entry.tenpo || 0), -(entry.gold || 0)];
}

function videoThumb(videoId) {
  return "https://i.ytimg.com/vi/" + videoId + "/mqdefault.jpg";
}

/* 開始時刻での4区分(設計書7.2)。 */
var TIME_BANDS = [
  { key: "noon", label: "昼", from: 0, to: 12 },
  { key: "afternoon", label: "午後", from: 12, to: 17 },
  { key: "evening", label: "夕方", from: 17, to: 19 },
  { key: "night", label: "夜", from: 19, to: 24 }
];

function timeBandOf(hhmm) {
  var hour = parseInt(String(hhmm).slice(0, 2), 10);
  if (isNaN(hour)) return TIME_BANDS[0].key;
  for (var i = 0; i < TIME_BANDS.length; i++) {
    if (hour >= TIME_BANDS[i].from && hour < TIME_BANDS[i].to) return TIME_BANDS[i].key;
  }
  return TIME_BANDS[TIME_BANDS.length - 1].key;
}

/* 条件に合うレッスンを担当している講師を集める。
 * 空の配列はその軸で絞り込まない。同一軸内はOR、軸をまたぐとAND。 */
function collectInstructors(days, filters) {
  var out = [];
  var index = {};
  if (!days || !days.length) return out;
  var f = filters || {};
  function passes(list, value) {
    return !list || !list.length || list.indexOf(value) >= 0;
  }
  days.forEach(function (day) {
    if (!passes(f.youbi, day.youbi)) return;
    (day.blocks || []).forEach(function (block) {
      (block.lessons || []).forEach(function (l) {
        if (!passes(f.bands, timeBandOf(l.s))) return;
        if (!passes(f.studios, studioKey(l.studio))) return;
        if (!passes(f.levels, l.level)) return;
        if (!passes(f.genres, genreKeyOf(l.gcode))) return;
        var code = l.icode;
        var found = Object.prototype.hasOwnProperty.call(index, code) ? index[code] : null;
        if (!found) {
          found = { code: code, name: l.teacher, lessons: [] };
          index[code] = found;
          out.push(found);
        }
        found.lessons.push({
          youbi: day.youbi, s: l.s, e: l.e, studio: l.studio,
          genre: l.genre, level: l.level, daikou: !!l.daikou
        });
      });
    });
  });
  return out;
}
