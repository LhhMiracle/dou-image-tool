"""
阿里云语音识别模块 - 录音文件识别
Alibaba Cloud ASR - Recording File Transcription

使用前需要：
1. 注册阿里云账号: https://www.aliyun.com/
2. 开通智能语音交互服务
3. 创建项目获取 AppKey
4. 获取 AccessKey ID 和 AccessKey Secret
"""

import os
import json
import time
import tempfile
import subprocess
import httpx
import hashlib
import hmac
import base64
import urllib.parse
from datetime import datetime, timezone

# 阿里云配置 - 请通过环境变量设置
ALIYUN_ACCESS_KEY_ID = os.getenv('ALIYUN_ACCESS_KEY_ID', '')
ALIYUN_ACCESS_KEY_SECRET = os.getenv('ALIYUN_ACCESS_KEY_SECRET', '')
ALIYUN_APPKEY = os.getenv('ALIYUN_APPKEY', '')

# 阿里云语音识别API地址
# 一句话识别（60秒以内）
SHORT_ASR_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/asr"
# 录音文件识别（长音频）
FILE_TRANS_URL = "https://filetrans.cn-shanghai.aliyuncs.com"


class AliyunASR:
    """阿里云语音识别"""

    def __init__(self, access_key_id: str = None, access_key_secret: str = None, appkey: str = None):
        self.access_key_id = access_key_id or ALIYUN_ACCESS_KEY_ID
        self.access_key_secret = access_key_secret or ALIYUN_ACCESS_KEY_SECRET
        self.appkey = appkey or ALIYUN_APPKEY

        if not self.access_key_id or not self.access_key_secret or not self.appkey:
            print("警告: 阿里云配置未设置，请设置 ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET 和 ALIYUN_APPKEY 环境变量")

    def extract_audio(self, video_path: str) -> str:
        """从视频中提取音频（转为WAV格式）"""
        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        wav_path = temp_file.name
        temp_file.close()

        try:
            # 使用ffmpeg提取音频并转换为WAV格式
            # 阿里云支持：16000Hz，16bit，单声道
            cmd = [
                'ffmpeg', '-y', '-i', video_path,
                '-vn',  # 不要视频
                '-acodec', 'pcm_s16le',  # 16bit PCM
                '-ar', '16000',  # 16000 采样率
                '-ac', '1',  # 单声道
                wav_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"ffmpeg转换失败: {result.stderr}")

            return wav_path

        except Exception as e:
            if os.path.exists(wav_path):
                os.unlink(wav_path)
            raise Exception(f"音频提取失败: {str(e)}")

    def transcribe_chunk(self, audio_data: bytes, token: str) -> str:
        """转录单个音频片段"""
        url = f"{SHORT_ASR_URL}?appkey={self.appkey}&format=wav&sample_rate=16000&enable_punctuation_prediction=true&enable_inverse_text_normalization=true"

        headers = {
            'Content-Type': 'application/octet-stream',
            'X-NLS-Token': token,
        }

        response = httpx.post(
            url,
            content=audio_data,
            headers=headers,
            timeout=180.0
        )

        result = response.json()

        if result.get('status') == 20000000:
            return result.get('result', '')
        else:
            raise Exception(f"阿里云ASR错误: {result.get('message', '未知错误')} (状态码: {result.get('status')})")

    def transcribe_short(self, audio_path: str) -> str:
        """
        一句话识别（支持长音频分段处理）
        使用 RESTful API
        """
        if not self.access_key_id or not self.access_key_secret or not self.appkey:
            raise ValueError("请先设置阿里云配置")

        wav_path = None
        try:
            # 提取音频
            print(f"正在提取音频: {audio_path}", flush=True)
            wav_path = self.extract_audio(audio_path)

            # 读取音频文件
            with open(wav_path, 'rb') as f:
                audio_data = f.read()

            audio_size = len(audio_data)
            print(f"音频数据大小: {audio_size / 1024:.2f} KB", flush=True)

            # 获取token
            token = self._get_token()

            # WAV文件头是44字节
            wav_header = audio_data[:44]
            pcm_data = audio_data[44:]

            # 阿里云限制2MB，使用1.8MB作为安全值
            # 16000Hz * 2bytes = 32000 bytes/秒
            # 1.8MB ≈ 56秒
            max_chunk_size = 1800000  # 约1.8MB的PCM数据

            if len(pcm_data) <= max_chunk_size:
                # 短音频，直接处理
                print("正在调用阿里云语音识别API...", flush=True)
                text = self.transcribe_chunk(audio_data, token)
                print(f"语音识别完成，文字长度: {len(text)}", flush=True)
                return text
            else:
                # 长音频，分段处理
                num_chunks = (len(pcm_data) + max_chunk_size - 1) // max_chunk_size
                print(f"音频较长，将分成 {num_chunks} 段处理...", flush=True)

                results = []
                for i in range(num_chunks):
                    start = i * max_chunk_size
                    end = min((i + 1) * max_chunk_size, len(pcm_data))
                    chunk_pcm = pcm_data[start:end]

                    # 重新添加WAV头
                    chunk_wav = self._create_wav_header(len(chunk_pcm)) + chunk_pcm

                    print(f"正在处理第 {i+1}/{num_chunks} 段...", flush=True)
                    try:
                        text = self.transcribe_chunk(chunk_wav, token)
                        results.append(text)
                    except Exception as e:
                        print(f"第 {i+1} 段处理失败: {e}", flush=True)
                        results.append("")

                    # 添加延迟避免QPS限制
                    if i < num_chunks - 1:
                        time.sleep(1)

                final_text = "".join(results)
                print(f"语音识别完成，文字长度: {len(final_text)}", flush=True)
                return final_text

        finally:
            if wav_path and os.path.exists(wav_path):
                os.unlink(wav_path)

    def _create_wav_header(self, data_size: int) -> bytes:
        """创建WAV文件头"""
        import struct

        # WAV文件参数
        channels = 1
        sample_rate = 16000
        bits_per_sample = 16
        byte_rate = sample_rate * channels * bits_per_sample // 8
        block_align = channels * bits_per_sample // 8

        header = struct.pack('<4sI4s4sIHHIIHH4sI',
            b'RIFF',
            36 + data_size,  # 文件大小 - 8
            b'WAVE',
            b'fmt ',
            16,  # fmt chunk size
            1,   # PCM格式
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
            b'data',
            data_size
        )
        return header

    def _get_token(self) -> str:
        """获取阿里云NLS Token"""
        # 使用AccessKey获取Token
        # 这里使用简化的方式，实际生产环境建议使用STS Token

        url = "https://nls-meta.cn-shanghai.aliyuncs.com/"

        # 构建签名
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        nonce = str(int(time.time() * 1000))

        params = {
            'AccessKeyId': self.access_key_id,
            'Action': 'CreateToken',
            'Format': 'JSON',
            'RegionId': 'cn-shanghai',
            'SignatureMethod': 'HMAC-SHA1',
            'SignatureNonce': nonce,
            'SignatureVersion': '1.0',
            'Timestamp': timestamp,
            'Version': '2019-02-28',
        }

        # 构建待签名字符串
        sorted_params = sorted(params.items())
        query_string = urllib.parse.urlencode(sorted_params, quote_via=urllib.parse.quote)
        string_to_sign = f"GET&%2F&{urllib.parse.quote(query_string, safe='')}"

        # 计算签名
        key = (self.access_key_secret + '&').encode('utf-8')
        signature = base64.b64encode(
            hmac.new(key, string_to_sign.encode('utf-8'), hashlib.sha1).digest()
        ).decode('utf-8')

        params['Signature'] = signature

        # 发送请求
        response = httpx.get(url, params=params, timeout=30)
        result = response.json()

        if 'Token' in result:
            return result['Token']['Id']
        else:
            raise Exception(f"获取Token失败: {result}")

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
            print(f"正在下载视频: {video_url[:80]}...", flush=True)
            async with httpx.AsyncClient(follow_redirects=True, timeout=180.0) as client:
                async with client.stream('GET', video_url, headers=headers) as response:
                    if response.status_code >= 400:
                        raise Exception(f"HTTP {response.status_code}")
                    total = 0
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        temp_file.write(chunk)
                        total += len(chunk)
            temp_file.close()
            print(f"视频下载完成，大小: {total / 1024 / 1024:.2f} MB", flush=True)
            return temp_path
        except Exception as e:
            temp_file.close()
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise Exception(f"下载视频失败: {str(e)}")

    async def transcribe_from_url(self, video_url: str) -> str:
        """从视频URL转录语音"""
        temp_path = None
        try:
            # 下载视频
            temp_path = await self.download_video(video_url)

            # 转录音频
            text = self.transcribe_short(temp_path)

            return text

        finally:
            # 清理临时文件
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)


# 全局实例
_aliyun_asr = None


def get_aliyun_asr(access_key_id: str = None, access_key_secret: str = None, appkey: str = None) -> AliyunASR:
    """获取全局阿里云ASR实例"""
    global _aliyun_asr
    if _aliyun_asr is None:
        _aliyun_asr = AliyunASR(access_key_id, access_key_secret, appkey)
    return _aliyun_asr


async def transcribe_video_aliyun(video_url: str) -> str:
    """
    便捷函数：使用阿里云ASR从视频URL转录语音

    Args:
        video_url: 视频URL

    Returns:
        转录的文字
    """
    asr = get_aliyun_asr()
    return await asr.transcribe_from_url(video_url)
