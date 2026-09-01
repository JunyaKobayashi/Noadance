/* NOA今日のスケジュール — 設定値と純粋関数
 *
 * index.html から <script src> で読み込まれ、tests/app-logic.test.mjs から
 * node:vm で評価される。そのため DOM (document / window / liff) には
 * 一切触れないこと。構文は index.html に合わせて ES5 に限定する。
 */

var API_URL = "https://r8t00r3qx7.execute-api.ap-northeast-1.amazonaws.com/PRD";

/* 取得対象。ここの genre は「大分類」コードであり、
 * レスポンスの GENRE_CODE(細分類)とは別の体系である。 */
var LOCATIONS = ["7", "10", "2", "8", "12", "11"]; // 中目黒, 都立大, 恵比寿, 新宿, 原宿, 赤坂
var GENRES = [
  "01", "18", // HIP-HOP
  "09",       // リズムトレーニング
  "03", "15", // JAZZ
  "10",       // LOCK・SOUL
  "02",       // HOUSE
  "07",       // POP
  "06"        // OTHERS
];

var STUDIOS = [
  { key: "中目黒", color: "var(--studio-nakameguro)" },
  { key: "都立大", color: "var(--studio-toritsudai)" },
  { key: "恵比寿", color: "var(--studio-ebisu)" },
  { key: "新宿", color: "var(--studio-shinjuku)" },
  { key: "原宿", color: "var(--studio-harajuku)" },
  { key: "赤坂", color: "var(--studio-akasaka)" }
];

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
