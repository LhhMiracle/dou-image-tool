"""
腾讯云自定义音色TTS模块
使用腾讯云声音复刻服务创建的音色进行语音合成
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

# 你的自定义音色ID（从腾讯云声音复刻服务获取）
CUSTOM_VOICE_ID = 'WCHN-add2502611834078ac62ba7dd8d2458e'  # 带货女声


class TencentCustomVoiceTTS:
    """腾讯云自定义音色TTS"""

    def __init__(self, secret_id: str = None, secret_key: str = None, voice_id: str = None):
        self.secret_id = secret_id or TENCENT_SECRET_ID
        self.secret_key = secret_key or TENCENT_SECRET_KEY
        self.voice_id = voice_id or CUSTOM_VOICE_ID

        if not self.secret_id or not self.secret_key:
            print("警告: 腾讯云配置未设置")

    def _get_client(self):
        """创建腾讯云客户端"""
        cred = credential.Credential(self.secret_id, self.secret_key)

        httpProfile = HttpProfile()
        httpProfile.endpoint = "tts.tencentcloudapi.com"

        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile

        client = tts_client.TtsClient(cred, "", clientProfile)
        return client

    def synthesize(
        self,
        text: str,
        speed: float = 0,
        volume: float = 0,
        sample_rate: int = 16000,
        codec: str = 'mp3'
    ) -> bytes:
        """
        使用自定义音色合成语音

        参数:
            text: 要合成的文本（建议每次不超过150字）
            speed: 语速，范围-2~6，默认0为正常语速
            volume: 音量，范围-10~10，默认0为正常音量
            sample_rate: 采样率，支持8000/16000/24000，默认16000
            codec: 音频格式，支持'wav'/'mp3'/'pcm'，默认'mp3'

        返回:
            音频二进制数据
        """
        if len(text) > 150:
            print(f"⚠️  文本长度 {len(text)} 超过建议的150字，可能需要分段合成")

        try:
            client = self._get_client()

            # 构造请求
            req = models.TextToVoiceRequest()
            params = {
                "Text": text,
                "SessionId": str(uuid.uuid4()),
                "VoiceType": 0,  # 使用自定义音色时，VoiceType设为0
                "PrimaryLanguage": 1,  # 1-中文
                "Speed": speed,
                "Volume": volume,
                "SampleRate": sample_rate,
                "Codec": codec,
                "VoiceoverDialectId": self.voice_id,  # 关键：使用自定义音色ID
            }

            req.from_json_string(json.dumps(params))

            # 发送请求
            resp = client.TextToVoice(req)

            # 获取音频数据
            audio_base64 = resp.Audio
            audio_data = base64.b64decode(audio_base64)

            print(f"✅ 自定义音色TTS合成成功，音频大小: {len(audio_data)} 字节")
            return audio_data

        except Exception as e:
            raise Exception(f"自定义音色TTS合成失败: {str(e)}")


async def text_to_speech_custom_voice(
    text: str,
    voice_id: str = CUSTOM_VOICE_ID,
    speed: float = 1.5,
    volume: float = 10
) -> bytes:
    """
    快捷函数：使用自定义音色文字转语音
    支持长文本自动分段合成

    参数:
        text: 文本内容（自动分段，无长度限制）
        voice_id: 自定义音色ID
        speed: 语速
        volume: 音量

    返回:
        MP3音频数据
    """
    from text_splitter import TextSplitter

    tts = TencentCustomVoiceTTS(voice_id=voice_id)

    # 如果文本不超过150字符，直接合成
    if len(text) <= 150:
        return tts.synthesize(
            text=text,
            speed=speed,
            volume=volume,
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
                speed=speed,
                volume=volume,
                codec='mp3'
            )
            audio_data_list.append(audio_data)

        except Exception as e:
            print(f"第 {i} 段合成失败: {e}")
            raise

    # 合并音频
    print("正在合并音频...")
    final_audio = b''.join(audio_data_list)

    print(f"✅ 长文本合成完成，总大小: {len(final_audio)} 字节")
    return final_audio


if __name__ == '__main__':
    # 测试代码
    import asyncio

    async def test():
        tts = TencentCustomVoiceTTS()

        # 测试文本
        test_text = "反季买衣服真的能省好几百啊！今天给大家带来一款超值的羽绒马甲。"

        print(f"正在使用自定义音色合成: {test_text}")
        print(f"音色ID: {CUSTOM_VOICE_ID}")

        try:
            audio_data = tts.synthesize(test_text, speed=1.5, volume=10)

            # 保存音频文件
            with open('test_custom_voice.mp3', 'wb') as f:
                f.write(audio_data)

            print(f"✅ 合成完成，已保存到 test_custom_voice.mp3")
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()

    asyncio.run(test())
