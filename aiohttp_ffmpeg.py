#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小黑盒游戏视频下载器 - aiohttp异步版本
使用aiohttp替换requests，添加并发控制和重试机制防止CDN限流
"""

import os
import re
import asyncio
import aiohttp
from urllib.parse import urljoin, urlparse
import tempfile
import shutil
import subprocess
import sys


async def retry_with_backoff(func, max_retries=3, base_delay=1.0):
    """
    指数退避重试装饰器
    
    Args:
        func: 要重试的异步函数
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
    
    Returns:
        函数执行结果
    """
    last_exception = None
    for attempt in range(max_retries):
        try:
            return await func()
        except (aiohttp.ClientError, asyncio.TimeoutError, aiohttp.ServerConnectionError) as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                raise last_exception
        except Exception as e:
            # 其他异常直接抛出，不重试
            raise e
    raise last_exception


class VideoDownloader:
    def __init__(self, base_url, session: aiohttp.ClientSession, 
                 semaphore: asyncio.Semaphore, segment_semaphore: asyncio.Semaphore,
                 headers=None):
        """
        初始化下载器
        
        Args:
            base_url: m3u8主文件的URL
            session: aiohttp ClientSession
            semaphore: 全局并发控制信号量
            segment_semaphore: 片段下载并发控制信号量
            headers: 请求头
        """
        self.base_url = base_url
        self.base_dir = urlparse(base_url).path.rsplit('/', 1)[0]
        self.domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
        self.session = session
        self.semaphore = semaphore
        self.segment_semaphore = segment_semaphore
        self.headers = headers or {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://store.steampowered.com/',
        }

    async def parse_m3u8(self, url):
        """
        解析m3u8文件，返回片段URL列表
        
        Args:
            url: m3u8文件的URL
        
        Returns:
            tuple: (init_segment, segments)
        """
        async def _fetch():
            async with self.session.get(url, headers=self.headers) as response:
                response.raise_for_status()
                return await response.text()
        
        content = await retry_with_backoff(_fetch)
        
        segments = []
        init_segment = None

        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('#EXT-X-MAP:URI='):
                init_uri = re.search(r'URI="([^"]+)"', line)
                if init_uri:
                    init_segment = init_uri.group(1)
            elif line and not line.startswith('#'):
                segments.append(line)

        base_url = url.rsplit('/', 1)[0] + '/'
        if init_segment:
            init_segment = urljoin(base_url, init_segment)
        segments = [urljoin(base_url, seg) for seg in segments]

        return init_segment, segments

    async def download_segment(self, url, output_path):
        """
        下载单个片段（带重试和并发控制）
        
        Args:
            url: 片段URL
            output_path: 输出路径
        
        Returns:
            bool: 是否成功
        """
        async def _download():
            async with self.segment_semaphore:  # 片段下载并发控制
                async with self.session.get(url, headers=self.headers) as response:
                    response.raise_for_status()
                    
                    # 使用同步文件写入（避免阻塞事件循环）
                    with open(output_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(2 * 1024 * 1024):  # 2MB chunks
                            f.write(chunk)
                    return True
        
        try:
            return await retry_with_backoff(_download)
        except Exception as e:
            print(f"下载失败 {url}: {e}")
            return False

    async def download_segments(self, segments, output_dir, label="segments"):
        """
        并发下载所有片段（使用asyncio.gather）
        
        Args:
            segments: 片段URL列表
            output_dir: 输出目录
            label: 标签（用于命名）
        
        Returns:
            list: 下载成功的文件路径列表
        """
        os.makedirs(output_dir, exist_ok=True)
        total = len(segments)

        async def download_with_index(index, url):
            filename = f"{label}_{index:05d}.m4s"
            filepath = os.path.join(output_dir, filename)
            if await self.download_segment(url, filepath):
                return filepath
            return None

        # 创建所有下载任务
        tasks = [download_with_index(i, url) for i, url in enumerate(segments, 1)]
        
        # 并发执行，但受semaphore控制
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        downloaded_files = []
        completed = 0
        for result in results:
            if isinstance(result, Exception):
                continue
            if result:
                downloaded_files.append(result)
                completed += 1
                print(f"下载进度 [{label}]: {completed}/{total} ({completed*100//total}%)", end='\r')

        print()  # 换行
        downloaded_files.sort()
        return downloaded_files

    def check_ffmpeg(self):
        """
        检查ffmpeg是否可用
        
        Returns:
            tuple: (是否可用, ffmpeg命令)
        """
        commands = ['ffmpeg.exe', 'ffmpeg'] if sys.platform == 'win32' else ['ffmpeg']
        
        for cmd in commands:
            try:
                subprocess.run([cmd, '-version'],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             check=True,
                             timeout=5)
                return True, cmd
            except Exception:
                continue
        
        return False, None

    def merge_video_audio(self, video_file, audio_file, output_path):
        """
        使用ffmpeg合并视频和音频
        """
        print("正在合并视频和音频...", end='\r')
        is_available, ffmpeg_cmd = self.check_ffmpeg()
        if not is_available:
            raise RuntimeError("ffmpeg未安装或不在PATH中")

        cmd = [
            ffmpeg_cmd,
            '-i', video_file,
            '-i', audio_file,
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-movflags', '+faststart',
            '-y',
            '-loglevel', 'error',
            output_path
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=300)
        print(f"合并完成: {os.path.basename(output_path)}")

    async def download_video(self, output_filename="output.mp4"):
        """
        下载并合并完整视频
        """
        print(f"开始下载视频: {os.path.basename(output_filename)}")
        temp_dir = tempfile.mkdtemp(prefix="xhh_video_")

        try:
            # 1. 解析主m3u8文件
            print("解析主m3u8文件...", end='\r')
            async def _fetch_master():
                async with self.session.get(self.base_url, headers=self.headers) as response:
                    response.raise_for_status()
                    return await response.text()
            
            master_content = await retry_with_backoff(_fetch_master)

            # 提取视频和音频m3u8 URL
            video_m3u8 = None
            audio_m3u8 = None

            for line in master_content.split('\n'):
                if 'TYPE=AUDIO' in line and 'URI=' in line:
                    audio_match = re.search(r'URI="([^"]+)"', line)
                    if audio_match:
                        audio_m3u8 = audio_match.group(1)
                        break

            for line in master_content.split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    if 'hls_264_0_video.m3u8' in line:
                        video_m3u8 = line
                        break
                    elif '.m3u8' in line and 'video' in line.lower():
                        video_m3u8 = line

            if not video_m3u8:
                for line in master_content.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#') and '.m3u8' in line:
                        video_m3u8 = line
                        break

            if not video_m3u8 or not audio_m3u8:
                raise ValueError("无法找到视频或音频m3u8文件")

            # 构建完整URL
            base_url_without_query = self.base_url.split('?')[0]
            base_url = base_url_without_query.rsplit('/', 1)[0] + '/'
            video_m3u8_url = urljoin(base_url, video_m3u8)
            audio_m3u8_url = urljoin(base_url, audio_m3u8)

            # 2. 并发解析视频和音频m3u8文件
            print("解析视频和音频片段列表...", end='\r')
            (video_init, video_segments), (audio_init, audio_segments) = await asyncio.gather(
                self.parse_m3u8(video_m3u8_url),
                self.parse_m3u8(audio_m3u8_url)
            )

            print(f"视频片段: {len(video_segments)}个, 音频片段: {len(audio_segments)}个")

            # 3. 并发下载init片段
            video_init_path = None
            audio_init_path = None

            async def download_video_init():
                nonlocal video_init_path
                if video_init:
                    video_init_path = os.path.join(temp_dir, "video_init.m4s")
                    await self.download_segment(video_init, video_init_path)

            async def download_audio_init():
                nonlocal audio_init_path
                if audio_init:
                    audio_init_path = os.path.join(temp_dir, "audio_init.m4s")
                    await self.download_segment(audio_init, audio_init_path)

            init_tasks = []
            if video_init:
                init_tasks.append(download_video_init())
            if audio_init:
                init_tasks.append(download_audio_init())

            if init_tasks:
                await asyncio.gather(*init_tasks)

            # 4. 并发下载所有片段
            print("开始下载片段...")
            video_files, audio_files = await asyncio.gather(
                self.download_segments(
                    video_segments,
                    os.path.join(temp_dir, "video"),
                    "video"
                ),
                self.download_segments(
                    audio_segments,
                    os.path.join(temp_dir, "audio"),
                    "audio"
                )
            )

            if not video_files or not audio_files:
                raise ValueError("下载片段失败")

            # 5. 合并视频片段
            print("合并视频片段...", end='\r')
            video_merged = os.path.join(temp_dir, "video_merged.m4s")
            if video_init_path:
                shutil.copy(video_init_path, video_merged)
                with open(video_merged, 'ab') as f:
                    for vfile in video_files:
                        with open(vfile, 'rb') as infile:
                            shutil.copyfileobj(infile, f)
            else:
                with open(video_merged, 'wb') as outfile:
                    for filepath in video_files:
                        with open(filepath, 'rb') as infile:
                            shutil.copyfileobj(infile, outfile)

            # 6. 合并音频片段
            print("合并音频片段...", end='\r')
            audio_merged = os.path.join(temp_dir, "audio_merged.m4s")
            if audio_init_path:
                shutil.copy(audio_init_path, audio_merged)
                with open(audio_merged, 'ab') as f:
                    for afile in audio_files:
                        with open(afile, 'rb') as infile:
                            shutil.copyfileobj(infile, f)
            else:
                with open(audio_merged, 'wb') as outfile:
                    for filepath in audio_files:
                        with open(filepath, 'rb') as infile:
                            shutil.copyfileobj(infile, outfile)

            # 7. 使用ffmpeg合并视频和音频
            self.merge_video_audio(video_merged, audio_merged, output_filename)

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


async def extract_all_videos_and_images_from_page(web_url, session: aiohttp.ClientSession):
    """
    从游戏页HTML中提取所有视频和图片URL
    
    Args:
        web_url: web端游戏页URL
        session: aiohttp ClientSession
    
    Returns:
        tuple: (video_m3u8_urls, image_urls) - 视频和图片URL列表
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.xiaoheihe.cn/',
    }

    async def _fetch():
        async with session.get(web_url, headers=headers) as response:
            response.raise_for_status()
            return await response.text()
    
    html_content = await retry_with_backoff(_fetch)

    video_m3u8_urls = []
    image_urls = []

    # 提取视频URL
    video_pattern = r'https?://[^"\'\s<>]+\.m3u8(?:\?[^"\'\s<>]*)?'
    video_matches = re.findall(video_pattern, html_content, re.IGNORECASE)
    for video_url in video_matches:
        if video_url not in video_m3u8_urls:
            video_m3u8_urls.append(video_url)

    # 提取图片URL
    image_url_pattern = r'https?://[^"\'\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?'
    all_image_matches = re.findall(image_url_pattern, html_content, re.IGNORECASE)

    game_image_keywords = ['gameimg', 'steam_item_assets', 'screenshot', 'game']
    for img_url in all_image_matches:
        if '/thumbnail/' in img_url:
            continue

        parsed_url = urlparse(img_url)
        if parsed_url.query and ('/thumbnail/' in parsed_url.query or 'thumbnail/' in parsed_url.query):
            continue

        url_lower = img_url.lower()
        is_game_image = any(keyword in url_lower for keyword in game_image_keywords)

        if is_game_image and img_url not in image_urls:
            image_urls.append(img_url)

    return video_m3u8_urls, image_urls


async def download_single_video(video_url, index, output_filename, session, semaphore, segment_semaphore):
    """
    下载单个视频
    
    Args:
        video_url: 视频m3u8 URL
        index: 视频索引
        output_filename: 输出文件名
        session: aiohttp ClientSession
        semaphore: 全局并发控制信号量
        segment_semaphore: 片段下载并发控制信号量
    """
    try:
        async with semaphore:  # 全局并发控制
            print(f"[视频 {index}] 开始下载...")
            downloader = VideoDownloader(video_url, session, semaphore, segment_semaphore)
            await downloader.download_video(output_filename)
            print(f"[视频 {index}] 下载完成: {output_filename}")
            return True
    except Exception as e:
        print(f"[视频 {index}] 下载失败: {e}")
        return False


async def download_single_image(image_url, index, output_filename, session, semaphore):
    """
    下载单个图片
    
    Args:
        image_url: 图片URL
        index: 图片索引
        output_filename: 输出文件名
        session: aiohttp ClientSession
        semaphore: 全局并发控制信号量
    """
    try:
        async with semaphore:  # 全局并发控制
            async def _fetch():
                async with session.get(image_url, headers={'User-Agent': 'Mozilla/5.0'}) as response:
                    response.raise_for_status()
                    return await response.read()
            
            image_data = await retry_with_backoff(_fetch)
            
            with open(output_filename, 'wb') as f:
                f.write(image_data)
            print(f"[图片 {index}] 下载完成: {output_filename}")
            return True
    except Exception as e:
        print(f"[图片 {index}] 下载失败: {e}")
        return False


async def main():
    """主函数"""
    if len(sys.argv) > 1:
        input_url = sys.argv[1]
    else:
        input_url = input("请输入小黑盒游戏页URL: ").strip()
        if not input_url:
            print("错误: 未提供URL")
            return

    # 创建连接池和会话
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=20)
    
    # 创建全局并发控制信号量（限制总并发数）
    global_semaphore = asyncio.Semaphore(25)
    # 创建片段下载并发控制信号量（每个视频的片段下载限制）
    segment_semaphore = asyncio.Semaphore(12)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        # 从页面中提取所有视频和图片URL
        video_m3u8_urls, image_urls = await extract_all_videos_and_images_from_page(input_url, session)

        if not video_m3u8_urls and not image_urls:
            print("错误: 未能从页面中提取到视频或图片URL")
            return

        # 创建下载任务列表
        download_tasks = []

        # 添加视频下载任务
        for idx, video_url in enumerate(video_m3u8_urls, 1):
            output_filename = f"xhh_video_{idx:02d}.mp4"
            download_tasks.append(
                download_single_video(video_url, idx, output_filename, session, global_semaphore, segment_semaphore)
            )

        # 添加图片下载任务
        for idx, image_url in enumerate(image_urls, 1):
            parsed_url = urlparse(image_url)
            image_url_clean = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
            ext = image_url_clean.rsplit('.', 1)[-1].lower() if '.' in image_url_clean else 'jpg'
            output_filename = f"xhh_image_{idx:02d}.{ext}"
            download_tasks.append(
                download_single_image(image_url_clean, idx, output_filename, session, global_semaphore)
            )

        if not download_tasks:
            return

        print(f"\n找到 {len(video_m3u8_urls)} 个视频, {len(image_urls)} 张图片")
        print(f"开始并发下载 {len(download_tasks)} 个任务...\n")

        # 并发执行所有下载任务
        results = await asyncio.gather(*download_tasks, return_exceptions=True)
        
        # 统计结果
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"✗ 任务异常: {result}")
            elif not result:
                print(f"✗ 任务失败")

        print("\n所有下载任务完成！")


if __name__ == "__main__":
    asyncio.run(main())

