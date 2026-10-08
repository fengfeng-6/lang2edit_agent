# -*- coding: utf-8 -*-
"""E2E 真实视频冒烟：模块一~六全链路（含反馈轮，在超算上运行，PYTHONPATH=src）。

模块六 FeedbackSession 编排全链：
    start(真实视频, 首句) — 模块一 parse(LLM) → 模块二 analyze(MediaPipe/librosa)
        → 增量查询 → 模块三 plan/materialize → 模块四 resolve(导入素材)
        → 模块五 apply → rev_0001
    reply × N            — IntentPatch → patch_flow → replan → 增量素材 → apply
    undo / state         — git-revert 式快照回滚

素材预置（模块四 user provider 检索源）：
    heart.png / star.png（PIL 真实 PNG，has_alpha）+
    music_a.wav / music_b.wav（stdlib wave，不同频率/时长保证 hash 不同）。

产物落在 <repo>/workspace/e2e_session/（gitignored）。打印每阶段摘要 JSON。
"""

import json
import math
import os
import struct
import sys
import traceback
import wave
from pathlib import Path

REPO = Path("/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/lang2edit_agent_gallant")
VIDEO = "/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/lang2edit_agent/data/test_video.mp4"
MODELS = "/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/lang2edit_agent/data/models"
WS = REPO / "workspace" / "e2e_session"
PID = "proj_session"
UTTERANCE = "每次比心时出现粉色爱心，视频结尾加上谢谢观看，加一段欢快的音乐"

os.environ["VU_MODEL_DIR"] = MODELS  # .env 里可能是相对路径，强制覆盖
sys.path.insert(0, str(REPO / "src"))

summary = {}


def dump(obj):
    from gesture_intent.models import model_dump

    try:
        return model_dump(obj)
    except Exception:
        return str(obj)


def step(name, fn):
    try:
        info = fn()
        summary[name] = {"ok": True, **(info or {})}
        print(f"[{name}] OK {json.dumps(summary[name], ensure_ascii=False, default=str)[:900]}")
        return summary[name]
    except Exception as exc:
        summary[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(f"[{name}] FAIL {exc}")
        traceback.print_exc()
        raise


def _heart_png(path):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pink = (255, 105, 180, 255)
    d.ellipse([40, 40, 136, 136], fill=pink)
    d.ellipse([120, 40, 216, 136], fill=pink)
    d.polygon([(48, 100), (208, 100), (128, 224)], fill=pink)
    img.save(path)


def _star_png(path):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r1, r2 = 128, 128, 110, 48
    pts = []
    for i in range(10):
        r = r1 if i % 2 == 0 else r2
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(pts, fill=(255, 215, 0, 255))
    img.save(path)


def _wav(path, freq=440.0, seconds=5.0, rate=22050):
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        buf = bytearray()
        for i in range(frames):
            v = int(16000 * math.sin(2 * math.pi * freq * i / rate))
            buf += struct.pack("<h", v)
        fh.writeframes(bytes(buf))


state = {}


# ---------- 准备素材 + 模块四 user provider 检索源 ----------
def prep():
    from asset_manager import AssetManager
    from video_understanding import VideoUnderstanding
    from edit_executor import EditingExecutor
    from session_feedback import FeedbackSession

    asset_dir = WS / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    heart = asset_dir / "heart.png"
    star = asset_dir / "star.png"
    music_a = asset_dir / "bgm_a.wav"
    music_b = asset_dir / "bgm_b.wav"
    _heart_png(heart)
    _star_png(star)
    _wav(music_a, freq=440.0, seconds=5.0)
    _wav(music_b, freq=660.0, seconds=7.0)

    mgr = AssetManager(workspace_root=str(WS), project_id=PID)
    recs = [
        mgr.import_user_asset(str(heart), caption="粉色爱心贴纸",
                              tags=["爱心", "粉色", "贴纸"],
                              asset_type="sticker", media_type="image"),
        mgr.import_user_asset(str(star), caption="黄色星星贴纸",
                              tags=["星星", "贴纸"],
                              asset_type="sticker", media_type="image"),
        mgr.import_user_asset(str(music_a), caption="欢快背景音乐A",
                              tags=["音乐", "欢快", "bgm", "背景音乐"],
                              asset_type="music", media_type="audio"),
        mgr.import_user_asset(str(music_b), caption="轻快背景音乐B",
                              tags=["音乐", "欢快", "轻快", "bgm"],
                              asset_type="music", media_type="audio"),
    ]
    session = FeedbackSession(
        WS, PID,
        vu=VideoUnderstanding(workspace_dir=str(WS / PID)),
        asset_manager=mgr,
        executor=EditingExecutor(workspace_root=str(WS)),
    )
    state["session"] = session
    return {"imported": [r.asset_uid for r in recs],
            "llm_intent": bool(os.environ.get("INTENT_LLM_API_KEY")),
            "llm_planner": bool(os.environ.get("PLANNER_LLM_API_KEY"))}


def _brief(result):
    ex = result.execution
    return {
        "status": str(result.status.value if hasattr(result.status, "value") else result.status),
        "turn": result.turn,
        "revision": getattr(ex, "revision", None) if ex else result.revision,
        "plan_patch": result.plan_patch,
        "asset_status": result.asset_status,
        "objects": len(result.edit_view.objects) if result.edit_view else None,
        "unresolved": len(result.unresolved),
        "notes": result.notes[:4],
        "warnings": (result.warnings or [])[:4],
        "errors": [
            f"{e.code}:{e.message[:80]}"
            for e in (getattr(ex, "errors", None) or [])[:3]
        ] if ex else [],
    }


def _music_uid(view):
    for o in view.objects:
        if o.role == "music":
            return o.asset_uid
    return None


# ---------- start：模块一~五全链 ----------
def do_start():
    r = state["session"].start(VIDEO, UTTERANCE)
    state["r_start"] = r
    info = _brief(r)
    info["overlays"] = sum(1 for o in r.edit_view.objects if o.role == "overlay")
    info["music_asset"] = _music_uid(r.edit_view)
    info["queries_seen"] = len(state["session"].state.queries_seen)
    return info


def do_remove_second():
    r = state["session"].reply("把第二个爱心删掉")
    info = _brief(r)
    info["overlays"] = sum(1 for o in r.edit_view.objects if o.role == "overlay")
    return info


def do_switch_music():
    before = _music_uid(state["session"].get_view())
    r = state["session"].reply("音乐换一个")
    after = _music_uid(r.edit_view) if r.edit_view else None
    info = _brief(r)
    info["music_before"] = before
    info["music_after"] = after
    info["asset_changed"] = bool(before and after and before != after)
    return info


def do_add_stars():
    r = state["session"].reply("每次挥手加星星")
    info = _brief(r)
    info["overlays"] = (
        sum(1 for o in r.edit_view.objects if o.role == "overlay")
        if r.edit_view else None
    )
    info["queries_seen"] = len(state["session"].state.queries_seen)
    return info


def do_undo():
    r = state["session"].undo()
    info = _brief(r)
    info["overlays"] = (
        sum(1 for o in r.edit_view.objects if o.role == "overlay")
        if r.edit_view else None
    )
    return info


def do_state():
    s = state["session"]
    root = WS / PID / "session"
    view = s.get_view()
    return {
        **s.get_state(),
        "session_files": sorted(p.name for p in root.iterdir()),
        "snapshots": sorted(
            p.name for p in (root / "snapshots").iterdir()
        ) if (root / "snapshots").exists() else [],
        "view_objects": len(view.objects),
        "view_revision": view.revision,
    }


def main():
    WS.mkdir(parents=True, exist_ok=True)
    for name, fn in [
        ("prep_assets", prep),
        ("start_full_chain", do_start),
        ("reply_remove_second_heart", do_remove_second),
        ("reply_switch_music", do_switch_music),
        ("reply_add_stars_on_wave", do_add_stars),
        ("undo_last", do_undo),
        ("final_state", do_state),
    ]:
        step(name, fn)
    out = WS / "e2e_session_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print("E2E_DONE", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
