"""
腾讯云语音合成模块 - Text to Speech
使用腾讯云智能语音交互服务的语音合成API
"""

import os
import json
import uuid
import base64
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tts.v20190823 import tts_client, models


# 腾讯云配置（已内置，无需配置）
TENCENT_SECRET_ID = os.getenv('TENCENT_SECRET_ID', 'AKID3A6oLhspX1HAuQkxvfnjQs56tRnvsdAd')
TENCENT_SECRET_KEY = os.getenv('TENCENT_SECRET_KEY', 'MUO8ew0qbBXfZHUYP2Rd2GH4f3DAuRlt')


class TencentTTS:
    """腾讯云语音合成"""

    # 适合电商带货的音色（优先推荐）
    VOICES = {
        # 营销导向
        '502004': '智小满 - 营销女声（推荐）',

        # 情感女声（适合带货）
        '601000': '爱小溪 - 情感聊天女声',
        '601005': '爱小静 - 情感聊天女声',
        '601009': '爱小芊 - 情感聊天女声',
        '601012': '爱小璟 - 特色女声',

        # 特色女声
        '603001': '潇湘妹妹 - 特色女声',
        '603004': '温柔小柠 - 聊天女声',

        # 精品通用女声
        '101055': '智付 - 通用女声',
        '101027': '智梅 - 通用女声',
        '101003': '智美 - 通用女声',
        '101004': '智云 - 通用女声',
        '101005': '智莉 - 通用女声',

        # 精品通用男声
        '101001': '智瑜 - 通用男声',
        '101002': '智聪 - 通用男声',
        '101006': '智侃 - 通用男声',
        '101007': '智娜 - 温柔女声',
    }

    # 情感支持（部分音色支持）
    EMOTIONS = {
        'neutral': '中性',
        'happy': '高兴',
        'sad': '悲伤',
        'angry': '生气',
        'fear': '恐惧',
        'spoil': '撒娇',
        'surprise': '震惊',
        'disgust': '厌恶',
        'calm': '平静',
    }

    def __init__(self, secret_id: str = None, secret_key: str = None):
        self.secret_id = secret_id or TENCENT_SECRET_ID
        self.secret_key = secret_key or TENCENT_SECRET_KEY

        if not self.secret_id or not self.secret_key:
            print("警告: 腾讯云TTS配置未设置")

    def _get_client(self):
        """创建腾讯云客户端"""
        cred = credential.Credential(self.secret_id, self.secret_key)

        # HTTP配置
        httpProfile = HttpProfile()
        httpProfile.endpoint = "tts.tencentcloudapi.com"

        # 客户端配置
        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile

        # 创建客户端
        client = tts_client.TtsClient(cred, "", clientProfile)
        return client

    def synthesize(
        self,
        text: str,
        voice: str = '502004',
        speed: float = 0,
        volume: float = 0,
        emotion: str = None,
        sample_rate: int = 16000,
        codec: str = 'mp3'
    ) -> bytes:
        """
        合成语音

        参数:
            text: 要合成的文本（中文最多150汉字）
            voice: 音色代码，默认'502004'（营销女声）
            speed: 语速，范围-2~6，默认0为正常语速
            volume: 音量，范围-10~10，默认0为正常音量
            emotion: 情感（仅部分音色支持）
            sample_rate: 采样率，支持8000/16000/24000，默认16000
            codec: 音频格式，支持'wav'/'mp3'/'pcm'，默认'mp3'

        返回:
            音频二进制数据
        """
        if len(text) > 150:
            raise ValueError("文本长度不能超过150字符（中文）")

        if voice not in self.VOICES:
            raise ValueError(f"不支持的音色: {voice}")

        try:
            # 创建客户端
            client = self._get_client()

            # 构造请求
            req = models.TextToVoiceRequest()
            params = {
                "Text": text,
                "SessionId": str(uuid.uuid4()),
                "VoiceType": int(voice),
                "Speed": speed,
                "Volume": volume,
                "SampleRate": sample_rate,
                "Codec": codec,
            }

            # 添加情感参数（如果支持）
            if emotion and emotion in self.EMOTIONS:
                params["EmotionCategory"] = emotion

            req.from_json_string(json.dumps(params))

            # 发送请求
            resp = client.TextToVoice(req)

            # 获取音频数据（base64编码）
            audio_base64 = resp.Audio
            audio_data = base64.b64decode(audio_base64)

            print(f"✅ 腾讯云TTS合成成功，音频大小: {len(audio_data)} 字节")
            return audio_data

        except Exception as e:
            raise Exception(f"腾讯云TTS合成失败: {str(e)}")


async def text_to_speech_tencent(
    text: str,
    voice: str = '502004',
    speed: float = 0,
    volume: float = 0,
    emotion: str = None
) -> bytes:
    """
    快捷函数：文字转语音（腾讯云）
    支持长文本自动分段合成

    参数:
        text: 文本内容（自动分段，无长度限制）
        voice: 音色代码
        speed: 语速
        volume: 音量
        emotion: 情感

    返回:
        MP3音频数据
    """
    from text_splitter import TextSplitter
    import io

    tts = TencentTTS()

    # 如果文本不超过150字符，直接合成
    if len(text) <= 150:
        return tts.synthesize(
            text=text,
            voice=voice,
            speed=speed,
            volume=volume,
            emotion=emotion,
            codec='mp3'
        )

    # 长文本，需要分段合成
    print(f"长文本检测：{len(text)} 字符，开始分段合成...")

    # 分段
    splitter = TextSplitter(max_length=150)
    segments = splitter.split(text)
    print(f"分成 {len(segments)} 段")

    # 逐段合成
    audio_data_list = []
    for i, segment in enumerate(segments, 1):
        print(f"正在合成第 {i}/{len(segments)} 段（{len(segment)} 字符）...")
        try:
            audio_data = tts.synthesize(
                text=segment,
                voice=voice,
                speed=speed,
                volume=volume,
                emotion=emotion,
                codec='mp3'
            )
            audio_data_list.append(audio_data)

        except Exception as e:
            print(f"第 {i} 段合成失败: {e}")
            raise

    # 简单拼接MP3文件（直接拼接字节流）
    print("正在合并音频...")
    final_audio = b''.join(audio_data_list)

    print(f"✅ 长文本合成完成，总大小: {len(final_audio)} 字节")
    return final_audio


if __name__ == '__main__':
    # 测试代码
    import asyncio

    async def test():
        tts = TencentTTS()

        # 测试文本（模拟带货文案）
        text = "反季买衣服真的能省好几百啊！今天给大家带来一款超值的羽绒马甲，点开小黄车看看，绝对物超所值！"

        print(f"正在合成语音: {text}")
        print(f"使用音色: 502004 - 智小满（营销女声）")

        try:
            audio_data = tts.synthesize(text, voice='502004')

            # 保存音频文件
            with open('test_tencent_tts.mp3', 'wb') as f:
                f.write(audio_data)

            print(f"✅ 语音合成完成，已保存到 test_tencent_tts.mp3，大小: {len(audio_data)} 字节")
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()

    asyncio.run(test())
