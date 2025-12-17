#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小黑盒游戏视频下载器 - 骨干逻辑版本
提取最核心的下载和合并逻辑
"""

import os
import re
import requests
import concurrent.futures
from urllib.parse import urljoin, urlparse
import tempfile
import shutil
import subprocess


class VideoDownloader:
    def __init__(self, base_url, headers=None):
        """初始化下载器"""
        self.base_url = base_url
        self.base_dir = urlparse(base_url).path.rsplit('/', 1)[0]
        self.domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
        self.headers = headers or {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.xiaoheihe.cn/',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def parse_m3u8(self, url):
        """解析m3u8文件，返回片段URL列表"""
        response = self.session.get(url)
        response.raise_for_status()
        content = response.text

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

    def download_segment(self, url, output_path):
        """下载单个片段"""
        response = self.session.get(url, stream=True)
        response.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=2 * 1024 * 1024):
                f.write(chunk)
        return True

    def download_segments(self, segments, output_dir, label="segments"):
        """并发下载所有片段"""
        os.makedirs(output_dir, exist_ok=True)
        downloaded_files = []
        total = len(segments)

        def download_with_index(index, url):
            filename = f"{label}_{index:05d}.m4s"
            filepath = os.path.join(output_dir, filename)
            if self.download_segment(url, filepath):
                return filepath
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(download_with_index, i, url): (i, url)
                       for i, url in enumerate(segments, 1)}

            completed = 0
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    downloaded_files.append(result)
                    completed += 1
                    print(f"下载进度 [{label}]: {completed}/{total} ({completed*100//total}%)", end='\r')

        print()  # 换行
        downloaded_files.sort()
        return downloaded_files

    def check_ffmpeg(self):
        """检查ffmpeg是否可用"""
        import sys
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
        """使用ffmpeg合并视频和音频"""
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
            '-loglevel', 'error',  # 只显示错误，不显示其他日志
            output_path
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=300)
        print(f"合并完成: {os.path.basename(output_path)}")

    def download_video(self, output_filename="output.mp4"):
        """下载并合并完整视频"""
        print(f"开始下载视频: {os.path.basename(output_filename)}")
        temp_dir = tempfile.mkdtemp(prefix="xhh_video_")

        try:
            # 1. 解析主m3u8文件
            print("解析主m3u8文件...", end='\r')
            response = self.session.get(self.base_url)
            response.raise_for_status()
            master_content = response.text

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
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                video_future = executor.submit(self.parse_m3u8, video_m3u8_url)
                audio_future = executor.submit(self.parse_m3u8, audio_m3u8_url)

                video_init, video_segments = video_future.result()
                audio_init, audio_segments = audio_future.result()

            print(f"视频片段: {len(video_segments)}个, 音频片段: {len(audio_segments)}个")

            # 3. 并发下载init片段
            video_init_path = None
            audio_init_path = None

            def download_video_init():
                nonlocal video_init_path
                if video_init:
                    video_init_path = os.path.join(temp_dir, "video_init.m4s")
                    self.download_segment(video_init, video_init_path)

            def download_audio_init():
                nonlocal audio_init_path
                if audio_init:
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

            # 4. 并发下载所有片段
            print("开始下载片段...")
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


def extract_all_videos_and_images_from_page(web_url):
    """从游戏页HTML中提取所有视频和图片URL"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.xiaoheihe.cn/',
    }

    session = requests.Session()
    session.headers.update(headers)

    response = session.get(web_url)
    response.raise_for_status()
    html_content = response.text

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


def main():
    """主函数"""
    import sys

    if len(sys.argv) > 1:
        input_url = sys.argv[1]
    else:
        input_url = input("请输入小黑盒游戏页URL: ").strip()
        if not input_url:
            print("错误: 未提供URL")
            return

    # 从页面中提取所有视频和图片URL
    video_m3u8_urls, image_urls = extract_all_videos_and_images_from_page(input_url)

    if not video_m3u8_urls and not image_urls:
        print("错误: 未能从页面中提取到视频或图片URL")
        return

    # 创建下载任务列表
    download_tasks = []

    # 添加视频下载任务
    for idx, video_url in enumerate(video_m3u8_urls, 1):
        output_filename = f"xhh_video_{idx:02d}.mp4"
        download_tasks.append(('video', video_url, idx, output_filename))

    # 添加图片下载任务
    for idx, image_url in enumerate(image_urls, 1):
        parsed_url = urlparse(image_url)
        image_url_clean = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        ext = image_url_clean.rsplit('.', 1)[-1].lower() if '.' in image_url_clean else 'jpg'
        output_filename = f"xhh_image_{idx:02d}.{ext}"
        download_tasks.append(('image', image_url_clean, idx, output_filename))

    if not download_tasks:
        return

    print(f"\n找到 {len(video_m3u8_urls)} 个视频, {len(image_urls)} 张图片")
    print(f"开始并发下载 {len(download_tasks)} 个任务...\n")

    def download_single_video(video_url, index, output_filename):
        """下载单个视频"""
        try:
            print(f"[视频 {index}] 开始下载...")
            downloader = VideoDownloader(video_url)
            downloader.download_video(output_filename)
            print(f"[视频 {index}] 下载完成: {output_filename}")
            return True
        except Exception as e:
            print(f"[视频 {index}] 下载失败: {e}")
            return False

    def download_single_image(image_url, index, output_filename):
        """下载单个图片"""
        try:
            response = requests.get(image_url, headers={'User-Agent': 'Mozilla/5.0'})
            response.raise_for_status()
            with open(output_filename, 'wb') as f:
                f.write(response.content)
            print(f"[图片 {index}] 下载完成: {output_filename}")
            return True
        except Exception as e:
            print(f"[图片 {index}] 下载失败: {e}")
            return False

    def execute_task(task):
        """执行下载任务"""
        task_type, url, index, output_filename = task
        if task_type == 'video':
            return download_single_video(url, index, output_filename)
        else:
            return download_single_image(url, index, output_filename)

    # 并发执行所有下载任务
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(execute_task, task): task for task in download_tasks}

        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            task_type, url, index, output_filename = task
            try:
                success = future.result()
                if not success:
                    print(f"✗ 任务失败: {task_type} {index}")
            except Exception as e:
                print(f"✗ 任务异常: {task_type} {index}: {e}")

    print("\n所有下载任务完成！")


if __name__ == "__main__":
    main()

