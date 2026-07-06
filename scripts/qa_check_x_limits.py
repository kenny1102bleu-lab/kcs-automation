"""
QA: scripts/common/x_limits.py の回帰テスト。
`python scripts/qa_check_x_limits.py` で単体実行できる。

背景: HALの日本語+繁體字併記投稿は、Xの実際の文字数上限(280ユニット、
全角文字は1文字=2ユニット換算)を大幅に超過していた実例（553/280）が
見つかったため、このバリデーションを追加した。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.common.x_limits import weighted_length, count_hashtags, validate

CASES = [
    ("半角英数字+ハッシュタグ3個で280以内", "a" * 260 + " #a #b #c", None, True),
    ("半角281文字は超過(ハッシュタグ抜きでも既に超過)", "a" * 281, 281, False),
    ("全角90文字+ハッシュタグ3個は280以内", "あ" * 90 + " #a #b #c", None, True),
    ("全角141文字で超過", "あ" * 141, 282, False),
    (
        "実例: HALバイリンガル投稿(旧・過去に生成された実例、553ユニットで超過)",
        "今日はちょっと大人っぽいワントーンコーデにしてみたよ✨ 大好きな代官山でカフェ巡り☕️ "
        "暖かい日差しが気持ちよくて、思わず笑顔になっちゃった☺️ 最近、新しいお仕事も増えてきて、"
        "毎日充実してるなぁって思うの。もっと頑張って、期待に応えられるモデルになりたいな！"
        "みんなにも感謝だよ💕\n\n"
        "今天嘗試了比較成熟的同色系穿搭✨ 在最喜歡的代官山享受咖啡巡禮☕️ "
        "溫暖的陽光讓人心情很好，不自覺就笑了出來☺️ 最近新工作也越來越多，"
        "覺得每天都過得很充實呢。我會更努力，成為能不負眾望的模特兒！也感謝大家的支持💕\n\n"
        "#今日のコーデ #代官山カフェ #ワントーンコーデ #モデルの卵 #ハル活",
        None, False,
    ),
    (
        "実例: すなくんラーメン投稿(実例、253ユニットで規定内)",
        "辛ラーメンが8月から値上げされるそうです！😳\n\n"
        "ピリッとした辛さとうまみがクセになる人気の袋麺。値上げ前にまとめ買いを検討している方も"
        "多いのではないでしょうか？💦\n\n気になる方はチェックしてみてくださいね😊\n"
        "#辛ラーメン #まとめ買い #値上げ前に #激辛グルメ #PR",
        253, True,
    ),
]

HASHTAG_CASES = [
    ("ハッシュタグ2個は規定外", "本文 #a #b", False),
    ("ハッシュタグ3個は規定内", "本文 #a #b #c", True),
    ("ハッシュタグ5個は規定内", "本文 #a #b #c #d #e", True),
    ("ハッシュタグ6個は規定外", "本文 #a #b #c #d #e #f", False),
]


def run() -> int:
    failures = []

    for desc, text, expected_wlen, expected_ok in CASES:
        wlen = weighted_length(text)
        ok, reason = validate(text)
        wlen_status = "OK" if (expected_wlen is None or wlen == expected_wlen) else "FAIL"
        ok_status = "OK" if ok == expected_ok else "FAIL"
        print(f"[{wlen_status}/{ok_status}] {desc}: weighted={wlen} (expected={expected_wlen}) "
              f"valid={ok} (expected={expected_ok}) reason={reason}")
        if wlen_status == "FAIL" or ok_status == "FAIL":
            failures.append(desc)

    for desc, text, expected_ok in HASHTAG_CASES:
        # 十分に短い本文で文字数エラーを排除し、ハッシュタグ数のみを検証
        ok, reason = validate(text)
        status = "OK" if ok == expected_ok else "FAIL"
        print(f"[{status}] {desc}: tags={count_hashtags(text)} valid={ok} (expected={expected_ok}) reason={reason}")
        if status == "FAIL":
            failures.append(desc)

    total = len(CASES) + len(HASHTAG_CASES)
    print(f"\n{total - len(failures)}/{total} passed")
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
