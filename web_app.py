"""
Web版抠图工具 - 基于Flask
功能：
1. 上传图片
2. 自动去背景
3. 下载处理后的图片
"""

from flask import Flask, render_template, request, send_file, jsonify
import os
import re
import requests
import base64
import zipfile
from werkzeug.utils import secure_filename
from PIL import Image, ImageEnhance
from rembg import remove
from rembg.session_factory import new_session
import io
import config
import numpy as np
import cv2
import asyncio
from content_generator import ContentGenerator
from video_parser import DouyinVideoParser
# 语音识别方式：'aliyun', 'baidu' 或 'whisper'
ASR_ENGINE = 'aliyun'

if ASR_ENGINE == 'aliyun':
    from aliyun_asr import transcribe_video_aliyun as transcribe_video
elif ASR_ENGINE == 'baidu':
    from baidu_asr import transcribe_video_baidu as transcribe_video
else:
    from audio_transcriber import transcribe_video

app = Flask(__name__)

# 初始化文案生成器（全局单例，避免重复加载模板）
content_generator = ContentGenerator()

# 配置
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 最大50MB
app.config['UPLOAD_FOLDER'] = 'web_uploads'
app.config['OUTPUT_FOLDER'] = 'web_outputs'

# 确保目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'bmp'}


def allowed_file(filename):
    """检查文件类型是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def preprocess_image(image):
    """预处理图片：增强对比度和亮度，帮助AI更好地识别前景"""
    # 增强对比度（1.2倍）
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(1.2)

    # 轻微增强亮度（1.1倍）
    enhancer = ImageEnhance.Brightness(image)
    image = enhancer.enhance(1.1)

    # 增强锐度（1.15倍）
    enhancer = ImageEnhance.Sharpness(image)
    image = enhancer.enhance(1.15)

    return image


def postprocess_mask(image_with_alpha):
    """后处理：填补mask中的小空洞，修复误删的前景"""
    # 转换为numpy数组
    img_array = np.array(image_with_alpha)

    # 提取alpha通道
    if img_array.shape[2] == 4:
        alpha = img_array[:, :, 3]

        # 使用形态学闭操作填补小空洞
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        alpha_closed = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, kernel, iterations=2)

        # 使用开操作去除小噪点
        kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        alpha_processed = cv2.morphologyEx(alpha_closed, cv2.MORPH_OPEN, kernel_open, iterations=1)

        # 替换alpha通道
        img_array[:, :, 3] = alpha_processed

        # 转回PIL Image
        return Image.fromarray(img_array)

    return image_with_alpha


def remove_background_single(input_path, output_path, model_name=None):
    """去除单张图片背景"""
    try:
        # 读取图片
        input_image = Image.open(input_path)

        # 创建模型 session - 使用指定的模型或默认模型
        selected_model = model_name if model_name else config.REMBG_CONFIG['model']
        session = new_session(selected_model)

        # 去除背景
        output_image = remove(
            input_image,
            session=session,
            alpha_matting=config.REMBG_CONFIG['alpha_matting'],
            alpha_matting_foreground_threshold=config.REMBG_CONFIG['alpha_matting_foreground_threshold'],
            alpha_matting_background_threshold=config.REMBG_CONFIG['alpha_matting_background_threshold'],
            alpha_matting_erode_size=config.REMBG_CONFIG['alpha_matting_erode_size'],
            post_process_mask=config.REMBG_CONFIG.get('post_process_mask', False),
        )

        # 后处理：填补小空洞（轻度修复）
        output_image = postprocess_mask(output_image)

        # 保存 - PNG无损格式，最高质量
        output_image.save(output_path, format='PNG', compress_level=1, optimize=True)
        return True

    except Exception as e:
        print(f"处理图片失败: {e}")
        import traceback
        traceback.print_exc()
        return False


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_files():
    """处理上传的文件"""
    if 'files[]' not in request.files:
        return jsonify({'error': '没有文件上传'}), 400

    files = request.files.getlist('files[]')

    if not files or files[0].filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    # 获取用户选择的模型（如果有）
    selected_model = request.form.get('model', 'u2net')
    print(f"使用模型: {selected_model}")

    # ✨ 每次上传新图片时，自动清空之前的输出文件
    # 这样下载时只包含当前这一批的图片
    for filename in os.listdir(app.config['OUTPUT_FOLDER']):
        file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
        try:
            os.remove(file_path)
        except:
            pass

    processed_files = []
    failed_files = []

    for file in files:
        if file and allowed_file(file.filename):
            # 保存原始文件
            filename = secure_filename(file.filename)
            upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(upload_path)

            # 处理图片 - 传递选择的模型
            output_filename = os.path.splitext(filename)[0] + '_nobg.png'
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

            if remove_background_single(upload_path, output_path, model_name=selected_model):
                processed_files.append({
                    'original': filename,
                    'processed': output_filename,
                    'download_url': f'/download/{output_filename}'
                })
            else:
                failed_files.append(filename)

            # 删除上传的原始文件
            os.remove(upload_path)

    return jsonify({
        'success': True,
        'processed': len(processed_files),
        'failed': len(failed_files),
        'files': processed_files
    })


@app.route('/download/<filename>')
def download_file(filename):
    """下载单个处理后的文件"""
    file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "文件不存在", 404


@app.route('/download_all')
def download_all():
    """打包下载所有处理后的文件"""
    output_files = os.listdir(app.config['OUTPUT_FOLDER'])

    if not output_files:
        return "没有文件可下载", 404

    # 创建ZIP文件
    zip_path = 'all_processed.zip'
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for filename in output_files:
            file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
            zipf.write(file_path, filename)

    return send_file(zip_path, as_attachment=True, download_name='processed_images.zip')


@app.route('/clear')
def clear_files():
    """清空输出目录"""
    for filename in os.listdir(app.config['OUTPUT_FOLDER']):
        file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
        os.remove(file_path)

    return jsonify({'success': True, 'message': '已清空所有文件'})


@app.route('/generate_content', methods=['POST'])
def generate_content():
    """生成文案"""
    try:
        data = request.get_json()
        product_name = data.get('product_name', '').strip()
        description = data.get('description', '').strip()
        template_index = data.get('template_index')  # 用于重新生成

        if not product_name or not description:
            return jsonify({
                'success': False,
                'error': '请输入商品名称和商品介绍'
            })

        print(f"生成文案 - 商品: {product_name}, 描述长度: {len(description)}")

        # 生成文案
        result = content_generator.generate_content(
            product_name=product_name,
            description=description,
            template_index=template_index
        )

        return jsonify({
            'success': True,
            'content': result['content'],
            'category': result['category'],
            'template_index': result['template_index'],
            'total_templates': result['total_templates']
        })

    except Exception as e:
        print(f"生成文案失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'生成失败: {str(e)}'
        })


@app.route('/parse_video', methods=['POST'])
def parse_video():
    """解析抖音视频，提取视频链接和文案"""
    try:
        import re
        data = request.get_json()
        text = data.get('url', '').strip()

        if not text:
            return jsonify({
                'success': False,
                'error': '请输入抖音视频链接'
            })

        # 从文本中提取URL（支持从抖音分享文本中提取）
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, text)

        # 找到抖音相关的URL
        url = None
        for u in urls:
            if any(domain in u for domain in ['douyin.com', 'iesdouyin.com']):
                url = u
                break

        # 如果没找到URL，检查整个文本是否就是URL
        if not url:
            if any(domain in text for domain in ['douyin.com', 'iesdouyin.com']):
                url = text
            else:
                return jsonify({
                    'success': False,
                    'error': '未找到有效的抖音视频链接，请检查输入'
                })

        print(f"解析视频链接: {url}", flush=True)

        # 使用异步函数解析视频
        async def do_parse():
            parser = DouyinVideoParser()
            try:
                result = await parser.parse(url)
                return result
            finally:
                await parser.close()

        # 运行异步函数
        result = asyncio.run(do_parse())

        video_data = result.to_dict()

        # 尝试进行语音识别（如果有视频链接）
        transcript = ""
        if video_data.get('video_url'):
            try:
                print("开始语音识别...", flush=True)

                async def do_transcribe():
                    return await transcribe_video(video_data['video_url'])

                transcript = asyncio.run(do_transcribe())
                print(f"语音识别完成，文字长度: {len(transcript)}", flush=True)
            except Exception as e:
                import traceback
                error_msg = str(e)
                print(f"语音识别失败: {error_msg}", flush=True)
                traceback.print_exc()
                transcript = f"[语音识别失败: {error_msg}]"

        video_data['transcript'] = transcript

        return jsonify({
            'success': True,
            'data': video_data
        })

    except ValueError as e:
        print(f"解析失败 (ValueError): {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        })
    except Exception as e:
        print(f"解析视频失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'解析失败: {str(e)}'
        })


@app.route('/parse_product', methods=['POST'])
def parse_product():
    """解析抖音商品页面，提取所有图片"""
    try:
        from product_parser import DouyinProductParser

        data = request.get_json()
        input_text = data.get('url', '').strip()

        if not input_text:
            return jsonify({
                'success': False,
                'error': '请输入商品链接'
            })

        print(f"输入文本: {input_text}", flush=True)

        # 优先提取v.douyin.com短链接（同时支持前端和后端分享格式）
        # 前端格式: 【抖音商城】https://v.douyin.com/xxxxx/ 商品名称
        # 后端格式: 【商品名称】复制此条消息...【➝➝xxx︽︽】 https://v.douyin.com/xxxxx/
        douyin_pattern = r'https://v\.douyin\.com/[a-zA-Z0-9_-]+/?'
        douyin_urls = re.findall(douyin_pattern, input_text)

        url = None
        if douyin_urls:
            url = douyin_urls[0]
            print(f"提取到抖音短链接: {url}", flush=True)
        else:
            # 如果没有找到短链接，尝试提取其他抖音相关链接
            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+[^\s<>"{}|\\^`\[\].,;:!?\'")\]]'
            urls = re.findall(url_pattern, input_text)

            for found_url in urls:
                if 'douyin.com' in found_url or 'jinritemai.com' in found_url:
                    url = found_url
                    break

            # 如果没有找到抖音链接，使用第一个URL
            if not url and urls:
                url = urls[0]

            # 如果没有找到任何URL，尝试把整个输入当作URL
            if not url:
                url = input_text

            print(f"提取到URL: {url}", flush=True)

        # 使用异步函数解析商品
        async def do_parse():
            parser = DouyinProductParser()
            return await parser.parse(url)

        result = asyncio.run(do_parse())
        product_data = result.to_dict()

        print(f"提取到 {product_data['total_images']} 张图片", flush=True)

        return jsonify({
            'success': True,
            'data': product_data
        })

    except ValueError as e:
        print(f"解析失败: {e}", flush=True)
        return jsonify({
            'success': False,
            'error': str(e)
        })
    except Exception as e:
        print(f"解析商品失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'解析失败: {str(e)}'
        })


@app.route('/batch_remove_bg', methods=['POST'])
def batch_remove_bg():
    """批量去除图片背景"""
    try:
        data = request.get_json()
        image_urls = data.get('images', [])

        if not image_urls:
            return jsonify({
                'success': False,
                'error': '没有选择图片'
            })

        print(f"批量处理 {len(image_urls)} 张图片", flush=True)

        results = []
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            'Referer': 'https://haohuo.jinritemai.com/',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Origin': 'https://haohuo.jinritemai.com',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
        }

        for i, img_url in enumerate(image_urls):
            try:
                print(f"处理第 {i+1}/{len(image_urls)} 张: {img_url[:80]}...", flush=True)

                # 下载图片，带重试逻辑
                response = None
                max_retries = 3
                for retry in range(max_retries):
                    try:
                        response = requests.get(img_url, headers=headers, timeout=30, allow_redirects=True)
                        if response.status_code == 200:
                            break
                    except (requests.exceptions.Timeout, requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                        if retry < max_retries - 1:
                            wait_time = (retry + 1) * 2  # 2, 4, 6秒
                            print(f"  网络错误，{wait_time}秒后重试 ({retry + 1}/{max_retries}): {str(e)[:50]}", flush=True)
                            import time
                            time.sleep(wait_time)
                        else:
                            raise e

                if response is None:
                    print(f"  下载失败: 无响应", flush=True)
                    results.append({'url': img_url, 'error': '下载失败: 无响应'})
                    continue

                print(f"  HTTP状态码: {response.status_code}, 内容长度: {len(response.content)}", flush=True)

                if response.status_code != 200:
                    print(f"  下载失败: HTTP {response.status_code}", flush=True)
                    results.append({'url': img_url, 'error': f'下载失败: HTTP {response.status_code}'})
                    continue

                if len(response.content) < 1000:
                    print(f"  内容过小，可能不是有效图片", flush=True)
                    results.append({'url': img_url, 'error': '下载内容无效'})
                    continue

                # 转换为PIL Image
                img = Image.open(io.BytesIO(response.content))

                # 去除背景
                output = remove(img)

                # 转换为base64
                buffered = io.BytesIO()
                output.save(buffered, format="PNG")
                img_base64 = base64.b64encode(buffered.getvalue()).decode()

                results.append({
                    'url': img_url,
                    'result': f'data:image/png;base64,{img_base64}'
                })

            except Exception as e:
                print(f"  处理失败: {str(e)}", flush=True)
                import traceback
                traceback.print_exc()
                results.append({'url': img_url, 'error': str(e)})

        print(f"批量处理完成，成功 {len([r for r in results if 'result' in r])} 张", flush=True)

        return jsonify({
            'success': True,
            'results': results
        })

    except Exception as e:
        print(f"批量处理失败: {e}", flush=True)
        return jsonify({
            'success': False,
            'error': str(e)
        })


@app.route('/download_batch_processed', methods=['POST'])
def download_batch_processed():
    """打包下载批量处理后的图片"""
    try:
        data = request.get_json()
        images = data.get('images', [])

        if not images:
            return jsonify({'success': False, 'error': '没有图片可下载'}), 400

        # 创建内存中的ZIP文件
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, img_data in enumerate(images):
                # 解码base64图片数据
                if img_data.startswith('data:image/png;base64,'):
                    img_data = img_data.replace('data:image/png;base64,', '')

                img_bytes = base64.b64decode(img_data)
                zf.writestr(f'processed_{i+1}.png', img_bytes)

        memory_file.seek(0)

        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='processed_images.zip'
        )

    except Exception as e:
        print(f"打包下载失败: {e}", flush=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/download_video', methods=['GET'])
def download_video():
    """代理下载抖音视频（避免403错误）"""
    import requests
    from flask import Response

    video_url = request.args.get('url', '')
    if not video_url:
        return jsonify({'success': False, 'error': '缺少视频URL'}), 400

    try:
        # 使用正确的请求头
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15',
            'Referer': 'https://www.douyin.com/',
            'Accept': '*/*',
        }

        # 流式下载视频
        response = requests.get(video_url, headers=headers, stream=True, timeout=180)

        if response.status_code != 200:
            return jsonify({'success': False, 'error': f'下载失败: HTTP {response.status_code}'}), 400

        # 获取文件名
        filename = 'douyin_video.mp4'

        # 流式返回视频
        def generate():
            for chunk in response.iter_content(chunk_size=8192):
                yield chunk

        return Response(
            generate(),
            mimetype='video/mp4',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': response.headers.get('Content-Length', '')
            }
        )

    except Exception as e:
        return jsonify({'success': False, 'error': f'下载失败: {str(e)}'}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5001))
    debug_mode = os.getenv('FLASK_ENV') != 'production'

    print("="*60)
    print(" "*20 + "Web抠图工具")
    print("="*60)
    print(f"\n访问地址: http://localhost:{port}")
    print("\n功能:")
    print("  1. 上传图片（支持多张）")
    print("  2. 自动去除背景")
    print("  3. 下载处理后的图片")
    print("\n按 Ctrl+C 停止服务")
    print("="*60 + "\n")

    app.run(debug=debug_mode, host='0.0.0.0', port=port)
