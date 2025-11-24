"""
抖音商品图片解析器
从抖音商品详情页提取所有图片
"""

import re
import json
import httpx
import asyncio
from typing import List, Dict, Optional
from dataclasses import dataclass
from playwright.async_api import async_playwright
from playwright_stealth import Stealth


@dataclass
class ProductInfo:
    """商品信息"""
    product_id: str
    title: str
    main_images: List[str]  # 主图/商品图
    detail_images: List[str]  # 详情图
    video_url: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            'product_id': self.product_id,
            'title': self.title,
            'main_images': self.main_images,
            'detail_images': self.detail_images,
            'video_url': self.video_url,
            'total_images': len(self.main_images) + len(self.detail_images)
        }


class DouyinProductParser:
    """抖音商品解析器"""

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://www.douyin.com/',
        }

    def extract_product_id(self, url: str) -> str:
        """从URL中提取商品ID"""
        # 支持多种URL格式
        patterns = [
            r'commodity_id=(\d+)',  # buyin.jinritemai.com格式
            r'id=(\d+)',  # haohuo.jinritemai.com格式
            r'product_id=(\d+)',
            r'/(\d{15,})(?:\?|$|&)',  # 直接的商品ID
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        # 如果是短链接格式，返回特殊标记
        if 'v.douyin.com' in url or 'douyin.com' in url:
            return 'SHORT_LINK'

        raise ValueError("无法从URL中提取商品ID")

    async def parse(self, url: str) -> ProductInfo:
        """解析商品页面，提取所有图片"""
        product_id = self.extract_product_id(url)
        print(f"解析商品ID: {product_id}", flush=True)

        # 如果是短链接，标记为需要跟随重定向
        is_short_link = (product_id == 'SHORT_LINK')
        if is_short_link:
            print("检测到抖音短链接，将跟随重定向...", flush=True)

        # 尝试多种方法获取商品信息
        result = None

        # 方法1: 使用Playwright渲染页面获取数据
        result = await self._parse_with_playwright(url, product_id if not is_short_link else 'unknown')

        if result:
            return result

        # 方法3: 尝试获取商品详情页HTML（无头请求）
        result = await self._parse_html(url, product_id)

        if result:
            return result

        raise ValueError("无法获取商品信息，请检查链接是否正确")

    async def _parse_with_playwright(self, url: str, product_id: str) -> Optional[ProductInfo]:
        """使用Playwright渲染页面获取商品图片"""
        try:
            print(f"使用Playwright渲染页面...", flush=True)

            # 用户数据目录，用于保存登录状态
            import os
            user_data_dir = os.path.expanduser('~/.haohuo_browser_data')

            async with async_playwright() as p:

                # 使用持久化浏览器上下文
                context = await p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=False,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                    ],
                    viewport={'width': 1280, 'height': 800},
                    locale='zh-CN',
                    timezone_id='Asia/Shanghai',
                )

                page = await context.new_page()

                # 应用stealth模式
                stealth = Stealth()
                await stealth.apply_stealth_async(page)

                # 访问页面 - 使用domcontentloaded而不是networkidle，因为抖音页面永远不会网络空闲
                print(f"正在访问: {url}", flush=True)
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                except Exception as nav_error:
                    print(f"页面导航失败: {str(nav_error)}", flush=True)
                    # 如果导航失败，等待一下看看是否有重定向
                    await page.wait_for_timeout(3000)

                # 获取最终URL（处理重定向）
                final_url = page.url
                print(f"最终URL: {final_url}", flush=True)

                # 检查是否成功重定向到商品页面
                if 'about:blank' in final_url or final_url == url:
                    print("警告：页面未能正确重定向，尝试直接导航...", flush=True)
                    # 尝试使用 evaluate 直接设置 location
                    try:
                        await page.evaluate(f'window.location.href = "{url}"')
                        await page.wait_for_timeout(5000)
                        final_url = page.url
                        print(f"直接导航后URL: {final_url}", flush=True)
                    except Exception as eval_error:
                        print(f"直接导航失败: {eval_error}", flush=True)

                    # 如果还是 about:blank，再等待一会
                    if 'about:blank' in final_url:
                        await page.wait_for_timeout(5000)
                        final_url = page.url
                        print(f"最终URL: {final_url}", flush=True)

                # 如果product_id是unknown，从最终URL提取
                if product_id == 'unknown':
                    id_match = re.search(r'id=(\d+)', final_url)
                    if id_match:
                        product_id = id_match.group(1)
                        print(f"从重定向URL提取到商品ID: {product_id}", flush=True)

                # 处理"页面已下线"对话框，点击"继续访问"按钮
                await page.wait_for_timeout(2000)
                try:
                    # 尝试点击"继续访问"按钮
                    continue_btn = page.locator('text=继续访问')
                    if await continue_btn.count() > 0:
                        print("检测到'页面已下线'对话框，点击'继续访问'...", flush=True)
                        # 先勾选"我已悉知上述信息"复选框（如果存在）
                        checkbox = page.locator('text=我已悉知上述信息')
                        if await checkbox.count() > 0:
                            await checkbox.click()
                            await page.wait_for_timeout(500)
                        await continue_btn.click()
                        await page.wait_for_timeout(3000)
                        print("已点击'继续访问'按钮", flush=True)
                except Exception as dialog_error:
                    print(f"处理对话框时出错（可忽略）: {dialog_error}", flush=True)

                # 检查是否出现二维码验证页面（安全风险检测）
                await page.wait_for_timeout(2000)
                try:
                    qr_page = page.locator('text=点此进入抖音查看商品信息')
                    if await qr_page.count() > 0:
                        print("检测到二维码验证页面，尝试点击链接...", flush=True)
                        await qr_page.click()
                        await page.wait_for_timeout(5000)
                        final_url = page.url
                        print(f"点击后URL: {final_url}", flush=True)
                except Exception as qr_error:
                    print(f"处理二维码页面时出错（可忽略）: {qr_error}", flush=True)

                # 智能等待：每2秒检测一次是否加载完成，最多等待60秒
                print("等待页面内容加载...", flush=True)
                content_loaded = False
                for i in range(30):  # 30次 * 2秒 = 60秒
                    await page.wait_for_timeout(2000)
                    html_check = await page.content()

                    # 检测是否已加载商品内容（包含图片CDN或足够长的内容）
                    if 'ecombdimg.com' in html_check or len(html_check) > 80000:
                        print(f"检测到商品内容已加载（HTML长度: {len(html_check)}），继续处理...", flush=True)
                        content_loaded = True
                        # 再等待2秒确保完全加载
                        await page.wait_for_timeout(2000)
                        break

                    # 检查是否需要验证
                    if 'qrcode' in html_check.lower() or '验证' in html_check:
                        if i == 0:
                            print("检测到验证页面，请在浏览器中完成验证...", flush=True)

                    if i % 5 == 0:  # 每10秒打印一次状态
                        print(f"等待内容加载... ({i*2}/60秒, HTML长度: {len(html_check)})", flush=True)

                if not content_loaded:
                    print("警告：等待超时，尝试继续处理...", flush=True)

                # 尝试滚动页面加载更多内容
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight / 2)')
                await page.wait_for_timeout(1000)
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await page.wait_for_timeout(2000)

                # 获取渲染后的HTML
                html = await page.content()
                print(f"Playwright获取到HTML，长度: {len(html)} 字符", flush=True)

                await context.close()

                # 解析HTML
                main_images = []
                detail_images = []
                title = ""
                video_url = None

                # 调试输出
                if 'ecombdimg.com' in html:
                    print("HTML中包含ecombdimg.com图片", flush=True)

                # 提取所有图片URL - 更全面的CDN匹配
                img_patterns = [
                    r'https?://p\d+-aio\.ecombdimg\.com[^"\'<>\s\)\],]+',
                    r'https?://lf\d+-[^\.]+\.bytetos\.com[^"\'<>\s\)\],]+',
                    r'https?://[^"\'<>\s\)\],]+\.douyinpic\.com[^"\'<>\s\)\],]+',
                    r'https?://[^"\'<>\s\)\],]+\.byteimg\.com[^"\'<>\s\)\],]+',
                    r'https?://[^"\'<>\s\)\],]+\.pstatp\.com[^"\'<>\s\)\],]+',
                    r'https?://[^"\'<>\s\)\],]+\.snssdk\.com[^"\'<>\s\)\],]+',
                    r'https?://[^"\'<>\s\)\],]+\.toutiaostatic\.com[^"\'<>\s\)\],]+',
                    r'https?://[^"\'<>\s\)\],]+\.feishucdn\.com[^"\'<>\s\)\],]+',
                    # 通用图片URL
                    r'https?://[^"\'<>\s\)\],]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'<>\s\)\],]*)?',
                ]

                all_images = set()
                for pattern in img_patterns:
                    matches = re.findall(pattern, html)
                    for img_url in matches:
                        # 清理URL
                        img_url = img_url.rstrip('",;')
                        if self._is_valid_product_image(img_url):
                            all_images.add(img_url)

                print(f"找到 {len(all_images)} 张图片", flush=True)

                # 调试：打印找到的图片URL
                if all_images:
                    for i, url in enumerate(list(all_images)[:5]):
                        print(f"  图片{i+1}: {url[:100]}...", flush=True)

                # 分类图片
                for img_url in all_images:
                    # 根据URL特征分类
                    if any(kw in img_url.lower() for kw in ['main', 'primary', 'cover', 'thumb', 'origin']):
                        main_images.append(img_url)
                    else:
                        detail_images.append(img_url)

                # 如果没有明确分类，前5张作为主图
                if not main_images and detail_images:
                    main_images = detail_images[:5]
                    detail_images = detail_images[5:]

                # 提取标题
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', html)
                if title_match:
                    title = title_match.group(1).strip()

                if main_images or detail_images:
                    return ProductInfo(
                        product_id=product_id,
                        title=title,
                        main_images=list(set(main_images)),
                        detail_images=list(set(detail_images)),
                        video_url=video_url
                    )

                return None

        except Exception as e:
            print(f"Playwright解析失败: {e}", flush=True)
            return None

    async def _parse_html(self, url: str, product_id: str) -> Optional[ProductInfo]:
        """从HTML页面解析商品图片"""
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                response = await client.get(url, headers=self.headers)

                if response.status_code != 200:
                    print(f"页面请求失败: HTTP {response.status_code}", flush=True)
                    return None

                html = response.text
                print(f"获取到HTML，长度: {len(html)} 字符", flush=True)

                # 尝试从页面中提取JSON数据
                main_images = []
                detail_images = []
                title = ""
                video_url = None

                # 调试：打印HTML中的关键片段
                if 'ecombdimg.com' in html:
                    print("HTML中包含ecombdimg.com图片", flush=True)
                if '_ROUTER_DATA' in html:
                    print("HTML中包含_ROUTER_DATA", flush=True)
                if '__INITIAL_STATE__' in html:
                    print("HTML中包含__INITIAL_STATE__", flush=True)

                # 查找script中的商品数据
                # 抖音商品页通常会在script标签中嵌入JSON数据
                json_patterns = [
                    r'window\._ROUTER_DATA\s*=\s*({.*?})\s*</script>',  # jinritemai格式
                    r'window\.__INITIAL_STATE__\s*=\s*({.*?});?\s*</script>',
                    r'window\.__NUXT__\s*=\s*({.*?});?\s*</script>',
                    r'<script[^>]*>window\.rawData\s*=\s*({.*?})\s*</script>',
                    r'<script[^>]*id="__NEXT_DATA__"[^>]*>({.*?})</script>',
                ]

                # 特别处理jinritemai的数据结构
                router_match = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>', html, re.DOTALL)
                if router_match:
                    try:
                        router_data = json.loads(router_match.group(1))
                        print(f"找到_ROUTER_DATA数据", flush=True)
                        # 递归查找商品数据
                        product_data = self._find_product_in_router(router_data)
                        if product_data:
                            images = self._extract_images_from_json(product_data)
                            if images:
                                main_images.extend(images.get('main', []))
                                detail_images.extend(images.get('detail', []))
                                title = images.get('title', '')
                                video_url = images.get('video')
                    except json.JSONDecodeError as e:
                        print(f"_ROUTER_DATA解析失败: {e}", flush=True)

                for pattern in json_patterns:
                    match = re.search(pattern, html, re.DOTALL)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            # 尝试从数据中提取图片
                            images = self._extract_images_from_json(data)
                            if images:
                                main_images.extend(images.get('main', []))
                                detail_images.extend(images.get('detail', []))
                                title = images.get('title', '')
                                video_url = images.get('video')
                        except json.JSONDecodeError:
                            continue

                # 如果JSON解析失败，尝试直接从HTML提取图片URL
                if not main_images and not detail_images:
                    # 提取所有图片URL - 特别关注字节跳动CDN
                    img_patterns = [
                        r'https?://p\d+-aio\.ecombdimg\.com[^"\'<>\s]+',  # 字节跳动商品图CDN
                        r'https?://lf\d+-[^\.]+\.bytetos\.com[^"\'<>\s]+',  # 字节跳动CDN
                        r'https?://[^"\'<>\s]+\.douyinpic\.com[^"\'<>\s]+',  # 抖音图片
                        r'https?://[^"\'<>\s]+\.byteimg\.com[^"\'<>\s]+',  # 字节图片
                        r'https?://[^"\'<>\s]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'<>\s]*)?',
                    ]

                    all_images = set()
                    for pattern in img_patterns:
                        matches = re.findall(pattern, html, re.IGNORECASE)
                        for img_url in matches:
                            # 过滤掉太小的图片（可能是图标）
                            if self._is_valid_product_image(img_url):
                                all_images.add(img_url)

                    # 按URL特征分类
                    for img_url in all_images:
                        if any(kw in img_url for kw in ['main', 'primary', 'cover', 'thumb']):
                            main_images.append(img_url)
                        else:
                            detail_images.append(img_url)

                    # 如果没有明确分类，前5张作为主图
                    if not main_images and detail_images:
                        main_images = detail_images[:5]
                        detail_images = detail_images[5:]

                # 提取标题
                if not title:
                    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html)
                    if title_match:
                        title = title_match.group(1).strip()

                if main_images or detail_images:
                    return ProductInfo(
                        product_id=product_id,
                        title=title,
                        main_images=list(set(main_images)),
                        detail_images=list(set(detail_images)),
                        video_url=video_url
                    )

                return None

        except Exception as e:
            print(f"HTML解析失败: {e}", flush=True)
            return None

    async def _parse_api(self, product_id: str) -> Optional[ProductInfo]:
        """尝试通过API获取商品信息"""
        # 尝试不同的API端点
        api_urls = [
            # 好货API - 多种格式
            f"https://haohuo.jinritemai.com/views/product/item2?id={product_id}",
            f"https://haohuo.jinritemai.com/ecommerce/trade/detail/v2/item?id={product_id}",
            f"https://haohuo.jinritemai.com/api/product/detail?product_id={product_id}",
            # 抖音电商API
            f"https://ec.snssdk.com/product/fxgajaxstaali498/{product_id}",
            f"https://ec.snssdk.com/product/lubanajaxsta498/{product_id}",
            # buyin API
            f"https://buyin.jinritemai.com/api/product/detail?product_id={product_id}",
        ]

        # 移动端UA可能更容易获取数据
        mobile_headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': 'https://www.douyin.com/',
        }

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            for api_url in api_urls:
                try:
                    print(f"尝试API: {api_url[:60]}...", flush=True)
                    response = await client.get(api_url, headers=mobile_headers)
                    if response.status_code == 200:
                        content = response.text

                        # 尝试解析JSON
                        try:
                            data = response.json()
                            images = self._extract_images_from_json(data)
                            if images and (images.get('main') or images.get('detail')):
                                print(f"从API JSON获取到图片数据", flush=True)
                                return ProductInfo(
                                    product_id=product_id,
                                    title=images.get('title', ''),
                                    main_images=images.get('main', []),
                                    detail_images=images.get('detail', []),
                                    video_url=images.get('video')
                                )
                        except json.JSONDecodeError:
                            pass

                        # 如果不是JSON，尝试从HTML提取图片
                        if 'ecombdimg.com' in content or len(content) > 10000:
                            print(f"尝试从HTML提取图片 (长度: {len(content)})", flush=True)

                            # 提取图片URL
                            img_patterns = [
                                r'https?://p\d+-aio\.ecombdimg\.com[^"\'<>\s\)\],]+',
                                r'https?://lf\d+-[^\.]+\.bytetos\.com[^"\'<>\s\)\],]+',
                                r'https?://[^"\'<>\s\)\],]+\.douyinpic\.com[^"\'<>\s\)\],]+',
                            ]

                            all_images = set()
                            for pattern in img_patterns:
                                matches = re.findall(pattern, content)
                                for img_url in matches:
                                    img_url = img_url.rstrip('",;')
                                    if self._is_valid_product_image(img_url):
                                        all_images.add(img_url)

                            if all_images:
                                print(f"从HTML提取到 {len(all_images)} 张图片", flush=True)
                                images_list = list(all_images)
                                main_images = images_list[:5]
                                detail_images = images_list[5:]

                                # 提取标题
                                title = ""
                                title_match = re.search(r'<title[^>]*>([^<]+)</title>', content)
                                if title_match:
                                    title = title_match.group(1).strip()

                                return ProductInfo(
                                    product_id=product_id,
                                    title=title,
                                    main_images=main_images,
                                    detail_images=detail_images,
                                    video_url=None
                                )
                except Exception as e:
                    print(f"API请求失败: {str(e)[:50]}", flush=True)
                    continue

        return None

    def _find_product_in_router(self, data, depth=0) -> Optional[dict]:
        """从_ROUTER_DATA中查找商品数据"""
        if depth > 15:
            return None

        if isinstance(data, dict):
            # 查找包含商品图片的数据
            for key, value in data.items():
                key_lower = key.lower()
                # 常见的商品数据键名
                if key_lower in ['product', 'goods', 'item', 'productinfo', 'goodsinfo', 'data']:
                    if isinstance(value, dict):
                        # 检查是否包含图片相关字段
                        if any(k in str(value).lower() for k in ['images', 'img', 'pic', 'gallery']):
                            return value

                # 递归搜索
                if isinstance(value, (dict, list)):
                    result = self._find_product_in_router(value, depth + 1)
                    if result:
                        return result

        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    result = self._find_product_in_router(item, depth + 1)
                    if result:
                        return result

        return None

    def _extract_images_from_json(self, data: dict, depth: int = 0) -> Optional[Dict]:
        """递归从JSON数据中提取图片URL"""
        if depth > 10:  # 防止无限递归
            return None

        result = {'main': [], 'detail': [], 'title': '', 'video': None}

        if isinstance(data, dict):
            # 查找常见的图片字段
            image_keys = ['images', 'imgs', 'image_list', 'pic_list', 'gallery', 'photos']
            main_keys = ['main_img', 'main_image', 'cover', 'thumb', 'primary']
            detail_keys = ['detail_images', 'desc_images', 'detail_pics']
            title_keys = ['title', 'name', 'product_name', 'goods_name']
            video_keys = ['video', 'video_url', 'video_src']

            for key, value in data.items():
                key_lower = key.lower()

                # 标题
                if key_lower in title_keys and isinstance(value, str):
                    result['title'] = value

                # 视频
                if key_lower in video_keys and isinstance(value, str):
                    result['video'] = value

                # 主图
                if any(k in key_lower for k in main_keys):
                    if isinstance(value, str) and self._is_valid_product_image(value):
                        result['main'].append(value)
                    elif isinstance(value, list):
                        for img in value:
                            if isinstance(img, str) and self._is_valid_product_image(img):
                                result['main'].append(img)
                            elif isinstance(img, dict):
                                img_url = img.get('url') or img.get('src') or img.get('img')
                                if img_url and self._is_valid_product_image(img_url):
                                    result['main'].append(img_url)

                # 详情图
                if any(k in key_lower for k in detail_keys):
                    if isinstance(value, list):
                        for img in value:
                            if isinstance(img, str) and self._is_valid_product_image(img):
                                result['detail'].append(img)
                            elif isinstance(img, dict):
                                img_url = img.get('url') or img.get('src') or img.get('img')
                                if img_url and self._is_valid_product_image(img_url):
                                    result['detail'].append(img_url)

                # 通用图片列表
                if any(k in key_lower for k in image_keys):
                    if isinstance(value, list):
                        for img in value:
                            if isinstance(img, str) and self._is_valid_product_image(img):
                                if len(result['main']) < 5:
                                    result['main'].append(img)
                                else:
                                    result['detail'].append(img)
                            elif isinstance(img, dict):
                                img_url = img.get('url') or img.get('src') or img.get('img')
                                if img_url and self._is_valid_product_image(img_url):
                                    if len(result['main']) < 5:
                                        result['main'].append(img_url)
                                    else:
                                        result['detail'].append(img_url)

                # 递归搜索
                if isinstance(value, (dict, list)):
                    nested = self._extract_images_from_json(value, depth + 1)
                    if nested:
                        result['main'].extend(nested.get('main', []))
                        result['detail'].extend(nested.get('detail', []))
                        if not result['title']:
                            result['title'] = nested.get('title', '')
                        if not result['video']:
                            result['video'] = nested.get('video')

        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    nested = self._extract_images_from_json(item, depth + 1)
                    if nested:
                        result['main'].extend(nested.get('main', []))
                        result['detail'].extend(nested.get('detail', []))

        # 去重
        result['main'] = list(dict.fromkeys(result['main']))
        result['detail'] = list(dict.fromkeys(result['detail']))

        if result['main'] or result['detail']:
            return result
        return None

    def _is_valid_product_image(self, url: str) -> bool:
        """判断是否为有效的商品图片URL"""
        if not url:
            return False

        # 排除太短的URL
        if len(url) < 20:
            return False

        # 排除明显的图标/logo
        exclude_patterns = [
            'icon', 'logo', 'avatar', 'emoji', 'badge',
            'loading', 'placeholder', 'default',
            '1x1', '2x2', 'blank', 'transparent',
            'sprite', 'btn', 'button'
        ]

        url_lower = url.lower()
        for pattern in exclude_patterns:
            if pattern in url_lower:
                return False

        # 必须是图片格式或字节跳动CDN
        is_image_ext = any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.webp'])
        is_bytedance_cdn = any(cdn in url_lower for cdn in ['ecombdimg.com', 'bytetos.com', 'douyinpic.com', 'byteimg.com'])

        if not is_image_ext and not is_bytedance_cdn:
            return False

        return True


# 便捷函数
async def parse_product(url: str) -> dict:
    """解析商品页面，返回商品信息字典"""
    parser = DouyinProductParser()
    result = await parser.parse(url)
    return result.to_dict()
