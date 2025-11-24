"""
抖音视频解析器 - 提取无水印视频和文案
Douyin Video Parser - Extract video without watermark and transcript
"""

import re
import json
import httpx
from typing import Optional


class VideoInfo:
    """视频信息模型"""
    def __init__(self, video_id: str, title: str, author: str, author_id: str,
                 cover_url: str, video_url: str, music_url: str = "",
                 duration: int = 0, create_time: int = 0, statistics: dict = None):
        self.video_id = video_id
        self.title = title  # 视频文案
        self.author = author
        self.author_id = author_id
        self.cover_url = cover_url
        self.video_url = video_url  # 无水印视频链接
        self.music_url = music_url
        self.duration = duration
        self.create_time = create_time
        self.statistics = statistics or {}

    def to_dict(self):
        return {
            'video_id': self.video_id,
            'title': self.title,
            'author': self.author,
            'author_id': self.author_id,
            'cover_url': self.cover_url,
            'video_url': self.video_url,
            'music_url': self.music_url,
            'duration': self.duration,
            'create_time': self.create_time,
            'statistics': self.statistics
        }


class DouyinVideoParser:
    """抖音视频解析器"""

    def __init__(self, cookie: str = None):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.douyin.com/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
        }
        if cookie:
            self.headers['Cookie'] = cookie
        self.client = httpx.AsyncClient(
            headers=self.headers,
            follow_redirects=True,
            timeout=30.0
        )

    async def close(self):
        """关闭客户端"""
        await self.client.aclose()

    def extract_video_id(self, url: str) -> Optional[str]:
        """
        从各种抖音链接格式中提取视频ID
        支持格式:
        - https://www.douyin.com/video/7123456789012345678
        - https://v.douyin.com/xxxxxx/
        - https://www.iesdouyin.com/share/video/7123456789012345678
        """
        # 标准视频链接
        match = re.search(r'/video/(\d+)', url)
        if match:
            return match.group(1)

        # 短链接中的ID
        match = re.search(r'modal_id=(\d+)', url)
        if match:
            return match.group(1)

        # note格式
        match = re.search(r'/note/(\d+)', url)
        if match:
            return match.group(1)

        return None

    async def get_real_url(self, short_url: str) -> str:
        """
        获取短链接的真实URL（使用移动端UA以获取分享页面）
        """
        try:
            # 使用移动端 User-Agent 来获取 iesdouyin.com 分享页面
            mobile_headers = {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            }
            async with httpx.AsyncClient(headers=mobile_headers, follow_redirects=True, timeout=30.0) as client:
                response = await client.get(short_url)
                return str(response.url)
        except Exception as e:
            raise Exception(f"Failed to resolve short URL: {e}")

    async def parse(self, url: str) -> VideoInfo:
        """
        解析抖音视频链接，返回视频信息
        """
        # 处理短链接
        real_url = url
        if 'v.douyin.com' in url or 'vm.tiktok.com' in url:
            real_url = await self.get_real_url(url)

        # 如果是分享页面，直接解析
        if 'iesdouyin.com/share/video/' in real_url:
            try:
                result = await self._parse_from_share_page(real_url)
                if result:
                    return result
            except Exception:
                pass

        # 提取视频ID
        video_id = self.extract_video_id(real_url)
        if not video_id:
            raise ValueError(f"Cannot extract video ID from URL: {real_url}")

        # 方法1: 尝试移动端API（限制较少）
        try:
            result = await self._parse_from_mobile_api(video_id)
            if result:
                return result
        except Exception:
            pass

        # 方法2: 尝试Web API
        api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/"
        params = {
            'aweme_id': video_id,
            'aid': '6383',
            'cookie_enabled': 'true',
            'platform': 'PC',
        }

        try:
            response = await self.client.get(api_url, params=params)
            data = response.json()
            if data.get('status_code') == 0:
                aweme_detail = data.get('aweme_detail', {})
                if aweme_detail:
                    return self._extract_video_info(aweme_detail)
        except Exception:
            pass

        # 方法3: 从网页中提取数据
        return await self._parse_from_webpage(video_id)

    async def _parse_from_share_page(self, share_url: str) -> Optional[VideoInfo]:
        """
        从 iesdouyin.com 分享页面解析视频信息
        """
        # 使用移动端 User-Agent
        mobile_headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
        }

        async with httpx.AsyncClient(headers=mobile_headers, follow_redirects=True, timeout=30.0) as client:
            response = await client.get(share_url)
            html = response.text

        # 提取视频ID
        video_id_match = re.search(r'/video/(\d+)', share_url)
        video_id = video_id_match.group(1) if video_id_match else ""

        # 提取视频URL
        video_url = ""
        video_patterns = [
            r'"playAddr":\s*\[\{"src":"([^"]+)"',
            r'"src":"(https:\\u002F\\u002F[^"]*playwm[^"]*)"',
            r'play_addr.*?"url_list":\s*\["([^"]+)"',
        ]

        for pattern in video_patterns:
            match = re.search(pattern, html)
            if match:
                video_url = match.group(1)
                # 解码 unicode
                video_url = video_url.replace('\\u002F', '/')
                # 转换为无水印URL
                video_url = video_url.replace('playwm', 'play')
                break

        if not video_url:
            return None

        # 提取标题
        title = ""
        title_match = re.search(r'"desc":"([^"]*)"', html)
        if title_match:
            title = title_match.group(1).replace('\\u002F', '/')

        # 提取封面
        cover_url = ""
        cover_match = re.search(r'"cover":\s*\{[^}]*"url_list":\s*\["([^"]+)"', html)
        if cover_match:
            cover_url = cover_match.group(1).replace('\\u002F', '/')

        # 提取作者
        author = ""
        author_match = re.search(r'"nickname":"([^"]*)"', html)
        if author_match:
            author = author_match.group(1)

        return VideoInfo(
            video_id=video_id,
            title=title,
            author=author,
            author_id="",
            cover_url=cover_url,
            video_url=video_url,
            music_url="",
            duration=0,
            create_time=0,
            statistics={}
        )

    async def _parse_from_mobile_api(self, video_id: str) -> Optional[VideoInfo]:
        """
        使用移动端API获取视频信息（限制较少）
        """
        # 使用 iesdouyin.com 接口
        api_url = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"
        params = {
            'item_ids': video_id
        }

        # 使用移动端 User-Agent
        mobile_headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            'Referer': 'https://www.douyin.com/',
        }

        async with httpx.AsyncClient(headers=mobile_headers, follow_redirects=True, timeout=30.0) as client:
            response = await client.get(api_url, params=params)
            data = response.json()

        if data.get('status_code') != 0:
            return None

        item_list = data.get('item_list', [])
        if not item_list:
            return None

        aweme_detail = item_list[0]
        return self._extract_video_info_v2(aweme_detail)

    def _extract_video_info_v2(self, item: dict) -> VideoInfo:
        """
        从移动端API响应中提取视频信息
        """
        video_id = item.get('aweme_id', '')
        title = item.get('desc', '')

        author_info = item.get('author', {})
        author = author_info.get('nickname', '')
        author_id = author_info.get('uid', '')

        # 封面
        cover_url = ''
        cover = item.get('video', {}).get('cover', {})
        if cover:
            url_list = cover.get('url_list', [])
            if url_list:
                cover_url = url_list[0]

        # 视频URL
        video_url = ''
        play_addr = item.get('video', {}).get('play_addr', {})
        if play_addr:
            url_list = play_addr.get('url_list', [])
            if url_list:
                video_url = url_list[0].replace('watermark=1', 'watermark=0')
                # 替换为无水印域名
                video_url = video_url.replace('playwm', 'play')

        # 音乐
        music_url = ''
        music = item.get('music', {})
        if music:
            play_url = music.get('play_url', {})
            if play_url:
                url_list = play_url.get('url_list', [])
                if url_list:
                    music_url = url_list[0]

        # 统计
        statistics = item.get('statistics', {})
        stats = {
            'digg_count': statistics.get('digg_count', 0),
            'comment_count': statistics.get('comment_count', 0),
            'share_count': statistics.get('share_count', 0),
            'collect_count': statistics.get('collect_count', 0),
        }

        return VideoInfo(
            video_id=video_id,
            title=title,
            author=author,
            author_id=author_id,
            cover_url=cover_url,
            video_url=video_url,
            music_url=music_url,
            duration=item.get('video', {}).get('duration', 0),
            create_time=item.get('create_time', 0),
            statistics=stats
        )

    async def _parse_from_webpage(self, video_id: str) -> VideoInfo:
        """
        从网页中提取视频信息（备用方案）
        """
        page_url = f"https://www.douyin.com/video/{video_id}"

        try:
            response = await self.client.get(page_url)
            html = response.text

            # 方法1: 从页面中提取 RENDER_DATA
            match = re.search(r'<script id="RENDER_DATA" type="application/json">(.+?)</script>', html)
            if match:
                import urllib.parse
                render_data = urllib.parse.unquote(match.group(1))
                data = json.loads(render_data)

                # 遍历找到视频数据
                for key, value in data.items():
                    if isinstance(value, dict):
                        if 'aweme' in value:
                            aweme_detail = value['aweme']['detail']
                            return self._extract_video_info(aweme_detail)
                        # 新版本结构
                        if 'awemeDetail' in value:
                            aweme_detail = value['awemeDetail']
                            return self._extract_video_info(aweme_detail)

            # 方法2: 提取 SSR_HYDRATED_DATA
            match = re.search(r'window\._SSR_HYDRATED_DATA\s*=\s*(\{.+?\})\s*</script>', html, re.DOTALL)
            if match:
                try:
                    # 替换 undefined 为 null
                    json_str = match.group(1).replace('undefined', 'null')
                    data = json.loads(json_str)

                    # 查找视频数据
                    if 'aweme' in data:
                        aweme_detail = data['aweme']['detail']
                        return self._extract_video_info(aweme_detail)
                except:
                    pass

            # 方法3: 直接从HTML提取视频URL（最后手段）
            video_url_match = re.search(r'"playAddr":\s*\[\{"src":"([^"]+)"', html)
            if not video_url_match:
                video_url_match = re.search(r'"play_addr":\s*\{[^}]*"url_list":\s*\["([^"]+)"', html)

            if video_url_match:
                video_url = video_url_match.group(1).replace('\\u002F', '/')

                # 提取标题
                title = ""
                title_match = re.search(r'"desc":\s*"([^"]*)"', html)
                if title_match:
                    title = title_match.group(1)

                # 提取封面
                cover_url = ""
                cover_match = re.search(r'"cover":\s*\{[^}]*"url_list":\s*\["([^"]+)"', html)
                if cover_match:
                    cover_url = cover_match.group(1).replace('\\u002F', '/')

                return VideoInfo(
                    video_id=video_id,
                    title=title,
                    author="",
                    author_id="",
                    cover_url=cover_url,
                    video_url=video_url.replace('watermark=1', 'watermark=0'),
                    music_url="",
                    duration=0,
                    create_time=0,
                    statistics={}
                )

            raise Exception("Cannot find video data in webpage")

        except Exception as e:
            raise Exception(f"Failed to parse from webpage: {e}")

    def _extract_video_info(self, aweme_detail: dict) -> VideoInfo:
        """
        从aweme_detail中提取视频信息
        """
        video_id = aweme_detail.get('aweme_id', '')

        # 获取视频描述/标题
        title = aweme_detail.get('desc', '') or aweme_detail.get('title', '')

        # 获取作者信息
        author_info = aweme_detail.get('author', {})
        author = author_info.get('nickname', '')
        author_id = author_info.get('uid', '') or author_info.get('sec_uid', '')

        # 获取封面
        cover = aweme_detail.get('video', {}).get('cover', {})
        cover_url = ''
        if cover:
            url_list = cover.get('url_list', [])
            if url_list:
                cover_url = url_list[0]

        # 获取无水印视频链接
        video_url = self._get_no_watermark_url(aweme_detail)

        # 获取音乐链接
        music_info = aweme_detail.get('music', {})
        music_url = ''
        if music_info:
            play_url = music_info.get('play_url', {})
            if play_url:
                url_list = play_url.get('url_list', [])
                if url_list:
                    music_url = url_list[0]

        # 获取视频时长
        duration = aweme_detail.get('video', {}).get('duration', 0)

        # 获取创建时间
        create_time = aweme_detail.get('create_time', 0)

        # 获取统计数据
        statistics = aweme_detail.get('statistics', {})
        stats = {
            'digg_count': statistics.get('digg_count', 0),  # 点赞数
            'comment_count': statistics.get('comment_count', 0),  # 评论数
            'share_count': statistics.get('share_count', 0),  # 分享数
            'collect_count': statistics.get('collect_count', 0),  # 收藏数
        }

        return VideoInfo(
            video_id=video_id,
            title=title,
            author=author,
            author_id=author_id,
            cover_url=cover_url,
            video_url=video_url,
            music_url=music_url,
            duration=duration,
            create_time=create_time,
            statistics=stats
        )

    def _get_no_watermark_url(self, aweme_detail: dict) -> str:
        """
        获取无水印视频链接
        """
        video_info = aweme_detail.get('video', {})

        # 方法1: 从 play_addr 获取
        play_addr = video_info.get('play_addr', {})
        if play_addr:
            url_list = play_addr.get('url_list', [])
            for url in url_list:
                # 替换水印参数
                if 'watermark' in url:
                    url = url.replace('watermark=1', 'watermark=0')
                # 优先选择无水印域名
                if 'aweme.snssdk.com' in url or 'v.douyin.com' not in url:
                    return url
            if url_list:
                return url_list[0].replace('watermark=1', 'watermark=0')

        # 方法2: 从 bit_rate 获取高清链接
        bit_rate = video_info.get('bit_rate', [])
        if bit_rate:
            # 选择最高码率
            sorted_rates = sorted(bit_rate, key=lambda x: x.get('bit_rate', 0), reverse=True)
            for rate in sorted_rates:
                play_addr = rate.get('play_addr', {})
                url_list = play_addr.get('url_list', [])
                if url_list:
                    return url_list[0].replace('watermark=1', 'watermark=0')

        # 方法3: 从 download_addr 获取
        download_addr = video_info.get('download_addr', {})
        if download_addr:
            url_list = download_addr.get('url_list', [])
            if url_list:
                return url_list[0]

        raise Exception("Cannot find video URL")


# 便捷函数
async def parse_douyin_video(url: str) -> VideoInfo:
    """
    解析抖音视频的便捷函数
    """
    parser = DouyinVideoParser()
    try:
        return await parser.parse(url)
    finally:
        await parser.close()
