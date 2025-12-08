#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小黑盒游戏视频下载器 - 并发测试版本
并发下载游戏页的所有视频和图片，视频的分片视频和音频同时并发下载
"""

import os
import re
import requests
import concurrent.futures
from urllib.parse import urljoin, urlparse, parse_qs
import tempfile
import shutil

try:
    import av
except ImportError:
    print("请安装PyAV库: pip install av")
    exit(1)


class VideoDownloader:
    def __init__(self, base_url, headers=None):
        """
        初始化下载器
        
        Args:
            base_url: m3u8主文件的URL
            headers: 请求头
        """
        self.base_url = base_url
        self.base_dir = urlparse(base_url).path.rsplit('/', 1)[0]
        self.domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
        self.headers = headers or {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0',
            'Referer': 'https://www.xiaoheihe.cn/',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept': '*/*',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
    
    def download_image(self, image_url, output_path):
        """
        下载图片
        
        Args:
            image_url: 图片URL
            output_path: 输出路径
        """
        try:
            print(f"正在下载图片: {image_url}")
            response = self.session.get(image_url, stream=True)
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"图片下载完成: {output_path}")
            return True
        except Exception as e:
            print(f"图片下载失败 {image_url}: {e}")
            return False
        
    def parse_m3u8(self, url):
        """
        解析m3u8文件，返回片段URL列表
        
        Args:
            url: m3u8文件的URL
            
        Returns:
            tuple: (init_segment, segments)
        """
        response = self.session.get(url)
        response.raise_for_status()
        content = response.text
        
        segments = []
        init_segment = None
        
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('#EXT-X-MAP:URI='):
                # 提取init片段
                init_uri = re.search(r'URI="([^"]+)"', line)
                if init_uri:
                    init_segment = init_uri.group(1)
            elif line and not line.startswith('#'):
                # 这是一个片段URL
                segments.append(line)
        
        # 构建完整URL
        base_url = url.rsplit('/', 1)[0] + '/'
        if init_segment:
            init_segment = urljoin(base_url, init_segment)
        segments = [urljoin(base_url, seg) for seg in segments]
        
        return init_segment, segments
    
    def download_segment(self, url, output_path):
        """
        下载单个片段
        
        Args:
            url: 片段URL
            output_path: 输出路径
            
        Returns:
            bool: 是否成功
        """
        try:
            response = self.session.get(url, stream=True)
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except Exception as e:
            print(f"下载失败 {url}: {e}")
            return False
    
    def download_segments(self, segments, output_dir, label="segments"):
        """
        并发下载所有片段
        
        Args:
            segments: 片段URL列表
            output_dir: 输出目录
            label: 标签（用于命名）
            
        Returns:
            list: 下载成功的文件路径列表
        """
        os.makedirs(output_dir, exist_ok=True)
        downloaded_files = []
        
        def download_with_index(index, url):
            filename = f"{label}_{index:05d}.m4s"
            filepath = os.path.join(output_dir, filename)
            if self.download_segment(url, filepath):
                return filepath
            return None
        
        # 并发下载
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(download_with_index, i, url): (i, url) 
                      for i, url in enumerate(segments, 1)}
            
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    downloaded_files.append(result)
                    print(f"已下载: {os.path.basename(result)}")
        
        # 按索引排序
        downloaded_files.sort()
        return downloaded_files
    
    def merge_segments(self, files, output_path):
        """
        合并多个m4s片段为一个文件
        
        Args:
            files: 文件路径列表
            output_path: 输出文件路径
        """
        with open(output_path, 'wb') as outfile:
            for filepath in files:
                with open(filepath, 'rb') as infile:
                    shutil.copyfileobj(infile, outfile)
    
    def merge_video_audio(self, video_file, audio_file, output_path):
        """
        使用PyAV合并视频和音频
        
        Args:
            video_file: 视频文件路径
            audio_file: 音频文件路径
            output_path: 输出文件路径
        """
        print("正在合并视频和音频...")
        
        # 打开输入文件
        video_container = av.open(video_file)
        audio_container = av.open(audio_file)
        
        # 打开输出文件
        output_container = av.open(output_path, mode='w', format='mp4')
        
        # 获取视频流和音频流
        video_stream = video_container.streams.video[0]
        audio_stream = audio_container.streams.audio[0]
        
        # 添加输出流（使用与输入相同的编解码器，保持质量）
        # 从输入流获取编解码器信息
        video_codec = video_stream.codec.name
        audio_codec = audio_stream.codec.name
        
        # 获取视频帧率
        video_framerate = None
        if hasattr(video_stream, 'average_rate') and video_stream.average_rate:
            video_framerate = video_stream.average_rate
        elif hasattr(video_stream.codec_context, 'framerate') and video_stream.codec_context.framerate:
            video_framerate = video_stream.codec_context.framerate
        else:
            video_framerate = 30  # 默认30fps
        
        # 创建视频输出流
        output_video_stream = output_container.add_stream(
            codec_name=video_codec,
            rate=video_framerate,
            width=video_stream.width,
            height=video_stream.height
        )
        # 复制编解码器参数以保持质量
        if video_stream.codec_context.bit_rate:
            output_video_stream.codec_context.bit_rate = video_stream.codec_context.bit_rate
        if hasattr(video_stream.codec_context, 'pix_fmt') and video_stream.codec_context.pix_fmt:
            output_video_stream.codec_context.pix_fmt = video_stream.codec_context.pix_fmt
        
        # 获取音频采样率
        audio_rate = None
        if hasattr(audio_stream, 'rate') and audio_stream.rate:
            audio_rate = audio_stream.rate
        elif hasattr(audio_stream.codec_context, 'sample_rate') and audio_stream.codec_context.sample_rate:
            audio_rate = audio_stream.codec_context.sample_rate
        else:
            audio_rate = 44100  # 默认44.1kHz
        
        # 创建音频输出流
        output_audio_stream = output_container.add_stream(
            codec_name=audio_codec,
            rate=audio_rate
        )
        # 复制音频参数
        if audio_stream.codec_context.bit_rate:
            output_audio_stream.codec_context.bit_rate = audio_stream.codec_context.bit_rate
        if audio_stream.codec_context.sample_rate:
            output_audio_stream.codec_context.sample_rate = audio_stream.codec_context.sample_rate
        try:
            if hasattr(audio_stream.codec_context, 'layout'):
                output_audio_stream.codec_context.layout = audio_stream.codec_context.layout
        except:
            pass
        
        # 解码并重新编码视频流（使用相同参数保持质量）
        print("处理视频流...")
        video_container.seek(0)
        for frame in video_container.decode(video_stream):
            # 重新编码帧
            for packet in output_video_stream.encode(frame):
                output_container.mux(packet)
        # 刷新编码器
        for packet in output_video_stream.encode():
            output_container.mux(packet)
        
        # 解码并重新编码音频流（使用相同参数保持质量）
        print("处理音频流...")
        audio_container.seek(0)
        for frame in audio_container.decode(audio_stream):
            # 重新编码帧
            for packet in output_audio_stream.encode(frame):
                output_container.mux(packet)
        # 刷新编码器
        for packet in output_audio_stream.encode():
            output_container.mux(packet)
        
        # 关闭所有容器
        video_container.close()
        audio_container.close()
        output_container.close()
        
        print(f"合并完成: {output_path}")
    
    def download_video(self, output_filename="output.mp4"):
        """
        下载并合并完整视频（视频分片和音频分片同时并发下载）
        
        Args:
            output_filename: 输出文件名
        """
        print(f"开始下载视频: {self.base_url}")
        
        # 创建临时目录
        temp_dir = tempfile.mkdtemp(prefix="xhh_video_")
        print(f"临时目录: {temp_dir}")
        
        try:
            # 1. 解析主m3u8文件
            print("解析主m3u8文件...")
            response = self.session.get(self.base_url)
            response.raise_for_status()
            master_content = response.text
            
            # 提取视频和音频m3u8 URL
            video_m3u8 = None
            audio_m3u8 = None
            
            # 首先提取音频m3u8（从EXT-X-MEDIA行）
            for line in master_content.split('\n'):
                if 'TYPE=AUDIO' in line and 'URI=' in line:
                    audio_match = re.search(r'URI="([^"]+)"', line)
                    if audio_match:
                        audio_m3u8 = audio_match.group(1)
                        break
            
            # 提取视频m3u8（优先选择最高质量的，即hls_264_0_video.m3u8）
            for line in master_content.split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    # 检查是否是视频流（通常在EXT-X-STREAM-INF之后）
                    if 'hls_264_0_video.m3u8' in line:
                        video_m3u8 = line
                        break
                    elif '.m3u8' in line and 'video' in line.lower():
                        video_m3u8 = line
                        # 继续查找更高质量的
            
            # 如果还没找到视频m3u8，使用第一个非注释行
            if not video_m3u8:
                for line in master_content.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#') and '.m3u8' in line:
                        video_m3u8 = line
                        break
            
            if not video_m3u8 or not audio_m3u8:
                raise ValueError(f"无法找到视频或音频m3u8文件。视频: {video_m3u8}, 音频: {audio_m3u8}")
            
            # 构建完整URL（去掉查询参数）
            base_url_without_query = self.base_url.split('?')[0]
            base_url = base_url_without_query.rsplit('/', 1)[0] + '/'
            video_m3u8_url = urljoin(base_url, video_m3u8)
            audio_m3u8_url = urljoin(base_url, audio_m3u8)
            
            print(f"视频m3u8: {video_m3u8_url}")
            print(f"音频m3u8: {audio_m3u8_url}")
            
            # 2. 并发解析视频和音频m3u8文件
            print("并发解析视频和音频片段...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                video_future = executor.submit(self.parse_m3u8, video_m3u8_url)
                audio_future = executor.submit(self.parse_m3u8, audio_m3u8_url)
                
                video_init, video_segments = video_future.result()
                audio_init, audio_segments = audio_future.result()
            
            print(f"视频片段数: {len(video_segments)}")
            print(f"音频片段数: {len(audio_segments)}")
            
            # 3. 并发下载init片段
            video_init_path = None
            audio_init_path = None
            
            def download_video_init():
                nonlocal video_init_path
                if video_init:
                    print("下载视频init片段...")
                    video_init_path = os.path.join(temp_dir, "video_init.m4s")
                    self.download_segment(video_init, video_init_path)
            
            def download_audio_init():
                nonlocal audio_init_path
                if audio_init:
                    print("下载音频init片段...")
                    audio_init_path = os.path.join(temp_dir, "audio_init.m4s")
                    self.download_segment(audio_init, audio_init_path)
            
            init_tasks = []
            if video_init:
                init_tasks.append(download_video_init)
            if audio_init:
                init_tasks.append(download_audio_init)
            
            if init_tasks:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(init_tasks)) as executor:
                    executor.map(lambda f: f(), init_tasks)
            
            # 4. 并发下载所有片段（视频分片和音频分片同时并发下载）
            print("开始并发下载视频和音频片段（同时进行）...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                video_future = executor.submit(
                    self.download_segments, 
                    video_segments, 
                    os.path.join(temp_dir, "video"), 
                    "video"
                )
                audio_future = executor.submit(
                    self.download_segments, 
                    audio_segments, 
                    os.path.join(temp_dir, "audio"), 
                    "audio"
                )
                
                video_files = video_future.result()
                audio_files = audio_future.result()
            
            if not video_files or not audio_files:
                raise ValueError("下载片段失败")
            
            # 5. 合并视频片段
            print("合并视频片段...")
            video_merged = os.path.join(temp_dir, "video_merged.m4s")
            if video_init_path:
                # 先复制init，再合并其他片段
                shutil.copy(video_init_path, video_merged)
                with open(video_merged, 'ab') as f:
                    for vfile in video_files:
                        with open(vfile, 'rb') as infile:
                            shutil.copyfileobj(infile, f)
            else:
                self.merge_segments(video_files, video_merged)
            
            # 6. 合并音频片段
            print("合并音频片段...")
            audio_merged = os.path.join(temp_dir, "audio_merged.m4s")
            if audio_init_path:
                # 先复制init，再合并其他片段
                shutil.copy(audio_init_path, audio_merged)
                with open(audio_merged, 'ab') as f:
                    for afile in audio_files:
                        with open(afile, 'rb') as infile:
                            shutil.copyfileobj(infile, f)
            else:
                self.merge_segments(audio_files, audio_merged)
            
            # 7. 使用PyAV合并视频和音频
            self.merge_video_audio(video_merged, audio_merged, output_filename)
            
            print(f"\n下载完成! 文件保存为: {output_filename}")
            
        finally:
            # 清理临时文件
            print("清理临时文件...")
            shutil.rmtree(temp_dir, ignore_errors=True)


def get_web_url_from_app_share(app_share_url):
    """
    从app分享链接获取web端URL
    
    Args:
        app_share_url: app分享链接，如 https://api.xiaoheihe.cn/game/share_game_detail?appid=3159330&game_type=pc&h_camp=game&h_src=YXBwX3NoYXJl
        
    Returns:
        str: web端URL
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    # 访问app分享链接，获取重定向后的URL
    response = session.get(app_share_url, allow_redirects=True)
    response.raise_for_status()
    
    # 返回最终重定向的URL
    return response.url


def extract_all_videos_and_images_from_page(web_url):
    """
    从游戏页HTML中提取所有视频和图片URL（使用HTML正则匹配方法）
    
    Args:
        web_url: web端游戏页URL
        
    Returns:
        tuple: (video_m3u8_urls, image_urls) - 视频和图片URL列表
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0',
        'Referer': 'https://www.xiaoheihe.cn/',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        response = session.get(web_url)
        response.raise_for_status()
        html_content = response.text
        
        video_m3u8_urls = []
        image_urls = []
        
        # 提取视频URL（使用正则匹配所有.m3u8 URL）
        video_pattern = r'https?://[^"\'\s<>]+\.m3u8(?:\?[^"\'\s<>]*)?'
        video_matches = re.findall(video_pattern, html_content, re.IGNORECASE)
        for video_url in video_matches:
            if video_url not in video_m3u8_urls:
                video_m3u8_urls.append(video_url)
                print(f"找到视频URL: {video_url}")
        
        # 提取图片URL（使用正则匹配）
        image_url_pattern = r'https?://[^"\'\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?'
        all_image_matches = re.findall(image_url_pattern, html_content, re.IGNORECASE)
        
        # 过滤游戏预览截图URL
        game_image_keywords = ['gameimg', 'steam_item_assets', 'screenshot', 'game']
        for img_url in all_image_matches:
            # 排除包含/thumbnail/的URL
            if '/thumbnail/' in img_url:
                continue
            
            # 检查查询参数中是否有thumbnail路径
            parsed_url = urlparse(img_url)
            if parsed_url.query and ('/thumbnail/' in parsed_url.query or 'thumbnail/' in parsed_url.query):
                continue
            
            # 只保留游戏预览截图相关的URL
            url_lower = img_url.lower()
            is_game_image = any(keyword in url_lower for keyword in game_image_keywords)
            
            if is_game_image and img_url not in image_urls:
                image_urls.append(img_url)
                print(f"找到游戏预览截图URL: {img_url}")
        
        print(f"成功提取 {len(video_m3u8_urls)} 个视频URL, {len(image_urls)} 个图片URL")
        return video_m3u8_urls, image_urls
        
    except Exception as e:
        print(f"提取视频和图片URL失败: {e}")
        return [], []


def is_app_share_url(url):
    """
    判断是否为app分享链接
    
    Args:
        url: URL字符串
        
    Returns:
        bool: 是否为app分享链接
    """
    return 'api.xiaoheihe.cn/game/share_game_detail' in url


def get_game_page_url(input_url):
    """
    获取游戏页web端URL
    
    Args:
        input_url: 输入的URL（可能是web端URL或app分享链接）
        
    Returns:
        str: web端URL
    """
    if is_app_share_url(input_url):
        print("检测到app分享链接，正在获取web端URL...")
        web_url = get_web_url_from_app_share(input_url)
        print(f"获取到web端URL: {web_url}")
        return web_url
    else:
        print("检测到web端URL，直接使用")
        return input_url


def download_single_video(video_url, index):
    """
    下载单个视频
    
    Args:
        video_url: 视频m3u8 URL
        index: 视频索引
    """
    try:
        output_filename = f"xhh_video_{index:02d}.mp4"
        downloader = VideoDownloader(video_url)
        downloader.download_video(output_filename)
        return True
    except Exception as e:
        print(f"下载视频 {video_url} 失败: {e}")
        return False


def download_single_image(image_url, index):
    """
    下载单个图片
    
    Args:
        image_url: 图片URL
        index: 图片索引
    """
    try:
        # 获取图片扩展名
        parsed_url = urlparse(image_url)
        # 去掉查询参数，获取原始URL
        image_url_clean = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        # 从URL中提取文件扩展名
        if '.' in image_url_clean:
            ext = image_url_clean.rsplit('.', 1)[-1].lower()
            if ext not in ['jpg', 'jpeg', 'png', 'webp']:
                ext = 'jpg'
        else:
            ext = 'jpg'
        
        output_filename = f"xhh_image_{index:02d}.{ext}"
        
        # 创建临时VideoDownloader用于下载图片
        temp_downloader = VideoDownloader(image_url_clean)
        return temp_downloader.download_image(image_url_clean, output_filename)
    except Exception as e:
        print(f"下载图片 {image_url} 失败: {e}")
        return False


def main():
    """主函数"""
    import sys
    
    # 从命令行参数获取URL，如果没有则提示用户输入
    if len(sys.argv) > 1:
        input_url = sys.argv[1]
    else:
        input_url = input("请输入小黑盒游戏页URL（web端或app分享链接）: ").strip()
        if not input_url:
            print("错误: 未提供URL")
            return
    
    # 获取游戏页web端URL
    web_url = get_game_page_url(input_url)
    
    # 从页面中提取所有视频和图片URL
    print("\n正在从页面提取所有视频和图片URL...")
    video_m3u8_urls, image_urls = extract_all_videos_and_images_from_page(web_url)
    
    if not video_m3u8_urls and not image_urls:
        print("错误: 未能从页面中提取到视频或图片URL")
        return
    
    print(f"\n找到 {len(video_m3u8_urls)} 个视频，{len(image_urls)} 张图片")
    
    # 创建下载任务列表
    download_tasks = []
    
    # 添加视频下载任务
    for idx, video_url in enumerate(video_m3u8_urls, 1):
        download_tasks.append(('video', video_url, idx))
        print(f"添加视频下载任务 {idx}: {video_url}")
    
    # 添加图片下载任务
    for idx, image_url in enumerate(image_urls, 1):
        download_tasks.append(('image', image_url, idx))
        print(f"添加图片下载任务 {idx}: {image_url}")
    
    # 并发执行所有下载任务
    print(f"\n开始并发下载 {len(download_tasks)} 个任务...")
    
    def execute_task(task):
        task_type, url, index = task
        if task_type == 'video':
            return download_single_video(url, index)
        else:
            return download_single_image(url, index)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(execute_task, task): task for task in download_tasks}
        
        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            task_type, url, index = task
            try:
                success = future.result()
                if success:
                    print(f"✓ 任务完成: {task_type} {index}")
                else:
                    print(f"✗ 任务失败: {task_type} {index}")
            except Exception as e:
                print(f"✗ 任务异常: {task_type} {index}: {e}")
    
    print("\n所有下载任务完成！")


if __name__ == "__main__":
    main()

