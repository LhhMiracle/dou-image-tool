"""
百度语音识别模块 - 云端ASR服务
Baidu ASR - Cloud-based Automatic Speech Recognition

使用前需要：
1. 注册百度云账号: https://cloud.baidu.com/
2. 创建语音识别应用，获取 API Key 和 Secret Key
3. 设置环境变量或直接在代码中配置
"""

import os
import json
import base64
import tempfile
import subprocess
import httpx
import time

# 百度ASR配置
BAIDU_API_KEY = os.getenv('BAIDU_API_KEY', 'ElTrULxvbmGUy3hm33WcSs7p')
BAIDU_SECRET_KEY = os.getenv('BAIDU_SECRET_KEY', 'zj9IsvMRtbzDLGKoxukHCm37BBJwm9TB')

# 百度ASR API地址
TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
ASR_URL = "https://vop.baidu.com/server_api"


class BaiduASR:
    """百度语音识别"""

    def __init__(self, api_key: str = None, secret_key: str = None):
        self.api_key = api_key or BAIDU_API_KEY
        self.secret_key = secret_key or BAIDU_SECRET_KEY
        self._access_token = None

        if not self.api_key or not self.secret_key:
            print("警告: 百度API密钥未设置，请设置 BAIDU_API_KEY 和 BAIDU_SECRET_KEY 环境变量")

    def get_access_token(self) -> str:
        """获取百度API访问令牌"""
        if self._access_token:
            return self._access_token

        if not self.api_key or not self.secret_key:
            raise ValueError("请先设置百度API密钥")

        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key
        }

        response = httpx.post(TOKEN_URL, params=params, timeout=30)
        result = response.json()

        if "access_token" in result:
            self._access_token = result["access_token"]
            return self._access_token
        else:
            raise Exception(f"获取access_token失败: {result}")

    def extract_audio(self, video_path: str) -> str:
        """从视频中提取音频（转为PCM格式）"""
        # 创建临时PCM文件
        temp_file = tempfile.NamedTemporaryFile(suffix='.pcm', delete=False)
        pcm_path = temp_file.name
        temp_file.close()

        try:
            # 使用ffmpeg提取音频并转换为PCM格式
            # 百度ASR要求：PCM格式，16000采样率，16bit，单声道
            cmd = [
                'ffmpeg', '-y', '-i', video_path,
                '-vn',  # 不要视频
                '-acodec', 'pcm_s16le',  # 16bit PCM
                '-ar', '16000',  # 16000 采样率
                '-ac', '1',  # 单声道
                '-f', 's16le',  # 输出格式
                pcm_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"ffmpeg转换失败: {result.stderr}")

            return pcm_path

        except Exception as e:
            if os.path.exists(pcm_path):
                os.unlink(pcm_path)
            raise Exception(f"音频提取失败: {str(e)}")

    def transcribe_chunk(self, audio_data: bytes, access_token: str, dev_pid: int) -> str:
        """转录单个音频片段"""
        audio_len = len(audio_data)
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')

        data = {
            "format": "pcm",
            "rate": 16000,
            "channel": 1,
            "cuid": "python_asr_client",
            "token": access_token,
            "dev_pid": dev_pid,
            "speech": audio_base64,
            "len": audio_len
        }

        response = httpx.post(
            ASR_URL,
            json=data,
            timeout=180.0,
            headers={"Content-Type": "application/json"}
        )

        result = response.json()

        if result.get("err_no") == 0:
            return "".join(result.get("result", []))
        else:
            err_msg = result.get("err_msg", "未知错误")
            raise Exception(f"百度ASR错误: {err_msg} (错误码: {result.get('err_no')})")

    def transcribe_audio(self, audio_path: str, language: str = "zh") -> str:
        """
        转录音频文件

        Args:
            audio_path: 音频/视频文件路径
            language: 语言代码 (zh=中文普通话, en=英文)

        Returns:
            转录的文字（带标点）
        """
        pcm_path = None
        try:
            # 获取access token
            access_token = self.get_access_token()

            # 提取并转换音频为PCM
            print(f"正在提取音频: {audio_path}")
            pcm_path = self.extract_audio(audio_path)

            # 读取PCM文件
            with open(pcm_path, 'rb') as f:
                audio_data = f.read()

            # 获取音频长度（字节数）
            audio_len = len(audio_data)
            print(f"音频数据大小: {audio_len / 1024:.2f} KB")

            # 设置语言
            dev_pid = 1537  # 普通话（支持简单英文）
            if language == "en":
                dev_pid = 1737  # 英语

            # 百度ASR限制60秒音频
            # 16000Hz * 2bytes * 1channel = 32000 bytes/秒
            # 50秒 = 1,600,000 bytes (留一些余量)
            chunk_size = 1600000  # 约50秒的音频

            if audio_len <= chunk_size:
                # 短音频，直接处理
                print("正在调用百度语音识别API...")
                text = self.transcribe_chunk(audio_data, access_token, dev_pid)
                print(f"语音识别完成，文字长度: {len(text)}")
                return text
            else:
                # 长音频，分段处理
                num_chunks = (audio_len + chunk_size - 1) // chunk_size
                print(f"音频较长，将分成 {num_chunks} 段处理...")

                results = []
                for i in range(num_chunks):
                    start = i * chunk_size
                    end = min((i + 1) * chunk_size, audio_len)
                    chunk = audio_data[start:end]

                    print(f"正在处理第 {i+1}/{num_chunks} 段...", flush=True)
                    try:
                        text = self.transcribe_chunk(chunk, access_token, dev_pid)
                        results.append(text)
                    except Exception as e:
                        print(f"第 {i+1} 段处理失败: {e}", flush=True)
                        results.append("")

                    # 添加延迟避免QPS限制
                    if i < num_chunks - 1:
                        time.sleep(1)

                final_text = "".join(results)
                print(f"语音识别完成，文字长度: {len(final_text)}")
                return final_text

        finally:
            # 清理临时PCM文件
            if pcm_path and os.path.exists(pcm_path):
                os.unlink(pcm_path)

    async def download_video(self, video_url: str) -> str:
        """下载视频到临时文件"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15',
            'Referer': 'https://www.douyin.com/',
            'Accept': '*/*',
        }

        temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        temp_path = temp_file.name

        try:
            print(f"正在下载视频: {video_url[:80]}...")
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

    async def transcribe_from_url(self, video_url: str, language: str = "zh") -> str:
        """从视频URL转录语音"""
        temp_path = None
        try:
            # 下载视频
            temp_path = await self.download_video(video_url)

            # 转录音频
            text = self.transcribe_audio(temp_path, language)

            return text

        finally:
            # 清理临时文件
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)


# 全局实例
_baidu_asr = None


def get_baidu_asr(api_key: str = None, secret_key: str = None) -> BaiduASR:
    """获取全局百度ASR实例"""
    global _baidu_asr
    if _baidu_asr is None:
        _baidu_asr = BaiduASR(api_key, secret_key)
    return _baidu_asr


async def transcribe_video_baidu(video_url: str, language: str = "zh") -> str:
    """
    便捷函数：使用百度ASR从视频URL转录语音

    Args:
        video_url: 视频URL
        language: 语言代码

    Returns:
        转录的文字（带标点）
    """
    asr = get_baidu_asr()
    return await asr.transcribe_from_url(video_url, language)
