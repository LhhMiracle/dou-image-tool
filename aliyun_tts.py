"""
阿里云语音合成模块 - Text to Speech
使用阿里云智能语音交互服务的语音合成API
"""

import os
import json
import uuid
import time
import hashlib
import hmac
import base64
import urllib.parse
from datetime import datetime, timezone
import httpx


# 阿里云配置（已内置，无需配置）
ALIYUN_ACCESS_KEY_ID = os.getenv('ALIYUN_ACCESS_KEY_ID', 'LTAI5tEmuPNqM75naKcLA6oD')
ALIYUN_ACCESS_KEY_SECRET = os.getenv('ALIYUN_ACCESS_KEY_SECRET', '47iYI6Vp5iDH3Mw02nrnH0Ntae3OxB')
ALIYUN_APPKEY = os.getenv('ALIYUN_APPKEY', 'oIJvTnPINd2za0Am')

# 阿里云API地址
TOKEN_URL = "https://nls-meta.cn-shanghai.aliyuncs.com/"
TTS_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts"


class AliyunTTS:
    """阿里云语音合成"""

    # 可用的发音人（音色）
    # 注：以下为基础版常用音色，部分高级音色需要额外权限
    VOICES = {
        'xiaoyun': '小云 - 女声，温柔知性',
        'xiaogang': '小刚 - 男声，沉稳成熟',
        'ruoxi': '若溪 - 女声，亲切自然',
        'siqi': '思琪 - 女声，温暖治愈',
        'sijia': '思佳 - 女声，标准女声',
        'sicheng': '思诚 - 男声，标准男声',
        'ninger': '宁儿 - 女声，萝莉',
        'ruilin': '瑞琳 - 女声，成熟女性',
        'siyue': '思悦 - 女声，温柔',
        'yina': '伊娜 - 女声，温柔',
        'sijing': '思婧 - 女声，温柔知性',
        'sitong': '思彤 - 女声，儿童',
        # 以下音色可能需要高级权限，已注释
        # 'aiqi': '艾琪 - 女声，甜美',
        # 'aijia': '艾佳 - 女声，活泼',
        # 'aicheng': '艾诚 - 男声，浑厚',
        # 'aida': '艾达 - 男声，激昂',
        # 'aixin': '艾薇 - 女声，客服',
        # 'aishuo': '艾硕 - 男声，播音员',
        # 'aitong': '艾彤 - 女声，客服',
        # 'aixia': '艾夏 - 女声，亲切',
    }

    def __init__(self, access_key_id: str = None, access_key_secret: str = None, appkey: str = None):
        self.access_key_id = access_key_id or ALIYUN_ACCESS_KEY_ID
        self.access_key_secret = access_key_secret or ALIYUN_ACCESS_KEY_SECRET
        self.appkey = appkey or ALIYUN_APPKEY
        self.token = None
        self.token_expire_time = 0

        if not self.access_key_id or not self.access_key_secret or not self.appkey:
            print("警告: 阿里云TTS配置未设置")

    def _generate_signature(self, params: dict, method: str = 'GET') -> str:
        """生成阿里云API签名"""
        # 按字典序排序参数
        sorted_params = sorted(params.items())

        # 构造待签名字符串
        query_string = '&'.join([f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted_params])

        # 构造待签名的字符串
        string_to_sign = f"{method}&%2F&{urllib.parse.quote(query_string, safe='')}"

        # 计算签名
        signature = hmac.new(
            (self.access_key_secret + '&').encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha1
        ).digest()

        return base64.b64encode(signature).decode('utf-8')

    async def get_token(self) -> str:
        """获取访问token（token有效期24小时）"""
        # 如果token还有效，直接返回
        current_time = time.time()
        if self.token and current_time < self.token_expire_time:
            return self.token

        # 获取新token
        try:
            # 生成时间戳
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

            # 基础参数
            params = {
                'AccessKeyId': self.access_key_id,
                'Action': 'CreateToken',
                'Format': 'JSON',
                'RegionId': 'cn-shanghai',
                'SignatureMethod': 'HMAC-SHA1',
                'SignatureNonce': str(uuid.uuid4()),
                'SignatureVersion': '1.0',
                'Timestamp': timestamp,
                'Version': '2019-02-28',
            }

            # 生成签名
            signature = self._generate_signature(params, 'GET')
            params['Signature'] = signature

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(TOKEN_URL, params=params)

                if response.status_code == 200:
                    data = response.json()
                    if 'Token' in data and 'Id' in data['Token']:
                        self.token = data['Token']['Id']
                        # token有效期为24小时，这里设置为23小时后过期
                        self.token_expire_time = current_time + 23 * 3600
                        print(f"✅ Token获取成功，有效期至: {datetime.fromtimestamp(self.token_expire_time).strftime('%Y-%m-%d %H:%M:%S')}")
                        return self.token
                    else:
                        raise Exception(f"Token响应格式错误: {data}")
                else:
                    error_msg = response.text[:300]
                    raise Exception(f"获取Token失败 (HTTP {response.status_code}): {error_msg}")

        except httpx.RequestError as e:
            raise Exception(f"获取Token网络请求失败: {str(e)}")

    async def synthesize(
        self,
        text: str,
        voice: str = 'xiaoyun',
        speech_rate: int = 0,
        pitch_rate: int = 0,
        volume: int = 50,
        format: str = 'mp3'
    ) -> bytes:
        """
        合成语音

        参数:
            text: 要合成的文本（最长1000字符）
            voice: 发音人，默认'xiaoyun'
            speech_rate: 语速，范围-500~500，默认0
            pitch_rate: 语调，范围-500~500，默认0
            volume: 音量，范围0~100，默认50
            format: 音频格式，支持'wav', 'mp3', 'pcm'，默认'mp3'

        返回:
            音频二进制数据
        """
        if len(text) > 1000:
            raise ValueError("文本长度不能超过1000字符")

        if voice not in self.VOICES:
            raise ValueError(f"不支持的发音人: {voice}")

        # 获取token
        token = await self.get_token()

        # 构造请求参数
        params = {
            'appkey': self.appkey,
            'token': token,
            'text': text,
            'format': format,
            'sample_rate': 16000,
            'voice': voice,
            'volume': volume,
            'speech_rate': speech_rate,
            'pitch_rate': pitch_rate,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # 使用GET请求
                response = await client.get(TTS_URL, params=params)

                if response.status_code == 200:
                    # 检查返回的内容类型
                    content_type = response.headers.get('Content-Type', '')

                    # 如果返回的是JSON，说明有错误
                    if 'json' in content_type:
                        error_data = response.json()
                        raise Exception(f"TTS合成失败: {error_data}")

                    # 返回音频数据
                    audio_size = len(response.content)
                    print(f"✅ TTS合成成功，音频大小: {audio_size} 字节")
                    return response.content
                else:
                    # 尝试解析错误信息
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('message', str(error_data))
                    except:
                        error_msg = response.text[:200]

                    raise Exception(f"TTS请求失败 (HTTP {response.status_code}): {error_msg}")

        except httpx.TimeoutException:
            raise Exception("TTS请求超时，请稍后重试")
        except httpx.RequestError as e:
            raise Exception(f"TTS网络请求失败: {str(e)}")


async def text_to_speech(
    text: str,
    voice: str = 'xiaoyun',
    speech_rate: int = 0,
    pitch_rate: int = 0,
    volume: int = 50
) -> bytes:
    """
    快捷函数：文字转语音

    参数:
        text: 文本内容
        voice: 发音人
        speech_rate: 语速
        pitch_rate: 语调
        volume: 音量

    返回:
        MP3音频数据
    """
    tts = AliyunTTS()
    return await tts.synthesize(
        text=text,
        voice=voice,
        speech_rate=speech_rate,
        pitch_rate=pitch_rate,
        volume=volume,
        format='mp3'
    )


if __name__ == '__main__':
    # 测试代码
    import asyncio

    async def test():
        tts = AliyunTTS()

        # 测试文本
        text = "你好，我是阿里云智能语音助手，很高兴为您服务。"

        print(f"正在合成语音: {text}")
        print(f"使用配置: AccessKeyId={tts.access_key_id[:8]}..., AppKey={tts.appkey[:8]}...")

        try:
            audio_data = await tts.synthesize(text, voice='xiaoyun')

            # 保存音频文件
            with open('test_tts.mp3', 'wb') as f:
                f.write(audio_data)

            print(f"✅ 语音合成完成，已保存到 test_tts.mp3，大小: {len(audio_data)} 字节")
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()

    asyncio.run(test())
