from __future__ import annotations

# Suumo の access 欄に出る路線名 → Slack カスタム絵文字名 (コロン無し)。
# 絵文字本体は投稿先ワークスペースに登録済み (tools/slack-emoji/ で生成・アップロード)。
# notifier はここで得た名前を `:name:` として出力するだけで、実描画は投稿先依存。
#
# キーは正規化済み表記。ルックアップ前に _normalize で全角 JR を半角化するため、
# Suumo が実際に返す 'ＪＲ山手線' (全角) も 'JR山手線' として一致する。
_ROUTE_EMOJI: dict[str, str] = {
    # --- 東京メトロ ---
    "東京メトロ銀座線": "metro-ginza",
    "東京メトロ丸ノ内線": "metro-marunouchi",
    "東京メトロ日比谷線": "metro-hibiya",
    "東京メトロ東西線": "metro-tozai",
    "東京メトロ千代田線": "metro-chiyoda",
    "東京メトロ有楽町線": "metro-yurakucho",
    "東京メトロ半蔵門線": "metro-hanzomon",
    "東京メトロ南北線": "metro-namboku",
    "東京メトロ副都心線": "metro-fukutoshin",
    # --- 都営・都電 ---
    "都営浅草線": "toei-asakusa",
    "都営三田線": "toei-mita",
    "都営新宿線": "toei-shinjuku",
    "都営大江戸線": "toei-oedo",
    "都電荒川線": "toden-arakawa",
    # --- JR (キーは半角 JR。全角 ＪＲ は _normalize で吸収) ---
    "JR山手線": "jr-yamanote",
    "JR京浜東北線": "jr-keihintohoku",
    "JR埼京線": "jr-saikyo",
    "JR中央線": "jr-chuo-rapid",  # Suumo「ＪＲ中央線」→ 中央線快速ロゴ
    "JR総武線": "jr-chuo-sobu",  # Suumo「ＪＲ総武線」→ 中央・総武各停ロゴ
    "湘南新宿ライン": "jr-shonan-shinjuku",
    "JR湘南新宿ライン": "jr-shonan-shinjuku",
    # --- 東急 ---
    "東急東横線": "tokyu-toyoko",
    "東急田園都市線": "tokyu-denentoshi",
    "東急世田谷線": "tokyu-setagaya",
    "東急目黒線": "tokyu-meguro",
    # --- 京王 ---
    "京王線": "keio",
    "京王新線": "keio",
    "京王井の頭線": "keio-inokashira",
    # --- 小田急 ---
    "小田急線": "odakyu",
    "小田急小田原線": "odakyu",
    # --- 西武 ---
    "西武池袋線": "seibu-ikebukuro",
    "西武有楽町線": "seibu-ikebukuro",
    "西武豊島線": "seibu-ikebukuro",
    "西武新宿線": "seibu-shinjuku",
    # --- 東武 ---
    "東武東上線": "tobu-tojo",
}


def _normalize(line_name: str) -> str:
    return line_name.strip().replace("ＪＲ", "JR")


def route_emoji(line_name: str) -> str | None:
    """路線名に対応する Slack 絵文字名 (コロン無し) を返す。未登録なら None。"""
    return _ROUTE_EMOJI.get(_normalize(line_name))
