"""Chrome ウェブストア用の画像をすべて再生成するスクリプト。

使い方（リポジトリのルートで実行）:
    python store/src/render.py              # すべて生成
    python store/src/render.py shot-1 promo-small   # 一部だけ生成

必要なもの: Python 3 + Pillow、Google Chrome（または Microsoft Edge）。
- store/src/*.html をヘッドレス Chrome でスクリーンショットし、
  Pillow でアルファチャンネルなしの 24bit PNG（RGB）に変換して store/assets/ に保存します。
- icons/icon128.png から、96x96 のアートワーク + 四辺 16px の透明余白を持つ
  ストア用アイコン store/assets/icon-128.png を作ります。
  元アイコンは奥のカード部分が半透明（背景が透ける）になっており、暗い背景では
  灰色に見えてしまうため、明るい背景で見たときと同じ見た目になるよう
  内側の半透明部分を白で合成して不透明化しています（外形の角丸のアンチエイリアスは維持）。
  同じ処理をしたアートワークを store/src/icon-art.png として書き出し、各 HTML で使用します。
- 起動したブラウザは、このスクリプトが起動したプロセス（PID）だけを対象に終了処理します。
  一時プロファイルは毎回新しく作成し、終了後に削除します。

環境変数:
    CHROME_PATH    使用するブラウザの実行ファイル（省略時は自動検出）
    RENDER_TMPDIR  一時プロファイルを作る場所（省略時は OS の一時フォルダ）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parent
STORE = SRC.parent
ROOT = STORE.parent
ASSETS = STORE / "assets"
ICON_SRC = ROOT / "icons" / "icon128.png"

# (名前, HTML, 出力ファイル, 幅, 高さ)
JOBS = [
    ("shot-1", "shot-1.html", "screenshot-1.png", 1280, 800),
    ("shot-2", "shot-2.html", "screenshot-2.png", 1280, 800),
    ("shot-3", "shot-3.html", "screenshot-3.png", 1280, 800),
    ("shot-4", "shot-4.html", "screenshot-4.png", 1280, 800),
    ("shot-5", "shot-5.html", "screenshot-5.png", 1280, 800),
    ("promo-small", "promo-small.html", "promo-small-440x280.png", 440, 280),
    ("promo-marquee", "promo-marquee.html", "promo-marquee-1400x560.png", 1400, 560),
]

ICON_OUT = "icon-128.png"
ICON_ART_FILE = SRC / "icon-art.png"  # HTML から参照する不透明化済みアートワーク
ICON_CANVAS = 128
ICON_ART = 96


def find_browser() -> str:
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).is_file():
        return env
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(local, r"Google\Chrome\Application\chrome.exe") if local else "",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    for name in ("google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    sys.exit("Chrome / Edge が見つかりません。環境変数 CHROME_PATH で指定してください。")


def kill_tree(pid: int) -> None:
    """自分が起動したプロセス（とその子）だけを終了する。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def screenshot(browser: str, html: Path, out_png: Path, width: int, height: int) -> Image.Image:
    tmp_root = os.environ.get("RENDER_TMPDIR") or None
    if tmp_root:
        Path(tmp_root).mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="cws-render-", dir=tmp_root)
    raw = Path(profile) / "raw.png"
    args = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--disable-lcd-text",  # サブピクセル（色にじみ）ではなくグレースケールのアンチエイリアスにする
        "--force-device-scale-factor=1",
        f"--window-size={width},{height}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--mute-audio",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=1500",
        f"--screenshot={raw}",
        html.resolve().as_uri(),
    ]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)  # 自分が起動した PID のツリーだけを終了
            proc.wait(timeout=30)
            raise RuntimeError(f"タイムアウトしました: {html.name}")
        if not raw.is_file():
            raise RuntimeError(f"スクリーンショットが作成されませんでした: {html.name}")
        with Image.open(raw) as im:
            im.load()
            img = im.copy()
    finally:
        # プロファイルのロック解除待ち（自分が起動したプロセスは既に終了している）
        for _ in range(10):
            shutil.rmtree(profile, ignore_errors=True)
            if not Path(profile).exists():
                break
            time.sleep(0.3)

    if img.size != (width, height):
        raise AssertionError(f"{html.name}: サイズが不正です {img.size} != {(width, height)}")

    # アルファチャンネルを除去して 24bit RGB に
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        img = bg
    else:
        img = img.convert("RGB")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, format="PNG", optimize=True)
    return img


def solid_artwork() -> Image.Image:
    """元アイコンの「内側の半透明部分」だけを白背景で合成して不透明にする。

    キャンバスの外周からアルファ < 255 の画素をたどって到達できる領域（角丸の外側と
    そのアンチエイリアス）は元のまま残し、それ以外の半透明画素（奥のカード）を
    白の上に合成した色・アルファ 255 に置き換える。明るい背景での見た目と同一になる。
    """
    with Image.open(ICON_SRC) as im:
        art = im.convert("RGBA")
    w, h = art.size
    px = art.load()
    outside = bytearray(w * h)
    stack = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    while stack:
        x, y = stack.pop()
        if not (0 <= x < w and 0 <= y < h) or outside[y * w + x]:
            continue
        if px[x, y][3] == 255:
            continue
        outside[y * w + x] = 1
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 255 and not outside[y * w + x]:
                k = a / 255.0
                px[x, y] = (
                    round(r * k + 255 * (1 - k)),
                    round(g * k + 255 * (1 - k)),
                    round(b * k + 255 * (1 - k)),
                    255,
                )
    return art


def build_icon(out_png: Path) -> Image.Image:
    """96x96 のアートワークを 128x128 の透明キャンバス中央に配置する。"""
    art = solid_artwork()
    # 透明部分の色にじみを防ぐため、乗算済みアルファで縮小する
    art = art.convert("RGBa").resize((ICON_ART, ICON_ART), Image.Resampling.LANCZOS).convert("RGBA")
    canvas = Image.new("RGBA", (ICON_CANVAS, ICON_CANVAS), (0, 0, 0, 0))
    pad = (ICON_CANVAS - ICON_ART) // 2
    canvas.paste(art, (pad, pad))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png, format="PNG", optimize=True)
    return canvas


def verify(path: Path, size: tuple[int, int], mode: str) -> str:
    with Image.open(path) as im:
        assert im.size == size, f"{path.name}: {im.size} != {size}"
        assert im.mode == mode, f"{path.name}: mode {im.mode} != {mode}"
        return f"{path.relative_to(ROOT).as_posix():44s} {im.size[0]}x{im.size[1]}  {im.mode}"


def main(argv: list[str]) -> int:
    wanted = set(argv)
    jobs = [j for j in JOBS if not wanted or j[0] in wanted]
    do_icon = not wanted or "icon" in wanted
    unknown = wanted - {j[0] for j in JOBS} - {"icon"}
    if unknown:
        sys.exit(f"不明な名前: {', '.join(sorted(unknown))}")

    report = []
    # HTML が参照する icon-art.png は毎回作り直す（元アイコンの変更を反映するため）
    solid_artwork().save(ICON_ART_FILE, format="PNG", optimize=True)
    if do_icon:
        build_icon(ASSETS / ICON_OUT)
        report.append(verify(ASSETS / ICON_OUT, (ICON_CANVAS, ICON_CANVAS), "RGBA"))

    if jobs:
        browser = find_browser()
        print(f"browser: {browser}")
        for name, html, out, w, h in jobs:
            t0 = time.time()
            screenshot(browser, SRC / html, ASSETS / out, w, h)
            report.append(verify(ASSETS / out, (w, h), "RGB"))
            print(f"  rendered {name} ({time.time() - t0:.1f}s)")

    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
