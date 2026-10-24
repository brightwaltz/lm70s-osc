# LM70S_OSC 設定 — 変更したら Live を再起動（または Control Surface を一度 None に戻して再選択）

# このトラック名の最初の Rack（Instrument Rack 推奨）の Macro を監視する（大文字小文字は無視）
TRACK_NAME = "LM70S"

# OSC 送信先（osc2dmx.py の --listen と合わせる）
OSC_HOST = "127.0.0.1"
OSC_PORT = 9000

# 送信アドレスの先頭。複数台を同時に動かすなら "/all"
OSC_PREFIX = "/lm70s"

# Macro 番号 -> パラメータ名。APC40 mkII の Device ノブは 1-8、BANK → で 9-16。
# Macro の名前をパラメータ名（例: "Tilt"）に変えると、番号に関係なくその名前が優先される。
MACROS = {
    1: "pan",
    2: "tilt",
    3: "dimmer",
    4: "strobe",
    5: "red",
    6: "green",
    7: "blue",
    8: "white",
    9: "speed",
    10: "color",
    11: "color_speed",
    12: "program",
}

# トラックのボリュームフェーダーを全体の明るさ（0dB 以上で 100%）として送る。None で無効
FADER_PARAM = "master"
FADER_UNITY = 0.85  # Live のボリューム値で 0dB に当たる位置

# update_display は約 100ms ごとに呼ばれる
STRUCTURE_CHECK_TICKS = 5   # トラック/Rack/Macro 名の変化を確認する間隔（約 0.5 秒）
RESEND_TICKS = 10           # 全値を再送する間隔（約 1 秒、ブリッジを後から起動しても同期する）
