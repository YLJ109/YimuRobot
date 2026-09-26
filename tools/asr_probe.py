# -*- coding: utf-8 -*-
"""tools/asr_probe.py —— 语音识别（ASR）链路探针。

一次性回答三个问题：
  1. faster-whisper / PyAV / 依赖是否装齐？
  2. 模型能否真正加载（默认 HF 不通时自动试 hf-mirror 镜像）？
  3. 给一段真的中文语音，识别结果对不对？

用法：
  cd backend && "F:/Program Files/Python311/python.exe" ../tools/asr_probe.py
  加 --load-only 只验证"依赖 + 模型加载"，不跑识别（模型没下完时先看这个）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(os.path.dirname(HERE), "backend")
sys.path.insert(0, BACKEND)

load_only = "--load-only" in sys.argv


def step(title):
    print("\n" + "=" * 62)
    print("  " + title)
    print("=" * 62)


step("1. 依赖")
deps = {}
for mod, why in (("faster_whisper", "ASR 引擎"), ("ctranslate2", "推理后端"),
                 ("av", "音频解码（webm/opus）"), ("onnxruntime", "VAD")):
    try:
        m = __import__(mod)
        deps[mod] = True
        print(f"  [OK]   {mod:<16} {why}")
    except Exception as e:  # noqa: BLE001
        deps[mod] = False
        print(f"  [MISS] {mod:<16} {why}  →  {e}")

import faster_whisper  # noqa: E402
assets = os.path.join(os.path.dirname(faster_whisper.__file__), "assets")
print(f"  silero VAD asset: {os.listdir(assets) if os.path.isdir(assets) else '缺失'}")

step("2. 模型加载")
from speech import AsrEngine  # noqa: E402

eng = AsrEngine()
t0 = time.time()
ok = eng.load()
print(f"  load() -> {ok}   耗时 {time.time() - t0:.1f}s")
print(f"  status: {eng.status()}")
if not ok:
    print("\n  ✗ 模型加载失败。上面 status.error 里是真实原因。")
    sys.exit(1)

if load_only:
    print("\n  --load-only：到此为止。")
    sys.exit(0)

step("3. 真实中文语音识别")
# 用 TTS 现场合成一段已知文本，再喂回 ASR —— 端到端闭环，不依赖外部音频素材。
from speech import tts_engine  # noqa: E402

CASES = [
    "把红色方块放到B区",
    "回家",
    "抓取蓝色方块",
]
passed = 0
for text in CASES:
    path = os.path.join(os.path.dirname(HERE), "tools", "shots", "_asr_tmp.mp3")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if tts_engine.synthesize(text, path) is None and not os.path.exists(path):
        print(f"  [SKIP] TTS 合成失败：{text}")
        continue
    t1 = time.time()
    got = eng.transcribe_file(path)
    dt = time.time() - t1
    hit = got.replace(" ", "") == text.replace(" ", "")
    passed += 1 if hit else 0
    print(f"  [{'PASS' if hit else 'DIFF'}] 期望「{text}」→ 实得「{got}」  ({dt:.2f}s)")

print(f"\n  识别 {passed}/{len(CASES)} 完全一致（DIFF 只要语义对也算可用）")
