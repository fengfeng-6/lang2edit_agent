"""抽帧导出：把指定时间点附近的帧存成 jpg，供人工核对检测候选。

    python scripts/dump_frames.py <video> <t1> <t2> ... --out frames/ [--window 0.3]

需要 PyAV（pip install -e ".[video]"）。检出事件与人眼判断不一致时，
对 peak 时刻抽帧核对是最快的真值判定手段。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("times", nargs="+", type=float)
    ap.add_argument("--out", default="frames")
    ap.add_argument("--window", type=float, default=0.0,
                    help="每个时间点 ±window 秒也各抽一帧")
    args = ap.parse_args()

    import av  # noqa: E402

    offsets = [-args.window, 0.0, args.window] if args.window else [0.0]
    targets = sorted({t + off for t in args.times for off in offsets})
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with av.open(args.video) as container:
        stream = container.streams.video[0]
        pending = list(targets)
        for frame in container.decode(stream):
            if not pending:
                break
            t = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
            if t < pending[0] - 1.0 / 30:
                continue
            if t >= pending[0] - 1.0 / 60:
                hit = pending.pop(0)
                img = frame.to_image()  # PIL
                path = out_dir / f"frame_{hit:06.2f}s.jpg"
                img.save(path, quality=70)
                print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
