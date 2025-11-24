"""
音频转文字模块 - 使用 Whisper 进行语音识别
Audio Transcriber - Using Whisper for speech recognition
"""

import os
import tempfile
import whisper
import httpx
import re
import threading
from opencc import OpenCC

# 全局锁，防止并发转录
_transcribe_lock = threading.Lock()


class AudioTranscriber:
    """音频转文字器"""

    def __init__(self, model_name: str = "medium"):
        """
        初始化转录器

        Args:
            model_name: Whisper 模型名称
                - tiny: 最快，准确度最低 (~39M)
                - base: 快速，准确度较好 (~74M)
                - small: 中等速度和准确度 (~244M)
                - medium: 较慢，准确度较高 (~769M) - 推荐
                - large: 最慢，准确度最高 (~1550M)
        """
        self.model_name = model_name
        self.model = None

    def load_model(self):
        """加载 Whisper 模型（懒加载）"""
        if self.model is None:
            print(f"正在加载 Whisper {self.model_name} 模型...")
            self.model = whisper.load_model(self.model_name)
            print(f"Whisper {self.model_name} 模型加载完成")
        return self.model

    async def download_video(self, video_url: str) -> str:
        """
        下载视频到临时文件

        Args:
            video_url: 视频URL

        Returns:
            临时文件路径
        """
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            'Referer': 'https://www.douyin.com/',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept-Encoding': 'identity',
            'Range': 'bytes=0-',
        }

        # 创建临时文件
        temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        temp_path = temp_file.name

        try:
            print(f"正在下载视频: {video_url[:100]}...")
            async with httpx.AsyncClient(follow_redirects=True, timeout=180.0) as client:
                async with client.stream('GET', video_url, headers=headers) as response:
                    if response.status_code >= 400:
                        raise Exception(f"HTTP {response.status_code}")
                    total = 0
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        temp_file.write(chunk)
                        total += len(chunk)
            temp_file.close()
            print(f"视频下载完成，大小: {total / 1024 / 1024:.2f} MB")
            return temp_path
        except Exception as e:
            temp_file.close()
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise Exception(f"下载视频失败: {str(e)}")

    def transcribe_audio(self, audio_path: str, language: str = "zh") -> str:
        """
        转录音频文件

        Args:
            audio_path: 音频/视频文件路径
            language: 语言代码 (zh=中文, en=英文)

        Returns:
            转录的文字
        """
        # 使用锁防止并发访问 Whisper 模型
        with _transcribe_lock:
            model = self.load_model()

            print(f"正在转录音频: {audio_path}")
            result = model.transcribe(
                audio_path,
                language=language,
                verbose=False
            )

        text = result["text"].strip()

        # 繁体转简体
        if language == "zh":
            cc = OpenCC('t2s')  # Traditional to Simplified
            text = cc.convert(text)

        # 添加标点符号（基于 segments 时间间隔和文本长度）
        if "segments" in result and result["segments"]:
            segments = result["segments"]
            processed_parts = []

            for i, seg in enumerate(segments):
                seg_text = seg["text"].strip()
                if not seg_text:
                    continue

                # 繁体转简体
                if language == "zh":
                    seg_text = cc.convert(seg_text)

                # 确保文本不为空再添加标点
                if not seg_text:
                    continue

                # 根据时间间隔和文本长度添加标点
                if i < len(segments) - 1:
                    next_seg = segments[i + 1]
                    gap = next_seg["start"] - seg["end"]
                    seg_len = len(seg_text)

                    # 判断是否需要添加标点
                    needs_period = False
                    needs_comma = False

                    # 基于时间间隔判断
                    if gap > 0.5:
                        needs_period = True
                    elif gap > 0.2:
                        needs_comma = True

                    # 基于文本长度判断（长句子更可能是完整句子）
                    if seg_len > 25:
                        needs_period = True
                    elif seg_len > 15:
                        needs_comma = True

                    # 添加标点
                    if seg_text[-1] not in '。！？，、；：':
                        if needs_period:
                            seg_text += '。'
                        elif needs_comma:
                            seg_text += '，'
                else:
                    # 最后一段
                    if seg_text[-1] not in '。！？':
                        seg_text += '。'

                processed_parts.append(seg_text)

            text = ''.join(processed_parts)

        # 额外处理：基于常见句式添加标点（保守策略）
        if language == "zh":
            import re

            # 只在明确的句子连接词前添加逗号
            sentence_starters = [
                '所以', '因为', '但是', '而且', '然后', '如果', '那么', '不过', '另外',
                '咱们', '我们', '你们', '他们', '大家', '大哥们',
                '现在', '今天', '最后', '首先', '其次'
            ]
            for starter in sentence_starters:
                # 前面至少8个字符且不是标点，才添加逗号
                text = re.sub(f'(.{{8,}})({starter})', lambda m: m.group(1) + ('，' if m.group(1)[-1] not in '。！？，、；：' else '') + m.group(2), text)

            # 只在明确的句末语气词后添加逗号（不包括"的"）
            text = re.sub(r'(了吧|了呢|了啊|了哦|好了|可以了|完了)([^。！？，、；：])', r'\1，\2', text)

            # 清理多余的逗号
            text = re.sub(r'，+', '，', text)
            text = re.sub(r'。，', '。', text)
            text = re.sub(r'，。', '。', text)

        return text

    async def transcribe_from_url(self, video_url: str, language: str = "zh") -> str:
        """
        从视频URL转录语音

        Args:
            video_url: 视频URL
            language: 语言代码

        Returns:
            转录的文字
        """
        temp_path = None
        try:
            # 下载视频
            print("正在下载视频...")
            temp_path = await self.download_video(video_url)

            # 转录音频
            text = self.transcribe_audio(temp_path, language)

            return text

        finally:
            # 清理临时文件
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)


# 全局转录器实例（单例模式，避免重复加载模型）
_transcriber = None

def get_transcriber(model_name: str = "medium") -> AudioTranscriber:
    """获取全局转录器实例"""
    global _transcriber
    if _transcriber is None or _transcriber.model_name != model_name:
        _transcriber = AudioTranscriber(model_name)
    return _transcriber


async def transcribe_video(video_url: str, language: str = "zh") -> str:
    """
    便捷函数：从视频URL转录语音

    Args:
        video_url: 视频URL
        language: 语言代码

    Returns:
        转录的文字
    """
    transcriber = get_transcriber()
    return await transcriber.transcribe_from_url(video_url, language)
