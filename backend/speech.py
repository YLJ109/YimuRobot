# -*- coding: utf-8 -*-
"""
语音模块
- ASR: faster-whisper（本地部署，中文优化）
- TTS: edge-tts（微软晓晓/云希声音，免费）
- 关键词唤醒检测
"""
import os
import io
import re
import asyncio
import threading
import tempfile
import time
from typing import Optional, Callable, List

# ⚠️ HF_ENDPOINT 必须在本模块 import huggingface_hub 之前写进环境变量 ——
#    huggingface_hub 是在 **import 时** 就把 endpoint 拼成常量的，之后再改
#    os.environ 完全无效。而 huggingface_hub 是被 faster_whisper 的 import
#    链拉进来的，所以这段必须放在下面 `from faster_whisper import ...` 之上。
#
#    这台机器上企业代理会把 huggingface.co 打成 502 Bad Gateway，
#    模型永远下不下来（现象：`[ASR] 模型加载失败: 502 Bad Gateway`）。
#    默认走国内镜像 hf-mirror.com；要回官方源就设 ASR_HF_ENDPOINT。
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = os.environ.get("ASR_HF_ENDPOINT", "https://hf-mirror.com")

try:
    import edge_tts
    _HAS_EDGE_TTS = True
except ImportError:
    _HAS_EDGE_TTS = False

try:
    from faster_whisper import WhisperModel
    _HAS_WHISPER = True
    _WHISPER_IMPORT_ERROR = ""
except ImportError as _e:
    _HAS_WHISPER = False
    _WHISPER_IMPORT_ERROR = str(_e)


# ────────────────────────── 配置 ──────────────────────────
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")  # tiny/base/small/medium
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

# 中文指令场景的偏置提示：whisper 在有上下文时对「红色方块 / 放到B区」这类
# 短语的识别率明显更好，不加提示容易听成同音词。
ASR_INITIAL_PROMPT = os.environ.get(
    "ASR_INITIAL_PROMPT",
    "以下是工业机器人的语音操作指令，涉及「红色方块」「蓝色方块」「绿色圆柱」"
    "「A区」「B区」「托盘」「抓取」「放下」「回家」「停止」「吸盘」等词。",
)

# 单段音频最长处理时长（秒），防止有人把半小时的录音丢进来把 CPU 占满
ASR_MAX_AUDIO_SECONDS = float(os.environ.get("ASR_MAX_AUDIO_SECONDS", 60))

# whisper 中文输出稳定带句号（"回家。"），而用户说的是"回家"。这个句号会一路
# 传进对话历史、工具调用参数和日志里，读起来像是系统自己加的话。
# 识别结果一律先过 clean_asr_text 再往外发。
_ASR_TRIM_CHARS = " \t\r\n，。！？、；：""''（）《》〈〉【】〔〕,.!?;:\"'()[]{}<>-—－…·~`　"


def clean_asr_text(text: str) -> str:
    """清掉 whisper 附带的句读与空白，得到「用户实际说的话」。

    只在**首尾**去标点（句中的逗号是有效停顿信息，保留）；
    中文句子里 whisper 偶尔会插空格（"把红色 方块放到 B区"），一并压掉。
    """
    if not text:
        return ""
    t = text.strip().strip(_ASR_TRIM_CHARS)
    if re.search(r"[\u4e00-\u9fff]", t):
        t = re.sub(r"\s+", "", t)
    return t

# TTS 语音配置
TTS_VOICE = "zh-CN-XiaoxiaoNeural"  # 微软晓晓（女声）
# 备选: "zh-CN-YunxiNeural"（云希，男声）
TTS_RATE = "+0%"    # 语速
TTS_VOLUME = "+0%"

# TTS 产物目录：用 __file__ 定位，不依赖启动时的工作目录。
# 必须与 app.py 的 AUDIO_DIR（/audio/<file> 路由）指向同一处，否则
# 文件写了但 HTTP 取不到 —— 前端报 "no supported source was found"。
AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "audio")

# 目录里最多保留多少个 mp3。每合成一次就落一个文件，长时间运行会堆积
# 成千上万个小文件；保留最近 N 个足够回放/排查，正在播的那个必然在队列最前。
AUDIO_KEEP = int(os.environ.get("TTS_AUDIO_KEEP", 100))


def prune_audio_dir(out_dir: str = AUDIO_DIR, keep: int = AUDIO_KEEP) -> int:
    """只保留最近 keep 个 mp3，返回删除数量（尽力而为，失败不抛）。"""
    try:
        files = [f for f in os.listdir(out_dir) if f.endswith(".mp3")]
        if len(files) <= keep:
            return 0
        files.sort(key=lambda f: os.path.getmtime(os.path.join(out_dir, f)), reverse=True)
        removed = 0
        for f in files[keep:]:
            try:
                os.remove(os.path.join(out_dir, f))
                removed += 1
            except OSError:
                pass
        return removed
    except OSError:
        return 0

# 关键词唤醒
WAKE_WORDS = ['小艺小艺', '小艺', '你好小艺']
WAKE_ENABLED = os.environ.get("WAKE_WORD_ENABLED", "false").lower() == "true"


class AsrEngine:
    """语音识别引擎（faster-whisper，CPU int8）

    历史问题（一次性踩了三个，任意一个都会让「语音转文字」彻底不出声）：
      1. `faster_whisper` 根本没装 → `_HAS_WHISPER=False` → transcribe 静默返回 ""，
         前端界面上什么都不显示，看起来就是"按钮没反应"；
      2. 企业代理把 huggingface.co 打成 502 → 模型下不下来 → 同上静默；
      3. 前端每 100ms 发一个 webm 分片，后端**逐片**喂给 whisper —— 除了第一片，
         其余分片都没有容器头，whisper 解不出来。

    所以这一版：装齐依赖、默认走 hf-mirror 镜像、**整段**音频一次转写，
    并且任何失败都要有一条人能读的 error 传出去（不再静默）。
    """

    def __init__(self):
        self._model: Optional[WhisperModel] = None
        self._loaded = False
        self._loading = False
        self._error: str = ""
        self._lock = threading.Lock()
        self._warm_thread: Optional[threading.Thread] = None

    # ── 状态（给 /api/health 和前端用）──
    def status(self) -> dict:
        return {
            "available": _HAS_WHISPER,
            "loaded": self._loaded,
            "loading": self._loading,
            "model": WHISPER_MODEL_SIZE,
            "device": WHISPER_DEVICE,
            "computeType": WHISPER_COMPUTE_TYPE,
            "endpoint": os.environ.get("HF_ENDPOINT", ""),
            "error": self._error or ("" if _HAS_WHISPER else
                                     f"未安装 faster-whisper（{_WHISPER_IMPORT_ERROR}）。"
                                     f"执行: pip install faster-whisper"),
        }

    def load(self) -> bool:
        """加载模型（线程安全，失败会把原因记到 self._error）"""
        if self._loaded:
            return True
        with self._lock:
            if self._loaded:
                return True
            if not _HAS_WHISPER:
                self._error = ("未安装 faster-whisper，语音识别不可用。"
                               "请在 backend 目录执行：pip install faster-whisper")
                print("[ASR] " + self._error)
                return False
            self._loading = True
            try:
                print(f"[ASR] 正在加载 whisper {WHISPER_MODEL_SIZE} 模型"
                      f"（device={WHISPER_DEVICE}/{WHISPER_COMPUTE_TYPE}, "
                      f"endpoint={os.environ.get('HF_ENDPOINT')}）...")
                t0 = time.time()
                self._model = WhisperModel(
                    WHISPER_MODEL_SIZE,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                )
                self._loaded = True
                self._error = ""
                print(f"[ASR] 模型加载完成，耗时 {time.time() - t0:.1f}s")
                return True
            except Exception as e:  # noqa: BLE001
                self._error = (f"whisper {WHISPER_MODEL_SIZE} 模型加载失败：{e}。"
                               f"首次使用需要联网下载模型（当前源 "
                               f"{os.environ.get('HF_ENDPOINT')}），"
                               f"可设 WHISPER_MODEL=tiny 先用小模型，"
                               f"或 ASR_HF_ENDPOINT 换下载源。")
                print(f"[ASR] 模型加载失败: {e}")
                return False
            finally:
                self._loading = False

    def warmup_async(self):
        """后台预热。

        第一次调用 transcribe 才 load 的话，模型下载（几百 MB）会把 socket
        回调阻塞几分钟，前端只会看到麦克风转圈然后什么都没有。启动时先把
        这条线铺好，用户点麦克风时已经是热的。
        """
        if self._loaded or self._loading or not _HAS_WHISPER:
            return
        if self._warm_thread and self._warm_thread.is_alive():
            return
        self._warm_thread = threading.Thread(target=self.load, daemon=True)
        self._warm_thread.start()

    # ── 转写 ──
    def _decode(self, path: str, language: str) -> str:
        segments, _info = self._model.transcribe(
            path,
            language=language,
            beam_size=5,
            vad_filter=True,
            # temperature=0：指令识别要的是稳定复现，不是"创意"
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=ASR_INITIAL_PROMPT,
        )
        return clean_asr_text("".join(seg.text for seg in segments))

    def transcribe_bytes(self, audio_bytes: bytes, suffix: str = ".webm",
                         language: str = "zh") -> str:
        """转写**一整段**音频（浏览器 MediaRecorder 的完整产物）。

        必须整段传：MediaRecorder 的 timeslice 分片只有第一片带容器头，
        一片一片喂给 whisper 是解不出声音的。

        suffix 要跟真实格式一致（webm/ogg/mp4/wav）。解码走 PyAV，它按内容
        嗅探，但后缀对得上能少一层猜测。
        """
        if not audio_bytes:
            return ""
        if not self._loaded:
            if not self.load():
                return ""
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name
            return self._decode(tmp_path, language)
        except Exception as e:  # noqa: BLE001
            self._error = f"语音转写失败：{e}"
            print(f"[ASR] 转写失败: {e}")
            return ""
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def transcribe_file(self, file_path: str, language: str = "zh") -> str:
        """转写音频文件"""
        if not self._loaded:
            if not self.load():
                return ""
        try:
            return self._decode(file_path, language)
        except Exception as e:  # noqa: BLE001
            self._error = f"语音转写失败：{e}"
            print(f"[ASR] 文件转写失败: {e}")
            return ""


class TtsEngine:
    """语音合成引擎（edge-tts）"""

    def __init__(self):
        self._voice = TTS_VOICE

    def synthesize(self, text: str, output_path: Optional[str] = None) -> Optional[bytes]:
        """合成语音
        text: 要合成的中文文本
        output_path: 保存路径（可选），不指定则返回字节流
        返回 mp3 字节流或 None
        """
        if not _HAS_EDGE_TTS:
            print("[TTS] edge-tts 未安装，语音合成不可用")
            return None

        try:
            async def _synthesize():
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=self._voice,
                    rate=TTS_RATE,
                    volume=TTS_VOLUME,
                )
                if output_path:
                    await communicate.save(output_path)
                    return None
                else:
                    buffer = io.BytesIO()
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            buffer.write(chunk["data"])
                    return buffer.getvalue()

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_synthesize())
            finally:
                loop.close()
            return result
        except Exception as e:
            print(f"[TTS] 合成失败: {e}")
            return None

    def synthesize_to_file(self, text: str, output_dir: Optional[str] = None) -> Optional[str]:
        """合成语音到文件，返回可供 HTTP 访问的相对 URL（/audio/xxx.mp3）

        output_dir 省略时用 AUDIO_DIR（绝对路径）。历史实现写的是相对路径
        "static/audio"，一旦后端从别的目录启动（比如 `python backend/app.py`），
        文件就落到别处，而 /audio 路由永远找不到它。
        """
        out_dir = output_dir or AUDIO_DIR
        os.makedirs(out_dir, exist_ok=True)
        filename = f"tts_{int(time.time() * 1000)}.mp3"
        output_path = os.path.join(out_dir, filename)
        result = self.synthesize(text, output_path)
        if result is None and os.path.exists(output_path):
            prune_audio_dir(out_dir)
            return f"/audio/{filename}"
        return None


class WakeWordDetector:
    """关键词唤醒检测器"""

    def __init__(self):
        self.enabled = WAKE_ENABLED
        self.activated = not WAKE_ENABLED  # 如果未启用唤醒词，默认激活

    def check(self, text: str) -> bool:
        """检测文本是否包含唤醒词
        返回 True 表示已激活（唤醒词后面的内容有效）
        """
        if not self.enabled:
            return True  # 未启用唤醒词，始终激活

        text_lower = text.lower().strip()
        for word in WAKE_WORDS:
            if word in text_lower:
                self.activated = True
                return True
        return self.activated

    def extract_command(self, text: str) -> str:
        """提取唤醒词后的实际指令"""
        if not self.enabled:
            return text.strip()
        text_clean = text.strip()
        for word in WAKE_WORDS:
            if word in text_clean:
                idx = text_clean.index(word) + len(word)
                return text_clean[idx:].strip()
        return text_clean

    def reset(self):
        """重置激活状态"""
        self.activated = not self.enabled


# 全局单例
asr_engine = AsrEngine()
tts_engine = TtsEngine()
wake_detector = WakeWordDetector()