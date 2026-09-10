#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
图标生成脚本 - 使用 Pillow 直接绘制
从 Python 代码直接生成多分辨率的 ICO/PNG 图标文件。

这种方法不依赖外部 SVG 转换库，兼容性最好。

使用方法：
    python assets/icons/source/generate_icons_pillow.py
    python assets/icons/source/generate_icons_pillow.py --sizes 16,32,48,64,128,256,512
"""

import os
import sys
import argparse
from pathlib import Path
from PIL import Image, ImageDraw

# 项目根目录（本脚本位于 assets/icons/source/ 下，向上三级）
ROOT = Path(__file__).resolve().parents[3]
RESOURCES_DIR = ROOT / "assets" / "icons"
OUTPUT_DIR = RESOURCES_DIR / "png"
BUILD_DIR = ROOT / "build"

# 标准图标分辨率 (Windows 推荐)
STANDARD_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]

# 颜色定义
COLORS = {
    "bg_start": (30, 64, 175),      # #1E40AF - 深蓝
    "bg_end": (59, 130, 246),       # #3B82F6 - 亮蓝
    "wave_start": (249, 115, 22),   # #F97316 - 亮橙
    "wave_mid": (251, 146, 60),     # #FB923C - 浅橙
    "wave_end": (253, 186, 116),    # #FDBA74 - 淡橙
    "accent": (234, 88, 12),        # #EA580C - 深橙
    "highlight": (6, 182, 212),     # #06B6D4 - 青色
    "white": (255, 255, 255),
    "dark_gray": (100, 116, 139),   # #64748B
    "light_gray": (226, 232, 240),  # #E2E8F0
    # 新的边框/描边颜色
    "border_white": (255, 255, 255), # 白色光环
    "border_light": (240, 244, 252),  # 极浅蓝光环
}


def create_rounded_rect(size, radius, color):
    """创建圆角矩形"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=color)
    return img


def create_gradient_background(size, radius):
    """创建渐变背景"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    pixels = img.load()
    
    for y in range(size):
        for x in range(size):
            # 计算渐变比例
            ratio_x = x / size
            ratio_y = y / size
            ratio = (ratio_x + ratio_y) / 2
            
            # 线性插值
            r = int(COLORS["bg_start"][0] * (1 - ratio) + COLORS["bg_end"][0] * ratio)
            g = int(COLORS["bg_start"][1] * (1 - ratio) + COLORS["bg_end"][1] * ratio)
            b = int(COLORS["bg_start"][2] * (1 - ratio) + COLORS["bg_end"][2] * ratio)
            
            pixels[x, y] = (r, g, b, 255)
    
    # 应用圆角遮罩
    mask = Image.new('L', (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    
    # 组合
    result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    
    return result


def draw_app_icon(size):
    """
    绘制应用主图标
    设计：蓝色渐变背景 + 白色外光环 + 增强温度波形 + 数据点 + 品牌文字
    
    关键改进：
    1. 白色外边框确保在任何背景下都清晰可见
    2. 增强橙色波形作为主要视觉标识
    3. 波形宽度增加约 40%
    """
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 计算缩放比例
    scale = size / 512
    
    # 0. 先绘制白色外光环
    border_width = max(4, int(16 * scale))  # 边框宽度 (大尺寸至少4px, 512尺寸为16px)
    outer_radius = int(96 * scale) + border_width
    inner_radius = int(96 * scale)
    
    # 绘制白色圆角矩形作为外环
    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=outer_radius,
        fill=(255, 255, 255, 255)  # 纯白
    )
    
    # 1. 创建圆角渐变背景 (比外光环小一圈)
    inner_size = size - 2 * border_width
    bg = create_gradient_background_with_margin(inner_size, inner_radius, 0, 0)
    
    # 创建新画布放置背景
    bg_img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    bg_img.paste(bg, (border_width, border_width))
    
    # 使用 alpha_composite 合成 - 蓝色背景会覆盖白色内部区域
    img = Image.alpha_composite(img, bg_img)
    draw = ImageDraw.Draw(img)
    
    # 2. 背景装饰网格 (仅大尺寸显示)
    if size >= 48:
        grid_alpha = int(255 * 0.15)
        grid_color = (255, 255, 255, grid_alpha)
        line_width = max(1, int(scale))
        for pos in [128, 256, 384]:
            x = int(pos * scale)
            draw.line([(x, int(128 * scale)), (x, int(384 * scale))], 
                     fill=grid_color, width=line_width)
        for pos in [256]:
            y = int(pos * scale)
            draw.line([(int(128 * scale), y), (int(384 * scale), y)], 
                     fill=grid_color, width=line_width)
    
    # 3. 温度波形曲线 (增强视觉权重)
    wave_points = [
        (int(80 * scale), int(280 * scale)),
        (int(120 * scale), int(280 * scale)),
        (int(140 * scale), int(200 * scale)),
        (int(180 * scale), int(200 * scale)),
        (int(220 * scale), int(200 * scale)),
        (int(240 * scale), int(320 * scale)),
        (int(280 * scale), int(320 * scale)),
        (int(320 * scale), int(320 * scale)),
        (int(340 * scale), int(160 * scale)),
        (int(380 * scale), int(160 * scale)),
        (int(420 * scale), int(160 * scale)),
        (int(432 * scale), int(240 * scale)),
    ]
    
    # 波形宽度增加约 40% (从 8px 增加到 11px)
    wave_width = max(3, int(11 * scale))
    wave_alpha = 255
    
    # 先用深色描边增加立体感
    dark_wave_alpha = int(255 * 0.3)
    for i in range(len(wave_points) - 1):
        p1 = wave_points[i]
        p2 = wave_points[i + 1]
        # 深色描边 (稍微偏移)
        draw.line([(p1[0], p1[1] + 2), (p2[0], p2[1] + 2)], 
                 fill=(139, 69, 19, dark_wave_alpha), width=wave_width)
    
    # 使用橙色渐变的曲线
    for i in range(len(wave_points) - 1):
        p1 = wave_points[i]
        p2 = wave_points[i + 1]
        
        # 计算颜色渐变
        ratio = i / max(len(wave_points) - 2, 1)
        r = int(COLORS["wave_start"][0] * (1 - ratio) + COLORS["wave_end"][0] * ratio)
        g = int(COLORS["wave_start"][1] * (1 - ratio) + COLORS["wave_end"][1] * ratio)
        b = int(COLORS["wave_start"][2] * (1 - ratio) + COLORS["wave_end"][2] * ratio)
        
        color = (r, g, b, wave_alpha)
        draw.line([p1, p2], fill=color, width=wave_width)
    
    # 4. 数据点标记 (增大尺寸)
    data_points = [
        (int(180 * scale), int(200 * scale), int(14 * scale)),
        (int(280 * scale), int(320 * scale), int(14 * scale)),
        (int(380 * scale), int(160 * scale), int(16 * scale)),
    ]
    
    for x, y, r in data_points:
        # 白色边框 (加粗)
        draw.ellipse([x - r - max(2, int(4 * scale)), y - r - max(2, int(4 * scale)),
                     x + r + max(2, int(4 * scale)), y + r + max(2, int(4 * scale))],
                    fill=(255, 255, 255, 255))
        # 橙色填充
        draw.ellipse([x - r, y - r, x + r, y + r],
                    fill=COLORS["accent"] + (255,))
    
    # 5. 温度计图标 (仅大尺寸显示)
    if size >= 64:
        thermometer_x = int(64 * scale)
        thermometer_y = int(180 * scale)
        thermometer_w = int(24 * scale)
        thermometer_h = int(100 * scale)
        therm_r = int(12 * scale)
        
        # 温度计外框
        draw.rounded_rectangle(
            [thermometer_x, thermometer_y, 
             thermometer_x + thermometer_w, thermometer_y + thermometer_h],
            radius=therm_r,
            fill=(255, 255, 255, 230)
        )
        
        # 温度计内部液体
        inner_margin = max(1, int(6 * scale))
        draw.rounded_rectangle(
            [thermometer_x + inner_margin, thermometer_y + inner_margin,
             thermometer_x + thermometer_w - inner_margin, 
             thermometer_y + thermometer_h - inner_margin],
            radius=max(1, int(6 * scale)),
            fill=COLORS["wave_start"] + (255,)
        )
        
        # 温度计底部圆球
        bulb_r = int(10 * scale)
        draw.ellipse(
            [thermometer_x + thermometer_w // 2 - bulb_r, 
             thermometer_y + thermometer_h - bulb_r * 2,
             thermometer_x + thermometer_w // 2 + bulb_r,
             thermometer_y + thermometer_h],
            fill=COLORS["accent"] + (255,)
        )
    
    # 6. 时间轴 (仅中尺寸以上显示)
    if size >= 32:
        timeline_y = int(380 * scale)
        timeline_x1 = int(80 * scale)
        timeline_x2 = int(432 * scale)
        timeline_width = max(1, int(3 * scale))
        draw.line([(timeline_x1, timeline_y), (timeline_x2, timeline_y)], 
                 fill=(255, 255, 255, 200), width=timeline_width)
        
        # 刻度点
        tick_r = max(1, int(4 * scale))
        for tick_x_ratio in [0.14, 0.35, 0.62, 0.85]:
            tx = int(timeline_x1 + (timeline_x2 - timeline_x1) * tick_x_ratio)
            draw.ellipse([tx - tick_r, timeline_y - tick_r, 
                         tx + tick_r, timeline_y + tick_r],
                        fill=(255, 255, 255, 128))
    
    # 7. 品牌文字 (仅大尺寸显示)
    if size >= 128:
        from PIL import ImageFont
        try:
            font_size = int(32 * scale)
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:  # 系统缺 arial.ttf 时回退 PIL 默认位图字体
            font = ImageFont.load_default()
        
        text = "MTA"
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        text_x = (size - text_width) // 2
        text_y = int(430 * scale)
        
        # 文字阴影增加可读性
        shadow_alpha = int(255 * 0.3)
        draw.text((text_x + 1, text_y + 1), text, fill=(0, 0, 0, shadow_alpha), font=font)
        draw.text((text_x, text_y), text, fill=(255, 255, 255, 240), font=font)
    
    return img


def create_gradient_background_with_margin(size, radius, offset_x, offset_y):
    """创建带偏移的渐变背景（用于外光环内部）"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    pixels = img.load()
    
    for y in range(size):
        for x in range(size):
            ratio_x = x / size
            ratio_y = y / size
            ratio = (ratio_x + ratio_y) / 2
            
            r = int(COLORS["bg_start"][0] * (1 - ratio) + COLORS["bg_end"][0] * ratio)
            g = int(COLORS["bg_start"][1] * (1 - ratio) + COLORS["bg_end"][1] * ratio)
            b = int(COLORS["bg_start"][2] * (1 - ratio) + COLORS["bg_end"][2] * ratio)
            
            pixels[x, y] = (r, g, b, 255)
    
    # 应用圆角遮罩
    mask = Image.new('L', (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    
    result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    
    return result


def draw_file_icon(size):
    """
    绘制文件关联图标
    设计：白色文件 + 温度波形预览 + TPX 扩展名标识
    """
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    scale = size / 256
    
    # 1. 文件主体
    file_x1 = int(40 * scale)
    file_y1 = int(24 * scale)
    file_x2 = int(216 * scale)
    file_y2 = int(232 * scale)
    fold_x = int(160 * scale)
    fold_y = int(24 * scale)
    fold_x2 = int(216 * scale)
    fold_y2 = int(80 * scale)
    
    # 圆角文件形状
    file_bg = (248, 250, 252, 255)  # #F8FAFC
    file_border = (148, 163, 184, 255)  # #94A3B8
    
    # 绘制文件主体
    draw.polygon([
        (file_x1, file_y1),
        (fold_x, fold_y),
        (fold_x2, fold_y2),
        (file_x2, file_y2),
        (file_x1, file_y2)
    ], fill=file_bg, outline=file_border)
    
    # 2. 文件折角
    fold_shape = [(fold_x, fold_y), (fold_x2, fold_y2), (fold_x, fold_y2)]
    draw.polygon(fold_shape, fill=(203, 213, 225, 128), outline=file_border)
    
    # 3. 文件头标签
    label_y = int(112 * scale)
    label_h = int(8 * scale)
    draw.rounded_rectangle(
        [file_x1 + int(16 * scale), label_y, file_x2 - int(16 * scale), label_y + label_h],
        radius=max(1, int(4 * scale)),
        fill=COLORS["bg_end"] + (200,)
    )
    
    # 4. 图表预览区域
    chart_x1 = file_x1 + int(8 * scale)
    chart_y1 = int(144 * scale)
    chart_x2 = file_x2 - int(8 * scale)
    chart_y2 = int(208 * scale)
    
    # 图表背景
    chart_bg = (255, 255, 255, 255)
    chart_border = (226, 232, 240, 255)
    chart_radius = max(1, int(8 * scale))
    draw.rounded_rectangle([chart_x1, chart_y1, chart_x2, chart_y2], 
                          radius=chart_radius, fill=chart_bg, outline=chart_border)
    
    # 5. 迷你温度曲线
    wave_points = [
        (int(56 * scale), int(180 * scale)),
        (int(72 * scale), int(180 * scale)),
        (int(80 * scale), int(160 * scale)),
        (int(96 * scale), int(160 * scale)),
        (int(112 * scale), int(160 * scale)),
        (int(120 * scale), int(200 * scale)),
        (int(136 * scale), int(200 * scale)),
        (int(152 * scale), int(200 * scale)),
        (int(160 * scale), int(152 * scale)),
        (int(176 * scale), int(152 * scale)),
        (int(192 * scale), int(152 * scale)),
        (int(200 * scale), int(180 * scale)),
    ]
    
    wave_width = max(1, int(3 * scale))
    for i in range(len(wave_points) - 1):
        p1 = wave_points[i]
        p2 = wave_points[i + 1]
        draw.line([p1, p2], fill=COLORS["wave_start"] + (255,), width=wave_width)
    
    # 6. 数据点
    data_dots = [
        (int(96 * scale), int(160 * scale), int(4 * scale), COLORS["wave_start"]),
        (int(136 * scale), int(200 * scale), int(4 * scale), COLORS["wave_mid"]),
        (int(176 * scale), int(152 * scale), int(5 * scale), COLORS["accent"]),
    ]
    
    for x, y, r, color in data_dots:
        # 白色边框
        draw.ellipse([x - r - 1, y - r - 1, x + r + 1, y + r + 1],
                    fill=(255, 255, 255, 255))
        # 彩色填充
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color + (255,))
    
    # 7. 时间基线
    baseline_y = int(204 * scale)
    draw.line([(int(56 * scale), baseline_y), (int(200 * scale), baseline_y)], 
             fill=(203, 213, 225, 255), width=max(1, int(1 * scale)))
    
    # 8. 文件扩展名标签
    if size >= 64:
        ext_x = file_x1 + int(16 * scale)
        ext_y = int(216 * scale)
        ext_w = int(48 * scale)
        ext_h = int(12 * scale)
        
        draw.rounded_rectangle([ext_x, ext_y, ext_x + ext_w, ext_y + ext_h],
                              radius=max(1, int(2 * scale)),
                              fill=COLORS["bg_end"] + (230,))
        
        # 文字
        from PIL import ImageFont
        try:
            font_size = int(9 * scale)
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:  # 系统缺 arial.ttf 时回退 PIL 默认位图字体
            font = ImageFont.load_default()
        
        text = "TPX"
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        text_x = ext_x + (ext_w - text_w) // 2
        text_y = ext_y + (ext_h - text_h) // 2
        
        draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font)
    
    return img


def draw_tray_icon(size):
    """
    绘制系统托盘图标
    设计：蓝色圆形背景 + 白色光环 + 增强温度波形
    
    关键改进：
    1. 白色外光环确保在任何背景下都清晰可见
    2. 增强橙色波形作为主要视觉标识
    """
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 1. 先绘制白色外光环
    center = size // 2
    outer_radius = int(30 * size / 64)  # 外光环半径
    inner_radius = int(27 * size / 64)  # 内部蓝色圆半径
    
    # 白色光环
    draw.ellipse([center - outer_radius, center - outer_radius, 
                  center + outer_radius, center + outer_radius],
                 fill=(255, 255, 255, 255))
    
    # 内部蓝色圆
    bg_circle = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg_circle)
    
    bg_draw.ellipse([center - inner_radius, center - inner_radius, 
                     center + inner_radius, center + inner_radius],
                    fill=COLORS["bg_end"] + (255,))
    
    img = Image.alpha_composite(img, bg_circle)
    draw = ImageDraw.Draw(img)
    
    # 2. 简化温度波形 (增强宽度)
    scale = size / 64
    wave_points = [
        (int(14 * scale), int(38 * scale)),
        (int(18 * scale), int(38 * scale)),
        (int(20 * scale), int(26 * scale)),
        (int(26 * scale), int(26 * scale)),
        (int(32 * scale), int(26 * scale)),
        (int(34 * scale), int(42 * scale)),
        (int(40 * scale), int(42 * scale)),
        (int(46 * scale), int(42 * scale)),
        (int(50 * scale), int(22 * scale)),
        (int(50 * scale), int(22 * scale)),
    ]
    
    # 波形宽度增加 (从 3 增加到 4)
    wave_width = max(2, int(4 * scale))
    
    # 深色描边增加立体感
    dark_alpha = int(255 * 0.3)
    for i in range(len(wave_points) - 1):
        p1 = wave_points[i]
        p2 = wave_points[i + 1]
        draw.line([(p1[0], p1[1] + 1), (p2[0], p2[1] + 1)], 
                 fill=(139, 69, 19, dark_alpha), width=wave_width)
    
    # 主波形
    for i in range(len(wave_points) - 1):
        p1 = wave_points[i]
        p2 = wave_points[i + 1]
        draw.line([p1, p2], fill=COLORS["wave_start"] + (255,), width=wave_width)
    
    # 3. 关键数据点
    dot_configs = [
        (int(26 * scale), int(26 * scale), int(3 * scale)),
        (int(40 * scale), int(42 * scale), int(3 * scale)),
        (int(50 * scale), int(22 * scale), int(3.5 * scale)),
    ]
    
    for x, y, r in dot_configs:
        # 白色边框
        draw.ellipse([x - r - max(1, int(1 * scale)), y - r - max(1, int(1 * scale)),
                     x + r + max(1, int(1 * scale)), y + r + max(1, int(1 * scale))],
                    fill=(255, 255, 255, 255))
        # 彩色填充
        draw.ellipse([x - r, y - r, x + r, y + r], fill=COLORS["accent"] + (255,))
    
    # 4. 底部温度刻度 (仅大尺寸)
    if size >= 32:
        baseline_y = int(50 * scale)
        draw.line([(int(12 * scale), baseline_y), (int(52 * scale), baseline_y)], 
                 fill=(255, 255, 255, 150), width=max(1, int(2 * scale)))
    
    return img


# 图标绘制函数映射
ICON_DRAWERS = {
    "app_icon": draw_app_icon,
    "file_icon": draw_file_icon,
    "tray_icon": draw_tray_icon,
}


def generate_png_icons(sizes=None):
    """生成 PNG 图标"""
    if sizes is None:
        sizes = STANDARD_SIZES
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    generated_files = {}
    
    for icon_name, drawer_fn in ICON_DRAWERS.items():
        print(f"\n生成图标: {icon_name}")
        generated_files[icon_name] = []
        
        for size in sizes:
            img = drawer_fn(size)
            png_path = OUTPUT_DIR / f"{icon_name}_{size}x{size}.png"
            img.save(str(png_path), 'PNG')
            generated_files[icon_name].append(png_path)
            print(f"  ✓ {size}x{size}")
    
    return generated_files


def create_ico_file(png_files, ico_path):
    """从多个 PNG 文件创建 ICO 文件（手动构建多尺寸 ICO）"""
    import struct
    
    # 读取所有 PNG 数据并按尺寸排序
    pngs_with_size = []
    for png_file in png_files:
        img = Image.open(png_file)
        size = img.size[0]
        img.close()
        
        with open(png_file, 'rb') as f:
            png_data = f.read()
        
        pngs_with_size.append((size, png_data))
    
    # 按尺寸排序
    pngs_with_size.sort(key=lambda x: x[0])
    
    num_images = len(pngs_with_size)
    if num_images == 0:
        return False
    
    # ICO 文件格式:
    # Header: 6 bytes (reserved=0, type=1, count)
    # Entries: 16 bytes each
    # Image data
    
    header_size = 6
    entry_size = 16
    data_offset = header_size + (entry_size * num_images)
    
    # 构建 header
    header = struct.pack('<HHH', 0, 1, num_images)
    
    # 构建 entries 和 data
    entries = b''
    images_data = b''
    current_offset = data_offset
    
    for size, png_data in pngs_with_size:
        png_size = len(png_data)
        
        # Entry: width, height, color_count, reserved, planes, bit_count, bytes_in_res, image_offset
        entry = struct.pack('<BBBBHHII',
            size if size < 256 else 0,  # width (0 means 256)
            size if size < 256 else 0,  # height (0 means 256)
            0,  # color count
            0,  # reserved
            1,  # color planes
            32,  # bits per pixel (32bpp RGBA)
            png_size,  # bytes in image
            current_offset  # image offset
        )
        entries += entry
        images_data += png_data
        current_offset += png_size
    
    # 写入 ICO 文件
    with open(ico_path, 'wb') as f:
        f.write(header)
        f.write(entries)
        f.write(images_data)
    
    return True


def main():
    parser = argparse.ArgumentParser(description="生成 多通道温度分析仪 图标系统 (Pillow 版)")
    parser.add_argument(
        "--sizes",
        type=str,
        default="16,24,32,48,64,128,256,512",
        help="要生成的尺寸，用逗号分隔"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="只生成预览用的 512x512 尺寸"
    )
    args = parser.parse_args()
    
    if args.preview:
        sizes = [512]
    else:
        sizes = [int(s.strip()) for s in args.sizes.split(",")]
    
    print("=" * 60)
    print("多通道温度分析仪 - 图标系统生成器 (Pillow 版)")
    print("=" * 60)
    print(f"\n项目根目录: {ROOT}")
    print(f"PNG 输出目录: {OUTPUT_DIR}")
    print(f"ICO 输出目录: {BUILD_DIR}")
    print(f"\n生成尺寸: {sizes}")
    print(f"图标数量: {len(ICON_DRAWERS)}")
    
    # 生成 PNG
    generated = generate_png_icons(sizes)
    
    # 创建 ICO 文件
    print("\n创建 ICO 文件...")
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    
    # 应用图标 ICO
    if "app_icon" in generated:
        ico_path = BUILD_DIR / "app.ico"
        create_ico_file(generated["app_icon"], ico_path)
        print(f"  ✓ app.ico -> {ico_path}")
    
    # 托盘图标 ICO
    if "tray_icon" in generated:
        ico_path = BUILD_DIR / "tray.ico"
        create_ico_file(generated["tray_icon"], ico_path)
        print(f"  ✓ tray.ico -> {ico_path}")
    
    # 文件图标 ICO
    if "file_icon" in generated:
        ico_path = RESOURCES_DIR / "file_icon.ico"
        create_ico_file(generated["file_icon"], ico_path)
        print(f"  ✓ file_icon.ico -> {ico_path}")
    
    print("\n" + "=" * 60)
    print("✅ 图标生成完成！")
    print("=" * 60)
    print("\n生成的文件：")
    print(f"  - PNG 图标: {OUTPUT_DIR}/")
    print(f"  - ICO 文件: {BUILD_DIR}/")
    
    # 列出所有生成的文件
    png_files = sorted(OUTPUT_DIR.glob("*.png"))
    if png_files:
        print("\nPNG 文件列表：")
        for f in png_files:
            print(f"  - {f.name}")
    
    ico_files = sorted(BUILD_DIR.glob("*.ico"))
    if ico_files:
        print("\nICO 文件列表：")
        for f in ico_files:
            print(f"  - {f.name}")


if __name__ == "__main__":
    main()