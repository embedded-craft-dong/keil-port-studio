#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
=============================================================================
 keil_port_tool.py —— Keil MDK 工程一键移植小助手
=============================================================================
 功能:
   1. 添加 C/C++、汇编、目标文件、静态库及头文件到 Keil 工程
      - 可编译/链接文件 -> 加入工程文件树 (按目录自动分组)
      - 头文件 -> 将其所在目录加入 include path
      - (可选) 同时把头文件加入工程文件树, 便于浏览
   2. 一键移植 FreeRTOS + CMSIS-RTOS V2
      - 共享目录只作为源码仓库；先复制到工程 Middlewares/Third_Party，再添加相对引用
      - 自动识别芯片内核, 匹配 Keil 专用的 RVDS 移植层 (ARM_CM0/CM3/CM4F...)
      - 添加内核源码 / 内存管理(heap_4) / port.c / cmsis_os2.c 到工程
      - 自动生成 FreeRTOSConfig.h (或修补已有配置, 补齐 CMSIS-V2 所需宏)
      - 本地没有源码时自动从 GitHub 下载官方发行版 (--freertos auto)
      - 自动添加 include path, 并自动在 CMSIS Pack 中查找 cmsis_os2.h
   3. 一键移植 LVGL (支持 v8 / v9)
      - 先复制为工程独立副本，不让多个工程直接共用可修改的源码
      - 本地没有源码时自动从 GitHub 下载 (AC5 工程自动选 v8, AC6 选最新版)
      - 自动生成并启用 lv_conf.h (可选色深 8/16/32)
      - 添加 src 下全部源文件、include path、宏定义
      - 自动开启 C99/GNU 编译选项 (AC5: uC99+uGnu; AC6: --std=gnu99)
      - 可选复制 lv_port_disp / lv_port_indev 移植模板
   4. 一键移植 FatFS
      - 复制 ST 官方 FatFS 中间件到工程独立目录
      - 根据工程是否使用 FreeRTOS，自动切换裸机/CMSIS-RTOS2 系统适配层
      - 自动生成 ffconf.h、CubeMX 风格 FatFs/App 与 FatFs/Target 框架
      - 默认列出全部可用组件，可按文件取消
   5. Target 级工程设置
      - 宏定义按名称添加、替换或删除，Include Path 规范化去重/清理
      - 设置优化等级和调试信息，可配置或清除 .sct 分散加载文件
      - 修改所选 Target 使用的 startup*.s 中 Stack_Size / Heap_Size
   6. 扩展组件
      - SEGGER RTT 高速日志（printf、Keil Syscalls、汇编加速可独立选择）
      - LittleFS Flash 文件系统（自动适配裸机/FreeRTOS，并生成块设备模板）
      - CMSIS-DSP / ARM Math（按算法模块选择源码并匹配 Cortex-M 内核宏）
      - FreeRTOS 外设锁（UART/SPI/I2C/Flash 可独立选择并自动初始化）
   7. 网络与 USB
      - LwIP TCP/IP 协议栈（裸机/FreeRTOS、IPv4/IPv6、HTTP/MQTT 等按需选择）
      - STM32 ETH、ENC28J60、W5500 MACRAW 与通用网卡适配骨架
      - TinyUSB Device/Host（CDC/MSC/HID/MIDI/Vendor 等设备类按需选择）
      - 自动生成工程私有配置和硬件抽象端口，不直接绑定某个 HAL 句柄

 所有功能可独立执行, 也可任意组合。无任务参数时默认启动图形界面；
 图形界面中所有发现的文件默认勾选，可按目录或单文件取消。

 用法:
   图形界面:    python keil_port_tool.py [工程路径]
   旧交互菜单:  python keil_port_tool.py [工程路径] --cli
   命令行模式:  python keil_port_tool.py <工程> [任务选项] [--yes]

 示例:
   python keil_port_tool.py                                          # 交互菜单
   python keil_port_tool.py MyProj.uvprojx -a --scan User;MyLib
   python keil_port_tool.py MyProj.uvprojx --freertos auto           # 自动下载+移植
   python keil_port_tool.py MyProj.uvprojx --lvgl auto --color-depth 16
   python keil_port_tool.py MyProj.uvprojx --fatfs auto --fatfs-mode auto
   python keil_port_tool.py MyProj.uvprojx --target Debug -D USE_RTT --optimization O2
   python keil_port_tool.py MyProj.uvprojx --scatter memory.sct --stack-size 0x1000
   python keil_port_tool.py MyProj.uvprojx -a --freertos auto --lvgl auto --yes  # 混合
   python keil_port_tool.py MyProj.uvprojx --freertos D:\FreeRTOSv10.5.1\FreeRTOS

 依赖: 仅 Python 3.8+ 标准库, 无任何第三方包。
 说明: 运行前请先关闭 Keil; 脚本写入工程前会自动备份原文件 (*.bak_时间戳)。
=============================================================================
"""

import argparse
import copy
import csv
import difflib
import gc
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import threading
import queue
import webbrowser
import urllib.request
import zipfile
import contextlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError:  # 服务器/精简 Python 仍可使用 CLI
    tk = None

# ---------------------------------------------------------------------------
# 全局默认配置 (可自行修改, 交互模式下会作为默认值)
# ---------------------------------------------------------------------------
CONFIG = {
    'PROJECT': '',            # 默认工程文件或所在目录, 留空则自动查找
    'SCAN_DIRS': '',          # 任务1默认扫描目录, 多个用 ; 分隔, 留空 = 工程根目录递归
    'FREERTOS_DIR': '',       # FreeRTOS 根目录, 留空 = 交互模式下自动下载
    'LVGL_DIR': '',           # LVGL 根目录, 留空 = 交互模式下自动下载
    'FATFS_DIR': '',          # FatFS 根目录, 留空 = 交互模式下自动下载
    'SEGGER_RTT_DIR': '',     # SEGGER RTT 根目录, 留空 = 自动下载
    'LITTLEFS_DIR': '',       # LittleFS 根目录, 留空 = 自动下载
    'CMSIS_DSP_DIR': '',      # CMSIS-DSP 根目录, 留空 = 自动下载
    'LWIP_DIR': '',           # LwIP 根目录, 留空 = 自动下载
    'TINYUSB_DIR': '',        # TinyUSB 根目录, 留空 = 自动下载
    'SDK_DIR': '',            # 源码下载根目录, 留空 = 自动选择 (常用库文件/工程上层目录)
    'FREERTOS_URL': '',       # FreeRTOS 下载地址覆盖 (留空=自动获取 GitHub 最新版, 可填镜像 zip)
    'LVGL_URL': '',           # LVGL 下载地址覆盖 (同上)
    'FATFS_URL': '',          # ST FatFS 中间件下载地址覆盖 (同上)
    'SEGGER_RTT_URL': '',     # SEGGER RTT 下载地址覆盖
    'LITTLEFS_URL': '',       # LittleFS 下载地址覆盖
    'CMSIS_DSP_URL': '',      # CMSIS-DSP 下载地址覆盖
    'LWIP_URL': '',           # LwIP 下载地址覆盖
    'TINYUSB_URL': '',        # TinyUSB 下载地址覆盖
    'CMSIS_OS2_URL': '',      # cmsis_os2.h 下载地址覆盖 (留空=ARM-software/CMSIS_5 官方仓库)
    'CMSIS_OS2_BASE': '',     # CMSIS-V2 适配层(cmsis_os2.c 等3个文件)下载前缀覆盖, 留空=ARM 官方
    'LVGL_COLOR_DEPTH': 16,   # LVGL 色深 8/16/32
    'INCLUDE_H_IN_TREE': False,
    'DOWNLOAD_RETRIES': 3,    # 下载失败后的总尝试次数
    'DOWNLOAD_PROXY': '',     # 例如 http://127.0.0.1:7890
    'MIRROR_PREFIX': '',      # GitHub 加速前缀，例如 https://gh-proxy.example/
    'DOWNLOAD_CHECKSUMS': {}, # 可选: {原始 URL: SHA256}
    'EXTRA_SCAN_SKIP_DIRS': '',
    'UV4_PATH': '',           # UV4.exe 路径；留空时自动搜索
}

# FreeRTOS / LVGL 下载默认版本
# 注意: FreeRTOS 主仓库 2024 年起把内核源码移除 (历史 tag 也被重写), 内核源码
#       现在在独立仓库 FreeRTOS-Kernel; CMSIS-RTOS V2 封装用 ARM 官方
#       CMSIS-FreeRTOS 适配层 (Keil 软件包同源)。
FREERTOS_KERNEL_TAG = 'V10.5.1'
LVGL_V8_TAG = 'v8.4.0'          # AC5 工程用 v8 (v9 不支持 AC5)
LVGL_V9_TAG = 'v9.3.0'          # AC6 工程兜底
FATFS_TAG = 'master'             # 上游无正式 Release；归档 SHA 会写入安装清单
LITTLEFS_TAG = 'v2.11.3'
CMSIS_DSP_TAG = 'v1.17.1'
CMSIS_5_TAG = '5.9.0'            # AC5 使用其内置 CMSIS-DSP 1.10.0
LWIP_TAG = 'STABLE-2_2_1_RELEASE'
TINYUSB_TAG = '0.21.0'
TINYUSB_AC5_TAG = '0.17.0'       # ARM Compiler 5 使用较保守且已验证广泛的版本
TINYUSB_AC5_HOST_TAG = '0.18.0'  # DWC2 Host requires hcd_dwc2.c plus scoped AC5 fixes
TOOL_VERSION = '2.2.0-rc6'
RTTHREAD_TAG = 'v5.2.2'
RTTHREAD_SHA256 = 'c40bd84ee10389988d10cb64dda0ed63d8df719a6a2065cbc1c49bebac4f45b0'
CMSIS_OS2_H_URL = ('https://raw.githubusercontent.com/ARM-software/CMSIS_5/5.9.0/'
                   'CMSIS/RTOS2/Include/cmsis_os2.h')
# 适配层必须与内核主版本一致。ARM 官方当前 main 会随新版内核变化，不能与
# 固定的 V10.5.1 内核混用，所以默认锁定同名 release tag。
CMSIS_OS2_RAW_BASE = ('https://raw.githubusercontent.com/ARM-software/CMSIS-FreeRTOS/'
                      + 'v' + FREERTOS_KERNEL_TAG.lstrip('Vv') + '/CMSIS/RTOS2/FreeRTOS')
# ARM 适配层 cmsis_os2.c 需要的配套头文件: (文件名, 仓库内相对路径)
CMSIS_OS2_BUNDLE = (
    ('cmsis_os2.c', 'Source/cmsis_os2.c'),
    ('freertos_mpool.h', 'Include/freertos_mpool.h'),
    ('freertos_os2.h', 'Include/freertos_os2.h'),
)
GITHUB_HEADERS = {'User-Agent': 'keil-port-tool/1.2'}

# 扫描时跳过的目录名 (避免把 FreeRTOS Demo/LVGL examples 等非移植代码扫进工程)
SCAN_SKIP_DIRS = {'demo', 'demos', 'test', 'tests', 'example', 'examples', 'docs'}
_DEFAULT_SCAN_SKIP_DIRS = frozenset(SCAN_SKIP_DIRS)

# µVision project FileType: C=1, ASM=2, Object=3, Library=4, Text/Header=5, C++=8.
PROJECT_FILE_TYPES = {
    '.c': 1, '.cpp': 8, '.cc': 8, '.cxx': 8,
    '.s': 2, '.asm': 2, '.a51': 2, '.a66': 2,
    '.o': 3, '.obj': 3,
    '.lib': 4, '.a': 4, '.ar': 4,
    '.h': 5, '.hpp': 5, '.hh': 5, '.inc': 5,
}
HEADER_SUFFIXES = {'.h', '.hpp', '.hh', '.inc'}
COMPILED_SUFFIXES = set(PROJECT_FILE_TYPES) - HEADER_SUFFIXES


# Keep imports working for legacy callers loading this entry point by file path.
_SOURCE_DIR = str(Path(__file__).resolve().parent)
if _SOURCE_DIR not in sys.path:
    sys.path.insert(0, _SOURCE_DIR)
from kps_core.errors import ToolError
from kps_core.runtime import _OPERATION_LOCAL, operation_context
from kps_core.source_patches import (PatchResult, _c_code, _c_function,
    patch_cmsis_wrapper_systick, patch_project_systick, patch_project_rtos_exceptions,
    patch_main_start_scheduler, patch_rtthread_irq)
from kps_core.ownership import _edit_hunks, _reverse_owned_hunks


USER_SETTINGS_DEFAULTS = {
    'language': 'zh-CN',
    'download_retries': 3,
    'proxy': '',
    'mirror_prefix': '',
    'extra_scan_skip_dirs': '',
    'uv4_path': '',
}


def user_settings_path():
    """全局设置保存在用户配置目录，不污染源码仓库和目标工程。"""
    base = os.environ.get('APPDATA')
    if base:
        return Path(base) / 'KeilPortStudio' / 'settings.json'
    return Path.home() / '.keil-port-studio.json'


def load_user_settings(path=None):
    settings = dict(USER_SETTINGS_DEFAULTS)
    target = Path(path) if path else user_settings_path()
    if not target.is_file():
        return settings
    try:
        data = json.loads(target.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('根节点必须是 JSON 对象')
        for key in settings:
            if key in data:
                settings[key] = data[key]
    except Exception as e:
        warn('无法读取用户设置 %s，已使用默认值: %s' % (target, e))
    return settings


def save_user_settings(settings, path=None):
    target = Path(path) if path else user_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = dict(USER_SETTINGS_DEFAULTS)
    clean.update({key: settings.get(key, clean[key]) for key in clean})
    target.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return target


def apply_user_settings(settings):
    try:
        retries = max(1, min(10, int(settings.get('download_retries', 3))))
    except (TypeError, ValueError):
        retries = 3
    CONFIG['DOWNLOAD_RETRIES'] = retries
    CONFIG['DOWNLOAD_PROXY'] = str(settings.get('proxy') or '').strip()
    CONFIG['MIRROR_PREFIX'] = str(settings.get('mirror_prefix') or '').strip()
    CONFIG['EXTRA_SCAN_SKIP_DIRS'] = str(settings.get('extra_scan_skip_dirs') or '').strip()
    CONFIG['UV4_PATH'] = str(settings.get('uv4_path') or '').strip()
    extras = re.split(r'[;,\s]+', CONFIG['EXTRA_SCAN_SKIP_DIRS'])
    SCAN_SKIP_DIRS.clear()
    SCAN_SKIP_DIRS.update(_DEFAULT_SCAN_SKIP_DIRS)
    SCAN_SKIP_DIRS.update(x.lower() for x in extras if x)


# ===========================================================================
# 基础工具函数
# ===========================================================================
_LOG_SINK = None
_PROGRESS_SINK = None
_LOG_LEVEL = 'normal'


def detect_text_format(path):
    """返回 (文本, 编码, BOM, 换行)。旧 Keil 工程常见 GBK，写回时必须保持。"""
    path = Path(path)
    raw = path.read_bytes()
    bom = b''
    if raw.startswith(b'\xef\xbb\xbf'):
        encoding, bom, payload = 'utf-8', b'\xef\xbb\xbf', raw[3:]
    elif raw.startswith(b'\xff\xfe'):
        encoding, bom, payload = 'utf-16-le', b'\xff\xfe', raw[2:]
    elif raw.startswith(b'\xfe\xff'):
        encoding, bom, payload = 'utf-16-be', b'\xfe\xff', raw[2:]
    else:
        payload = raw
        for encoding in ('utf-8', 'gb18030'):
            try:
                payload.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ToolError('无法识别文件编码，为避免损坏已停止修改: %s' % path)
    try:
        text = payload.decode(encoding)
    except UnicodeDecodeError as e:
        raise ToolError('文件编码解析失败，为避免损坏已停止修改: %s (%s)' % (path, e))
    newline = '\r\n' if b'\r\n' in raw else '\n'
    return text, encoding, bom, newline


def read_source_text(path):
    return detect_text_format(path)[0]


def encode_preserving_format(path, content):
    """已有文件保持编码/BOM/换行；新文件使用 UTF-8 + LF。"""
    path = Path(path)
    if path.is_file():
        _old, encoding, bom, newline = detect_text_format(path)
    else:
        encoding, bom, newline = 'utf-8', b'', '\n'
    normalized = str(content).replace('\r\n', '\n').replace('\r', '\n')
    if not normalized.endswith('\n'):
        normalized += '\n'
    if newline != '\n':
        normalized = normalized.replace('\n', newline)
    try:
        return bom + normalized.encode(encoding)
    except UnicodeEncodeError as e:
        raise ToolError('新内容无法用原编码 %s 写回 %s: %s' % (encoding, path, e))


def sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), 'rb') as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(path):
    h = hashlib.sha256()
    root = Path(path)
    for item in sorted(p for p in root.rglob('*') if p.is_file()):
        h.update(str(item.relative_to(root)).replace('\\', '/').encode('utf-8'))
        h.update(bytes.fromhex(sha256_file(item)))
    return h.hexdigest()




def _operation_level():
    context = getattr(_OPERATION_LOCAL, 'value', None)
    return context.level if context else _LOG_LEVEL


def _console_write(text):
    """Best-effort Unicode rendering without changing the caller's encoding."""
    stream = sys.stdout
    if stream is None:  # Windowed EXE without a console.
        return
    try:
        stream.write(text)
    except UnicodeEncodeError:
        # Imported API callers may have a Western Windows redirected console.
        # Logging must not abort a file transaction; keep the GUI sink lossless.
        encoding = getattr(stream, 'encoding', None) or 'utf-8'
        stream.write(text.encode(encoding, errors='backslashreplace').decode(encoding))


def log(msg=''):
    """统一日志出口：CLI 打印到终端，GUI 同时写入日志框。"""
    if (_operation_level() == 'quiet' and
            not str(msg).startswith(('[警告]', '[错误]', '[异常]'))):
        return
    _console_write(str(msg) + '\n')
    context = getattr(_OPERATION_LOCAL, 'value', None)
    sink = context.log_sink if context else _LOG_SINK
    if sink is not None:
        try:
            sink(str(msg))
        except Exception:
            pass


def info(msg):
    if _operation_level() != 'quiet':
        log('[信息] ' + msg)


def warn(msg):
    log('[警告] ' + msg)


def verbose(msg):
    if _operation_level() == 'verbose':
        log('[详细] ' + msg)


def set_progress(value, message=''):
    """报告 0..100 的进度；CLI 与 GUI 共用，未绑定 GUI 时保持安静。"""
    value = max(0, min(100, int(value)))
    context = getattr(_OPERATION_LOCAL, 'value', None)
    sink = context.progress_sink if context else _PROGRESS_SINK
    if sink is not None:
        try:
            sink(value, str(message or ''))
        except Exception:
            pass


def ask(prompt, default=None):
    if default:
        prompt += ' (默认: %s)' % default
    s = input(prompt + ': ').strip().strip('"')
    return s or default


def ask_yn(prompt, default=True):
    hint = ' [Y/n] ' if default else ' [y/N] '
    s = input(prompt + hint).strip().lower()
    if not s:
        return default
    return s in ('y', 'yes', '是', '1')


def rel_or_abs(p, base):
    """把路径转成 Keil 风格 (反斜杠); 相对工程目录优先, 跨盘则用绝对路径。"""
    p, base = str(p), str(base)
    try:
        r = os.path.relpath(p, base)
    except ValueError:
        r = p
    return r.replace('/', '\\')


def split_keil_defines(value):
    """拆分 Keil Define 字段，同时保留引号或括号内的逗号。"""
    tokens, current = [], []
    quote = None
    depth = 0
    escaped = False
    for char in str(value or ''):
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == '\\':
            current.append(char)
            escaped = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            current.append(char)
            quote = char
        elif char == '(':
            current.append(char)
            depth += 1
        elif char == ')':
            current.append(char)
            depth = max(0, depth - 1)
        elif char == ',' and depth == 0:
            token = ''.join(current).strip()
            if token:
                tokens.append(token)
            current = []
        else:
            current.append(char)
    token = ''.join(current).strip()
    if token:
        tokens.append(token)
    return tokens


def order_keil_defines(tokens):
    """把带引号的宏放到最后，避免 ARMCC5 吞掉其后的宏。"""
    plain, quoted = [], []
    for token in tokens:
        (quoted if ('"' in token or "'" in token) else plain).append(token)
    return plain + quoted


def find_in_tree(root, name, exclude=()):
    """在目录树中查找指定文件名, 优先非 Demo/Test/Example 目录下的。"""
    root = Path(root)
    ex = [Path(e).resolve() for e in exclude if e]
    hits = []
    for p in root.rglob(name):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if any(part.startswith('.') for part in rel.parts[:-1]):
            continue
        rp = p.resolve()
        if any(rp == e or e in rp.parents for e in ex):
            continue
        hits.append(p)
    if not hits:
        return None
    hits.sort(key=lambda p: (any(x.lower() in SCAN_SKIP_DIRS for x in p.parts), str(p)))
    return hits[0]


# ===========================================================================
# 源码自动下载 (FreeRTOS / LVGL)
# ===========================================================================
def _download_url(url):
    prefix = str(CONFIG.get('MIRROR_PREFIX') or '').strip().rstrip('/')
    if prefix and re.match(r'^https?://(?:raw\.)?github\.com/', url, re.I):
        return prefix + '/' + url
    if prefix and re.match(r'^https?://codeload\.github\.com/', url, re.I):
        return prefix + '/' + url
    return url


def _url_opener():
    proxy = str(CONFIG.get('DOWNLOAD_PROXY') or '').strip()
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({
        'http': proxy, 'https': proxy,
    }))


def _fetch_json(url):
    final_url = _download_url(url)
    retries = max(1, int(CONFIG.get('DOWNLOAD_RETRIES') or 1))
    last_error = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(final_url, headers=GITHUB_HEADERS)
        try:
            with _url_opener().open(req, timeout=30) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            last_error = e
            if attempt < retries:
                verbose('元数据请求失败，第 %d/%d 次重试: %s' % (attempt, retries, e))
                time.sleep(min(attempt, 3))
    raise ToolError('读取在线版本信息失败: %s\n可在“设置”配置镜像/代理，或选择本地源码。\n'
                    'Configure a mirror/proxy in Settings, or select local sources.' % last_error)


def download_file(url, dest, desc='', expected_sha256=None):
    """下载文件，支持代理/镜像/重试/进度和可选 SHA-256 校验。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    final_url = _download_url(url)
    retries = max(1, int(CONFIG.get('DOWNLOAD_RETRIES') or 1))
    expected_sha256 = (expected_sha256 or
                       (CONFIG.get('DOWNLOAD_CHECKSUMS') or {}).get(url) or '').lower().strip()
    info('开始下载 %s ...' % desc)
    info('  地址: %s' % final_url)
    part = dest.with_name(dest.name + '.part')
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
        req = urllib.request.Request(final_url, headers=GITHUB_HEADERS)
        try:
            if retries > 1:
                verbose('下载尝试 %d/%d' % (attempt, retries))
            with _url_opener().open(req, timeout=180) as r, open(str(part), 'wb') as f:
                total = int(r.headers.get('Content-Length') or 0)
                done = 0
                last = -1
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        if pct != last:
                            last = pct
                            set_progress(pct, '正在下载 %s' % desc)
                            if _LOG_LEVEL != 'quiet' and _LOG_SINK is None:
                                _console_write('\r  进度: %d%% (%d/%d KB)    ' %
                                               (pct, done // 1024, total // 1024))
                                if sys.stdout is not None:
                                    sys.stdout.flush()
                if total and _LOG_LEVEL != 'quiet' and _LOG_SINK is None:
                    _console_write('\n')
            if expected_sha256:
                actual = sha256_file(part).lower()
                if actual != expected_sha256:
                    raise ToolError('SHA-256 校验失败，期望 %s，实际 %s' %
                                    (expected_sha256, actual))
            os.replace(str(part), str(dest))
            last_error = None
            break
        except Exception as e:
            last_error = e
            try:
                part.unlink()
            except OSError:
                pass
            if attempt < retries:
                warn('下载失败，第 %d/%d 次重试: %s' % (attempt, retries, e))
                time.sleep(min(attempt, 3))
    if last_error is not None:
        raise ToolError('下载失败，已尝试 %d 次: %s\n可在“设置”配置镜像/代理，或选择本地源码。\n'
                        'Configure a mirror/proxy in Settings, or select local sources.' % (retries, last_error))
    set_progress(100, '%s 下载完成' % desc)
    info('下载完成: %s (%.1f MB)' % (dest, dest.stat().st_size / 1048576.0))


def download_archive(url, dest, desc='', expected_sha256=None):
    """下载并缓存 ZIP；URL 与哈希都匹配时复用，避免浮动分支被重复下载。"""
    dest = Path(dest)
    meta_path = dest.with_name(dest.name + '.source.json')
    configured = CONFIG.get('DOWNLOAD_CHECKSUMS') or {}
    named_checksum = next((value for key, value in configured.items()
                           if str(key).lower() in str(desc).lower()), '')
    expected = (expected_sha256 or configured.get(url) or named_checksum or '').lower()
    meta = _load_json(meta_path, {}) if meta_path.is_file() else {}
    if dest.is_file() and meta.get('url') == url:
        actual = sha256_file(dest).lower()
        if (not expected or actual == expected) and actual == str(meta.get('sha256', '')).lower():
            try:
                with zipfile.ZipFile(str(dest)) as archive:
                    if archive.testzip() is None:
                        info('复用已校验的下载缓存: %s' % dest)
                        return {'url': url, 'sha256': actual, 'archive': str(dest), 'cached': True}
            except (OSError, zipfile.BadZipFile):
                pass
        warn('下载缓存校验失败，将重新下载: %s' % dest)
    download_file(url, dest, desc, expected_sha256=expected or None)
    try:
        with zipfile.ZipFile(str(dest)) as archive:
            broken = archive.testzip()
    except zipfile.BadZipFile as e:
        raise ToolError('下载结果不是有效 ZIP: %s' % e)
    if broken:
        raise ToolError('ZIP 完整性检查失败，首个损坏文件: %s' % broken)
    actual = sha256_file(dest)
    meta = {'url': url, 'resolved_url': _download_url(url), 'sha256': actual,
            'downloaded_at': datetime.now().isoformat(timespec='seconds')}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return dict(meta, archive=str(dest), cached=False)


def extract_zip(zip_path, dest_root, keep_prefixes=(), keep_files=()):
    """安全解压 zip；拒绝路径穿越、绝对路径、驱动器路径和符号链接。"""
    dest_root = Path(dest_root).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path)) as z:
        safe_infos = []
        for info in z.infolist():
            normalized = info.filename.replace('\\', '/')
            parts = [part for part in normalized.split('/') if part not in ('', '.')]
            if (normalized.startswith('/') or re.match(r'^[A-Za-z]:', normalized) or
                    '..' in parts):
                raise ToolError('ZIP 包含不安全路径，已拒绝解压: %s' % info.filename)
            target = (dest_root / Path(*parts)).resolve() if parts else dest_root
            if target != dest_root and dest_root not in target.parents:
                raise ToolError('ZIP 路径越过目标目录，已拒绝解压: %s' % info.filename)
            safe_infos.append((info, normalized, parts))
        roots = [parts[0] for _info, _name, parts in safe_infos if parts]
        if not roots:
            raise ToolError('ZIP 压缩包为空: %s' % zip_path)
        top = roots[0]
        members = []
        for info, n, _parts in safe_infos:
            if not n.startswith(top + '/'):
                continue
            rel = n[len(top) + 1:]
            if not rel:
                continue
            if keep_prefixes or keep_files:
                if not (rel.startswith(keep_prefixes) or rel in keep_files):
                    continue
            members.append(info)
        if (keep_prefixes or keep_files) and not members:
            raise ToolError('ZIP 中没有找到所需文件，可能是下载地址或版本不匹配: %s' % zip_path)
        selected = members if (keep_prefixes or keep_files) else [item[0] for item in safe_infos]
        # Official SDK archives may contain documentation symlinks outside the
        # explicit source allowlist. Never extract a symlink, but an excluded
        # documentation entry need not block an otherwise safe source import.
        for info in selected:
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if (unix_mode & 0o170000) == 0o120000:
                raise ToolError('ZIP 包含不安全路径，已拒绝解压: %s' % info.filename)
        for info in selected:
            z.extract(info, str(dest_root))
    return dest_root / top


def default_sdk_dir(proj):
    """源码下载根目录: 配置 > 常用库文件 > 工程上层目录。"""
    if CONFIG.get('SDK_DIR'):
        p = Path(CONFIG['SDK_DIR']).expanduser()
        if p.is_dir():
            return p
    root = proj.dir.parent
    for cand in (root.parent / '常用库文件', root / '常用库文件', root.parent):
        if cand.is_dir():
            return cand
    return root.parent


def project_content_root(proj):
    """返回工程源码根目录；CubeMX 工程通常是 MDK-ARM 的上一级。"""
    parent = proj.dir.parent
    if (parent / 'Core').is_dir() or any(parent.glob('*.ioc')):
        return parent
    return proj.dir


def plan_project_library_copy(proj, source_root, folder_name, locator, rep):
    """规划把共享库复制到工程内，并把已有工程引用映射到本地副本。"""
    source_root = Path(source_root).resolve()
    if not any(item.get('path') == str(source_root) for item in rep.sources):
        rep.sources.append({'kind': 'local', 'path': str(source_root),
                            'tree_sha256': None})
    local_root = (project_content_root(proj) / 'Middlewares' / 'Third_Party' /
                  folder_name).resolve()
    if source_root == local_root:
        return source_root, local_root

    existing = locator(local_root) if local_root.is_dir() else None
    if local_root.exists() and not local_root.is_dir():
        raise ToolError('组件目标路径已被同名文件占用，无法复制: %s' % local_root)
    if local_root.is_dir() and existing is None:
        raise ToolError('工程内组件目录已存在但结构不完整，为避免生成悬空引用已停止: %s。'
                        '请在“安全与恢复”中卸载旧组件，或手动修复/移走该目录后重试。' % local_root)
    if existing:
        content_root = Path(existing).resolve()
        rep.notes.append('工程内源码已存在，保留本工程的修改且不从共享库覆盖: %s' % content_root)
    else:
        content_root = source_root
        rep.copy_trees.append((source_root, local_root, '%s 工程独立副本' % folder_name))

    remapped_files = proj.remap_file_prefix(source_root, local_root)
    remapped_inc = proj.remap_include_prefix(source_root, local_root)
    if remapped_files or remapped_inc:
        rep.notes.append('已把原共享库引用迁移为工程内相对路径（文件 %d，Include %d）' %
                         (remapped_files, remapped_inc))
    rep.notes.append('工程实际使用的 %s 源码: %s' % (folder_name, local_root))
    return content_root, local_root


def find_existing_sdk(sdk_dir, locator):
    for d in sorted(Path(sdk_dir).glob('*')):
        if d.is_dir() and locator(d):
            return d
    return None


def freertos_kernel_version(base):
    """从 task.h 返回 (major, minor, patch)，无法识别时返回 None。"""
    task_h = Path(base) / 'include' / 'task.h'
    try:
        text = read_source_text(task_h)
    except OSError:
        return None
    nums = []
    for key in ('MAJOR', 'MINOR', 'BUILD'):
        m = re.search(r'#define\s+tskKERNEL_VERSION_%s\s+(\d+)' % key, text)
        if not m:
            return None
        nums.append(int(m.group(1)))
    return tuple(nums)


def _ensure_cmsis_os2_wrapper(base, opts):
    """下载与内核版本一致的 ARM CMSIS-FreeRTOS 封装层。"""
    os2_dir = base / 'CMSIS_RTOS_V2'
    bundle_paths = [os2_dir / name for name, _rel in CMSIS_OS2_BUNDLE]
    refresh = not all(p.is_file() for p in bundle_paths)
    version = freertos_kernel_version(base)
    if not refresh and version and version[0] <= 10:
        try:
            wrapper = read_source_text(os2_dir / 'cmsis_os2.c')
        except OSError:
            wrapper = ''
        # 这些标识来自 11.x 适配层；放到 10.5.1 会出现当前测试工程中的
        # osThreadPrivileged/uxQueueGetQueueLength 编译错误。
        if 'osThreadPrivileged' in wrapper or 'uxQueueGetQueueLength' in wrapper:
            refresh = True
            warn('检测到 CMSIS-RTOS2 适配层与 FreeRTOS %d.%d.%d 不匹配，将刷新为同版本' % version)
    if not refresh:
        return
    if getattr(opts, 'no_download', False) or getattr(opts, 'dry_run', False):
        return
    os2_dir.mkdir(parents=True, exist_ok=True)
    if version:
        matched_base = ('https://raw.githubusercontent.com/ARM-software/CMSIS-FreeRTOS/'
                        'v%d.%d.%d/CMSIS/RTOS2/FreeRTOS' % version)
    else:
        matched_base = CMSIS_OS2_RAW_BASE
    base_url = (os.environ.get('KEIL_TOOL_CMSIS_OS2_BASE')
                or CONFIG.get('CMSIS_OS2_BASE') or matched_base).rstrip('/')
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    for name, rel in CMSIS_OS2_BUNDLE:
        dst = os2_dir / name
        if dst.is_file() and refresh:
            bak = dst.with_name(dst.name + '.bak_' + ts)
            shutil.copy2(str(dst), str(bak))
            info('旧适配文件已备份: %s' % bak)
        elif dst.is_file():
            continue
        download_file('%s/%s' % (base_url, rel), dst, '%s (CMSIS-RTOS V2 适配层)' % name)


def ensure_freertos_sdk(proj, opts, rep):
    """查找或下载 FreeRTOS 内核 + CMSIS-V2 封装, 返回内核根目录。"""
    sdk = Path(getattr(opts, 'sdk_dir', None) or default_sdk_dir(proj))
    found = find_existing_sdk(sdk, locate_freertos_source)
    if found:
        info('使用已有 FreeRTOS 源码: %s' % found)
        return found
    if getattr(opts, 'no_download', False):
        return None
    if getattr(opts, 'dry_run', False):
        rep.notes.append('DRY-RUN: 将自动下载 FreeRTOS 内核到 %s' % sdk)
        return None
    if getattr(opts, 'interactive', False):
        if not ask_yn('未找到 FreeRTOS 源码, 是否自动下载到 %s ?' % sdk, True):
            return None
    override = os.environ.get('KEIL_TOOL_FREERTOS_URL') or CONFIG.get('FREERTOS_URL')
    if override:
        url, tag = override, 'custom'
    else:
        tag = FREERTOS_KERNEL_TAG
        url = 'https://codeload.github.com/FreeRTOS/FreeRTOS-Kernel/zip/refs/tags/' + tag
    zip_path = sdk / ('FreeRTOS-Kernel-%s.zip' % tag)
    rep.sources.append(download_archive(url, zip_path, 'FreeRTOS 内核 (%s)' % tag))
    top = extract_zip(zip_path, sdk)  # 内核包很小, 全部解压
    base = locate_freertos_source(top)
    if base is None:
        # 某些镜像的根目录名带提交哈希, 兜底扫描 sdk 目录
        for d in sorted(sdk.iterdir()):
            if d.is_dir():
                b = locate_freertos_source(d)
                if b:
                    top, base = d, b
                    break
    if base is None:
        raise ToolError('下载解压后仍未找到 FreeRTOS 源码结构, '
                        '请手动下载 FreeRTOS 源码 zip 并用 --freertos 指定目录')
    _ensure_cmsis_os2_wrapper(base, opts)
    info('FreeRTOS 已就绪: %s' % top)
    return top


def ensure_lvgl_sdk(proj, opts, rep):
    """查找或下载 LVGL, 返回 LVGL 根目录。AC5 工程自动用 v8 (v9 不支持 AC5)。"""
    sdk = Path(getattr(opts, 'sdk_dir', None) or default_sdk_dir(proj))
    found = find_existing_sdk(sdk, locate_lvgl_root)
    if found:
        info('使用已有 LVGL 源码: %s' % found)
        return found
    if getattr(opts, 'no_download', False):
        return None
    if getattr(opts, 'dry_run', False):
        rep.notes.append('DRY-RUN: 将自动下载 LVGL 源码到 %s' % sdk)
        return None
    if getattr(opts, 'interactive', False):
        if not ask_yn('未找到 LVGL 源码, 是否自动下载到 %s ?' % sdk, True):
            return None
    ac6 = proj.any_ac6()
    override = os.environ.get('KEIL_TOOL_LVGL_URL') or CONFIG.get('LVGL_URL')
    if override:
        url, tag = override, 'custom'
    elif ac6:
        # “latest” 会让同一份工程在不同日期得到不同源码。默认固定版本；
        # 需要升级时显式修改版本或通过 KEIL_TOOL_LVGL_URL 覆盖。
        tag = LVGL_V9_TAG
        url = 'https://codeload.github.com/lvgl/lvgl/zip/refs/tags/' + tag
    else:
        tag = LVGL_V8_TAG
        url = 'https://codeload.github.com/lvgl/lvgl/zip/refs/tags/' + tag
        info('当前工程为 AC5 编译器, 自动选择 LVGL v8 (%s)' % tag)
    zip_path = sdk / ('lvgl-%s.zip' % tag)
    rep.sources.append(download_archive(url, zip_path, 'LVGL (%s)' % tag))
    top = extract_zip(zip_path, sdk,
                      keep_prefixes=('src/', 'examples/porting/'),
                      keep_files=('lvgl.h', 'lv_conf_template.h', 'LICENSE.txt', 'README.md'))
    root = locate_lvgl_root(top)
    if root is None:
        raise ToolError('下载解压后仍未找到 LVGL 源码结构, '
                        '请手动下载 lvgl 源码 zip 并用 --lvgl 指定目录')
    info('LVGL 已就绪: %s' % top)
    return top


def ensure_fatfs_sdk(proj, opts, rep):
    """查找或下载 ST 官方 FatFS 中间件，返回包含 source 的顶层目录。"""
    sdk = Path(getattr(opts, 'sdk_dir', None) or default_sdk_dir(proj))
    found = find_existing_sdk(sdk, locate_fatfs_root)
    if found:
        info('使用已有 FatFS 源码: %s' % found)
        return found
    if getattr(opts, 'no_download', False):
        return None
    if getattr(opts, 'dry_run', False):
        rep.notes.append('DRY-RUN: 将自动下载 ST FatFS 中间件到 %s' % sdk)
        return None
    if getattr(opts, 'interactive', False):
        if not ask_yn('未找到 FatFS 源码, 是否自动下载到 %s ?' % sdk, True):
            return None
    override = os.environ.get('KEIL_TOOL_FATFS_URL') or CONFIG.get('FATFS_URL')
    url = override or ('https://codeload.github.com/STMicroelectronics/'
                       'stm32-mw-fatfs/zip/refs/heads/' + FATFS_TAG)
    tag = 'custom' if override else FATFS_TAG
    zip_path = sdk / ('stm32-mw-fatfs-%s.zip' % tag)
    rep.sources.append(download_archive(url, zip_path, 'ST FatFS 中间件 (%s)' % tag))
    top = extract_zip(zip_path, sdk,
                      keep_prefixes=('source/',),
                      keep_files=('LICENSE.md', 'README.md', 'Release_Notes.html',
                                  'st_license.txt'))
    if locate_fatfs_root(top) is None:
        for d in sorted(sdk.iterdir()):
            if d.is_dir() and locate_fatfs_root(d):
                top = d
                break
    if locate_fatfs_root(top) is None:
        raise ToolError('下载解压后仍未找到 FatFS 源码结构, '
                        '请手动下载 ST stm32-mw-fatfs 并用 --fatfs 指定目录')
    info('FatFS 已就绪: %s' % top)
    return top


def ensure_segger_rtt_sdk(proj, opts, rep):
    """查找或下载 SEGGER 官方 RTT Target Sources。"""
    sdk = Path(getattr(opts, 'sdk_dir', None) or default_sdk_dir(proj))
    found = find_existing_sdk(sdk, locate_segger_rtt_root)
    if found:
        info('使用已有 SEGGER RTT 源码: %s' % found)
        return found
    if getattr(opts, 'no_download', False):
        return None
    if getattr(opts, 'dry_run', False):
        rep.notes.append('DRY-RUN: 将自动下载 SEGGER RTT 到 %s' % sdk)
        return None
    override = os.environ.get('KEIL_TOOL_SEGGER_RTT_URL') or CONFIG.get('SEGGER_RTT_URL')
    url = override or 'https://codeload.github.com/SEGGERMicro/RTT/zip/refs/heads/main'
    zip_path = sdk / 'SEGGER-RTT-main.zip'
    rep.sources.append(download_archive(url, zip_path, 'SEGGER RTT Target Sources'))
    top = extract_zip(zip_path, sdk,
                      keep_prefixes=('RTT/', 'Config/', 'Syscalls/'),
                      keep_files=('LICENSE.md', 'README.md', 'SEGGER.RTT.pdsc'))
    if locate_segger_rtt_root(top) is None:
        raise ToolError('下载解压后未找到 RTT/SEGGER_RTT.c')
    info('SEGGER RTT 已就绪: %s' % top)
    return top


def ensure_littlefs_sdk(proj, opts, rep):
    """查找或下载 littlefs 官方稳定版。"""
    sdk = Path(getattr(opts, 'sdk_dir', None) or default_sdk_dir(proj))
    found = find_existing_sdk(sdk, locate_littlefs_root)
    if found:
        info('使用已有 LittleFS 源码: %s' % found)
        return found
    if getattr(opts, 'no_download', False):
        return None
    if getattr(opts, 'dry_run', False):
        rep.notes.append('DRY-RUN: 将自动下载 LittleFS 到 %s' % sdk)
        return None
    override = os.environ.get('KEIL_TOOL_LITTLEFS_URL') or CONFIG.get('LITTLEFS_URL')
    tag = LITTLEFS_TAG
    url = override or ('https://codeload.github.com/littlefs-project/littlefs/zip/refs/tags/' + tag)
    zip_path = sdk / ('littlefs-%s.zip' % ('custom' if override else tag))
    rep.sources.append(download_archive(url, zip_path,
                                        'LittleFS (%s)' % ('custom' if override else tag)))
    top = extract_zip(zip_path, sdk,
                      keep_files=('lfs.c', 'lfs.h', 'lfs_util.c', 'lfs_util.h',
                                  'LICENSE.md', 'README.md', 'SPEC.md'))
    if locate_littlefs_root(top) is None:
        raise ToolError('下载解压后未找到 lfs.c/lfs.h')
    info('LittleFS 已就绪: %s' % top)
    return top


def ensure_cmsis_dsp_sdk(proj, opts, rep):
    """AC5 使用 CMSIS 5.9 内置 DSP 1.10；AC6 使用独立 CMSIS-DSP 稳定版。"""
    sdk = Path(getattr(opts, 'sdk_dir', None) or default_sdk_dir(proj))
    found = find_existing_sdk(sdk, locate_cmsis_dsp_root)
    if found and not proj.any_ac6():
        found_root = locate_cmsis_dsp_root(found)
        # 自动模式下，AC5 只复用 CMSIS_5/CMSIS/DSP 结构；独立仓库的新版本
        # 主要面向 AC6。用户显式指定目录时仍尊重用户选择。
        if not (found_root and found_root.name == 'DSP' and
                found_root.parent.name.upper() == 'CMSIS'):
            found = None
    if found:
        info('使用已有 CMSIS-DSP 源码: %s' % found)
        return found
    if getattr(opts, 'no_download', False):
        return None
    if getattr(opts, 'dry_run', False):
        rep.notes.append('DRY-RUN: 将自动下载 CMSIS-DSP 到 %s' % sdk)
        return None
    override = os.environ.get('KEIL_TOOL_CMSIS_DSP_URL') or CONFIG.get('CMSIS_DSP_URL')
    if override:
        url, label = override, 'custom'
        prefixes = ()
    elif proj.any_ac6():
        label = CMSIS_DSP_TAG
        url = ('https://codeload.github.com/ARM-software/CMSIS-DSP/zip/refs/tags/' + label)
        prefixes = ('Source/', 'Include/', 'PrivateInclude/')
    else:
        label = 'CMSIS-%s-DSP-1.10.0' % CMSIS_5_TAG
        url = ('https://codeload.github.com/ARM-software/CMSIS_5/zip/refs/tags/' +
               CMSIS_5_TAG)
        prefixes = ('CMSIS/DSP/',)
        info('当前工程包含 AC5 Target，自动选择 CMSIS 5.9.0 内置 DSP 1.10.0')
    zip_path = sdk / ('CMSIS-DSP-%s.zip' % label)
    rep.sources.append(download_archive(url, zip_path, 'CMSIS-DSP (%s)' % label))
    if override:
        top = extract_zip(zip_path, sdk)
    else:
        top = extract_zip(zip_path, sdk, keep_prefixes=prefixes,
                          keep_files=('LICENSE', 'LICENSE.txt', 'README.md'))
    if locate_cmsis_dsp_root(top) is None:
        raise ToolError('下载解压后未找到 CMSIS-DSP Source/Include 结构')
    info('CMSIS-DSP 已就绪: %s' % top)
    return top


def ensure_lwip_sdk(proj, opts, rep):
    """查找或下载上游 LwIP 稳定版。"""
    sdk = Path(getattr(opts, 'sdk_dir', None) or default_sdk_dir(proj))
    found = find_existing_sdk(sdk, locate_lwip_root)
    if found:
        info('使用已有 LwIP 源码: %s' % found)
        return found
    if getattr(opts, 'no_download', False):
        return None
    if getattr(opts, 'dry_run', False):
        rep.notes.append('DRY-RUN: 将自动下载 LwIP 到 %s' % sdk)
        return None
    override = os.environ.get('KEIL_TOOL_LWIP_URL') or CONFIG.get('LWIP_URL')
    tag = 'custom' if override else LWIP_TAG
    url = override or ('https://codeload.github.com/lwip-tcpip/lwip/zip/refs/tags/' + LWIP_TAG)
    archive = sdk / ('lwip-%s.zip' % tag)
    rep.sources.append(download_archive(url, archive, 'LwIP (%s)' % tag))
    top = extract_zip(archive, sdk, keep_prefixes=('src/',),
                      keep_files=('COPYING', 'README', 'README.md', 'CHANGELOG'))
    if locate_lwip_root(top) is None:
        raise ToolError('下载解压后未找到 LwIP src/include/lwip/init.h')
    info('LwIP 已就绪: %s' % top)
    return top


def ensure_tinyusb_sdk(proj, opts, rep):
    """查找或下载 TinyUSB 官方稳定版。"""
    sdk = Path(getattr(opts, 'sdk_dir', None) or default_sdk_dir(proj))
    mode = getattr(opts, 'tinyusb_mode', 'device')
    host = mode in ('host', 'both')
    compatible_tag = (TINYUSB_TAG if proj.any_ac6() else
                      TINYUSB_AC5_HOST_TAG if host else TINYUSB_AC5_TAG)
    found = None
    for candidate in sorted(sdk.glob('*')) if sdk.is_dir() else ():
        root = locate_tinyusb_root(candidate) if candidate.is_dir() else None
        if not root:
            continue
        version = tinyusb_version(root)
        if host and version != tuple(int(n) for n in compatible_tag.split('.')):
            continue
        if not host and not proj.any_ac6() and version and version[:2] > (0, 17):
            continue
        if host:
            try:
                validate_tinyusb_controller_sources(tinyusb_portable_sources(root, proj, mode), mode)
            except ToolError:
                continue
        found = candidate
        break
    if found:
        info('使用已有 TinyUSB 源码: %s' % found)
        return found
    if getattr(opts, 'no_download', False):
        return None
    if getattr(opts, 'dry_run', False):
        rep.notes.append('DRY-RUN: 将自动下载 TinyUSB 到 %s' % sdk)
        return None
    override = os.environ.get('KEIL_TOOL_TINYUSB_URL') or CONFIG.get('TINYUSB_URL')
    tag = 'custom' if override else compatible_tag
    url = override or ('https://codeload.github.com/hathach/tinyusb/zip/refs/tags/' + compatible_tag)
    archive = sdk / ('tinyusb-%s.zip' % tag)
    rep.sources.append(download_archive(url, archive, 'TinyUSB (%s)' % tag))
    top = extract_zip(archive, sdk, keep_prefixes=('src/',),
                      keep_files=('LICENSE', 'README.md'))
    if locate_tinyusb_root(top) is None:
        raise ToolError('下载解压后未找到 TinyUSB src/tusb.c')
    info('TinyUSB 已就绪: %s' % top)
    return top


# ===========================================================================
# 芯片内核 <-> FreeRTOS RVDS 移植层 映射
# ===========================================================================
CORE_DIRS = {
    'Cortex-M0': ['ARM_CM0'],
    'Cortex-M0+': ['ARM_CM0'],
    'SC000': ['ARM_CM0'],
    'SC300': ['ARM_CM3'],
    'Cortex-M1': ['ARM_CM3'],
    'Cortex-M3': ['ARM_CM3'],
    'Cortex-M4': ['ARM_CM4F', 'ARM_CM4'],
    'Cortex-M7': ['ARM_CM7/r0p1', 'ARM_CM7'],
    'Cortex-M23': ['ARM_CM23_NTZ', 'ARM_CM23'],
    'Cortex-M33': ['ARM_CM33_NTZ', 'ARM_CM33'],
    'Cortex-M35P': ['ARM_CM33_NTZ'],
    'Cortex-M55': ['ARM_CM55', 'ARM_CM33_NTZ'],
    'Cortex-M85': ['ARM_CM85', 'ARM_CM55'],
}

# 通过芯片型号前缀推断内核 (当 .uvprojx 里没有 CPUTYPE 信息时兜底)
DEVICE_CORES = [
    ('STM32C0', 'Cortex-M0+'), ('STM32F0', 'Cortex-M0'), ('STM32G0', 'Cortex-M0+'),
    ('STM32F1', 'Cortex-M3'), ('STM32L1', 'Cortex-M3'),
    ('STM32F2', 'Cortex-M4'), ('STM32F3', 'Cortex-M4'), ('STM32F4', 'Cortex-M4'),
    ('STM32G4', 'Cortex-M4'), ('STM32L4', 'Cortex-M4'), ('STM32WBA', 'Cortex-M33'),
    ('STM32WB', 'Cortex-M4'), ('STM32WL', 'Cortex-M4'),
    ('STM32F7', 'Cortex-M7'), ('STM32H7', 'Cortex-M7'),
    ('STM32H5', 'Cortex-M33'), ('STM32L5', 'Cortex-M33'), ('STM32U5', 'Cortex-M33'),
    ('NRF51', 'Cortex-M0'), ('NRF52', 'Cortex-M4'),
    ('NRF53', 'Cortex-M33'), ('NRF91', 'Cortex-M33'),
    ('LPC11', 'Cortex-M0'), ('LPC13', 'Cortex-M3'), ('LPC15', 'Cortex-M3'),
    ('LPC17', 'Cortex-M3'), ('LPC18', 'Cortex-M3'), ('LPC40', 'Cortex-M4'),
    ('LPC43', 'Cortex-M4'), ('LPC51', 'Cortex-M0+'), ('LPC54', 'Cortex-M4'),
    ('LPC55', 'Cortex-M33'),
    ('MK02', 'Cortex-M4'), ('MK10', 'Cortex-M4'), ('MK20', 'Cortex-M4'),
    ('MK22', 'Cortex-M4'), ('MK60', 'Cortex-M4'), ('MK64', 'Cortex-M4'),
    ('MK66', 'Cortex-M4'),
    ('GD32E5', 'Cortex-M33'), ('GD32F1', 'Cortex-M3'), ('GD32F2', 'Cortex-M3'),
    ('GD32F3', 'Cortex-M4'), ('GD32F4', 'Cortex-M4'), ('GD32E1', 'Cortex-M4'),
    ('GD32E2', 'Cortex-M4'), ('GD32C1', 'Cortex-M4'),
    ('MM32F0', 'Cortex-M0'), ('MM32F1', 'Cortex-M3'), ('MM32F3', 'Cortex-M3'),
    ('APM32F1', 'Cortex-M3'), ('APM32F4', 'Cortex-M4'), ('AT32F4', 'Cortex-M4'),
    ('HC32F4', 'Cortex-M4'), ('HC32L1', 'Cortex-M0+'),
]


def core_from_device(device):
    dev = (device or '').upper()
    for pre, core in DEVICE_CORES:
        if dev.startswith(pre):
            return core
    return None


# ===========================================================================
# 变更报告
# ===========================================================================
class Report:
    TITLES = {
        'add_files': '添加工程文件与静态库',
        'freertos': '移植 FreeRTOS (CMSIS-RTOS V2)',
        'rtthread': '移植 RT-Thread (单核内核)',
        'lvgl': '移植 LVGL',
        'fatfs': '移植 FatFS',
        'segger_rtt': '移植 SEGGER RTT',
        'littlefs': '移植 LittleFS',
        'cmsis_dsp': '移植 CMSIS-DSP / ARM Math',
        'rtos_guard': '生成 FreeRTOS 外设线程安全层',
        'lwip': '移植 LwIP 网络协议栈',
        'tinyusb': '移植 TinyUSB 协议栈',
        'project_settings': '工程元素与编译设置',
    }

    def __init__(self, key):
        self.key = key
        self.files = []       # (组名, 文件名)
        self.inc = []         # 新增 include path
        self.defines = []     # 新增宏定义
        self.copy_trees = []  # (源目录, 目标目录, 说明) 待复制的源码树
        self.gen_files = []   # (路径, 内容, 说明) 待写入的文件
        self.obsolete_files = []  # (路径, 说明) 写入时移为时间戳备份
        self.sources = []     # 下载 URL、归档哈希与缓存来源
        self.source_edits = []  # Exact, reversible edits owned by this component.
        self.notes = []
        self.warnings = []

    def has_changes(self):
        return bool(self.files or self.inc or self.defines or self.copy_trees or
                    self.gen_files or self.obsolete_files)

    def print(self):
        line = '-' * 58
        log('')
        log(line)
        log('【%s】' % self.TITLES.get(self.key, self.key))
        if self.files:
            log('  新增工程文件 (%d 个):' % len(self.files))
            for g, n in self.files:
                log('    - %-24s -> 组 %s' % (n, g))
        if self.inc:
            log('  新增 include path (%d 个):' % len(self.inc))
            for p in self.inc:
                log('    - %s' % p)
        if self.defines:
            log('  新增宏定义: %s' % ', '.join(self.defines))
        if self.copy_trees:
            log('  将复制源码库到工程 (%d 个):' % len(self.copy_trees))
            for src, dst, d in self.copy_trees:
                log('    - %s -> %s  (%s)' % (src, dst, d))
        if self.gen_files:
            log('  将生成/更新文件 (%d 个):' % len(self.gen_files))
            for p, _c, d in self.gen_files:
                log('    - %s  (%s)' % (p, d))
        if self.obsolete_files:
            log('  将停用旧文件 (%d 个，保留备份):' % len(self.obsolete_files))
            for p, d in self.obsolete_files:
                log('    - %s  (%s)' % (p, d))
        if self.sources:
            log('  源码来源 (%d 项):' % len(self.sources))
            for item in self.sources:
                label = item.get('url') or item.get('path') or '未知来源'
                digest = item.get('sha256') or item.get('tree_sha256')
                log('    - %s%s' % (label, ('  SHA256=' + digest) if digest else ''))
        for n in self.notes:
            log('  [提示] ' + n)
        for w in self.warnings:
            log('  [警告] ' + w)
        log(line)


# ===========================================================================
# Keil 工程封装 (.uvprojx / .uvproj)
# ===========================================================================
class KeilProject:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.dir = self.path.parent
        self.original_bytes = self.path.read_bytes()
        self.original_text, self.xml_encoding, self.xml_bom, self.newline = \
            detect_text_format(self.path)
        decl = re.match(r'\s*(<\?xml[^?]*\?>)', self.original_text)
        self.xml_declaration = decl.group(1) if decl else None
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        self.tree = ET.parse(str(self.path), parser=parser)
        self.root = self.tree.getroot()
        root_tag = self.root.tag if isinstance(self.root.tag, str) else ''
        ns_match = re.match(r'^\{([^}]+)\}', root_tag)
        self.namespace = ns_match.group(1) if ns_match else None
        if self.namespace:
            for element in self.root.iter():
                if isinstance(element.tag, str) and element.tag.startswith('{'):
                    element.tag = element.tag.split('}', 1)[1]
        self.legacy = self.path.suffix.lower() == '.uvproj'
        self.all_targets = self.root.findall('Targets/Target')
        if not self.all_targets:
            self.all_targets = self.root.findall('Target')
        self.targets = list(self.all_targets)
        if not self.targets:
            raise ToolError('工程中没有找到 Target；如果这是 MDK 6 CMSIS Solution，'
                            '请传入对应的 .uvprojx，YAML 工程将在后续阶段支持')
        self.dirty = False
        self._initial_root = copy.deepcopy(self.root)

    # ---------- 基本信息 ----------
    def device(self):
        for t in self.targets:
            d = t.findtext('TargetOption/TargetCommonOption/Device', '')
            if d:
                return d
        return ''

    def target_ram_size(self, target):
        """读取 Keil Target 中启用的 IRAM 区域总容量；无法识别时返回 0。"""
        regions = []
        memories = target.find('TargetOption/TargetArmAds/ArmAdsMisc/OnChipMemories')
        if memories is None:
            return 0
        for node in list(memories):
            name = str(node.tag).upper()
            if not name.startswith(('IRAM', 'XRAM')):
                continue
            try:
                start = int((node.findtext('StartAddress') or '0').strip(), 0)
                size = int((node.findtext('Size') or '0').strip(), 0)
            except ValueError:
                continue
            if size > 0:
                regions.append((start, size))
        # 同一段 RAM 可能同时出现在兼容字段中，按地址和大小去重。
        return sum(size for _start, size in set(regions))

    def ram_size_bytes(self):
        """返回所选 Target 中最小的非零 RAM 容量，便于给出保守配置。"""
        sizes = [self.target_ram_size(t) for t in self.targets]
        sizes = [value for value in sizes if value > 0]
        return min(sizes) if sizes else 0

    def target_names(self, all_targets=False):
        source = self.all_targets if all_targets else self.targets
        return [t.findtext('TargetName', '') for t in source]

    def select_targets(self, names=None):
        """限制本次操作影响的 Target。names 为空或含 '*' 时选择全部。"""
        if not names or '*' in names:
            self.targets = list(self.all_targets)
            return
        wanted = {str(name).strip() for name in names if str(name).strip()}
        selected = [t for t in self.all_targets if t.findtext('TargetName', '') in wanted]
        missing = sorted(wanted - {t.findtext('TargetName', '') for t in selected})
        if missing:
            raise ToolError('工程中不存在 Target: %s；可选项: %s' %
                            (', '.join(missing), ', '.join(self.target_names(True))))
        if not selected:
            raise ToolError('至少选择一个 Target')
        self.targets = selected

    def core_info(self):
        """返回 (内核名, 是否有 FPU)。优先从 CPUTYPE 解析, 其次按芯片型号猜。"""
        for t in self.targets:
            core, fpu = self.target_core_info(t)
            if core:
                return core, fpu
        return None, False

    def target_core_info(self, target):
        cpu = target.findtext('TargetOption/TargetCommonOption/Cpu', '') or ''
        dev = target.findtext('TargetOption/TargetCommonOption/Device', '') or ''
        match = re.search(r'CPUTYPE\("([^"]+)"\)', cpu)
        if match:
            return match.group(1), ('FPU' in cpu)
        core = core_from_device(dev)
        return core, bool(core in ('Cortex-M4', 'Cortex-M7'))

    def is_ac6(self, target=None):
        if target is None:
            target = self.targets[0] if self.targets else None
        if target is None:
            return False
        pcc = (target.findtext('pCCUsed', '') or '').upper()
        uac6 = target.findtext('uAC6', '') or ''
        return ('V6' in pcc) or ('ARMCLANG' in pcc) or (uac6 == '1')

    def any_ac6(self):
        return any(self.is_ac6(t) for t in self.targets)

    # ---------- 底层查找 ----------
    def _cads_list(self):
        """返回所有 Target 下的"工程级" Cads 元素 (uv4/uv5 结构通吃)。
        排除每个文件自己的 FileOption/FileArmAds/Cads, 避免污染单文件编译选项。"""
        out = []
        for t in self.targets:
            out.extend(self._target_cads(t))
        return out

    def _target_cads(self, target):
        # Only documented Target-level locations, never group/file overrides.
        paths = ('TargetOption/TargetArmAds/Cads', 'TargetArmAds/Cads')
        return [node for path in paths for node in target.findall(path)]

    def norm_file(self, fp):
        """工程文件路径归一化 (用于去重比较)。"""
        fp = str(fp).replace('\\', '/').replace('//', '/')
        while fp.startswith('./'):
            fp = fp[2:]
        if not os.path.isabs(fp):
            fp = os.path.join(str(self.dir), fp)
        return os.path.normcase(os.path.normpath(fp))

    # ---------- 读 ----------
    def files_in_project(self):
        fps = []
        for f in self.root.iter('File'):
            fp = f.findtext('FilePath')
            if fp:
                fps.append(fp)
        return fps

    def file_records(self):
        """返回当前所选 Target 的工程文件记录。"""
        records = []
        for target in self.targets:
            target_name = target.findtext('TargetName', '')
            groups = target.find('Groups')
            if groups is None:
                continue
            for group in groups.findall('Group'):
                group_name = group.findtext('GroupName', '')
                for file_el in group.findall('Files/File'):
                    value = file_el.findtext('FilePath', '') or ''
                    records.append({'target': target_name, 'group': group_name,
                                    'name': file_el.findtext('FileName', '') or '',
                                    'path': value})
        return records

    def include_dirs_abs(self):
        dirs = set()
        for c in self._cads_list():
            inc = c.findtext('VariousControls/IncludePath', '') or ''
            for e in inc.split(';'):
                e = e.strip()
                if not e or '$' in e:
                    continue
                if not os.path.isabs(e):
                    e = os.path.join(str(self.dir), e)
                dirs.add(os.path.normcase(os.path.normpath(e)))
        return dirs

    # ---------- 写 (只改内存中的树, save() 才落盘) ----------
    def add_include_path(self, entry, report):
        entry = str(entry).replace('/', '\\')
        if os.path.isabs(entry):
            key = os.path.normcase(os.path.normpath(entry))
        else:
            key = os.path.normcase(os.path.normpath(os.path.join(str(self.dir), entry)))
        added = False
        for c in self._cads_list():
            vc = c.find('VariousControls')
            if vc is None:
                vc = ET.SubElement(c, 'VariousControls')
            inc = vc.find('IncludePath')
            if inc is None:
                inc = ET.SubElement(vc, 'IncludePath')
                inc.text = ''
            entries = [e.strip() for e in (inc.text or '').split(';') if e.strip()]
            dup = False
            for e in entries:
                e2 = e
                if not os.path.isabs(e2):
                    e2 = os.path.join(str(self.dir), e2)
                if os.path.normcase(os.path.normpath(e2)) == key:
                    dup = True
                    break
            if not dup:
                entries.append(entry)
                inc.text = ';'.join(entries)
                added = True
                self.dirty = True
        if added:
            report.inc.append(entry)

    def add_define(self, macro, report):
        added = False
        for c in self._cads_list():
            vc = c.find('VariousControls')
            if vc is None:
                vc = ET.SubElement(c, 'VariousControls')
            d = vc.find('Define')
            if d is None:
                d = ET.SubElement(vc, 'Define')
                d.text = ''
            tokens = split_keil_defines(d.text)
            was_present = macro in tokens
            if not was_present:
                tokens.append(macro)
            new_text = ','.join(order_keil_defines(tokens))
            if new_text != (d.text or ''):
                d.text = new_text
                self.dirty = True
            if not was_present:
                added = True
        if added:
            report.defines.append(macro)

    def enable_c99_gnu(self, report):
        """LVGL 需要 C99 + GNU 扩展: AC5 -> uC99/uGnu 勾选; AC6 -> MiscControls 追加 --std=gnu99。"""
        changed = False
        for t in self.targets:
            ac6 = self.is_ac6(t)
            for c in t.findall('.//Cads'):
                if ac6:
                    vc = c.find('VariousControls')
                    if vc is None:
                        vc = ET.SubElement(c, 'VariousControls')
                    misc = vc.find('MiscControls')
                    if misc is None:
                        misc = ET.SubElement(vc, 'MiscControls')
                        misc.text = ''
                    text = (misc.text or '').strip()
                    if '--std' not in text and '-std' not in text:
                        misc.text = (text + ' --std=gnu99').strip()
                        changed = True
                else:
                    for tag in ('uC99', 'uGnu'):
                        el = c.find(tag)
                        if el is None:
                            el = ET.SubElement(c, tag)
                        if (el.text or '') != '1':
                            el.text = '1'
                            changed = True
        if changed:
            self.dirty = True
            report.notes.append('已设置 C99/GNU 编译选项 (AC5: uC99+uGnu; AC6: 追加 --std=gnu99)')

    def add_file(self, group_name, filename, ftype, filepath):
        """向每个 Target 的工程树添加文件，已存在则跳过。返回是否有新增。"""
        containers = self.targets or [self.root]
        key = self.norm_file(filepath)
        added = False
        for container in containers:
            # 文件可能已在该 Target 的其他组中，避免重复编译。
            duplicate = False
            for f in container.iter('File'):
                if self.norm_file(f.findtext('FilePath', '') or '') == key:
                    duplicate = True
                    break
            if duplicate:
                continue
            groups_el = container.find('Groups')
            if groups_el is None:
                groups_el = ET.SubElement(container, 'Groups')
            grp = None
            for g in groups_el.findall('Group'):
                if g.findtext('GroupName') == group_name:
                    grp = g
                    break
            if grp is None:
                grp = ET.SubElement(groups_el, 'Group')
                ET.SubElement(grp, 'GroupName').text = group_name
                files_el = ET.SubElement(grp, 'Files')
            else:
                files_el = grp.find('Files')
                if files_el is None:
                    files_el = ET.SubElement(grp, 'Files')
            fe = ET.SubElement(files_el, 'File')
            ET.SubElement(fe, 'FileName').text = filename
            ET.SubElement(fe, 'FileType').text = str(ftype)
            ET.SubElement(fe, 'FilePath').text = filepath
            added = True
        if added:
            self.dirty = True
        return added

    def remove_file(self, filepath):
        """从每个 Target 删除指定文件项。只改工程 XML，不删除磁盘文件。"""
        key = self.norm_file(filepath)
        removed = False
        for container in self.targets or [self.root]:
            for files_el in list(container.iter('Files')):
                for file_el in list(files_el.findall('File')):
                    value = file_el.findtext('FilePath', '') or ''
                    if self.norm_file(value) == key:
                        files_el.remove(file_el)
                        removed = True
        if removed:
            self.dirty = True
        return removed

    def remap_file_prefix(self, old_root, new_root):
        """把工程中 old_root 下的文件引用整体改为 new_root 下的对应路径。"""
        old_root = Path(old_root).resolve()
        new_root = Path(new_root).resolve()
        changed = 0
        for file_el in self.root.iter('File'):
            path_el = file_el.find('FilePath')
            if path_el is None or not (path_el.text or '').strip():
                continue
            raw_path = Path(path_el.text.replace('\\', os.sep))
            absolute = raw_path if raw_path.is_absolute() else self.dir / raw_path
            absolute = absolute.resolve()
            try:
                rel = absolute.relative_to(old_root)
            except ValueError:
                continue
            path_el.text = rel_or_abs(new_root / rel, self.dir)
            changed += 1
        if changed:
            self.dirty = True
        return changed

    def remap_include_prefix(self, old_root, new_root):
        """把 Include Path 中 old_root 下的目录整体映射到工程内 new_root。"""
        old_root = Path(old_root).resolve()
        new_root = Path(new_root).resolve()
        changed = 0
        for cads in self._cads_list():
            inc = cads.find('VariousControls/IncludePath')
            if inc is None:
                continue
            entries = [e.strip() for e in (inc.text or '').split(';') if e.strip()]
            mapped = []
            for entry in entries:
                absolute = Path(entry) if os.path.isabs(entry) else self.dir / entry
                try:
                    rel = absolute.resolve().relative_to(old_root)
                except ValueError:
                    mapped.append(entry)
                    continue
                mapped.append(rel_or_abs(new_root / rel, self.dir))
                changed += 1
            inc.text = ';'.join(dict.fromkeys(mapped))
        if changed:
            self.dirty = True
        return changed

    def remove_include_path(self, target):
        """删除与 target 完全相同的 Include Path 项。"""
        # manifest 记录的是 Keil 工程中的原始字符串，通常是相对
        # .uvprojx 所在目录的路径。如果直接 Path.resolve()，它会被错误地
        # 相对于工具当前工作目录解析，导致卸载时永远匹配不到。
        target = Path(str(target).replace('\\', os.sep))
        if not target.is_absolute():
            target = self.dir / target
        target = target.resolve()
        changed = 0
        for cads in self._cads_list():
            inc = cads.find('VariousControls/IncludePath')
            if inc is None:
                continue
            entries = [e.strip() for e in (inc.text or '').split(';') if e.strip()]
            kept = []
            for entry in entries:
                absolute = Path(entry) if os.path.isabs(entry) else self.dir / entry
                if absolute.resolve() == target:
                    changed += 1
                else:
                    kept.append(entry)
            inc.text = ';'.join(kept)
        if changed:
            self.dirty = True
        return changed

    def remove_define(self, macro):
        """从当前 Target 的工程级宏列表中删除精确匹配项。"""
        changed = 0
        for cads in self._cads_list():
            define = cads.find('VariousControls/Define')
            if define is None:
                continue
            tokens = split_keil_defines(define.text)
            kept = [x for x in tokens if x != macro]
            if len(kept) != len(tokens):
                define.text = ','.join(kept)
                changed += len(tokens) - len(kept)
        if changed:
            self.dirty = True
        return changed

    def set_define(self, macro, report=None):
        """按宏名添加或替换定义，例如 BUF_SIZE=256 会替换旧 BUF_SIZE。"""
        macro = str(macro).strip()
        if not macro:
            return 0
        name = macro.split('=', 1)[0].strip()
        if not re.match(r'^[A-Za-z_]\w*$', name):
            raise ToolError('宏名称无效: %s' % name)
        changed = 0
        for cads in self._cads_list():
            vc = cads.find('VariousControls')
            if vc is None:
                vc = ET.SubElement(cads, 'VariousControls')
            define = vc.find('Define')
            if define is None:
                define = ET.SubElement(vc, 'Define')
            tokens = split_keil_defines(define.text)
            kept = [x for x in tokens if x.split('=', 1)[0].strip() != name]
            if macro not in kept:
                kept.append(macro)
            new_text = ','.join(order_keil_defines(kept))
            if new_text != (define.text or ''):
                define.text = new_text
                changed += 1
        if changed:
            self.dirty = True
            if report is not None:
                report.defines.append(macro)
                report.notes.append('宏已添加/替换: %s' % macro)
        return changed

    def remove_define_name(self, name, report=None):
        name = str(name).split('=', 1)[0].strip()
        changed = 0
        for cads in self._cads_list():
            define = cads.find('VariousControls/Define')
            if define is None:
                continue
            tokens = split_keil_defines(define.text)
            kept = [x for x in tokens if x.split('=', 1)[0].strip() != name]
            if len(kept) != len(tokens):
                define.text = ','.join(kept)
                changed += 1
        if changed:
            self.dirty = True
            if report is not None:
                report.notes.append('宏已删除: %s' % name)
        return changed

    def clean_include_paths(self, remove_missing=False, report=None):
        """按绝对规范路径去重；可选移除确实不存在的普通路径。"""
        removed_duplicates = 0
        removed_missing = 0
        for cads in self._cads_list():
            include = cads.find('VariousControls/IncludePath')
            if include is None:
                continue
            entries = [x.strip() for x in (include.text or '').split(';') if x.strip()]
            kept, seen = [], set()
            for entry in entries:
                if '$' in entry or '%' in entry:
                    key = entry.lower()
                    exists = True
                else:
                    absolute = Path(entry) if os.path.isabs(entry) else self.dir / entry
                    key = os.path.normcase(os.path.normpath(str(absolute.resolve())))
                    exists = absolute.exists()
                if key in seen:
                    removed_duplicates += 1
                    continue
                seen.add(key)
                if remove_missing and not exists:
                    removed_missing += 1
                    continue
                kept.append(entry)
            new_text = ';'.join(kept)
            if new_text != (include.text or ''):
                include.text = new_text
                self.dirty = True
        if report is not None:
            report.notes.append('Include 清理: 去重 %d 项%s' %
                                (removed_duplicates,
                                 '，移除不存在路径 %d 项' % removed_missing
                                 if remove_missing else ''))
        return removed_duplicates, removed_missing

    def set_optimization(self, level, report=None):
        """设置当前 Target 的 C/C++ 优化等级。AC5 使用 Optim，AC6 使用 clang 参数。"""
        level = str(level).upper().strip()
        if level not in ('O0', 'O1', 'O2', 'O3', 'OS', 'OZ'):
            raise ToolError('优化等级无效: %s' % level)
        ac5_values = {'O0': '1', 'O1': '2', 'O2': '3', 'O3': '4',
                      'OS': '3', 'OZ': '3'}
        changed = 0
        ac5_size_approximation = False
        for target in self.targets:
            for cads in self._target_cads(target):
                if self.is_ac6(target):
                    vc = cads.find('VariousControls')
                    if vc is None:
                        vc = ET.SubElement(cads, 'VariousControls')
                    misc = vc.find('MiscControls')
                    if misc is None:
                        misc = ET.SubElement(vc, 'MiscControls')
                    old = misc.text or ''
                    cleaned = re.sub(r'(?<!\S)-O(?:[0-3]|s|z)(?!\S)', '', old,
                                     flags=re.I).strip()
                    new = (cleaned + ' -' + level[0] + level[1:].lower()).strip()
                    if new != old.strip():
                        misc.text = new
                        changed += 1
                else:
                    if level in ('OS', 'OZ'):
                        ac5_size_approximation = True
                    optim = cads.find('Optim')
                    if optim is None:
                        optim = ET.SubElement(cads, 'Optim')
                    value = ac5_values[level]
                    if (optim.text or '') != value:
                        optim.text = value
                        changed += 1
                    otime = cads.find('oTime')
                    if otime is not None and level in ('OS', 'OZ') and (otime.text or '') != '0':
                        otime.text = '0'
                        changed += 1
        if changed:
            self.dirty = True
            if report is not None:
                report.notes.append('优化等级已设置为 %s（仅当前所选 Target）' % level)
                if ac5_size_approximation:
                    report.notes.append('Arm Compiler 5 不提供独立 Os/Oz；已映射为 O2 + 空间优先')
        return changed

    def set_debug_information(self, enabled, report=None):
        value = '1' if enabled else '0'
        changed = 0
        for target in self.targets:
            common = target.find('TargetOption/TargetCommonOption')
            if common is None:
                continue
            element = common.find('DebugInformation')
            if element is None:
                element = ET.SubElement(common, 'DebugInformation')
            if (element.text or '') != value:
                element.text = value
                changed += 1
        if changed:
            self.dirty = True
            if report is not None:
                report.notes.append('调试信息: %s' % ('开启' if enabled else '关闭'))
        return changed

    def set_scatter_file(self, path, report=None):
        path = Path(path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != '.sct':
            raise ToolError('请选择有效的 Keil .sct 分散加载文件: %s' % path)
        value = rel_or_abs(path, self.dir)
        changed = 0
        for target in self.targets:
            arm = target.find('TargetOption/TargetArmAds')
            if arm is None:
                raise ToolError('Target %s 没有 ARM Linker 配置' %
                                target.findtext('TargetName', ''))
            ldads = arm.find('LDads')
            if ldads is None:
                ldads = ET.SubElement(arm, 'LDads')
            for name, text_value in (('umfTarg', '0'), ('useFile', '1'),
                                     ('ScatterFile', value)):
                element = ldads.find(name)
                if element is None:
                    element = ET.SubElement(ldads, name)
                if (element.text or '') != text_value:
                    element.text = text_value
                    changed += 1
        if changed:
            self.dirty = True
            if report is not None:
                report.notes.append('分散加载文件已设置: %s' % value)
        return changed

    def clear_scatter_file(self, report=None):
        changed = 0
        for target in self.targets:
            ldads = target.find('TargetOption/TargetArmAds/LDads')
            if ldads is None:
                continue
            for name, value in (('umfTarg', '1'), ('useFile', '0'), ('ScatterFile', '')):
                element = ldads.find(name)
                if element is not None and (element.text or '') != value:
                    element.text = value
                    changed += 1
        if changed:
            self.dirty = True
            if report is not None:
                report.notes.append('已恢复使用 Target 内存布局，不再使用自定义 .sct')
        return changed

    def serialize(self):
        """按原编码、BOM、换行和命名空间序列化；保留已解析的 XML 注释。"""
        root = copy.deepcopy(self.root)
        if self.namespace:
            ET.register_namespace('', self.namespace)
            for element in root.iter():
                if isinstance(element.tag, str) and not element.tag.startswith('{'):
                    element.tag = '{%s}%s' % (self.namespace, element.tag)
        encoding = self.xml_encoding
        body = ET.tostring(root, encoding=encoding, xml_declaration=False,
                           short_empty_elements=True)
        text = body.decode(encoding)
        declaration = self.xml_declaration
        if declaration:
            declaration = re.sub(r'encoding\s*=\s*["\'][^"\']+["\']',
                                 'encoding="%s"' % encoding.upper(), declaration,
                                 flags=re.I)
        else:
            declaration = '<?xml version="1.0" encoding="%s"?>' % encoding.upper()
        text = declaration + self.newline + text.lstrip('\r\n')
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if self.newline != '\n':
            text = text.replace('\n', self.newline)
        if not text.endswith(self.newline):
            text += self.newline
        payload = self.xml_bom + text.encode(encoding)
        self.validate_serialized(payload)
        return payload

    def validate_serialized(self, payload):
        """Validate planned XML and untouched Targets before any filesystem write."""
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        parsed = ET.fromstring(payload.decode(self.xml_encoding).lstrip('\ufeff'), parser=parser)
        for node in parsed.iter():
            if isinstance(node.tag, str) and node.tag.startswith('{'):
                node.tag = node.tag.split('}', 1)[1]
        def shape(node):
            return (node.tag if isinstance(node.tag, str) else '#comment',
                    tuple(sorted(node.attrib.items())), (node.text or '').strip(),
                    tuple(shape(child) for child in node))
        if shape(parsed) != shape(self.root):
            raise ToolError('XML 写入自检失败：序列化结果与规划结构不一致')
        initial = self._initial_root.findall('Targets/Target') or self._initial_root.findall('Target')
        current = parsed.findall('Targets/Target') or parsed.findall('Target')
        if self.root.tag != self._initial_root.tag or [t.findtext('TargetName') for t in initial] != [t.findtext('TargetName') for t in current]:
            raise ToolError('XML 写入自检失败：根节点或 Target 身份发生非预期改变')
        chosen = set(self.target_names())
        for before, after in zip(initial, current):
            if before.findtext('TargetName', '') not in chosen and shape(before) != shape(after):
                raise ToolError('XML 写入自检失败：修改了未选择的 Target')

    def save(self):
        """备份后按原格式写回工程文件。"""
        payload = self.serialize()
        if self.path.read_bytes() != self.original_bytes:
            raise ToolError('工程文件在规划后被外部修改，已停止写入；请重新载入工程')
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        bak = self.path.with_name(self.path.name + '.bak_' + ts)
        shutil.copy2(str(self.path), str(bak))
        self.path.write_bytes(payload)
        self.original_bytes = payload
        self.original_text = payload[len(self.xml_bom):].decode(self.xml_encoding)
        self._initial_root = copy.deepcopy(self.root)
        self.dirty = False
        return bak


# ===========================================================================
# 任务1: 添加 C/C++、汇编、目标文件、静态库及头文件
# ===========================================================================
def _is_path_inside(path, root):
    """返回 path 是否等于 root 或位于 root 内。"""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _project_boundary_from_file(project_file):
    """估算 Keil 工程的源码边界；CubeMX 通常为 MDK-ARM 的上一级。"""
    project_dir = Path(project_file).resolve().parent
    parent = project_dir.parent
    if (parent / 'Core').is_dir() or any(parent.glob('*.ioc')):
        return parent.resolve()
    return project_dir.resolve()


def scope_scan_dirs(proj, scan_dirs):
    """扫描目录是工作区祖先时，自动收窄到当前工程边界。"""
    current_root = project_content_root(proj).resolve()
    scoped, narrowed = [], []
    for value in scan_dirs:
        root = Path(value).resolve()
        if root != current_root and _is_path_inside(current_root, root):
            scoped.append(current_root)
            narrowed.append(root)
        else:
            scoped.append(root)
    unique = []
    seen = set()
    for value in scoped:
        key = os.path.normcase(str(value))
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique, narrowed


def scan_exclusion_roots(proj, scan_dirs):
    """
    返回（其他 Keil 工程根目录，当前 Target 生成目录）。

    用户可以选择工作区上层目录，但不能因此把兄弟工程的源码、
    启动文件和 .o/.obj 构建产物一起加入当前工程。
    """
    current_root = project_content_root(proj).resolve()
    foreign_roots = set()
    for scan_dir in scan_dirs:
        # 优先访问 MDK 目录；一旦识别出兄弟工程，就剪枝它的整棵源码树。
        # 这比对巨大 workspace 分别 rglob *.uvprojx/*.uvproj 快得多。
        try:
            walker = os.walk(str(scan_dir), topdown=True)
            for root_text, dirnames, filenames in walker:
                root = Path(root_text)
                # 当前工程的边界已知，无需为发现“其他工程”再遍历其整棵源码树。
                if _is_path_inside(root, current_root):
                    dirnames[:] = []
                    continue
                if any(_is_path_inside(root, value) for value in foreign_roots):
                    dirnames[:] = []
                    continue
                dirnames[:] = [name for name in dirnames
                               if not name.startswith('.') and name.lower() not in SCAN_SKIP_DIRS]
                dirnames.sort(key=lambda name: (0 if name.lower() in
                                                ('mdk-arm', 'mdk', 'keil') else 1,
                                                name.lower()))
                for filename in filenames:
                    if Path(filename).suffix.lower() not in ('.uvprojx', '.uvproj'):
                        continue
                    project_file = root / filename
                    try:
                        if project_file.resolve() == proj.path.resolve():
                            continue
                        boundary = _project_boundary_from_file(project_file)
                        if boundary != current_root:
                            foreign_roots.add(boundary)
                            dirnames[:] = []
                    except OSError:
                        continue
        except OSError:
            continue

    output_roots = set()
    for target in proj.all_targets:
        for tag in ('OutputDirectory', 'ListingPath'):
            for node in target.findall('.//' + tag):
                value = (node.text or '').strip().strip('"')
                if not value:
                    continue
                path = Path(value.replace('\\', os.sep))
                if not path.is_absolute():
                    path = proj.dir / path
                try:
                    output_roots.add(path.resolve())
                except OSError:
                    pass
    managed_roots = set()
    third_party = current_root / 'Middlewares' / 'Third_Party'
    if third_party.is_dir():
        managed_roots.add(third_party.resolve())
    return (sorted(foreign_roots, key=lambda p: len(str(p)), reverse=True),
            sorted(output_roots, key=lambda p: len(str(p)), reverse=True),
            sorted(managed_roots, key=lambda p: len(str(p)), reverse=True))


def scan_skip_reason(path, foreign_roots, output_roots, managed_roots=()):
    """返回扫描文件应跳过的原因，None 表示可处理。"""
    if any(_is_path_inside(path, root) for root in output_roots):
        return 'build_output'
    if any(_is_path_inside(path, root) for root in foreign_roots):
        return 'foreign_project'
    if any(_is_path_inside(path, root) for root in managed_roots):
        return 'managed_component'
    return None


def iter_scan_files(scan_dir, foreign_roots, output_roots, managed_roots=()):
    """单次遍历可处理文件，对外部工程、输出目录和受管组件目录直接剪枝。"""
    excluded = tuple(foreign_roots) + tuple(output_roots) + tuple(managed_roots)
    try:
        walker = os.walk(str(scan_dir), topdown=True)
        for root_text, dirnames, filenames in walker:
            root = Path(root_text)
            if any(_is_path_inside(root, value) for value in excluded):
                dirnames[:] = []
                continue
            kept = []
            for name in dirnames:
                child = root / name
                if name.startswith('.') or name.lower() in SCAN_SKIP_DIRS:
                    continue
                if any(_is_path_inside(child, value) for value in excluded):
                    continue
                kept.append(name)
            dirnames[:] = sorted(kept, key=str.lower)
            for filename in sorted(filenames, key=str.lower):
                path = root / filename
                if path.suffix.lower() in PROJECT_FILE_TYPES:
                    yield path
    except OSError:
        return


def do_add_files(proj, opts, rep):
    scan_spec = opts.scan_dirs or str(proj.dir)
    dirs = []
    for part in str(scan_spec).split(';'):
        p = Path(part.strip().strip('"')).expanduser()
        if not p.is_dir():
            rep.warnings.append('扫描目录不存在, 已跳过: %s' % p)
            continue
        dirs.append(p.resolve())
    if not dirs:
        return

    dirs, narrowed_dirs = scope_scan_dirs(proj, dirs)
    foreign_roots, output_roots, managed_roots = scan_exclusion_roots(proj, dirs)
    skipped = {'foreign_project': 0, 'build_output': 0, 'managed_component': 0}

    selected = getattr(opts, 'scan_files', None)
    if selected is not None:
        # Normalize Windows 8.3 aliases as well as case/separators. scan dirs
        # are resolved above, while selections can still contain RUNNER~1.
        selected = {os.path.normcase(str(Path(x).resolve())) for x in selected}
    existing = {proj.norm_file(fp) for fp in proj.files_in_project()}
    inc_dirs = proj.include_dirs_abs()
    inc_dirs.add(os.path.normcase(str(proj.dir)))
    new_sources = []
    new_header_dirs = []
    header_files = []
    for d in dirs:
        for f in iter_scan_files(d, foreign_roots, output_roots, managed_roots):
            suf = f.suffix.lower()
            if selected is not None and os.path.normcase(str(f.resolve())) not in selected:
                continue
            try:
                rel = f.relative_to(d)
            except ValueError:
                continue
            if any(p.startswith('.') for p in rel.parts[:-1]):
                continue
            if any(p.lower() in SCAN_SKIP_DIRS for p in rel.parts[:-1]):
                continue
            reason = scan_skip_reason(f, foreign_roots, output_roots, managed_roots)
            if reason:
                skipped[reason] += 1
                continue
            if suf in COMPILED_SUFFIXES:
                key = proj.norm_file(str(f))
                if key in existing:
                    continue
                existing.add(key)
                new_sources.append(f)
            else:
                parent_key = os.path.normcase(str(f.parent.resolve()))
                if parent_key not in inc_dirs:
                    inc_dirs.add(parent_key)
                    new_header_dirs.append(f.parent)
                if opts.include_h and proj.norm_file(str(f)) not in existing:
                    existing.add(proj.norm_file(str(f)))
                    header_files.append(f)

    for f in new_sources:
        fp = rel_or_abs(f, proj.dir)
        rel = fp.rstrip('\\')
        parts = [x for x in rel.split('\\')[:-1] if x not in ('.', '..')]
        group = '/'.join(parts) or 'ProjectRoot'
        if proj.add_file(group, f.name, PROJECT_FILE_TYPES[f.suffix.lower()], fp):
            rep.files.append((group, f.name))
    for d in new_header_dirs:
        proj.add_include_path(rel_or_abs(d, proj.dir), rep)
    if opts.include_h:
        for f in header_files:
            fp = rel_or_abs(f, proj.dir)
            parts = [x for x in fp.split('\\')[:-1] if x not in ('.', '..')]
            group = '/'.join(parts) or 'ProjectRoot'
            if proj.add_file(group, f.name, 5, fp):
                rep.files.append((group, f.name))
    if not rep.has_changes():
        rep.notes.append('没有发现新文件, 工程无需修改')
    else:
        by_type = {}
        for path in new_sources:
            key = path.suffix.lower()
            by_type[key] = by_type.get(key, 0) + 1
        summary = ', '.join('%s=%d' % (key, by_type[key]) for key in sorted(by_type)) or '无编译文件'
        rep.notes.append('新增工程元素: %s；新增 %d 个头文件检索目录' %
                         (summary, len(new_header_dirs)))
    if narrowed_dirs:
        rep.warnings.append('已阻止跨工程扫描：所选目录覆盖整个工作区，'
                            '已自动收窄到当前工程 %s' % project_content_root(proj))
    elif foreign_roots:
        rep.warnings.append('已阻止跨工程扫描：隔离其他 Keil 工程目录 %d 个' %
                            len(foreign_roots))
    if output_roots:
        rep.notes.append('已排除 %d 个 Target 输出/列表目录及其构建产物' %
                         len(output_roots))
    if managed_roots:
        rep.notes.append('已排除 Middlewares/Third_Party；该目录由组件移植任务独立管理')


# ===========================================================================
# 任务: Target 级工程设置
# ===========================================================================
def _parse_size(value, label):
    if value in (None, ''):
        return None
    try:
        parsed = int(str(value).strip(), 0)
    except ValueError:
        raise ToolError('%s必须是十进制或 0x 开头的十六进制整数' % label)
    if parsed < 0:
        raise ToolError('%s不能为负数' % label)
    return parsed


def patch_startup_memory(text, stack_size=None, heap_size=None):
    changed = False
    replacements = (('Stack_Size', stack_size), ('Heap_Size', heap_size))
    for symbol, value in replacements:
        if value is None:
            continue
        pattern = re.compile(r'(?mi)^(\s*' + symbol + r'\s+EQU\s+)(0x[0-9a-f]+|\d+)')
        match = pattern.search(text)
        if match:
            replacement = match.group(1) + '0x%X' % value
            if replacement != match.group(0):
                text = text[:match.start()] + replacement + text[match.end():]
                changed = True
    return text, changed


def do_project_settings(proj, opts, rep):
    for macro in getattr(opts, 'define_values', None) or []:
        proj.set_define(macro, rep)
    for name in getattr(opts, 'remove_defines', None) or []:
        if not proj.remove_define_name(name, rep):
            rep.notes.append('宏不存在，无需删除: %s' % name)
    if getattr(opts, 'clean_includes', False) or getattr(opts, 'remove_missing_includes', False):
        proj.clean_include_paths(bool(getattr(opts, 'remove_missing_includes', False)), rep)
    optimization = getattr(opts, 'optimization', None)
    if optimization:
        proj.set_optimization(optimization, rep)
    debug_info = getattr(opts, 'debug_information', None)
    if debug_info is not None:
        proj.set_debug_information(bool(debug_info), rep)
    if getattr(opts, 'clear_scatter', False):
        proj.clear_scatter_file(rep)
    elif getattr(opts, 'scatter_file', None):
        proj.set_scatter_file(opts.scatter_file, rep)

    stack_size = _parse_size(getattr(opts, 'stack_size', None), 'Stack Size')
    heap_size = _parse_size(getattr(opts, 'heap_size', None), 'Heap Size')
    if stack_size is not None or heap_size is not None:
        found = 0
        seen = set()
        for record in proj.file_records():
            if record['target'] not in proj.target_names():
                continue
            if Path(record['name']).suffix.lower() not in ('.s', '.asm'):
                continue
            if 'startup' not in record['name'].lower():
                continue
            raw = Path(record['path'].replace('\\', os.sep))
            path = raw if raw.is_absolute() else proj.dir / raw
            path = path.resolve()
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            text = read_source_text(path)
            patched, changed = patch_startup_memory(text, stack_size, heap_size)
            if changed:
                rep.gen_files.append((path, patched, '更新启动文件 Stack/Heap 大小'))
                found += 1
        if not found:
            rep.warnings.append('所选 Target 中未找到可修改的 startup*.s；'
                                'AC6 工程也可能在 scatter 文件中定义 Stack/Heap')
        else:
            rep.notes.append('已规划修改 %d 个启动文件的 Stack/Heap' % found)
# ===========================================================================
# 任务2: FreeRTOS + CMSIS-RTOS V2
# ===========================================================================
def locate_freertos_source(d):
    d = Path(d).expanduser()
    for c in (d, d / 'FreeRTOS', d / 'FreeRTOS-Kernel', d / 'Source',
              d / 'FreeRTOS' / 'Source'):
        if (c / 'include' / 'FreeRTOS.h').is_file():
            return c
    if (d / 'FreeRTOS.h').is_file():
        return d.parent
    return None


def detect_port(proj, base, opts):
    manual = getattr(opts, 'port_rel', None)
    if manual:
        manual = str(manual).replace('\\', '/').strip('/')
        if (base / 'portable' / 'RVDS' / manual / 'port.c').is_file():
            return manual
        raise ToolError('手动指定的移植层不存在: portable/RVDS/%s' % manual)
    core, fpu = proj.core_info()
    rvds = base / 'portable' / 'RVDS'
    if core:
        cands = list(CORE_DIRS.get(core, []))
        if not fpu:
            cands = [c for c in cands if ('F' not in c and 'r0p1' not in c)] + \
                    [c for c in cands if ('F' in c or 'r0p1' in c)]
        for c in cands:
            if (rvds / c / 'port.c').is_file():
                return c
    avail = set()
    if rvds.is_dir():
        for p in rvds.glob('*/*/port.c'):
            if 'ARM' in p.parts:
                avail.add(str(p.parent.relative_to(rvds)).replace('\\', '/'))
        for p in rvds.glob('*/port.c'):
            avail.add(str(p.parent.relative_to(rvds)).replace('\\', '/'))
    avail = sorted(avail)
    if not avail:
        raise ToolError('未找到任何 RVDS 移植目录 (portable/RVDS/.../port.c), 请检查 FreeRTOS 目录')
    if getattr(opts, 'interactive', False):
        log('无法自动匹配芯片内核, 请手动选择 Keil 移植层:')
        for i, a in enumerate(avail, 1):
            log('  [%d] %s' % (i, a))
        s = ask('请输入序号 (1-%d)' % len(avail))
        if s and s.isdigit() and 1 <= int(s) <= len(avail):
            return avail[int(s) - 1]
        raise ToolError('未选择移植层, 已取消 FreeRTOS 移植')
    raise ToolError('无法自动匹配芯片内核与移植层, 可选目录: ' + ', '.join(avail))


def detect_cmsis_device_header(proj):
    """推断 CMSIS 设备头文件名，例如 STM32G431 -> stm32g4xx.h。"""
    search_roots = [proj.dir]
    if proj.dir.parent != proj.dir:
        search_roots.append(proj.dir.parent)
    for root in search_roots:
        try:
            sources = sorted(root.rglob('system_*.c'))
        except OSError:
            sources = []
        for src in sources:
            # 忽略 CMSIS 自带示例，优先工程自身的 system_xxx.c。
            if any(x.lower() in ('examples', 'example') for x in src.parts):
                continue
            try:
                head = read_source_text(src)[:12000]
            except OSError:
                continue
            for name in re.findall(r'#\s*include\s*[<"]([^>"]+\.h)[>"]', head):
                low = name.lower()
                if low.startswith('system_') or low.startswith('core_'):
                    continue
                if low.startswith(('stm32', 'gd32', 'nrf', 'lpc', 'mm32', 'hc32', 'at32', 'apm32')):
                    return name

    dev = (proj.device() or '').lower()
    m = re.match(r'stm32([a-z][0-9])', dev)
    if m:
        return 'stm32%sxx.h' % m.group(1)
    if dev.startswith('nrf'):
        return 'nrf.h'
    for vendor in ('gd32', 'mm32', 'hc32', 'at32', 'apm32'):
        m = re.match(vendor + r'([a-z][0-9])', dev)
        if m:
            return vendor + m.group(1) + 'xx.h'
    return None


def find_cmsis_device_header_path(proj, header_name):
    """在工程及已有 Include Path 中定位设备头文件，供 CMSIS-RTOS2 自动补路径。"""
    if not header_name:
        return None
    roots = [Path(value) for value in proj.include_dirs_abs()]
    content = project_content_root(proj)
    roots.extend((content / 'Drivers' / 'CMSIS', content / 'Core', proj.dir))
    seen = set()
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(root))
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        direct = root / header_name
        if direct.is_file():
            return direct.resolve()
        try:
            hits = list(root.rglob(header_name))
        except OSError:
            hits = []
        if hits:
            hits.sort(key=lambda p: (len(p.parts), str(p)))
            return hits[0].resolve()
    return None


def find_project_systick_source(proj, exclude=()):
    """优先只在工程已引用的源文件中查找 SysTick_Handler。

    不能对整个工程根目录做无条件 rglob：.keil-port-tool/transactions
    里保存着历史源码快照，将它误认为当前源文件会破坏回滚依据。
    """
    excluded = [Path(x).resolve() for x in exclude if x]
    seen = set()
    candidates = []

    # 能与 cmsis_os2.c 发生 handler 冲突的文件必然会参与工程
    # 编译，因此工程 XML 记录是最可靠的候选集。
    for record in proj.file_records():
        raw = Path((record.get('path') or '').replace('\\', os.sep))
        path = raw if raw.is_absolute() else proj.dir / raw
        if path.suffix.lower() == '.c':
            candidates.append(path)

    # 兼容尚未把中断文件加入 XML 的 CubeMX 工程，但只在标准
    # Core/Src 内回退查找，不扫描状态目录、备份或构建产物。
    content = project_content_root(proj)
    source_root = content / 'Core' / 'Src'
    if source_root.is_dir():
        try:
            candidates.extend(sorted(source_root.glob('*.c')))
        except OSError:
            pass

    for path in candidates:
        try:
            rp = path.resolve()
        except OSError:
            continue
        if rp in seen or any(rp == e or e in rp.parents for e in excluded):
            continue
        seen.add(rp)
        if not rp.is_file():
            continue
        try:
            text = read_source_text(rp)
        except OSError:
            continue
        if re.search(r'\bvoid\s+SysTick_Handler\s*\(\s*void\s*\)', text):
            return rp, text
    return None, None




def patch_freertos_config(text, device_header=None, custom_systick=False,
                          conflicts_out=None, force=False):
    """只补缺失宏；已有且不同的用户值默认保留，并通过 conflicts_out 报告。"""
    ensures = [
        ('configUSE_MUTEXES', '1'),
        ('configUSE_RECURSIVE_MUTEXES', '1'),
        ('configUSE_COUNTING_SEMAPHORES', '1'),
        ('configSUPPORT_STATIC_ALLOCATION', '1'),
        ('configSUPPORT_DYNAMIC_ALLOCATION', '1'),
        ('configUSE_POSIX_ERRNO', '1'),
        ('vPortSVCHandler', 'SVC_Handler'),
        ('xPortPendSVHandler', 'PendSV_Handler'),
        ('configUSE_TIMERS', '1'),
        ('configTIMER_TASK_PRIORITY', '( configMAX_PRIORITIES - 1 )'),
        ('configTIMER_QUEUE_LENGTH', '10'),
        ('configTIMER_TASK_STACK_DEPTH', '256'),
        # ARM CMSIS-FreeRTOS 适配层 (freertos_os2.h) 的硬性要求
        ('configMAX_PRIORITIES', '56'),
        ('configUSE_PORT_OPTIMISED_TASK_SELECTION', '0'),
        ('configUSE_16_BIT_TICKS', '0'),
        ('configUSE_TASK_NOTIFICATIONS', '1'),
        ('INCLUDE_vTaskDelay', '1'),
        ('INCLUDE_vTaskDelete', '1'),
        ('INCLUDE_vTaskSuspend', '1'),
        ('INCLUDE_xTaskGetCurrentTaskHandle', '1'),
        ('INCLUDE_eTaskGetState', '1'),
        ('INCLUDE_vTaskPrioritySet', '1'),
        ('INCLUDE_uxTaskPriorityGet', '1'),
        ('INCLUDE_xSemaphoreGetMutexHolder', '1'),
        ('INCLUDE_xTaskDelayUntil', '1'),
        ('INCLUDE_xTimerPendFunctionCall', '1'),
        ('INCLUDE_xTaskResumeFromISR', '1'),
        ('INCLUDE_xTaskGetSchedulerState', '1'),
        ('INCLUDE_uxTaskGetStackHighWaterMark', '1'),
    ]
    if custom_systick:
        ensures.append(('USE_CUSTOM_SYSTICK_HANDLER_IMPLEMENTATION', '1'))
    changed = False
    for name, val in ensures:
        pat = re.compile(r'^[ \t]*#define[ \t]+' + re.escape(name) + r'\b.*$', re.M)
        m = pat.search(text)
        newline = '#define %s %s' % (name, val)
        if m:
            # 语义比较: 值相同则保留原行 (包括原有的对齐空格), 避免反复重写
            line = m.group(0)
            parts = line.split()
            cur_val = ' '.join(parts[2:]).split('/*')[0].strip() if len(parts) >= 3 else ''
            if cur_val != val:
                if force:
                    text = text[:m.start()] + newline + text[m.end():]
                    changed = True
                elif conflicts_out is not None:
                    conflicts_out.append((name, cur_val, val))
        else:
            anchor = re.search(r'^[ \t]*#define[ \t]+INCLUDE_', text, re.M)
            if anchor:
                text = text[:anchor.start()] + newline + '\n' + text[anchor.start():]
            else:
                i = text.rfind('#endif')
                text = text[:i] + newline + '\n' + text[i:]
            changed = True
    # FreeRTOS 10.5.1 起 xTaskDelayUntil() 已是正式 API。FreeRTOS.h 明确禁止
    # INCLUDE_vTaskDelayUntil 与 INCLUDE_xTaskDelayUntil 同时定义；旧配置中常见
    # 的 v 宏必须清理，否则每个内核源文件都会在预处理阶段报 #35。
    legacy = re.compile(r'^[ \t]*#define[ \t]+INCLUDE_vTaskDelayUntil\b[^\r\n]*(?:\r?\n)?', re.M)
    text, removed = legacy.subn('', text)
    if removed:
        changed = True
    old_assert = re.compile(
        r'^[ \t]*#define[ \t]+configASSERT[ \t]*\([ \t]*x[ \t]*\)'
        r'[^\r\n]*taskDISABLE_INTERRUPTS\(\)[^\r\n]*for[ \t]*\([ \t]*;[ \t]*;[ \t]*\)'
        r'[^\r\n]*$', re.M)
    match = old_assert.search(text)
    if match and 'void vAssertCalled(' not in text:
        replacement = (
            'void vAssertCalled(const char *file, int line);\n'
            '#define configASSERT( x ) do { if( ( x ) == 0 ) '
            'vAssertCalled(__FILE__, __LINE__); } while( 0 )')
        text = text[:match.start()] + replacement + text[match.end():]
        changed = True
    if device_header:
        macro_line = '#define CMSIS_device_header "%s"' % device_header
        pat = re.compile(r'^[ \t]*#define[ \t]+CMSIS_device_header\b.*$', re.M)
        m = pat.search(text)
        if m:
            if m.group(0).strip() != macro_line:
                if force:
                    text = text[:m.start()] + macro_line + text[m.end():]
                    changed = True
                elif conflicts_out is not None:
                    current = m.group(0).split(None, 2)[-1].strip()
                    conflicts_out.append(('CMSIS_device_header', current,
                                          '"%s"' % device_header))
        else:
            guard = re.search(r'^[ \t]*#define[ \t]+FREERTOS_CONFIG_H\b.*$', text, re.M)
            pos = guard.end() if guard else 0
            text = text[:pos] + '\n\n' + macro_line + text[pos:]
            changed = True
    return text, changed


def find_cmsis_os2_in_packs():
    """在 Keil 的 CMSIS Pack 安装目录中查找 cmsis_os2.h。"""
    roots = []
    for env in ('LOCALAPPDATA', 'APPDATA'):
        v = os.environ.get(env)
        if v:
            roots.append(Path(v) / 'Arm' / 'Packs')
    for cand in ('C:/Keil_v5/ARM/PACK', 'C:/Keil_v5/ARM/Packs'):
        roots.append(Path(cand))
    for r in roots:
        if not r.is_dir():
            continue
        try:
            hits = [p for p in r.rglob('cmsis_os2.h')
                    if p.is_file() and 'RTOS2' in p.parts]
        except OSError:
            continue
        if hits:
            hits.sort()
            rtos = hits[-1].parent
            core = rtos.parent.parent / 'Core' / 'Include'
            return rtos, core
    return None, None


def find_cmsis_os_tick_source(proj):
    """查找 CMSIS-RTOS2 的 SysTick 实现（提供 OS_Tick_Get* 系列函数）。"""
    candidates = []
    for inc in proj.include_dirs_abs():
        p = Path(inc)
        candidates.extend((p.parent / 'Source' / 'os_systick.c',
                           p / 'os_systick.c'))
    candidates.extend((proj.dir.parent / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Source' / 'os_systick.c',
                       proj.dir / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Source' / 'os_systick.c'))
    for p in candidates:
        if p.is_file():
            return p.resolve()
    # Only search roots belonging to this project, pruning backups and examples.
    # Never walk the parent workspace (which can contain unrelated projects).
    content = project_content_root(proj)
    for root in (content / 'Drivers' / 'CMSIS', content / 'CMSIS', proj.dir / 'RTE'):
        if not root.is_dir():
            continue
        for directory, dirs, files in os.walk(str(root)):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d.lower() not in SCAN_SKIP_DIRS)
            if 'os_systick.c' in files:
                return (Path(directory) / 'os_systick.c').resolve()
    return None


def find_project_cmsis_os2_include(proj, os_tick_source=None):
    """在 CubeMX/Keil 工程内定位 CMSIS-RTOS2 Include，同时要求 cmsis_os2.h。"""
    content = project_content_root(proj)
    candidates = []
    if os_tick_source:
        source_dir = Path(os_tick_source).resolve().parent
        candidates.extend((source_dir.parent / 'Include', source_dir / 'Include'))
    candidates.extend((content / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Include',
                       proj.dir / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Include'))
    seen = set()
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if (candidate / 'cmsis_os2.h').is_file():
            return candidate
    return None


def read_ioc_sysclk(project_root):
    """从 CubeMX 的 .ioc 文件读取系统主频 (Hz), 用于自动填写 configCPU_CLOCK_HZ。"""
    root = Path(project_root)
    if not root.is_dir():
        return 0
    for f in sorted(root.glob('*.ioc')):
        try:
            text = read_source_text(f)
        except OSError:
            continue
        m = re.search(r'RCC\.SYSCLKFreq_VALUE=(\d+)', text)
        if m:
            return int(m.group(1))
    return 0




def freertos_app_templates(use_os2=True):
    if use_os2:
        header = '''\
#ifndef FREERTOS_APP_H
#define FREERTOS_APP_H

#include "cmsis_os2.h"

void MX_FREERTOS_Init(void);

#endif /* FREERTOS_APP_H */
'''
        source = '''\
/* FreeRTOS application layer generated by keil_port_tool.py.
 * KPS_USER_ACTION / 用户接入：在 RTOS_THREADS 区创建任务，任务函数中填写业务。
 * CMSIS stack_size is BYTES; check each osThreadNew result and heap budget.
 * FreeRTOS tasks must not return: loop, or call osThreadExit() to finish.
 * 任务不能直接 return；完成后显式 osThreadExit()，否则会进入任务退出断言。
 * 核对 main.c 中已生成的内核启动，不要重复启动；osDelay 的单位是内核 tick。
 * See docs/POST-PORTING.en.md / docs/POST-PORTING.zh-CN.md (FreeRTOS).
 * Add thread handles/attributes in this file and create them in MX_FREERTOS_Init().
 * Put each task's work in its corresponding task function.
 */
#include "FreeRTOS.h"
#include "task.h"
#include "freertos_app.h"

volatile const char *g_freertos_assert_file;
volatile int g_freertos_assert_line;

void vAssertCalled(const char *file, int line)
{
    g_freertos_assert_file = file;
    g_freertos_assert_line = line;
    taskDISABLE_INTERRUPTS();
    for (;;)
    {
        /* Inspect g_freertos_assert_file/g_freertos_assert_line in debugger. */
    }
}

osThreadId_t defaultTaskHandle;

static const osThreadAttr_t defaultTask_attributes = {
    .name = "defaultTask",
    .stack_size = 128U * 4U,
    .priority = osPriorityNormal
};

static void StartDefaultTask(void *argument);

void MX_FREERTOS_Init(void)
{
    /* USER CODE BEGIN RTOS_MUTEX */
    /* USER CODE END RTOS_MUTEX */

    /* USER CODE BEGIN RTOS_SEMAPHORES */
    /* USER CODE END RTOS_SEMAPHORES */

    /* USER CODE BEGIN RTOS_TIMERS */
    /* USER CODE END RTOS_TIMERS */

    /* Create the default task. Add further osThreadNew() calls below. */
    defaultTaskHandle = osThreadNew(StartDefaultTask, NULL, &defaultTask_attributes);
    configASSERT(defaultTaskHandle != NULL);

    /* USER CODE BEGIN RTOS_THREADS */
    /* USER CODE END RTOS_THREADS */
}

static void StartDefaultTask(void *argument)
{
    (void)argument;
    /* USER CODE BEGIN StartDefaultTask */
    for (;;)
    {
        /* KPS_USER_ACTION: 在这里填写任务业务 / Put task work here.
         * Keep the loop blocking/yielding; do not spin at the highest priority. */
        osDelay(1U);
    }
    /* USER CODE END StartDefaultTask */
}
'''
    else:
        header = '''\
#ifndef FREERTOS_APP_H
#define FREERTOS_APP_H

#include "FreeRTOS.h"
#include "task.h"

void MX_FREERTOS_Init(void);

#endif /* FREERTOS_APP_H */
'''
        source = '''\
/* FreeRTOS application layer generated by keil_port_tool.py.
 * KPS_USER_ACTION / 用户接入：在任务循环填写业务，在 MX_FREERTOS_Init 创建新任务。
 * xTaskCreate stack depth is StackType_t elements, NOT bytes. Check return values.
 * Tasks must not return: loop, or call vTaskDelete(NULL) to finish.
 * 任务不能直接 return；完成后显式 vTaskDelete(NULL)。
 * main.c already starts the scheduler; 不要重复启动调度器。
 * See docs/POST-PORTING.en.md / docs/POST-PORTING.zh-CN.md (FreeRTOS). */
#include "FreeRTOS.h"
#include "task.h"
#include "freertos_app.h"

volatile const char *g_freertos_assert_file;
volatile int g_freertos_assert_line;

void vAssertCalled(const char *file, int line)
{
    g_freertos_assert_file = file;
    g_freertos_assert_line = line;
    taskDISABLE_INTERRUPTS();
    for (;;)
    {
        /* Inspect g_freertos_assert_file/g_freertos_assert_line in debugger. */
    }
}

TaskHandle_t defaultTaskHandle;

static void StartDefaultTask(void *argument)
{
    (void)argument;
    for (;;)
    {
        /* Put the default task processing here. */
        vTaskDelay(pdMS_TO_TICKS(1U));
    }
}

void MX_FREERTOS_Init(void)
{
    BaseType_t ok = xTaskCreate(StartDefaultTask, "defaultTask", 128U, NULL,
                                tskIDLE_PRIORITY + 1U, &defaultTaskHandle);
    configASSERT(ok == pdPASS);
    /* Add further xTaskCreate() calls here. */
}
'''
    return header, source


def add_freertos_application(proj, use_os2, rep):
    """生成类似 CubeMX 的 freertos_app.c/h，并让 main.c 自动启动调度器。"""
    project_root = proj.dir.parent if (proj.dir.parent / 'Core').is_dir() else proj.dir
    src_dir = project_root / 'Core' / 'Src'
    inc_dir = project_root / 'Core' / 'Inc'
    if not src_dir.is_dir():
        src_dir = proj.dir / 'Application'
    if not inc_dir.is_dir():
        inc_dir = src_dir
    app_c = src_dir / 'freertos_app.c'
    app_h = inc_dir / 'freertos_app.h'
    header, source = freertos_app_templates(use_os2)

    # Windows/Keil 不区分文件名大小写，应用层 freertos.h 会遮蔽内核的
    # FreeRTOS.h。仅迁移本工具旧版本生成的文件，不碰用户/CubeMX 自有文件。
    old_c = src_dir / 'freertos.c'
    old_h = inc_dir / 'freertos.h'
    if old_h.is_file():
        try:
            old_h_text = read_source_text(old_h)
        except OSError:
            old_h_text = ''
        if 'FREERTOS_APP_H' in old_h_text:
            rep.obsolete_files.append((old_h, '旧文件名与内核 FreeRTOS.h 冲突'))
    if old_c.is_file():
        try:
            old_c_text = read_source_text(old_c)
        except OSError:
            old_c_text = ''
        if 'FreeRTOS application layer generated by keil_port_tool.py' in old_c_text:
            rep.obsolete_files.append((old_c, '迁移为 freertos_app.c'))
            if proj.remove_file(old_c):
                rep.notes.append('已从工程中移除旧的 freertos.c 项')

    if not app_h.is_file():
        rep.gen_files.append((app_h, header, 'FreeRTOS 应用层头文件'))
    if not app_c.is_file():
        rep.gen_files.append((app_c, source, 'FreeRTOS 任务创建与处理文件'))
    else:
        try:
            existing_app = read_source_text(app_c)
        except OSError:
            existing_app = ''
        if 'MX_FREERTOS_Init' not in existing_app:
            rep.warnings.append('已有 freertos_app.c 但没有 MX_FREERTOS_Init()，未覆盖该文件')
        elif 'FreeRTOS application layer generated by keil_port_tool.py' in existing_app:
            upgraded_app = existing_app
            upgrades = []
            if '#include "task.h"' not in upgraded_app:
                upgraded_app = upgraded_app.replace(
                    '#include "FreeRTOS.h"',
                    '#include "FreeRTOS.h"\n#include "task.h"', 1)
                upgrades.append('task.h 依赖')
            if 'void vAssertCalled(' not in upgraded_app:
                assert_support = '''\

volatile const char *g_freertos_assert_file;
volatile int g_freertos_assert_line;

void vAssertCalled(const char *file, int line)
{
    g_freertos_assert_file = file;
    g_freertos_assert_line = line;
    taskDISABLE_INTERRUPTS();
    for (;;)
    {
        /* Inspect g_freertos_assert_file/g_freertos_assert_line in debugger. */
    }
}
'''
                include_anchor = '#include "freertos_app.h"\n'
                if include_anchor in upgraded_app:
                    upgraded_app = upgraded_app.replace(
                        include_anchor, include_anchor + assert_support, 1)
                    upgrades.append('可定位的 FreeRTOS 断言处理')
            if upgraded_app != existing_app:
                rep.gen_files.append((app_c, upgraded_app,
                                      '升级 FreeRTOS 应用层: %s' % '、'.join(upgrades)))
    if proj.add_file('FreeRTOS/Application', app_c.name, 1, rel_or_abs(app_c, proj.dir)):
        rep.files.append(('FreeRTOS/Application', app_c.name))
    proj.add_include_path(rel_or_abs(inc_dir, proj.dir), rep)

    main_candidates = [project_root / 'Core' / 'Src' / 'main.c', proj.dir / 'main.c']
    main_c = next((p for p in main_candidates if p.is_file()), None)
    if main_c:
        main_text = _planned_text(proj, main_c)
        outcome = patch_main_start_scheduler(main_text, use_os2).require_safe(main_c)
        new_main, changed = outcome
        if changed:
            rep.gen_files.append((main_c, new_main, '在 main() 中自动启动 FreeRTOS 调度器'))
        elif not re.search(r'\b(osKernelStart|vTaskStartScheduler)\s*\(', main_text):
            rep.warnings.append('main.c 没有标准 CubeMX USER CODE 标记，无法自动插入调度器启动代码')
    else:
        raise ToolError('未找到 main.c，无法验证调度器启动；请提供标准入口或选择不生成应用层')
    rep.notes.append('任务创建/处理入口: %s' % app_c)


def do_freertos(proj, opts, rep):
    if opts.freertos:
        requested_base = locate_freertos_source(opts.freertos)
        if requested_base is None:
            raise ToolError('无法在 "%s" 下找到 FreeRTOS 源码 (需含 include/FreeRTOS.h), '
                            '请传入 FreeRTOS 根目录' % opts.freertos)
    else:
        top = ensure_freertos_sdk(proj, opts, rep)
        if top is None:
            if getattr(opts, 'dry_run', False):
                rep.notes.append('DRY-RUN: 下载完成后将移植 FreeRTOS (CMSIS-V2)')
                return
            raise ToolError('未找到 FreeRTOS 源码。可用 --freertos auto 自动下载, '
                            '或手动下载发行版 zip 后用 --freertos 指定目录')
        requested_base = locate_freertos_source(top)
        if requested_base is None:
            raise ToolError('FreeRTOS 目录结构异常: %s' % top)
    requested_base = Path(requested_base).resolve()
    rep.notes.append('FreeRTOS 共享源码仓库: %s' % requested_base)

    if not getattr(opts, 'no_os2', False):
        try:
            _ensure_cmsis_os2_wrapper(requested_base, opts)
        except ToolError as e:
            rep.warnings.append(str(e))

    base, project_base = plan_project_library_copy(
        proj, requested_base, 'FreeRTOS', locate_freertos_source, rep)

    port_rel = detect_port(proj, base, opts)
    rep.notes.append('匹配到 Keil(RVDS) 移植层: portable/%s' % port_rel)

    # 1) 添加源码文件
    kernel = [base / n for n in
              ('tasks.c', 'queue.c', 'list.c', 'timers.c',
               'event_groups.c', 'stream_buffer.c', 'croutine.c')]
    heap_name = getattr(opts, 'heap_file', None) or 'heap_4.c'
    heap = base / 'portable' / 'MemMang' / heap_name
    if not heap.is_file():
        heap = base / 'portable' / 'MemMang' / 'heap_4.c'
    if not heap.is_file():
        heap = base / 'portable' / 'MemMang' / 'heap_2.c'
    if not heap.is_file():
        raise ToolError('未找到 portable/MemMang/heap_4.c (或 heap_2.c)')
    port_dir = base / 'portable' / 'RVDS' / port_rel
    port_c = port_dir / 'port.c'
    if not port_c.is_file():
        raise ToolError('未找到移植文件 %s' % port_c)

    selected = getattr(opts, 'freertos_files', None)
    selected_abs = set()
    selected_rel = set()
    if selected is not None:
        for value in selected:
            path = Path(value).resolve()
            selected_abs.add(os.path.normcase(os.path.normpath(str(path))))
            for selection_root in (requested_base, base):
                try:
                    selected_rel.add(os.path.normcase(str(path.relative_to(selection_root))))
                    break
                except ValueError:
                    pass

    def chosen(path):
        if selected is None:
            return True
        path = Path(path).resolve()
        try:
            rel_key = os.path.normcase(str(path.relative_to(base)))
            return rel_key in selected_rel
        except ValueError:
            return os.path.normcase(os.path.normpath(str(path))) in selected_abs

    def project_path(path):
        """把读取用的库路径映射为工程副本路径。"""
        return project_base / Path(path).resolve().relative_to(Path(base).resolve())

    use_os2 = not getattr(opts, 'no_os2', False)
    os2_c = base / 'CMSIS_RTOS_V2' / 'cmsis_os2.c'
    if selected is not None and not chosen(os2_c):
        use_os2 = False
    if use_os2 and not os2_c.is_file():
        rep.warnings.append('未找到 CMSIS_RTOS_V2/cmsis_os2.c (内核版本过旧?), 已跳过 CMSIS-V2 封装')
        use_os2 = False
    os_tick_c = find_cmsis_os_tick_source(proj) if use_os2 else None

    for f in kernel:
        if not chosen(f):
            continue
        if not f.is_file():
            rep.warnings.append('未找到 %s, 已跳过 (可能为旧版内核)' % f.name)
            continue
        target = project_path(f)
        if proj.add_file('FreeRTOS/Kernel', f.name, 1, rel_or_abs(target, proj.dir)):
            rep.files.append(('FreeRTOS/Kernel', f.name))
    if chosen(heap) and proj.add_file('FreeRTOS/Portable', heap.name, 1,
                                      rel_or_abs(project_path(heap), proj.dir)):
        rep.files.append(('FreeRTOS/Portable', heap.name))
    if chosen(port_c) and proj.add_file('FreeRTOS/Portable', 'port.c', 1,
                                        rel_or_abs(project_path(port_c), proj.dir)):
        rep.files.append(('FreeRTOS/Portable', 'port.c'))
    if use_os2 and proj.add_file('FreeRTOS/CMSIS-RTOS_V2', 'cmsis_os2.c', 1,
                                 rel_or_abs(project_path(os2_c), proj.dir)):
        rep.files.append(('FreeRTOS/CMSIS-RTOS_V2', 'cmsis_os2.c'))
    # os_systick.c 不属于 FreeRTOS 内核的可选源文件，而是
    # cmsis_os2.c 的强制链接依赖（提供 OS_Tick_Get* 等函数）。
    # GUI 的 freertos_files 选择集可能不包含这个工程外部文件，
    # 因此不能再用 chosen() 过滤，否则会静默制造链接错误。
    if use_os2 and os_tick_c:
        if proj.add_file('FreeRTOS/CMSIS-RTOS_V2', os_tick_c.name, 1,
                         rel_or_abs(os_tick_c, proj.dir)):
            rep.files.append(('FreeRTOS/CMSIS-RTOS_V2', os_tick_c.name))
    elif use_os2 and not os_tick_c:
        rep.warnings.append('未找到 CMSIS/RTOS2/Source/os_systick.c，CMSIS 系统计时函数将无法链接')

    # 2) include path
    proj.add_include_path(rel_or_abs(project_base / 'include', proj.dir), rep)
    proj.add_include_path(rel_or_abs(project_base / 'portable' / 'RVDS' / port_rel, proj.dir), rep)
    if (base / 'portable' / 'Common').is_dir():
        proj.add_include_path(rel_or_abs(project_base / 'portable' / 'Common', proj.dir), rep)
    if use_os2:
        proj.add_include_path(rel_or_abs(project_base / 'CMSIS_RTOS_V2', proj.dir), rep)

    # STM32Cube 等工程通常已有 SysTick_Handler；CMSIS-FreeRTOS 10.5.1 也会
    # 定义同名函数。保留用户工程的 handler，并在 CubeMX USER CODE 区接入 RTOS tick。
    custom_systick = False
    if use_os2:
        try:
            wrapper_text = read_source_text(os2_c)
        except OSError:
            wrapper_text = ''
        if re.search(r'\bvoid\s+SysTick_Handler\s*\(\s*void\s*\)', wrapper_text):
            systick_file, systick_text = find_project_systick_source(
                proj, exclude=[base, project_base])
            if systick_file:
                custom_systick = True
                outcome = patch_cmsis_wrapper_systick(wrapper_text).require_safe(os2_c)
                new_wrapper, wrapper_changed = outcome
                if wrapper_changed:
                    rep.gen_files.append((project_path(os2_c), new_wrapper,
                                          '为 CMSIS-RTOS2 SysTick 添加可配置保护'))
                new_irq, irq_changed = patch_project_systick(systick_text).require_safe(systick_file)
                if irq_changed:
                    rep.gen_files.append((systick_file, new_irq,
                                          '在现有 SysTick_Handler 中接入 FreeRTOS tick'))
                    rep.notes.append('已复用工程 SysTick_Handler: %s' % systick_file)
                elif 'xPortSysTickHandler();' not in systick_text:
                    raise ToolError(
                        '发现重复 SysTick_Handler，但该文件没有 CubeMX USER CODE 标记，'
                        '请手动调用 xPortSysTickHandler(): %s' % systick_file)

    # Cortex-M 的 SVC/PendSV 必须直接进入 FreeRTOS 的裸汇编处理函数。
    # CubeMX 生成的空强符号会覆盖启动文件弱符号，导致 svc 0 返回空处理函数，
    # 第一个任务永远无法恢复。保留 CubeMX 源码但在端口别名存在时条件禁用。
    irq_file, irq_text = find_project_systick_source(proj, exclude=[base, project_base])
    if irq_file and irq_text is not None:
        planned_index = None
        working_text = irq_text
        irq_key = os.path.normcase(str(Path(irq_file).resolve()))
        for index, (path, value, _desc) in enumerate(rep.gen_files):
            if os.path.normcase(str(Path(path).resolve())) == irq_key:
                planned_index = index
                working_text = value
        outcome = patch_project_rtos_exceptions(working_text).require_safe(irq_file)
        wrapped_irq, handlers_changed = outcome
        if handlers_changed:
            item = (irq_file, wrapped_irq,
                    '让 FreeRTOS 端口直接接管 SVC/PendSV 异常向量')
            if planned_index is None:
                rep.gen_files.append(item)
            else:
                rep.gen_files[planned_index] = item
            rep.notes.append('已禁用工程中的空 SVC/PendSV 处理函数: %s' % irq_file)
    else:
        rep.warnings.append('未找到工程中断文件，无法检查 SVC/PendSV 是否由 FreeRTOS 接管')

    # 3) FreeRTOSConfig.h
    device_header = detect_cmsis_device_header(proj) if use_os2 else None
    if use_os2:
        if device_header:
            rep.notes.append('CMSIS 设备头文件: %s' % device_header)
            # os_systick.c 不包含 FreeRTOSConfig.h，因此还需在工程编译宏中定义。
            proj.add_define('CMSIS_device_header=\\"%s\\"' % device_header, rep)
            device_path = find_cmsis_device_header_path(proj, device_header)
            if device_path:
                proj.add_include_path(rel_or_abs(device_path.parent, proj.dir), rep)
                rep.notes.append('CMSIS 设备头文件目录: %s' % device_path.parent)
            else:
                rep.warnings.append('已定义 CMSIS_device_header，但未能定位 %s 所在目录；'
                                    '请确认该目录已加入 Include Path' % device_header)
        else:
            rep.warnings.append('无法自动识别 CMSIS_device_header；请在 FreeRTOSConfig.h 中定义它')
    existing = find_in_tree(proj.dir, 'FreeRTOSConfig.h', exclude=[base, project_base])
    if existing:
        text = read_source_text(existing)
        if use_os2:
            conflicts = []
            text, changed = patch_freertos_config(
                text, device_header, custom_systick, conflicts_out=conflicts)
            if changed:
                rep.gen_files.append((existing, text,
                                      '修补已有 FreeRTOSConfig.h (补齐 CMSIS-V2 所需宏)'))
            for name, current, required in conflicts:
                rep.warnings.append('FreeRTOSConfig.h 保留用户值 %s=%s；CMSIS-V2 建议/要求为 %s，'
                                    '请在写入前确认' % (name, current, required))
        proj.add_include_path(rel_or_abs(existing.parent, proj.dir), rep)
        rep.notes.append('使用已有 FreeRTOSConfig.h: %s' % existing)
    else:
        cfg = proj.dir / 'FreeRTOS' / 'Config' / 'FreeRTOSConfig.h'
        content = DEFAULT_FREERTOS_CONFIG
        memory = embedded_memory_profile(proj)
        content = re.sub(r'(?m)^(#define\s+configTOTAL_HEAP_SIZE\s+)\d+',
                         lambda m: m.group(1) + str(memory['freertos_heap']),
                         content, count=1)
        if memory['ram'] and memory['ram'] <= 64 * 1024:
            rep.notes.append('Target RAM 为 %d KB；新 FreeRTOS 堆采用保守值 %d KB' %
                             (memory['ram'] // 1024, memory['freertos_heap'] // 1024))
        if use_os2 and device_header:
            content = content.replace(
                '#define FREERTOS_CONFIG_H',
                '#define FREERTOS_CONFIG_H\n\n#define CMSIS_device_header "%s"' % device_header,
                1)
        if custom_systick:
            content = content.replace(
                '#define FREERTOS_CONFIG_H',
                '#define FREERTOS_CONFIG_H\n\n#define USE_CUSTOM_SYSTICK_HANDLER_IMPLEMENTATION 1',
                1)
        timers_enabled = chosen(base / 'timers.c')
        croutine_enabled = chosen(base / 'croutine.c')
        content = re.sub(r'(?m)^(#define\s+configUSE_TIMERS\s+)\d+',
                         r'\g<1>%d' % (1 if timers_enabled else 0), content, count=1)
        # 默认模板没有协程宏；按文件选择同步生成，避免选了 croutine.c 却被配置关闭。
        marker = '/* 软件定时器 (CMSIS-RTOS V2 的 osTimer 依赖) */'
        content = content.replace(marker,
                                  '#define configUSE_CO_ROUTINES                 %d\n'
                                  '#define configMAX_CO_ROUTINE_PRIORITIES       2\n\n%s' %
                                  (1 if croutine_enabled else 0, marker))
        clock = read_ioc_sysclk(proj.dir.parent)
        if clock:
            rep.notes.append('.ioc 报告系统主频 %d Hz；新配置使用运行时 SystemCoreClock，避免陈旧 .ioc 或 AHB 分频导致节拍错误' % clock)
        rep.notes.append('新 FreeRTOS 配置使用 CMSIS SystemCoreClock；必须在启动调度器前完成时钟初始化并更新此变量')
        rep.gen_files.append((cfg, content,
                              '自动生成 FreeRTOSConfig.h (CMSIS-V2 就绪)'))
        proj.add_include_path('FreeRTOS\\Config', rep)

    # 4) 查找 CMSIS pack 中的 cmsis_os2.h; 都没有则自动下载这个小头文件
    if use_os2:
        found = False
        for d in proj.include_dirs_abs():
            if (Path(d) / 'cmsis_os2.h').is_file():
                found = True
                break
        if not found:
            project_rtos_inc = find_project_cmsis_os2_include(proj, os_tick_c)
            if project_rtos_inc:
                proj.add_include_path(rel_or_abs(project_rtos_inc, proj.dir), rep)
                rep.notes.append('已从当前工程找到 CMSIS-RTOS2 头文件: %s' %
                                 project_rtos_inc)
                found = True
        if not found:
            rtos_inc, core_inc = find_cmsis_os2_in_packs()
            if rtos_inc:
                proj.add_include_path(str(rtos_inc), rep)
                if core_inc and core_inc.is_dir():
                    proj.add_include_path(str(core_inc), rep)
                rep.notes.append('已从 CMSIS Pack 找到 cmsis_os2.h: %s' % rtos_inc)
            else:
                target_dir = proj.dir.parent / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Include'
                if not target_dir.is_dir():
                    target_dir = proj.dir / 'FreeRTOS' / 'Config'
                if getattr(opts, 'dry_run', False):
                    rep.notes.append('DRY-RUN: 将自动下载 cmsis_os2.h 到 %s' % target_dir)
                elif not getattr(opts, 'no_download', False):
                    url = (os.environ.get('KEIL_TOOL_CMSIS_OS2_URL')
                           or CONFIG.get('CMSIS_OS2_URL') or CMSIS_OS2_H_URL)
                    try:
                        download_file(url, target_dir / 'cmsis_os2.h', 'cmsis_os2.h (ARM CMSIS_5)')
                        proj.add_include_path(rel_or_abs(target_dir, proj.dir), rep)
                        rep.notes.append('已自动下载 cmsis_os2.h 到 %s' % target_dir)
                    except ToolError as e:
                        rep.warnings.append('%s; 请手动安装 ARM::CMSIS Pack, 或把 '
                                            'CMSIS/RTOS2/Include 加入 include path' % e)
                else:
                    rep.warnings.append('未找到 cmsis_os2.h! 请安装 ARM::CMSIS Pack '
                                        '(Keil -> Pack Installer), 或手动把 CMSIS/RTOS2/Include '
                                        '目录加入 include path')

    # 5) 应用层任务文件与调度器启动
    if getattr(opts, 'freertos_app', True):
        add_freertos_application(proj, use_os2, rep)

    # 6) 使用提示
    if getattr(opts, 'freertos_app', True):
        rep.notes.append('已自动创建 defaultTask 并在 main() 中启动调度器')
    else:
        rep.notes.append('未生成应用层；需自行创建任务并启动调度器')
    if chosen(heap):
        rep.notes.append('内存方案为 %s' % heap.name)
    else:
        rep.warnings.append('未选择 heap_x.c；使用动态内存 API 时会链接失败')
    rep.notes.append('请确认 configCPU_CLOCK_HZ 与 CPU/HCLK 实际频率一致；已有用户配置不会被强制改写')


# ===========================================================================
# 任务3: LVGL
# ===========================================================================
def locate_lvgl_root(d):
    d = Path(d).expanduser()
    for c in (d, d / 'lvgl'):
        if (c / 'lvgl.h').is_file() and (c / 'src').is_dir():
            return c
    return None


def lvgl_version(root):
    for cand in (root / 'lv_version.h', root / 'src' / 'lv_version.h'):
        try:
            text = read_source_text(cand)
        except OSError:
            continue
        m = re.search(r'LVGL_VERSION_MAJOR\s+(\d+)', text)
        if m:
            return int(m.group(1))
    return 0


def enable_lv_conf(text):
    """把 lv_conf_template.h 顶部的 #if 0 打开成 #if 1。"""
    m = re.search(r'(?m)^#if[ \t]+0[ \t]*/\*[^\n]*enable content[^\n]*\*/', text)
    if m:
        line = re.sub(r'#if[ \t]+0', '#if 1', m.group(0), count=1)
        return text[:m.start()] + line + text[m.end():]
    m = re.search(r'(?m)^#if[ \t]+0[ \t]*$', text)
    if m:
        return text[:m.start()] + '#if 1' + text[m.end():]
    return text


LVGL_CONF_FORWARDER = (
    '/* Generated by keil_port_tool.py: LVGL configuration forwarder. */\n'
    '/* Edit the project configuration in ../lv_conf.h, not this file. */\n'
    '#include "../lv_conf.h"\n'
)


def is_lvgl_conf_forwarder(text):
    """只接管完整且未被修改的工具转发头，不能仅凭标识覆盖用户内容。"""
    return text.replace('\r\n', '\n').strip() == LVGL_CONF_FORWARDER.strip()


def do_lvgl(proj, opts, rep):
    if project_uses_rtthread(proj):
        # Kernel-only integration has no RT-Thread Env/Kconfig LVGL package.
        # Keep using the project's explicit lv_conf.h instead of requiring
        # lv_rt_thread_conf.h from a BSP that was never installed.
        proj.add_define('LV_KCONFIG_IGNORE', rep)
    if opts.lvgl:
        requested_root = locate_lvgl_root(opts.lvgl)
        if requested_root is None:
            raise ToolError('无法在 "%s" 下找到 LVGL (需含 lvgl.h 与 src 目录)' % opts.lvgl)
    else:
        top = ensure_lvgl_sdk(proj, opts, rep)
        if top is None:
            if getattr(opts, 'dry_run', False):
                rep.notes.append('DRY-RUN: 下载完成后将移植 LVGL')
                return
            raise ToolError('未找到 LVGL 源码。可用 --lvgl auto 自动下载, '
                            '或手动下载 lvgl 源码后用 --lvgl 指定目录')
        requested_root = locate_lvgl_root(top)
        if requested_root is None:
            raise ToolError('LVGL 目录结构异常: %s' % top)
    requested_root = Path(requested_root).resolve()
    root, project_lvgl = plan_project_library_copy(
        proj, requested_root, 'LVGL', locate_lvgl_root, rep)
    ver = lvgl_version(root)
    rep.notes.append('LVGL 共享源码仓库: %s%s' %
                     (requested_root, (' (版本 v%d)' % ver) if ver else ''))

    # 1) lv_conf.h。LVGL v8 在未识别 LV_CONF_INCLUDE_SIMPLE 时会从
    # src/lv_conf_internal.h 使用 ../../lv_conf.h，因此标准位置是 lvgl 目录的同级。
    # 同时保留 INCLUDE_SIMPLE，兼容 v8/v9 和不同编译器的宏处理差异。
    inner_conf = root / 'lv_conf.h'
    shared_conf = requested_root.parent / 'lv_conf.h'
    conf = project_lvgl.parent / 'lv_conf.h'
    project_inner_conf = project_lvgl / 'lv_conf.h'
    # INCLUDE_SIMPLE can find the library root before its parent. A private
    # forwarding header makes both that route and LVGL's ../../lv_conf.h
    # fallback resolve to ONE project configuration. Never replace an existing
    # project-local custom configuration, even if it happens to match the other
    # copy today: the user must choose/merge it explicitly first.
    if project_inner_conf.is_file() and not is_lvgl_conf_forwarder(
            read_source_text(project_inner_conf)):
        raise ToolError(
            '检测到工程内自定义 LVGL 配置，Include 顺序可能使同级配置失效: %s。'
            '为保留已有设置，本次 LVGL 未写入。请先比较该文件与 %s，'
            '把选定配置合并保存在后者，并备份/移走前者后重试；'
            '工具随后只会创建指向同级配置的转发头。' % (project_inner_conf, conf))
    if project_inner_conf.exists() and not project_inner_conf.is_file():
        raise ToolError('LVGL 配置目标不是普通文件: %s' % project_inner_conf)
    if (root / 'src' / 'lv_conf.h').exists():
        raise ToolError('LVGL src/lv_conf.h 会优先于工程配置，'
                        '请先明确配置来源并移除冲突后重试: %s' %
                        (root / 'src' / 'lv_conf.h'))
    known_configs = {path.resolve() for path in
                     (inner_conf, shared_conf, project_inner_conf, conf)}
    for include_dir in sorted(proj.include_dirs_abs()):
        candidate = Path(include_dir) / 'lv_conf.h'
        if candidate.is_file() and candidate.resolve() not in known_configs:
            raise ToolError('已有 Include 目录包含另一份 LVGL 配置，'
                            '可能遮蔽 %s；请先合并/明确配置来源后重试: %s' %
                            (conf, candidate))
    custom_config_macros = [token for token in _project_define_tokens(proj)
                            if token.split('=', 1)[0].strip() in
                            ('LV_CONF_PATH', 'LV_CONF_SKIP')]
    if custom_config_macros:
        raise ToolError('工程已通过 %s 自定义 LVGL 配置来源；'
                        '请先确认并合并到 %s 后移除该宏，避免工具配置被绕过。' %
                        (', '.join(custom_config_macros), conf))
    memory = embedded_memory_profile(proj)

    def adapt_new_lv_conf(text):
        text = re.sub(
            r'(?m)^(\s*#define\s+LV_MEM_SIZE\s+).*$',
            lambda m: m.group(1) + '(%dU * 1024U) /* Target RAM 自适应默认值 */' %
            memory['lvgl_heap_kb'], text, count=1)
        marker = 'Generated by keil_port_tool.py; tune LV_MEM_SIZE for the product.'
        return text if marker in text else '/* %s */\n%s' % (marker, text)

    if conf.is_file():
        rep.notes.append('检测到已有 lv_conf.h, 未做修改: %s' % conf)
        existing_conf = read_source_text(conf)
        heap_match = re.search(
            r'(?m)^\s*#define\s+LV_MEM_SIZE\s+\(?\s*(\d+)U?\s*\*\s*1024U?\s*\)?',
            existing_conf)
        if heap_match and memory['ram']:
            lv_heap = int(heap_match.group(1)) * 1024
            if lv_heap >= memory['ram']:
                rep.warnings.append(
                    '已有 LV_MEM_SIZE=%d KB，而 Target RAM 仅 %d KB；工具保留用户配置，'
                    '请减小 LV_MEM_SIZE 或改用外部内存' %
                    (lv_heap // 1024, memory['ram'] // 1024))
    elif shared_conf.is_file():
        text = adapt_new_lv_conf(read_source_text(shared_conf))
        rep.gen_files.append((conf, text, '复制 LVGL 配置到工程'))
        rep.notes.append('将共享 lv_conf.h 复制到工程: %s' % conf)
    elif inner_conf.is_file() and not is_lvgl_conf_forwarder(read_source_text(inner_conf)):
        text = adapt_new_lv_conf(read_source_text(inner_conf))
        rep.gen_files.append((conf, text, '复制到 LVGL 默认查找位置'))
        rep.notes.append('将 lv_conf.h 放到 LVGL 目录同级: %s' % conf)
    else:
        tpl = root / 'lv_conf_template.h'
        if not tpl.is_file():
            raise ToolError('未找到 lv_conf_template.h')
        text = read_source_text(tpl)
        text = enable_lv_conf(text)
        cd = int(getattr(opts, 'color_depth', 16) or 16)
        text = re.sub(r'(?m)^(\s*#define\s+LV_COLOR_DEPTH\s+)\d+',
                      lambda m: m.group(1) + str(cd), text, count=1)
        text = adapt_new_lv_conf(text)
        rep.gen_files.append((conf, text,
                              '由 lv_conf_template.h 生成并启用 (色深 %d bit)' % cd))
        rep.notes.append('lv_conf.h 生成在 LVGL 目录同级: %s' % conf)
        if memory['ram'] and memory['ram'] <= 64 * 1024:
            rep.notes.append('Target RAM 为 %d KB；新 LVGL 内存池采用保守值 %d KB' %
                             (memory['ram'] // 1024, memory['lvgl_heap_kb']))

    if not project_inner_conf.is_file():
        # run_tasks copies source trees before writing gen_files. For a new
        # copy this replaces only the copied root configuration, never the SDK
        # original; prepare_transaction tracks both the directory and header.
        rep.gen_files.append((project_inner_conf, LVGL_CONF_FORWARDER,
                              '统一 LVGL 配置入口（转发到工程同级 lv_conf.h）'))
    rep.notes.append('LVGL 有效配置入口: %s；库内 lv_conf.h 仅转发至此' % conf)

    # 2) src 下全部 .c
    src = root / 'src'
    if not src.is_dir():
        raise ToolError('未找到 src 目录')
    selected = getattr(opts, 'lvgl_files', None)
    selected_rel = set()
    if selected is not None:
        for value in selected:
            path = Path(value).resolve()
            for selection_root in (requested_root, root):
                try:
                    selected_rel.add(os.path.normcase(str(path.relative_to(selection_root))))
                    break
                except ValueError:
                    pass
    count = 0
    for f in sorted(src.rglob('*.c')):
        if not f.is_file():
            continue
        if selected is not None:
            rel_key = os.path.normcase(str(f.relative_to(root)))
            if rel_key not in selected_rel:
                continue
        rel = f.relative_to(src)
        parts = [p for p in rel.parts[:-1]]
        group = 'LVGL/' + '/'.join(parts) if parts else 'LVGL'
        target = project_lvgl / f.relative_to(root)
        if proj.add_file(group, f.name, 1, rel_or_abs(target, proj.dir)):
            rep.files.append((group, f.name))
            count += 1

    # 3) include path / 宏 / C99
    if requested_root.parent != project_lvgl.parent:
        proj.remove_include_path(requested_root.parent)
    proj.add_include_path(rel_or_abs(project_lvgl, proj.dir), rep)
    proj.add_include_path(rel_or_abs(project_lvgl.parent, proj.dir), rep)
    proj.add_include_path(rel_or_abs(project_lvgl / 'src', proj.dir), rep)
    proj.add_define('LV_CONF_INCLUDE_SIMPLE', rep)
    proj.add_define('LV_LVGL_H_INCLUDE_SIMPLE', rep)
    proj.enable_c99_gnu(rep)

    # 4) 可选: disp/indev 移植模板
    if getattr(opts, 'ports', False):
        src_dir = root / 'examples' / 'porting'
        names = ('lv_port_disp_template.c', 'lv_port_disp_template.h',
                 'lv_port_indev_template.c', 'lv_port_indev_template.h')
        copied = 0
        for n in names:
            sf = src_dir / n
            if not sf.is_file():
                rep.warnings.append('未找到移植模板 %s, 已跳过' % n)
                continue
            dst = proj.dir / 'LVGL' / 'porting' / n
            if dst.is_file():
                continue  # 已存在, 幂等跳过
            guidance = ('/* KPS_USER_ACTION / 用户接入 (LVGL):\n'
                        ' * Enable this upstream template; set resolution/rotation/controller offsets.\n'
                        ' * 启用模板开关，填写显示初始化、flush；无触摸不必注册 indev。\n'
                        ' * Signal flush_ready only AFTER DMA completes; keep pixel buffers alive.\n'
                        ' * DMA 完成后再通知刷新完成；真实毫秒 tick 和 timer_handler 都要接入。\n'
                        ' * See docs/POST-PORTING.en.md / docs/POST-PORTING.zh-CN.md (LVGL).\n'
                        ' */\n')
            rep.gen_files.append((dst, guidance + read_source_text(sf),
                                  'LVGL 移植模板'))
            if n.endswith('.c') and proj.add_file('LVGL/porting', n, 1,
                                                  rel_or_abs(dst, proj.dir)):
                rep.files.append(('LVGL/porting', n))
            copied += 1
        if copied:
            proj.add_include_path('LVGL\\porting', rep)

    if ver >= 9 and not proj.any_ac6():
        rep.warnings.append('LVGL v9 不支持 Arm Compiler 5 (AC5), '
                            '请在 Keil 中切换到 AC6 (Options -> Target -> ARM Compiler -> V6)')
    rep.notes.append('已添加 %d 个 LVGL 源文件' % count)
    rep.notes.append('记得周期性调用 lv_tick_inc(1) (例如放在 SysTick_Handler 里), '
                     '或启用 LV_TICK_CUSTOM')
    rep.notes.append('显示/触摸驱动需要自己实现 (可用复制出的 lv_port_* 模板)')


# ===========================================================================
# 任务4: FatFS
# ===========================================================================
def locate_fatfs_root(d):
    """返回同时包含 ff.c/ff.h 的 FatFS 源码目录。兼容 ST source 与常见 src 布局。"""
    if not d:
        return None
    d = Path(d).expanduser()
    for c in (d, d / 'source', d / 'src', d / 'FatFs', d / 'Middlewares' /
              'Third_Party' / 'FatFs' / 'src'):
        if (c / 'ff.c').is_file() and (c / 'ff.h').is_file():
            return c
    return None


def locate_rtthread_root(path):
    path = Path(path)
    if (path / 'include/rtthread.h').is_file() and (path / 'src/thread.c').is_file():
        return path.resolve()
    return None


def project_uses_rtthread(proj):
    return any('rtthread' in str(p).lower() or 'rt-thread' in str(p).lower()
               for p in proj.files_in_project())


def ensure_rtthread_sdk(proj, opts, rep):
    sdk = Path(getattr(opts, 'sdk_dir', None) or default_sdk_dir(proj))
    found = find_existing_sdk(sdk, locate_rtthread_root)
    if found:
        return found
    if getattr(opts, 'no_download', False):
        raise ToolError('找不到 RT-Thread 源码；请用 --rtthread 指定 v5.2.2 源码目录')
    if getattr(opts, 'dry_run', False):
        rep.notes.append('DRY-RUN: 将下载固定 RT-Thread %s（标准仓库较大）' % RTTHREAD_TAG)
        return None
    url = 'https://codeload.github.com/RT-Thread/rt-thread/zip/refs/tags/' + RTTHREAD_TAG
    archive = sdk / ('rt-thread-%s.zip' % RTTHREAD_TAG)
    rep.sources.append(download_archive(url, archive, 'RT-Thread ' + RTTHREAD_TAG,
                                        expected_sha256=RTTHREAD_SHA256))
    return extract_zip(archive, sdk, keep_prefixes=('src/', 'include/',
                       'libcpu/arm/common/', 'libcpu/arm/cortex-m4/',
                       'components/libc/compilers/common/extension/'),
                       keep_files=('LICENSE', 'README.md'))


def rtthread_source_files(root):
    """Pinned standard kernel profile, not a Nano/DFS/Env package manager."""
    root = Path(root)
    names = ('clock.c', 'components.c', 'cpu_up.c', 'defunct.c', 'idle.c', 'ipc.c',
             'irq.c', 'kservice.c', 'mem.c', 'memheap.c', 'mempool.c', 'object.c',
             'scheduler_comm.c', 'scheduler_up.c', 'signal.c', 'slab.c', 'thread.c', 'timer.c')
    files = [root / 'src' / name for name in names]
    files += [root / 'src/klibc' / name for name in
              ('kerrno.c', 'kstdio.c', 'kstring.c', 'rt_vsnprintf_tiny.c', 'rt_vsscanf.c')]
    files += [root / 'libcpu/arm/cortex-m4' / name for name in ('cpuport.c', 'context_rvds.S')]
    files += [root / 'libcpu/arm/common' / name for name in ('div0.c', 'showmem.c')]
    return files


RTTHREAD_CONFIG = '''#ifndef KPS_RTCONFIG_H
#define KPS_RTCONFIG_H
/* RT-Thread 5.2.2 standard kernel, single Cortex-M4; NOT a complete BSP/Env. */
#define RT_NAME_MAX 16
#define RT_ALIGN_SIZE 8
#define RT_THREAD_PRIORITY_MAX 32
#define RT_TICK_PER_SECOND 1000
#define RT_CPUS_NR 1
#define RT_USING_OVERFLOW_CHECK
#define RT_DEBUG
#define RT_DEBUGING_ASSERT
#define RT_BACKTRACE_LEVEL_MAX_NR 16
#define RT_USING_SEMAPHORE
#define RT_USING_MUTEX
#define RT_USING_EVENT
#define RT_USING_MAILBOX
#define RT_USING_MESSAGEQUEUE
#define RT_USING_MEMPOOL
#define RT_USING_SMALL_MEM
#define RT_USING_SMALL_MEM_AS_HEAP
#define RT_USING_HEAP
#define RT_USING_TIMER_SOFT
#define RT_TIMER_THREAD_PRIO 4
#define RT_TIMER_THREAD_STACK_SIZE 1024
#define RT_TIMER_TICK_PER_SECOND RT_TICK_PER_SECOND
#define IDLE_THREAD_STACK_SIZE 512
#define RT_USING_CONSOLE
#define RT_CONSOLEBUF_SIZE 128
#define RT_USING_CPU_FFS
/* No RT_USING_USER_MAIN: CubeMX main initializes peripherals before KPS start. */
#define KPS_RTTHREAD_HEAP_SIZE 16384
#define KPS_RTTHREAD_APP_STACK_SIZE 2048
#endif
'''


def rtthread_app_templates(device_header):
    header = '''#ifndef KPS_RTTHREAD_APP_H
#define KPS_RTTHREAD_APP_H
#include <rtthread.h>
void MX_RTTHREAD_Init(void);
void KPS_RTTHREAD_Tick(void);
void RTThread_DefaultTask(void *argument);
extern volatile rt_uint32_t g_rtthread_heartbeat;
extern volatile rt_uint32_t g_rtthread_assert_line;
#endif
'''
    source = '''#include "rtthread_app.h"
#include <rthw.h>
#include "%s"

rt_align(RT_ALIGN_SIZE) static rt_uint8_t kernel_heap[KPS_RTTHREAD_HEAP_SIZE];
rt_align(RT_ALIGN_SIZE) static rt_uint8_t app_stack[KPS_RTTHREAD_APP_STACK_SIZE];
static struct rt_thread app_thread;
static volatile rt_uint8_t kernel_ready;
volatile rt_uint32_t g_rtthread_heartbeat;
volatile rt_uint32_t g_rtthread_assert_line;

#if RT_TICK_PER_SECOND != 1000
#error "KPS CubeMX shared SysTick requires RT_TICK_PER_SECOND=1000 for the HAL 1 ms tick"
#endif
#ifdef RT_DEBUGING_ASSERT
static void KPS_RTThread_Assert(const char *expression, const char *function, rt_size_t line)
{
    (void)expression;
    (void)function;
    g_rtthread_assert_line = (rt_uint32_t)line;
    rt_hw_interrupt_disable();
    for (;;) { } /* Inspect line and the call stack with the debugger. */
}
#endif

/* USER CODE BEGIN RTThread_DefaultTask */
/* KPS_USER_ACTION / 用户接入：在此线程中填写业务，创建并启动其他线程。
 * Smaller priority numbers are higher; stack sizes are BYTES.
 * rtconfig.h 配置堆/栈；当前共享 SysTick 要求 1000 Hz，不要重复启动内核。
 * See docs/POST-PORTING.en.md / docs/POST-PORTING.zh-CN.md (RT-Thread). */
void RTThread_DefaultTask(void *argument)
{
    (void)argument;
    for (;;)
    {
        ++g_rtthread_heartbeat;
        rt_thread_mdelay(1000);
    }
}
/* USER CODE END RTThread_DefaultTask */

void KPS_RTTHREAD_Tick(void)
{
    if (!kernel_ready) return;
    rt_interrupt_enter();
    rt_tick_increase();
    rt_interrupt_leave();
}

void MX_RTTHREAD_Init(void)
{
    rt_err_t status;
    rt_hw_interrupt_disable();
#ifdef RT_DEBUGING_ASSERT
    rt_assert_set_hook(KPS_RTThread_Assert);
#endif
    rt_system_timer_init();
    rt_system_scheduler_init();
    /* RT_USING_SMALL_MEM_AS_HEAP selects the allocator; this call initializes
       its backing memory exactly once before threads allocate from it. */
    rt_system_heap_init(kernel_heap, kernel_heap + sizeof(kernel_heap));
    status = rt_thread_init(&app_thread, "app", RTThread_DefaultTask, RT_NULL,
                            app_stack, sizeof(app_stack), 16, 10);
    RT_ASSERT(status == RT_EOK);
    status = rt_thread_startup(&app_thread);
    RT_ASSERT(status == RT_EOK);
    rt_system_timer_thread_init();
    rt_thread_idle_init();
    rt_thread_defunct_init();
    /* Clock is sampled AFTER CubeMX SystemClock_Config(). HAL tick stays 1 ms. */
    SystemCoreClockUpdate();
    status = (rt_err_t)SysTick_Config(SystemCoreClock / RT_TICK_PER_SECOND);
    RT_ASSERT(status == 0);
    if (status != 0) for (;;) { }
    kernel_ready = 1;
    rt_system_scheduler_start();
    RT_ASSERT(0); /* scheduler must never return */
}
''' % device_header
    return header, source




def do_rtthread(proj, opts, rep):
    if project_uses_freertos(proj):
        raise ToolError('同一工程已存在 FreeRTOS；RT-Thread 不能同时接管调度和中断向量')
    if len(proj.targets) != len(proj.all_targets):
        raise ToolError('RT-Thread 会接入共享 main/中断文件；暂不允许只修改部分 Target')
    if any(proj.target_core_info(t)[0] != 'Cortex-M4' for t in proj.targets):
        raise ToolError('当前 RT-Thread 配置仅支持 Cortex-M4 单核；其他内核需独立端口验证')
    requested = getattr(opts, 'rtthread', None) or 'auto'
    if str(requested).lower() == 'auto':
        requested = ensure_rtthread_sdk(proj, opts, rep)
    if not requested:
        return
    requested = locate_rtthread_root(requested)
    if not requested:
        raise ToolError('未找到 RT-Thread include/rtthread.h 和 src/thread.c')
    extension = requested / 'components/libc/compilers/common/extension/sys'
    if not all((extension / name).is_file() for name in ('types.h', 'errno.h')):
        raise ToolError('RT-Thread SDK 不完整：缺少 components/libc/compilers/common/extension/sys 类型或错误码头文件')
    # Unknown versions are not silently treated as the tested layout.
    defs = read_source_text(requested / 'include/rtdef.h')
    for macro, value in (('RT_VERSION_MAJOR', 5), ('RT_VERSION_MINOR', 2), ('RT_VERSION_PATCH', 2)):
        if not re.search(r'#define\s+' + macro + r'\s+' + str(value) + r'\b', defs):
            raise ToolError('此配置要求 RT-Thread 5.2.2；请勿混用其他版本目录')
    root, local = plan_project_library_copy(proj, requested, 'RTThread', locate_rtthread_root, rep)
    files = rtthread_source_files(root)
    selected = _selected_relative(getattr(opts, 'rtthread_files', None), (requested, root))
    optional = {'memheap.c', 'mempool.c', 'signal.c', 'slab.c', 'components.c', 'rt_vsscanf.c', 'showmem.c'}
    missing = [p.name for p in files if (not p.is_file() or
               (p.name not in optional and not _component_chosen(p, root, selected)))]
    if missing:
        raise ToolError('RT-Thread 必需文件缺失或未勾选: ' + ', '.join(missing))
    device = detect_cmsis_device_header(proj)
    device_path = find_cmsis_device_header_path(proj, device) if device else None
    if not device_path:
        raise ToolError('未能定位 CMSIS 设备头文件；无法安全生成 RT-Thread 时钟端口')
    content = project_content_root(proj)
    main = content / 'Core/Src/main.c'
    irq, irq_text = find_project_systick_source(proj, exclude=[root, local])
    if not main.is_file() or not irq:
        raise ToolError('当前自动启动支持 CubeMX Core/Src/main.c 与 SysTick 中断文件')
    main_text = _planned_text(proj, main)
    if '/* USER CODE END 2 */' not in main_text:
        raise ToolError('main.c 缺少 USER CODE 2 区；不自动插入调度启动')
    config_dir = content / 'RTThread/Config'
    app_dir = content / 'RTThread/App'
    if (config_dir / 'rtconfig.h').is_file():
        existing_config = _defined_values(read_source_text(config_dir / 'rtconfig.h'))
        if existing_config.get('RT_TICK_PER_SECOND', '').strip('() ') != '1000':
            raise ToolError('RT-Thread 共享 HAL SysTick 模式要求 RT_TICK_PER_SECOND=1000；已有配置保持不变')
    config = RTTHREAD_CONFIG
    if not any(p.name == 'mempool.c' and _component_chosen(p, root, selected) for p in files):
        config = config.replace('#define RT_USING_MEMPOOL\n', '')
    for path in files:
        if _component_chosen(path, root, selected):
            dest = local / path.relative_to(root)
            if proj.add_file('RTThread/Kernel', dest.name, 2 if dest.suffix == '.S' else 1,
                             rel_or_abs(dest, proj.dir)):
                rep.files.append(('RTThread/Kernel', dest.name))
    for folder in (local / 'include', local / 'libcpu/arm/cortex-m4',
                   local / 'components/libc/compilers/common/extension', config_dir,
                   app_dir, device_path.parent):
        proj.add_include_path(rel_or_abs(folder, proj.dir), rep)
    for macro in ('KPS_USING_RTTHREAD', '__RTTHREAD__', '__RT_KERNEL_SOURCE__', 'LV_KCONFIG_IGNORE'):
        proj.add_define(macro, rep)
    proj.enable_c99_gnu(rep)
    header, source = rtthread_app_templates(device)
    for path, value in ((config_dir / 'rtconfig.h', config),
                        (app_dir / 'rtthread_app.h', header), (app_dir / 'rtthread_app.c', source)):
        if not path.exists():
            rep.gen_files.append((path, value, 'RT-Thread 工程私有配置/任务入口'))
        if path.suffix == '.c' and proj.add_file('RTThread/App', path.name, 1, rel_or_abs(path, proj.dir)):
            rep.files.append(('RTThread/App', path.name))
    new_main, changed = _patch_component_init(main_text, 'rtthread_app.h', 'MX_RTTHREAD_Init();')
    main_span = _c_function(new_main, 'main', 'int')
    body = _c_code(new_main[main_span[1] + 1:main_span[2] - 1]) if main_span else ''
    calls = list(re.finditer(r'\bMX_RTTHREAD_Init\s*\(\s*\)\s*;', body))
    if len(calls) != 1 or re.search(r'\b(if|for|while|switch|return|goto)\b|^\s*#', body[:calls[0].start()] if calls else '', re.M):
        raise ToolError('无法确认 main 中 RT-Thread 启动调用的位置/可达性；未写入，请手动适配')
    if changed:
        rep.gen_files.append((main, new_main, '外设初始化后自动启动 RT-Thread'))
    updated_irq = patch_rtthread_irq(_planned_text(proj, irq))
    if updated_irq != _planned_text(proj, irq):
        rep.gen_files.append((irq, updated_irq, 'RT-Thread SysTick/PendSV/HardFault 适配'))
    rep.notes.append('标准内核 5.2.2 / Cortex-M4 单核；不自动安装 BSP、DFS、FinSH、软件包或 SMP')
    rep.notes.append('任务入口 RTThread/App/rtthread_app.c；已有 rtconfig.h 与任务文件保留，不覆盖用户修改')


def project_uses_freertos(proj):
    """判断工程当前是否已经包含 FreeRTOS（也能识别本次任务刚加入的 XML 项）。"""
    markers = ('freertos.h', 'task.h', 'cmsis_os2.c', 'freertos_app.c')
    for value in proj.files_in_project():
        low = str(value).replace('\\', '/').lower()
        if any(('/' + marker) in ('/' + low) for marker in markers):
            return True
        if 'freertos' in low or 'cmsis-rtos' in low or 'cmsis_rtos' in low:
            return True
    root = project_content_root(proj)
    for rel in ('Core/Src/freertos_app.c', 'Core/Src/freertos.c',
                'FreeRTOS/Config/FreeRTOSConfig.h'):
        if (root / Path(rel)).is_file():
            return True
    return False


def embedded_memory_profile(proj):
    """为新配置提供与 Target RAM 相称的保守默认值。已有用户配置不在这里覆盖。"""
    ram = proj.ram_size_bytes()
    if ram and ram <= 32 * 1024:
        return {'ram': ram, 'freertos_heap': 4 * 1024, 'lvgl_heap_kb': 4,
                'lwip_heap_kb': 4, 'lwip_pbufs': 2, 'lwip_pcbs': 2}
    if ram and ram <= 64 * 1024:
        return {'ram': ram, 'freertos_heap': 8 * 1024, 'lvgl_heap_kb': 8,
                'lwip_heap_kb': 8, 'lwip_pbufs': 4, 'lwip_pcbs': 4}
    return {'ram': ram, 'freertos_heap': 15 * 1024, 'lvgl_heap_kb': 48,
            'lwip_heap_kb': 16, 'lwip_pbufs': 16, 'lwip_pcbs': 8}


def fatfs_rtos_mode(proj, opts):
    """解析 FatFS 运行模式：auto / rtos / baremetal。"""
    mode = str(getattr(opts, 'fatfs_mode', 'auto') or 'auto').strip().lower()
    aliases = {'bare': 'baremetal', 'none': 'baremetal', 'freertos': 'rtos',
               'cmsis': 'rtos', 'cmsis-v2': 'rtos'}
    mode = aliases.get(mode, mode)
    if mode not in ('auto', 'rtos', 'baremetal'):
        raise ToolError('FatFS 模式无效: %s（应为 auto、rtos 或 baremetal）' % mode)
    return (project_uses_freertos(proj) or project_uses_rtthread(proj)) if mode == 'auto' else mode == 'rtos'


def find_fatfs_system_file(root, use_rtos):
    """兼容 ST 新旧版系统层命名；裸机且没有系统层时允许按配置裁剪掉。"""
    root = Path(root)
    preferred = ('ffsystem_cmsis_os.c', 'ffsystem.c') if use_rtos else (
        'ffsystem_baremetal.c', 'ffsystem.c')
    for name in preferred:
        path = root / name
        if path.is_file():
            if use_rtos and name == 'ffsystem.c':
                text = read_source_text(path)
                if not re.search(r'\b(?:osMutex|osSemaphore|ff_mutex_)', text):
                    continue
            return path.resolve()
    return None


def find_c_function_definitions(root, function_name, exclude=()):
    """用于冲突检测，只识别带函数体的 C 定义，不把声明和调用当成定义。"""
    root = Path(root)
    excluded = [Path(path).resolve() for path in exclude]
    pattern = re.compile(r'(?ms)^\s*(?:[A-Za-z_]\w*[ \t]+)*' +
                         re.escape(function_name) + r'\s*\([^;{}]*\)\s*\{')
    hits = []
    try:
        files = root.rglob('*.c')
    except OSError:
        files = ()
    for path in files:
        resolved = path.resolve()
        if any(resolved == item or item in resolved.parents for item in excluded):
            continue
        try:
            if pattern.search(read_source_text(path)):
                hits.append(resolved)
        except (OSError, ToolError):
            continue
    return sorted(set(hits))


def patch_fatfs_config(text, use_rtos):
    """同步新旧 FatFS 配置名，并为无 RTC 的通用工程提供可直接链接的默认值。"""
    changed = False
    values = {
        'FF_FS_REENTRANT': '1' if use_rtos else '0',
        'FF_FS_TIMEOUT': '1000',
        'FF_FS_NORTC': '1',
    }
    legacy = {
        '_FS_REENTRANT': values['FF_FS_REENTRANT'],
        '_FS_TIMEOUT': '1000',
        '_FS_NORTC': '1',
    }
    found_reentrant = False
    for name, value in tuple(values.items()) + tuple(legacy.items()):
        pat = re.compile(r'^[ \t]*#define[ \t]+' + re.escape(name) + r'\b[^\r\n]*', re.M)
        m = pat.search(text)
        if not m:
            continue
        if name in ('FF_FS_REENTRANT', '_FS_REENTRANT'):
            found_reentrant = True
        newline = '#define %-20s %s' % (name, value)
        current = m.group(0).split('/*', 1)[0].split('//', 1)[0].strip()
        parts = current.split()
        current_value = parts[2] if len(parts) > 2 else ''
        if current_value != value:
            text = text[:m.start()] + newline + text[m.end():]
            changed = True
    if not found_reentrant:
        line = '#define FF_FS_REENTRANT     %s\n#define FF_FS_TIMEOUT       1000\n' % (
            '1' if use_rtos else '0')
        end = text.rfind('#endif')
        if end < 0:
            end = len(text)
        text = text[:end] + line + text[end:]
        changed = True
    return text, changed


def patch_main_fatfs_init(text):
    """在 CubeMX main.c 中包含 fatfs.h，并在启动调度器前初始化磁盘注册层。"""
    if 'MX_FATFS_Init();' in text:
        return text, False
    inc_end = '/* USER CODE END Includes */'
    init_end = '/* USER CODE END 2 */'
    if inc_end not in text or init_end not in text:
        return text, False
    changed = False
    if '#include "fatfs.h"' not in text:
        text = text.replace(inc_end, '#include "fatfs.h"\n' + inc_end, 1)
        changed = True
    call = '  MX_FATFS_Init();\n'
    marker_pos = text.find(init_end)
    before = text[:marker_pos]
    scheduler = re.search(r'(?m)^[ \t]*(?:osKernelInitialize|MX_FREERTOS_Init|osKernelStart|'
                          r'vTaskStartScheduler)[ \t]*\(', before)
    if scheduler:
        text = text[:scheduler.start()] + call + text[scheduler.start():]
    else:
        text = text.replace(init_end, '\n' + call + init_end, 1)
    return text, True


def fatfs_diskio_templates(fatfs_root):
    """生成与当前 ST ff_gen_drv.h 扇区地址类型匹配的空白磁盘驱动。"""
    drv_h = Path(fatfs_root) / 'ff_gen_drv.h'
    try:
        drv_text = read_source_text(drv_h)
    except OSError:
        drv_text = ''
    lba_type = 'LBA_t' if 'LBA_t' in drv_text else 'DWORD'
    header = '''\
#ifndef USER_DISKIO_H
#define USER_DISKIO_H

#include "ff_gen_drv.h"

extern Diskio_drvTypeDef USER_Driver;

#endif /* USER_DISKIO_H */
'''
    source = '''\
/* FatFS low-level disk I/O skeleton generated by keil_port_tool.py.
 * KPS_USER_ACTION / 用户接入：下面五个 USER_* 函数必须接真实块设备驱动。
 * sector is an LBA, count may exceed one; do NOT treat sector as a byte address.
 * CTRL_SYNC 等待写入完成；扇区总数、扇区大小、擦除块大小不要混用单位。
 * SDIO polling can overrun/underrun when preempted; filesystem locks do not
 * protect FIFO service timing. Use a reviewed DMA/IRQ adapter with timeouts,
 * completion/error handling and aligned DMA-accessible buffers (not F407 CCM).
 * RTOS 抢占可能导致轮询 FIFO 溢出；文件系统互斥锁不能代替 DMA/IRQ 时序设计。
 * Check main/task stack budgets with real filesystem calls, not build success.
 * 裸机主栈与 RTOS 任务栈都需实测；格式化、局部 FIL/缓冲区会增加栈需求。
 * STA_NOINIT / RES_NOTRDY are intentional until a real driver is installed.
 * See docs/POST-PORTING.en.md / docs/POST-PORTING.zh-CN.md (FatFS).
 * Replace the TODO bodies with calls to your SDIO/SPI flash/USB storage driver.
 */
#include "user_diskio.h"

static DSTATUS USER_initialize(BYTE lun)
{
    (void)lun;
    /* TODO: initialize the physical storage and return 0 on success. */
    return STA_NOINIT;
}

static DSTATUS USER_status(BYTE lun)
{
    (void)lun;
    /* TODO: return 0 when the physical storage is ready. */
    return STA_NOINIT;
}

static DRESULT USER_read(BYTE lun, BYTE *buff, %(lba)s sector, UINT count)
{
    (void)lun; (void)buff; (void)sector; (void)count;
    /* TODO: read count sectors into buff. */
    return RES_NOTRDY;
}

static DRESULT USER_write(BYTE lun, const BYTE *buff, %(lba)s sector, UINT count)
{
    (void)lun; (void)buff; (void)sector; (void)count;
    /* TODO: write count sectors from buff. */
    return RES_NOTRDY;
}

static DRESULT USER_ioctl(BYTE lun, BYTE cmd, void *buff)
{
    (void)lun; (void)cmd; (void)buff;
    /* TODO: implement CTRL_SYNC, GET_SECTOR_COUNT/BLOCK_SIZE as required. */
    return RES_PARERR;
}

Diskio_drvTypeDef USER_Driver = {
    USER_initialize,
    USER_status,
    USER_read,
    USER_write,
    USER_ioctl
};
''' % {'lba': lba_type}
    return header, source


def fatfs_app_templates():
    header = '''\
#ifndef FATFS_APP_H
#define FATFS_APP_H

#include "ff.h"

extern FATFS USERFatFS;
extern char USERPath[4];

void MX_FATFS_Init(void);

#endif /* FATFS_APP_H */
'''
    source = '''\
/* FatFS application glue generated by keil_port_tool.py. */
#include "fatfs.h"
#include "ff_gen_drv.h"
#include "user_diskio.h"

FATFS USERFatFS;
char USERPath[4];

void MX_FATFS_Init(void)
{
    /* KPS_USER_ACTION: 这里只注册驱动，不是挂载 / Registers the driver, not a mount.
     * After hardware is ready, f_mount(&USERFatFS, USERPath, 1) and check FRESULT.
     * RTOS 下在线程中挂载；不要因任意挂载错误就格式化 / Never format blindly. */
    (void)FATFS_LinkDriver(&USER_Driver, USERPath);
}
'''
    return header, source


def _planned_text(proj, path):
    key = os.path.normcase(str(Path(path).resolve()))
    planned = getattr(proj, '_planned_generated_files', {})
    if key in planned:
        return planned[key]
    return read_source_text(path)


def fatfs_rtthread_system_template():
    return '''/* FatFS RT-Thread backend generated by keil_port_tool.py. */
#include "ff.h"
#include <rtthread.h>
#if FF_USE_LFN == 3
void *ff_memalloc(UINT size) { return rt_malloc(size); }
void ff_memfree(void *p) { rt_free(p); }
#endif
#if FF_FS_REENTRANT
/* f_mount/unmount must be serialized by the application, as required by FatFs. */
static struct rt_mutex locks[FF_VOLUMES + 1];
static unsigned char ready[FF_VOLUMES + 1];
int ff_mutex_create(int vol)
{
    if (vol < 0 || vol > FF_VOLUMES) return 0;
    if (!ready[vol]) {
        if (rt_mutex_init(&locks[vol], "fatfs", RT_IPC_FLAG_PRIO) != RT_EOK) return 0;
        ready[vol] = 1;
    }
    return 1;
}
void ff_mutex_delete(int vol)
{
    if (vol >= 0 && vol <= FF_VOLUMES && ready[vol]) {
        rt_mutex_detach(&locks[vol]); ready[vol] = 0;
    }
}
int ff_mutex_take(int vol)
{
    if (vol < 0 || vol > FF_VOLUMES || !ready[vol]) return 0;
    /* FF_FS_TIMEOUT is expressed in OS ticks, not milliseconds. */
    return rt_mutex_take(&locks[vol], FF_FS_TIMEOUT) == RT_EOK;
}
void ff_mutex_give(int vol)
{
    if (vol >= 0 && vol <= FF_VOLUMES && ready[vol]) rt_mutex_release(&locks[vol]);
}
#endif
'''


def do_fatfs(proj, opts, rep):
    if getattr(opts, 'fatfs', None):
        requested_root = locate_fatfs_root(opts.fatfs)
        if requested_root is None:
            raise ToolError('无法在 "%s" 下找到 FatFS（需含 ff.c 与 ff.h）' % opts.fatfs)
    else:
        top = ensure_fatfs_sdk(proj, opts, rep)
        if top is None:
            if getattr(opts, 'dry_run', False):
                rep.notes.append('DRY-RUN: 下载完成后将移植 FatFS')
                return
            raise ToolError('未找到 FatFS 源码。可用 --fatfs auto 自动下载，'
                            '或用 --fatfs 指定 ST stm32-mw-fatfs 目录')
        requested_root = locate_fatfs_root(top)
        if requested_root is None:
            raise ToolError('FatFS 目录结构异常: %s' % top)

    requested_root = Path(requested_root).resolve()
    use_rtos = fatfs_rtos_mode(proj, opts)
    use_rtthread = use_rtos and project_uses_rtthread(proj)
    if use_rtos and not (project_uses_freertos(proj) or use_rtthread):
        raise ToolError('FatFS 选择了 FreeRTOS 模式，但工程尚未接入 FreeRTOS。'
                        '请同时启用 FreeRTOS 任务，或把 FatFS 模式改为“裸机”。')
    root, project_fatfs = plan_project_library_copy(
        proj, requested_root, 'FatFS', locate_fatfs_root, rep)
    root = Path(root).resolve()
    system_file = None if use_rtthread else find_fatfs_system_file(root, use_rtos)
    if use_rtthread and not re.search(r'\bff_mutex_create\s*\(', read_source_text(root / 'ff.h')):
        raise ToolError('RT-Thread FatFS 后端当前支持 ff_mutex_* 接口版本；旧 ff_cre_syncobj 版本需另行适配')
    if use_rtos and not use_rtthread and system_file is None:
        raise ToolError('当前 FatFS 源码没有可用的 FreeRTOS 系统层；支持 '
                        'ffsystem_cmsis_os.c，或包含 CMSIS-OS/ff_mutex 实现的 ffsystem.c')
    system_name = system_file.name if system_file else '由 ffconf.h 裁剪（无需系统层）'
    for other_system in ('ffsystem_baremetal.c', 'ffsystem_cmsis_os.c', 'ffsystem.c'):
        if system_file is not None and other_system == system_file.name:
            continue
        other_target = project_fatfs / other_system
        if proj.remove_file(other_target):
            rep.notes.append('已从工程中移除不适用的系统层: %s' % other_system)

    selected = getattr(opts, 'fatfs_files', None)
    selected_rel = None
    if selected is not None:
        selected_rel = set()
        for value in selected:
            value = Path(value).resolve()
            for selection_root in (requested_root, root):
                try:
                    selected_rel.add(os.path.normcase(str(value.relative_to(selection_root))))
                    break
                except ValueError:
                    pass

    def chosen(path):
        if selected_rel is None:
            return True
        return os.path.normcase(str(Path(path).relative_to(root))) in selected_rel

    required = ('ff.c', 'diskio.c', 'ff_gen_drv.c')
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise ToolError('FatFS 源码缺少 ST 驱动适配文件: %s' % ', '.join(missing))
    not_selected = [name for name in required if not chosen(root / name)]
    if not_selected:
        raise ToolError('FatFS 核心文件不能取消: %s' % ', '.join(not_selected))
    if system_file is not None and not chosen(system_file):
        raise ToolError('%s 模式必须选择 %s' % ('FreeRTOS' if use_rtos else '裸机', system_name))

    source_files = [root / name for name in required]
    if system_file is not None:
        source_files.append(system_file)
    unicode_file = root / 'ffunicode.c'
    if unicode_file.is_file() and chosen(unicode_file):
        source_files.append(unicode_file)
    for source in source_files:
        target = project_fatfs / source.relative_to(root)
        group = 'FatFS/System' if source.name.startswith('ffsystem') else 'FatFS/Middleware'
        if source.name == 'ffunicode.c':
            group = 'FatFS/Optional'
        if proj.add_file(group, source.name, 1, rel_or_abs(target, proj.dir)):
            rep.files.append((group, source.name))

    project_root = project_content_root(proj)
    app_dir = project_root / 'FatFs' / 'App'
    target_dir = project_root / 'FatFs' / 'Target'
    if use_rtthread:
        system_path = target_dir / 'ffsystem_rtthread.c'
        _plan_owned_template(system_path, fatfs_rtthread_system_template(),
                             'generated by keil_port_tool.py', 'FatFS 原生 RT-Thread 系统层', rep)
        if proj.add_file('FatFS/System', system_path.name, 1, rel_or_abs(system_path, proj.dir)):
            rep.files.append(('FatFS/System', system_path.name))
    config_candidates = (
        project_root / 'FatFs' / 'Config' / 'ffconf.h',
        project_root / 'FatFs' / 'Target' / 'ffconf.h',
        project_root / 'FatFs' / 'App' / 'ffconf.h',
        project_root / 'ffconf.h',
        proj.dir / 'ffconf.h',
    )
    config_file = next((p for p in config_candidates if p.is_file()), None)
    if config_file is None:
        try:
            discovered = sorted(p for p in project_root.rglob('ffconf.h')
                                if project_fatfs not in p.resolve().parents)
        except OSError:
            discovered = []
        config_file = discovered[0] if discovered else config_candidates[0]
    config_dir = config_file.parent
    if config_file.is_file():
        config_text = read_source_text(config_file)
        config_text, changed = patch_fatfs_config(config_text, use_rtos)
        if changed:
            rep.gen_files.append((config_file, config_text,
                                  '同步 FatFS 裸机/FreeRTOS 可重入配置'))
        rep.notes.append('保留并适配工程已有 ffconf.h: %s' % config_file)
    else:
        template = root / 'ffconf_template.h'
        if not template.is_file():
            raise ToolError('未找到 ffconf_template.h，无法生成与当前 ff.c 匹配的配置')
        config_text = read_source_text(template)
        config_text, _changed = patch_fatfs_config(config_text, use_rtos)
        rep.gen_files.append((config_file, config_text,
                              '由官方模板生成 ffconf.h（%s模式）' %
                              ('FreeRTOS/CMSIS-V2' if use_rtos else '裸机')))

    proj.add_include_path(rel_or_abs(project_fatfs, proj.dir), rep)
    proj.add_include_path(rel_or_abs(config_dir, proj.dir), rep)

    if getattr(opts, 'fatfs_app', True):
        existing_init = find_c_function_definitions(
            project_root, 'MX_FATFS_Init', exclude=(project_fatfs, _state_dir(proj)))
        if len(existing_init) > 1:
            raise ToolError('检测到多个 MX_FATFS_Init() 定义，继续生成会产生重复符号：%s' %
                            ', '.join(str(path) for path in existing_init))
        existing_drivers = []
        for record in proj.file_records():
            name = record.get('name', '').lower()
            if name != 'diskio.c' and name.endswith('diskio.c'):
                existing_drivers.append(record)
        app_h, app_c = fatfs_app_templates()
        disk_h, disk_c = fatfs_diskio_templates(root)
        generated = []
        if existing_init:
            rep.notes.append('检测到 CubeMX/现有 MX_FATFS_Init()，复用而不再生成 FatFs/App/fatfs.c: %s' %
                             existing_init[0])
            header = next((path for path in (
                existing_init[0].with_suffix('.h'),
                existing_init[0].parent / 'fatfs.h',
                existing_init[0].parent.parent / 'Inc' / 'fatfs.h') if path.is_file()), None)
            if header is not None:
                proj.add_include_path(rel_or_abs(header.parent, proj.dir), rep)
        else:
            generated.extend(((app_dir / 'fatfs.h', app_h, 'FatFS 应用层头文件'),
                              (app_dir / 'fatfs.c', app_c, 'FatFS 应用层与逻辑盘注册')))
        if existing_drivers:
            rep.notes.append('检测到工程已有 FatFS 磁盘驱动，跳过 user_diskio.c 模板: %s' %
                             ', '.join(item['name'] for item in existing_drivers))
        else:
            generated.extend(((target_dir / 'user_diskio.h', disk_h, 'FatFS 底层磁盘驱动头文件'),
                              (target_dir / 'user_diskio.c', disk_c, 'FatFS 底层磁盘驱动框架')))
        for path, content, desc in generated:
            if not path.is_file():
                rep.gen_files.append((path, content, desc))
        if not existing_init and proj.add_file('FatFS/Application', 'fatfs.c', 1,
                         rel_or_abs(app_dir / 'fatfs.c', proj.dir)):
            rep.files.append(('FatFS/Application', 'fatfs.c'))
        if not existing_drivers and proj.add_file('FatFS/Target', 'user_diskio.c', 1,
                         rel_or_abs(target_dir / 'user_diskio.c', proj.dir)):
            rep.files.append(('FatFS/Target', 'user_diskio.c'))
        if not existing_init:
            proj.add_include_path(rel_or_abs(app_dir, proj.dir), rep)
        if not existing_drivers:
            proj.add_include_path(rel_or_abs(target_dir, proj.dir), rep)

        main_candidates = ([project_root / 'RTThread/App/rtthread_app.c'] if use_rtthread else
                           [project_root / 'Core' / 'Src' / 'main.c', proj.dir / 'main.c'])
        main_c = next((p for p in main_candidates if p.is_file() or
                      os.path.normcase(str(p.resolve())) in getattr(proj, '_planned_generated_files', {})), None)
        if main_c:
            main_text = _planned_text(proj, main_c)
            new_main, changed = (_patch_component_init(main_text, 'fatfs.h', 'MX_FATFS_Init();', True)
                                 if use_rtthread else patch_main_fatfs_init(main_text))
            if changed:
                rep.gen_files.append((main_c, new_main,
                                      '在 main() 中初始化 FatFS 逻辑盘'))
            elif 'MX_FATFS_Init();' not in main_text:
                rep.warnings.append('main.c 没有标准 CubeMX USER CODE 标记，'
                                    '请手动调用 MX_FATFS_Init()')
        else:
            rep.warnings.append('未找到 main.c，请手动调用 MX_FATFS_Init()')

    mode_text = 'FreeRTOS + CMSIS-RTOS2（线程安全）' if use_rtos else '裸机'
    rep.notes.append('FatFS 运行模式: %s；系统层: %s' % (mode_text, system_name))
    rep.notes.append('ffconf.h: %s' % config_file)
    rep.notes.append('必须在 user_diskio.c 中接入你的 SDIO/SPI Flash/USB 存储读写函数')
    rep.notes.append('底层驱动就绪后调用 f_mount(&USERFatFS, USERPath, 1) 挂载文件系统')


# ===========================================================================
# 任务6: SEGGER RTT / LittleFS / CMSIS-DSP / FreeRTOS 外设锁
# ===========================================================================
def locate_segger_rtt_root(d):
    if not d:
        return None
    d = Path(d).expanduser()
    for root in (d, d / 'SEGGER_RTT', d / 'RTT'):
        if (root / 'RTT' / 'SEGGER_RTT.c').is_file():
            return root
        if root.name.lower() == 'rtt' and (root / 'SEGGER_RTT.c').is_file():
            return root.parent
    return None


def locate_littlefs_root(d):
    if not d:
        return None
    d = Path(d).expanduser()
    for root in (d, d / 'littlefs', d / 'src'):
        if (root / 'lfs.c').is_file() and (root / 'lfs.h').is_file():
            return root
    return None


def locate_cmsis_dsp_root(d):
    if not d:
        return None
    d = Path(d).expanduser()
    candidates = (d, d / 'CMSIS' / 'DSP', d / 'CMSIS-DSP', d / 'DSP')
    for root in candidates:
        if ((root / 'Source').is_dir() and
                (root / 'Include' / 'arm_math.h').is_file()):
            return root
    return None


def _selected_relative(selected, roots):
    if selected is None:
        return None
    result = set()
    for value in selected:
        path = Path(value).resolve()
        for root in roots:
            try:
                result.add(str(path.relative_to(Path(root).resolve())).replace('\\', '/').lower())
                break
            except ValueError:
                continue
    return result


def _component_chosen(path, base, selected_rel):
    if selected_rel is None:
        return True
    key = str(Path(path).resolve().relative_to(Path(base).resolve())).replace('\\', '/').lower()
    return key in selected_rel


def _project_define_tokens(proj):
    tokens = set()
    for cads in proj._cads_list():
        text = cads.findtext('VariousControls/Define', '') or ''
        tokens.update(split_keil_defines(text))
    return tokens


def _owned_component_defines(proj, component):
    try:
        return set(installed_components(proj).get(component, {}).get('defines', []))
    except Exception:
        return set()


def _plan_owned_template(path, content, marker, description, rep, refresh_owned=False):
    """创建模板；仅在明确要求时刷新仍带工具标记的文件。"""
    path = Path(path)
    if not path.is_file():
        rep.gen_files.append((path, content, description))
        return True
    existing = read_source_text(path)
    if (refresh_owned and marker and marker.lower() in existing.lower() and
            existing.replace('\r\n', '\n') != str(content).replace('\r\n', '\n')):
        rep.gen_files.append((path, content, description))
        rep.notes.append('已按当前组件选择刷新工具生成文件（原文件会自动备份）: %s' % path)
        return True
    rep.notes.append('已有文件保持不变: %s' % path)
    return False


def _defined_values(text):
    """返回简单 C 预处理宏的 {name: value} 映射。"""
    values = {}
    for match in re.finditer(
            r'(?m)^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+([^\r\n]+)',
            str(text)):
        values[match.group(1)] = match.group(2).strip()
    return values


def _plan_managed_config(path, desired, managed_names, description, rep):
    """只同步工具负责的配置宏，保留用户在同一文件中的其他修改。"""
    path = Path(path)
    if not path.is_file():
        rep.gen_files.append((path, desired, description))
        return True
    current = read_source_text(path)
    wanted = _defined_values(desired)
    updated = current
    changed_names = []
    for name in managed_names:
        pattern = re.compile(
            r'(?m)^[ \t]*#[ \t]*define[ \t]+' + re.escape(name) +
            r'(?:[ \t]+[^\r\n]*)?[ \t]*(?:\r?\n|$)')
        match = pattern.search(updated)
        if name in wanted:
            replacement = '#define %-31s %s\n' % (name, wanted[name])
            if match:
                if match.group(0).replace('\r\n', '\n') != replacement:
                    updated = updated[:match.start()] + replacement + updated[match.end():]
                    changed_names.append(name)
            else:
                end = list(re.finditer(r'(?m)^[ \t]*#[ \t]*endif\b[^\r\n]*', updated))
                pos = end[-1].start() if end else len(updated)
                prefix = '' if pos == 0 or updated[:pos].endswith(('\n', '\r')) else '\n'
                updated = updated[:pos] + prefix + replacement + updated[pos:]
                changed_names.append(name)
        elif match:
            updated = updated[:match.start()] + updated[match.end():]
            changed_names.append(name)
    if updated != current:
        rep.gen_files.append((path, updated, description))
        rep.notes.append('已同步工具管理的配置项: %s (%s)' %
                         (path, ', '.join(changed_names)))
        return True
    rep.notes.append('已有配置与当前选择一致: %s' % path)
    return False


def _plan_lwip_errno_upgrade(path, description, rep):
    """升级旧版工具生成的 LwIP 端口，避免 ARMCC5 缺少完整 POSIX errno。"""
    path = Path(path)
    if not path.is_file():
        return False
    current = read_source_text(path)
    if 'generated by keil_port_tool.py' not in current.lower():
        rep.warnings.append('未确认文件归属，保留用户 LwIP 文件，不自动升级: %s' % path)
        return False
    if re.search(r'\b(?:extern\s+)?int\s+lwip_errno\s*;', _c_code(current)):
        return False
    updated = current
    if path.name.lower() == 'cc.h' and 'LWIP_ARCH_CC_H' in current:
        marker = '#define BYTE_ORDER LITTLE_ENDIAN'
        block = marker + '\nextern int lwip_errno;\n#ifndef errno\n#define errno lwip_errno\n#endif'
        updated = current.replace(marker, block, 1)
    elif (path.name.lower() == 'lwip_port.c' and
          'generated by keil_port_tool.py' in current.lower()):
        marker = '#include "lwip/dhcp.h"'
        updated = current.replace(marker, marker + '\n\nint lwip_errno;', 1)
    if updated != current:
        rep.gen_files.append((path, updated, description))
        rep.notes.append('已升级旧版 LwIP errno 适配: %s' % path)
        return True
    return False


def do_segger_rtt(proj, opts, rep):
    requested = locate_segger_rtt_root(getattr(opts, 'segger_rtt', None))
    if requested is None and getattr(opts, 'segger_rtt', None):
        raise ToolError('所选目录中未找到 RTT/SEGGER_RTT.c')
    if requested is None:
        top = ensure_segger_rtt_sdk(proj, opts, rep)
        requested = locate_segger_rtt_root(top) if top else None
    if requested is None:
        if getattr(opts, 'dry_run', False):
            return
        raise ToolError('未找到 SEGGER RTT 源码；可用 --rtt auto 自动下载')
    requested = Path(requested).resolve()
    root, project_root = plan_project_library_copy(
        proj, requested, 'SEGGER_RTT', locate_segger_rtt_root, rep)
    root, project_root = Path(root).resolve(), Path(project_root).resolve()
    selected_rel = _selected_relative(getattr(opts, 'rtt_files', None), (requested, root))

    core_file = root / 'RTT' / 'SEGGER_RTT.c'
    if not core_file.is_file() or not _component_chosen(core_file, root, selected_rel):
        raise ToolError('SEGGER_RTT.c 是 RTT 必需文件，不能取消')
    optional = [root / 'RTT' / 'SEGGER_RTT_printf.c']
    syscalls_file = root / 'Syscalls' / 'SEGGER_RTT_Syscalls_KEIL.c'
    syscalls_supported = all(proj.is_ac6(target) for target in proj.targets)
    if not getattr(opts, 'rtt_no_syscalls', False) and syscalls_supported:
        optional.append(syscalls_file)
    asm_file = root / 'RTT' / 'SEGGER_RTT_ASM_ARMv7M.S'
    selected_cores = {proj.target_core_info(target)[0] for target in proj.targets}
    # 官方这个 .S 使用 GNU/Clang 预处理汇编语法。ARMCC5 的 armasm 会直接
    # 报 A1167E，只有全部 Target 都是 ARMClang 且为 ARMv7M/ARMv8M 时才启用。
    asm_supported = (bool(selected_cores) and None not in selected_cores and
                     all(proj.is_ac6(target) for target in proj.targets) and not
                     (selected_cores & {'Cortex-M0', 'Cortex-M0+'}))
    if not getattr(opts, 'rtt_no_asm', False) and asm_supported:
        optional.append(asm_file)

    sources = [core_file]
    if not getattr(opts, 'rtt_no_printf', False):
        sources.extend(optional[:1])
    sources.extend(optional[1:])
    sources = [p for p in sources if p.is_file() and _component_chosen(p, root, selected_rel)]
    removed = 0
    for candidate in (root / 'RTT' / 'SEGGER_RTT_printf.c', syscalls_file, asm_file):
        if candidate not in sources:
            target = project_root / candidate.relative_to(root)
            removed += proj.remove_file(rel_or_abs(target, proj.dir))
    if removed:
        rep.notes.append('已从工程移除 %d 个当前未启用/不兼容的 RTT 文件' % removed)
    asm_added = asm_file in sources
    proj.set_define('RTT_USE_ASM=%d' % (1 if asm_added else 0), rep)
    for source in sources:
        target = project_root / source.relative_to(root)
        group = 'SEGGER RTT/Syscalls' if 'Syscalls' in source.parts else 'SEGGER RTT/Core'
        ftype = PROJECT_FILE_TYPES.get(source.suffix.lower(), 1)
        if proj.add_file(group, source.name, ftype, rel_or_abs(target, proj.dir)):
            rep.files.append((group, source.name))
    proj.add_include_path(rel_or_abs(project_root / 'RTT', proj.dir), rep)

    config_source = root / 'Config' / 'SEGGER_RTT_Conf.h'
    config_dir = project_content_root(proj) / 'Config' / 'SEGGER_RTT'
    config_target = config_dir / 'SEGGER_RTT_Conf.h'
    if config_source.is_file():
        _plan_owned_template(config_target, read_source_text(config_source),
                             'SEGGER RTT', 'SEGGER RTT 工程级配置文件', rep)
        proj.add_include_path(rel_or_abs(config_dir, proj.dir), rep)
    elif (root / 'Config').is_dir():
        proj.add_include_path(rel_or_abs(project_root / 'Config', proj.dir), rep)
    else:
        rep.warnings.append('未找到 SEGGER_RTT_Conf.h，将使用 SEGGER_RTT_ConfDefaults.h 默认配置')
    if not asm_supported and asm_file.is_file():
        compiler = 'ARMCC5' if not all(proj.is_ac6(t) for t in proj.targets) else '当前编译器/内核'
        rep.notes.append('%s 不兼容 SEGGER 的 GNU/Clang .S 文件，已使用纯 C 实现' % compiler)
    if any(p.name == 'SEGGER_RTT_Syscalls_KEIL.c' for p in sources):
        rep.warnings.append('已启用 Keil printf 重定向；若工程已有 fputc/_write 重定向，请二选一以免重复定义')
    elif not getattr(opts, 'rtt_no_syscalls', False) and not syscalls_supported:
        rep.notes.append('ARMCC5 下 RTT Syscalls 容易与标准库 sys_io.o 重复定义，已自动停用；'
                         '仍可直接使用 SEGGER_RTT_printf()')
    rep.notes.append('日志调用示例: SEGGER_RTT_printf(0, "value=%d\\n", value)')


def project_uses_cmsis_os2(proj):
    for value in proj.files_in_project():
        low = str(value).replace('\\', '/').lower()
        if 'cmsis_os2.c' in low or 'cmsis-rtos_v2' in low or 'cmsis_rtos_v2' in low:
            return True
    for record in proj.file_records():
        if record['name'].lower() == 'freertos_app.c':
            raw = Path(record['path'].replace('\\', os.sep))
            path = raw if raw.is_absolute() else proj.dir / raw
            if path.is_file() and 'cmsis_os2.h' in _planned_text(proj, path):
                return True
    return False


def littlefs_rtos_mode(proj, opts):
    mode = str(getattr(opts, 'littlefs_mode', 'auto') or 'auto').lower()
    mode = {'bare': 'baremetal', 'none': 'baremetal', 'freertos': 'rtos'}.get(mode, mode)
    if mode not in ('auto', 'rtos', 'baremetal'):
        raise ToolError('LittleFS 模式无效: %s' % mode)
    return (project_uses_freertos(proj) or project_uses_rtthread(proj)) if mode == 'auto' else mode == 'rtos'


def littlefs_port_templates(use_rtos, use_cmsis2, use_rtthread=False):
    header = '''\
#ifndef LITTLEFS_PORT_H
#define LITTLEFS_PORT_H

#include "lfs.h"

extern lfs_t g_littlefs;
extern const struct lfs_config g_littlefs_config;

int LittleFS_Init(void);
int LittleFS_Format(void);

/* Implement these weak hooks for the actual NOR/NAND Flash. */
int LittleFS_BD_Read(const struct lfs_config *cfg, lfs_block_t block,
                     lfs_off_t off, void *buffer, lfs_size_t size);
int LittleFS_BD_Prog(const struct lfs_config *cfg, lfs_block_t block,
                     lfs_off_t off, const void *buffer, lfs_size_t size);
int LittleFS_BD_Erase(const struct lfs_config *cfg, lfs_block_t block);
int LittleFS_BD_Sync(const struct lfs_config *cfg);

#endif /* LITTLEFS_PORT_H */
'''
    if use_rtos and use_rtthread:
        rtos_include = '#include "rtthread.h"\n'
        rtos_state = 'static rt_mutex_t s_littlefs_mutex;\n'
        rtos_init = '''
    if (s_littlefs_mutex == RT_NULL) {
        s_littlefs_mutex = rt_mutex_create("lfs", RT_IPC_FLAG_PRIO);
        if (s_littlefs_mutex == RT_NULL) return LFS_ERR_NOMEM;
    }
'''
        lock_code = '''
static int LittleFS_Lock(const struct lfs_config *cfg)
{
    (void)cfg;
    return rt_mutex_take(s_littlefs_mutex, RT_WAITING_FOREVER) == RT_EOK ? 0 : LFS_ERR_IO;
}
static int LittleFS_Unlock(const struct lfs_config *cfg)
{
    (void)cfg;
    return rt_mutex_release(s_littlefs_mutex) == RT_EOK ? 0 : LFS_ERR_IO;
}
'''
    elif use_rtos and use_cmsis2:
        rtos_include = '#include "cmsis_os2.h"\n'
        rtos_state = 'static osMutexId_t s_littlefs_mutex;\n'
        rtos_init = '''\
    if (s_littlefs_mutex == NULL) {
        s_littlefs_mutex = osMutexNew(NULL);
        if (s_littlefs_mutex == NULL) return LFS_ERR_NOMEM;
    }
'''
        lock_code = '''\
static int LittleFS_Lock(const struct lfs_config *cfg)
{
    (void)cfg;
    return osMutexAcquire(s_littlefs_mutex, osWaitForever) == osOK ? 0 : LFS_ERR_IO;
}

static int LittleFS_Unlock(const struct lfs_config *cfg)
{
    (void)cfg;
    return osMutexRelease(s_littlefs_mutex) == osOK ? 0 : LFS_ERR_IO;
}
'''
    elif use_rtos:
        rtos_include = '#include "FreeRTOS.h"\n#include "semphr.h"\n'
        rtos_state = 'static SemaphoreHandle_t s_littlefs_mutex;\n'
        rtos_init = '''\
    if (s_littlefs_mutex == NULL) {
        s_littlefs_mutex = xSemaphoreCreateMutex();
        if (s_littlefs_mutex == NULL) return LFS_ERR_NOMEM;
    }
'''
        lock_code = '''\
static int LittleFS_Lock(const struct lfs_config *cfg)
{
    (void)cfg;
    return xSemaphoreTake(s_littlefs_mutex, portMAX_DELAY) == pdTRUE ? 0 : LFS_ERR_IO;
}

static int LittleFS_Unlock(const struct lfs_config *cfg)
{
    (void)cfg;
    return xSemaphoreGive(s_littlefs_mutex) == pdTRUE ? 0 : LFS_ERR_IO;
}
'''
    else:
        rtos_include = rtos_state = rtos_init = lock_code = ''
    lock_fields = '    .lock = LittleFS_Lock,\n    .unlock = LittleFS_Unlock,\n' if use_rtos else ''
    source = '''\
/* LittleFS port generated by keil_port_tool.py.
 * KPS_USER_ACTION / 用户接入：修改下面的几何参数，覆盖 LittleFS_BD_* 弱函数。
 * 4096 x 128 is an EXAMPLE partition, not detected Flash geometry.
 * Address = partition_start + block * block_size + off; enforce partition bounds.
 * 检查页写边界、擦除对齐、忙超时；Sync 必须等待完成，不能假报成功。
 * Format only with permission to erase; 格式化后需再次挂载，不能覆盖程序区。
 * See docs/POST-PORTING.en.md / docs/POST-PORTING.zh-CN.md (LittleFS). */
#include "littlefs_port.h"
#include <stdint.h>
%(rtos_include)s
#if defined(__CC_ARM)
#define LFS_PORT_WEAK __weak
#elif defined(__GNUC__) || defined(__clang__)
#define LFS_PORT_WEAK __attribute__((weak))
#else
#define LFS_PORT_WEAK
#endif

#define LFS_READ_SIZE       16U
#define LFS_PROG_SIZE       16U
#define LFS_BLOCK_SIZE      4096U
#define LFS_BLOCK_COUNT     128U
#define LFS_CACHE_SIZE      64U
#define LFS_LOOKAHEAD_SIZE  16U

static uint8_t s_read_buffer[LFS_CACHE_SIZE];
static uint8_t s_prog_buffer[LFS_CACHE_SIZE];
static uint8_t s_lookahead_buffer[LFS_LOOKAHEAD_SIZE];
%(rtos_state)s
%(lock_code)s
lfs_t g_littlefs;
const struct lfs_config g_littlefs_config = {
    .read = LittleFS_BD_Read,
    .prog = LittleFS_BD_Prog,
    .erase = LittleFS_BD_Erase,
    .sync = LittleFS_BD_Sync,
%(lock_fields)s    .read_size = LFS_READ_SIZE,
    .prog_size = LFS_PROG_SIZE,
    .block_size = LFS_BLOCK_SIZE,
    .block_count = LFS_BLOCK_COUNT,
    .cache_size = LFS_CACHE_SIZE,
    .lookahead_size = LFS_LOOKAHEAD_SIZE,
    .block_cycles = 500,
    .read_buffer = s_read_buffer,
    .prog_buffer = s_prog_buffer,
    .lookahead_buffer = s_lookahead_buffer,
};

int LittleFS_Init(void)
{
%(rtos_init)s    return lfs_mount(&g_littlefs, &g_littlefs_config);
}

int LittleFS_Format(void)
{
%(rtos_init)s    return lfs_format(&g_littlefs, &g_littlefs_config);
}

LFS_PORT_WEAK int LittleFS_BD_Read(const struct lfs_config *cfg, lfs_block_t block,
                                   lfs_off_t off, void *buffer, lfs_size_t size)
{
    (void)cfg; (void)block; (void)off; (void)buffer; (void)size;
    return LFS_ERR_IO;
}

LFS_PORT_WEAK int LittleFS_BD_Prog(const struct lfs_config *cfg, lfs_block_t block,
                                   lfs_off_t off, const void *buffer, lfs_size_t size)
{
    (void)cfg; (void)block; (void)off; (void)buffer; (void)size;
    return LFS_ERR_IO;
}

LFS_PORT_WEAK int LittleFS_BD_Erase(const struct lfs_config *cfg, lfs_block_t block)
{
    (void)cfg; (void)block;
    return LFS_ERR_IO;
}

LFS_PORT_WEAK int LittleFS_BD_Sync(const struct lfs_config *cfg)
{
    (void)cfg;
    return 0;
}
''' % {'rtos_include': rtos_include, 'rtos_state': rtos_state,
       'rtos_init': rtos_init, 'lock_code': lock_code, 'lock_fields': lock_fields}
    return header, source


def littlefs_diagnostics_templates():
    """Keep LittleFS diagnostics independent of host stdio and board hardware."""
    header = '''\
/* LittleFS diagnostics generated by keil_port_tool.py. */
#ifndef LITTLEFS_PORT_CONFIG_H
#define LITTLEFS_PORT_CONFIG_H

/* Default LittleFS logging uses host stdio. Override these macros here to
 * connect an initialized UART/RTT backend; no board peripheral is assumed. */
#ifndef LFS_NO_DEBUG
#define LFS_NO_DEBUG
#endif
#ifndef LFS_NO_WARN
#define LFS_NO_WARN
#endif
#ifndef LFS_NO_ERROR
#define LFS_NO_ERROR
#endif
#ifndef LFS_TRACE
#define LFS_TRACE(...) ((void)0)
#endif
#ifndef LFS_DEBUG
#define LFS_DEBUG(...) ((void)0)
#endif
#ifndef LFS_WARN
#define LFS_WARN(...) ((void)0)
#endif
#ifndef LFS_ERROR
#define LFS_ERROR(...) ((void)0)
#endif

#ifdef __cplusplus
extern "C" {
#endif
extern const char * volatile g_littlefs_assert_expression;
extern const char * volatile g_littlefs_assert_file;
extern volatile unsigned int g_littlefs_assert_line;
/* Optional strong override: report/reset/stop without calling host stdio.
 * The default records the fault for a debugger and never returns. */
void LittleFS_AssertFailed(const char *expression, const char *file,
                         unsigned int line);
#ifdef __cplusplus
}
#endif

/* Preserve runtime assertions even when the C library has NDEBUG enabled.
 * If a user hook unexpectedly returns, do not continue with corrupt state. */
#ifndef LFS_ASSERT
#define LFS_ASSERT(condition) do { \\
    if (!(condition)) { \\
        LittleFS_AssertFailed(#condition, __FILE__, __LINE__); \\
        for (;;) { } \\
    } \\
} while (0)
#endif

#endif /* LITTLEFS_PORT_CONFIG_H */
'''
    source = '''\
/* LittleFS diagnostics generated by keil_port_tool.py. */
#include "littlefs_port_config.h"

#if defined(__CC_ARM)
#define LFS_DIAGNOSTIC_WEAK __weak
#elif defined(__GNUC__) || defined(__clang__)
#define LFS_DIAGNOSTIC_WEAK __attribute__((weak))
#else
#define LFS_DIAGNOSTIC_WEAK
#endif

const char * volatile g_littlefs_assert_expression;
const char * volatile g_littlefs_assert_file;
volatile unsigned int g_littlefs_assert_line;

LFS_DIAGNOSTIC_WEAK void LittleFS_AssertFailed(const char *expression,
                                             const char *file,
                                             unsigned int line)
{
    g_littlefs_assert_expression = expression;
    g_littlefs_assert_file = file;
    g_littlefs_assert_line = line;
    for (;;) { }
}
'''
    return header, source


def _plan_littlefs_diagnostics(proj, sdk_root, rep):
    """Install component-owned config; never replace a user's LFS config."""
    macro = 'LFS_DEFINES=littlefs_port_config.h'
    owned = installed_components(proj).get('littlefs', {})
    owned_defines = set(owned.get('defines', []))
    owned_targets = set(owned.get('targets', []))
    custom = set()
    for target in proj.targets:
        # Include group/file overrides: adding a project define must not
        # silently fight a source-specific LFS configuration.
        for controls in target.findall('.//Cads/VariousControls'):
            for token in split_keil_defines(controls.findtext('Define', '')):
                name = token.split('=', 1)[0].strip()
                if name in ('LFS_CONFIG', 'LFS_DEFINES'):
                    if (token != macro or token not in owned_defines or
                            target.findtext('TargetName', '') not in owned_targets):
                        custom.add(token)
            misc = controls.findtext('MiscControls', '') or ''
            if re.search(r'LFS_(?:CONFIG|DEFINES)\b', misc):
                custom.add('MiscControls: ' + misc)
    if custom:
        rep.warnings.append(
            '保留用户 LittleFS 配置，不注入默认日志/断言配置: %s。'
            '请确认其日志和断言不依赖 semihosting；混合 Target 可分别选择后移植。' %
            '; '.join(sorted(custom)))
        return
    util = Path(sdk_root) / 'lfs_util.h'
    if not util.is_file() or 'LFS_DEFINES' not in read_source_text(util):
        rep.warnings.append(
            '所选 LittleFS 未提供 LFS_DEFINES 配置入口，未修改 SDK。'
            '请自行重定向日志/断言；默认 C 库 stdio 可能在 main 前触发 semihosting。')
        return
    config_dir = project_content_root(proj) / 'Config' / 'LittleFS'
    header_path = config_dir / 'littlefs_port_config.h'
    source_path = config_dir / 'littlefs_port_diagnostics.c'
    owned_paths = {item.get('path') for item in owned.get('generated_files', [])}
    for path in (header_path, source_path):
        if path.exists() and _relative_project_path(proj, path) not in owned_paths:
            rep.warnings.append(
                '保留未归属本工具的 LittleFS 配置文件，不注入 LFS_DEFINES: %s。'
                '请检查默认日志/断言的 semihosting 依赖。' % path)
            return
    header, source = littlefs_diagnostics_templates()
    for path, content, description in (
            (header_path, header, 'LittleFS 工程级无半主机日志/断言配置'),
            (source_path, source, 'LittleFS 可覆盖断言停机钩子')):
        _plan_owned_template(path, content, 'LittleFS diagnostics generated',
                             description, rep)
    proj.add_define(macro, rep)
    proj.add_include_path(rel_or_abs(config_dir, proj.dir), rep)
    if proj.add_file('LittleFS/Port', source_path.name, 1,
                     rel_or_abs(source_path, proj.dir)):
        rep.files.append(('LittleFS/Port', source_path.name))
    rep.notes.append(
        'LittleFS 默认日志不调用 stdio；断言保留，失败记录 g_littlefs_assert_* 后停机。'
        '可编辑 littlefs_port_config.h 接入日志，或强定义 LittleFS_AssertFailed() 接入板级故障处理。')


def do_littlefs(proj, opts, rep):
    requested = locate_littlefs_root(getattr(opts, 'littlefs', None))
    if requested is None and getattr(opts, 'littlefs', None):
        raise ToolError('所选目录中未找到 lfs.c/lfs.h')
    if requested is None:
        top = ensure_littlefs_sdk(proj, opts, rep)
        requested = locate_littlefs_root(top) if top else None
    if requested is None:
        if getattr(opts, 'dry_run', False):
            return
        raise ToolError('未找到 LittleFS 源码；可用 --littlefs auto 自动下载')
    requested = Path(requested).resolve()
    root, project_root = plan_project_library_copy(
        proj, requested, 'LittleFS', locate_littlefs_root, rep)
    root, project_root = Path(root).resolve(), Path(project_root).resolve()
    selected_rel = _selected_relative(getattr(opts, 'littlefs_files', None), (requested, root))
    required = [root / 'lfs.c', root / 'lfs_util.c']
    missing = [p.name for p in required if not p.is_file()]
    if missing:
        raise ToolError('LittleFS 缺少核心文件: %s' % ', '.join(missing))
    deselected = [p.name for p in required if not _component_chosen(p, root, selected_rel)]
    if deselected:
        raise ToolError('LittleFS 核心文件不能取消: %s' % ', '.join(deselected))
    for source in required:
        target = project_root / source.relative_to(root)
        if proj.add_file('LittleFS/Core', source.name, 1, rel_or_abs(target, proj.dir)):
            rep.files.append(('LittleFS/Core', source.name))
    proj.add_include_path(rel_or_abs(project_root, proj.dir), rep)
    proj.enable_c99_gnu(rep)

    _plan_littlefs_diagnostics(proj, root, rep)

    use_rtos = littlefs_rtos_mode(proj, opts)
    if use_rtos and not (project_uses_freertos(proj) or project_uses_rtthread(proj)):
        raise ToolError('LittleFS 线程安全模式需要先接入 FreeRTOS 或 RT-Thread')
    if use_rtos:
        proj.add_define('LFS_THREADSAFE', rep)
    elif 'LFS_THREADSAFE' in _project_define_tokens(proj):
        if 'LFS_THREADSAFE' in _owned_component_defines(proj, 'littlefs'):
            proj.remove_define_name('LFS_THREADSAFE', rep)
        else:
            raise ToolError('工程已有用户定义 LFS_THREADSAFE，与 LittleFS 裸机模式冲突')
    if getattr(opts, 'littlefs_port', True):
        use_cmsis2 = use_rtos and project_uses_cmsis_os2(proj)
        header, source = littlefs_port_templates(use_rtos, use_cmsis2, project_uses_rtthread(proj))
        content_root = project_content_root(proj)
        src_dir = content_root / 'Core' / 'Src'
        inc_dir = content_root / 'Core' / 'Inc'
        if not src_dir.is_dir():
            src_dir = content_root / 'Application' / 'LittleFS'
        if not inc_dir.is_dir():
            inc_dir = src_dir
        port_h, port_c = inc_dir / 'littlefs_port.h', src_dir / 'littlefs_port.c'
        _plan_owned_template(port_h, header, 'LITTLEFS_PORT_H', 'LittleFS 端口头文件', rep)
        _plan_owned_template(port_c, source, 'generated by keil_port_tool.py',
                             'LittleFS Flash 块设备模板', rep)
        if proj.add_file('LittleFS/Port', port_c.name, 1, rel_or_abs(port_c, proj.dir)):
            rep.files.append(('LittleFS/Port', port_c.name))
        proj.add_include_path(rel_or_abs(inc_dir, proj.dir), rep)
    rep.notes.append('LittleFS 模式: %s' % ('RT-Thread 线程安全' if project_uses_rtthread(proj) and use_rtos
                                           else 'FreeRTOS 线程安全' if use_rtos else '裸机'))
    rep.notes.append('使用前必须修改 littlefs_port.c 的容量/擦除参数，并实现四个 LittleFS_BD_* 函数')
    rep.notes.append('LittleFS_Init() 只尝试挂载，不会自动格式化；首次使用请明确调用 LittleFS_Format()')


def cmsis_dsp_source_files(root):
    """返回官方建议的目录聚合编译单元，避免把被 include 的子 .c 重复编译。"""
    root = Path(root)
    result = []
    source_root = root / 'Source'
    if not source_root.is_dir():
        return result
    for directory in sorted(p for p in source_root.iterdir() if p.is_dir()):
        exact = []
        for suffix in ('', 'F16'):
            candidate = directory / (directory.name + suffix + '.c')
            if candidate.is_file():
                exact.append(candidate)
        if exact:
            result.extend(exact)
            continue
        direct = sorted(directory.glob('*.c'))
        if len(direct) == 1:
            result.extend(direct)
    return result


def _cmsis_dsp_core_macro(proj):
    core, _fpu = proj.core_info()
    return {
        'Cortex-M0': 'ARM_MATH_CM0', 'Cortex-M0+': 'ARM_MATH_CM0PLUS',
        'Cortex-M3': 'ARM_MATH_CM3', 'Cortex-M4': 'ARM_MATH_CM4',
        'Cortex-M7': 'ARM_MATH_CM7', 'Cortex-M23': 'ARM_MATH_ARMV8MBL',
        'Cortex-M33': 'ARM_MATH_ARMV8MML', 'Cortex-M55': 'ARM_MATH_ARMV8MML',
        'Cortex-M85': 'ARM_MATH_ARMV8MML',
    }.get(core), core


def _find_cmsis_core_include(proj):
    for value in proj.include_dirs_abs():
        path = Path(value)
        try:
            if any(path.glob('core_cm*.h')):
                return path
        except OSError:
            pass
    content = project_content_root(proj)
    for rel in ('Drivers/CMSIS/Include', 'Drivers/CMSIS/Core/Include', 'CMSIS/Core/Include'):
        path = content / Path(rel)
        if path.is_dir() and any(path.glob('core_cm*.h')):
            return path
    return None


def do_cmsis_dsp(proj, opts, rep):
    compiler_modes = {proj.is_ac6(target) for target in proj.targets}
    selected_cores = {proj.target_core_info(target)[0] for target in proj.targets}
    if len(compiler_modes) > 1:
        raise ToolError('所选 Target 同时包含 AC5 与 AC6；请分别移植 CMSIS-DSP')
    if len(selected_cores) > 1:
        raise ToolError('所选 Target 使用不同 CPU 内核；请分别移植 CMSIS-DSP')
    requested = locate_cmsis_dsp_root(getattr(opts, 'cmsis_dsp', None))
    if requested is None and getattr(opts, 'cmsis_dsp', None):
        raise ToolError('所选目录中未找到 CMSIS-DSP Source/Include')
    if requested is None:
        top = ensure_cmsis_dsp_sdk(proj, opts, rep)
        requested = locate_cmsis_dsp_root(top) if top else None
    if requested is None:
        if getattr(opts, 'dry_run', False):
            return
        raise ToolError('未找到 CMSIS-DSP；可用 --cmsis-dsp auto 自动下载')
    requested = Path(requested).resolve()
    root, project_root = plan_project_library_copy(
        proj, requested, 'CMSIS-DSP', locate_cmsis_dsp_root, rep)
    root, project_root = Path(root).resolve(), Path(project_root).resolve()
    available = cmsis_dsp_source_files(root)
    if not available:
        raise ToolError('CMSIS-DSP 未找到可用的目录聚合源码')
    selected_rel = _selected_relative(getattr(opts, 'cmsis_dsp_files', None), (requested, root))
    modules = {x.lower() for x in (getattr(opts, 'dsp_modules', None) or [])}
    allow_f16 = bool(getattr(opts, 'dsp_float16', False)) and proj.any_ac6()
    sources = []
    for source in available:
        if modules and source.parent.name.lower() not in modules:
            continue
        if source.stem.lower().endswith('f16') and not allow_f16:
            continue
        if _component_chosen(source, root, selected_rel):
            sources.append(source)
    if not sources:
        raise ToolError('CMSIS-DSP 至少要选择一个算法模块')
    for source in sources:
        target = project_root / source.relative_to(root)
        group = 'CMSIS-DSP/' + source.parent.name
        if proj.add_file(group, source.name, 1, rel_or_abs(target, proj.dir)):
            rep.files.append((group, source.name))
    proj.add_include_path(rel_or_abs(project_root / 'Include', proj.dir), rep)
    if (root / 'PrivateInclude').is_dir():
        proj.add_include_path(rel_or_abs(project_root / 'PrivateInclude', proj.dir), rep)
    core_include = _find_cmsis_core_include(proj)
    if core_include:
        proj.add_include_path(rel_or_abs(core_include, proj.dir), rep)
    else:
        rep.warnings.append('未定位到 CMSIS-Core Include；arm_math.h 需要工程已有 core_cm*.h 路径')
    core_macro, core = _cmsis_dsp_core_macro(proj)
    if core_macro:
        known_core_macros = {
            'ARM_MATH_CM0', 'ARM_MATH_CM0PLUS', 'ARM_MATH_CM3', 'ARM_MATH_CM4',
            'ARM_MATH_CM7', 'ARM_MATH_ARMV8MBL', 'ARM_MATH_ARMV8MML'}
        current = _project_define_tokens(proj)
        conflicts = sorted((current & known_core_macros) - {core_macro})
        owned = _owned_component_defines(proj, 'cmsis_dsp')
        foreign = [name for name in conflicts if name not in owned]
        if foreign:
            raise ToolError('已有 CMSIS-DSP 内核宏与当前 Target 冲突: %s' % ', '.join(foreign))
        for name in conflicts:
            proj.remove_define_name(name, rep)
        proj.add_define(core_macro, rep)
    else:
        rep.warnings.append('无法识别 Cortex-M 内核，未自动添加 ARM_MATH_CM* 宏')
    if allow_f16 and 'DISABLEFLOAT16' in _project_define_tokens(proj):
        if 'DISABLEFLOAT16' in _owned_component_defines(proj, 'cmsis_dsp'):
            proj.remove_define_name('DISABLEFLOAT16', rep)
        else:
            raise ToolError('工程已有用户定义 DISABLEFLOAT16，不能同时启用 Float16 模块')
    elif not allow_f16:
        proj.add_define('DISABLEFLOAT16', rep)
    rep.notes.append('CMSIS-DSP: %s，加入 %d 个算法聚合编译单元' % (core or '未知内核', len(sources)))
    rep.notes.append('只编译每个模块的聚合 .c，避免与其内部 include 的子源码重复定义')


def rtos_guard_templates(resources, use_cmsis2, use_rtthread=False):
    names = [str(x).upper() for x in resources]
    enum_lines = ''.join('    RTOS_GUARD_%s,\n' % name for name in names)
    header = '''\
#ifndef RTOS_PERIPHERAL_GUARD_H
#define RTOS_PERIPHERAL_GUARD_H

#include <stdint.h>

typedef enum {
%(enum_lines)s    RTOS_GUARD_COUNT
} RTOS_GuardId;

int RTOS_PeripheralGuard_Init(void);
int RTOS_PeripheralGuard_Lock(RTOS_GuardId id, uint32_t timeout_ms);
void RTOS_PeripheralGuard_Unlock(RTOS_GuardId id);

#endif /* RTOS_PERIPHERAL_GUARD_H */
''' % {'enum_lines': enum_lines}
    if use_rtthread:
        body = '''#include <rtthread.h>
static rt_mutex_t s_guards[RTOS_GUARD_COUNT];
int RTOS_PeripheralGuard_Init(void)
{
    unsigned int i;
    for(i=0; i<RTOS_GUARD_COUNT; ++i) {
        if(!s_guards[i]) s_guards[i]=rt_mutex_create("io",RT_IPC_FLAG_PRIO);
        if(!s_guards[i]) return -1;
    }
    return 0;
}
int RTOS_PeripheralGuard_Lock(RTOS_GuardId id,uint32_t timeout_ms)
{
    rt_int32_t ticks;
    rt_uint64_t value;
    if((unsigned)id>=RTOS_GUARD_COUNT || !s_guards[id]) return -1;
    value=((rt_uint64_t)timeout_ms*RT_TICK_PER_SECOND+999U)/1000U;
    ticks=timeout_ms==UINT32_MAX?RT_WAITING_FOREVER:
          value>0x7fffffffU?0x7fffffff:(rt_int32_t)value;
    return rt_mutex_take(s_guards[id],ticks)==RT_EOK?0:-1;
}
void RTOS_PeripheralGuard_Unlock(RTOS_GuardId id)
{
    if((unsigned)id<RTOS_GUARD_COUNT && s_guards[id]) rt_mutex_release(s_guards[id]);
}
'''
    elif use_cmsis2:
        body = '''\
#include "cmsis_os2.h"
static osMutexId_t s_guards[RTOS_GUARD_COUNT];

int RTOS_PeripheralGuard_Init(void)
{
    uint32_t i;
    for (i = 0U; i < (uint32_t)RTOS_GUARD_COUNT; ++i) {
        if (s_guards[i] == NULL) s_guards[i] = osMutexNew(NULL);
        if (s_guards[i] == NULL) return -1;
    }
    return 0;
}

int RTOS_PeripheralGuard_Lock(RTOS_GuardId id, uint32_t timeout_ms)
{
    uint32_t timeout = (timeout_ms == UINT32_MAX) ? osWaitForever : timeout_ms;
    if ((uint32_t)id >= (uint32_t)RTOS_GUARD_COUNT || s_guards[id] == NULL) return -1;
    return osMutexAcquire(s_guards[id], timeout) == osOK ? 0 : -1;
}

void RTOS_PeripheralGuard_Unlock(RTOS_GuardId id)
{
    if ((uint32_t)id < (uint32_t)RTOS_GUARD_COUNT && s_guards[id] != NULL)
        (void)osMutexRelease(s_guards[id]);
}
'''
    else:
        body = '''\
#include "FreeRTOS.h"
#include "semphr.h"
static SemaphoreHandle_t s_guards[RTOS_GUARD_COUNT];

int RTOS_PeripheralGuard_Init(void)
{
    uint32_t i;
    for (i = 0U; i < (uint32_t)RTOS_GUARD_COUNT; ++i) {
        if (s_guards[i] == NULL) s_guards[i] = xSemaphoreCreateMutex();
        if (s_guards[i] == NULL) return -1;
    }
    return 0;
}

int RTOS_PeripheralGuard_Lock(RTOS_GuardId id, uint32_t timeout_ms)
{
    TickType_t timeout = (timeout_ms == UINT32_MAX) ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms);
    if ((uint32_t)id >= (uint32_t)RTOS_GUARD_COUNT || s_guards[id] == NULL) return -1;
    return xSemaphoreTake(s_guards[id], timeout) == pdTRUE ? 0 : -1;
}

void RTOS_PeripheralGuard_Unlock(RTOS_GuardId id)
{
    if ((uint32_t)id < (uint32_t)RTOS_GUARD_COUNT && s_guards[id] != NULL)
        (void)xSemaphoreGive(s_guards[id]);
}
'''
    source = ('/* FreeRTOS peripheral guard generated by keil_port_tool.py.\n'
              ' * KPS_USER_ACTION / 用户接入：在自己的 HAL 调用处配对 Lock/Unlock。\n'
              ' * Check lock results; release on every exit. No blocking lock in an ISR.\n'
              ' * DMA 事务必须保护到完成；模板不自动绑定句柄或修改已有 HAL 调用。\n'
              ' * See docs/POST-PORTING.en.md / docs/POST-PORTING.zh-CN.md (Guards). */\n'
              '#include "rtos_peripheral_guard.h"\n' + body)
    return header, source


def patch_guard_init(text):
    changed = False
    if '#include "rtos_peripheral_guard.h"' not in text:
        marker = '/* USER CODE END Includes */'
        if marker in text:
            text = text.replace(marker, '#include "rtos_peripheral_guard.h"\n' + marker, 1)
        else:
            includes = list(re.finditer(r'(?m)^#include[^\r\n]*', text))
            if not includes:
                return text, False
            pos = includes[-1].end()
            text = text[:pos] + '\n#include "rtos_peripheral_guard.h"' + text[pos:]
        changed = True
    if 'RTOS_PeripheralGuard_Init();' not in text:
        match = re.search(r'\bvoid\s+MX_FREERTOS_Init\s*\([^)]*\)\s*\{', text)
        if not match:
            return text, False
        pos = match.end()
        text = text[:pos] + '\n    (void)RTOS_PeripheralGuard_Init();' + text[pos:]
        changed = True
    return text, changed


def do_rtos_guard(proj, opts, rep):
    use_rtthread = project_uses_rtthread(proj)
    if not (project_uses_freertos(proj) or use_rtthread):
        raise ToolError('RTOS 外设锁需要先接入 FreeRTOS 或 RT-Thread')
    resources = []
    for value in getattr(opts, 'guard_resources', None) or ('UART', 'SPI', 'I2C', 'FLASH'):
        name = str(value).upper()
        if name not in ('UART', 'SPI', 'I2C', 'FLASH'):
            raise ToolError('未知外设锁: %s' % value)
        if name not in resources:
            resources.append(name)
    if not resources:
        raise ToolError('至少选择一个外设锁')
    use_cmsis2 = project_uses_cmsis_os2(proj)
    header, source = rtos_guard_templates(resources, use_cmsis2, use_rtthread)
    content_root = project_content_root(proj)
    src_dir, inc_dir = content_root / 'Core' / 'Src', content_root / 'Core' / 'Inc'
    if not src_dir.is_dir():
        src_dir = content_root / 'Application' / 'ThreadSafe'
    if not inc_dir.is_dir():
        inc_dir = src_dir
    guard_h, guard_c = inc_dir / 'rtos_peripheral_guard.h', src_dir / 'rtos_peripheral_guard.c'
    _plan_owned_template(guard_h, header, 'RTOS_PERIPHERAL_GUARD_H', 'FreeRTOS 外设锁头文件', rep)
    _plan_owned_template(guard_c, source, 'generated by keil_port_tool.py',
                         'FreeRTOS 外设锁实现', rep)
    group = 'RTThread/ThreadSafe' if use_rtthread else 'FreeRTOS/ThreadSafe'
    if proj.add_file(group, guard_c.name, 1, rel_or_abs(guard_c, proj.dir)):
        rep.files.append((group, guard_c.name))
    proj.add_include_path(rel_or_abs(inc_dir, proj.dir), rep)

    app_candidates = ((content_root / 'RTThread/App/rtthread_app.c',) if use_rtthread else ()) + (content_root / 'Core' / 'Src' / 'freertos_app.c',
                      content_root / 'Core' / 'Src' / 'freertos.c',
                      proj.dir / 'Application' / 'freertos_app.c')
    app = next((p for p in app_candidates if p.is_file() or
                os.path.normcase(str(p.resolve())) in getattr(proj, '_planned_generated_files', {})), None)
    if app:
        new_text, changed = (_patch_component_init(_planned_text(proj, app), 'rtos_peripheral_guard.h',
                             '(void)RTOS_PeripheralGuard_Init();', True) if use_rtthread else
                             patch_guard_init(_planned_text(proj, app)))
        if changed:
            rep.gen_files.append((app, new_text, '自动初始化 FreeRTOS 外设锁'))
        elif 'RTOS_PeripheralGuard_Init();' not in _planned_text(proj, app):
            rep.warnings.append('未能自动修改 MX_FREERTOS_Init()，请手动调用 RTOS_PeripheralGuard_Init()')
    else:
        rep.warnings.append('未找到 FreeRTOS 应用入口，请在调度器启动前调用 RTOS_PeripheralGuard_Init()')
    rep.notes.append('已生成外设锁: %s（%s API）' %
                     (', '.join(resources), 'CMSIS-RTOS2' if use_cmsis2 else '原生 FreeRTOS'))
    rep.notes.append('在 HAL_UART/SPI/I2C 或 Flash 操作前 Lock，完成后 Unlock')


# ===========================================================================
# 任务7: LwIP / TinyUSB
# ===========================================================================
def locate_lwip_root(d):
    if not d:
        return None
    d = Path(d).expanduser()
    for root in (d, d / 'lwip', d / 'Middlewares' / 'Third_Party' / 'LwIP'):
        if ((root / 'src' / 'include' / 'lwip' / 'init.h').is_file() and
                (root / 'src' / 'core' / 'init.c').is_file()):
            return root
    return None


def locate_tinyusb_root(d):
    if not d:
        return None
    d = Path(d).expanduser()
    for root in (d, d / 'tinyusb', d / 'Middlewares' / 'Third_Party' / 'TinyUSB'):
        if ((root / 'src' / 'tusb.c').is_file() and (root / 'src' / 'tusb.h').is_file()):
            return root
    return None


def tinyusb_version(root):
    try:
        text = read_source_text(Path(root) / 'src' / 'tusb_option.h')
    except (OSError, ToolError):
        return None
    values = []
    for name in ('MAJOR', 'MINOR', 'REVISION'):
        match = re.search(r'#define\s+TUSB_VERSION_%s\s+(\d+)' % name, text)
        if not match:
            return None
        values.append(int(match.group(1)))
    return tuple(values)


LWIP_SAFE_APPS = ('http', 'mqtt', 'mdns', 'sntp', 'netbiosns', 'tftp')
LWIP_DRIVERS = ('auto', 'stm32_eth', 'enc28j60', 'w5500', 'generic')


def lwip_rtos_mode(proj, opts):
    mode = str(getattr(opts, 'lwip_mode', 'auto') or 'auto').lower()
    mode = {'bare': 'baremetal', 'none': 'baremetal', 'freertos': 'rtos'}.get(mode, mode)
    if mode not in ('auto', 'rtos', 'baremetal'):
        raise ToolError('LwIP 模式无效: %s' % mode)
    return (project_uses_freertos(proj) or project_uses_rtthread(proj)) if mode == 'auto' else mode == 'rtos'


def lwip_available_sources(root, use_rtos=True, ipv6=True):
    root = Path(root)
    result = []
    for folder in ('src/core', 'src/core/ipv4'):
        result.extend(sorted((root / folder).glob('*.c')))
    ethernet = root / 'src' / 'netif' / 'ethernet.c'
    if ethernet.is_file():
        result.append(ethernet)
    if use_rtos:
        result.extend(sorted((root / 'src' / 'api').glob('*.c')))
    if ipv6:
        result.extend(sorted((root / 'src' / 'core' / 'ipv6').glob('*.c')))
    for app in LWIP_SAFE_APPS:
        app_sources = sorted((root / 'src' / 'apps' / app).glob('*.c'))
        # http/fs.c 通过 HTTPD_FSDATA_FILE 直接 include fsdata.c，不能再把
        # fsdata.c 当独立编译单元加入，否则网页资源符号会重复定义。
        result.extend(p for p in app_sources if not (app == 'http' and p.name == 'fsdata.c'))
    return list(dict.fromkeys(p.resolve() for p in result if p.is_file()))


def lwip_rtthread_sys_templates():
    header = '''/* RT-Thread sys_arch generated by keil_port_tool.py. */
#ifndef LWIP_ARCH_SYS_ARCH_H
#define LWIP_ARCH_SYS_ARCH_H
#include <rtthread.h>
typedef rt_sem_t sys_sem_t;
typedef rt_mutex_t sys_mutex_t;
typedef rt_mailbox_t sys_mbox_t;
typedef rt_thread_t sys_thread_t;
typedef rt_base_t sys_prot_t;
#define SYS_MBOX_NULL ((sys_mbox_t)0)
#define SYS_SEM_NULL ((sys_sem_t)0)
#endif
'''
    source = '''/* RT-Thread sys_arch generated by keil_port_tool.py. */
#include "lwip/opt.h"
#include "lwip/sys.h"
#include <rthw.h>
static rt_int32_t wait_ticks(u32_t ms)
{
    rt_uint64_t ticks;
    if (!ms) return RT_WAITING_FOREVER;
    ticks = ((rt_uint64_t)ms * RT_TICK_PER_SECOND + 999U) / 1000U;
    return ticks > 0x7fffffffU ? 0x7fffffff : (rt_int32_t)ticks;
}
static u32_t elapsed(rt_tick_t start)
{ return (u32_t)((rt_uint64_t)(rt_tick_get()-start)*1000U/RT_TICK_PER_SECOND); }
void sys_init(void) {}
u32_t sys_now(void) { return (u32_t)((rt_uint64_t)rt_tick_get()*1000U/RT_TICK_PER_SECOND); }
u32_t sys_jiffies(void) { return rt_tick_get(); }
err_t sys_sem_new(sys_sem_t *s, u8_t count)
{ *s=rt_sem_create("lwsem",count?1:0,RT_IPC_FLAG_PRIO); return *s?ERR_OK:ERR_MEM; }
void sys_sem_free(sys_sem_t *s) { rt_sem_delete(*s); *s=RT_NULL; }
void sys_sem_signal(sys_sem_t *s) { rt_sem_release(*s); }
u32_t sys_arch_sem_wait(sys_sem_t *s,u32_t timeout)
{
    rt_tick_t start=rt_tick_get();
    if(rt_sem_take(*s,wait_ticks(timeout))!=RT_EOK) return SYS_ARCH_TIMEOUT;
    return elapsed(start);
}
int sys_sem_valid(sys_sem_t *s) { return s && *s; }
void sys_sem_set_invalid(sys_sem_t *s) { *s=RT_NULL; }
err_t sys_mutex_new(sys_mutex_t *m)
{ *m=rt_mutex_create("lwmutex",RT_IPC_FLAG_PRIO); return *m?ERR_OK:ERR_MEM; }
void sys_mutex_lock(sys_mutex_t *m) { rt_mutex_take(*m,RT_WAITING_FOREVER); }
void sys_mutex_unlock(sys_mutex_t *m) { rt_mutex_release(*m); }
void sys_mutex_free(sys_mutex_t *m) { rt_mutex_delete(*m); *m=RT_NULL; }
int sys_mutex_valid(sys_mutex_t *m) { return m && *m; }
void sys_mutex_set_invalid(sys_mutex_t *m) { *m=RT_NULL; }
err_t sys_mbox_new(sys_mbox_t *m,int size)
{
    if(size<=0) { *m=RT_NULL; return ERR_VAL; }
    *m=rt_mb_create("lwmb",size,RT_IPC_FLAG_PRIO); return *m?ERR_OK:ERR_MEM;
}
void sys_mbox_free(sys_mbox_t *m) { rt_mb_delete(*m); *m=RT_NULL; }
void sys_mbox_post(sys_mbox_t *m,void *msg)
{ rt_mb_send_wait(*m,(rt_ubase_t)msg,RT_WAITING_FOREVER); }
err_t sys_mbox_trypost(sys_mbox_t *m,void *msg)
{ return rt_mb_send(*m,(rt_ubase_t)msg)==RT_EOK?ERR_OK:ERR_MEM; }
err_t sys_mbox_trypost_fromisr(sys_mbox_t *m,void *msg)
{ return sys_mbox_trypost(m,msg); }
u32_t sys_arch_mbox_fetch(sys_mbox_t *m,void **msg,u32_t timeout)
{
    rt_ubase_t value;
    rt_tick_t start=rt_tick_get();
    if(rt_mb_recv(*m,&value,wait_ticks(timeout))!=RT_EOK) return SYS_ARCH_TIMEOUT;
    if(msg) *msg=(void *)value;
    return elapsed(start);
}
u32_t sys_arch_mbox_tryfetch(sys_mbox_t *m,void **msg)
{
    rt_ubase_t value;
    if(rt_mb_recv(*m,&value,0)!=RT_EOK) return SYS_MBOX_EMPTY;
    if(msg) *msg=(void *)value;
    return 0;
}
int sys_mbox_valid(sys_mbox_t *m) { return m && *m; }
void sys_mbox_set_invalid(sys_mbox_t *m) { *m=RT_NULL; }
sys_thread_t sys_thread_new(const char *name,lwip_thread_fn fn,void *arg,int stacksize,int priority)
{
    rt_thread_t thread;
    if(stacksize<=0 || priority<0 || priority>=RT_THREAD_PRIORITY_MAX) return RT_NULL;
    thread=rt_thread_create(name,fn,arg,(rt_uint32_t)stacksize,(rt_uint8_t)priority,10);
    if(thread && rt_thread_startup(thread)!=RT_EOK) { rt_thread_delete(thread); return RT_NULL; }
    return thread;
}
sys_prot_t sys_arch_protect(void) { return rt_hw_interrupt_disable(); }
void sys_arch_unprotect(sys_prot_t level) { rt_hw_interrupt_enable(level); }
'''
    return header, source


def lwip_port_templates(use_rtos, enabled_apps, ipv6, memory=None, use_rtthread=False):
    apps = {str(x).lower() for x in enabled_apps}
    memory = memory or {'lwip_heap_kb': 16, 'lwip_pbufs': 16, 'lwip_pcbs': 8}
    opts_h = '''\
#ifndef LWIPOPTS_H
#define LWIPOPTS_H

/* Generated by keil_port_tool.py; tune pool sizes for the product. */
#define NO_SYS                          %(no_sys)d
#define SYS_LIGHTWEIGHT_PROT            1
#define MEM_ALIGNMENT                   4
#define MEM_SIZE                        (%(heap_kb)dU * 1024U)
#define MEMP_NUM_PBUF                   %(pbufs)d
#define MEMP_NUM_TCP_PCB                %(pcbs)d
#define PBUF_POOL_SIZE                  %(pbufs)d
#define PBUF_POOL_BUFSIZE               1536
#define LWIP_IPV4                       1
#define LWIP_IPV6                       %(ipv6)d
#define LWIP_ARP                        1
#define LWIP_IGMP                       %(mdns)d
#define LWIP_ICMP                       1
#define LWIP_RAW                        1
#define LWIP_UDP                        1
#define LWIP_TCP                        1
#define LWIP_DHCP                       1
#define LWIP_DNS                        1
#define LWIP_NETCONN                    %(rtos)d
#define LWIP_SOCKET                     %(rtos)d
#define LWIP_NETIF_API                  %(rtos)d
#define LWIP_TCPIP_CORE_LOCKING         0
/* LwIP 上游对这些 RTOS 参数的默认值是 0。对 FreeRTOS 端口来说，
 * 0 长度邮箱会直接触发 xQueueCreate() 断言，0 字节栈也无法
 * 创建 TCP/IP 线程。栈大小在本工具的 sys_arch.c 中以字节为单位。 */
#define TCPIP_THREAD_STACKSIZE          1024
#define TCPIP_THREAD_PRIO               4
#define TCPIP_MBOX_SIZE                 16
#define DEFAULT_THREAD_STACKSIZE        1024
#define DEFAULT_THREAD_PRIO             3
#define DEFAULT_RAW_RECVMBOX_SIZE       8
#define DEFAULT_UDP_RECVMBOX_SIZE       8
#define DEFAULT_TCP_RECVMBOX_SIZE       16
#define DEFAULT_ACCEPTMBOX_SIZE         8
#define LWIP_HTTPD                      %(http)d
#define LWIP_MQTT                       %(mqtt)d
#define LWIP_MDNS_RESPONDER             %(mdns)d
#define LWIP_SNTP                       %(sntp)d
#define LWIP_NETBIOS_RESPOND_NAME_QUERY %(netbiosns)d
#define LWIP_TFTP                       %(tftp)d
#define LWIP_STATS                      0
#define LWIP_PROVIDE_ERRNO              1
#define LWIP_NETIF_HOSTNAME             1
#define LWIP_NETIF_STATUS_CALLBACK      1
#define LWIP_NETIF_LINK_CALLBACK        1
#define LWIP_NUM_NETIF_CLIENT_DATA      %(mdns)d
#define CHECKSUM_GEN_IP                 1
#define CHECKSUM_GEN_UDP                1
#define CHECKSUM_GEN_TCP                1
#define CHECKSUM_CHECK_IP               1
#define CHECKSUM_CHECK_UDP              1
#define CHECKSUM_CHECK_TCP              1

#endif /* LWIPOPTS_H */
''' % {'no_sys': 0 if use_rtos else 1, 'rtos': 1 if use_rtos else 0,
       'ipv6': 1 if ipv6 else 0, 'heap_kb': memory['lwip_heap_kb'],
       'pbufs': memory['lwip_pbufs'], 'pcbs': memory['lwip_pcbs'],
       'http': 1 if 'http' in apps else 0, 'mqtt': 1 if 'mqtt' in apps else 0,
       'mdns': 1 if 'mdns' in apps else 0, 'sntp': 1 if 'sntp' in apps else 0,
       'netbiosns': 1 if 'netbiosns' in apps else 0, 'tftp': 1 if 'tftp' in apps else 0}
    cc_h = '''\
/* Generated by keil_port_tool.py. */
#ifndef LWIP_ARCH_CC_H
#define LWIP_ARCH_CC_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define BYTE_ORDER LITTLE_ENDIAN
/* Arm Compiler 5 is not covered by lwIP's GCC/Clang packing defaults. */
#if defined(__CC_ARM)
#define PACK_STRUCT_BEGIN __packed
#define PACK_STRUCT_END
#define PACK_STRUCT_STRUCT
#define PACK_STRUCT_FIELD(x) x
#endif
extern int lwip_errno;
#ifndef errno
#define errno lwip_errno
#endif
#define LWIP_PLATFORM_DIAG(x) do { printf x; } while (0)
#define LWIP_PLATFORM_ASSERT(x) do { (void)(x); for (;;) {} } while (0)
#define LWIP_RAND() ((uint32_t)rand())

#endif /* LWIP_ARCH_CC_H */
'''
    sys_h = '''\
#ifndef LWIP_ARCH_SYS_ARCH_H
#define LWIP_ARCH_SYS_ARCH_H

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"
typedef SemaphoreHandle_t sys_sem_t;
typedef SemaphoreHandle_t sys_mutex_t;
typedef QueueHandle_t sys_mbox_t;
typedef TaskHandle_t sys_thread_t;
typedef UBaseType_t sys_prot_t;
#define SYS_MBOX_NULL ((sys_mbox_t)0)
#define SYS_SEM_NULL  ((sys_sem_t)0)

#endif /* LWIP_ARCH_SYS_ARCH_H */
'''
    sys_c = '''\
/* FreeRTOS sys_arch port generated by keil_port_tool.py. */
#include "lwip/opt.h"
#include "lwip/sys.h"
#include "lwip/stats.h"

void sys_init(void) {}
u32_t sys_now(void) { return (u32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS); }
u32_t sys_jiffies(void) { return (u32_t)xTaskGetTickCount(); }

err_t sys_sem_new(sys_sem_t *sem, u8_t count)
{
    *sem = xSemaphoreCreateBinary();
    if (*sem == NULL) return ERR_MEM;
    if (count) (void)xSemaphoreGive(*sem);
    return ERR_OK;
}
void sys_sem_free(sys_sem_t *sem) { vSemaphoreDelete(*sem); *sem = NULL; }
void sys_sem_signal(sys_sem_t *sem) { (void)xSemaphoreGive(*sem); }
u32_t sys_arch_sem_wait(sys_sem_t *sem, u32_t timeout)
{
    TickType_t start = xTaskGetTickCount();
    TickType_t ticks = timeout ? pdMS_TO_TICKS(timeout) : portMAX_DELAY;
    if (timeout && ticks == 0) ticks = 1;
    if (xSemaphoreTake(*sem, ticks) != pdTRUE) return SYS_ARCH_TIMEOUT;
    return (u32_t)((xTaskGetTickCount() - start) * portTICK_PERIOD_MS);
}
int sys_sem_valid(sys_sem_t *sem) { return *sem != NULL; }
void sys_sem_set_invalid(sys_sem_t *sem) { *sem = NULL; }

err_t sys_mutex_new(sys_mutex_t *mutex)
{
    *mutex = xSemaphoreCreateMutex();
    return *mutex ? ERR_OK : ERR_MEM;
}
void sys_mutex_lock(sys_mutex_t *mutex) { (void)xSemaphoreTake(*mutex, portMAX_DELAY); }
void sys_mutex_unlock(sys_mutex_t *mutex) { (void)xSemaphoreGive(*mutex); }
void sys_mutex_free(sys_mutex_t *mutex) { vSemaphoreDelete(*mutex); *mutex = NULL; }
int sys_mutex_valid(sys_mutex_t *mutex) { return *mutex != NULL; }
void sys_mutex_set_invalid(sys_mutex_t *mutex) { *mutex = NULL; }

err_t sys_mbox_new(sys_mbox_t *mbox, int size)
{
    *mbox = xQueueCreate((UBaseType_t)size, sizeof(void *));
    return *mbox ? ERR_OK : ERR_MEM;
}
void sys_mbox_free(sys_mbox_t *mbox) { vQueueDelete(*mbox); *mbox = NULL; }
void sys_mbox_post(sys_mbox_t *mbox, void *msg) { (void)xQueueSendToBack(*mbox, &msg, portMAX_DELAY); }
err_t sys_mbox_trypost(sys_mbox_t *mbox, void *msg)
{ return xQueueSendToBack(*mbox, &msg, 0) == pdTRUE ? ERR_OK : ERR_MEM; }
err_t sys_mbox_trypost_fromisr(sys_mbox_t *mbox, void *msg)
{
    BaseType_t wake = pdFALSE;
    BaseType_t ok = xQueueSendToBackFromISR(*mbox, &msg, &wake);
    portYIELD_FROM_ISR(wake);
    return ok == pdTRUE ? ERR_OK : ERR_MEM;
}
u32_t sys_arch_mbox_fetch(sys_mbox_t *mbox, void **msg, u32_t timeout)
{
    TickType_t start = xTaskGetTickCount();
    TickType_t ticks = timeout ? pdMS_TO_TICKS(timeout) : portMAX_DELAY;
    void *dummy;
    if (timeout && ticks == 0) ticks = 1;
    if (xQueueReceive(*mbox, msg ? msg : &dummy, ticks) != pdTRUE) return SYS_ARCH_TIMEOUT;
    return (u32_t)((xTaskGetTickCount() - start) * portTICK_PERIOD_MS);
}
u32_t sys_arch_mbox_tryfetch(sys_mbox_t *mbox, void **msg)
{
    void *dummy;
    return xQueueReceive(*mbox, msg ? msg : &dummy, 0) == pdTRUE ? 0U : SYS_MBOX_EMPTY;
}
int sys_mbox_valid(sys_mbox_t *mbox) { return *mbox != NULL; }
void sys_mbox_set_invalid(sys_mbox_t *mbox) { *mbox = NULL; }

sys_thread_t sys_thread_new(const char *name, lwip_thread_fn thread, void *arg,
                            int stacksize, int priority)
{
    TaskHandle_t handle = NULL;
    configSTACK_DEPTH_TYPE depth = (configSTACK_DEPTH_TYPE)
        (((unsigned)stacksize + sizeof(StackType_t) - 1U) / sizeof(StackType_t));
    if (xTaskCreate(thread, name, depth, arg,
                    (UBaseType_t)priority, &handle) != pdPASS) return NULL;
    return handle;
}
sys_prot_t sys_arch_protect(void) { taskENTER_CRITICAL(); return 1U; }
void sys_arch_unprotect(sys_prot_t pval) { (void)pval; taskEXIT_CRITICAL(); }
'''
    port_h = '''\
#ifndef LWIP_PORT_H
#define LWIP_PORT_H

#include "lwip/err.h"
#include "lwip/netif.h"
#include "lwip/pbuf.h"

extern struct netif g_lwip_netif;
void LwIP_AppInit(void);
void LwIP_Poll(void);
err_t LwIP_Platform_Init(struct netif *netif);
err_t LwIP_Platform_LinkOutput(struct netif *netif, struct pbuf *p);
struct pbuf *LwIP_Platform_Input(struct netif *netif);

#endif /* LWIP_PORT_H */
'''
    rtos_include = ('#include "lwip/tcpip.h"\n#include "FreeRTOS.h"\n#include "task.h"\n'
                    if use_rtos else '#include "lwip/init.h"\n')
    rtos_task = '''\
static void LwIP_InputTask(void *argument)
{
    (void)argument;
    for (;;) { LwIP_Poll(); vTaskDelay(pdMS_TO_TICKS(1U)); }
}
''' if use_rtos else ''
    init_body = ('''\
    BaseType_t status;
    tcpip_init(LwIP_AddNetif, NULL);
    status = xTaskCreate(LwIP_InputTask, "lwip_rx", 384U, NULL,
                         tskIDLE_PRIORITY + 3U, NULL);
    configASSERT(status == pdPASS);
    if (status != pdPASS) return;
''' if use_rtos else
                 '    lwip_init();\n    LwIP_AddNetif(NULL);\n')
    timeout_body = '' if use_rtos else '    sys_check_timeouts();\n'
    if use_rtthread:
        sys_h, sys_c = lwip_rtthread_sys_templates()
        rtos_include = '#include "lwip/tcpip.h"\n#include "rtthread.h"\n'
        rtos_task = '''static struct rt_thread input_thread;
rt_align(RT_ALIGN_SIZE) static rt_uint8_t input_stack[1536];
static void LwIP_InputTask(void *argument)
{
    (void)argument;
    for (;;) { LwIP_Poll(); rt_thread_mdelay(1); }
}
'''
        init_body = '''    rt_err_t status;
    tcpip_init(LwIP_AddNetif, NULL);
    status=rt_thread_init(&input_thread,"lwip_rx",LwIP_InputTask,RT_NULL,
                           input_stack,sizeof(input_stack),13,10);
    RT_ASSERT(status==RT_EOK);
    if(status!=RT_EOK) return;
    status=rt_thread_startup(&input_thread);
    RT_ASSERT(status==RT_EOK);
'''
    port_c = '''\
/* Generic Ethernet port generated by keil_port_tool.py. */
#include "lwip_port.h"
#include "lwip/etharp.h"
#include "lwip/ethip6.h"
#include "lwip/timeouts.h"
#include "netif/ethernet.h"
#include "lwip/dhcp.h"
%(rtos_include)s
int lwip_errno;

#if defined(__CC_ARM)
#define LWIP_PORT_WEAK __weak
#elif defined(__GNUC__) || defined(__clang__)
#define LWIP_PORT_WEAK __attribute__((weak))
#else
#define LWIP_PORT_WEAK
#endif

struct netif g_lwip_netif;
static volatile uint8_t s_lwip_ready;
%(rtos_task)s

static err_t LwIP_NetifInit(struct netif *netif)
{
    netif->name[0] = 'e'; netif->name[1] = 'n';
    netif->output = etharp_output;
#if LWIP_IPV6
    netif->output_ip6 = ethip6_output;
#endif
    netif->linkoutput = LwIP_Platform_LinkOutput;
    netif->mtu = 1500U;
    netif->flags = NETIF_FLAG_BROADCAST | NETIF_FLAG_ETHARP;
    return LwIP_Platform_Init(netif);
}

static void LwIP_AddNetif(void *argument)
{
    ip4_addr_t ip, mask, gateway;
    (void)argument;
    IP4_ADDR(&ip, 0, 0, 0, 0);
    IP4_ADDR(&mask, 0, 0, 0, 0);
    IP4_ADDR(&gateway, 0, 0, 0, 0);
    if (netif_add(&g_lwip_netif, &ip, &mask, &gateway, NULL,
                  LwIP_NetifInit, %(netif_input)s) != NULL) {
        netif_set_default(&g_lwip_netif);
        netif_set_up(&g_lwip_netif);
#if LWIP_DHCP
        (void)dhcp_start(&g_lwip_netif);
#endif
        s_lwip_ready = 1U;
    }
}

void LwIP_AppInit(void)
{
%(init_body)s}

void LwIP_Poll(void)
{
    struct pbuf *p;
    unsigned int budget = 8U;
    if (!s_lwip_ready) return;
    while (budget-- && (p = LwIP_Platform_Input(&g_lwip_netif)) != NULL) {
        if (g_lwip_netif.input(p, &g_lwip_netif) != ERR_OK) pbuf_free(p);
    }
%(timeout_body)s}

LWIP_PORT_WEAK err_t LwIP_Platform_Init(struct netif *netif)
{ (void)netif; return ERR_IF; }
LWIP_PORT_WEAK err_t LwIP_Platform_LinkOutput(struct netif *netif, struct pbuf *p)
{ (void)netif; (void)p; return ERR_IF; }
LWIP_PORT_WEAK struct pbuf *LwIP_Platform_Input(struct netif *netif)
{ (void)netif; return NULL; }
''' % {'rtos_include': rtos_include, 'rtos_task': rtos_task, 'init_body': init_body,
       'timeout_body': timeout_body,
       'netif_input': 'tcpip_input' if use_rtos else 'ethernet_input'}
    if use_rtthread:
        # lwIP checks LWIP_PROVIDE_ERRNO with #ifdef, so defining it as 0
        # still redeclares RT-Thread's error constants. Use one errno family.
        opts_h = opts_h.replace('#define LWIP_PROVIDE_ERRNO              1',
                                '#define LWIP_ERRNO_INCLUDE              "sys/errno.h"')
        cc_h = cc_h.replace('#include <stdint.h>', '''#include <rtthread.h>
#include <sys/types.h>
#include <limits.h>
/* RT-Thread already supplies ssize_t; do not let lwIP typedef it again. */
#ifndef SSIZE_MAX
#define SSIZE_MAX LONG_MAX
#endif
#define LWIP_NO_UNISTD_H 1
#include <stdint.h>''')
        cc_h = cc_h.replace('extern int lwip_errno;\n#ifndef errno\n#define errno lwip_errno\n#endif',
                              '#ifdef errno\n#undef errno\n#endif\n#define errno (*_rt_errno())')
        port_c = port_c.replace('int lwip_errno;\n', '')
    return opts_h, cc_h, (sys_h if use_rtos else None), (sys_c if use_rtos else None), port_h, port_c


def lwip_driver_value(proj, value):
    value = str(value or 'auto').lower()
    if value not in LWIP_DRIVERS:
        raise ToolError('未知 LwIP 网卡类型: %s' % value)
    if value == 'auto':
        return 'stm32_eth' if (proj.device() or '').upper().startswith('STM32') else 'generic'
    return value


def lwip_driver_templates(driver):
    labels = {
        'stm32_eth': 'STM32 ETH MAC + PHY',
        'enc28j60': 'ENC28J60 SPI Ethernet',
        'w5500': 'W5500 MACRAW Ethernet',
        'generic': 'Generic Ethernet MAC',
    }
    label = labels[driver]
    header = '''\
#ifndef LWIP_NETIF_DRIVER_H
#define LWIP_NETIF_DRIVER_H
#include <stdint.h>
int NetworkDriver_Init(void);
int NetworkDriver_Send(const uint8_t *frame, uint16_t length);
int NetworkDriver_Receive(uint8_t *frame, uint16_t capacity);
void NetworkDriver_GetMac(uint8_t mac[6]);
#endif /* LWIP_NETIF_DRIVER_H */
'''
    source = '''\
/* %(label)s adapter generated by keil_port_tool.py.
 * KPS_USER_ACTION / 用户接入：在本文件或独立 C 文件实现下面四个 NetworkDriver_*。
 * Init/Send: 0 means success. Receive: frame bytes, 0 when idle; enforce capacity.
 * Send must finish/copy before return because the shared TX buffer is reused.
 * 核对 PHY 地址/复位/RMII 时钟、MAC 地址、DMA；补齐断连重连 link 状态。
 * lwip_port.c/LwIP_AddNetif: choose static IPv4 or DHCP; no DHCP on a plain PC link.
 * See docs/POST-PORTING.en.md / docs/POST-PORTING.zh-CN.md (LwIP).
 * Implement the four weak NetworkDriver_* hooks with your HAL/SPI driver.
 */
#include "lwip_netif_driver.h"
#include "lwip_port.h"
#include "lwip/pbuf.h"
#include <string.h>

#if defined(__CC_ARM)
#define NET_DRIVER_WEAK __weak
#elif defined(__GNUC__) || defined(__clang__)
#define NET_DRIVER_WEAK __attribute__((weak))
#else
#define NET_DRIVER_WEAK
#endif

static uint8_t s_tx_frame[1536];
static uint8_t s_rx_frame[1536];

err_t LwIP_Platform_Init(struct netif *netif)
{
    netif->hwaddr_len = 6U;
    NetworkDriver_GetMac(netif->hwaddr);
    if (NetworkDriver_Init() != 0) return ERR_IF;
    netif->flags |= NETIF_FLAG_LINK_UP;
    return ERR_OK;
}

err_t LwIP_Platform_LinkOutput(struct netif *netif, struct pbuf *p)
{
    struct pbuf *q;
    uint16_t offset = 0U;
    (void)netif;
    if (p->tot_len > sizeof(s_tx_frame)) return ERR_BUF;
    for (q = p; q != NULL; q = q->next) {
        memcpy(&s_tx_frame[offset], q->payload, q->len);
        offset = (uint16_t)(offset + q->len);
    }
    return NetworkDriver_Send(s_tx_frame, offset) == 0 ? ERR_OK : ERR_IF;
}

struct pbuf *LwIP_Platform_Input(struct netif *netif)
{
    int length;
    struct pbuf *p;
    (void)netif;
    length = NetworkDriver_Receive(s_rx_frame, sizeof(s_rx_frame));
    if (length <= 0 || length > (int)sizeof(s_rx_frame)) return NULL;
    p = pbuf_alloc(PBUF_RAW, (u16_t)length, PBUF_POOL);
    if (p == NULL) return NULL;
    if (pbuf_take(p, s_rx_frame, (u16_t)length) != ERR_OK) { pbuf_free(p); return NULL; }
    return p;
}

NET_DRIVER_WEAK int NetworkDriver_Init(void) { return -1; }
NET_DRIVER_WEAK int NetworkDriver_Send(const uint8_t *frame, uint16_t length)
{ (void)frame; (void)length; return -1; }
NET_DRIVER_WEAK int NetworkDriver_Receive(uint8_t *frame, uint16_t capacity)
{ (void)frame; (void)capacity; return 0; }
NET_DRIVER_WEAK void NetworkDriver_GetMac(uint8_t mac[6])
{ static const uint8_t fallback[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x01}; memcpy(mac, fallback, 6); }
''' % {'label': label}
    return header, source, label


def _patch_component_init(text, header, call, prefer_rtos=False):
    changed = False
    include = '#include "%s"' % header
    if include not in text:
        marker = '/* USER CODE END Includes */'
        if marker in text:
            text = text.replace(marker, include + '\n' + marker, 1)
        else:
            includes = list(re.finditer(r'(?m)^#include[^\r\n]*', text))
            if not includes:
                return text, False
            pos = includes[-1].end()
            text = text[:pos] + '\n' + include + text[pos:]
        changed = True
    if call in text:
        return text, changed
    if prefer_rtos:
        match = re.search(r'\bvoid\s+RTThread_DefaultTask\s*\([^)]*\)\s*\{', text)
        if match:
            text = text[:match.end()] + '\n    %s' % call + text[match.end():]
            return text, True
        match = re.search(r'\bvoid\s+MX_FREERTOS_Init\s*\([^)]*\)\s*\{', text)
        if match:
            text = text[:match.end()] + '\n    %s' % call + text[match.end():]
            return text, True
    marker = '/* USER CODE END 2 */'
    if marker in text:
        text = text.replace(marker, '  %s\n' % call + marker, 1)
        return text, True
    return text, changed


def _patch_main_loop_call(text, call):
    if call in text:
        return text, False
    marker = '/* USER CODE BEGIN 3 */'
    if marker in text:
        return text.replace(marker, marker + '\n    ' + call, 1), True
    return text, False


def do_lwip(proj, opts, rep):
    use_rtos = lwip_rtos_mode(proj, opts)
    use_rtthread = use_rtos and project_uses_rtthread(proj)
    if use_rtos and not (project_uses_freertos(proj) or use_rtthread):
        raise ToolError('LwIP RTOS 模式要求工程已接入 FreeRTOS 或 RT-Thread')
    requested = locate_lwip_root(getattr(opts, 'lwip', None))
    if requested is None and getattr(opts, 'lwip', None):
        raise ToolError('所选目录中未找到 LwIP src/include/lwip/init.h')
    if requested is None:
        top = ensure_lwip_sdk(proj, opts, rep)
        requested = locate_lwip_root(top) if top else None
    if requested is None:
        if getattr(opts, 'dry_run', False):
            return
        raise ToolError('未找到 LwIP；可用 --lwip auto 自动下载')
    requested = Path(requested).resolve()
    root, project_root = plan_project_library_copy(
        proj, requested, 'LwIP', locate_lwip_root, rep)
    root, project_root = Path(root).resolve(), Path(project_root).resolve()
    ipv6 = bool(getattr(opts, 'lwip_ipv6', False))
    available = lwip_available_sources(root, use_rtos, ipv6=True)
    selected_rel = _selected_relative(getattr(opts, 'lwip_files', None), (requested, root))
    requested_apps = getattr(opts, 'lwip_apps', None)
    apps_filter = ({str(x).lower() for x in LWIP_SAFE_APPS} if requested_apps is None else
                   {str(x).lower() for x in requested_apps})
    sources = []
    for source in available:
        rel = str(source.relative_to(root)).replace('\\', '/').lower()
        if '/core/ipv6/' in rel and not ipv6:
            continue
        app_match = re.search(r'/apps/([^/]+)/', rel)
        if app_match and app_match.group(1) not in apps_filter:
            continue
        if _component_chosen(source, root, selected_rel):
            sources.append(source)
    required_paths = {p.resolve() for p in lwip_available_sources(root, use_rtos, ipv6=False)
                      if '/apps/' not in str(p).replace('\\', '/').lower()}
    chosen_paths = {p.resolve() for p in sources}
    missing = sorted(p.name for p in required_paths - chosen_paths)
    if missing:
        raise ToolError('LwIP 核心文件不能取消: %s' % ', '.join(missing))
    embedded_fsdata = root / 'src' / 'apps' / 'http' / 'fsdata.c'
    if embedded_fsdata.is_file():
        target = project_root / embedded_fsdata.relative_to(root)
        if proj.remove_file(rel_or_abs(target, proj.dir)):
            rep.notes.append('已移除独立 fsdata.c：LwIP 的 fs.c 会直接包含该文件')
    removed = 0
    for candidate in available:
        if candidate.resolve() not in chosen_paths:
            target = project_root / candidate.relative_to(root)
            removed += proj.remove_file(rel_or_abs(target, proj.dir))
    if removed:
        rep.notes.append('已从工程移除 %d 个当前未选择的 LwIP 文件' % removed)
    app_sources = [p for p in sources if p.parent.parent.name.lower() == 'apps']
    enabled_apps = sorted({p.parent.name.lower() for p in app_sources
                           if p.parent.name.lower() != 'http' or p.name.lower() == 'httpd.c'})
    selected_app_modules = sorted({p.parent.name.lower() for p in app_sources})
    ipv6_enabled = any('/core/ipv6/' in str(p).replace('\\', '/').lower() for p in sources)
    for source in sources:
        target = project_root / source.relative_to(root)
        rel = source.relative_to(root / 'src')
        group = 'LwIP/' + '/'.join(rel.parts[:-1])
        if proj.add_file(group, source.name, 1, rel_or_abs(target, proj.dir)):
            rep.files.append((group, source.name))
    proj.add_include_path(rel_or_abs(project_root / 'src' / 'include', proj.dir), rep)
    proj.enable_c99_gnu(rep)

    driver = lwip_driver_value(proj, getattr(opts, 'lwip_driver', 'auto'))
    memory = embedded_memory_profile(proj)
    opts_h, cc_h, sys_h, sys_c, port_h, port_c = lwip_port_templates(
        use_rtos, enabled_apps, ipv6_enabled, memory, use_rtthread)
    driver_h, driver_c, driver_label = lwip_driver_templates(driver)
    content = project_content_root(proj)
    cfg = content / 'Config' / 'LwIP'
    src_dir, inc_dir = content / 'Core' / 'Src', content / 'Core' / 'Inc'
    if not src_dir.is_dir():
        src_dir = content / 'Application' / 'LwIP'
    if not inc_dir.is_dir():
        inc_dir = src_dir
    generated = [(cfg / 'lwipopts.h', opts_h, 'LwIP 工程配置'),
                 (cfg / 'arch' / 'cc.h', cc_h, 'LwIP 编译器适配'),
                 (inc_dir / 'lwip_port.h', port_h, 'LwIP 硬件端口头文件'),
                 (src_dir / 'lwip_port.c', port_c, 'LwIP 通用以太网端口'),
                 (inc_dir / 'lwip_netif_driver.h', driver_h, '%s 适配头文件' % driver_label),
                 (src_dir / 'lwip_netif_driver.c', driver_c, '%s 适配骨架' % driver_label)]
    if use_rtos:
        backend_name = 'RT-Thread' if use_rtthread else 'FreeRTOS'
        generated.extend(((cfg / 'arch' / 'sys_arch.h', sys_h, 'LwIP %s 类型适配' % backend_name),
                          (src_dir / 'sys_arch.c', sys_c, 'LwIP %s 系统层' % backend_name)))
    lwip_managed = (
        'NO_SYS', 'LWIP_IPV6', 'LWIP_IGMP', 'LWIP_NETCONN', 'LWIP_SOCKET',
        'LWIP_NETIF_API', 'LWIP_HTTPD', 'LWIP_MQTT', 'LWIP_MDNS_RESPONDER',
        'LWIP_SNTP', 'LWIP_NETBIOS_RESPOND_NAME_QUERY', 'LWIP_TFTP',
        'LWIP_NUM_NETIF_CLIENT_DATA', 'LWIP_ERRNO_STDINCLUDE', 'LWIP_ERRNO_INCLUDE',
        'LWIP_PROVIDE_ERRNO', 'TCPIP_THREAD_STACKSIZE', 'TCPIP_THREAD_PRIO',
        'TCPIP_MBOX_SIZE', 'DEFAULT_THREAD_STACKSIZE', 'DEFAULT_THREAD_PRIO',
        'DEFAULT_RAW_RECVMBOX_SIZE', 'DEFAULT_UDP_RECVMBOX_SIZE',
        'DEFAULT_TCP_RECVMBOX_SIZE', 'DEFAULT_ACCEPTMBOX_SIZE')
    for path, value, desc in generated:
        if path.name == 'lwipopts.h':
            _plan_managed_config(path, value, lwip_managed, desc, rep)
        elif not use_rtthread and path.name in ('cc.h', 'lwip_port.c') and _plan_lwip_errno_upgrade(path, desc, rep):
            pass
        else:
            _plan_owned_template(path, value, 'generated by keil_port_tool.py', desc, rep)
    for path in (src_dir / 'lwip_port.c', src_dir / 'lwip_netif_driver.c', src_dir / 'sys_arch.c'):
        if path.name == 'sys_arch.c' and not use_rtos:
            continue
        if proj.add_file('LwIP/Port', path.name, 1, rel_or_abs(path, proj.dir)):
            rep.files.append(('LwIP/Port', path.name))
    proj.add_include_path(rel_or_abs(cfg, proj.dir), rep)
    proj.add_include_path(rel_or_abs(inc_dir, proj.dir), rep)

    if use_rtos:
        candidates = ((content / 'RTThread/App/rtthread_app.c',) if use_rtthread else ()) + (content / 'Core' / 'Src' / 'freertos_app.c',
                      content / 'Core' / 'Src' / 'freertos.c',
                      proj.dir / 'Application' / 'freertos_app.c',
                      content / 'Core' / 'Src' / 'main.c', proj.dir / 'main.c')
    else:
        candidates = (content / 'Core' / 'Src' / 'main.c', proj.dir / 'main.c')
    entry = next((p for p in candidates if p.is_file() or
                  os.path.normcase(str(p.resolve())) in getattr(proj, '_planned_generated_files', {})), None)
    if entry:
        patched, changed = _patch_component_init(_planned_text(proj, entry), 'lwip_port.h',
                                                 'LwIP_AppInit();', use_rtos)
        if not use_rtos:
            patched, loop_changed = _patch_main_loop_call(patched, 'LwIP_Poll();')
            changed = changed or loop_changed
        if changed:
            rep.gen_files.append((entry, patched, '自动初始化 LwIP'))
        if 'LwIP_AppInit();' not in patched:
            rep.warnings.append('应用入口缺少标准 CubeMX 插入点；请自行调用 LwIP_AppInit()')
        if not use_rtos and 'LwIP_Poll();' not in patched:
            rep.warnings.append('裸机 main() 缺少 USER CODE BEGIN 3；请在主循环调用 LwIP_Poll()')
    else:
        rep.warnings.append('未找到应用入口；请自行调用 LwIP_AppInit()')
    rep.notes.append('LwIP 模式: %s；网卡: %s；IPv6: %s；应用: %s' %
                     ('FreeRTOS' if use_rtos else '裸机', driver_label,
                      '开' if ipv6_enabled else '关', ', '.join(selected_app_modules) or '无'))
    if memory['ram'] and memory['ram'] <= 64 * 1024:
        rep.notes.append('Target RAM 为 %d KB；LwIP 新配置采用 %d KB 堆、%d 个 PBUF' %
                         (memory['ram'] // 1024, memory['lwip_heap_kb'],
                          memory['lwip_pbufs']))
    rep.notes.append('必须实现 NetworkDriver_Init/Send/Receive/GetMac，并在合适的任务或主循环调用 LwIP_Poll()')


TINYUSB_CLASSES = ('CDC', 'MSC', 'HID', 'MIDI', 'VENDOR')


def tinyusb_mode_value(value):
    value = str(value or 'device').lower()
    if value not in ('device', 'host', 'both'):
        raise ToolError('TinyUSB 模式无效: %s' % value)
    return value


def tinyusb_mcu_macro(proj):
    dev = (proj.device() or '').upper()
    for prefix, macro in (
            ('STM32F0', 'OPT_MCU_STM32F0'), ('STM32F1', 'OPT_MCU_STM32F1'),
            ('STM32F2', 'OPT_MCU_STM32F2'), ('STM32F3', 'OPT_MCU_STM32F3'),
            ('STM32F4', 'OPT_MCU_STM32F4'), ('STM32F7', 'OPT_MCU_STM32F7'),
            ('STM32G0', 'OPT_MCU_STM32G0'), ('STM32G4', 'OPT_MCU_STM32G4'),
            ('STM32H5', 'OPT_MCU_STM32H5'), ('STM32H7', 'OPT_MCU_STM32H7'),
            ('STM32L0', 'OPT_MCU_STM32L0'), ('STM32L1', 'OPT_MCU_STM32L1'),
            ('STM32L4', 'OPT_MCU_STM32L4'), ('STM32L5', 'OPT_MCU_STM32L5'),
            ('STM32U5', 'OPT_MCU_STM32U5'), ('STM32U0', 'OPT_MCU_STM32U0'),
            ('STM32C0', 'OPT_MCU_STM32C0'), ('STM32WBA', 'OPT_MCU_STM32WBA'),
            ('STM32N6', 'OPT_MCU_STM32N6'), ('STM32WB', 'OPT_MCU_STM32WB')):
        if dev.startswith(prefix):
            return macro
    return None


def tinyusb_portable_sources(root, proj, mode):
    root = Path(root)
    dev = (proj.device() or '').upper()
    candidates = []
    if dev.startswith(('STM32F0', 'STM32F1', 'STM32F3', 'STM32G0', 'STM32G4',
                       'STM32L0', 'STM32L1', 'STM32L4', 'STM32U0', 'STM32C0',
                       'STM32WBA', 'STM32WB')):
        if mode in ('device', 'both'):
            candidates.append(root / 'src' / 'portable' / 'st' / 'stm32_fsdev' / 'dcd_stm32_fsdev.c')
    elif dev.startswith('STM32'):
        base = root / 'src' / 'portable' / 'synopsys' / 'dwc2'
        candidates.append(base / 'dwc2_common.c')
        if mode in ('device', 'both'):
            candidates.append(base / 'dcd_dwc2.c')
        if mode in ('host', 'both'):
            candidates.append(base / 'hcd_dwc2.c')
    return [p.resolve() for p in candidates if p.is_file()]


def validate_tinyusb_controller_sources(sources, mode):
    """Common portable helpers do not implement a Device/Host controller."""
    portable = [Path(p).name for p in sources if 'portable' in Path(p).parts]
    for role, prefix in (('device', 'dcd_'), ('host', 'hcd_')):
        if mode in (role, 'both') and not any(name.startswith(prefix) for name in portable):
            raise ToolError('TinyUSB %s 缺少/未选择 %s 控制器驱动；请检查芯片、SDK 版本和文件勾选。'
                            'STM32 DWC2 Host 不能使用缺少 hcd_dwc2.c 的 0.17 SDK。'
                            ' / Missing %s controller driver: check MCU, SDK and selected files.' %
                            (role, prefix, role))


def tinyusb_timebase_template(use_rtos=False, use_rtthread=False, use_hal=False):
    """Real millisecond clock for SDK >=0.18; never fake a constant timebase."""
    declarations = ''
    if use_rtthread:
        expression = '(uint32_t)(((uint64_t)rt_tick_get() * 1000U) / RT_TICK_PER_SECOND)'
    elif use_rtos:
        expression = '(uint32_t)(((uint64_t)xTaskGetTickCount() * 1000U) / configTICK_RATE_HZ)'
    elif use_hal:
        declarations = 'extern uint32_t HAL_GetTick(void);\n'
        expression = 'HAL_GetTick()'
    else:
        declarations = ('/* KPS_USER_ACTION: implement a continuously advancing millisecond clock.\n'
                        ' * 用户接入：实现持续递增的毫秒时基；不可返回常数，否则 Host 枚举会卡住。 */\n'
                        'extern uint32_t TinyUSB_Platform_Millis(void);\n')
        expression = 'TinyUSB_Platform_Millis()'
    return ('\n/* SDK >=0.18 timebase. Override this weak function if using a different clock.\n'
            ' * RTOS tick must be running before starting USB enumeration. */\n' + declarations +
            'TUSB_APP_WEAK uint32_t tusb_time_millis_api(void)\n{\n    return ' + expression + ';\n}\n')


def tinyusb_source_files(root, proj, mode, classes):
    root = Path(root)
    result = [root / 'src' / 'tusb.c', root / 'src' / 'common' / 'tusb_fifo.c']
    if mode in ('device', 'both'):
        result.append(root / 'src' / 'device' / 'usbd.c')
        # TinyUSB <= 0.20 使用独立 usbd_control.c；0.21 已合并进 usbd.c。
        control = root / 'src' / 'device' / 'usbd_control.c'
        if control.is_file():
            result.append(control)
    if mode in ('host', 'both'):
        result.extend((root / 'src' / 'host' / 'usbh.c',
                       root / 'src' / 'host' / 'hub.c'))
        control = root / 'src' / 'host' / 'usbh_control.c'
        if control.is_file():
            result.append(control)
    for name in classes:
        folder = root / 'src' / 'class' / name.lower()
        if not folder.is_dir() and name == 'VENDOR':
            folder = root / 'src' / 'class' / 'vendor'
        if mode in ('device', 'both'):
            result.extend(sorted(folder.glob('*_device.c')))
        if mode in ('host', 'both'):
            result.extend(sorted(folder.glob('*_host.c')))
    result.extend(tinyusb_portable_sources(root, proj, mode))
    return list(dict.fromkeys(p.resolve() for p in result if p.is_file()))


def patch_tinyusb_armcc5_compiler(text):
    """
    TinyUSB 0.17 的 GNU 分支在 ARMCC5 --gnu 模式下会误用
    __builtin_bswap16/32；ARMCC5 把它们当外部函数，最终链接失败。
    只在 __CC_ARM 下改用纯 C 字节序交换，不影响 GCC/Clang/AC6。
    """
    marker = 'defined(__CC_ARM) && !defined(__clang__) /* Keil Port Studio AC5 */'
    if marker in text:
        return text, False
    old = '''\
  #else
    #define TU_BSWAP16(u16) (__builtin_bswap16(u16))
    #define TU_BSWAP32(u32) (__builtin_bswap32(u32))
  #endif
'''
    if old not in text:
        return text, False
    new = '''\
  #else
    #if defined(__CC_ARM) && !defined(__clang__) /* Keil Port Studio AC5 */
      #define TU_BSWAP16(u16) ((unsigned short)((((unsigned short)(u16) & 0x00ffU) << 8) | (((unsigned short)(u16) & 0xff00U) >> 8)))
      #define TU_BSWAP32(u32) ((((unsigned long)(u32) & 0xff000000UL) >> 24) | (((unsigned long)(u32) & 0x00ff0000UL) >> 8) | (((unsigned long)(u32) & 0x0000ff00UL) << 8) | (((unsigned long)(u32) & 0x000000ffUL) << 24))
    #else
      #define TU_BSWAP16(u16) (__builtin_bswap16(u16))
      #define TU_BSWAP32(u32) (__builtin_bswap32(u32))
    #endif
  #endif
'''
    return text.replace(old, new, 1), True


def patch_tinyusb_armcc5_dwc2_registers(text):
    """Keep MMIO bitfield accesses word-sized on AC5; never alter USB wire structs."""
    if 'KPS_DWC2_REG_LAYOUT' in text:
        return text, False
    pattern = r'typedef struct TU_ATTR_PACKED\s*\{([^{}]*)\}\s*(dwc2_\w+_t)\s*;'
    matches = list(re.finditer(pattern, text))
    if not matches:
        return text, False
    for match in matches:
        body = re.sub(r'/\*.*?\*/|//[^\n]*', '', match.group(1), flags=re.S)
        field_pattern = r'(?:const\s+)?uint32_t\s+\w+\s*:\s*\d+\s*;'
        fields = re.findall(field_pattern, body)
        remaining = re.sub(field_pattern, '', body)
        if not fields or remaining.strip():
            raise ToolError('TinyUSB DWC2 register layout changed; AC5 fix requires review: ' + match.group(2))
    marker = '#define TUSB_DWC2_TYPES_H_'
    if text.count(marker) != 1:
        raise ToolError('TinyUSB DWC2 header guard changed; cannot safely apply AC5 register fix')
    text = re.sub(pattern, lambda m: m.group(0).replace('TU_ATTR_PACKED', 'KPS_DWC2_REG_LAYOUT', 1), text)
    definitions = '''
/* Keil Port Studio: AC5 packed volatile bitfields generate byte/halfword
 * MMIO accesses (e.g. LDRB at HPRT+2). DWC2 requires 32-bit register access.
 * These types contain only uint32_t bitfields. Retain upstream size/offset
 * assertions and leave protocol/wire-layout packing completely unchanged. */
#if defined(__CC_ARM) && !defined(__clang__)
#define KPS_DWC2_REG_LAYOUT
#else
#define KPS_DWC2_REG_LAYOUT TU_ATTR_PACKED
#endif
'''
    return text.replace(marker, marker + '\n' + definitions, 1), True


def patch_tinyusb_dwc2_fifo_snapshot(text):
    """RX status is read-to-pop: copy its raw word once, not each bitfield."""
    if 'KPS_DWC2_RX_SNAPSHOT' in text:
        return text, False
    pattern = r'const\s+dwc2_grxstsp_t\s+grxstsp_bm\s*=\s*dwc2->grxstsp_bm\s*;'
    if not re.search(pattern, text):
        if 'dwc2->grxstsp_bm' in text:
            raise ToolError('TinyUSB DWC2 RX FIFO layout changed; manual AC5 review required')
        return text, False
    replacement = '''/* KPS_DWC2_RX_SNAPSHOT: one 32-bit read, one FIFO pop. */
  union { uint32_t raw; dwc2_grxstsp_t bits; } kps_rx_snapshot;
  kps_rx_snapshot.raw = dwc2->grxstsp;
  const dwc2_grxstsp_t grxstsp_bm = kps_rx_snapshot.bits;'''
    return re.sub(pattern, replacement, text), True


def patch_tinyusb_dwc2_host_fifo_layout(text):
    """RX occupies the bottom of FIFO RAM; TX must be allocated from its top."""
    if 'KPS_DWC2_HOST_FIFO_LAYOUT' in text:
        return text, False
    pattern = r'dfifo_top\s*-=\s*rxfsiz;\s*dwc2->grxfsiz\s*=\s*rxfsiz;'
    if len(re.findall(pattern, text)) > 1:
        raise ToolError('Ambiguous TinyUSB DWC2 Host FIFO allocation')
    fixed, count = re.subn(pattern,
        '/* KPS_DWC2_HOST_FIFO_LAYOUT: RX starts at 0; reserve it via the\n'
        '   * ptxfsiz budget, not by subtracting it twice from the TX top. */\n'
        '  dwc2->grxfsiz = rxfsiz;', text)
    return fixed, bool(count)


def tinyusb_endpoint_numbers(classes):
    """Compact allocation: disabled classes must not consume endpoint numbers."""
    selected = {name.upper() for name in classes}
    endpoints = {}
    number = 1
    for name in ('CDC', 'MSC', 'HID', 'MIDI', 'VENDOR'):
        if name in selected:
            endpoints[name] = number
            number += 2 if name == 'CDC' else 1
    return endpoints, number - 1


def tinyusb_templates(use_rtos, mode, classes, mcu_macro, use_rtthread=False):
    cls = {x.upper() for x in classes}
    device = mode in ('device', 'both')
    host = mode in ('host', 'both')
    if mode == 'both':
        rh_modes = ('#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)\n'
                    '#define CFG_TUSB_RHPORT1_MODE (OPT_MODE_HOST | OPT_MODE_FULL_SPEED)')
    else:
        rh_modes = '#define CFG_TUSB_RHPORT0_MODE (%s | OPT_MODE_FULL_SPEED)' % (
            'OPT_MODE_DEVICE' if device else 'OPT_MODE_HOST')
    config = '''\
#ifndef TUSB_CONFIG_H
#define TUSB_CONFIG_H

#define CFG_TUSB_MCU %(mcu)s
#define CFG_TUSB_OS %(os)s
#define CFG_TUSB_DEBUG 0
#define CFG_TUD_ENABLED %(device)d
#define CFG_TUH_ENABLED %(host)d
%(rh_modes)s
#define CFG_TUD_ENDPOINT0_SIZE 64
#define CFG_TUD_CDC %(cdc)d
#define CFG_TUD_MSC %(msc)d
#define CFG_TUD_HID %(hid)d
#define CFG_TUD_MIDI %(midi)d
#define CFG_TUD_VENDOR %(vendor)d
#define CFG_TUH_CDC %(hcdc)d
#define CFG_TUH_MSC %(hmsc)d
#define CFG_TUH_HID %(hhid)d
#define CFG_TUH_MIDI %(hmidi)d
#define CFG_TUH_VENDOR %(hvendor)d
#define CFG_TUD_CDC_RX_BUFSIZE 64
#define CFG_TUD_CDC_TX_BUFSIZE 64
#define CFG_TUD_MSC_EP_BUFSIZE 512
#define CFG_TUD_MIDI_RX_BUFSIZE 64
#define CFG_TUD_MIDI_TX_BUFSIZE 64

#endif /* TUSB_CONFIG_H */
''' % {'mcu': mcu_macro, 'os': 'OPT_OS_RTTHREAD' if use_rtthread else 'OPT_OS_FREERTOS' if use_rtos else 'OPT_OS_NONE',
       'device': 1 if device else 0, 'host': 1 if host else 0,
       'rh_modes': rh_modes,
       'cdc': 1 if 'CDC' in cls and device else 0, 'msc': 1 if 'MSC' in cls and device else 0,
       'hid': 1 if 'HID' in cls and device else 0, 'midi': 1 if 'MIDI' in cls and device else 0,
       'vendor': 1 if 'VENDOR' in cls and device else 0,
       'hcdc': 1 if 'CDC' in cls and host else 0, 'hmsc': 1 if 'MSC' in cls and host else 0,
       'hhid': 1 if 'HID' in cls and host else 0, 'hmidi': 1 if 'MIDI' in cls and host else 0,
       'hvendor': 1 if 'VENDOR' in cls and host else 0}
    header = '''\
#ifndef TINYUSB_APP_H
#define TINYUSB_APP_H
void TinyUSB_AppInit(void);
void TinyUSB_AppTask(void);
void TinyUSB_Platform_Init(void);
#endif /* TINYUSB_APP_H */
'''
    rtos_inc = '#include "FreeRTOS.h"\n#include "task.h"\n' if use_rtos else ''
    if use_rtthread:
        rtos_inc = '#include "rtthread.h"\n'
    init_calls = ''
    task_calls = ''
    if device:
        init_calls += '    (void)tud_init(0);\n'
        task_calls += '    tud_task_ext(%d, false);\n' % (1 if use_rtos else 0)
        if 'CDC' in cls:
            task_calls += '    TinyUSB_CdcEcho();\n'
    if host:
        init_calls += '    (void)tuh_init(%d);\n' % (1 if mode == 'both' else 0)
        task_calls += '    tuh_task_ext(%d, false);\n' % (1 if use_rtos else 0)
    if use_rtos:
        task_def = '''\
static void TinyUSB_RtosTask(void *argument)
{
    (void)argument;
    /* Start hardware/IRQs only after the scheduler is running. USB Host can
     * interrupt immediately when a device is already attached. */
    TinyUSB_Platform_Init();
%(init_calls)s
    for (;;) {
%(task_calls)s    }
}
'''
        start = ('    BaseType_t status = xTaskCreate(TinyUSB_RtosTask, "tinyusb", 512U, NULL,\n'
                 '                                      tskIDLE_PRIORITY + 3U, NULL);\n'
                 '    configASSERT(status == pdPASS);\n'
                 '    if (status != pdPASS) return;\n')
        if use_rtthread:
            task_def = ('static struct rt_thread usb_thread;\n'
                        'rt_align(RT_ALIGN_SIZE) static rt_uint8_t usb_stack[2048];\n' + task_def)
            start = ('    rt_err_t status = rt_thread_init(&usb_thread, "tinyusb", TinyUSB_RtosTask, RT_NULL,\n'
                     '                                      usb_stack, sizeof(usb_stack), 13, 10);\n'
                     '    RT_ASSERT(status == RT_EOK);\n'
                     '    if (status != RT_EOK) return;\n'
                     '    status = rt_thread_startup(&usb_thread);\n'
                     '    RT_ASSERT(status == RT_EOK);\n')
    else:
        task_def = ''
        start = ''
    source = '''\
/* TinyUSB application generated by keil_port_tool.py. */
#include "tinyusb_app.h"
#include "tusb.h"
#include <string.h>
%(rtos_inc)s
#if defined(__CC_ARM)
#define TUSB_APP_WEAK __weak
#elif defined(__GNUC__) || defined(__clang__)
#define TUSB_APP_WEAK __attribute__((weak))
#else
#define TUSB_APP_WEAK
#endif

#if CFG_TUD_CDC
static void TinyUSB_CdcEcho(void);
#endif
%(task_def)s
void TinyUSB_AppInit(void)
{
%(platform_init)s%(app_init_calls)s%(start)s}

void TinyUSB_AppTask(void)
{
%(task_calls)s}

TUSB_APP_WEAK void TinyUSB_Platform_Init(void)
{
    /* KPS_USER_ACTION / 用户接入：在此或用同名强函数配置真实 USB 硬件。
     * F407 USB FS needs a measured/configured 48 MHz clock, data cable and correct IRQ.
     * MSC 还须填写 ready/capacity/read10/write10；PC 和 MCU 不能同时写同一卷。
     * See docs/POST-PORTING.en.md / docs/POST-PORTING.zh-CN.md (TinyUSB). */
    /* Configure the USB clock, GPIO and IRQ for the selected controller.
     * TinyUSB owns this controller: do NOT also start HAL_PCD / USB_DEVICE.
     * IRQ handler must forward to tud_int_handler()/tuh_int_handler().
     * FreeRTOS: choose an IRQ priority permitted by
     * configMAX_SYSCALL_INTERRUPT_PRIORITY before enabling the interrupt.
     * Configure VBUS sensing to match the actual board wiring. */
}
''' % {'rtos_inc': rtos_inc,
       'task_def': task_def % {'task_calls': task_calls, 'init_calls': init_calls},
       'platform_init': '' if use_rtos else '    TinyUSB_Platform_Init();\n',
       'app_init_calls': '' if use_rtos else init_calls,
       'start': start, 'task_calls': task_calls}
    if device and 'CDC' in cls:
        source += '''\

/* Poll from the USB owner task, not an optional weak callback: ARMCC5's
 * linker can discard callbacks that have only weak references.
 * Leave RX queued until TX has room so a slow host cannot lose echo bytes. */
static void TinyUSB_CdcEcho(void)
{
    uint8_t buffer[64];
    for (uint8_t itf = 0; itf < CFG_TUD_CDC; ++itf) {
        uint32_t room = tud_cdc_n_write_available(itf);
        uint32_t count;
        if (room > sizeof(buffer)) room = sizeof(buffer);
        if (!tud_cdc_n_connected(itf) || !room) continue;
        count = tud_cdc_n_read(itf, buffer, room);
        if (count) (void)tud_cdc_n_write(itf, buffer, count);
        (void)tud_cdc_n_write_flush(itf);
    }
}
'''
    if device and 'MSC' in cls:
        source += '''\

TUSB_APP_WEAK void tud_msc_inquiry_cb(uint8_t lun, uint8_t vendor_id[8],
                                      uint8_t product_id[16], uint8_t product_rev[4])
{
    (void)lun; memcpy(vendor_id, "KPS     ", 8); memcpy(product_id, "MSC Storage     ", 16);
    memcpy(product_rev, "1.0 ", 4);
}
TUSB_APP_WEAK bool tud_msc_test_unit_ready_cb(uint8_t lun) { (void)lun; return false; }
TUSB_APP_WEAK void tud_msc_capacity_cb(uint8_t lun, uint32_t *block_count, uint16_t *block_size)
{ (void)lun; *block_count = 0; *block_size = 512; }
TUSB_APP_WEAK int32_t tud_msc_read10_cb(uint8_t lun, uint32_t lba, uint32_t offset,
                                       void *buffer, uint32_t bufsize)
{ (void)lun; (void)lba; (void)offset; (void)buffer; (void)bufsize; return -1; }
TUSB_APP_WEAK int32_t tud_msc_write10_cb(uint8_t lun, uint32_t lba, uint32_t offset,
                                        uint8_t *buffer, uint32_t bufsize)
{ (void)lun; (void)lba; (void)offset; (void)buffer; (void)bufsize; return -1; }
TUSB_APP_WEAK int32_t tud_msc_scsi_cb(uint8_t lun, uint8_t const scsi_cmd[16],
                                     void *buffer, uint16_t bufsize)
{ (void)lun; (void)scsi_cmd; (void)buffer; (void)bufsize; return -1; }
'''
    if device and 'HID' in cls:
        source += '''\

TUSB_APP_WEAK uint16_t tud_hid_get_report_cb(uint8_t itf, uint8_t report_id,
                                             hid_report_type_t report_type,
                                             uint8_t *buffer, uint16_t reqlen)
{ (void)itf; (void)report_id; (void)report_type; (void)buffer; (void)reqlen; return 0; }
TUSB_APP_WEAK void tud_hid_set_report_cb(uint8_t itf, uint8_t report_id,
                                        hid_report_type_t report_type,
                                        uint8_t const *buffer, uint16_t bufsize)
{ (void)itf; (void)report_id; (void)report_type; (void)buffer; (void)bufsize; }
'''

    enum_parts, desc_parts, total_parts = [], [], ['TUD_CONFIG_DESC_LEN']
    endpoints, _endpoint_count = tinyusb_endpoint_numbers(cls)
    interface = 0
    if 'CDC' in cls:
        enum_parts.extend(('ITF_NUM_CDC = %d' % interface, 'ITF_NUM_CDC_DATA'))
        interface += 2
        total_parts.append('TUD_CDC_DESC_LEN')
        ep = endpoints['CDC']
        desc_parts.append('    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 4, 0x%02x, 8, 0x%02x, 0x%02x, 64),' %
                          (0x80 | ep, ep + 1, 0x80 | (ep + 1)))
    if 'MSC' in cls:
        enum_parts.append('ITF_NUM_MSC = %d' % interface); interface += 1
        total_parts.append('TUD_MSC_DESC_LEN')
        ep = endpoints['MSC']
        desc_parts.append('    TUD_MSC_DESCRIPTOR(ITF_NUM_MSC, 5, 0x%02x, 0x%02x, 64),' %
                          (ep, 0x80 | ep))
    if 'HID' in cls:
        enum_parts.append('ITF_NUM_HID = %d' % interface); interface += 1
        total_parts.append('TUD_HID_DESC_LEN')
        ep = endpoints['HID']
        desc_parts.append('    TUD_HID_DESCRIPTOR(ITF_NUM_HID, 6, HID_ITF_PROTOCOL_KEYBOARD, '
                          'sizeof(s_hid_report_desc), 0x%02x, 16, 10),' % (0x80 | ep))
    if 'MIDI' in cls:
        enum_parts.extend(('ITF_NUM_MIDI = %d' % interface, 'ITF_NUM_MIDI_STREAMING'))
        interface += 2
        total_parts.append('TUD_MIDI_DESC_LEN')
        ep = endpoints['MIDI']
        desc_parts.append('    TUD_MIDI_DESCRIPTOR(ITF_NUM_MIDI, 7, 0x%02x, 0x%02x, 64),' %
                          (ep, 0x80 | ep))
    if 'VENDOR' in cls:
        enum_parts.append('ITF_NUM_VENDOR = %d' % interface); interface += 1
        total_parts.append('TUD_VENDOR_DESC_LEN')
        ep = endpoints['VENDOR']
        desc_parts.append('    TUD_VENDOR_DESCRIPTOR(ITF_NUM_VENDOR, 8, 0x%02x, 0x%02x, 64),' %
                          (ep, 0x80 | ep))
    enum_parts.append('ITF_NUM_TOTAL = %d' % interface)
    hid_decl = ''
    hid_callback = ''
    if 'HID' in cls:
        hid_decl = ('static uint8_t const s_hid_report_desc[] = { '
                    'TUD_HID_REPORT_DESC_KEYBOARD() };\n')
        hid_callback = ('uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance)\n'
                        '{ (void)instance; return s_hid_report_desc; }\n')
    descriptors = '''\
/* TinyUSB device descriptors generated by keil_port_tool.py.
 * KPS_USER_ACTION / 用户接入：示例 VID/PID 不是产品授权；修改产品 ID 与序列号。
 * Default HID is keyboard only; match report descriptors to actual reports.
 * Edit VID/PID, strings, endpoint allocation and class callbacks for the product.
 */
#include "tusb.h"
#include <string.h>

#if CFG_TUD_ENABLED
#define USB_VID 0xCafe
#define USB_PID (0x4000 | (CFG_TUD_CDC << 0) | (CFG_TUD_MSC << 1) | (CFG_TUD_HID << 2) | (CFG_TUD_MIDI << 3) | (CFG_TUD_VENDOR << 4))
static tusb_desc_device_t const s_device_desc = {
    .bLength = sizeof(tusb_desc_device_t), .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200, .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON, .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE, .idVendor = USB_VID, .idProduct = USB_PID,
    .bcdDevice = 0x0100, .iManufacturer = 1, .iProduct = 2, .iSerialNumber = 3,
    .bNumConfigurations = 1
};
uint8_t const *tud_descriptor_device_cb(void) { return (uint8_t const *)&s_device_desc; }

%(hid_decl)s
enum { %(enum_parts)s };
#define CONFIG_TOTAL_LEN (%(total_len)s)
static uint8_t const s_config_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 100),
%(desc_parts)s
};
uint8_t const *tud_descriptor_configuration_cb(uint8_t index)
{ (void)index; return s_config_desc; }
%(hid_callback)s

static char const *s_strings[] = { (const char[]){0x09, 0x04}, "Keil Port Studio",
                                   "TinyUSB Device", "000001", "CDC", "MSC", "HID",
                                   "MIDI", "Vendor" };
static uint16_t s_string_desc[32];
uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
    (void)langid;
    uint8_t count;
    if (index == 0) { memcpy(&s_string_desc[1], s_strings[0], 2); count = 1; }
    else {
        if (index >= sizeof(s_strings) / sizeof(s_strings[0])) return NULL;
        count = (uint8_t)strlen(s_strings[index]); if (count > 31) count = 31;
        for (uint8_t i = 0; i < count; ++i) s_string_desc[1 + i] = s_strings[index][i];
    }
    s_string_desc[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * count + 2));
    return s_string_desc;
}
#endif
''' % {'hid_decl': hid_decl, 'enum_parts': ', '.join(enum_parts),
       'total_len': ' + '.join(total_parts), 'desc_parts': '\n'.join(desc_parts),
       'hid_callback': hid_callback}
    return config, header, source, descriptors


def do_tinyusb(proj, opts, rep):
    mode = tinyusb_mode_value(getattr(opts, 'tinyusb_mode', 'device'))
    classes = []
    for value in getattr(opts, 'tinyusb_classes', None) or ('CDC',):
        name = str(value).upper()
        if name not in TINYUSB_CLASSES:
            raise ToolError('未知 TinyUSB 类: %s' % name)
        if name not in classes:
            classes.append(name)
    # These STM32F4 parts have four FS IN endpoints including EP0.
    # Generated device mode always uses RHPORT0 (FS). Do not emit descriptors
    # that compile but cannot be opened by the physical controller.
    _endpoints, endpoint_count = tinyusb_endpoint_numbers(classes)
    if (mode in ('device', 'both') and endpoint_count > 3 and
            (proj.device() or '').upper().startswith(('STM32F405', 'STM32F407',
                                                     'STM32F415', 'STM32F417'))):
        raise ToolError('当前 STM32F405/407/415/417 USB FS 只有 3 个非控制 IN 端点；'
                        '所选设备类需要 %d 个，请减少组合（例如 CDC+MSC 或 CDC+HID）' %
                        endpoint_count)
    requested = locate_tinyusb_root(getattr(opts, 'tinyusb', None))
    if requested is None and getattr(opts, 'tinyusb', None):
        raise ToolError('所选目录中未找到 TinyUSB src/tusb.c')
    if requested is None:
        top = ensure_tinyusb_sdk(proj, opts, rep)
        requested = locate_tinyusb_root(top) if top else None
    if requested is None:
        if getattr(opts, 'dry_run', False):
            return
        raise ToolError('未找到 TinyUSB；可用 --tinyusb auto 自动下载')
    requested = Path(requested).resolve()
    root, project_root = plan_project_library_copy(
        proj, requested, 'TinyUSB', locate_tinyusb_root, rep)
    root, project_root = Path(root).resolve(), Path(project_root).resolve()
    use_rtthread = project_uses_rtthread(proj)
    use_rtos = project_uses_freertos(proj) or use_rtthread
    if use_rtos:
        osal_name = 'osal_rtthread.h' if use_rtthread else 'osal_freertos.h'
        osal_header = root / 'src' / 'osal' / osal_name
        if not osal_header.is_file():
            raise ToolError('TinyUSB 当前版本缺少 src/osal/%s，无法提供线程安全适配' % osal_name)
        rep.notes.append('TinyUSB OSAL 使用头文件实现: src/osal/%s（无需对应 .c）' % osal_name)
    mcu = tinyusb_mcu_macro(proj)
    if not mcu:
        raise ToolError('无法从 Device 自动确定 TinyUSB CFG_TUSB_MCU；当前先支持 STM32 系列')
    option_header = root / 'src' / 'tusb_option.h'
    if option_header.is_file() and not re.search(
            r'(?m)^\s*#\s*define\s+' + re.escape(mcu) + r'\b', read_source_text(option_header)):
        raise ToolError('当前 TinyUSB 版本不支持芯片宏 %s；请升级 TinyUSB 或改用兼容版本' % mcu)
    version = tinyusb_version(root)
    if not proj.any_ac6() and version and version[:2] > (0, 17):
        if not (mode in ('host', 'both') and version == (0, 18, 0)):
            rep.warnings.append('当前 AC5 工程显式选择了 TinyUSB %d.%d.%d；若遇到编译器兼容问题，'
                                '建议改用 auto（Device 0.17.0 / Host 0.18.0）。'
                                ' / AC5 compatibility: use auto for the selected role.' % version)
    if not proj.any_ac6():
        compiler_header = root / 'src' / 'common' / 'tusb_compiler.h'
        if compiler_header.is_file():
            patched, changed = patch_tinyusb_armcc5_compiler(read_source_text(compiler_header))
            if changed:
                target_header = project_root / 'src' / 'common' / 'tusb_compiler.h'
                rep.gen_files.append((target_header, patched,
                                      'TinyUSB ARMCC5 字节序内建函数兼容修复'))
                rep.notes.append('已为 ARMCC5 修复 TinyUSB __builtin_bswap16/32 链接兼容性')
        register_header = root / 'src' / 'portable' / 'synopsys' / 'dwc2' / 'dwc2_type.h'
        if mode in ('host', 'both') and register_header.is_file():
            patched, changed = patch_tinyusb_armcc5_dwc2_registers(read_source_text(register_header))
            if changed:
                rep.gen_files.append((project_root / register_header.relative_to(root), patched,
                                      'TinyUSB AC5 DWC2 32-bit MMIO register access'))
            for driver in ('hcd_dwc2.c', 'dcd_dwc2.c'):
                driver_path = register_header.with_name(driver)
                if driver_path.is_file():
                    fixed, touched = patch_tinyusb_dwc2_fifo_snapshot(read_source_text(driver_path))
                    if touched:
                        rep.gen_files.append((project_root / driver_path.relative_to(root), fixed,
                                              'TinyUSB DWC2 single-read RX FIFO snapshot'))
    if mode in ('host', 'both'):
        hcd = root / 'src' / 'portable' / 'synopsys' / 'dwc2' / 'hcd_dwc2.c'
        if hcd.is_file():
            destination = project_root / hcd.relative_to(root)
            # Compose with the AC5 RX snapshot fix, not a second stale write.
            planned = next((i for i, item in enumerate(rep.gen_files) if item[0] == destination), None)
            current = rep.gen_files[planned][1] if planned is not None else read_source_text(hcd)
            fixed, touched = patch_tinyusb_dwc2_host_fifo_layout(current)
            if touched:
                item = (destination, fixed, 'TinyUSB DWC2 non-overlapping Host FIFO layout')
                if planned is None:
                    rep.gen_files.append(item)
                else:
                    rep.gen_files[planned] = item
    unsupported = []
    for name in classes:
        folder = root / 'src' / 'class' / name.lower()
        if mode in ('device', 'both') and not any(folder.glob('*_device.c')):
            unsupported.append('%s Device' % name)
        if mode in ('host', 'both') and not any(folder.glob('*_host.c')):
            unsupported.append('%s Host' % name)
    if unsupported:
        raise ToolError('当前 TinyUSB 版本没有这些协议类实现: %s；请取消对应选项' %
                        ', '.join(unsupported))
    available = tinyusb_source_files(root, proj, mode, classes)
    selected_rel = _selected_relative(getattr(opts, 'tinyusb_files', None), (requested, root))
    sources = [p for p in available if _component_chosen(p, root, selected_rel)]
    required = {'tusb.c', 'tusb_fifo.c'}
    if mode in ('device', 'both'):
        required.add('usbd.c')
    if mode in ('host', 'both'):
        required.add('usbh.c')
    missing = sorted(required - {p.name for p in sources})
    if missing:
        raise ToolError('TinyUSB 核心文件不能取消: %s' % ', '.join(missing))
    validate_tinyusb_controller_sources(sources, mode)
    all_known = tinyusb_source_files(root, proj, 'both', TINYUSB_CLASSES)
    selected_paths = {p.resolve() for p in sources}
    removed = 0
    for candidate in all_known:
        if candidate.resolve() not in selected_paths:
            target = project_root / candidate.relative_to(root)
            removed += proj.remove_file(rel_or_abs(target, proj.dir))
    if removed:
        rep.notes.append('已从工程移除 %d 个当前未选择的 TinyUSB 文件' % removed)
    for source in sources:
        target = project_root / source.relative_to(root)
        rel = source.relative_to(root / 'src')
        group = 'TinyUSB/' + '/'.join(rel.parts[:-1] or ('Core',))
        if proj.add_file(group, source.name, 1, rel_or_abs(target, proj.dir)):
            rep.files.append((group, source.name))
    proj.add_include_path(rel_or_abs(project_root / 'src', proj.dir), rep)
    for source in sources:
        if 'portable' in source.parts:
            proj.add_include_path(rel_or_abs(project_root / source.parent.relative_to(root), proj.dir), rep)
    proj.enable_c99_gnu(rep)
    config, header, app, descriptors = tinyusb_templates(use_rtos, mode, classes, mcu, use_rtthread)
    if version and version >= (0, 18, 0):
        use_hal = any(re.fullmatch(r'stm32[a-z0-9]+_hal\.c',
                                   Path(record['path'].replace('\\', '/')).name, re.I)
                      for record in proj.file_records())
        app += tinyusb_timebase_template(use_rtos, use_rtthread, use_hal)
        if not (use_rtos or use_hal):
            rep.warnings.append('TinyUSB >=0.18: 请实现 TinyUSB_Platform_Millis() 毫秒时基。'
                                ' / Implement TinyUSB_Platform_Millis() before building.')
    content = project_content_root(proj)
    cfg = content / 'Config' / 'TinyUSB'
    src_dir, inc_dir = content / 'Core' / 'Src', content / 'Core' / 'Inc'
    if not src_dir.is_dir():
        src_dir = content / 'Application' / 'TinyUSB'
    if not inc_dir.is_dir():
        inc_dir = src_dir
    generated = ((cfg / 'tusb_config.h', config, 'TinyUSB 工程配置'),
                 (inc_dir / 'tinyusb_app.h', header, 'TinyUSB 应用头文件'),
                 (src_dir / 'tinyusb_app.c', app, 'TinyUSB 应用与任务'),
                 (src_dir / 'usb_descriptors.c', descriptors, 'TinyUSB Device 描述符'))
    tinyusb_managed = (
        'CFG_TUSB_MCU', 'CFG_TUSB_OS', 'CFG_TUD_ENABLED', 'CFG_TUH_ENABLED',
        'CFG_TUSB_RHPORT0_MODE', 'CFG_TUSB_RHPORT1_MODE',
        'CFG_TUD_CDC', 'CFG_TUD_MSC', 'CFG_TUD_HID', 'CFG_TUD_MIDI',
        'CFG_TUD_VENDOR', 'CFG_TUH_CDC', 'CFG_TUH_MSC', 'CFG_TUH_HID',
        'CFG_TUH_MIDI', 'CFG_TUH_VENDOR', 'CFG_TUD_CDC_RX_BUFSIZE',
        'CFG_TUD_CDC_TX_BUFSIZE', 'CFG_TUD_MSC_EP_BUFSIZE',
        'CFG_TUD_MIDI_RX_BUFSIZE', 'CFG_TUD_MIDI_TX_BUFSIZE')
    for path, value, desc in generated:
        if path.name == 'usb_descriptors.c' and mode == 'host':
            continue
        if path.name == 'tusb_config.h':
            _plan_managed_config(path, value, tinyusb_managed, desc, rep)
        else:
            refresh = path.name in ('tinyusb_app.c', 'usb_descriptors.c')
            _plan_owned_template(path, value, 'generated by keil_port_tool.py', desc, rep,
                                 refresh_owned=refresh)
        if path.suffix == '.c' and proj.add_file('TinyUSB/Application', path.name, 1,
                                                rel_or_abs(path, proj.dir)):
            rep.files.append(('TinyUSB/Application', path.name))
    proj.add_include_path(rel_or_abs(cfg, proj.dir), rep)
    proj.add_include_path(rel_or_abs(inc_dir, proj.dir), rep)
    candidates = ((content / 'RTThread/App/rtthread_app.c',) if use_rtthread else ()) + (content / 'Core' / 'Src' / 'freertos_app.c',
                  content / 'Core' / 'Src' / 'freertos.c', content / 'Core' / 'Src' / 'main.c',
                  proj.dir / 'Application' / 'freertos_app.c', proj.dir / 'main.c')
    entry = next((p for p in candidates if p.is_file() or
                  os.path.normcase(str(p.resolve())) in getattr(proj, '_planned_generated_files', {})), None)
    if entry:
        patched, changed = _patch_component_init(_planned_text(proj, entry), 'tinyusb_app.h',
                                                 'TinyUSB_AppInit();', use_rtos)
        if not use_rtos:
            patched, loop_changed = _patch_main_loop_call(patched, 'TinyUSB_AppTask();')
            changed = changed or loop_changed
        if changed:
            rep.gen_files.append((entry, patched, '自动初始化 TinyUSB'))
        if 'TinyUSB_AppInit();' not in patched:
            rep.warnings.append('应用入口缺少标准插入点；请自行调用 TinyUSB_AppInit()')
        if not use_rtos and 'TinyUSB_AppTask();' not in patched:
            rep.warnings.append('裸机 main() 缺少 USER CODE BEGIN 3；请在主循环调用 TinyUSB_AppTask()')
    else:
        rep.warnings.append('未找到应用入口；请自行调用 TinyUSB_AppInit()')
    if mode != 'host' and 'MSC' in classes:
        rep.warnings.append('MSC 默认回调故意返回未就绪；必须连接实际块设备后主机才会挂载磁盘')
    rep.notes.append('TinyUSB: %s%s，%s，类: %s' %
                     (mcu, ('，版本 %d.%d.%d' % version) if version else '',
                      mode, ', '.join(classes)))
    rep.notes.append('必须在 TinyUSB_Platform_Init() 中启用 USB 时钟/引脚/中断；裸机主循环调用 TinyUSB_AppTask()')


# ===========================================================================
# 安全事务、差异预览与组件清单
# ===========================================================================
STATE_DIR_NAME = '.keil-port-tool'
MANIFEST_VERSION = 3


def _state_dir(proj):
    return project_content_root(proj) / STATE_DIR_NAME


def _relative_project_path(proj, path):
    root = project_content_root(proj).resolve()
    path = Path(path).resolve()
    try:
        return str(path.relative_to(root)).replace('\\', '/')
    except ValueError:
        return None


def _resolve_manifest_path(proj, value):
    path = Path(value)
    return path if path.is_absolute() else project_content_root(proj) / path


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return copy.deepcopy(default)


class ProjectTransaction:
    """项目内事务快照。任何写入失败时恢复原文件并移除本次新建内容。"""
    def __init__(self, proj, action, components):
        self.proj = proj
        self.root = project_content_root(proj).resolve()
        self.id = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.dir = _state_dir(proj) / 'transactions' / self.id
        self.before = self.dir / 'before'
        self.meta_path = self.dir / 'transaction.json'
        self.meta = {
            'version': MANIFEST_VERSION, 'id': self.id, 'action': action,
            'components': list(components), 'status': 'preparing',
            'project': _relative_project_path(proj, proj.path),
            'targets': proj.target_names(), 'created_files': [],
            'created_dirs': [], 'snapshots': [], 'dir_snapshots': [],
        }

    def _rel(self, path):
        rel = _relative_project_path(self.proj, path)
        if rel is None or rel.startswith('../'):
            raise ToolError('事务拒绝操作工程目录之外的路径: %s' % path)
        return rel

    def snapshot(self, path):
        path = Path(path).resolve()
        rel = self._rel(path)
        if any(item['path'] == rel for item in self.meta['snapshots']):
            return
        if path.is_file():
            dst = self.before / Path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(path), str(dst))
            self.meta['snapshots'].append({'path': rel, 'backup': str(Path('before') / rel)})
        elif not path.exists() and rel not in self.meta['created_files']:
            self.meta['created_files'].append(rel)

    def created_dir(self, path):
        rel = self._rel(path)
        if rel not in self.meta['created_dirs']:
            self.meta['created_dirs'].append(rel)

    def snapshot_dir(self, path):
        path = Path(path).resolve()
        rel = self._rel(path)
        if any(item['path'] == rel for item in self.meta['dir_snapshots']):
            return
        if path.is_dir():
            backup_rel = Path('before_dirs') / rel
            destination = self.dir / backup_rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(path), str(destination))
            self.meta['dir_snapshots'].append({'path': rel, 'backup': str(backup_rel)})

    def save_meta(self, status=None, error=None):
        if status:
            self.meta['status'] = status
        if error:
            self.meta['error'] = str(error)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2) + '\n',
                                  encoding='utf-8')

    def rollback(self, error=None):
        # 只处理经过 _rel 验证、明确位于项目根目录下的路径。
        for rel in sorted(self.meta['created_files'], key=len, reverse=True):
            path = self.root / Path(rel)
            if path.is_file():
                path.unlink()
        for rel in sorted(self.meta['created_dirs'], key=len, reverse=True):
            path = self.root / Path(rel)
            if path.is_dir() and path != self.root and self.root in path.parents:
                shutil.rmtree(str(path))
        for item in self.meta['snapshots']:
            source = self.dir / Path(item['backup'])
            destination = self.root / Path(item['path'])
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(destination))
        for item in self.meta.get('dir_snapshots', []):
            source = self.dir / Path(item['backup'])
            destination = self.root / Path(item['path'])
            if source.is_dir():
                if destination.is_dir():
                    shutil.rmtree(str(destination))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(str(source), str(destination))
        self.save_meta('failed_rolled_back', error)


def prepare_transaction(proj, reports, action='install', components=()):
    tx = ProjectTransaction(proj, action, components)
    tx.snapshot(proj.path)
    manifest = _state_dir(proj) / 'manifest.json'
    tx.snapshot(manifest)
    for rep in reports:
        for _source, destination, _desc in rep.copy_trees:
            if not Path(destination).exists():
                tx.created_dir(destination)
        for path, _content, _desc in rep.gen_files:
            tx.snapshot(path)
        for path, _desc in rep.obsolete_files:
            tx.snapshot(path)
    tx.save_meta('prepared')
    return tx


def _installed_file_record(proj, path):
    path = Path(path)
    if not path.is_file():
        return None
    rel = _relative_project_path(proj, path)
    if rel is None:
        return None
    return {'path': rel, 'sha256': sha256_file(path)}


def _component_version(component, root):
    """从实际源码中尽量提取版本；失败时明确返回 unknown，不凭目录名猜测。"""
    root = Path(root)
    try:
        if component == 'freertos':
            value = freertos_kernel_version(root)
            return '.'.join(str(x) for x in value) if value else 'unknown'
        if component == 'rtthread':
            text = read_source_text(root / 'include/rtdef.h')
            values = [re.search(r'#define\s+RT_VERSION_' + key + r'\s+(\d+)', text)
                      for key in ('MAJOR', 'MINOR', 'PATCH')]
            return '.'.join(v.group(1) for v in values) if all(values) else 'unknown'
        if component == 'lvgl':
            value = lvgl_version(root)
            return str(value) if value else 'unknown'
        if component == 'tinyusb':
            value = tinyusb_version(root)
            return '.'.join(str(x) for x in value) if value else 'unknown'
        candidates = {
            'fatfs': (root / 'ff.h', r'FF_DEFINED\s+(\d+)'),
            'littlefs': (root / 'lfs.h', r'LFS_VERSION\s+0x([0-9A-Fa-f]+)'),
            'lwip': (root / 'src' / 'include' / 'lwip' / 'init.h',
                     r'LWIP_VERSION_STRING\s+"([^"]+)"'),
        }
        if component in candidates:
            path, pattern = candidates[component]
            if path.is_file():
                match = re.search(pattern, read_source_text(path))
                if match:
                    return match.group(1)
    except (OSError, ValueError):
        pass
    return 'unknown'




def _manifest_source_unpatches(proj, component, data):
    if 'source_edits' not in data or data.get('source_edits_complete') is False:
        if any(not record.get('created') for record in data.get('generated_files', [])):
            raise ToolError('旧安装记录没有源码补丁归属，不能安全自动卸载 %s；请预览事务回滚或人工迁移' % component)
        return []
    values = {}
    for record in reversed(data['source_edits']):
        path = _resolve_manifest_path(proj, record['path'])
        if not path.is_file():
            raise ToolError('卸载停止：已记录的源码文件不存在: %s' % path)
        old, current = values.get(path, (None, None))
        if old is None:
            old = current = read_source_text(path)
        values[path] = (old, _reverse_owned_hunks(current, record['hunks'], path))
    return [(path, old, new) for path, (old, new) in values.items() if old != new]


def update_component_manifest(proj, reports, transaction):
    state = _state_dir(proj)
    state.mkdir(parents=True, exist_ok=True)
    manifest_path = state / 'manifest.json'
    manifest = _load_json(manifest_path, {'version': MANIFEST_VERSION, 'components': {}})
    manifest['version'] = MANIFEST_VERSION
    manifest.setdefault('components', {})
    records = proj.file_records()
    now = datetime.now().isoformat(timespec='seconds')
    created_files = set(transaction.meta.get('created_files', []))
    for rep in reports:
        if rep.key == 'project_settings' or rep.key not in TASK_FUNCS or not rep.has_changes():
            continue
        project_files = []
        for group, name in rep.files:
            for record in records:
                if record['group'] == group and record['name'] == name:
                    project_files.append(record)
        generated = []
        for path, _content, _desc in rep.gen_files:
            record = _installed_file_record(proj, path)
            if record:
                record['created'] = record['path'] in created_files
                generated.append(record)
        copied = []
        source_hashes = {}
        for source, destination, _desc in rep.copy_trees:
            destination = Path(destination)
            rel = _relative_project_path(proj, destination)
            if rel is not None and destination.is_dir():
                source_key = os.path.normcase(str(Path(source).resolve()))
                source_hashes[source_key] = sha256_tree(source)
                copied.append({'path': rel, 'sha256': sha256_tree(destination),
                               'source_path': str(Path(source).resolve()),
                               'source_sha256': source_hashes[source_key]})
        sources = []
        for item in rep.sources:
            record = dict(item)
            source_path = record.get('path')
            if source_path and Path(source_path).is_dir():
                source_key = os.path.normcase(str(Path(source_path).resolve()))
                record['tree_sha256'] = source_hashes.get(source_key) or sha256_tree(source_path)
                record['version'] = _component_version(rep.key, source_path)
            sources.append(record)
        previous = manifest['components'].get(rep.key, {})

        # 重复运行时 rep 只记录“本轮新增项”。不能因此抹掉此前的组件归属，
        # 否则后续卸载会漏掉已经存在的文件、路径和宏。
        current_file_keys = {(item['target'], proj.norm_file(item['path'])) for item in records}
        merged_project_files = []
        seen_project_files = set()
        for item in list(previous.get('project_files', [])) + project_files:
            key = (item.get('target', ''), proj.norm_file(item.get('path', '')))
            if key in current_file_keys and key not in seen_project_files:
                merged_project_files.append(item)
                seen_project_files.add(key)

        current_includes = proj.include_dirs_abs()
        merged_includes = []
        for entry in list(previous.get('include_paths', [])) + list(rep.inc):
            value = str(entry).replace('/', '\\')
            absolute = value if os.path.isabs(value) else os.path.join(str(proj.dir), value)
            if (os.path.normcase(os.path.normpath(absolute)) in current_includes and
                    entry not in merged_includes):
                merged_includes.append(entry)

        current_defines = _project_define_tokens(proj)
        merged_defines = []
        for macro in list(previous.get('defines', [])) + list(rep.defines):
            if macro in current_defines and macro not in merged_defines:
                merged_defines.append(macro)

        generated_by_path = {}
        for item in previous.get('generated_files', []):
            path = _resolve_manifest_path(proj, item.get('path', ''))
            if path.exists():
                generated_by_path[item.get('path', '')] = item
        for item in generated:
            old_record = generated_by_path.get(item.get('path', ''))
            if old_record and old_record.get('created'):
                item['created'] = True
            generated_by_path[item.get('path', '')] = item

        copied_by_path = {}
        for item in previous.get('copied_dirs', []):
            path = _resolve_manifest_path(proj, item.get('path', ''))
            if path.is_dir():
                copied_by_path[item.get('path', '')] = item
        for item in copied:
            copied_by_path[item.get('path', '')] = item

        source_by_key = {}
        for item in list(previous.get('sources', [])) + sources:
            key = item.get('path') or item.get('url') or json.dumps(item, sort_keys=True)
            source_by_key[key] = item

        manifest['components'][rep.key] = {
            'installed_at': now, 'transaction': transaction.id,
            'tool_version': TOOL_VERSION,
            'targets': list(dict.fromkeys(list(previous.get('targets', [])) +
                                          proj.target_names())),
            'project_files': merged_project_files,
            'include_paths': merged_includes,
            'defines': merged_defines,
            'generated_files': list(generated_by_path.values()),
            'copied_dirs': list(copied_by_path.values()),
            'sources': list(source_by_key.values()),
            'source_edits': list(previous.get('source_edits', [])) + rep.source_edits,
            'source_edits_complete': (previous.get('source_edits_complete', 'source_edits' in previous)
                                      if previous else True),
        }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                             encoding='utf-8')


def build_diff_preview(proj, reports, max_lines=1800):
    """生成工程 XML 和待写配置/源码的 unified diff。"""
    blocks = []
    if proj.dirty:
        after_bytes = proj.serialize()
        payload = after_bytes[len(proj.xml_bom):] if proj.xml_bom else after_bytes
        after = payload.decode(proj.xml_encoding)
        diff = difflib.unified_diff(
            proj.original_text.splitlines(True), after.splitlines(True),
            fromfile=str(proj.path) + ' (修改前)', tofile=str(proj.path) + ' (修改后)')
        blocks.extend(diff)
    latest = {}
    for rep in reports:
        for path, content, desc in rep.gen_files:
            latest[os.path.normcase(str(Path(path).resolve()))] = (Path(path), content, desc)
    for path, content, desc in latest.values():
        before = read_source_text(path) if path.is_file() else ''
        blocks.append('\n### %s: %s\n' % (desc, path))
        blocks.extend(difflib.unified_diff(
            before.splitlines(True), str(content).splitlines(True),
            fromfile=str(path) + (' (修改前)' if before else ' (新文件)'),
            tofile=str(path) + ' (修改后)'))
    for rep in reports:
        for source, destination, desc in rep.copy_trees:
            blocks.append('\n### 新目录: %s\n    %s -> %s\n' % (desc, source, destination))
        for path, desc in rep.obsolete_files:
            blocks.append('\n### 停用文件: %s\n    %s\n' % (desc, path))
    if not blocks:
        return '没有文本差异。\n'
    if max_lines and len(blocks) > max_lines:
        omitted = len(blocks) - max_lines
        blocks = blocks[:max_lines] + ['\n... 已省略 %d 行差异，请使用 --diff-file 导出完整内容。\n' % omitted]
    return ''.join(blocks)


# ===========================================================================
# 任务调度: 收集报告 -> 确认 -> 事务写入
# ===========================================================================
TASK_FUNCS = {'add_files': do_add_files, 'freertos': do_freertos, 'rtthread': do_rtthread,
              'lvgl': do_lvgl, 'fatfs': do_fatfs,
              'segger_rtt': do_segger_rtt, 'littlefs': do_littlefs,
              'cmsis_dsp': do_cmsis_dsp, 'rtos_guard': do_rtos_guard,
              'lwip': do_lwip, 'tinyusb': do_tinyusb,
              'project_settings': do_project_settings}


def run_tasks(proj, tasks, opts):
    if ('rtthread' in tasks and ('freertos' in tasks or project_uses_freertos(proj)) or
            'freertos' in tasks and project_uses_rtthread(proj)):
        raise ToolError('FreeRTOS 与 RT-Thread 不能同时安装到同一工程；请使用独立工程副本')
    proj._planning_failed = False
    original_root = copy.deepcopy(proj.root)
    original_dirty = proj.dirty
    original_targets = proj.target_names()
    original_planned = dict(getattr(proj, '_planned_generated_files', {}))
    reports = []
    fatal_error = False
    set_progress(2, '正在分析工程')
    for index, t in enumerate(tasks):
        set_progress(5 + int(35 * index / max(1, len(tasks))),
                     '正在规划：%s' % Report.TITLES.get(t, t))
        rep = Report(t)
        root_before = copy.deepcopy(proj.root)
        dirty_before = proj.dirty
        selected_targets = proj.target_names()
        try:
            TASK_FUNCS[t](proj, opts, rep)
        except ToolError as e:
            proj.root = root_before
            proj.all_targets = proj.root.findall('Targets/Target') or proj.root.findall('Target')
            proj.targets = [target for target in proj.all_targets
                            if target.findtext('TargetName', '') in selected_targets]
            proj.dirty = dirty_before
            rep = Report(t)
            rep.warnings.append('该组件未规划任何写入: %s' % e)
            fatal_error = True
            log('[错误] %s: %s' % (Report.TITLES.get(t, t), e))
        except Exception:
            proj.root = root_before
            proj.all_targets = proj.root.findall('Targets/Target') or proj.root.findall('Target')
            proj.targets = [target for target in proj.all_targets
                            if target.findtext('TargetName', '') in selected_targets]
            proj.dirty = dirty_before
            rep = Report(t)
            rep.warnings.append('发生未预期异常，已撤销本组件规划；本次执行停止')
            fatal_error = True
            log('[异常] %s' % Report.TITLES.get(t, t))
            traceback.print_exc()
        reports.append(rep)
        planned = getattr(proj, '_planned_generated_files', {})
        for path, content, _desc in rep.gen_files:
            path = Path(path).resolve()
            key = os.path.normcase(str(path))
            before = planned.get(key)
            if before is None and path.is_file():
                before = read_source_text(path)
            if before is not None and before != content:
                rel = _relative_project_path(proj, path)
                if rel is None or rel.startswith('../'):
                    raise ToolError('源码补丁超出工程边界: %s' % path)
                rep.source_edits.append({'path': rel, 'hunks': _edit_hunks(before, content)})
            planned[key] = content
        proj._planned_generated_files = planned

    ram = proj.ram_size_bytes()
    memory_heavy = [name for name in ('freertos', 'rtthread', 'lvgl', 'lwip', 'tinyusb')
                    if name in tasks]
    if ram and ram <= 64 * 1024 and len(memory_heavy) >= 2:
        target_rep = next((item for item in reversed(reports)
                           if item.key in memory_heavy), reports[-1] if reports else None)
        if target_rep:
            target_rep.warnings.append(
                '所选 Target 仅 %d KB RAM，同时启用了 %s。工具会为新配置采用保守默认值，'
                '但是否能链接仍取决于任务栈、LVGL 缓冲区和网络池；请在 Keil map 文件中复核。' %
                (ram // 1024, '、'.join(memory_heavy)))

    for rep in reports:
        rep.print()

    if fatal_error:
        proj._planning_failed = True
        proj.root = original_root
        proj.all_targets = proj.root.findall('Targets/Target') or proj.root.findall('Target')
        proj.targets = [target for target in proj.all_targets
                        if target.findtext('TargetName', '') in original_targets]
        proj.dirty = original_dirty
        proj._planned_generated_files = original_planned
        warn('组件规划失败，为避免产生不完整工程，本次未写入任何文件。')
        return False

    if not proj.dirty and not any(r.copy_trees or r.gen_files or r.obsolete_files for r in reports):
        info('没有需要写入的更改, 工程保持原样。')
        return False
    diff_text = build_diff_preview(proj, reports)
    diff_file = getattr(opts, 'diff_file', None)
    if diff_file:
        Path(diff_file).write_text(build_diff_preview(proj, reports, None), encoding='utf-8')
        info('完整差异已导出: %s' % Path(diff_file).resolve())
    if getattr(opts, 'dry_run', False):
        log('\n========== 详细差异预览 ==========\n' + diff_text)
        info('DRY-RUN 模式: 仅预览, 未写入任何文件。')
        return False
    log('[提醒] 如果该工程正在 Keil 中打开, 请先关闭工程再运行本脚本 (或运行后重新打开); '
         '否则 Keil 可能会用内存里的旧内容覆盖磁盘上的修改。')
    preview_callback = getattr(opts, 'preview_callback', None)
    if preview_callback:
        if not preview_callback(diff_text):
            info('已取消, 未写入任何文件。')
            return False
    elif not getattr(opts, 'yes', False):
        log('\n========== 详细差异预览 ==========\n' + diff_text)
        if not ask_yn('确认将以上更改写入工程 (写入前会自动备份)?', True):
            info('已取消, 未写入任何文件。')
            return False

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    tx = prepare_transaction(proj, reports, 'install', tasks)
    try:
        for rep_index, rep in enumerate(reports):
            set_progress(45 + int(40 * rep_index / max(1, len(reports))),
                         '正在写入：%s' % Report.TITLES.get(rep.key, rep.key))
            for source, destination, desc in rep.copy_trees:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    log('[已跳过] 工程内目录已存在，不覆盖: %s' % destination)
                    continue
                shutil.copytree(str(source), str(destination),
                                ignore=shutil.ignore_patterns('.git', '__pycache__'))
                log('[已复制] %s: %s -> %s' % (desc, source, destination))
            for path, desc in rep.obsolete_files:
                if not path.is_file():
                    continue
                obsolete_bak = path.with_name(path.name + '.obsolete_bak_' + timestamp)
                tx.snapshot(obsolete_bak)
                path.replace(obsolete_bak)
                log('[已停用] %s: %s -> %s' % (desc, path, obsolete_bak))
            for path, content, desc in rep.gen_files:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_file():
                    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                    file_bak = path.with_name(path.name + '.bak_' + ts)
                    shutil.copy2(str(path), str(file_bak))
                    log('[备份] 原文件已备份到: %s' % file_bak)
                path.write_bytes(encode_preserving_format(path, content))
                log('[已生成] %s -> %s' % (desc, path))
        if proj.dirty:
            bak = proj.save()
            log('[备份] 原工程已备份到: %s' % bak)
            log('[已保存] %s' % proj.path)
        update_component_manifest(proj, reports, tx)
        tx.save_meta('success')
    except Exception as e:
        tx.rollback(e)
        raise ToolError('写入失败，已自动恢复事务开始前的工程状态: %s' % e)
    info('完成! 事务编号 %s；可在“安全与恢复”中卸载或回滚。' % tx.id)
    info('请在 Keil 中重新打开 / 重新加载工程 (Project -> Reload)。')
    set_progress(100, '处理完成')
    return True


def installed_components(proj):
    manifest = _load_json(_state_dir(proj) / 'manifest.json',
                          {'version': MANIFEST_VERSION, 'components': {}})
    return manifest.get('components', {})


# ===========================================================================
# 第五阶段：工程清单、许可证、Git 与 Keil 命令行构建
# ===========================================================================
COMPONENT_LICENSES = {
    'rtthread': ('Apache-2.0', 'RT-Thread 标准内核；检查工程副本 LICENSE 与源文件 SPDX'),
    'freertos': ('MIT', 'FreeRTOS-Kernel 与 CMSIS-FreeRTOS 各自许可证以复制源码为准'),
    'lvgl': ('MIT', 'LVGL'),
    'fatfs': ('FatFs license', '宽松许可证，非标准 SPDX；以源码 LICENSE/COPYING 为准'),
    'segger_rtt': ('SEGGER license', '使用前核对随源码分发的 LICENSE 文件'),
    'littlefs': ('BSD-3-Clause', 'littlefs'),
    'cmsis_dsp': ('Apache-2.0', 'CMSIS-DSP'),
    'lwip': ('BSD-3-Clause', 'lwIP'),
    'tinyusb': ('MIT', 'TinyUSB'),
    'rtos_guard': ('Generated', '本工具为当前工程生成的模板'),
    'add_files': ('Project-owned', '用户选择的工程文件'),
}


def _target_export_data(proj, target):
    target_name = target.findtext('TargetName', '') or ''
    common = target.find('TargetOption/TargetCommonOption')
    device = common.findtext('Device', '') if common is not None else ''
    include_paths, defines = [], []
    for cads in proj._target_cads(target):
        vc = cads.find('VariousControls')
        if vc is None:
            continue
        for value in (vc.findtext('IncludePath', '') or '').split(';'):
            if value.strip() and value.strip() not in include_paths:
                include_paths.append(value.strip())
        for value in re.split(r'[,;]', vc.findtext('Define', '') or ''):
            if value.strip() and value.strip() not in defines:
                defines.append(value.strip())
    files = [item for item in proj.file_records() if item['target'] == target_name]
    return {
        'name': target_name,
        'device': device,
        'compiler': 'Arm Compiler 6' if proj.is_ac6(target) else 'Arm Compiler 5',
        'files': files,
        'include_paths': include_paths,
        'defines': defines,
    }


def project_manifest_data(proj):
    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'project': str(proj.path),
        'format': proj.path.suffix.lower().lstrip('.'),
        'targets': [_target_export_data(proj, target) for target in proj.targets],
        'installed_components': installed_components(proj),
    }


def export_project_manifest(proj, destination):
    """导出工程源文件、路径、宏、编译器与已安装组件，支持 JSON/CSV/Markdown。"""
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = project_manifest_data(proj)
    suffix = destination.suffix.lower()
    if suffix == '.json':
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                               encoding='utf-8-sig')
    elif suffix == '.csv':
        with open(str(destination), 'w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.writer(stream)
            writer.writerow(['Target', '类别', '组/名称', '值', '文件类型'])
            for target in data['targets']:
                for item in target['files']:
                    writer.writerow([target['name'], '源文件', item['group'], item['path'],
                                     Path(item['path']).suffix.lower()])
                for value in target['include_paths']:
                    writer.writerow([target['name'], 'Include Path', '', value, ''])
                for value in target['defines']:
                    writer.writerow([target['name'], '宏定义', '', value, ''])
    else:
        lines = ['# Keil 工程清单', '', '- 工程：`%s`' % data['project'],
                 '- 生成时间：%s' % data['generated_at'], '']
        for target in data['targets']:
            lines.extend(['## Target：%s' % target['name'], '',
                          '- 芯片：`%s`' % (target['device'] or '未指定'),
                          '- 编译器：%s' % target['compiler'],
                          '- 工程文件：%d 个' % len(target['files']), '',
                          '### 工程文件', '', '| 分组 | 文件 | 路径 |',
                          '|---|---|---|'])
            for item in target['files']:
                lines.append('| %s | %s | `%s` |' %
                             (item['group'].replace('|', '\\|'), item['name'].replace('|', '\\|'),
                              item['path'].replace('|', '\\|')))
            lines.extend(['', '### Include Path', ''])
            lines.extend('- `%s`' % value for value in target['include_paths'])
            lines.extend(['', '### 宏定义', ''])
            lines.extend('- `%s`' % value for value in target['defines'])
            lines.append('')
        components = data['installed_components']
        lines.extend(['## 工具安装记录', ''])
        if components:
            for key, value in sorted(components.items()):
                lines.append('- %s：%s' %
                             (Report.TITLES.get(key, key), value.get('installed_at', '')))
        else:
            lines.append('- 尚无 `.keil-port-tool` 组件清单。')
        destination.write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    info('工程清单已导出: %s' % destination)
    return destination


def _find_component_license_files(proj, component_record):
    found = []
    for item in component_record.get('copied_dirs', []):
        root = _resolve_manifest_path(proj, item.get('path', ''))
        if not root.is_dir():
            continue
        for pattern in ('LICENSE*', 'COPYING*', 'COPYRIGHT*'):
            for path in root.glob(pattern):
                if path.is_file() and str(path) not in found:
                    found.append(str(path))
    return found


def export_license_report(proj, destination):
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    components = installed_components(proj)
    lines = ['# 第三方组件许可证清单', '',
             '> 本清单用于辅助审查，最终条款以工程中实际复制的许可证文件为准。', '']
    if not components:
        lines.append('当前工程没有由本工具记录的已安装组件。')
    for key, record in sorted(components.items()):
        license_name, note = COMPONENT_LICENSES.get(
            key, ('Unknown', '请人工核对随源码分发的许可证'))
        lines.extend(['## %s' % Report.TITLES.get(key, key), '',
                      '- 许可证：%s' % license_name,
                      '- 说明：%s' % note])
        files = _find_component_license_files(proj, record)
        if files:
            lines.append('- 工程内许可证文件：')
            lines.extend('  - `%s`' % path for path in files)
        else:
            lines.append('- 工程内未在组件根目录发现 LICENSE/COPYING 文件，请人工确认。')
        lines.append('')
    destination.write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    info('许可证清单已导出: %s' % destination)
    return destination


def update_project_gitignore(proj, include_third_party=False):
    """只追加本工具明确拥有的忽略项，不改写用户已有规则。"""
    root = project_content_root(proj)
    path = root / '.gitignore'
    old = read_source_text(path) if path.is_file() else ''
    entries = ['/.keil-port-tool/', '*.bak_*', '*.obsolete_bak_*']
    if include_third_party:
        entries.append('/Middlewares/Third_Party/')
    existing = {line.strip() for line in old.splitlines()}
    missing = [entry for entry in entries if entry not in existing]
    if not missing:
        info('.gitignore 已包含所需规则。')
        return False
    new = old
    if new and not new.endswith(('\n', '\r')):
        new += '\n'
    if new and not new.endswith('\n\n'):
        new += '\n'
    new += '# Keil Port Studio\n' + '\n'.join(missing) + '\n'
    if path.is_file():
        backup = path.with_name(path.name + '.bak_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        shutil.copy2(str(path), str(backup))
        info('.gitignore 备份: %s' % backup)
    path.write_bytes(encode_preserving_format(path, new))
    info('.gitignore 已追加 %d 条规则: %s' % (len(missing), path))
    return True


def find_uv4_executable(explicit=None):
    candidates = []
    for value in (explicit, CONFIG.get('UV4_PATH'), os.environ.get('UV4_PATH')):
        if value:
            candidates.append(Path(str(value).strip().strip('"')).expanduser())
    for root in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), 'C:\\Keil_v5',
                 'C:\\Keil'):
        if root:
            base = Path(root)
            candidates.extend((base / 'Keil_v5' / 'UV4' / 'UV4.exe',
                               base / 'UV4' / 'UV4.exe'))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which('UV4.exe') or shutil.which('UV4')
    if found:
        return Path(found).resolve()
    raise ToolError('未找到 UV4.exe。请在“设置”或 --uv4 中指定 Keil UV4.exe 路径。')


def analyze_keil_build_output(output):
    """解析 UV4 日志。UV4 在部分失败场景中仍会返回 ERRORLEVEL=0。"""
    text = str(output or '')
    summaries = re.findall(r'(\d+)\s+Error\(s\)\s*,\s*(\d+)\s+Warning\(s\)',
                           text, flags=re.I)
    if summaries:
        error_count, warning_count = (int(value) for value in summaries[-1])
    else:
        error_lines = re.findall(r'(?im)^.*(?:\berror\s*-|:\s*error(?:\s+[A-Z]?\d+)?\s*:).*$', text)
        warning_lines = re.findall(r'(?im)^.*(?:\bwarning\s*-|:\s*warning(?:\s+[A-Z]?\d+)?\s*:).*$', text)
        error_count = len(error_lines)
        warning_count = len(warning_lines)
    target_not_created = bool(re.search(r'\bTarget\s+not\s+created\b', text, re.I))
    return {
        'errors': error_count,
        'warnings': warning_count,
        'target_not_created': target_not_created,
    }


def build_keil_project(proj, target=None, uv4_path=None, rebuild=False, log_file=None):
    uv4 = find_uv4_executable(uv4_path)
    log_path = (Path(log_file).expanduser().resolve() if log_file else
                proj.dir / ('keil_rebuild.log' if rebuild else 'keil_build.log'))
    command = [str(uv4), '-r' if rebuild else '-b', str(proj.path), '-q', '-j0',
               '-o', str(log_path)]
    if target:
        command.extend(['-t', str(target)])
    # Preserve an earlier log, but never mistake it for this invocation's result.
    if log_path.exists():
        previous = log_path.with_name(log_path.name + '.previous_%d' % time.time_ns())
        try:
            log_path.rename(previous)
        except OSError as e:
            raise ToolError('无法保留旧编译日志，未启动编译: %s' % e)
    info('调用 Keil %s: %s' % ('重新构建' if rebuild else '增量构建', proj.path.name))
    verbose('命令: %s' % subprocess.list2cmdline(command))
    set_progress(10, 'Keil 正在编译')
    try:
        result = subprocess.run(command, cwd=str(proj.dir), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True)
    except OSError as e:
        raise ToolError('无法启动 Keil: %s' % e)
    set_progress(100, 'Keil 编译结束')
    output = ''
    if log_path.is_file():
        try:
            output = read_source_text(log_path)
        except Exception:
            output = log_path.read_text(encoding='utf-8', errors='replace')
    elif result.stdout:
        output = result.stdout
    if output.strip():
        log(output.rstrip())
    code = int(result.returncode)
    diagnostics = analyze_keil_build_output(output)
    if code > 1 or diagnostics['errors'] or diagnostics['target_not_created']:
        detail = []
        if diagnostics['errors']:
            detail.append('日志检出 %d 个错误' % diagnostics['errors'])
        if diagnostics['target_not_created']:
            detail.append('Target not created')
        if code > 1:
            detail.append('ERRORLEVEL=%d' % code)
        raise ToolError('Keil 编译失败（%s）。日志: %s' %
                        ('；'.join(detail) or '未知错误', log_path))
    if not re.search(r'\d+\s+Error\(s\)\s*,\s*\d+\s+Warning\(s\)', output, re.I):
        raise ToolError('未收到本次 Keil 编译完成摘要，不能确认成功。请检查工程、Target 和日志: %s' % log_path)
    if code == 1 or diagnostics['warnings']:
        warn('Keil 编译完成，存在 %d 个警告。日志: %s' %
             (diagnostics['warnings'], log_path))
    else:
        info('Keil 编译成功，无警告。日志: %s' % log_path)
    return code, log_path


def build_keil_targets(proj, targets=None, uv4_path=None, rebuild=False, log_file=None):
    """逐个构建所选 Target，避免 µVision 默认 Target 掩盖 Debug/Release 差异。"""
    names = list(targets or proj.target_names())
    if not names:
        names = [None]
    results, failures = [], []
    for name in names:
        target_log = log_file
        if len(names) > 1:
            base = (Path(log_file).expanduser() if log_file else
                    proj.dir / ('keil_rebuild.log' if rebuild else 'keil_build.log'))
            safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(name or 'default'))
            target_log = base.with_name('%s_%s%s' % (base.stem, safe_name, base.suffix or '.log'))
        try:
            results.append(build_keil_project(
                proj, target=name, uv4_path=uv4_path, rebuild=rebuild, log_file=target_log))
        except ToolError as e:
            failures.append('%s: %s' % (name or '默认 Target', e))
    if failures:
        raise ToolError('以下 Target 编译失败：\n' + '\n'.join(failures))
    return results


def _confirm_safety_action(text, yes=False, preview_callback=None):
    if preview_callback:
        return bool(preview_callback(text))
    log('\n========== 操作预览 ==========\n' + text)
    return bool(yes or ask_yn('确认执行以上操作?', False))


def _rtos_guard_unpatches(proj):
    return _component_init_unpatches(proj, 'rtos_guard')


def _component_init_unpatches(proj, component):
    data = installed_components(proj).get(component, {})
    return _manifest_source_unpatches(proj, component, data)


def uninstall_component(proj, component, yes=False, preview_callback=None):
    """按 manifest 卸载组件；只删除哈希未变化且由工具创建的文件。"""
    manifest_path = _state_dir(proj) / 'manifest.json'
    manifest = _load_json(manifest_path, {'version': MANIFEST_VERSION, 'components': {}})
    if component == 'rtthread':
        dependents = set(manifest.get('components', {})) & {'fatfs', 'littlefs', 'lwip', 'tinyusb', 'rtos_guard'}
        if dependents:
            raise ToolError('请先卸载依赖 RT-Thread 的组件或回滚整个事务: ' + ', '.join(sorted(dependents)))
    data = manifest.get('components', {}).get(component)
    if not data:
        raise ToolError('没有找到组件 %s 的安装记录；可卸载项: %s' %
                        (component, ', '.join(sorted(manifest.get('components', {}))) or '无'))

    # Validate every source reversal BEFORE mutating even the in-memory project.
    source_unpatches = _manifest_source_unpatches(proj, component, data)

    for record in data.get('project_files', []):
        proj.remove_file(record.get('path', ''))
    for entry in data.get('include_paths', []):
        proj.remove_include_path(entry)
    for macro in data.get('defines', []):
        proj.remove_define(macro)

    deletable_files, preserved_files = [], []
    for record in data.get('generated_files', []):
        path = _resolve_manifest_path(proj, record.get('path', ''))
        if not record.get('created'):
            preserved_files.append((path, '安装时已存在，只移除工程配置，不覆盖用户文件'))
        elif path.is_file() and sha256_file(path) == record.get('sha256'):
            deletable_files.append(path)
        elif path.exists():
            preserved_files.append((path, '内容已被修改'))

    deletable_dirs, preserved_dirs = [], []
    for record in data.get('copied_dirs', []):
        path = _resolve_manifest_path(proj, record.get('path', ''))
        if path.is_dir() and sha256_tree(path) == record.get('sha256'):
            deletable_dirs.append(path)
        elif path.exists():
            preserved_dirs.append((path, '目录内容已被修改'))

    if source_unpatches:
        unpatched = {Path(path).resolve() for path, _old, _new in source_unpatches}
        preserved_files = [
            (path, ('文件保留；仅移除工具自动插入的 include/初始化行'
                    if Path(path).resolve() in unpatched else reason))
            for path, reason in preserved_files]

    after_bytes = proj.serialize()
    payload = after_bytes[len(proj.xml_bom):] if proj.xml_bom else after_bytes
    after = payload.decode(proj.xml_encoding)
    preview = ''.join(difflib.unified_diff(
        proj.original_text.splitlines(True), after.splitlines(True),
        fromfile=str(proj.path) + ' (卸载前)', tofile=str(proj.path) + ' (卸载后)'))
    for path, old, new in source_unpatches:
        preview += ''.join(difflib.unified_diff(
            old.splitlines(True), new.splitlines(True),
            fromfile=str(path) + ' (卸载前)', tofile=str(path) + ' (卸载后)'))
    preview += '\n将删除未修改的工具生成文件:\n' + ''.join(
        '  - %s\n' % p for p in deletable_files)
    preview += '将删除未修改的组件源码目录:\n' + ''.join(
        '  - %s\n' % p for p in deletable_dirs)
    if preserved_files or preserved_dirs:
        preview += '为保护用户修改，将保留:\n' + ''.join(
            '  - %s（%s）\n' % item for item in preserved_files + preserved_dirs)
    if not _confirm_safety_action(preview, yes, preview_callback):
        info('已取消卸载。')
        return False

    tx = ProjectTransaction(proj, 'uninstall', [component])
    tx.snapshot(proj.path)
    tx.snapshot(manifest_path)
    for path in deletable_files:
        tx.snapshot(path)
    for path in deletable_dirs:
        tx.snapshot_dir(path)
    for path, _old, _new in source_unpatches:
        tx.snapshot(path)
    tx.save_meta('prepared')
    try:
        for path, _old, new in source_unpatches:
            path.write_bytes(encode_preserving_format(path, new))
        for path in deletable_files:
            tx._rel(path)
            if path.is_file():
                path.unlink()
        for path in sorted(deletable_dirs, key=lambda p: len(str(p)), reverse=True):
            tx._rel(path)
            if path.is_dir():
                shutil.rmtree(str(path))
        if proj.dirty:
            proj.save()
        del manifest['components'][component]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
        tx.save_meta('success')
    except Exception as e:
        tx.rollback(e)
        raise ToolError('卸载失败，已自动恢复: %s' % e)
    info('组件 %s 已卸载。用户修改过的文件不会被删除。' % component)
    return True


def _successful_transactions(proj):
    root = _state_dir(proj) / 'transactions'
    if not root.is_dir():
        return []
    result = []
    for directory in sorted(root.iterdir(), reverse=True):
        meta_path = directory / 'transaction.json'
        meta = _load_json(meta_path, {})
        if meta.get('status') == 'success' and meta.get('action') != 'rollback':
            result.append((directory, meta))
    return result


def rollback_last_transaction(proj, yes=False, preview_callback=None):
    transactions = _successful_transactions(proj)
    if not transactions:
        raise ToolError('没有可回滚的成功事务')
    directory, original = transactions[0]
    preview = ('将回滚事务 %s\n动作: %s\n组件: %s\nTarget: %s\n'
               '会恢复事务前的工程/配置文件，并删除该事务创建的文件与目录。\n' %
               (original.get('id'), original.get('action'),
                ', '.join(original.get('components', [])),
                ', '.join(original.get('targets', []))))
    if not _confirm_safety_action(preview, yes, preview_callback):
        info('已取消回滚。')
        return False

    safety = ProjectTransaction(proj, 'rollback', original.get('components', []))
    affected_files = set(original.get('created_files', []))
    affected_files.update(item.get('path') for item in original.get('snapshots', []))
    for rel in affected_files:
        if rel:
            safety.snapshot(safety.root / Path(rel))
    for rel in original.get('created_dirs', []):
        if rel:
            safety.snapshot_dir(safety.root / Path(rel))
    safety.save_meta('prepared')
    try:
        for rel in sorted(original.get('created_files', []), key=len, reverse=True):
            path = safety.root / Path(rel)
            safety._rel(path)
            if path.is_file():
                path.unlink()
        for rel in sorted(original.get('created_dirs', []), key=len, reverse=True):
            path = safety.root / Path(rel)
            safety._rel(path)
            if path.is_dir():
                shutil.rmtree(str(path))
        for item in original.get('snapshots', []):
            source = directory / Path(item['backup'])
            destination = safety.root / Path(item['path'])
            safety._rel(destination)
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(destination))
        original['status'] = 'rolled_back'
        (directory / 'transaction.json').write_text(
            json.dumps(original, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        safety.save_meta('success')
    except Exception as e:
        safety.rollback(e)
        raise ToolError('回滚失败，已恢复回滚开始前的状态: %s' % e)
    info('已回滚事务 %s。' % original.get('id'))
    return True


# ===========================================================================
# 交互菜单
# ===========================================================================
def print_banner(proj):
    log('=' * 60)
    log('  Keil 工程一键移植工具')
    log('  [1] 工程文件  [2] FreeRTOS  [3] LVGL  [4] FatFS  [5] 工程设置  [6] 扩展组件  [7] 网络/USB')
    log('=' * 60)
    log('工程: %s' % proj.path)
    dev = proj.device()
    if dev:
        log('芯片: %s' % dev)
    names = [n for n in proj.target_names() if n]
    if names:
        log('目标: %s' % ', '.join(names))


def interactive_menu(proj):
    while True:
        print_banner(proj)
        s = input('  请选择功能 (可多选, 如 126; 0 退出): ').strip()
        if s in ('0', '', 'q', 'quit', 'exit'):
            break
        tasks = []
        if '1' in s:
            tasks.append('add_files')
        if '2' in s:
            tasks.append('freertos')
        if '3' in s:
            tasks.append('lvgl')
        if '4' in s:
            tasks.append('fatfs')
        if '5' in s:
            tasks.append('project_settings')
        extension_requested = '6' in s
        network_requested = '7' in s
        if not tasks and not extension_requested and not network_requested:
            log('  输入无效, 请重新输入。')
            log('')
            continue

        opts = SimpleNamespace(interactive=True, yes=False, dry_run=False,
                               scan_dirs=None, include_h=CONFIG['INCLUDE_H_IN_TREE'],
                               freertos=None, no_os2=False, freertos_app=True, lvgl=None,
                               color_depth=CONFIG['LVGL_COLOR_DEPTH'], ports=False,
                               fatfs=None, fatfs_mode='auto', fatfs_app=True,
                               define_values=[], remove_defines=[], optimization=None,
                               debug_information=None, clean_includes=False,
                               remove_missing_includes=False, scatter_file=None,
                               clear_scatter=False, stack_size=None, heap_size=None,
                               segger_rtt=None, rtt_no_printf=False,
                               rtt_no_syscalls=False, rtt_no_asm=False,
                               littlefs=None, littlefs_mode='auto', littlefs_port=True,
                               cmsis_dsp=None, dsp_modules=[], dsp_float16=False,
                               guard_resources=['UART', 'SPI', 'I2C', 'FLASH'],
                               lwip=None, lwip_mode='auto', lwip_ipv6=False,
                               lwip_apps=list(LWIP_SAFE_APPS), lwip_driver='auto',
                               tinyusb=None, tinyusb_mode='device', tinyusb_classes=['CDC'],
                               sdk_dir=CONFIG['SDK_DIR'] or None, no_download=False)

        if 'add_files' in tasks:
            d = ask('任务1: 扫描目录 (默认: 工程根目录递归; 多个用 ; 分隔)',
                    CONFIG['SCAN_DIRS'] or None)
            opts.scan_dirs = d or str(proj.dir)
            opts.include_h = ask_yn('任务1: 同时把新头文件加入工程文件树 (便于浏览)?', False)
        if 'freertos' in tasks:
            d = ask('任务2: FreeRTOS 根目录 (留空 = 自动下载到 %s; 输入 skip 跳过)' %
                    default_sdk_dir(proj), CONFIG['FREERTOS_DIR'] or None)
            if d and d.strip().lower() in ('skip', '跳过'):
                log('  已跳过 FreeRTOS 移植。')
            else:
                opts.freertos = (d or None)
                if opts.freertos is None:
                    opts.no_os2 = False  # 下载的完整发行版自带 CMSIS-V2, 默认启用
                elif (Path(d) / 'Source' / 'CMSIS_RTOS_V2' / 'cmsis_os2.c').is_file():
                    opts.no_os2 = not ask_yn('任务2: 加入 CMSIS-RTOS V2 封装 (cmsis_os2.c)?', True)
        if 'lvgl' in tasks:
            d = ask('任务3: LVGL 根目录 (留空 = 自动下载到 %s; 输入 skip 跳过)' %
                    default_sdk_dir(proj), CONFIG['LVGL_DIR'] or None)
            if d and d.strip().lower() in ('skip', '跳过'):
                log('  已跳过 LVGL 移植。')
            else:
                opts.lvgl = (d or None)
                cd = ask('任务3: 色深 (16/32)', str(CONFIG['LVGL_COLOR_DEPTH']))
                opts.color_depth = int(cd) if cd in ('8', '16', '32') else CONFIG['LVGL_COLOR_DEPTH']
                opts.ports = ask_yn('任务3: 复制 lv_port_disp/indev 移植模板到工程?', False)
        if 'fatfs' in tasks:
            d = ask('任务4: FatFS 根目录 (留空 = 自动下载到 %s; 输入 skip 跳过)' %
                    default_sdk_dir(proj), CONFIG['FATFS_DIR'] or None)
            if d and d.strip().lower() in ('skip', '跳过'):
                tasks.remove('fatfs')
                log('  已跳过 FatFS 移植。')
            else:
                opts.fatfs = d or None
                mode = ask('任务4: 运行模式 (auto/rtos/baremetal)', 'auto')
                opts.fatfs_mode = mode if mode in ('auto', 'rtos', 'baremetal') else 'auto'
        if 'project_settings' in tasks:
            macros = ask('任务5: 添加/替换宏 (多个用 ; 分隔)', None)
            opts.define_values = [x.strip() for x in (macros or '').split(';') if x.strip()]
            remove = ask('任务5: 删除宏 (多个用 ; 分隔)', None)
            opts.remove_defines = [x.strip() for x in (remove or '').split(';') if x.strip()]
            optim = ask('任务5: 优化等级 (留空不改; O0/O1/O2/O3/Os/Oz)', None)
            opts.optimization = optim if optim in ('O0', 'O1', 'O2', 'O3', 'Os', 'Oz') else None
            opts.clean_includes = ask_yn('任务5: Include Path 去重?', True)
        if extension_requested:
            selected = (ask('任务6: 扩展组件 (r=RTT, l=LittleFS, d=CMSIS-DSP, g=外设锁；可多选)',
                            'rldg') or '').lower()
            if 'r' in selected:
                tasks.append('segger_rtt')
                opts.segger_rtt = ask('SEGGER RTT 根目录 (留空自动下载)',
                                      CONFIG['SEGGER_RTT_DIR'] or None)
            if 'l' in selected:
                tasks.append('littlefs')
                opts.littlefs = ask('LittleFS 根目录 (留空自动下载)',
                                    CONFIG['LITTLEFS_DIR'] or None)
                opts.littlefs_mode = ask('LittleFS 模式 (auto/rtos/baremetal)', 'auto')
            if 'd' in selected:
                tasks.append('cmsis_dsp')
                opts.cmsis_dsp = ask('CMSIS-DSP 根目录 (留空自动下载)',
                                     CONFIG['CMSIS_DSP_DIR'] or None)
            if 'g' in selected:
                tasks.append('rtos_guard')
                values = ask('外设锁 (UART/SPI/I2C/FLASH，用 ; 分隔)', 'UART;SPI;I2C;FLASH')
                opts.guard_resources = [x.strip().upper() for x in values.split(';') if x.strip()]
            if (not network_requested and
                    not any(t in tasks for t in ('segger_rtt', 'littlefs', 'cmsis_dsp', 'rtos_guard'))):
                log('  未选择扩展组件。')
                continue
        if network_requested:
            selected = (ask('任务7: 网络/USB (l=LwIP, u=TinyUSB；可多选)', 'lu') or '').lower()
            if 'l' in selected:
                tasks.append('lwip')
                opts.lwip = ask('LwIP 根目录 (留空自动下载)', CONFIG['LWIP_DIR'] or None)
                opts.lwip_mode = ask('LwIP 模式 (auto/rtos/baremetal)', 'auto')
                opts.lwip_ipv6 = ask_yn('启用 IPv6?', False)
                driver = ask('网卡 (auto/stm32_eth/enc28j60/w5500/generic)', 'auto')
                opts.lwip_driver = driver if driver in LWIP_DRIVERS else 'auto'
                values = ask('LwIP 应用 (http/mqtt/mdns/sntp/netbiosns/tftp，用 ; 分隔)',
                             ';'.join(LWIP_SAFE_APPS))
                opts.lwip_apps = [x.strip().lower() for x in values.split(';') if x.strip()]
            if 'u' in selected:
                tasks.append('tinyusb')
                opts.tinyusb = ask('TinyUSB 根目录 (留空自动下载)',
                                    CONFIG['TINYUSB_DIR'] or None)
                mode = ask('TinyUSB 角色 (device/host/both)', 'device')
                opts.tinyusb_mode = mode if mode in ('device', 'host', 'both') else 'device'
                values = ask('TinyUSB 类 (CDC/MSC/HID/MIDI/VENDOR，用 ; 分隔)', 'CDC')
                opts.tinyusb_classes = [x.strip().upper() for x in values.split(';') if x.strip()]
            if not any(t in tasks for t in ('lwip', 'tinyusb')):
                log('  未选择网络/USB 组件。')
                continue

        log('')
        run_tasks(proj, tasks, opts)
        log('')


# ===========================================================================
# Tkinter 图形界面
# ===========================================================================
def _checkbox_images(master):
    """Locally draw tick indicators; independent of Windows fonts/clam's X glyph."""
    root = master.winfo_toplevel()
    if hasattr(root, '_kps_check_images'):
        return root._kps_check_images
    size = max(18, round(15 * float(root.tk.call('tk', 'scaling'))))
    images = {}
    for state in ('off', 'on', 'mixed', 'disabled_off', 'disabled_on', 'disabled_mixed'):
        disabled = state.startswith('disabled')
        selected = state.endswith('on') or state.endswith('mixed')
        fill = '#CBD5E1' if disabled and selected else '#2563EB' if selected else '#FFFFFF'
        edge = '#D1D9E5' if disabled else '#2563EB' if selected else '#94A3B8'
        im = tk.PhotoImage(master=root, width=size, height=size)
        scale = size / 20.0
        for y in range(size):
            for x in range(size):
                px, py = (x + .5) / scale, (y + .5) / scale
                if 2 <= px < 18 and 2 <= py < 18:
                    # Small rounded corners; retain transparent exterior.
                    if (px < 3 or px > 17) and (py < 3 or py > 17):
                        continue
                    color = edge if min(px-2, 18-px, py-2, 18-py) < 1.2 else fill
                    if selected:
                        if state.endswith('mixed'):
                            mark = 5 <= px <= 15 and 9 <= py <= 11
                        else:
                            # Distance to two line segments forms a real tick, not X.
                            mark = False
                            for ax, ay, bx, by in ((5, 10, 8.5, 13.5), (8.5, 13.5, 15, 6.5)):
                                dx, dy = bx-ax, by-ay
                                t = max(0, min(1, ((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy)))
                                mark |= (px-ax-t*dx)**2 + (py-ay-t*dy)**2 <= 1.1**2
                        if mark:
                            color = '#FFFFFF'
                    im.put(color, (x, y))
        images[state] = im
    root._kps_check_images = images
    return images


def find_git():
    """Also find a freshly installed Windows Git before PATH is refreshed."""
    candidates = [shutil.which('git')]
    if sys.platform == 'win32':
        for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'),
                     str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs')):
            if base:
                candidates.append(str(Path(base) / 'Git' / 'cmd' / 'git.exe'))
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None)


def git_install_command():
    executable = shutil.which('winget') if sys.platform == 'win32' else None
    return [executable, 'install', '--id', 'Git.Git', '--exact', '--source', 'winget',
            '--interactive'] if executable else None


def _gt(zh, en):
    return en if _UI_LANGUAGE == 'en' else zh


class GitRepository:
    """Small explicit Git operations; never shell commands, force push, or auto-stash.

    A commit includes the WHOLE existing index, not just the selected UI rows.
    Git hooks/credential helpers remain Git's responsibility: open trusted repos only.
    """
    def __init__(self, folder, executable=None):
        self.executable = executable or find_git()
        if not self.executable:
            raise ToolError(_gt('未检测到 Git，请先安装或重新检测。', 'Git not found. Install Git, then detect again.'))
        self.folder = Path(folder).resolve()
        if not self.folder.is_dir():
            raise ToolError(_gt('请选择存在的工程目录。', 'Choose an existing project directory.'))

    @staticmethod
    def redact(text):
        return re.sub(r'(https?://)[^/\s@]+@', r'\1***@', text)

    def run(self, *args, **kwargs):
        env = os.environ.copy()
        # Do not let a calling shell silently redirect operations to a different index/repo.
        for key in tuple(env):
            if key.startswith('GIT_'):
                env.pop(key)
        env.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GCM_INTERACTIVE='never')
        try:
            result = subprocess.run([self.executable, '--no-pager', '-c', 'color.ui=false',
                                     '-c', 'core.quotepath=false', '-C', str(self.folder), *args],
                                    input=kwargs.get('input'), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                                    env=env, timeout=kwargs.get('timeout', 120),
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise ToolError(_gt('Git 超时，操作结果可能未知；请刷新状态后再决定是否重试。',
                                'Git timed out; outcome may be unknown. Refresh before retrying.'))
        except OSError as e:
            raise ToolError(str(e))
        if result.returncode and kwargs.get('check', True):
            raise ToolError(self.redact((result.stderr or result.stdout).strip()))
        return result

    def root(self):
        value = self.run('rev-parse', '--show-toplevel').stdout.strip()
        return Path(value).resolve()

    def at_root(self):
        return GitRepository(self.root(), self.executable)

    def initialize(self):
        if self.run('rev-parse', '--show-toplevel', check=False).returncode == 0:
            raise ToolError(_gt('目录已属于 Git 仓库，不创建嵌套仓库。', 'Already inside a Git repository; no nested repository created.'))
        return self.run('init').stdout

    def status(self):
        parts = self.run('status', '--porcelain=v1', '-z', '--untracked-files=all').stdout.split('\0')
        entries, index = [], 0
        while index < len(parts) and parts[index]:
            entry = parts[index]
            index += 1
            original = None
            if 'R' in entry[:2] or 'C' in entry[:2]:
                original = parts[index]
                index += 1
            entries.append({'status': entry[:2], 'path': entry[3:], 'original': original})
        return entries

    def branch(self):
        result = self.run('symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
        return result.stdout.strip() if result.returncode == 0 else ''

    def paths(self, paths):
        out = []
        for path in paths:
            # Always literal pathspecs: filenames beginning '-' or containing '*' are safe.
            p = Path(path)
            if not path or p.is_absolute() or '..' in p.parts or '\x00' in path:
                raise ToolError('Invalid repository-relative path: %r' % path)
            out.append(':(literal)' + path.replace('\\', '/'))
        if not out:
            raise ToolError(_gt('请先选择文件。', 'Select files first.'))
        return out

    def stage(self, paths):
        return self.run('add', '--', *self.paths(paths)).stdout

    def unstage(self, paths):
        specs = self.paths(paths)
        if self.run('rev-parse', '--verify', 'HEAD', check=False).returncode == 0:
            return self.run('restore', '--staged', '--', *specs).stdout
        return self.run('rm', '--cached', '--', *specs).stdout  # Keeps working files.

    def diff(self):
        return ('=== INDEX / STAGED ===\n' + self.run('diff', '--cached', '--no-ext-diff', '--no-textconv').stdout
                + '\n=== WORKTREE / UNSTAGED ===\n' + self.run('diff', '--no-ext-diff', '--no-textconv').stdout)

    def history(self):
        if self.run('rev-parse', '--verify', 'HEAD', check=False).returncode:
            return _gt('尚无提交。', 'No commits yet.')
        return self.run('log', '-30', '--date=iso-local', '--format=%h  %ad  %an%n    %s').stdout

    def commit(self, message):
        if not message.strip():
            raise ToolError(_gt('请填写提交说明。', 'Enter a commit message.'))
        if not any(e['status'][0] not in (' ', '?', '!') for e in self.status()):
            raise ToolError(_gt('没有暂存的修改。', 'No staged changes.'))
        return self.run('commit', '--file=-', input=message.strip() + '\n').stdout

    def set_identity(self, name, email):
        if not name.strip() or not email.strip() or any(c in name + email for c in '\r\n\0'):
            raise ToolError(_gt('请填写有效姓名和邮箱。', 'Enter a valid name and email.'))
        self.run('config', '--local', 'user.name', name.strip())
        self.run('config', '--local', 'user.email', email.strip())

    def remotes(self):
        return self.run('remote').stdout.splitlines()

    def remote_url(self, remote, push=False):
        if remote not in self.remotes():
            raise ToolError(_gt('请选择已有远端。', 'Select an existing remote.'))
        flags = ('--push', '--all') if push else ()
        return self.run('remote', 'get-url', *flags, remote).stdout.strip()

    def set_remote(self, name, url):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise ToolError('Invalid remote name.')
        if not url or url.startswith('-') or any(c in url for c in '\r\n\0'):
            raise ToolError('Invalid remote URL.')
        if re.search(r'https?://[^/]+@', url):
            raise ToolError(_gt('不要把密码或 token 写入 URL；请使用 Git 凭据管理器。',
                                'Do not put passwords or tokens in URLs; use Git Credential Manager.'))
        # ext:: can execute arbitrary commands; never accept remote-helper commands from this panel.
        if '::' in url or (re.match(r'^[A-Za-z]+://', url) and not url.startswith(('https://', 'ssh://', 'file://'))):
            raise ToolError('Use HTTPS, SSH, or a local repository path.')
        return self.run('remote', 'set-url' if name in self.remotes() else 'add', name, url).stdout

    def sync(self, action, remote, branch):
        self.remote_url(remote)
        if action not in ('fetch', 'pull', 'push'):
            raise ToolError('Unsupported Git action.')
        if action == 'fetch':
            return self.run('fetch', '--', remote).stderr
        if not branch or branch.startswith('-') or self.run('check-ref-format', '--branch', branch, check=False).returncode:
            raise ToolError(_gt('分支名称无效。', 'Invalid branch name.'))
        if not self.branch():
            raise ToolError(_gt('当前处于分离 HEAD，请先使用 Git 切换到分支。', 'Detached HEAD: switch to a branch with Git first.'))
        if action == 'pull':
            if self.status():
                raise ToolError(_gt('有未提交或未跟踪文件，请先处理后再拉取。', 'Commit or handle all modified/untracked files before pulling.'))
            result = self.run('pull', '--ff-only', '--no-rebase', '--', remote, branch)
        else:
            result = self.run('push', '--', remote, 'HEAD:refs/heads/' + branch)
        return result.stdout + result.stderr


_UI_LANGUAGE = 'zh-CN'
_EN_MESSAGES = {
    '文件 / 目录': 'File / directory', '用途': 'Purpose', '目录': 'Directory',
    '尚未扫描文件': 'Not scanned',
    '尚未扫描文件  ·  请先选择源码并扫描 / 自动准备': 'Not scanned — select sources, then scan or prepare',
    '已选 %d / %d 个文件  ·  空格切换选择': '%d / %d files selected — Space to toggle',
    '准备源码后，在这里逐项选择文件\n\n首次扫描默认全选，可按目录或文件取消':
        'Prepare sources to choose individual files here\n\nFirst scan selects all; uncheck files or folders as needed',
    '工程': 'Project', '工程文件': 'Project files', '工程设置': 'Build options',
    '全部 Target': 'All Targets', '全部文件': 'All files', '库仓库': 'Library store',
    '选择工程': 'Choose project', '选择目录': 'Choose folder', '添加目录': 'Add folder',
    '选择组件 · 核对文件 · 预览修改': 'Select components · Review files · Preview changes',
    '组件独立副本  /  修改前预览': 'Private copies / Preview first',
    '中间件独立复制，不改共享库': 'Private middleware copies; shared sources stay unchanged',
    '设置': 'Settings', '预设': 'Presets', '工程工具': 'Project tools',
    '安全与恢复': 'Safety & recovery', '查看日志': 'Show log', '隐藏日志': 'Hide log',
    '运行日志': 'Run log', '运行日志 · Keil Port Studio': 'Run log · Keil Port Studio',
    '预览并执行  →': 'Preview & apply  →', '清空': 'Clear', '关闭': 'Close', '取消': 'Cancel',
    '已启用 1 项': '1 feature enabled', '已启用 %d 项': '%d features enabled',
    '请选择一种 RTOS': 'Choose one RTOS', '启用此功能': 'Enable feature',
    '请选择 Keil 工程，然后扫描要添加的文件。': 'Choose a Keil project, then scan source files.',
    '01   工程文件': '01   Project files', '05   工程设置': '05   Build options',
    '06   扩展组件': '06   More components', '07   网络与 USB': '07   Network & USB',
    '更多组件': 'More components', '网络与 USB': 'Network & USB',
    'LVGL 显示': 'LVGL display', 'FatFS 存储': 'FatFS storage', 'RTT 日志': 'RTT logging',
    'LwIP 网络': 'LwIP network', '外设锁': 'Peripheral guards', '外设线程安全': 'Peripheral thread safety',
    '添加工程文件与静态库': 'Add project files & libraries',
    '为 C/C++、汇编、库与头文件添加路径引用；外部文件请先复制到工程。':
        'Add C/C++, assembly, library and header references. Copy external files into the project first.',
    '扫描目录': 'Scan folders', '↻  扫描刷新': '↻  Scan / refresh', '全选': 'Select all',
    '清空选择': 'Select none', '头文件也显示在工程树': 'Show headers in project tree',
    '共享源码': 'Source location', '自动准备': 'Prepare sources', '浏览': 'Browse',
    '创建工程独立副本、任务入口和默认任务，并自动启动调度器。':
        'Create private sources, a default task and automatic scheduler startup.',
    '内存方案': 'Heap scheme', 'CPU 移植层': 'CPU port', '任务入口 / 自动调度': 'Tasks / scheduler',
    'RT-Thread 5.2.2 · 单核内核': 'RT-Thread 5.2.2 · Single-core kernel',
    'Cortex-M4；工程独立副本、任务入口、自动调度启动。与 FreeRTOS 互斥。':
        'Cortex-M4 private kernel, task entry and scheduler startup. Exclusive with FreeRTOS.',
    '默认全选。核心依赖不可缺失；配置与任务文件重装时保留。不是完整 BSP/Env 安装器。':
        'All selected initially. Keep required dependencies. Existing tasks/config are retained. Not a full BSP/Env installer.',
    'LVGL 图形库': 'LVGL graphics', '颜色深度': 'Color depth',
    '复制独立源码、生成配置，并按模块选择要加入工程的组件。':
        'Copy private sources, generate configuration and choose modules below.',
    '复制显示/输入设备 porting 模板': 'Generate display/input port templates',
    'FatFS 文件系统': 'FatFS filesystem', '运行模式': 'Runtime mode',
    '复制独立源码，根据工程 RTOS 自动选择线程安全或裸机适配。':
        'Copy private sources; choose a matching RTOS or bare-metal backend.',
    '生成应用框架并接入初始化': 'Generate application and initialization',
    '自动判断': 'Auto-detect', 'RTOS（工程内核）': 'RTOS (project kernel)', '裸机': 'Bare metal',
    'Target 级编译与链接设置': 'Target compiler & linker settings',
    '宏、优化、调试信息、Include 清理、Stack/Heap 与分散加载文件。':
        'Defines, optimization, debug info, includes, stack/heap and scatter file.',
    '添加/替换宏': 'Add/replace defines', '删除宏': 'Remove defines',
    '多个宏用 ; 分隔，例如 LOG_LEVEL=2;USE_DSP': 'Separate defines with ; e.g. LOG_LEVEL=2;USE_DSP',
    '优化等级': 'Optimization', '调试信息': 'Debug info', '保持不变': 'Keep unchanged', '开启': 'On',
    'Include 路径去重': 'Deduplicate includes',
    '同时移除不存在的 Include 路径': 'Also remove missing include paths',
    'Scatter 文件': 'Scatter file', '选择 .sct': 'Choose .sct',
    '清除自定义 Scatter，恢复 Target 内存布局': 'Clear custom scatter; use Target memory layout',
    '支持 0x400 或十进制；修改所选 Target 引用的 startup*.s':
        'Hex (0x400) or decimal; updates startup*.s referenced by selected Targets',
    'SEGGER RTT 高速日志': 'SEGGER RTT logging',
    '核心、格式化输出、ARM 汇编加速和 Keil printf 重定向均可单独取消。':
        'Choose core, formatted output, ARM assembly and optional Keil printf redirect separately.',
    'LittleFS Flash 文件系统': 'LittleFS Flash filesystem',
    '复制独立源码，自动适配工程 RTOS；模板不会自动格式化 Flash。':
        'Private sources with matching RTOS backend. The template never auto-formats Flash.',
    '生成 littlefs_port.c/h Flash 模板': 'Generate littlefs_port.c/h Flash hooks',
    '按算法目录选择官方聚合编译单元，自动添加 Include 与 Cortex-M 宏。':
        'Choose official algorithm aggregators; add includes and Cortex-M defines.',
    'AC6 启用 Float16 模块': 'Enable Float16 on AC6',
    'AC5 自动使用兼容的 CMSIS-DSP 1.10，并禁用 Float16': 'AC5 uses CMSIS-DSP 1.10 without Float16',
    'RTOS 外设线程安全层': 'RTOS peripheral guards',
    '生成互斥锁 API，自动接入 FreeRTOS 或 RT-Thread 初始化入口。':
        'Generate mutex APIs and connect FreeRTOS or RT-Thread initialization.',
    '选择需要独立保护的外设': 'Choose resource categories to protect',
    '将生成 rtos_peripheral_guard.c/h。调用外设 HAL 函数前 Lock，完成后 Unlock；不会绑定特定 STM32 HAL 句柄。':
        'Generates rtos_peripheral_guard.c/h. Lock before HAL operations, unlock after completion. No automatic HAL handle binding.',
    'LwIP TCP/IP 协议栈': 'LwIP TCP/IP stack',
    '核心、IPv4/IPv6、RTOS API、HTTP、MQTT 等均在下方详细列出。':
        'Core, IPv4/IPv6, RTOS APIs, HTTP and MQTT files are listed below.',
    '启用 IPv6': 'Enable IPv6', '网卡': 'Network driver', '自动选择': 'Auto-select',
    '通用以太网': 'Generic Ethernet', '协议角色': 'USB role', '设备类': 'Classes',
    '根据 STM32 型号选择 USB 控制器驱动；协议类源码仍可逐文件取消。':
        'Select controller sources for the STM32 device; individual class files remain selectable.',
    'Keil Port Studio 设置': 'Keil Port Studio settings', '下载与开发环境': 'Downloads & tools',
    '下载重试次数': 'Download attempts', '1–10；网络失败会自动重试': '1–10; retry network failures',
    'HTTP/HTTPS 代理': 'HTTP/HTTPS proxy', 'GitHub 镜像前缀': 'GitHub mirror prefix',
    '例如 http://127.0.0.1:7890；留空为系统直连': 'e.g. http://127.0.0.1:7890; blank for default',
    '只改写 GitHub/codeload/raw 下载地址；留空关闭': 'Rewrites GitHub/codeload/raw URLs; blank disables',
    '额外跳过目录': 'Extra excluded folders', '多个目录名用 ; 分隔，例如 build;output':
        'Separate folder names with ; e.g. build;output',
    '选择 UV4.exe': 'Choose UV4.exe', '可执行文件': 'Executables',
    '设置保存在当前 Windows 用户配置目录，不会写入工程或 Git 仓库。':
        'Settings are stored in your user profile, not the project or Git repository.',
    '保存设置': 'Save settings', '设置无效': 'Invalid settings',
    '下载重试次数必须是 1–10 的整数。': 'Download attempts must be an integer from 1 to 10.',
    '保存设置失败': 'Cannot save settings', '设置已保存到 %s': 'Settings saved to %s',
    '保存当前预设…': 'Save current preset…', '加载预设…': 'Load preset…',
    '保存移植预设': 'Save porting preset', '加载移植预设': 'Load porting preset', 'JSON 预设': 'JSON preset',
    '保存预设失败': 'Cannot save preset', '加载预设失败': 'Cannot load preset',
    '预设已保存: %s': 'Preset saved: %s',
    '预设已加载；组件扫描后会自动恢复文件勾选。': 'Preset loaded; scanning restores saved file selections.',
    '预设版本不支持或根节点不是 JSON 对象': 'Unsupported preset version or non-object JSON root',
    '预设 %s 必须是 JSON 对象': 'Preset section %s must be a JSON object',
    '预设字段类型无效: %s.%s': 'Invalid preset field type: %s.%s',
    '当前工程没有 Target: %s；请选择对应工程后再加载预设。':
        'Target %s is absent. Select the matching project before loading this preset.',
    '无法打开': 'Cannot open', '工程化辅助': 'Project utilities',
    '导出不会修改工程；Git 规则只追加并自动备份原文件。':
        'Exports do not modify the project. Git rules are appended with a backup.',
    '导出工程清单': 'Export project inventory',
    '导出工程清单（MD / JSON / CSV）': 'Export project inventory (MD / JSON / CSV)',
    '导出第三方许可证清单': 'Export third-party license report', '导出许可证清单': 'Export license report',
    '导出完成': 'Export complete', '导出失败': 'Export failed',
    '工程清单已导出。': 'Project inventory exported.', '许可证清单已导出。': 'License report exported.',
    '将忽略工具状态/备份文件': 'Ignore tool state and backups',
    '，并忽略整个 Middlewares/Third_Party。': ', including the entire Middlewares/Third_Party directory.',
    '。第三方源码仍提交 Git。': '. Third-party sources remain tracked.',
    '更新 .gitignore': 'Update .gitignore', '更新失败': 'Update failed', '.gitignore 已更新。': '.gitignore updated.',
    '更新 .gitignore（提交第三方源码）': 'Update .gitignore (track third-party sources)',
    '更新 .gitignore（忽略第三方源码）': 'Update .gitignore (ignore third-party sources)',
    '重新构建（Rebuild）': 'Rebuild all', '立即调用 Keil 编译': 'Build with Keil now',
    '移植成功后自动调用 Keil 编译': 'Build automatically after porting',
    'Keil 编译失败': 'Keil build failed', 'Keil 编译完成': 'Keil build complete',
    '编译完成，详细结果请查看运行日志。': 'Build complete. See the run log for details.',
    'Keil 工程': 'Keil project', '选择 Keil 工程': 'Choose Keil project',
    '选择 Keil 分散加载文件': 'Choose Keil scatter file',
    '选择要扫描的源码目录': 'Choose source folder to scan',
    '请选择有效的 .uvprojx 或 .uvproj 工程文件': 'Choose a valid .uvprojx or .uvproj project',
    '工程：%s；芯片：%s': 'Project: %s; device: %s', '未识别': 'Unknown',
    '加入所在目录到 Include Path': 'Add containing folder to include paths',
    '加入链接：静态库': 'Link static library', '加入链接：目标文件': 'Link object file',
    '加入工程并汇编': 'Add and assemble', '加入工程并以 C++ 编译': 'Add and compile as C++',
    '加入工程并编译': 'Add and compile', '扫描失败': 'Scan failed',
    '发现 %d 个可处理的工程文件，默认已全选%s。': 'Found %d project files; all selected%s.',
    '已自动收窄到当前工程，避免扫入兄弟工程': 'Scan restricted to current project, excluding siblings',
    '已隔离其他工程目录 %d 个': '%d foreign project directories excluded',
    '已排除 Target 输出目录 %d 个': '%d Target output directories excluded',
    '未找到 RT-Thread 内核目录': 'RT-Thread kernel directory not found',
    'RT-Thread 文件已列出，默认全选；请核对需要的组件。': 'RT-Thread sources listed; review required components.',
    'RT-Thread 准备失败': 'RT-Thread preparation failed',
    '内核/端口依赖': 'Kernel/port dependency', '固定块内存池（可选）': 'Fixed-block pool (optional)',
    '多堆分配器（默认配置关闭）': 'Multiple heaps (disabled by default)',
    'slab 分配器（默认配置关闭）': 'Slab allocator (disabled by default)',
    '自动初始化框架（默认配置关闭）': 'Auto-init framework (disabled by default)',
    '信号（默认配置关闭）': 'Signals (disabled by default)',
    '信号量 / 互斥量 / 事件 / 邮箱 / 消息队列（核心）': 'Semaphores / mutexes / events / mailboxes / queues (core)',
    '无法自动识别内核，请从“RVDS 移植层”下拉框选择一项': 'Cannot detect CPU. Choose an RVDS port.',
    '未找到 portable/MemMang/heap_[1-5].c': 'portable/MemMang/heap_[1-5].c not found',
    '未找到所选内存管理文件：%s': 'Selected heap implementation not found: %s',
    '未找到移植层 port.c：%s': 'Port implementation not found: %s',
    '任务、调度器、任务通知（核心）': 'Tasks, scheduler and task notifications (core)',
    '内核链表（核心）': 'Kernel lists (core)', '队列、信号量、互斥量（核心）': 'Queues, semaphores and mutexes (core)',
    '事件组（按需）': 'Event groups (optional)', '流缓冲区、消息缓冲区（按需）': 'Stream/message buffers (optional)',
    '软件定时器（按需）': 'Software timers (optional)', '协程（按需，较少使用）': 'Co-routines (optional, uncommon)',
    '动态内存管理方案（请选择且仅使用一个）': 'Heap implementation (choose exactly one)',
    'CPU/编译器移植层（核心）': 'CPU/compiler port (core)', 'CMSIS-RTOS V2 API 封装': 'CMSIS-RTOS2 API wrapper',
    'CMSIS-RTOS2 系统节拍实现（必需）': 'CMSIS-RTOS2 system tick (required)',
    '未找到 CMSIS RTOS2 Source/os_systick.c': 'CMSIS RTOS2 Source/os_systick.c not found',
    '未找到 cmsis_os2.c；当前只能移植原生 FreeRTOS': 'cmsis_os2.c not found; native FreeRTOS only',
    'FreeRTOS 共 %d 个源文件，默认已全选；可取消按需组件。': '%d FreeRTOS sources selected; optional components may be unchecked.',
    'LVGL %s 模块': 'LVGL %s module',
    'LVGL 共 %d 个源文件，默认已全选；可按目录或单文件取消。': '%d LVGL sources selected; uncheck individual files or folders.',
    'FatFS 文件系统核心（必需）': 'FatFS filesystem core (required)',
    'ST 逻辑磁盘分发层（必需）': 'ST logical disk dispatch (required)',
    'ST 磁盘驱动注册层（必需）': 'ST disk registration (required)',
    '长文件名字符集转换（启用 LFN 时必需）': 'Long filename conversion (required with LFN)',
    'CMSIS-RTOS2 互斥锁/内存适配（FreeRTOS 必需）': 'CMSIS-RTOS2 mutex/memory backend (required for FreeRTOS)',
    '裸机内存与系统适配（按配置）': 'Bare-metal memory/system backend (as configured)',
    'RT-Thread（自动生成 ffsystem_rtthread.c）': 'RT-Thread (generates ffsystem_rtthread.c)',
    'FatFS 已按%s列出 %d 个组件，默认已全选。': 'FatFS %s mode: %d components selected.',
    'FreeRTOS / CMSIS-V2 线程安全模式': 'FreeRTOS / CMSIS-V2 thread-safe mode',
    '裸机模式': 'Bare-metal mode', 'FreeRTOS 线程安全': 'FreeRTOS thread-safe',
    '当前 FatFS 源码没有可用的 FreeRTOS 系统层': 'These FatFS sources lack a compatible FreeRTOS system layer',
    'RTT 环形缓冲区核心（必需）': 'RTT ring buffer core (required)',
    '轻量格式化输出 SEGGER_RTT_printf（按需）': 'SEGGER_RTT_printf formatting (optional)',
    'Keil printf/fputc 重定向（按需，注意冲突）': 'Keil printf/fputc redirect (optional; avoid conflicts)',
    'ARMv7M/ARMv8M 汇编加速（按需）': 'ARMv7M/ARMv8M assembly (optional)',
    'SEGGER RTT 共 %d 个可选编译单元，默认已全选。': '%d SEGGER RTT translation units selected.',
    'LittleFS 文件系统核心（必需）': 'LittleFS filesystem core (required)',
    'CRC、内存与日志工具（必需）': 'CRC, memory and logging utilities (required)',
    'LittleFS 已按%s列出核心文件，默认已全选。': 'LittleFS %s core sources selected.',
    '%s 算法聚合编译单元': '%s algorithm aggregator', '（Float16，可取消）': ' (Float16, optional)',
    '没有找到 CMSIS-DSP 聚合源码': 'CMSIS-DSP aggregator sources not found',
    'CMSIS-DSP 共 %d 个算法编译单元，默认已全选；可整组取消。': '%d CMSIS-DSP algorithm units selected; folders can be unchecked.',
    '线程安全模式需要 FreeRTOS 或 RT-Thread。': 'Thread-safe mode requires FreeRTOS or RT-Thread.',
    'FreeRTOS 模式需要先接入 FreeRTOS。': 'FreeRTOS mode requires an installed FreeRTOS kernel.',
    'TCP/IP 核心（必需或基础协议）': 'TCP/IP core (required/base protocols)',
    'IPv6 协议核心（按需）': 'IPv6 core (optional)', '线程/Socket/Netconn API（FreeRTOS）': 'Thread/socket/netconn APIs (RTOS)',
    '%s 应用协议（按需）': '%s application protocol (optional)',
    'LwIP 共 %d 个源文件，已按%s模式展开，默认全部勾选。': '%d LwIP sources in %s mode; all selected.',
    'TinyUSB 公共核心（必需）': 'TinyUSB common core (required)',
    'USB Device 核心（必需）': 'USB Device core (required)', 'USB Host 核心（必需）': 'USB Host core (required)',
    '%s 协议类（按需）': '%s class (optional)', '当前 STM32 USB 控制器驱动（必需）': 'STM32 USB controller driver (required)',
    '至少选择一个 TinyUSB 设备类': 'Select at least one TinyUSB class',
    '当前模式没有发现可用 TinyUSB 源码': 'No TinyUSB sources found for this mode',
    'TinyUSB 共 %d 个源文件，默认全部勾选。': '%d TinyUSB sources selected.',
    '详细差异预览': 'Detailed diff preview', '确认并继续': 'Confirm & continue',
    '请检查实际文件差异。只有确认后才会建立事务并写入工程。':
        'Review actual file changes. A transaction is created only after confirmation.',
    '组件安装记录': 'Installed components',
    '卸载只删除未经修改的工具文件；回滚会恢复最近事务的完整快照。':
        'Uninstall removes only unchanged tool files. Rollback restores the latest transaction snapshot.',
    '当前工程还没有组件安装清单': 'No component installation record in this project',
    '卸载所选组件': 'Uninstall selected', '回滚最近事务': 'Rollback latest',
    '卸载预览': 'Uninstall preview', '回滚预览': 'Rollback preview',
    '卸载完成': 'Uninstall complete', '卸载失败': 'Uninstall failed',
    '回滚完成': 'Rollback complete', '回滚失败': 'Rollback failed',
    '组件已卸载；用户修改过的文件已保留。': 'Component removed; user-modified files retained.',
    '最近一次事务已恢复。': 'Latest transaction restored.',
    '同一工程不能同时选择 FreeRTOS 和 RT-Thread': 'FreeRTOS and RT-Thread cannot share one project',
    '外设锁需要 FreeRTOS': 'Peripheral guards require an RTOS',
    '请同时启用 FreeRTOS，或选择一个已经接入 FreeRTOS 的工程。':
        'Enable FreeRTOS or RT-Thread, or choose a project with a supported RTOS.',
    '未选择任务': 'No features selected', '请至少勾选一个任务。': 'Enable at least one feature.',
    '核心文件未全选': 'Required files unchecked',
    'FreeRTOS 的 tasks.c、list.c、queue.c、port.c 或 heap_x.c 未全部选择，工程很可能无法链接。仍要继续吗？':
        'FreeRTOS tasks.c/list.c/queue.c/port.c/heap_x.c are incomplete. Linking will likely fail. Continue?',
    'CMSIS V2 依赖缺失': 'CMSIS V2 dependencies missing',
    'cmsis_os2.c 会直接使用这些组件。请重新勾选：\n': 'cmsis_os2.c requires these components. Re-select:\n',
    '\n\n如果只想使用原生 FreeRTOS，也可以取消 cmsis_os2.c。': '\n\nFor native FreeRTOS only, uncheck cmsis_os2.c.',
    '当前选择了 FreeRTOS / CMSIS-V2 模式，但工程中未检测到 FreeRTOS。\n请同时启用 FreeRTOS 任务，或把 FatFS 运行模式改为“裸机”。':
        'No RTOS detected for the selected mode.\nEnable an RTOS or select bare-metal FatFS.',
    '当前 FatFS 源码缺少 %s': 'FatFS sources missing %s', '不能取消：': 'Required: ',
    'SEGGER_RTT.c 不能取消。': 'SEGGER_RTT.c is required.', 'LittleFS 缺少 %s': 'LittleFS missing %s',
    '未选择 DSP 模块': 'No DSP modules selected', '请至少勾选一个算法模块。': 'Select at least one algorithm module.',
    '未选择外设锁': 'No peripheral guards selected', '请至少选择一种外设。': 'Select at least one resource category.',
    '当前模式必须选择这些文件：\n': 'This mode requires these files:\n',
    'TinyUSB 选择为空': 'Empty TinyUSB selection', '请至少选择一个设备类和源码文件。': 'Select at least one class and source file.',
    '开始处理：%s': 'Processing: %s', '正在写入工程…': 'Applying project changes…',
    '规划失败，未写入工程。请查看日志中的具体错误。': 'Planning failed; project unchanged. See the log.',
    '未写入工程': 'Project unchanged', '有组件规划失败，本次未写入任何工程文件。\n请查看运行日志。':
        'Component planning failed. No project files were written.\nSee the run log.',
    '操作已取消或工程无需修改。': 'Cancelled, or no project changes needed.',
    '\nKeil 编译也已完成，详情见运行日志。': '\nKeil build also completed. See the run log.',
    '完成': 'Complete', '执行失败': 'Operation failed', '执行失败。': 'Operation failed.', '[错误] ': '[Error] ',
    '处理完成。原工程已在同目录生成时间戳备份。\n请在 Keil 中重新加载工程。':
        'Complete. A timestamped project backup was created.\nReload the project in Keil.',
    '处理完成。请重新打开或 Reload Keil 工程。': 'Complete. Reopen or reload the project in Keil.',
    '当前 Python 没有 Tkinter，请安装带 Tcl/Tk 的 Python，或使用 --cli':
        'Tkinter is unavailable. Install Python with Tcl/Tk, or use --cli.',
}
for _component in ('FreeRTOS', 'LVGL', 'FatFS', 'SEGGER RTT', 'LittleFS', 'CMSIS-DSP', 'LwIP', 'TinyUSB'):
    for _suffix, _english in ((' 下载/定位失败', ' source download/location failed'),
                              (' 准备失败', ' preparation failed'), (' 准备失败。', ' preparation failed.')):
        _EN_MESSAGES[_component + _suffix] = _component + _english
    for _suffix in (' 源码…', ' 源码，请稍候…'):
        _EN_MESSAGES['正在准备 ' + _component + _suffix] = ('Preparing ' + _component +
            (' sources…' if _suffix == ' 源码…' else ' sources, please wait…'))
for _component in ('FatFS', 'LittleFS', 'LwIP', 'RTT'):
    _EN_MESSAGES[_component + ' 核心依赖缺失'] = _component + ' core dependencies missing'
    _EN_MESSAGES[_component + ' 模式不匹配'] = _component + ' mode mismatch'
for _files in ('include/FreeRTOS.h', 'ff.c 与 ff.h', 'lfs.c/lfs.h', 'lvgl.h 与 src 目录',
               'RTT/SEGGER_RTT.c', 'Source/Include/arm_math.h', 'LwIP src/include/lwip/init.h', 'TinyUSB src/tusb.c'):
    _EN_MESSAGES['所选目录中未找到 ' + _files] = 'Selected folder does not contain ' + _files.replace(' 与 ', ' and ').replace(' 目录', ' directory')
def _reverse_message_catalog(messages):
    reverse = {}
    for key, value in messages.items():
        if value in reverse and reverse[value] != key:
            raise ValueError('Ambiguous UI translation: %r / %r -> %r' % (reverse[value], key, value))
        reverse[value] = key
    return reverse


_ZH_MESSAGES = _reverse_message_catalog(_EN_MESSAGES)


def _tr(message):
    """Translate presentation strings, never source code, file paths or identifiers."""
    return _EN_MESSAGES.get(message, message) if _UI_LANGUAGE == 'en' else message


def _ui_canonical(message):
    return _ZH_MESSAGES.get(message, message)


class CheckTree(ttk.Frame if tk is not None else object):
    """带三级状态的文件树。单击复选框/名称或按空格切换，目录会级联。"""

    CHECKED = '☑ '
    EMPTY = '☐ '
    PARTIAL = '◩ '

    def __init__(self, master, height=14):
        ttk.Frame.__init__(self, master, style='TreeCard.TFrame')
        self.tree = ttk.Treeview(self, columns=('note',), height=height,
                                 selectmode='browse', style='Modern.Treeview')
        self.tree.heading('#0', text=_tr('文件 / 目录'), anchor='w')
        self.tree.heading('note', text=_tr('用途'), anchor='w')
        self.tree.column('#0', width=390, minwidth=220)
        self.tree.column('note', width=330, minwidth=180)
        sy = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)
        sx = ttk.Scrollbar(self, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        sy.grid(row=0, column=1, sticky='ns')
        sx.grid(row=1, column=0, sticky='ew')
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._state = {}
        self._label = {}
        self._path = {}
        self._counter = 0
        self.root_path = None
        self._images = _checkbox_images(self)
        self.summary_var = tk.StringVar(value=_tr('尚未扫描文件'))
        self.empty_label = ttk.Label(self.tree, text=_tr('准备源码后，在这里逐项选择文件\n\n首次扫描默认全选，可按目录或文件取消'),
                                     style='Muted.TLabel', justify='center', anchor='center')
        self.empty_label.place(relx=.5, rely=.5, anchor='center')
        self.tree.bind('<ButtonRelease-1>', self._click)
        self.tree.bind('<space>', self._space)
        self.tree.tag_configure('folder', foreground='#334155')
        self.tree.tag_configure('file', foreground='#475569')

    def clear(self):
        for item in self.tree.get_children(''):
            self.tree.delete(item)
        self._state.clear()
        self._label.clear()
        self._path.clear()
        self._counter = 0
        self.root_path = None
        self._refresh_summary()

    def _new_id(self):
        self._counter += 1
        return 'n%d' % self._counter

    def set_files(self, root, files):
        """files: iterable[(Path, 用途说明)]，默认全部勾选。"""
        previous_root = self.root_path
        previous_states = {p: self._state[i] for i, p in self._path.items()}
        self.clear()
        root = Path(root).resolve()
        self.root_path = root
        groups = {}
        for path, note in files:
            path = Path(path).resolve()
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = Path(path.name)
            parent = ''
            key_parts = []
            for part in rel.parts[:-1]:
                key_parts.append(part)
                key = '/'.join(key_parts)
                if key not in groups:
                    iid = self._new_id()
                    self._label[iid] = part
                    self._state[iid] = True
                    self.tree.insert(parent, 'end', iid=iid,
                                     text=part, image=self._images['on'], values=(_tr('目录'),), open=True,
                                     tags=('folder',))
                    groups[key] = iid
                parent = groups[key]
            iid = self._new_id()
            self._label[iid] = rel.name
            self._state[iid] = True
            self._path[iid] = path
            self.tree.insert(parent, 'end', iid=iid,
                             text=rel.name, image=self._images['on'], values=(note,), tags=('file',))
            if previous_root == root and path in previous_states:
                self._state[iid] = previous_states[path]
                self._paint(iid)
        for iid in self._path:
            self._update_parents(iid)
        self._refresh_summary()

    def _refresh_summary(self):
        total = len(self._path)
        selected = sum(self._state.get(i) is True for i in self._path)
        self.summary_var.set(_tr('已选 %d / %d 个文件  ·  空格切换选择') % (selected, total)
                             if total else _tr('尚未扫描文件  ·  请先选择源码并扫描 / 自动准备'))
        if total:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=.5, rely=.5, anchor='center')

    def _paint(self, iid):
        state = self._state.get(iid)
        mark = 'on' if state is True else 'off' if state is False else 'mixed'
        self.tree.item(iid, text=self._label[iid], image=self._images[mark])

    def _set_branch(self, iid, state):
        self._state[iid] = state
        self._paint(iid)
        for child in self.tree.get_children(iid):
            self._set_branch(child, state)

    def _update_parents(self, iid):
        parent = self.tree.parent(iid)
        while parent:
            vals = [self._state[c] for c in self.tree.get_children(parent)]
            self._state[parent] = True if all(v is True for v in vals) else \
                                  False if all(v is False for v in vals) else None
            self._paint(parent)
            parent = self.tree.parent(parent)

    def toggle(self, iid):
        if not iid:
            return
        self._set_branch(iid, self._state.get(iid) is not True)
        self._update_parents(iid)
        self._refresh_summary()

    def _click(self, event):
        if 'indicator' in self.tree.identify_element(event.x, event.y):
            return  # Expanding a folder must not change its selection.
        if self.tree.identify_column(event.x) != '#0':
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self.toggle(iid)

    def _space(self, _event):
        sel = self.tree.selection()
        if sel:
            self.toggle(sel[0])
        return 'break'

    def select_all(self, value=True):
        for iid in self.tree.get_children(''):
            self._set_branch(iid, value)
        self._refresh_summary()

    def checked_paths(self):
        return {p for iid, p in self._path.items() if self._state.get(iid) is True}

    def selection_keys(self):
        keys = []
        for path in sorted(self.checked_paths()):
            try:
                keys.append(str(path.relative_to(self.root_path)).replace('\\', '/'))
            except (ValueError, TypeError):
                keys.append(path.name)
        return keys

    def apply_selection_keys(self, keys):
        """按相对路径恢复预设；树中新增文件默认不选，避免预设静默扩大组件。"""
        wanted = {str(value).replace('\\', '/').lower() for value in (keys or [])}
        for iid, path in self._path.items():
            try:
                key = str(path.relative_to(self.root_path)).replace('\\', '/').lower()
            except (ValueError, TypeError):
                key = path.name.lower()
            self._state[iid] = key in wanted
            self._paint(iid)
        for iid in reversed(list(self._state)):
            if iid not in self._path:
                values = [self._state[c] for c in self.tree.get_children(iid)]
                if values:
                    self._state[iid] = (True if all(v is True for v in values) else
                                        False if all(v is False for v in values) else None)
                    self._paint(iid)
        self._refresh_summary()

    def file_count(self):
        return len(self._path)


def available_rvds_ports(base):
    rvds = Path(base) / 'portable' / 'RVDS'
    out = []
    if rvds.is_dir():
        for p in rvds.rglob('port.c'):
            out.append(str(p.parent.relative_to(rvds)).replace('\\', '/'))
    return sorted(set(out))


class GitPanel:
    """Worker threads do Git I/O only; Tk state stays on the GUI thread."""
    def __init__(self, app, folder):
        self.app, self.busy, self.repository = app, False, None
        self.results = queue.Queue()
        self.window = win = tk.Toplevel(app.root)
        win.title(_gt('Git 版本管理', 'Git version control'))
        win.configure(background=app.colors['surface'])
        app._size_dialog(win, 980, 740)
        win.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(win, padding=16, style='Surface.TFrame')
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)
        self.folder = tk.StringVar(value=str(folder))
        self.info = tk.StringVar(value=_gt('请选择可信的工程；提交可能运行仓库 hooks。',
                                         'Open trusted projects only; commits may run repository hooks.'))
        self.remote = tk.StringVar(value='origin')
        self.branch = tk.StringVar()
        self.message = tk.StringVar()
        self.controls = []
        top = ttk.Frame(frame, style='Surface.TFrame')
        top.grid(row=0, column=0, sticky='ew')
        top.columnconfigure(0, weight=1)
        entry = ttk.Entry(top, textvariable=self.folder)
        entry.grid(row=0, column=0, sticky='ew')
        self.controls.append(entry)
        self.button(top, _gt('其他工程…', 'Other project…'), self.browse).grid(row=0, column=1, padx=6)
        self.button(top, _gt('刷新 / 检测', 'Refresh / detect'), self.refresh).grid(row=0, column=2)
        bar = ttk.Frame(frame, style='Surface.TFrame')
        bar.grid(row=1, column=0, sticky='ew', pady=8)
        for zh, en, callback in (
                ('初始化仓库', 'Initialize', self.initialize), ('安装 Git…', 'Install Git…', self.install),
                ('提交身份…', 'Identity…', self.identity), ('配置远端…', 'Remote…', self.configure_remote)):
            self.button(bar, _gt(zh, en), callback).pack(side='left', padx=(0, 6))
        ttk.Label(frame, textvariable=self.info, wraplength=850, style='Body.TLabel').grid(row=2, column=0, sticky='ew', pady=4)
        ttk.Label(frame, text=_gt('选中行后暂存；XY 第一列是暂存区，第二列是工作区，?? 为未跟踪。',
                                  'Select rows to stage. XY: index / worktree; ?? means untracked.'), style='Muted.TLabel').grid(row=3, column=0, sticky='w')
        nb = ttk.Notebook(frame, style='Modern.TNotebook')
        self.notebook = nb
        nb.grid(row=4, column=0, sticky='nsew', pady=6)
        changes = ttk.Frame(nb, style='Surface.TFrame')
        nb.add(changes, text=_gt('文件修改', 'Changes'))
        changes.rowconfigure(0, weight=1); changes.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(changes, columns=('xy', 'path'), show='headings', selectmode='extended', style='Modern.Treeview')
        self.tree.heading('xy', text='XY'); self.tree.heading('path', text=_gt('文件', 'File'))
        self.tree.column('xy', width=55, stretch=False); self.tree.column('path', width=650)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(changes, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns'); self.tree.configure(yscrollcommand=scroll.set)
        self.details = ScrolledText(nb, wrap='none', state='disabled', height=12)
        nb.add(self.details, text=_gt('差异 / 历史 / 操作结果', 'Diff / history / results'))
        actions = ttk.Frame(frame, style='Surface.TFrame')
        actions.grid(row=5, column=0, sticky='ew')
        for zh, en, action in (('暂存所选', 'Stage selected', 'stage'), ('取消暂存', 'Unstage selected', 'unstage'),
                                ('查看差异', 'View diff', 'diff'), ('提交历史', 'History', 'history')):
            self.button(actions, _gt(zh, en), lambda a=action: self.action(a)).pack(side='left', padx=(0, 6))
        commit = ttk.Frame(frame, style='Surface.TFrame')
        commit.grid(row=6, column=0, sticky='ew', pady=8); commit.columnconfigure(1, weight=1)
        ttk.Label(commit, text=_gt('提交说明', 'Message'), style='Body.TLabel').grid(row=0, column=0, padx=(0, 8))
        entry = ttk.Entry(commit, textvariable=self.message)
        entry.grid(row=0, column=1, sticky='ew'); self.controls.append(entry)
        self.button(commit, _gt('提交暂存区…', 'Commit staged…'), lambda: self.action('commit')).grid(row=0, column=2, padx=(8, 0))
        sync = ttk.Frame(frame, style='Surface.TFrame')
        sync.grid(row=7, column=0, sticky='ew')
        ttk.Label(sync, text=_gt('远端', 'Remote'), style='Body.TLabel').pack(side='left')
        self.remote_combo = ttk.Combobox(sync, textvariable=self.remote, state='readonly', width=12)
        self.remote_combo.pack(side='left', padx=6); self.controls.append(self.remote_combo)
        ttk.Label(sync, text=_gt('远端分支', 'Remote branch'), style='Body.TLabel').pack(side='left')
        entry = ttk.Entry(sync, textvariable=self.branch, width=18)
        entry.pack(side='left', padx=6); self.controls.append(entry)
        for zh, en, action in (('获取', 'Fetch', 'fetch'), ('拉取（仅快进）', 'Pull (FF only)', 'pull'), ('推送…', 'Push…', 'push')):
            self.button(sync, _gt(zh, en), lambda a=action: self.action(a)).pack(side='left', padx=(0, 6))
        ttk.Label(frame, text=_gt('不会自动添加文件、强推或覆盖冲突。首次远端认证请先通过 Git / Git Credential Manager 完成。',
                                  'No auto-stage, force push, or conflict overwrite. Set up authentication in Git / Git Credential Manager first.'),
                  wraplength=850, style='Muted.TLabel').grid(row=8, column=0, sticky='ew', pady=(8, 0))
        self.refresh()

    def button(self, parent, label, command):
        button = ttk.Button(parent, text=label, command=command)
        self.controls.append(button)
        return button

    def output(self, text, show=False):
        self.details.configure(state='normal')
        self.details.insert('end', GitRepository.redact(str(text)) + '\n')
        self.details.see('end'); self.details.configure(state='disabled')
        if show:
            self.notebook.select(self.details)

    def job(self, function, done=None):
        if self.busy:
            return
        self.busy = True
        for control in self.controls:
            control.state(['disabled'])
        self.info.set(_gt('Git 正在执行，请等待；不关闭程序。', 'Git is running. Please wait; do not close the application.'))
        def worker():
            try:
                self.results.put((True, function()))
            except Exception as e:
                self.results.put((False, str(e)))
        threading.Thread(target=worker, daemon=True).start()
        def poll():
            try:
                success, value = self.results.get_nowait()
            except queue.Empty:
                self.window.after(50, poll)
                return
            self.busy = False
            for control in self.controls:
                control.state(['!disabled'])
            self.remote_combo.configure(state='readonly')
            if success:
                self.info.set(_gt('操作完成。', 'Operation completed.'))
                if done:
                    done(value)
                else:
                    self.output(value or _gt('完成。', 'Done.'))
                    self.refresh()
            else:
                self.info.set(_gt('操作未完成，详情见操作结果。', 'Operation did not complete. See results.'))
                self.output(value, show=True)
                messagebox.showerror('Git', GitRepository.redact(value), parent=self.window)
        self.window.after(50, poll)

    def refresh(self):
        if self.busy:
            return
        folder = self.folder.get()
        self.repository = None
        def read():
            repo = GitRepository(folder)
            found = repo.run('rev-parse', '--show-toplevel', check=False)
            if found.returncode:
                if 'not a git repository' in found.stderr:
                    return None
                raise ToolError(found.stderr)
            repo = GitRepository(found.stdout.strip(), repo.executable)
            return repo, repo.status(), repo.branch(), repo.remotes()
        def display(value):
            if value is None:
                self.tree.delete(*self.tree.get_children())
                self.info.set(_gt('此目录还不是 Git 仓库。可点击“初始化仓库”开始；不会自动提交。',
                                  'This folder is not a Git repository yet. Use Initialize; no automatic commit.'))
                return
            self.repository, entries, branch, remotes = value
            self.folder.set(str(self.repository.folder))
            self.entries = entries
            self.tree.delete(*self.tree.get_children())
            for i, entry in enumerate(entries):
                name = entry['path'] + ('  <- ' + entry['original'] if entry['original'] else '')
                self.tree.insert('', 'end', iid=str(i), values=(entry['status'], name))
            self.remote_combo.configure(values=remotes)
            if self.remote.get() not in remotes:
                self.remote.set(remotes[0] if remotes else '')
            self.branch.set(branch)
            self.info.set('%s | %s | %d %s' % (self.repository.folder, branch or 'DETACHED HEAD', len(entries),
                                              _gt('项修改', 'changed files')))
        self.job(read, display)

    def browse(self):
        path = filedialog.askdirectory(parent=self.window, initialdir=self.folder.get())
        if path:
            self.folder.set(path); self.refresh()

    def initialize(self):
        folder = self.folder.get()
        if messagebox.askyesno('Git init', _gt('在以下目录创建 Git 仓库？不会自动提交文件。\n',
                                              'Initialize a repository here? No files will be committed automatically.\n') + folder, parent=self.window):
            self.job(lambda: GitRepository(folder).initialize())

    def form(self, title, labels, defaults, save):
        win = tk.Toplevel(self.window); win.title(title); win.transient(self.window)
        win.grab_set()
        frame = ttk.Frame(win, padding=20); frame.pack(fill='both', expand=True)
        variables = [tk.StringVar(value=value) for value in defaults]
        for i, (label, variable) in enumerate(zip(labels, variables)):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky='w', pady=6)
            ttk.Entry(frame, textvariable=variable, width=52).grid(row=i, column=1, padx=8)
        def confirm():
            values = [v.get() for v in variables]
            win.destroy(); save(*values)
        ttk.Button(frame, text=_gt('保存到此仓库', 'Save in this repository'), command=confirm).grid(row=len(labels), column=1, sticky='e', pady=12)

    def identity(self):
        if self.repository and Path(self.folder.get()).resolve() == self.repository.folder:
            repo = self.repository
            self.form(_gt('本仓库提交身份', 'Repository identity'), (_gt('姓名', 'Name'), _gt('邮箱', 'Email')), ('', ''),
                      lambda name, email: self.job(lambda: repo.set_identity(name, email)))

    def configure_remote(self):
        if self.repository and Path(self.folder.get()).resolve() == self.repository.folder:
            repo = self.repository
            self.form(_gt('远端配置（不保存密码）', 'Remote configuration (no passwords)'), ('Name', 'URL'),
                      (self.remote.get() or 'origin', ''), lambda name, url: self.job(lambda: repo.set_remote(name, url)))

    def action(self, action):
        repo = self.repository
        if not repo or self.busy:
            return
        if Path(self.folder.get()).resolve() != repo.folder:
            messagebox.showerror('Git', _gt('目录已改变，请先刷新确认仓库。', 'Folder changed. Refresh to confirm the repository first.'), parent=self.window)
            return
        if action in ('stage', 'unstage'):
            paths = []
            for item in self.tree.selection():
                entry = self.entries[int(item)]
                include_original = entry['original'] and (action == 'unstage' or entry['status'][1] in 'RC')
                paths.extend([entry['path']] + ([entry['original']] if include_original else []))
            self.job(lambda: getattr(repo, action)(paths))
        elif action in ('diff', 'history'):
            self.job(lambda: getattr(repo, action)(), lambda value: self.output(value, show=True))
        elif action == 'commit':
            message = self.message.get()
            if messagebox.askyesno('Git commit', _gt('提交整个暂存区（含此前已暂存的文件）？请先检查差异。\n\n',
                                                     'Commit the entire index (including previously staged files)? Review the diff first.\n\n') + message,
                                    parent=self.window):
                self.job(lambda: repo.commit(message))
        else:
            remote, branch = self.remote.get(), self.branch.get()
            # Read destination in a worker too: even remote helper/config reads can be slow.
            def destination_ready(url):
                if messagebox.askyesno('Git ' + action, '%s\n%s\n%s' % (GitRepository.redact(url), branch, action), parent=self.window):
                    self.job(lambda: repo.sync(action, remote, branch))
            self.job(lambda: repo.remote_url(remote, push=action == 'push'), destination_ready)

    def install(self):
        if find_git():
            messagebox.showinfo('Git', _gt('已检测到 Git，无需安装。', 'Git is already installed.'), parent=self.window)
            return
        if not messagebox.askyesno('Git', _gt('安装 Git 会修改本机环境，并可能需要管理员确认。打开官方安装流程？\n安装后请点“刷新 / 检测”。',
                                            'Installing Git changes this computer and may require administrator confirmation. Open official installation?\nUse Refresh / detect afterwards.'), parent=self.window):
            return
        command = git_install_command()
        try:
            if command:
                # Interactive installer owned by the user. Never accept UAC/agreements silently.
                subprocess.Popen(command, creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
            else:
                webbrowser.open('https://git-scm.com/install/windows' if sys.platform == 'win32' else 'https://git-scm.com/install/')
            self.output(_gt('已打开安装流程；完成后重新检测。未将启动安装器视为安装成功。',
                            'Installation opened. Detect again when finished; launching an installer is not installation success.'))
        except OSError as e:
            messagebox.showerror('Git', str(e), parent=self.window)

    def close(self):
        if self.busy:
            messagebox.showinfo('Git', _gt('请等待 Git 操作完成再关闭。', 'Wait for the Git operation to finish before closing.'), parent=self.window)
            return
        self.window.destroy()


class KeilPortGUI:
    def __init__(self, initial_project=None, scaling=None, language=None):
        if tk is None:
            raise ToolError(_tr('当前 Python 没有 Tkinter，请安装带 Tcl/Tk 的 Python，或使用 --cli'))
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass  # Already configured by a host, or an older Windows version.
        self.root = tk.Tk()
        if scaling is not None:  # Deterministic display-scale QA; normal launch uses OS DPI.
            self.root.tk.call('tk', 'scaling', scaling)
        self.ui_scale = float(self.root.tk.call('tk', 'scaling')) / (96.0 / 72.0)
        self.root.title('Keil Port Studio')
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = min(round(1180*self.ui_scale), screen_w - 60)
        win_h = min(round(800*self.ui_scale), screen_h - 90)
        pos_x = max(0, (screen_w - win_w) // 2)
        pos_y = max(0, (screen_h - win_h) // 2)
        self.root.geometry('%dx%d+%d+%d' % (win_w, win_h, pos_x, pos_y))
        self.root.minsize(min(round(980*self.ui_scale), screen_w-60),
                          min(round(650*self.ui_scale), screen_h-90))
        self.user_settings = load_user_settings()
        global _UI_LANGUAGE
        _UI_LANGUAGE = language or self.user_settings.get('language', 'zh-CN')
        if _UI_LANGUAGE not in ('en', 'zh-CN'):
            _UI_LANGUAGE = 'zh-CN'
        self.language_var = tk.StringVar(value=_UI_LANGUAGE)
        self._nav_traces = []
        apply_user_settings(self.user_settings)
        self._configure_styles()

        self.project_var = tk.StringVar(value=str(initial_project or ''))
        self.target_var = tk.StringVar(value=_tr('全部 Target'))
        self.sdk_var = tk.StringVar()
        self.scan_var = tk.StringVar()
        self.add_enabled = tk.BooleanVar(value=True)
        self.freertos_enabled = tk.BooleanVar(value=False)
        self.rtthread_enabled = tk.BooleanVar(value=False)
        self.rtthread_dir = tk.StringVar()
        self.rtthread_root = None
        self.lvgl_enabled = tk.BooleanVar(value=False)
        self.fatfs_enabled = tk.BooleanVar(value=False)
        self.settings_enabled = tk.BooleanVar(value=False)
        self.rtt_enabled = tk.BooleanVar(value=False)
        self.littlefs_enabled = tk.BooleanVar(value=False)
        self.cmsis_dsp_enabled = tk.BooleanVar(value=False)
        self.rtos_guard_enabled = tk.BooleanVar(value=False)
        self.lwip_enabled = tk.BooleanVar(value=False)
        self.tinyusb_enabled = tk.BooleanVar(value=False)
        self.include_h = tk.BooleanVar(value=False)
        self.freertos_dir = tk.StringVar()
        self.heap_var = tk.StringVar(value='heap_4.c')
        self.freertos_app = tk.BooleanVar(value=True)
        self.port_var = tk.StringVar()
        self.lvgl_dir = tk.StringVar()
        self.color_var = tk.StringVar(value='16')
        self.lv_ports = tk.BooleanVar(value=False)
        self.fatfs_dir = tk.StringVar()
        self.fatfs_mode = tk.StringVar(value=_tr('自动判断'))
        self.fatfs_app = tk.BooleanVar(value=True)
        self.rtt_dir = tk.StringVar()
        self.littlefs_dir = tk.StringVar()
        self.littlefs_mode = tk.StringVar(value=_tr('自动判断'))
        self.littlefs_port = tk.BooleanVar(value=True)
        self.cmsis_dsp_dir = tk.StringVar()
        self.dsp_float16 = tk.BooleanVar(value=True)
        self.guard_vars = {name: tk.BooleanVar(value=True)
                           for name in ('UART', 'SPI', 'I2C', 'FLASH')}
        self.lwip_dir = tk.StringVar()
        self.lwip_mode = tk.StringVar(value=_tr('自动判断'))
        self.lwip_ipv6 = tk.BooleanVar(value=False)
        self.lwip_driver = tk.StringVar(value=_tr('自动选择'))
        self.tinyusb_dir = tk.StringVar()
        self.tinyusb_mode = tk.StringVar(value='Device')
        self.tinyusb_class_vars = {name: tk.BooleanVar(value=True)
                                   for name in TINYUSB_CLASSES}
        self.define_var = tk.StringVar()
        self.remove_define_var = tk.StringVar()
        self.optimization_var = tk.StringVar(value=_tr('保持不变'))
        self.debug_info_var = tk.StringVar(value=_tr('保持不变'))
        self.scatter_var = tk.StringVar()
        self.clear_scatter_var = tk.BooleanVar(value=False)
        self.clean_includes_var = tk.BooleanVar(value=False)
        self.remove_missing_includes_var = tk.BooleanVar(value=False)
        self.stack_size_var = tk.StringVar()
        self.heap_size_var = tk.StringVar()
        self.build_after_var = tk.BooleanVar(value=False)
        self.rebuild_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value=_tr('请选择 Keil 工程，然后扫描要添加的文件。'))
        self.progress_var = tk.IntVar(value=0)
        self._pending_preset_selections = {}
        self.log_visible = False
        self.freertos_root = None
        self.lvgl_root = None
        self.fatfs_root = None
        self.fatfs_preview_rtos = None
        self.rtt_root = None
        self.littlefs_root = None
        self.littlefs_preview_rtos = None
        self.cmsis_dsp_root = None
        self.lwip_root = None
        self.lwip_preview = None
        self.tinyusb_root = None
        self.tinyusb_preview = None

        self._build()
        global _LOG_SINK, _PROGRESS_SINK
        _LOG_SINK = self.append_log
        _PROGRESS_SINK = self.update_progress
        self.root.protocol('WM_DELETE_WINDOW', self._close)

        if not self.project_var.get():
            hits = _find_projects(Path.cwd())
            if len(hits) == 1:
                self.project_var.set(str(hits[0].resolve()))
        if self.project_var.get():
            self._project_changed()

    def _configure_styles(self):
        """不依赖第三方主题的现代浅色视觉系统。"""
        self.colors = {
            'bg': '#F3F6FB', 'surface': '#FFFFFF', 'surface_alt': '#F8FAFC',
            'text': '#0F172A', 'muted': '#64748B', 'border': '#DCE3EC',
            'primary': '#2563EB', 'primary_hover': '#1D4ED8',
            'header': '#FFFFFF', 'success': '#0F766E', 'selection': '#EAF1FF',
        }
        self.root.configure(background=self.colors['bg'])
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        c = self.colors
        style.configure('.', font=('Microsoft YaHei UI', 10), foreground=c['text'])
        style.configure('App.TFrame', background=c['bg'])
        style.configure('Surface.TFrame', background=c['surface'])
        style.configure('TreeCard.TFrame', background=c['surface'], relief='flat', borderwidth=0)
        style.configure('Header.TFrame', background=c['header'])
        style.configure('HeaderTitle.TLabel', background=c['header'], foreground=c['text'],
                        font=('Microsoft YaHei UI', 16, 'bold'))
        style.configure('HeaderSub.TLabel', background=c['header'], foreground=c['muted'],
                        font=('Microsoft YaHei UI', 9))
        style.configure('Badge.TLabel', background='#EFF6FF', foreground=c['primary'],
                        font=('Microsoft YaHei UI', 9, 'bold'), padding=(10, 5))
        style.configure('Section.TLabel', background=c['surface'], foreground=c['text'],
                        font=('Microsoft YaHei UI', 12, 'bold'))
        style.configure('TaskTitle.TLabel', background=c['surface'], foreground=c['text'],
                        font=('Microsoft YaHei UI', 13, 'bold'))
        style.configure('Body.TLabel', background=c['surface'], foreground=c['text'])
        style.configure('Muted.TLabel', background=c['surface'], foreground=c['muted'],
                        font=('Microsoft YaHei UI', 9))
        style.configure('Status.TLabel', background=c['bg'], foreground=c['muted'])
        style.configure('TEntry', fieldbackground='#FFFFFF', foreground=c['text'],
                        bordercolor=c['border'], lightcolor=c['border'], darkcolor=c['border'],
                        insertcolor=c['text'], padding=(8, 6))
        style.map('TEntry', bordercolor=[('focus', c['primary'])],
                  lightcolor=[('focus', c['primary'])], darkcolor=[('focus', c['primary'])])
        style.configure('TCombobox', fieldbackground='#FFFFFF', foreground=c['text'],
                        bordercolor=c['border'], arrowcolor=c['muted'], padding=(8, 6))
        style.map('TCombobox', bordercolor=[('focus', c['primary'])],
                  fieldbackground=[('readonly', '#FFFFFF')])
        style.configure('TButton', background='#FFFFFF', foreground='#334155',
                        bordercolor=c['border'], lightcolor=c['border'], darkcolor=c['border'],
                        padding=(11, 6), font=('Microsoft YaHei UI', 9))
        style.map('TButton', background=[('active', '#EEF2F7'), ('pressed', '#E2E8F0')])
        style.configure('Primary.TButton', background=c['primary'], foreground='#FFFFFF',
                        bordercolor=c['primary'], lightcolor=c['primary'], darkcolor=c['primary'],
                        padding=(18, 8), font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Primary.TButton', background=[('active', c['primary_hover']),
                                                 ('pressed', '#1E40AF')],
                  foreground=[('disabled', '#CBD5E1')])
        style.configure('Quiet.TButton', background=c['surface'], foreground=c['primary'],
                        borderwidth=0, padding=(9, 6))
        style.map('Quiet.TButton', background=[('active', '#EFF6FF')])
        style.configure('Modern.TCheckbutton', background=c['surface'], foreground=c['text'],
                        padding=(3, 5))
        style.map('Modern.TCheckbutton', background=[('active', c['surface'])],
                  foreground=[('disabled', '#94A3B8')])
        marks = _checkbox_images(self.root)
        style.element_create('KPS.Check.indicator', 'image', marks['off'],
                             ('disabled', 'selected', marks['disabled_on']),
                             ('disabled', 'alternate', marks['disabled_mixed']),
                             ('disabled', marks['disabled_off']),
                             ('selected', marks['on']), ('alternate', marks['mixed']),
                             width=marks['off'].width()+5, sticky='w')
        style.layout('Modern.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
            ('KPS.Check.indicator', {'side': 'left', 'sticky': 'w'}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
        style.configure('Nav.TButton', background=c['bg'], foreground=c['muted'],
                        borderwidth=0, relief='flat', padding=(12, 10), anchor='w',
                        font=('Microsoft YaHei UI', 10))
        style.map('Nav.TButton', background=[('selected', c['selection']), ('active', '#E8EDF5')],
                  foreground=[('selected', c['primary']), ('active', c['text'])])
        style.configure('Segment.TButton', background=c['surface_alt'], foreground=c['muted'],
                        padding=(14, 4), borderwidth=0)
        style.map('Segment.TButton', background=[('selected', c['selection']), ('active', '#EFF6FF')],
                  foreground=[('selected', c['primary'])])
        style.layout('Content.TNotebook.Tab', [])
        style.configure('Content.TNotebook', background=c['surface'], borderwidth=0, tabmargins=0,
                        bordercolor=c['border'], lightcolor=c['surface'], darkcolor=c['surface'])
        style.layout('Content.TNotebook', [('Notebook.client', {'sticky': 'nswe'})])
        for direction, sticky in (('Vertical', 'ns'), ('Horizontal', 'ew')):
            style.configure(direction+'.TScrollbar', background='#CCD5E2', troughcolor=c['surface_alt'],
                            bordercolor=c['surface_alt'], lightcolor='#CCD5E2', darkcolor='#CCD5E2',
                            borderwidth=0, arrowsize=11)
            style.layout(direction+'.TScrollbar', [(direction+'.Scrollbar.trough', {'sticky': sticky,
                'children': [(direction+'.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        style.configure('Horizontal.TProgressbar', background=c['primary'], troughcolor='#E5EBF4',
                        bordercolor='#E5EBF4', lightcolor=c['primary'], darkcolor=c['primary'], thickness=5)
        style.configure('Modern.TNotebook', background=c['surface'], borderwidth=0, tabmargins=(0, 0, 0, 0),
                        bordercolor=c['surface'], lightcolor=c['surface'], darkcolor=c['surface'])
        style.configure('Modern.TNotebook.Tab', background='#E8EDF5', foreground=c['muted'],
                        borderwidth=0, padding=(14, 7), font=('Microsoft YaHei UI', 9))
        style.map('Modern.TNotebook.Tab', background=[('selected', c['surface']), ('active', '#F1F5F9')],
                  foreground=[('selected', c['primary']), ('active', c['text'])])
        style.configure('Modern.Treeview', background='#FFFFFF', fieldbackground='#FFFFFF',
                        foreground='#334155', borderwidth=0, rowheight=round(29*self.ui_scale),
                        font=('Microsoft YaHei UI', 9))
        style.configure('Modern.Treeview.Heading', background='#F1F5F9', foreground='#475569',
                        relief='flat', padding=(10, 8), font=('Microsoft YaHei UI', 9, 'bold'))
        style.map('Modern.Treeview', background=[('selected', c['selection'])],
                  foreground=[('selected', c['text'])])

    def _build(self):
        app = ttk.Frame(self.root, style='App.TFrame')
        app.pack(fill='both', expand=True)

        header = ttk.Frame(app, style='Header.TFrame', padding=(20, 8))
        header.pack(fill='x')
        self.header_frame = header
        header.columnconfigure(0, weight=1)
        title_box = ttk.Frame(header, style='Header.TFrame')
        title_box.grid(row=0, column=0, sticky='w')
        ttk.Label(title_box, text='Keil Port Studio', style='HeaderTitle.TLabel').pack(side='left')
        ttk.Label(title_box, text=_tr('选择组件 · 核对文件 · 预览修改'),
                  style='HeaderSub.TLabel').pack(side='left', padx=(18, 0))
        ttk.Label(header, text=_tr('组件独立副本  /  修改前预览'), style='Badge.TLabel').grid(
            row=0, column=1, sticky='e')
        language = ttk.Combobox(header, textvariable=self.language_var,
                               values=('zh-CN', 'en'), state='readonly', width=6)
        language.grid(row=0, column=2, padx=(10, 0))
        language.bind('<<ComboboxSelected>>', lambda _e: self.change_language(self.language_var.get()))

        outer = ttk.Frame(app, style='App.TFrame', padding=(16, 10, 16, 10))
        outer.pack(fill='both', expand=True)
        self.main_frame = outer
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        project_card = ttk.Frame(outer, style='Surface.TFrame', padding=(14, 8))
        project_card.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        self.project_card = project_card
        project_card.columnconfigure(1, weight=1)
        ttk.Label(project_card, text=_tr('工程'), style='Body.TLabel').grid(
            row=0, column=0, sticky='w', padx=(0, 8))
        self.project_entry = ttk.Entry(project_card, textvariable=self.project_var)
        self.project_entry.grid(row=0, column=1, sticky='ew')
        self.project_entry.bind('<Return>', lambda _e: self._project_changed())
        ttk.Button(project_card, text=_tr('选择工程'), command=self.browse_project).grid(
            row=0, column=2, padx=(8, 12))
        ttk.Label(project_card, text='Target', style='Body.TLabel').grid(
            row=0, column=3, sticky='w', padx=(0, 7))
        self.target_combo = ttk.Combobox(project_card, textvariable=self.target_var,
                                         state='readonly', width=17,
                                         values=[_tr('全部 Target')])
        self.target_combo.grid(row=0, column=4, sticky='ew')
        ttk.Label(project_card, text=_tr('库仓库'), style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 8), pady=(6, 0))
        ttk.Entry(project_card, textvariable=self.sdk_var).grid(
            row=1, column=1, sticky='ew', pady=(6, 0))
        ttk.Button(project_card, text=_tr('选择目录'), command=lambda: self.browse_dir(self.sdk_var)).grid(
            row=1, column=2, padx=(8, 12), pady=(6, 0))
        ttk.Label(project_card, text=_tr('中间件独立复制，不改共享库'),
                  style='Muted.TLabel').grid(row=1, column=3, columnspan=2, sticky='w', pady=(6, 0))

        workspace = ttk.Frame(outer, style='App.TFrame')
        workspace.grid(row=1, column=0, sticky='nsew')
        workspace.columnconfigure(1, weight=1)
        workspace.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(workspace, style='App.TFrame', padding=(0, 4, 12, 0))
        sidebar.grid(row=0, column=0, sticky='ns')
        self.sidebar = sidebar
        nb = ttk.Notebook(workspace, style='Content.TNotebook')
        nb.grid(row=0, column=1, sticky='nsew')
        self.notebook = nb
        self._build_add_tab(nb)
        self._build_freertos_tab(nb)
        self._build_rtthread_tab(nb)
        self._build_lvgl_tab(nb)
        self._build_fatfs_tab(nb)
        self._build_settings_tab(nb)
        self._build_extensions_tab(nb)
        self._build_network_usb_tab(nb)
        self._nav_labels = [_tr('工程文件'), 'FreeRTOS', 'RT-Thread', _tr('LVGL 显示'),
                            _tr('FatFS 存储'), _tr('工程设置'), _tr('更多组件'), _tr('网络与 USB')]
        self._nav_variables = [(self.add_enabled,), (self.freertos_enabled,),
            (self.rtthread_enabled,), (self.lvgl_enabled,), (self.fatfs_enabled,),
            (self.settings_enabled,), (self.rtt_enabled, self.littlefs_enabled,
            self.cmsis_dsp_enabled, self.rtos_guard_enabled),
            (self.lwip_enabled, self.tinyusb_enabled)]
        self.nav_buttons = []
        for index, label in enumerate(self._nav_labels):
            button = ttk.Button(sidebar, text=label, width=14, style='Nav.TButton',
                                command=lambda i=index: nb.select(i))
            button.pack(fill='x', pady=(0, 3))
            self.nav_buttons.append(button)
        nb.bind('<<NotebookTabChanged>>', self._update_navigation)
        for variables in self._nav_variables:
            for variable in variables:
                self._nav_traces.append((variable, variable.trace_add('write', self._update_navigation)))

        self.log_window = tk.Toplevel(self.root)
        self.log_window.title(_tr('运行日志 · Keil Port Studio'))
        self._size_dialog(self.log_window, 850, 420)
        self.log_window.minsize(500, 250)
        self.log_window.withdraw()
        self.log_window.protocol('WM_DELETE_WINDOW', self.toggle_log)
        log_frame = ttk.Frame(self.log_window, style='Surface.TFrame', padding=(16, 10))
        log_frame.pack(fill='both', expand=True)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        ttk.Label(log_frame, text=_tr('运行日志'), style='Section.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Button(log_frame, text=_tr('清空'), style='Quiet.TButton', command=self.clear_log).grid(
            row=0, column=1, sticky='e')
        self.log_box = ScrolledText(log_frame, height=6, wrap='word', state='disabled',
                                    relief='flat', borderwidth=0, padx=12, pady=10,
                                    background='#0F172A', foreground='#CBD5E1',
                                    insertbackground='#FFFFFF', selectbackground='#334155',
                                    font=('Cascadia Mono', 9))
        self.log_box.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(7, 0))
        self.log_frame = log_frame

        bottom = ttk.Frame(outer, style='App.TFrame')
        bottom.grid(row=3, column=0, sticky='ew', pady=(10, 0))
        self.bottom_frame = bottom
        bottom.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(bottom, textvariable=self.status_var, style='Status.TLabel')
        self.status_label.grid(row=0, column=0, sticky='ew', padx=(0, 12))
        self.status_label.bind('<Configure>', lambda e: self.status_label.configure(wraplength=max(200, e.width)))
        self.selection_summary = tk.StringVar(value=_tr('已启用 1 项'))
        ttk.Label(bottom, textvariable=self.selection_summary, style='Status.TLabel').grid(row=0, column=1, sticky='e')
        actions = ttk.Frame(bottom, style='App.TFrame')
        actions.grid(row=1, column=0, sticky='ew', pady=(7, 0))
        for label, command in ((_tr('设置'), self.open_settings), (_tr('预设'), self.open_presets),
                                (_tr('工程工具'), self.open_project_tools), (_tr('安全与恢复'), self.open_safety_manager),
                                ('Git', self.open_git), (_gt('指南', 'Guide'), self.open_guides)):
            ttk.Button(actions, text=label, style='Quiet.TButton', command=command).pack(side='left', padx=(0, 6))
        self.log_toggle_button = ttk.Button(actions, text=_tr('查看日志'), style='Quiet.TButton',
                                            command=self.toggle_log)
        self.log_toggle_button.pack(side='left')
        self.execute_button = ttk.Button(bottom, text=_tr('预览并执行  →'), style='Primary.TButton', command=self.execute)
        self.execute_button.grid(row=1, column=1, columnspan=2, sticky='e', pady=(7, 0))
        self.progress_bar = ttk.Progressbar(bottom, variable=self.progress_var, maximum=100,
                                            length=110)
        self.progress_bar.grid(row=0, column=2, sticky='e', padx=(12, 0))
        self._update_navigation()

    def _update_navigation(self, *_args):
        current = self.notebook.index(self.notebook.select())
        count = 0
        for index, (button, label, variables) in enumerate(zip(self.nav_buttons, self._nav_labels, self._nav_variables)):
            enabled = sum(v.get() for v in variables)
            count += enabled
            button.configure(text=label + ('  ✓' if enabled else ''))
            button.state(['selected'] if index == current else ['!selected'])
        if hasattr(self, 'selection_summary'):
            conflict = self.freertos_enabled.get() and self.rtthread_enabled.get()
            self.selection_summary.set(_tr('请选择一种 RTOS') if conflict else _tr('已启用 %d 项') % count)
            self.execute_button.state(['disabled'] if conflict or not count else ['!disabled'])

    def change_language(self, language):
        global _UI_LANGUAGE
        if getattr(self, '_operation_busy', False):
            self.language_var.set(_UI_LANGUAGE)
            return
        if getattr(self, 'git_panel', None) and self.git_panel.busy:
            self.language_var.set(_UI_LANGUAGE)
            return
        if language not in ('zh-CN', 'en') or language == _UI_LANGUAGE:
            return
        candidate = dict(self.user_settings, language=language)
        try:
            save_user_settings(candidate)
        except OSError as e:
            self.language_var.set(_UI_LANGUAGE)
            messagebox.showerror('Language', str(e), parent=self.root)
            return
        snapshots = {}
        for name, tree in vars(self).items():
            if isinstance(tree, CheckTree) and tree.root_path:
                snapshots[name] = (tree.root_path,
                    [(p, _ui_canonical(tree.tree.item(i, 'values')[0])) for i, p in tree._path.items()],
                    tree.selection_keys())
        page = self.notebook.index(self.notebook.select())
        logs = self.log_box.get('1.0', 'end-1c')
        enum_vars = (self.target_var, self.fatfs_mode, self.littlefs_mode, self.lwip_mode,
                     self.lwip_driver, self.optimization_var, self.debug_info_var)
        values = [_ui_canonical(v.get()) for v in enum_vars]
        for variable, token in self._nav_traces:
            variable.trace_remove('write', token)
        self._nav_traces = []
        for child in self.root.winfo_children():
            child.destroy()
        self.user_settings = candidate
        _UI_LANGUAGE = language
        for variable, value in zip(enum_vars, values):
            variable.set(_tr(value))
        self.language_var.set(language)
        self.log_visible = False
        self._build()
        if self.project_var.get():
            self._project_changed()
        for name, (root, files, keys) in snapshots.items():
            tree = getattr(self, name)
            tree.set_files(root, [(path, _tr(note)) for path, note in files])
            tree.apply_selection_keys(keys)
        self.notebook.select(page)
        if logs:
            self.append_log(logs)
        self.status_var.set('Language: English' if language == 'en' else '语言：简体中文')

    def open_git(self):
        panel = getattr(self, 'git_panel', None)
        if panel and panel.window.winfo_exists():
            panel.window.lift()
            return
        folder = Path(self.project_var.get()) if self.project_var.get() else Path.cwd()
        if folder.is_file():
            try:
                folder = project_content_root(self._project())
            except Exception:
                folder = folder.parent
        self.git_panel = GitPanel(self, folder)

    def open_guides(self):
        win = tk.Toplevel(self.root)
        win.title(_gt('离线使用指南', 'Offline guides'))
        self._size_dialog(win, 900, 700)
        notebook = ttk.Notebook(win, style='Modern.TNotebook')
        notebook.pack(fill='both', expand=True, padx=12, pady=12)
        base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'docs'
        for stem, label in (('GUI', _gt('界面使用', 'Interface')),
                            ('POST-PORTING', _gt('移植后必做', 'After porting')),
                            ('GIT', _gt('Git 使用', 'Git workflow'))):
            view = ScrolledText(notebook, wrap='word', padx=16, pady=16, font=('Microsoft YaHei UI', 10))
            notebook.add(view, text=label)
            path = base / (stem + '.' + _UI_LANGUAGE + '.md')
            try:
                content = path.read_text(encoding='utf-8')
            except OSError:
                content = _gt('未找到文档。请将发布包内 docs 目录与脚本放在一起：\n',
                              'Guide not found. Keep the distribution docs directory beside the script:\n') + str(path)
            view.insert('1.0', content)
            view.configure(state='disabled')

    def _task_header(self, parent, title, description, variable):
        box = ttk.Frame(parent, style='Surface.TFrame')
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text=title, style='TaskTitle.TLabel').grid(row=0, column=0, sticky='w')
        detail = ttk.Label(box, text=description, style='Muted.TLabel')
        detail.grid(row=1, column=0, sticky='ew', pady=(3, 0))
        box.bind('<Configure>', lambda e: detail.configure(wraplength=max(240, e.width-round(135*self.ui_scale))))
        ttk.Checkbutton(box, text=_tr('启用此功能'), variable=variable,
                        style='Modern.TCheckbutton').grid(row=0, column=1, rowspan=2, sticky='e')
        return box

    def _tree_buttons(self, parent, tree, scan_command):
        bar = ttk.Frame(parent, style='Surface.TFrame')
        ttk.Button(bar, text=_tr('↻  扫描刷新'), command=scan_command).pack(side='left')
        ttk.Button(bar, text=_tr('全选'), style='Quiet.TButton',
                   command=lambda: tree.select_all(True)).pack(side='left', padx=(8, 2))
        ttk.Button(bar, text=_tr('清空选择'), style='Quiet.TButton',
                   command=lambda: tree.select_all(False)).pack(side='left')
        ttk.Label(bar, textvariable=tree.summary_var, style='Muted.TLabel').pack(side='left', padx=10)
        return bar

    def _build_add_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(16, 10))
        nb.add(tab, text=_tr('01   工程文件'))
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(2, weight=1)
        self._task_header(tab, _tr('添加工程文件与静态库'),
                          _tr('为 C/C++、汇编、库与头文件添加路径引用；外部文件请先复制到工程。'),
                          self.add_enabled).grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0, 13))
        ttk.Label(tab, text=_tr('扫描目录'), style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 12))
        ttk.Entry(tab, textvariable=self.scan_var).grid(row=1, column=1, sticky='ew')
        ttk.Button(tab, text=_tr('添加目录'), command=self.add_scan_dir).grid(
            row=1, column=2, padx=(10, 0))
        self.add_tree = CheckTree(tab, height=15)
        self.add_tree.grid(row=2, column=0, columnspan=3, sticky='nsew', pady=(13, 0))
        bar = self._tree_buttons(tab, self.add_tree, self.scan_new_files)
        bar.grid(row=3, column=0, columnspan=3, sticky='w', pady=(9, 0))
        ttk.Checkbutton(bar, text=_tr('头文件也显示在工程树'), variable=self.include_h,
                        style='Modern.TCheckbutton').pack(side='left', padx=(10, 0))

    def _build_freertos_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(16, 10))
        nb.add(tab, text='02   FreeRTOS')
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)
        self._task_header(tab, 'FreeRTOS + CMSIS-RTOS V2',
                          _tr('创建工程独立副本、任务入口和默认任务，并自动启动调度器。'),
                          self.freertos_enabled).grid(row=0, column=0, columnspan=4, sticky='ew', pady=(0, 13))
        ttk.Label(tab, text=_tr('共享源码'), style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 12))
        ttk.Entry(tab, textvariable=self.freertos_dir).grid(row=1, column=1, sticky='ew')
        ttk.Button(tab, text=_tr('浏览'), command=lambda: self.browse_dir(self.freertos_dir)).grid(
            row=1, column=2, padx=(10, 0))
        ttk.Button(tab, text=_tr('自动准备'), command=self.prepare_freertos).grid(
            row=1, column=3, padx=(7, 0))
        opts = ttk.Frame(tab, style='Surface.TFrame')
        opts.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(11, 10))
        ttk.Label(opts, text=_tr('内存方案'), style='Body.TLabel').pack(side='left')
        ttk.Combobox(opts, textvariable=self.heap_var, width=11, state='readonly',
                     values=['heap_1.c', 'heap_2.c', 'heap_3.c', 'heap_4.c', 'heap_5.c']).pack(side='left', padx=(5, 16))
        ttk.Label(opts, text=_tr('CPU 移植层'), style='Body.TLabel').pack(side='left')
        self.port_combo = ttk.Combobox(opts, textvariable=self.port_var, width=17)
        self.port_combo.pack(side='left', padx=5)
        ttk.Checkbutton(opts, text=_tr('任务入口 / 自动调度'),
                        variable=self.freertos_app, style='Modern.TCheckbutton').pack(side='left', padx=(8, 0))
        self.freertos_tree = CheckTree(tab, height=15)
        self.freertos_tree.grid(row=3, column=0, columnspan=4, sticky='nsew')
        self._tree_buttons(tab, self.freertos_tree, self.prepare_freertos).grid(
            row=4, column=0, columnspan=4, sticky='w', pady=(7, 0))

    def _build_rtthread_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(16, 10))
        nb.add(tab, text='RT-Thread')
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)
        self._task_header(tab, _tr('RT-Thread 5.2.2 · 单核内核'),
                          _tr('Cortex-M4；工程独立副本、任务入口、自动调度启动。与 FreeRTOS 互斥。'),
                          self.rtthread_enabled).grid(row=0, column=0, columnspan=4, sticky='ew')
        ttk.Label(tab, text=_tr('共享源码'), style='Body.TLabel').grid(row=1, column=0, padx=(0, 12))
        ttk.Entry(tab, textvariable=self.rtthread_dir).grid(row=1, column=1, sticky='ew')
        ttk.Button(tab, text=_tr('浏览'), command=lambda: self.browse_dir(self.rtthread_dir)).grid(row=1, column=2)
        ttk.Button(tab, text=_tr('自动准备'), command=self.prepare_rtthread).grid(row=1, column=3)
        ttk.Label(tab, text=_tr('默认全选。核心依赖不可缺失；配置与任务文件重装时保留。不是完整 BSP/Env 安装器。'),
                  style='Muted.TLabel').grid(row=2, column=0, columnspan=4, sticky='w', pady=10)
        self.rtthread_tree = CheckTree(tab, height=15)
        self.rtthread_tree.grid(row=3, column=0, columnspan=4, sticky='nsew')
        self._tree_buttons(tab, self.rtthread_tree, self.prepare_rtthread).grid(row=4, column=0, columnspan=4, sticky='w')

    def prepare_rtthread(self):
        try:
            proj = self._project()
            specified = self.rtthread_dir.get().strip()
            root = (locate_rtthread_root(specified) if specified else
                    self._background_call(ensure_rtthread_sdk, proj, self._sdk_opts(), Report('rtthread')))
            if not root:
                raise ToolError(_tr('未找到 RT-Thread 内核目录'))
            root = Path(root).resolve()
            self.rtthread_dir.set(str(root))
            self.rtthread_root = root
            notes = {'ipc.c': _tr('信号量 / 互斥量 / 事件 / 邮箱 / 消息队列（核心）'),
                     'mempool.c': _tr('固定块内存池（可选）'), 'signal.c': _tr('信号（默认配置关闭）'),
                     'slab.c': _tr('slab 分配器（默认配置关闭）'),
                     'memheap.c': _tr('多堆分配器（默认配置关闭）'),
                     'components.c': _tr('自动初始化框架（默认配置关闭）')}
            self.rtthread_tree.set_files(root, [(p, notes.get(p.name, _tr('内核/端口依赖')))
                                                for p in rtthread_source_files(root) if p.is_file()])
            self._restore_tree_preset('rtthread_tree', self.rtthread_tree)
            self.status_var.set(_tr('RT-Thread 文件已列出，默认全选；请核对需要的组件。'))
        except Exception as e:
            messagebox.showerror(_tr('RT-Thread 准备失败'), str(e), parent=self.root)

    def _build_lvgl_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(16, 10))
        nb.add(tab, text='03   LVGL')
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)
        self._task_header(tab, _tr('LVGL 图形库'),
                          _tr('复制独立源码、生成配置，并按模块选择要加入工程的组件。'),
                          self.lvgl_enabled).grid(row=0, column=0, columnspan=4,
                                                  sticky='ew', pady=(0, 13))
        ttk.Label(tab, text=_tr('共享源码'), style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 12))
        ttk.Entry(tab, textvariable=self.lvgl_dir).grid(row=1, column=1, sticky='ew')
        ttk.Button(tab, text=_tr('浏览'), command=lambda: self.browse_dir(self.lvgl_dir)).grid(
            row=1, column=2, padx=(10, 0))
        ttk.Button(tab, text=_tr('自动准备'), command=self.prepare_lvgl).grid(
            row=1, column=3, padx=(7, 0))
        opts = ttk.Frame(tab, style='Surface.TFrame')
        opts.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(11, 10))
        ttk.Label(opts, text=_tr('颜色深度'), style='Body.TLabel').pack(side='left')
        ttk.Combobox(opts, textvariable=self.color_var, state='readonly', width=9,
                     values=['8', '16', '32']).pack(side='left', padx=(5, 16))
        ttk.Checkbutton(opts, text=_tr('复制显示/输入设备 porting 模板'), variable=self.lv_ports,
                        style='Modern.TCheckbutton').pack(side='left')
        self.lvgl_tree = CheckTree(tab, height=15)
        self.lvgl_tree.grid(row=3, column=0, columnspan=4, sticky='nsew')
        self._tree_buttons(tab, self.lvgl_tree, self.prepare_lvgl).grid(
            row=4, column=0, columnspan=4, sticky='w', pady=(7, 0))

    def _build_fatfs_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(16, 10))
        nb.add(tab, text='04   FatFS')
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)
        self._task_header(tab, _tr('FatFS 文件系统'),
                          _tr('复制独立源码，根据工程 RTOS 自动选择线程安全或裸机适配。'),
                          self.fatfs_enabled).grid(row=0, column=0, columnspan=4,
                                                  sticky='ew', pady=(0, 13))
        ttk.Label(tab, text=_tr('共享源码'), style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 12))
        ttk.Entry(tab, textvariable=self.fatfs_dir).grid(row=1, column=1, sticky='ew')
        ttk.Button(tab, text=_tr('浏览'), command=lambda: self.browse_dir(self.fatfs_dir)).grid(
            row=1, column=2, padx=(10, 0))
        ttk.Button(tab, text=_tr('自动准备'), command=self.prepare_fatfs).grid(
            row=1, column=3, padx=(7, 0))
        opts = ttk.Frame(tab, style='Surface.TFrame')
        opts.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(11, 10))
        ttk.Label(opts, text=_tr('运行模式'), style='Body.TLabel').pack(side='left')
        mode_combo = ttk.Combobox(opts, textvariable=self.fatfs_mode, state='readonly', width=18,
                                  values=[_tr('自动判断'), _tr('RTOS（工程内核）'), _tr('裸机')])
        mode_combo.pack(side='left', padx=(5, 16))
        mode_combo.bind('<<ComboboxSelected>>', lambda _e: self.prepare_fatfs()
                        if self.fatfs_root else None)
        ttk.Checkbutton(opts, text=_tr('生成应用框架并接入初始化'),
                        variable=self.fatfs_app, style='Modern.TCheckbutton').pack(side='left')
        self.fatfs_tree = CheckTree(tab, height=15)
        self.fatfs_tree.grid(row=3, column=0, columnspan=4, sticky='nsew')
        self._tree_buttons(tab, self.fatfs_tree, self.prepare_fatfs).grid(
            row=4, column=0, columnspan=4, sticky='w', pady=(7, 0))

    def _build_settings_tab(self, nb):
        shell = ttk.Frame(nb, style='Surface.TFrame')
        nb.add(shell, text=_tr('05   工程设置'))
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        canvas = tk.Canvas(shell, background=self.colors['surface'], highlightthickness=0,
                           borderwidth=0, yscrollincrement=16)
        scroll = ttk.Scrollbar(shell, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        tab = ttk.Frame(canvas, style='Surface.TFrame', padding=(16, 10))
        item = canvas.create_window((0, 0), window=tab, anchor='nw')
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(item, width=e.width))
        tab.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        self.settings_canvas = canvas
        self.settings_body = tab
        def wheel(event):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
            return 'break'
        def reveal_focus(event):
            if not event.widget.winfo_ismapped():
                return
            top = event.widget.winfo_rooty()-canvas.winfo_rooty()
            bottom = top+event.widget.winfo_height()
            delta = top if top < 0 else bottom-canvas.winfo_height() if bottom > canvas.winfo_height() else 0
            if delta:
                canvas.yview_scroll(int(delta/16)+(1 if delta > 0 else -1), 'units')
        def bind_scroll(widget):
            if not isinstance(widget, ttk.Combobox):
                widget.bind('<MouseWheel>', wheel, add='+')
            widget.bind('<FocusIn>', reveal_focus, add='+')
            for child in widget.winfo_children():
                bind_scroll(child)
        self.root.after_idle(lambda: bind_scroll(tab) if tab.winfo_exists() else None)
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(3, weight=1)
        self._task_header(tab, _tr('Target 级编译与链接设置'),
                          _tr('宏、优化、调试信息、Include 清理、Stack/Heap 与分散加载文件。'),
                          self.settings_enabled).grid(row=0, column=0, columnspan=5,
                                                      sticky='ew', pady=(0, 16))
        ttk.Label(tab, text=_tr('添加/替换宏'), style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 10), pady=5)
        ttk.Entry(tab, textvariable=self.define_var).grid(
            row=1, column=1, columnspan=4, sticky='ew', pady=5)
        ttk.Label(tab, text=_tr('多个宏用 ; 分隔，例如 LOG_LEVEL=2;USE_DSP'),
                  style='Muted.TLabel').grid(row=2, column=1, columnspan=4, sticky='w')
        ttk.Label(tab, text=_tr('删除宏'), style='Body.TLabel').grid(
            row=3, column=0, sticky='w', padx=(0, 10), pady=(12, 5))
        ttk.Entry(tab, textvariable=self.remove_define_var).grid(
            row=3, column=1, columnspan=4, sticky='ew', pady=(12, 5))

        ttk.Label(tab, text=_tr('优化等级'), style='Body.TLabel').grid(
            row=4, column=0, sticky='w', padx=(0, 10), pady=(14, 5))
        ttk.Combobox(tab, textvariable=self.optimization_var, state='readonly', width=14,
                     values=[_tr('保持不变'), 'O0', 'O1', 'O2', 'O3', 'Os', 'Oz']).grid(
                         row=4, column=1, sticky='w', pady=(14, 5))
        ttk.Label(tab, text=_tr('调试信息'), style='Body.TLabel').grid(
            row=4, column=2, sticky='e', padx=(18, 10), pady=(14, 5))
        ttk.Combobox(tab, textvariable=self.debug_info_var, state='readonly', width=14,
                     values=[_tr('保持不变'), _tr('开启'), _tr('关闭')]).grid(
                         row=4, column=3, sticky='w', pady=(14, 5))

        ttk.Label(tab, text='Stack Size', style='Body.TLabel').grid(
            row=5, column=0, sticky='w', padx=(0, 10), pady=5)
        ttk.Entry(tab, textvariable=self.stack_size_var, width=18).grid(
            row=5, column=1, sticky='ew', pady=5)
        ttk.Label(tab, text='Heap Size', style='Body.TLabel').grid(
            row=5, column=2, sticky='e', padx=(18, 10), pady=5)
        ttk.Entry(tab, textvariable=self.heap_size_var, width=18).grid(
            row=5, column=3, sticky='ew', pady=5)
        ttk.Label(tab, text=_tr('支持 0x400 或十进制；修改所选 Target 引用的 startup*.s'),
                  style='Muted.TLabel').grid(row=6, column=1, columnspan=4, sticky='w')

        ttk.Label(tab, text=_tr('Scatter 文件'), style='Body.TLabel').grid(
            row=7, column=0, sticky='w', padx=(0, 10), pady=(14, 5))
        ttk.Entry(tab, textvariable=self.scatter_var).grid(
            row=7, column=1, columnspan=3, sticky='ew', pady=(14, 5))
        ttk.Button(tab, text=_tr('选择 .sct'), command=self.browse_scatter).grid(
            row=7, column=4, padx=(10, 0), pady=(14, 5))

        checks = ttk.Frame(tab, style='Surface.TFrame')
        checks.grid(row=8, column=0, columnspan=5, sticky='w', pady=(12, 0))
        ttk.Checkbutton(checks, text=_tr('Include 路径去重'), variable=self.clean_includes_var,
                        style='Modern.TCheckbutton').pack(side='left')
        ttk.Checkbutton(checks, text=_tr('同时移除不存在的 Include 路径'),
                        variable=self.remove_missing_includes_var,
                        style='Modern.TCheckbutton').pack(side='left', padx=(12, 0))
        ttk.Checkbutton(checks, text=_tr('清除自定义 Scatter，恢复 Target 内存布局'),
                        variable=self.clear_scatter_var,
                        style='Modern.TCheckbutton').pack(side='left', padx=(12, 0))

    def _extension_source_page(self, parent, title, description, enabled, variable,
                               prepare_command, tree_name, extra_builder=None):
        page = ttk.Frame(parent, style='Surface.TFrame', padding=(14, 9))
        page.columnconfigure(1, weight=1)
        page.rowconfigure(3, weight=1)
        self._task_header(page, title, description, enabled).grid(
            row=0, column=0, columnspan=4, sticky='ew', pady=(0, 9))
        ttk.Label(page, text=_tr('共享源码'), style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 10))
        ttk.Entry(page, textvariable=variable).grid(row=1, column=1, sticky='ew')
        ttk.Button(page, text=_tr('浏览'), command=lambda: self.browse_dir(variable)).grid(
            row=1, column=2, padx=(8, 0))
        ttk.Button(page, text=_tr('自动准备'), command=prepare_command).grid(
            row=1, column=3, padx=(6, 0))
        if extra_builder:
            extra_builder(page)
        tree = CheckTree(page, height=10)
        tree.grid(row=3, column=0, columnspan=4, sticky='nsew', pady=(7, 0))
        self._tree_buttons(page, tree, prepare_command).grid(
            row=4, column=0, columnspan=4, sticky='w', pady=(6, 0))
        setattr(self, tree_name, tree)
        return page

    def _build_extensions_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(0, 0))
        nb.add(tab, text=_tr('06   扩展组件'))
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        inner = ttk.Notebook(tab, style='Content.TNotebook')
        inner.grid(row=1, column=0, sticky='nsew')

        rtt = self._extension_source_page(
            inner, _tr('SEGGER RTT 高速日志'),
            _tr('核心、格式化输出、ARM 汇编加速和 Keil printf 重定向均可单独取消。'),
            self.rtt_enabled, self.rtt_dir, self.prepare_rtt, 'rtt_tree')
        inner.add(rtt, text=_tr('RTT 日志'))

        def littlefs_extra(page):
            bar = ttk.Frame(page, style='Surface.TFrame')
            bar.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(7, 0))
            ttk.Label(bar, text=_tr('运行模式'), style='Body.TLabel').pack(side='left')
            combo = ttk.Combobox(bar, textvariable=self.littlefs_mode, state='readonly', width=18,
                                 values=[_tr('自动判断'), _tr('RTOS（工程内核）'), _tr('裸机')])
            combo.pack(side='left', padx=(6, 15))
            combo.bind('<<ComboboxSelected>>', lambda _e: self.prepare_littlefs()
                       if self.littlefs_root else None)
            ttk.Checkbutton(bar, text=_tr('生成 littlefs_port.c/h Flash 模板'),
                            variable=self.littlefs_port,
                            style='Modern.TCheckbutton').pack(side='left')

        littlefs = self._extension_source_page(
            inner, _tr('LittleFS Flash 文件系统'),
            _tr('复制独立源码，自动适配工程 RTOS；模板不会自动格式化 Flash。'),
            self.littlefs_enabled, self.littlefs_dir, self.prepare_littlefs,
            'littlefs_tree', littlefs_extra)
        inner.add(littlefs, text='LittleFS')

        def dsp_extra(page):
            bar = ttk.Frame(page, style='Surface.TFrame')
            bar.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(7, 0))
            ttk.Checkbutton(bar, text=_tr('AC6 启用 Float16 模块'), variable=self.dsp_float16,
                            style='Modern.TCheckbutton').pack(side='left')
            ttk.Label(bar, text=_tr('AC5 自动使用兼容的 CMSIS-DSP 1.10，并禁用 Float16'),
                      style='Muted.TLabel').pack(side='left', padx=(14, 0))

        dsp = self._extension_source_page(
            inner, 'CMSIS-DSP / ARM Math',
            _tr('按算法目录选择官方聚合编译单元，自动添加 Include 与 Cortex-M 宏。'),
            self.cmsis_dsp_enabled, self.cmsis_dsp_dir, self.prepare_cmsis_dsp,
            'cmsis_dsp_tree', dsp_extra)
        inner.add(dsp, text='CMSIS-DSP')

        guard = ttk.Frame(inner, style='Surface.TFrame', padding=(18, 14))
        guard.columnconfigure(0, weight=1)
        self._task_header(
            guard, _tr('RTOS 外设线程安全层'),
            _tr('生成互斥锁 API，自动接入 FreeRTOS 或 RT-Thread 初始化入口。'),
            self.rtos_guard_enabled).grid(row=0, column=0, sticky='ew', pady=(0, 18))
        ttk.Label(guard, text=_tr('选择需要独立保护的外设'), style='Section.TLabel').grid(
            row=1, column=0, sticky='w')
        choices = ttk.Frame(guard, style='Surface.TFrame')
        choices.grid(row=2, column=0, sticky='w', pady=(10, 16))
        for index, name in enumerate(('UART', 'SPI', 'I2C', 'FLASH')):
            ttk.Checkbutton(choices, text=name, variable=self.guard_vars[name],
                            style='Modern.TCheckbutton').grid(row=0, column=index, padx=(0, 18))
        ttk.Label(guard,
                  text=_tr('将生成 rtos_peripheral_guard.c/h。调用外设 HAL 函数前 Lock，完成后 Unlock；'
                       '不会绑定特定 STM32 HAL 句柄。'),
                  style='Muted.TLabel', wraplength=820, justify='left').grid(
                      row=3, column=0, sticky='w')
        inner.add(guard, text=_tr('外设线程安全'))
        self.extensions_notebook = inner
        self.extension_buttons = self._sub_navigation(tab, inner, (_tr('RTT 日志'), 'LittleFS', 'CMSIS-DSP', _tr('外设锁')))

    def _sub_navigation(self, parent, notebook, labels):
        bar = ttk.Frame(parent, style='Surface.TFrame', padding=(14, 4, 14, 0))
        bar.grid(row=0, column=0, sticky='ew')
        buttons = []
        for index, label in enumerate(labels):
            button = ttk.Button(bar, text=label, style='Segment.TButton',
                                command=lambda i=index: notebook.select(i))
            button.pack(side='left', padx=(0, 6))
            buttons.append(button)
        def changed(_event=None):
            selected = notebook.index(notebook.select())
            for index, button in enumerate(buttons):
                button.state(['selected'] if index == selected else ['!selected'])
        notebook.bind('<<NotebookTabChanged>>', changed)
        changed()
        return buttons

    def _build_network_usb_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(0, 0))
        nb.add(tab, text=_tr('07   网络与 USB'))
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        inner = ttk.Notebook(tab, style='Content.TNotebook')
        inner.grid(row=1, column=0, sticky='nsew')

        def lwip_extra(page):
            bar = ttk.Frame(page, style='Surface.TFrame')
            bar.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(7, 0))
            ttk.Label(bar, text=_tr('运行模式'), style='Body.TLabel').pack(side='left')
            combo = ttk.Combobox(bar, textvariable=self.lwip_mode, state='readonly', width=18,
                                 values=[_tr('自动判断'), _tr('RTOS（工程内核）'), _tr('裸机')])
            combo.pack(side='left', padx=(6, 15))
            combo.bind('<<ComboboxSelected>>', lambda _e: self.prepare_lwip()
                       if self.lwip_root else None)
            ttk.Checkbutton(bar, text=_tr('启用 IPv6'), variable=self.lwip_ipv6,
                            command=lambda: self.prepare_lwip() if self.lwip_root else None,
                            style='Modern.TCheckbutton').pack(side='left')
            ttk.Label(bar, text=_tr('网卡'), style='Body.TLabel').pack(side='left', padx=(15, 0))
            ttk.Combobox(bar, textvariable=self.lwip_driver, state='readonly', width=15,
                         values=[_tr('自动选择'), 'STM32 ETH', 'ENC28J60', 'W5500 MACRAW',
                                 _tr('通用以太网')]).pack(side='left', padx=(6, 0))

        lwip = self._extension_source_page(
            inner, _tr('LwIP TCP/IP 协议栈'),
            _tr('核心、IPv4/IPv6、RTOS API、HTTP、MQTT 等均在下方详细列出。'),
            self.lwip_enabled, self.lwip_dir, self.prepare_lwip, 'lwip_tree', lwip_extra)
        inner.add(lwip, text=_tr('LwIP 网络'))

        def tinyusb_extra(page):
            bar = ttk.Frame(page, style='Surface.TFrame')
            bar.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(7, 0))
            ttk.Label(bar, text=_tr('协议角色'), style='Body.TLabel').pack(side='left')
            combo = ttk.Combobox(bar, textvariable=self.tinyusb_mode, state='readonly', width=10,
                                 values=['Device', 'Host', 'Device + Host'])
            combo.pack(side='left', padx=(6, 15))
            combo.bind('<<ComboboxSelected>>', lambda _e: self.prepare_tinyusb()
                       if self.tinyusb_root else None)
            ttk.Label(bar, text=_tr('设备类'), style='Body.TLabel').pack(side='left')
            for name in TINYUSB_CLASSES:
                ttk.Checkbutton(bar, text=name, variable=self.tinyusb_class_vars[name],
                                command=lambda: self.prepare_tinyusb() if self.tinyusb_root else None,
                                style='Modern.TCheckbutton').pack(side='left', padx=(7, 0))

        tinyusb = self._extension_source_page(
            inner, 'TinyUSB Device / Host',
            _tr('根据 STM32 型号选择 USB 控制器驱动；协议类源码仍可逐文件取消。'),
            self.tinyusb_enabled, self.tinyusb_dir, self.prepare_tinyusb,
            'tinyusb_tree', tinyusb_extra)
        inner.add(tinyusb, text='TinyUSB')
        self.network_usb_notebook = inner
        self.network_buttons = self._sub_navigation(tab, inner, (_tr('LwIP 网络'), 'TinyUSB'))

    def clear_log(self):
        self.log_box.configure(state='normal')
        self.log_box.delete('1.0', 'end')
        self.log_box.configure(state='disabled')

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_window.deiconify()
            self.log_window.lift()
            self.log_toggle_button.configure(text=_tr('隐藏日志'))
        else:
            self.log_window.withdraw()
            self.log_toggle_button.configure(text=_tr('查看日志'))
        self.root.update_idletasks()

    def append_log(self, msg):
        if not hasattr(self, 'log_box'):
            return
        self.log_box.configure(state='normal')
        self.log_box.insert('end', str(msg) + '\n')
        self.log_box.see('end')
        self.log_box.configure(state='disabled')
        self.root.update_idletasks()

    def update_progress(self, value, message=''):
        self.progress_var.set(int(value))
        if message:
            self.status_var.set(message)
        self.root.update_idletasks()

    def _close(self):
        global _LOG_SINK, _PROGRESS_SINK
        if getattr(self, '_operation_busy', False):
            return  # Never terminate a writer halfway through its transaction.
        if getattr(self, 'git_panel', None) and self.git_panel.busy:
            self.git_panel.close()
            return
        _LOG_SINK = None
        _PROGRESS_SINK = None
        self.root.destroy()

    def _background_call(self, function, *args, **kwargs):
        """Run pure backend work in one worker; marshal all UI back to Tk.

        Callers must snapshot Tk variables before passing arguments. A modal
        window prevents re-entry; wait_variable keeps Tk's event loop running.
        No forced cancellation during writes: cancellation is at diff preview.
        """
        if threading.current_thread() is not threading.main_thread():
            raise ToolError('Background operations must be started on the UI thread')
        if getattr(self, '_operation_busy', False):
            raise ToolError('Another project operation is still running')
        if getattr(self, 'git_panel', None) and self.git_panel.busy:
            raise ToolError('Wait for the Git operation to finish')
        # Reclaim unreachable widgets/Variables from closed dialogs on Tk's
        # owning thread, before a worker allocation can trigger cyclic GC.
        gc.collect()
        events = queue.Queue()
        completed = tk.BooleanVar(master=self.root, value=False)
        outcome = {}
        win = tk.Toplevel(self.root)
        win.title(_gt('正在处理', 'Working'))
        win.transient(self.root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=22)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=_gt('操作正在后台执行，界面仍可响应。',
                                  'The operation is running in the background.')).pack(anchor='w')
        ttk.Label(frame, text=_gt('请勿断电或强行结束程序；写入前仍需确认差异。',
                                  'Do not force-close. Review changes before writing.')).pack(anchor='w', pady=8)
        spinner = ttk.Progressbar(frame, mode='indeterminate', length=430)
        spinner.pack(fill='x')
        spinner.start(30)
        ttk.Label(frame, textvariable=self.status_var, wraplength=430).pack(anchor='w', pady=(8, 0))
        win.protocol('WM_DELETE_WINDOW', lambda: None)
        previous_grab = self.root.grab_current()
        win.grab_set()
        self._operation_busy = True

        def ui_call(callback, *call_args, **call_kwargs):
            ready, reply = threading.Event(), {}
            events.put(('call', (callback, call_args, call_kwargs, ready, reply)))
            ready.wait()
            if 'error' in reply:
                raise reply['error']
            return reply.get('value')

        def work():
            try:
                with operation_context(lambda text: events.put(('log', text)),
                                       lambda value, text: events.put(('progress', (value, text))),
                                       ui_call, _LOG_LEVEL):
                    result = function(*args, **kwargs)
                events.put(('done', {'value': result}))
            except BaseException as error:
                events.put(('done', {'error': error}))

        def poll():
            for _ in range(200):
                try:
                    kind, data = events.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == 'call':
                        callback, call_args, call_kwargs, ready, reply = data
                        try:
                            win.withdraw()
                            reply['value'] = callback(*call_args, **call_kwargs)
                        except BaseException as error:
                            reply['error'] = error
                        finally:
                            win.deiconify()
                            win.grab_set()
                            ready.set()
                    elif kind == 'log':
                        self.append_log(data)
                    elif kind == 'progress':
                        self.update_progress(*data)
                    elif kind == 'done':
                        outcome.update(data)
                        completed.set(True)
                        return
                except Exception as error:
                    # A broken status widget must not strand a live transaction.
                    print('GUI event error: %s' % error)
            self.root.after(25, poll)

        thread = threading.Thread(target=work, name='KPS-project-operation', daemon=False)
        try:
            thread.start()
            self.root.after(0, poll)
            self.root.wait_variable(completed)
        finally:
            # Break poll's self-referential closure while still on Tk's thread.
            # Otherwise a later worker GC can finalize old Tk Variables there.
            poll = None
            if thread.ident is not None:
                thread.join()
            spinner.stop()
            win.destroy()
            self._operation_busy = False
            if previous_grab is not None and previous_grab.winfo_exists():
                previous_grab.grab_set()
        if 'error' in outcome:
            raise outcome['error']
        return outcome.get('value')

    def _size_dialog(self, window, width, height):
        width = min(round(width*self.ui_scale), self.root.winfo_screenwidth()-60)
        height = min(round(height*self.ui_scale), self.root.winfo_screenheight()-90)
        self.root.update_idletasks()
        x = max(0, min(self.root.winfo_rootx() + (self.root.winfo_width()-width)//2,
                       self.root.winfo_screenwidth()-width-20))
        y = max(0, min(self.root.winfo_rooty() + (self.root.winfo_height()-height)//2,
                       self.root.winfo_screenheight()-height-60))
        window.geometry('%dx%d+%d+%d' % (width, height, x, y))

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title(_tr('Keil Port Studio 设置'))
        self._size_dialog(win, 720, 430)
        win.transient(self.root)
        frame = ttk.Frame(win, style='Surface.TFrame', padding=20)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=_tr('下载与开发环境'), style='TaskTitle.TLabel').grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 14))
        retries = tk.StringVar(value=str(self.user_settings.get('download_retries', 3)))
        proxy = tk.StringVar(value=str(self.user_settings.get('proxy', '')))
        mirror = tk.StringVar(value=str(self.user_settings.get('mirror_prefix', '')))
        skip_dirs = tk.StringVar(value=str(self.user_settings.get('extra_scan_skip_dirs', '')))
        uv4 = tk.StringVar(value=str(self.user_settings.get('uv4_path', '')))
        rows = (
            (_tr('下载重试次数'), retries, _tr('1–10；网络失败会自动重试')),
            (_tr('HTTP/HTTPS 代理'), proxy, _tr('例如 http://127.0.0.1:7890；留空为系统直连')),
            (_tr('GitHub 镜像前缀'), mirror, _tr('只改写 GitHub/codeload/raw 下载地址；留空关闭')),
            (_tr('额外跳过目录'), skip_dirs, _tr('多个目录名用 ; 分隔，例如 build;output')),
        )
        for index, (label, variable, note) in enumerate(rows, 1):
            ttk.Label(frame, text=label, style='Body.TLabel').grid(
                row=index, column=0, sticky='w', padx=(0, 12), pady=7)
            ttk.Entry(frame, textvariable=variable).grid(row=index, column=1, sticky='ew', pady=7)
            ttk.Label(frame, text=note, style='Muted.TLabel').grid(
                row=index, column=2, sticky='w', padx=(10, 0), pady=7)
        ttk.Label(frame, text='UV4.exe', style='Body.TLabel').grid(
            row=5, column=0, sticky='w', padx=(0, 12), pady=7)
        ttk.Entry(frame, textvariable=uv4).grid(row=5, column=1, sticky='ew', pady=7)

        def pick_uv4():
            value = filedialog.askopenfilename(parent=win, title=_tr('选择 UV4.exe'),
                                                filetypes=[('Keil µVision', 'UV4.exe'),
                                                           (_tr('可执行文件'), '*.exe')])
            if value:
                uv4.set(value)
        ttk.Button(frame, text=_tr('浏览'), command=pick_uv4).grid(row=5, column=2, sticky='w', padx=(10, 0))
        ttk.Label(frame, text=_tr('设置保存在当前 Windows 用户配置目录，不会写入工程或 Git 仓库。'),
                  style='Muted.TLabel').grid(row=6, column=0, columnspan=3, sticky='w', pady=(15, 0))
        bar = ttk.Frame(frame, style='Surface.TFrame')
        bar.grid(row=7, column=0, columnspan=3, sticky='ew', pady=(24, 0))

        def save_and_close():
            try:
                retry_value = int(retries.get())
                if not 1 <= retry_value <= 10:
                    raise ValueError()
            except ValueError:
                messagebox.showerror(_tr('设置无效'), _tr('下载重试次数必须是 1–10 的整数。'), parent=win)
                return
            settings = dict(self.user_settings)
            settings.update({
                'download_retries': retry_value, 'proxy': proxy.get().strip(),
                'mirror_prefix': mirror.get().strip(),
                'extra_scan_skip_dirs': skip_dirs.get().strip(), 'uv4_path': uv4.get().strip(),
            })
            try:
                path = save_user_settings(settings)
            except OSError as e:
                messagebox.showerror(_tr('保存设置失败'), str(e), parent=win)
                return
            self.user_settings = settings
            apply_user_settings(settings)
            self.status_var.set(_tr('设置已保存到 %s') % path)
            win.destroy()
        ttk.Button(bar, text=_tr('取消'), command=win.destroy).pack(side='right')
        ttk.Button(bar, text=_tr('保存设置'), style='Primary.TButton',
                   command=save_and_close).pack(side='right', padx=(0, 8))
        win.grab_set()

    def _preset_data(self):
        tree_names = ('add_tree', 'freertos_tree', 'rtthread_tree', 'lvgl_tree', 'fatfs_tree', 'rtt_tree',
                      'littlefs_tree', 'cmsis_dsp_tree', 'lwip_tree', 'tinyusb_tree')
        selections = dict(self._pending_preset_selections)
        for name in tree_names:
            tree = getattr(self, name, None)
            if tree is not None and tree.file_count():
                selections[name] = tree.selection_keys()
        data = {
            'version': 2,
            'enabled': {
                name: getattr(self, name + '_enabled').get()
                for name in ('add', 'freertos', 'rtthread', 'lvgl', 'fatfs', 'settings', 'rtt',
                             'littlefs', 'cmsis_dsp', 'rtos_guard', 'lwip', 'tinyusb')
            },
            'values': {
                'target': self.target_var.get(),
                'sdk': self.sdk_var.get(), 'scan': self.scan_var.get(),
                'freertos_dir': self.freertos_dir.get(), 'heap': self.heap_var.get(),
                'rtthread_dir': self.rtthread_dir.get(),
                'port': self.port_var.get(), 'lvgl_dir': self.lvgl_dir.get(),
                'color': self.color_var.get(), 'fatfs_dir': self.fatfs_dir.get(),
                'fatfs_mode': self.fatfs_mode.get(), 'rtt_dir': self.rtt_dir.get(),
                'littlefs_dir': self.littlefs_dir.get(), 'littlefs_mode': self.littlefs_mode.get(),
                'cmsis_dsp_dir': self.cmsis_dsp_dir.get(), 'lwip_dir': self.lwip_dir.get(),
                'lwip_mode': self.lwip_mode.get(), 'lwip_driver': self.lwip_driver.get(),
                'tinyusb_dir': self.tinyusb_dir.get(), 'tinyusb_mode': self.tinyusb_mode.get(),
                'define': self.define_var.get(), 'remove_define': self.remove_define_var.get(),
                'optimization': self.optimization_var.get(), 'debug_info': self.debug_info_var.get(),
                'scatter': self.scatter_var.get(), 'stack_size': self.stack_size_var.get(),
                'heap_size': self.heap_size_var.get(),
            },
            'flags': {
                'include_h': self.include_h.get(), 'freertos_app': self.freertos_app.get(),
                'lv_ports': self.lv_ports.get(), 'fatfs_app': self.fatfs_app.get(),
                'littlefs_port': self.littlefs_port.get(), 'dsp_float16': self.dsp_float16.get(),
                'lwip_ipv6': self.lwip_ipv6.get(),
                'clear_scatter': self.clear_scatter_var.get(),
                'clean_includes': self.clean_includes_var.get(),
                'remove_missing_includes': self.remove_missing_includes_var.get(),
                'build_after': self.build_after_var.get(), 'rebuild': self.rebuild_var.get(),
            },
            'guards': {name: var.get() for name, var in self.guard_vars.items()},
            'tinyusb_classes': {name: var.get() for name, var in self.tinyusb_class_vars.items()},
            'selections': selections,
        }
        for key in ('target', 'fatfs_mode', 'littlefs_mode', 'lwip_mode', 'lwip_driver',
                    'optimization', 'debug_info'):
            data['values'][key] = _ui_canonical(data['values'][key])
        return data

    def save_preset(self):
        path = filedialog.asksaveasfilename(parent=self.root, title=_tr('保存移植预设'),
                                            defaultextension='.json',
                                            filetypes=[(_tr('JSON 预设'), '*.json')])
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self._preset_data(), ensure_ascii=False, indent=2) + '\n',
                                  encoding='utf-8')
        except OSError as e:
            messagebox.showerror(_tr('保存预设失败'), str(e), parent=self.root)
            return
        self.status_var.set(_tr('预设已保存: %s') % path)

    def load_preset(self):
        path = filedialog.askopenfilename(parent=self.root, title=_tr('加载移植预设'),
                                          filetypes=[(_tr('JSON 预设'), '*.json')])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            # Validate the entire document before touching any live Tk variable.
            if not isinstance(data, dict) or data.get('version', 1) not in (1, 2):
                raise ValueError(_tr('预设版本不支持或根节点不是 JSON 对象'))
            for section in ('enabled', 'values', 'flags', 'guards', 'tinyusb_classes', 'selections'):
                entries = data.get(section, {})
                if not isinstance(entries, dict):
                    raise ValueError(_tr('预设 %s 必须是 JSON 对象') % section)
                for key, value in entries.items():
                    valid = (isinstance(value, str) if section == 'values' else
                             isinstance(value, list) and all(isinstance(x, str) for x in value)
                             if section == 'selections' else isinstance(value, bool))
                    if not valid:
                        raise ValueError(_tr('预设字段类型无效: %s.%s') % (section, key))
            chosen = data.get('values', {}).get('target')
            if chosen:
                chosen = _tr(_ui_canonical(chosen))
            if chosen and chosen != _tr('全部 Target'):
                available = tuple(self.target_combo.cget('values'))
                if chosen not in available:
                    raise ValueError(_tr('当前工程没有 Target: %s；请选择对应工程后再加载预设。') % chosen)
            enabled = data.get('enabled', {})
            for name, value in enabled.items():
                variable = getattr(self, name + '_enabled', None)
                if variable is not None:
                    variable.set(bool(value))
            value_vars = {
                'target': self.target_var,
                'rtthread_dir': self.rtthread_dir,
                'sdk': self.sdk_var, 'scan': self.scan_var, 'freertos_dir': self.freertos_dir,
                'heap': self.heap_var, 'port': self.port_var, 'lvgl_dir': self.lvgl_dir,
                'color': self.color_var, 'fatfs_dir': self.fatfs_dir, 'fatfs_mode': self.fatfs_mode,
                'rtt_dir': self.rtt_dir, 'littlefs_dir': self.littlefs_dir,
                'littlefs_mode': self.littlefs_mode, 'cmsis_dsp_dir': self.cmsis_dsp_dir,
                'lwip_dir': self.lwip_dir, 'lwip_mode': self.lwip_mode,
                'lwip_driver': self.lwip_driver, 'tinyusb_dir': self.tinyusb_dir,
                'tinyusb_mode': self.tinyusb_mode, 'define': self.define_var,
                'remove_define': self.remove_define_var, 'optimization': self.optimization_var,
                'debug_info': self.debug_info_var,
                'scatter': self.scatter_var, 'stack_size': self.stack_size_var,
                'heap_size': self.heap_size_var,
            }
            old_sources = {key: var.get() for key, var in value_vars.items()
                           if key.endswith('_dir') or key == 'scan'}
            for name, value in data.get('values', {}).items():
                if name in value_vars:
                    if name in ('target', 'fatfs_mode', 'littlefs_mode', 'lwip_mode',
                                'lwip_driver', 'optimization', 'debug_info'):
                        value = _tr(_ui_canonical(value))
                    value_vars[name].set(value)
            flag_vars = {
                'include_h': self.include_h, 'freertos_app': self.freertos_app,
                'lv_ports': self.lv_ports, 'fatfs_app': self.fatfs_app,
                'littlefs_port': self.littlefs_port, 'dsp_float16': self.dsp_float16,
                'lwip_ipv6': self.lwip_ipv6,
                'clear_scatter': self.clear_scatter_var,
                'clean_includes': self.clean_includes_var,
                'remove_missing_includes': self.remove_missing_includes_var,
                'build_after': self.build_after_var, 'rebuild': self.rebuild_var,
            }
            for name, value in data.get('flags', {}).items():
                if name in flag_vars:
                    flag_vars[name].set(bool(value))
            for name, value in data.get('guards', {}).items():
                if name in self.guard_vars:
                    self.guard_vars[name].set(bool(value))
            for name, value in data.get('tinyusb_classes', {}).items():
                if name in self.tinyusb_class_vars:
                    self.tinyusb_class_vars[name].set(bool(value))
            self._pending_preset_selections = data.get('selections', {})
            # Never reuse stale references from a different preset's source tree.
            for key, old_value in old_sources.items():
                if value_vars[key].get() != old_value:
                    name = 'add_tree' if key == 'scan' else key[:-4] + '_tree'
                    tree = getattr(self, name, None)
                    if tree is not None:
                        tree.clear()
            for name, keys in self._pending_preset_selections.items():
                tree = getattr(self, name, None)
                if tree is not None and tree.file_count():
                    tree.apply_selection_keys(keys)
            self.status_var.set(_tr('预设已加载；组件扫描后会自动恢复文件勾选。'))
        except Exception as e:
            messagebox.showerror(_tr('加载预设失败'), str(e), parent=self.root)

    def open_presets(self):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label=_tr('保存当前预设…'), command=self.save_preset)
        menu.add_command(label=_tr('加载预设…'), command=self.load_preset)
        try:
            x = self.root.winfo_pointerx()
            y = self.root.winfo_pointery()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _restore_tree_preset(self, name, tree):
        keys = self._pending_preset_selections.get(name)
        if keys is not None:
            tree.apply_selection_keys(keys)

    def open_project_tools(self):
        try:
            proj = self._project()
        except Exception as e:
            messagebox.showerror(_tr('无法打开'), str(e), parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title(_tr('工程工具'))
        self._size_dialog(win, 560, 430)
        win.transient(self.root)
        frame = ttk.Frame(win, style='Surface.TFrame', padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=_tr('工程化辅助'), style='TaskTitle.TLabel').pack(anchor='w')
        ttk.Label(frame, text=_tr('导出不会修改工程；Git 规则只追加并自动备份原文件。'),
                  style='Muted.TLabel').pack(anchor='w', pady=(3, 16))

        def export_manifest_ui():
            path = filedialog.asksaveasfilename(parent=win, title=_tr('导出工程清单'),
                                                defaultextension='.md',
                                                filetypes=[('Markdown', '*.md'), ('JSON', '*.json'),
                                                           ('CSV', '*.csv')])
            if path:
                try:
                    export_project_manifest(self._project(), path)
                    messagebox.showinfo(_tr('导出完成'), _tr('工程清单已导出。'), parent=win)
                except Exception as e:
                    messagebox.showerror(_tr('导出失败'), str(e), parent=win)

        def export_license_ui():
            path = filedialog.asksaveasfilename(parent=win, title=_tr('导出许可证清单'),
                                                defaultextension='.md',
                                                filetypes=[('Markdown', '*.md')])
            if path:
                try:
                    export_license_report(self._project(), path)
                    messagebox.showinfo(_tr('导出完成'), _tr('许可证清单已导出。'), parent=win)
                except Exception as e:
                    messagebox.showerror(_tr('导出失败'), str(e), parent=win)

        def gitignore_ui(third_party=False):
            text = (_tr('将忽略工具状态/备份文件') +
                    (_tr('，并忽略整个 Middlewares/Third_Party。') if third_party else _tr('。第三方源码仍提交 Git。')))
            if messagebox.askyesno(_tr('更新 .gitignore'), text, parent=win):
                try:
                    update_project_gitignore(self._project(), third_party)
                    messagebox.showinfo(_tr('完成'), _tr('.gitignore 已更新。'), parent=win)
                except Exception as e:
                    messagebox.showerror(_tr('更新失败'), str(e), parent=win)

        def build_ui():
            try:
                build_proj = self._project()
                self._background_call(build_keil_targets, build_proj, targets=build_proj.target_names(),
                                   uv4_path=self.user_settings.get('uv4_path'),
                                   rebuild=self.rebuild_var.get())
                messagebox.showinfo(_tr('Keil 编译完成'), _tr('编译完成，详细结果请查看运行日志。'), parent=win)
            except Exception as e:
                messagebox.showerror(_tr('Keil 编译失败'), str(e), parent=win)

        ttk.Button(frame, text=_tr('导出工程清单（MD / JSON / CSV）'),
                   command=export_manifest_ui).pack(fill='x', pady=4)
        ttk.Button(frame, text=_tr('导出第三方许可证清单'), command=export_license_ui).pack(fill='x', pady=4)
        ttk.Button(frame, text=_tr('更新 .gitignore（提交第三方源码）'),
                   command=lambda: gitignore_ui(False)).pack(fill='x', pady=4)
        ttk.Button(frame, text=_tr('更新 .gitignore（忽略第三方源码）'),
                   command=lambda: gitignore_ui(True)).pack(fill='x', pady=4)
        ttk.Separator(frame).pack(fill='x', pady=12)
        ttk.Checkbutton(frame, text=_tr('重新构建（Rebuild）'), variable=self.rebuild_var,
                        style='Modern.TCheckbutton').pack(anchor='w')
        ttk.Button(frame, text=_tr('立即调用 Keil 编译'), style='Primary.TButton',
                   command=build_ui).pack(fill='x', pady=(8, 4))
        ttk.Checkbutton(frame, text=_tr('移植成功后自动调用 Keil 编译'), variable=self.build_after_var,
                        style='Modern.TCheckbutton').pack(anchor='w', pady=(8, 0))
        ttk.Button(frame, text=_tr('关闭'), command=win.destroy).pack(anchor='e', pady=(14, 0))
        win.grab_set()

    def browse_project(self):
        p = filedialog.askopenfilename(title=_tr('选择 Keil 工程'),
                                       filetypes=[(_tr('Keil 工程'), '*.uvprojx *.uvproj'), (_tr('全部文件'), '*.*')])
        if p:
            self.project_var.set(p)
            self._project_changed()

    def browse_dir(self, variable):
        p = filedialog.askdirectory(title=_tr('选择目录'))
        if p:
            variable.set(p)

    def browse_scatter(self):
        path = filedialog.askopenfilename(
            title=_tr('选择 Keil 分散加载文件'),
            filetypes=[('Keil Scatter File', '*.sct'), (_tr('全部文件'), '*.*')])
        if path:
            self.scatter_var.set(path)
            self.clear_scatter_var.set(False)

    def add_scan_dir(self):
        p = filedialog.askdirectory(title=_tr('选择要扫描的源码目录'))
        if p:
            old = self.scan_var.get().strip()
            parts = [x for x in old.split(';') if x] if old else []
            if p not in parts:
                parts.append(p)
            self.scan_var.set(';'.join(parts))
            self.scan_new_files()

    def _project(self):
        p = Path(self.project_var.get().strip().strip('"')).expanduser()
        if not p.is_file() or p.suffix.lower() not in ('.uvprojx', '.uvproj'):
            raise ToolError(_tr('请选择有效的 .uvprojx 或 .uvproj 工程文件'))
        project = KeilProject(p)
        chosen = self.target_var.get().strip()
        if chosen and chosen != _tr('全部 Target'):
            project.select_targets([chosen])
        return project

    def _project_changed(self):
        try:
            p = Path(self.project_var.get().strip().strip('"')).expanduser()
            proj = KeilProject(p)
            target_names = [name for name in proj.target_names(True) if name]
            self.target_combo.configure(values=[_tr('全部 Target')] + target_names)
            if self.target_var.get() not in [_tr('全部 Target')] + target_names:
                self.target_var.set(_tr('全部 Target'))
            if self.target_var.get() != _tr('全部 Target'):
                proj.select_targets([self.target_var.get()])
            if not self.scan_var.get():
                self.scan_var.set(str(proj.dir))
            if not self.sdk_var.get():
                self.sdk_var.set(str(default_sdk_dir(proj)))
            self.status_var.set(_tr('工程：%s；芯片：%s') % (proj.path.name, proj.device() or _tr('未识别')))
        except Exception as e:
            self.status_var.set(str(e))

    def scan_new_files(self):
        try:
            proj = self._project()
            specs = self.scan_var.get().strip() or str(proj.dir)
            dirs = [Path(x.strip().strip('"')).expanduser().resolve()
                    for x in specs.split(';') if x.strip()]
            dirs, narrowed_dirs = scope_scan_dirs(proj, dirs)
            existing = {proj.norm_file(x) for x in proj.files_in_project()}
            items = []
            foreign_roots, output_roots, managed_roots = scan_exclusion_roots(proj, dirs)
            skipped_foreign = 0
            skipped_output = 0
            for d in dirs:
                if not d.is_dir():
                    continue
                for f in iter_scan_files(d, foreign_roots, output_roots, managed_roots):
                    rel = f.relative_to(d)
                    if any(x.startswith('.') or x.lower() in SCAN_SKIP_DIRS for x in rel.parts[:-1]):
                        continue
                    reason = scan_skip_reason(f, foreign_roots, output_roots, managed_roots)
                    if reason == 'foreign_project':
                        skipped_foreign += 1
                        continue
                    if reason == 'build_output':
                        skipped_output += 1
                        continue
                    suffix = f.suffix.lower()
                    if suffix in COMPILED_SUFFIXES and proj.norm_file(f) in existing:
                        continue
                    if suffix in HEADER_SUFFIXES:
                        note = _tr('加入所在目录到 Include Path')
                    elif PROJECT_FILE_TYPES[suffix] == 4:
                        note = _tr('加入链接：静态库')
                    elif PROJECT_FILE_TYPES[suffix] == 3:
                        note = _tr('加入链接：目标文件')
                    elif PROJECT_FILE_TYPES[suffix] == 2:
                        note = _tr('加入工程并汇编')
                    elif PROJECT_FILE_TYPES[suffix] == 8:
                        note = _tr('加入工程并以 C++ 编译')
                    else:
                        note = _tr('加入工程并编译')
                    items.append((f, note))
            try:
                common = Path(os.path.commonpath([str(d) for d in dirs])) if dirs else proj.dir
            except ValueError:  # Windows 跨盘扫描没有共同路径
                common = proj.dir
            self.add_tree.set_files(common, items)
            self._restore_tree_preset('add_tree', self.add_tree)
            detail = []
            if narrowed_dirs:
                detail.append(_tr('已自动收窄到当前工程，避免扫入兄弟工程'))
            elif foreign_roots:
                detail.append(_tr('已隔离其他工程目录 %d 个') % len(foreign_roots))
            if output_roots:
                detail.append(_tr('已排除 Target 输出目录 %d 个') % len(output_roots))
            suffix = ('；' + '，'.join(detail)) if detail else ''
            self.status_var.set(_tr('发现 %d 个可处理的工程文件，默认已全选%s。') %
                                (len(items), suffix))
        except Exception as e:
            messagebox.showerror(_tr('扫描失败'), str(e), parent=self.root)

    def _sdk_opts(self):
        return SimpleNamespace(interactive=False, yes=True, dry_run=False,
                               sdk_dir=self.sdk_var.get().strip() or None,
                               no_download=False, port_rel=self.port_var.get().strip() or None)

    def prepare_freertos(self):
        try:
            self.status_var.set(_tr('正在准备 FreeRTOS 源码，请稍候…'))
            self.root.update_idletasks()
            proj = self._project()
            specified = self.freertos_dir.get().strip()
            if specified:
                base = locate_freertos_source(specified)
                if base is None:
                    raise ToolError(_tr('所选目录中未找到 include/FreeRTOS.h'))
            else:
                rep = Report('freertos')
                top = self._background_call(ensure_freertos_sdk, proj, self._sdk_opts(), rep)
                base = locate_freertos_source(top) if top else None
                if base is None:
                    raise ToolError(_tr('FreeRTOS 下载/定位失败'))
                self.freertos_dir.set(str(base))
            try:
                self._background_call(_ensure_cmsis_os2_wrapper, base, self._sdk_opts())
            except ToolError as e:
                warn(str(e))

            ports = available_rvds_ports(base)
            self.port_combo.configure(values=ports)
            if self.port_var.get().strip() not in ports:
                self.port_var.set('')
            opts = self._sdk_opts()
            try:
                port_rel = detect_port(proj, base, opts)
                self.port_var.set(port_rel)
            except ToolError:
                if not self.port_var.get() and len(ports) == 1:
                    self.port_var.set(ports[0])
                elif not self.port_var.get():
                    raise ToolError(_tr('无法自动识别内核，请从“RVDS 移植层”下拉框选择一项'))
            port_rel = self.port_var.get().strip()
            files = []
            notes = {
                'tasks.c': _tr('任务、调度器、任务通知（核心）'),
                'list.c': _tr('内核链表（核心）'),
                'queue.c': _tr('队列、信号量、互斥量（核心）'),
                'timers.c': _tr('软件定时器（按需）'),
                'event_groups.c': _tr('事件组（按需）'),
                'stream_buffer.c': _tr('流缓冲区、消息缓冲区（按需）'),
                'croutine.c': _tr('协程（按需，较少使用）'),
            }
            for name, note in notes.items():
                f = Path(base) / name
                if f.is_file():
                    files.append((f, note))
            mem_dir = Path(base) / 'portable' / 'MemMang'
            heaps = [p.name for p in sorted(mem_dir.glob('heap_[1-5].c'))]
            if not heaps:
                raise ToolError(_tr('未找到 portable/MemMang/heap_[1-5].c'))
            if self.heap_var.get() not in heaps:
                self.heap_var.set('heap_4.c' if 'heap_4.c' in heaps else heaps[0])
            heap = mem_dir / self.heap_var.get()
            if not heap.is_file():
                raise ToolError(_tr('未找到所选内存管理文件：%s') % heap)
            files.append((heap, _tr('动态内存管理方案（请选择且仅使用一个）')))
            port_c = Path(base) / 'portable' / 'RVDS' / port_rel / 'port.c'
            if not port_c.is_file():
                raise ToolError(_tr('未找到移植层 port.c：%s') % port_c)
            files.append((port_c, _tr('CPU/编译器移植层（核心）')))
            os2_c = Path(base) / 'CMSIS_RTOS_V2' / 'cmsis_os2.c'
            if os2_c.is_file():
                files.append((os2_c, _tr('CMSIS-RTOS V2 API 封装')))
                os_tick_c = find_cmsis_os_tick_source(proj)
                if os_tick_c:
                    files.append((os_tick_c, _tr('CMSIS-RTOS2 系统节拍实现（必需）')))
                else:
                    warn(_tr('未找到 CMSIS RTOS2 Source/os_systick.c'))
            else:
                warn(_tr('未找到 cmsis_os2.c；当前只能移植原生 FreeRTOS'))
            self.freertos_tree.set_files(base, files)
            self._restore_tree_preset('freertos_tree', self.freertos_tree)
            self.freertos_root = Path(base).resolve()
            self.status_var.set(_tr('FreeRTOS 共 %d 个源文件，默认已全选；可取消按需组件。') % len(files))
        except Exception as e:
            messagebox.showerror(_tr('FreeRTOS 准备失败'), str(e), parent=self.root)
            self.status_var.set(_tr('FreeRTOS 准备失败。'))

    def prepare_lvgl(self):
        try:
            self.status_var.set(_tr('正在准备 LVGL 源码，请稍候…'))
            self.root.update_idletasks()
            proj = self._project()
            specified = self.lvgl_dir.get().strip()
            if specified:
                root = locate_lvgl_root(specified)
                if root is None:
                    raise ToolError(_tr('所选目录中未找到 lvgl.h 与 src 目录'))
            else:
                rep = Report('lvgl')
                top = self._background_call(ensure_lvgl_sdk, proj, self._sdk_opts(), rep)
                root = locate_lvgl_root(top) if top else None
                if root is None:
                    raise ToolError(_tr('LVGL 下载/定位失败'))
                self.lvgl_dir.set(str(root))
            files = []
            src = Path(root) / 'src'
            for f in sorted(src.rglob('*.c')):
                rel = f.relative_to(src)
                section = rel.parts[0] if len(rel.parts) > 1 else 'core'
                files.append((f, _tr('LVGL %s 模块') % section))
            self.lvgl_tree.set_files(src, files)
            self._restore_tree_preset('lvgl_tree', self.lvgl_tree)
            self.lvgl_root = Path(root).resolve()
            self.status_var.set(_tr('LVGL 共 %d 个源文件，默认已全选；可按目录或单文件取消。') % len(files))
        except Exception as e:
            messagebox.showerror(_tr('LVGL 准备失败'), str(e), parent=self.root)
            self.status_var.set(_tr('LVGL 准备失败。'))

    def _fatfs_mode_value(self):
        return {_tr('自动判断'): 'auto', _tr('RTOS（工程内核）'): 'rtos', 'FreeRTOS / CMSIS-V2': 'rtos',
                _tr('裸机'): 'baremetal'}.get(self.fatfs_mode.get(), 'auto')

    def _fatfs_preview_uses_rtos(self, proj):
        mode = self._fatfs_mode_value()
        if mode == 'rtos':
            return True
        if mode == 'baremetal':
            return False
        return bool(self.freertos_enabled.get() or self.rtthread_enabled.get() or
                    project_uses_freertos(proj) or project_uses_rtthread(proj))

    def prepare_fatfs(self):
        try:
            self.status_var.set(_tr('正在准备 FatFS 源码，请稍候…'))
            self.root.update_idletasks()
            proj = self._project()
            specified = self.fatfs_dir.get().strip()
            if specified:
                root = locate_fatfs_root(specified)
                if root is None:
                    raise ToolError(_tr('所选目录中未找到 ff.c 与 ff.h'))
            else:
                rep = Report('fatfs')
                top = self._background_call(ensure_fatfs_sdk, proj, self._sdk_opts(), rep)
                root = locate_fatfs_root(top) if top else None
                if root is None:
                    raise ToolError(_tr('FatFS 下载/定位失败'))
                self.fatfs_dir.set(str(root))
            root = Path(root).resolve()
            use_rtos = self._fatfs_preview_uses_rtos(proj)
            use_rtthread = use_rtos and (self.rtthread_enabled.get() or project_uses_rtthread(proj))
            system_file = None if use_rtthread else find_fatfs_system_file(root, use_rtos)
            if use_rtos and not use_rtthread and system_file is None:
                raise ToolError(_tr('当前 FatFS 源码没有可用的 FreeRTOS 系统层'))
            system_name = system_file.name if system_file else None
            notes = {
                'ff.c': _tr('FatFS 文件系统核心（必需）'),
                'diskio.c': _tr('ST 逻辑磁盘分发层（必需）'),
                'ff_gen_drv.c': _tr('ST 磁盘驱动注册层（必需）'),
                'ffunicode.c': _tr('长文件名字符集转换（启用 LFN 时必需）'),
            }
            if system_name:
                notes[system_name] = (_tr('CMSIS-RTOS2 互斥锁/内存适配（FreeRTOS 必需）')
                                      if use_rtos else _tr('裸机内存与系统适配（按配置）'))
            files = []
            for name, note in notes.items():
                path = root / name
                if path.is_file():
                    files.append((path, note))
                elif name != 'ffunicode.c':
                    raise ToolError(_tr('当前 FatFS 源码缺少 %s') % name)
            self.fatfs_tree.set_files(root, files)
            self._restore_tree_preset('fatfs_tree', self.fatfs_tree)
            self.fatfs_root = root
            self.fatfs_preview_rtos = use_rtos
            mode_text = (_tr('RT-Thread（自动生成 ffsystem_rtthread.c）') if use_rtthread else
                         _tr('FreeRTOS / CMSIS-V2 线程安全模式') if use_rtos else _tr('裸机模式'))
            self.status_var.set(_tr('FatFS 已按%s列出 %d 个组件，默认已全选。') %
                                (mode_text, len(files)))
        except Exception as e:
            messagebox.showerror(_tr('FatFS 准备失败'), str(e), parent=self.root)
            self.status_var.set(_tr('FatFS 准备失败。'))

    def prepare_rtt(self):
        try:
            self.status_var.set(_tr('正在准备 SEGGER RTT 源码…'))
            self.root.update_idletasks()
            proj = self._project()
            specified = self.rtt_dir.get().strip()
            if specified:
                root = locate_segger_rtt_root(specified)
                if root is None:
                    raise ToolError(_tr('所选目录中未找到 RTT/SEGGER_RTT.c'))
            else:
                rep = Report('segger_rtt')
                top = self._background_call(ensure_segger_rtt_sdk, proj, self._sdk_opts(), rep)
                root = locate_segger_rtt_root(top) if top else None
                if root is None:
                    raise ToolError(_tr('SEGGER RTT 下载/定位失败'))
                self.rtt_dir.set(str(root))
            root = Path(root).resolve()
            notes = {
                'SEGGER_RTT.c': _tr('RTT 环形缓冲区核心（必需）'),
                'SEGGER_RTT_printf.c': _tr('轻量格式化输出 SEGGER_RTT_printf（按需）'),
                'SEGGER_RTT_ASM_ARMv7M.S': _tr('ARMv7M/ARMv8M 汇编加速（按需）'),
                'SEGGER_RTT_Syscalls_KEIL.c': _tr('Keil printf/fputc 重定向（按需，注意冲突）'),
            }
            candidates = (root / 'RTT' / 'SEGGER_RTT.c',
                          root / 'RTT' / 'SEGGER_RTT_printf.c',
                          root / 'RTT' / 'SEGGER_RTT_ASM_ARMv7M.S',
                          root / 'Syscalls' / 'SEGGER_RTT_Syscalls_KEIL.c')
            selected_cores = {proj.target_core_info(target)[0] for target in proj.targets}
            files = []
            for path in candidates:
                if (path.name == 'SEGGER_RTT_ASM_ARMv7M.S' and
                        (None in selected_cores or selected_cores & {'Cortex-M0', 'Cortex-M0+'})):
                    continue
                if path.is_file():
                    files.append((path, notes[path.name]))
            self.rtt_tree.set_files(root, files)
            self._restore_tree_preset('rtt_tree', self.rtt_tree)
            self.rtt_root = root
            self.status_var.set(_tr('SEGGER RTT 共 %d 个可选编译单元，默认已全选。') % len(files))
        except Exception as e:
            messagebox.showerror(_tr('SEGGER RTT 准备失败'), str(e), parent=self.root)
            self.status_var.set(_tr('SEGGER RTT 准备失败。'))

    def _littlefs_mode_value(self):
        return {_tr('自动判断'): 'auto', _tr('RTOS（工程内核）'): 'rtos', 'FreeRTOS': 'rtos', _tr('裸机'): 'baremetal'}.get(
            self.littlefs_mode.get(), 'auto')

    def _littlefs_preview_uses_rtos(self, proj):
        mode = self._littlefs_mode_value()
        if mode == 'rtos':
            return True
        if mode == 'baremetal':
            return False
        return bool(self.freertos_enabled.get() or self.rtthread_enabled.get() or
                    project_uses_freertos(proj) or project_uses_rtthread(proj))

    def prepare_littlefs(self):
        try:
            self.status_var.set(_tr('正在准备 LittleFS 源码…'))
            self.root.update_idletasks()
            proj = self._project()
            specified = self.littlefs_dir.get().strip()
            if specified:
                root = locate_littlefs_root(specified)
                if root is None:
                    raise ToolError(_tr('所选目录中未找到 lfs.c/lfs.h'))
            else:
                rep = Report('littlefs')
                top = self._background_call(ensure_littlefs_sdk, proj, self._sdk_opts(), rep)
                root = locate_littlefs_root(top) if top else None
                if root is None:
                    raise ToolError(_tr('LittleFS 下载/定位失败'))
                self.littlefs_dir.set(str(root))
            root = Path(root).resolve()
            files = []
            for name, note in (('lfs.c', _tr('LittleFS 文件系统核心（必需）')),
                               ('lfs_util.c', _tr('CRC、内存与日志工具（必需）'))):
                path = root / name
                if not path.is_file():
                    raise ToolError(_tr('LittleFS 缺少 %s') % name)
                files.append((path, note))
            self.littlefs_tree.set_files(root, files)
            self._restore_tree_preset('littlefs_tree', self.littlefs_tree)
            self.littlefs_root = root
            self.littlefs_preview_rtos = self._littlefs_preview_uses_rtos(proj)
            mode = _tr('FreeRTOS 线程安全') if self.littlefs_preview_rtos else _tr('裸机')
            self.status_var.set(_tr('LittleFS 已按%s列出核心文件，默认已全选。') % mode)
        except Exception as e:
            messagebox.showerror(_tr('LittleFS 准备失败'), str(e), parent=self.root)
            self.status_var.set(_tr('LittleFS 准备失败。'))

    def prepare_cmsis_dsp(self):
        try:
            self.status_var.set(_tr('正在准备 CMSIS-DSP 源码…'))
            self.root.update_idletasks()
            proj = self._project()
            specified = self.cmsis_dsp_dir.get().strip()
            if specified:
                root = locate_cmsis_dsp_root(specified)
                if root is None:
                    raise ToolError(_tr('所选目录中未找到 Source/Include/arm_math.h'))
            else:
                rep = Report('cmsis_dsp')
                top = self._background_call(ensure_cmsis_dsp_sdk, proj, self._sdk_opts(), rep)
                root = locate_cmsis_dsp_root(top) if top else None
                if root is None:
                    raise ToolError(_tr('CMSIS-DSP 下载/定位失败'))
                self.cmsis_dsp_dir.set(str(root))
            root = Path(root).resolve()
            files = []
            for path in cmsis_dsp_source_files(root):
                if path.stem.lower().endswith('f16') and not proj.any_ac6():
                    continue
                note = _tr('%s 算法聚合编译单元') % path.parent.name
                if path.stem.lower().endswith('f16'):
                    note += _tr('（Float16，可取消）')
                files.append((path, note))
            if not files:
                raise ToolError(_tr('没有找到 CMSIS-DSP 聚合源码'))
            self.cmsis_dsp_tree.set_files(root / 'Source', files)
            self._restore_tree_preset('cmsis_dsp_tree', self.cmsis_dsp_tree)
            self.cmsis_dsp_root = root
            self.status_var.set(_tr('CMSIS-DSP 共 %d 个算法编译单元，默认已全选；可整组取消。') % len(files))
        except Exception as e:
            messagebox.showerror(_tr('CMSIS-DSP 准备失败'), str(e), parent=self.root)
            self.status_var.set(_tr('CMSIS-DSP 准备失败。'))

    def _lwip_mode_value(self):
        return {_tr('自动判断'): 'auto', _tr('RTOS（工程内核）'): 'rtos', 'FreeRTOS': 'rtos', _tr('裸机'): 'baremetal'}.get(
            self.lwip_mode.get(), 'auto')

    def _lwip_preview_uses_rtos(self, proj):
        mode = self._lwip_mode_value()
        if mode == 'rtos':
            return True
        if mode == 'baremetal':
            return False
        return bool(self.freertos_enabled.get() or self.rtthread_enabled.get() or
                    project_uses_freertos(proj) or project_uses_rtthread(proj))

    def prepare_lwip(self):
        try:
            self.status_var.set(_tr('正在准备 LwIP 源码…'))
            self.root.update_idletasks()
            proj = self._project()
            specified = self.lwip_dir.get().strip()
            if specified:
                root = locate_lwip_root(specified)
                if root is None:
                    raise ToolError(_tr('所选目录中未找到 LwIP src/include/lwip/init.h'))
            else:
                rep = Report('lwip')
                top = self._background_call(ensure_lwip_sdk, proj, self._sdk_opts(), rep)
                root = locate_lwip_root(top) if top else None
                if root is None:
                    raise ToolError(_tr('LwIP 下载/定位失败'))
                self.lwip_dir.set(str(root))
            root = Path(root).resolve()
            use_rtos = self._lwip_preview_uses_rtos(proj)
            ipv6 = self.lwip_ipv6.get()
            files = []
            for path in lwip_available_sources(root, use_rtos, ipv6):
                rel = str(path.relative_to(root)).replace('\\', '/')
                if '/apps/' in rel:
                    note = _tr('%s 应用协议（按需）') % path.parent.name.upper()
                elif '/api/' in rel:
                    note = _tr('线程/Socket/Netconn API（FreeRTOS）')
                elif '/ipv6/' in rel:
                    note = _tr('IPv6 协议核心（按需）')
                else:
                    note = _tr('TCP/IP 核心（必需或基础协议）')
                files.append((path, note))
            self.lwip_tree.set_files(root / 'src', files)
            self._restore_tree_preset('lwip_tree', self.lwip_tree)
            self.lwip_root = root
            self.lwip_preview = (use_rtos, ipv6)
            self.status_var.set(_tr('LwIP 共 %d 个源文件，已按%s模式展开，默认全部勾选。') %
                                (len(files), 'FreeRTOS' if use_rtos else _tr('裸机')))
        except Exception as e:
            messagebox.showerror(_tr('LwIP 准备失败'), str(e), parent=self.root)
            self.status_var.set(_tr('LwIP 准备失败。'))

    def _tinyusb_mode_value(self):
        return {'Device': 'device', 'Host': 'host', 'Device + Host': 'both'}.get(
            self.tinyusb_mode.get(), 'device')

    def _tinyusb_classes(self):
        return [name for name, value in self.tinyusb_class_vars.items() if value.get()]

    def prepare_tinyusb(self):
        try:
            self.status_var.set(_tr('正在准备 TinyUSB 源码…'))
            self.root.update_idletasks()
            proj = self._project()
            specified = self.tinyusb_dir.get().strip()
            if specified:
                root = locate_tinyusb_root(specified)
                if root is None:
                    raise ToolError(_tr('所选目录中未找到 TinyUSB src/tusb.c'))
            else:
                rep = Report('tinyusb')
                sdk_opts = self._sdk_opts()
                sdk_opts.tinyusb_mode = self._tinyusb_mode_value()
                top = self._background_call(ensure_tinyusb_sdk, proj, sdk_opts, rep)
                root = locate_tinyusb_root(top) if top else None
                if root is None:
                    raise ToolError(_tr('TinyUSB 下载/定位失败'))
                self.tinyusb_dir.set(str(root))
            root = Path(root).resolve()
            mode, classes = self._tinyusb_mode_value(), self._tinyusb_classes()
            if not classes:
                raise ToolError(_tr('至少选择一个 TinyUSB 设备类'))
            files = []
            for path in tinyusb_source_files(root, proj, mode, classes):
                rel = str(path.relative_to(root)).replace('\\', '/')
                if '/class/' in rel:
                    note = _tr('%s 协议类（按需）') % path.parent.name.upper()
                elif '/portable/' in rel:
                    note = _tr('当前 STM32 USB 控制器驱动（必需）')
                elif '/device/' in rel:
                    note = _tr('USB Device 核心（必需）')
                elif '/host/' in rel:
                    note = _tr('USB Host 核心（必需）')
                else:
                    note = _tr('TinyUSB 公共核心（必需）')
                files.append((path, note))
            if not files:
                raise ToolError(_tr('当前模式没有发现可用 TinyUSB 源码'))
            self.tinyusb_tree.set_files(root / 'src', files)
            self._restore_tree_preset('tinyusb_tree', self.tinyusb_tree)
            self.tinyusb_root = root
            self.tinyusb_preview = (mode, tuple(classes), proj.device())
            self.status_var.set(_tr('TinyUSB 共 %d 个源文件，默认全部勾选。') % len(files))
        except Exception as e:
            messagebox.showerror(_tr('TinyUSB 准备失败'), str(e), parent=self.root)
            self.status_var.set(_tr('TinyUSB 准备失败。'))

    def confirm_diff_preview(self, text, title=_tr('详细差异预览')):
        context = getattr(_OPERATION_LOCAL, 'value', None)
        if context and context.ui_call:
            return context.ui_call(self.confirm_diff_preview, text, title)
        result = {'ok': False}
        win = tk.Toplevel(self.root)
        win.title(title)
        self._size_dialog(win, 1040, 720)
        win.minsize(760, 480)
        win.transient(self.root)
        win.configure(background=self.colors['bg'])
        frame = ttk.Frame(win, style='Surface.TFrame', padding=16)
        frame.pack(fill='both', expand=True, padx=14, pady=14)
        ttk.Label(frame, text=title, style='TaskTitle.TLabel').pack(anchor='w')
        ttk.Label(frame, text=_tr('请检查实际文件差异。只有确认后才会建立事务并写入工程。'),
                  style='Muted.TLabel').pack(anchor='w', pady=(3, 10))
        box = ScrolledText(frame, wrap='none', relief='flat', borderwidth=1,
                           background='#0F172A', foreground='#CBD5E1',
                           insertbackground='#FFFFFF', font=('Cascadia Mono', 9))
        box.pack(fill='both', expand=True)
        box.insert('1.0', text)
        box.configure(state='disabled')
        buttons = ttk.Frame(frame, style='Surface.TFrame')
        buttons.pack(fill='x', pady=(12, 0))
        ttk.Button(buttons, text=_tr('取消'), command=win.destroy).pack(side='right')

        def approve():
            result['ok'] = True
            win.destroy()

        ttk.Button(buttons, text=_tr('确认并继续'), style='Primary.TButton',
                   command=approve).pack(side='right', padx=(0, 8))
        win.protocol('WM_DELETE_WINDOW', win.destroy)
        win.grab_set()
        self.root.wait_window(win)
        return result['ok']

    def open_safety_manager(self):
        try:
            proj = self._project()
            components = installed_components(proj)
        except Exception as e:
            messagebox.showerror(_tr('无法打开'), str(e), parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title(_tr('安全与恢复'))
        self._size_dialog(win, 620, 430)
        win.transient(self.root)
        frame = ttk.Frame(win, style='Surface.TFrame', padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=_tr('组件安装记录'), style='TaskTitle.TLabel').pack(anchor='w')
        ttk.Label(frame, text=_tr('卸载只删除未经修改的工具文件；回滚会恢复最近事务的完整快照。'),
                  style='Muted.TLabel').pack(anchor='w', pady=(3, 10))
        listing = tk.Listbox(frame, activestyle='none', font=('Microsoft YaHei UI', 10),
                             borderwidth=1, relief='solid', selectmode='browse')
        listing.pack(fill='both', expand=True)
        keys = sorted(components)
        for key in keys:
            item = components[key]
            listing.insert('end', '%s    %s    Target: %s' %
                           (Report.TITLES.get(key, key), item.get('installed_at', ''),
                            ', '.join(item.get('targets', []))))
        if keys:
            listing.selection_set(0)
        else:
            listing.insert('end', _tr('当前工程还没有组件安装清单'))
            listing.configure(state='disabled')
        bar = ttk.Frame(frame, style='Surface.TFrame')
        bar.pack(fill='x', pady=(12, 0))

        def uninstall_selected():
            selected = listing.curselection()
            if not selected or not keys:
                return
            key = keys[selected[0]]
            win.destroy()
            try:
                changed = self._background_call(uninstall_component, self._project(), key,
                                              preview_callback=lambda text:
                                              self.confirm_diff_preview(text, _tr('卸载预览')))
                if changed:
                    messagebox.showinfo(_tr('卸载完成'), _tr('组件已卸载；用户修改过的文件已保留。'),
                                        parent=self.root)
            except Exception as e:
                messagebox.showerror(_tr('卸载失败'), str(e), parent=self.root)

        def rollback_latest():
            win.destroy()
            try:
                changed = self._background_call(rollback_last_transaction,
                    self._project(), preview_callback=lambda text:
                    self.confirm_diff_preview(text, _tr('回滚预览')))
                if changed:
                    messagebox.showinfo(_tr('回滚完成'), _tr('最近一次事务已恢复。'), parent=self.root)
            except Exception as e:
                messagebox.showerror(_tr('回滚失败'), str(e), parent=self.root)

        ttk.Button(bar, text=_tr('关闭'), command=win.destroy).pack(side='right')
        ttk.Button(bar, text=_tr('回滚最近事务'), command=rollback_latest).pack(side='left')
        ttk.Button(bar, text=_tr('卸载所选组件'), style='Primary.TButton',
                   command=uninstall_selected).pack(side='left', padx=(8, 0))
        win.grab_set()

    def execute(self):
        if getattr(self, 'git_panel', None) and self.git_panel.busy:
            messagebox.showinfo('Git', _gt('请等待 Git 操作完成后再移植工程。',
                                         'Wait for Git to finish before modifying the project.'), parent=self.root)
            return
        try:
            proj = self._project()
            tasks = []
            if self.freertos_enabled.get() and self.rtthread_enabled.get():
                raise ToolError(_tr('同一工程不能同时选择 FreeRTOS 和 RT-Thread'))
            if self.rtthread_enabled.get():
                current = locate_rtthread_root(self.rtthread_dir.get().strip())
                if not self.rtthread_tree.file_count() or current != self.rtthread_root:
                    self.prepare_rtthread()
                if not self.rtthread_tree.file_count():
                    return
                tasks.append('rtthread')
            if self.add_enabled.get():
                if not self.add_tree.file_count():
                    self.scan_new_files()
                tasks.append('add_files')
            if self.freertos_enabled.get():
                current = locate_freertos_source(self.freertos_dir.get().strip()) if self.freertos_dir.get().strip() else None
                if not self.freertos_tree.file_count() or current is None or self.freertos_root != Path(current).resolve():
                    self.prepare_freertos()
                    current = locate_freertos_source(self.freertos_dir.get().strip())
                if current is None or not self.freertos_tree.file_count():
                    return
                tasks.append('freertos')
            if self.lvgl_enabled.get():
                current_lv = locate_lvgl_root(self.lvgl_dir.get().strip()) if self.lvgl_dir.get().strip() else None
                if not self.lvgl_tree.file_count() or current_lv is None or self.lvgl_root != Path(current_lv).resolve():
                    self.prepare_lvgl()
                    current_lv = locate_lvgl_root(self.lvgl_dir.get().strip())
                if current_lv is None or not self.lvgl_tree.file_count():
                    return
                tasks.append('lvgl')
            if self.fatfs_enabled.get():
                current_fatfs = (locate_fatfs_root(self.fatfs_dir.get().strip())
                                 if self.fatfs_dir.get().strip() else None)
                expected_rtos = self._fatfs_preview_uses_rtos(proj)
                needs_prepare = (not self.fatfs_tree.file_count() or current_fatfs is None or
                                 self.fatfs_root != Path(current_fatfs).resolve() or
                                 self.fatfs_preview_rtos != expected_rtos)
                if needs_prepare:
                    self.prepare_fatfs()
                    current_fatfs = locate_fatfs_root(self.fatfs_dir.get().strip())
                if current_fatfs is None or not self.fatfs_tree.file_count():
                    return
                tasks.append('fatfs')
            if self.rtt_enabled.get():
                current = (locate_segger_rtt_root(self.rtt_dir.get().strip())
                           if self.rtt_dir.get().strip() else None)
                if not self.rtt_tree.file_count() or current is None or self.rtt_root != Path(current).resolve():
                    self.prepare_rtt()
                    current = locate_segger_rtt_root(self.rtt_dir.get().strip())
                if current is None or not self.rtt_tree.file_count():
                    return
                tasks.append('segger_rtt')
            if self.littlefs_enabled.get():
                current = (locate_littlefs_root(self.littlefs_dir.get().strip())
                           if self.littlefs_dir.get().strip() else None)
                expected_rtos = self._littlefs_preview_uses_rtos(proj)
                needs_prepare = (not self.littlefs_tree.file_count() or current is None or
                                 self.littlefs_root != Path(current).resolve() or
                                 self.littlefs_preview_rtos != expected_rtos)
                if needs_prepare:
                    self.prepare_littlefs()
                    current = locate_littlefs_root(self.littlefs_dir.get().strip())
                if current is None or not self.littlefs_tree.file_count():
                    return
                tasks.append('littlefs')
            if self.cmsis_dsp_enabled.get():
                current = (locate_cmsis_dsp_root(self.cmsis_dsp_dir.get().strip())
                           if self.cmsis_dsp_dir.get().strip() else None)
                if (not self.cmsis_dsp_tree.file_count() or current is None or
                        self.cmsis_dsp_root != Path(current).resolve()):
                    self.prepare_cmsis_dsp()
                    current = locate_cmsis_dsp_root(self.cmsis_dsp_dir.get().strip())
                if current is None or not self.cmsis_dsp_tree.file_count():
                    return
                tasks.append('cmsis_dsp')
            if self.rtos_guard_enabled.get():
                if not (self.freertos_enabled.get() or self.rtthread_enabled.get() or
                        project_uses_freertos(proj) or project_uses_rtthread(proj)):
                    messagebox.showerror(
                        _tr('外设锁需要 FreeRTOS'),
                        _tr('请同时启用 FreeRTOS，或选择一个已经接入 FreeRTOS 的工程。'),
                        parent=self.root)
                    return
                tasks.append('rtos_guard')
            if self.lwip_enabled.get():
                current = (locate_lwip_root(self.lwip_dir.get().strip())
                           if self.lwip_dir.get().strip() else None)
                expected = (self._lwip_preview_uses_rtos(proj), self.lwip_ipv6.get())
                if (not self.lwip_tree.file_count() or current is None or
                        self.lwip_root != Path(current).resolve() or self.lwip_preview != expected):
                    self.prepare_lwip()
                    current = locate_lwip_root(self.lwip_dir.get().strip())
                if current is None or not self.lwip_tree.file_count():
                    return
                tasks.append('lwip')
            if self.tinyusb_enabled.get():
                current = (locate_tinyusb_root(self.tinyusb_dir.get().strip())
                           if self.tinyusb_dir.get().strip() else None)
                expected = (self._tinyusb_mode_value(), tuple(self._tinyusb_classes()), proj.device())
                if (not self.tinyusb_tree.file_count() or current is None or
                        self.tinyusb_root != Path(current).resolve() or
                        self.tinyusb_preview != expected):
                    self.prepare_tinyusb()
                    current = locate_tinyusb_root(self.tinyusb_dir.get().strip())
                if current is None or not self.tinyusb_tree.file_count():
                    return
                tasks.append('tinyusb')
            if self.settings_enabled.get():
                tasks.append('project_settings')
            if not tasks:
                messagebox.showwarning(_tr('未选择任务'), _tr('请至少勾选一个任务。'), parent=self.root)
                return

            selected_fr = self.freertos_tree.checked_paths()
            if 'freertos' in tasks:
                required = {'tasks.c', 'list.c', 'queue.c', 'port.c'}
                chosen_names = {p.name for p in selected_fr}
                if not required.issubset(chosen_names) or not any(n.startswith('heap_') for n in chosen_names):
                    if not messagebox.askyesno(_tr('核心文件未全选'),
                        _tr('FreeRTOS 的 tasks.c、list.c、queue.c、port.c 或 heap_x.c 未全部选择，工程很可能无法链接。仍要继续吗？'),
                        parent=self.root):
                        return
                if 'cmsis_os2.c' in chosen_names:
                    cmsis_deps = {'tasks.c', 'list.c', 'queue.c', 'timers.c',
                                  'event_groups.c', 'os_systick.c'}
                    missing = sorted(cmsis_deps - chosen_names)
                    if missing:
                        messagebox.showerror(
                            _tr('CMSIS V2 依赖缺失'),
                            _tr('cmsis_os2.c 会直接使用这些组件。请重新勾选：\n') +
                            '、'.join(missing) +
                            _tr('\n\n如果只想使用原生 FreeRTOS，也可以取消 cmsis_os2.c。'),
                            parent=self.root)
                        return
            selected_fatfs = self.fatfs_tree.checked_paths()
            if 'fatfs' in tasks:
                if (self.fatfs_preview_rtos and not self.freertos_enabled.get()
                        and not self.rtthread_enabled.get() and not project_uses_rtthread(proj)
                        and not project_uses_freertos(proj)):
                    messagebox.showerror(
                        _tr('FatFS 模式不匹配'),
                        _tr('当前选择了 FreeRTOS / CMSIS-V2 模式，但工程中未检测到 FreeRTOS。\n'
                        '请同时启用 FreeRTOS 任务，或把 FatFS 运行模式改为“裸机”。'),
                        parent=self.root)
                    return
                system_file = (None if self.rtthread_enabled.get() or project_uses_rtthread(proj) else
                               find_fatfs_system_file(self.fatfs_root, self.fatfs_preview_rtos))
                required = {'ff.c', 'diskio.c', 'ff_gen_drv.c'}
                if system_file is not None:
                    required.add(system_file.name)
                missing = sorted(required - {p.name for p in selected_fatfs})
                if missing:
                    messagebox.showerror(
                        _tr('FatFS 核心依赖缺失'),
                        _tr('当前模式必须选择这些文件：\n') + '、'.join(missing),
                        parent=self.root)
                    return
            selected_rtt = self.rtt_tree.checked_paths()
            if 'segger_rtt' in tasks and 'SEGGER_RTT.c' not in {p.name for p in selected_rtt}:
                messagebox.showerror(_tr('RTT 核心依赖缺失'), _tr('SEGGER_RTT.c 不能取消。'), parent=self.root)
                return
            selected_littlefs = self.littlefs_tree.checked_paths()
            if 'littlefs' in tasks:
                missing = {'lfs.c', 'lfs_util.c'} - {p.name for p in selected_littlefs}
                if missing:
                    messagebox.showerror(_tr('LittleFS 核心依赖缺失'),
                                         _tr('不能取消：') + '、'.join(sorted(missing)), parent=self.root)
                    return
                if (self.littlefs_preview_rtos and not self.freertos_enabled.get()
                        and not self.rtthread_enabled.get() and not project_uses_rtthread(proj)
                        and not project_uses_freertos(proj)):
                    messagebox.showerror(_tr('LittleFS 模式不匹配'),
                                         _tr('线程安全模式需要 FreeRTOS 或 RT-Thread。'), parent=self.root)
                    return
            selected_dsp = self.cmsis_dsp_tree.checked_paths()
            if 'cmsis_dsp' in tasks and not selected_dsp:
                messagebox.showerror(_tr('未选择 DSP 模块'), _tr('请至少勾选一个算法模块。'), parent=self.root)
                return
            guard_resources = [name for name, var in self.guard_vars.items() if var.get()]
            if 'rtos_guard' in tasks and not guard_resources:
                messagebox.showerror(_tr('未选择外设锁'), _tr('请至少选择一种外设。'), parent=self.root)
                return
            selected_lwip = self.lwip_tree.checked_paths()
            if 'lwip' in tasks:
                required_paths = {p.resolve() for p in lwip_available_sources(
                    self.lwip_root, self.lwip_preview[0], ipv6=False)
                    if '/apps/' not in str(p).replace('\\', '/').lower()}
                missing = sorted(p.name for p in required_paths -
                                 {p.resolve() for p in selected_lwip})
                if missing:
                    messagebox.showerror(_tr('LwIP 核心依赖缺失'),
                                         _tr('不能取消：') + '、'.join(missing), parent=self.root)
                    return
                if (self.lwip_preview[0] and not self.freertos_enabled.get()
                        and not self.rtthread_enabled.get() and not project_uses_rtthread(proj)
                        and not project_uses_freertos(proj)):
                    messagebox.showerror(_tr('LwIP 模式不匹配'), _tr('FreeRTOS 模式需要先接入 FreeRTOS。'),
                                         parent=self.root)
                    return
            selected_tinyusb = self.tinyusb_tree.checked_paths()
            tinyusb_classes = self._tinyusb_classes()
            if 'tinyusb' in tasks and (not selected_tinyusb or not tinyusb_classes):
                messagebox.showerror(_tr('TinyUSB 选择为空'), _tr('请至少选择一个设备类和源码文件。'),
                                     parent=self.root)
                return
            opts = SimpleNamespace(
                interactive=False, yes=True, dry_run=False,
                scan_dirs=self.scan_var.get().strip() or str(proj.dir),
                scan_files=self.add_tree.checked_paths(), include_h=self.include_h.get(),
                freertos=self.freertos_dir.get().strip() or None, no_os2=False,
                rtthread=self.rtthread_dir.get().strip() or None,
                rtthread_files=self.rtthread_tree.checked_paths(),
                freertos_files=selected_fr, heap_file=self.heap_var.get(),
                freertos_app=self.freertos_app.get(),
                port_rel=self.port_var.get().strip() or None,
                lvgl=self.lvgl_dir.get().strip() or None,
                lvgl_files=self.lvgl_tree.checked_paths(),
                color_depth=int(self.color_var.get()), ports=self.lv_ports.get(),
                fatfs=self.fatfs_dir.get().strip() or None,
                fatfs_files=selected_fatfs, fatfs_mode=self._fatfs_mode_value(),
                fatfs_app=self.fatfs_app.get(),
                segger_rtt=self.rtt_dir.get().strip() or None,
                rtt_files=selected_rtt, rtt_no_printf=False,
                rtt_no_syscalls=False, rtt_no_asm=False,
                littlefs=self.littlefs_dir.get().strip() or None,
                littlefs_files=selected_littlefs,
                littlefs_mode=self._littlefs_mode_value(),
                littlefs_port=self.littlefs_port.get(),
                cmsis_dsp=self.cmsis_dsp_dir.get().strip() or None,
                cmsis_dsp_files=selected_dsp, dsp_modules=[],
                dsp_float16=self.dsp_float16.get(),
                guard_resources=guard_resources,
                lwip=self.lwip_dir.get().strip() or None,
                lwip_files=selected_lwip, lwip_mode=self._lwip_mode_value(),
                lwip_ipv6=self.lwip_ipv6.get(), lwip_apps=[],
                lwip_driver={_tr('自动选择'): 'auto', 'STM32 ETH': 'stm32_eth',
                             'ENC28J60': 'enc28j60', 'W5500 MACRAW': 'w5500',
                             _tr('通用以太网'): 'generic'}.get(self.lwip_driver.get(), 'auto'),
                tinyusb=self.tinyusb_dir.get().strip() or None,
                tinyusb_files=selected_tinyusb, tinyusb_mode=self._tinyusb_mode_value(),
                tinyusb_classes=tinyusb_classes,
                define_values=[x.strip() for x in self.define_var.get().split(';') if x.strip()],
                remove_defines=[x.strip() for x in self.remove_define_var.get().split(';') if x.strip()],
                optimization=(None if self.optimization_var.get() == _tr('保持不变')
                              else self.optimization_var.get()),
                debug_information=({_tr('开启'): True, _tr('关闭'): False}.get(
                    self.debug_info_var.get())),
                scatter_file=self.scatter_var.get().strip() or None,
                clear_scatter=self.clear_scatter_var.get(),
                clean_includes=self.clean_includes_var.get(),
                remove_missing_includes=self.remove_missing_includes_var.get(),
                stack_size=self.stack_size_var.get().strip() or None,
                heap_size=self.heap_size_var.get().strip() or None,
                preview_callback=self.confirm_diff_preview, diff_file=None,
                sdk_dir=self.sdk_var.get().strip() or None, no_download=False)
            self.append_log('=' * 62)
            self.append_log(_tr('开始处理：%s') % proj.path)
            self.status_var.set(_tr('正在写入工程…'))
            self.root.update_idletasks()
            changed = self._background_call(run_tasks, proj, tasks, opts)
            if not changed:
                if getattr(proj, '_planning_failed', False):
                    self.status_var.set(_tr('规划失败，未写入工程。请查看日志中的具体错误。'))
                    messagebox.showerror(_tr('未写入工程'), _tr('有组件规划失败，本次未写入任何工程文件。\n请查看运行日志。'), parent=self.root)
                    return
                self.status_var.set(_tr('操作已取消或工程无需修改。'))
                return
            build_note = ''
            if self.build_after_var.get():
                build_proj = self._project()
                self._background_call(build_keil_targets, build_proj, targets=build_proj.target_names(),
                                   uv4_path=self.user_settings.get('uv4_path'),
                                   rebuild=self.rebuild_var.get())
                build_note = _tr('\nKeil 编译也已完成，详情见运行日志。')
            self.status_var.set(_tr('处理完成。请重新打开或 Reload Keil 工程。'))
            messagebox.showinfo(_tr('完成'), _tr('处理完成。原工程已在同目录生成时间戳备份。\n'
                                '请在 Keil 中重新加载工程。') + build_note,
                                parent=self.root)
        except Exception as e:
            traceback.print_exc()
            self.append_log(_tr('[错误] ') + str(e))
            self.status_var.set(_tr('执行失败。'))
            messagebox.showerror(_tr('执行失败'), str(e), parent=self.root)

    def run(self):
        self.root.mainloop()


def launch_gui(project=None):
    KeilPortGUI(project).run()


# ===========================================================================
# 工程定位 & main
# ===========================================================================
def locate_project(spec):
    if spec:
        p = Path(spec).expanduser()
        if p.is_file():
            return p.resolve()
        if p.is_dir():
            return _pick_project(_find_projects(p))
    return _pick_project(_find_projects(Path.cwd()))


def _find_projects(base):
    """在目录中找 .uvprojx/.uvproj: 先看当前层, 没有则递归子目录(如 MDK-ARM)。"""
    base = Path(base)
    hits = sorted(list(base.glob('*.uvprojx')) + list(base.glob('*.uvproj')))
    if not hits:
        for p in sorted(list(base.rglob('*.uvprojx')) + list(base.rglob('*.uvproj'))):
            try:
                depth = len(p.relative_to(base).parts)
            except ValueError:
                continue
            if depth <= 3:
                hits.append(p)
        # 优先 MDK-ARM 目录下的工程 (CubeMX 风格)
        hits.sort(key=lambda x: (not any(part.lower() == 'mdk-arm' for part in x.parts), str(x)))
    return hits


def _pick_project(hits):
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        log('找到多个 Keil 工程:')
        for i, h in enumerate(hits, 1):
            log('  [%d] %s' % (i, h))
        s = ask('请输入序号 (1-%d)' % len(hits))
        if s and s.isdigit() and 1 <= int(s) <= len(hits):
            return hits[int(s) - 1]
        raise ToolError('未选择工程')
    raise ToolError('未找到 .uvprojx/.uvproj 工程文件 '
                    '(用法: python keil_port_tool.py <工程文件或目录>)')


def _preload_cli_config(argv=None):
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--config')
    known, _unknown = pre.parse_known_args(argv)
    if not known.config:
        return {}, None
    path = Path(known.config).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception as e:
        raise ToolError('无法读取 CLI 配置文件 %s: %s' % (path, e))
    if not isinstance(data, dict):
        raise ToolError('CLI 配置文件根节点必须是 JSON 对象: %s' % path)
    return data, path


def main():
    if sys.version_info < (3, 8):
        raise ToolError('本工具需要 Python 3.8 或更高版本；当前为 %s' %
                        '.'.join(str(x) for x in sys.version_info[:3]))
    apply_user_settings(load_user_settings())
    cli_defaults, cli_config_path = _preload_cli_config()
    ap = argparse.ArgumentParser(
        prog='keil_port_tool.py',
        description='Keil MDK 工程一键移植小助手: FreeRTOS、LVGL、文件系统、RTT、DSP、'
                    'LwIP 与 TinyUSB 可独立或混合执行。',
        epilog='示例:\n'
               '  python keil_port_tool.py                                   # 交互菜单\n'
               '  python keil_port_tool.py MyProj.uvprojx -a --scan User;MyLib\n'
               '  python keil_port_tool.py MyProj.uvprojx --freertos D:\\FreeRTOS\n'
               '  python keil_port_tool.py MyProj.uvprojx --lvgl D:\\lvgl --color-depth 16\n'
               '  python keil_port_tool.py MyProj.uvprojx --fatfs auto --fatfs-mode auto\n'
               '  python keil_port_tool.py MyProj.uvprojx --rtt auto --littlefs auto\n'
               '  python keil_port_tool.py MyProj.uvprojx --cmsis-dsp auto '
               '--dsp-module TransformFunctions\n'
               '  python keil_port_tool.py MyProj.uvprojx --lwip auto --lwip-mode auto --lwip-app mqtt\n'
               '  python keil_port_tool.py MyProj.uvprojx --tinyusb auto --tinyusb-class CDC\n'
               '  python keil_port_tool.py MyProj.uvprojx -a --freertos D:\\FreeRTOS '
               '--lvgl D:\\lvgl --yes\n',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', nargs='?',
                    help='工程文件(.uvprojx/.uvproj)或所在目录; 省略则自动查找')
    ap.add_argument('--version', action='version', version='%(prog)s ' + TOOL_VERSION)
    ap.add_argument('--config', metavar='FILE.json',
                    help='从 JSON 文件载入命令行参数默认值；显式参数优先')
    ap.add_argument('--target', action='append', metavar='NAME',
                    help='只修改指定 Target；可重复使用。省略则修改全部 Target')
    ap.add_argument('-a', '--add-files', action='store_true',
                    help='任务1: 添加 C/C++、汇编、静态库、目标文件与头文件')
    ap.add_argument('--scan', help='任务1扫描目录 (多个用 ; 分隔), 默认工程根目录')
    ap.add_argument('--include-h', action='store_true',
                    help='任务1: 同时把头文件加入工程文件树')
    ap.add_argument('--freertos', metavar='DIR|auto',
                    help='任务2: FreeRTOS 根目录; 填 auto = 自动下载最新发行版')
    ap.add_argument('--rtthread', metavar='DIR|auto',
                    help='RT-Thread 5.2.2 标准单核内核（当前 Cortex-M4）；与 FreeRTOS 互斥')
    ap.add_argument('--no-os2', action='store_true',
                    help='任务2: 不加入 cmsis_os2.c (裸 FreeRTOS, 无 CMSIS-V2 封装)')
    ap.add_argument('--no-freertos-app', action='store_true',
                    help='任务2: 不生成 freertos_app.c/h，也不自动修改 main() 启动调度器')
    ap.add_argument('--lvgl', metavar='DIR|auto',
                    help='任务3: LVGL 根目录; 填 auto = 自动下载 (AC5 工程自动选 v8)')
    ap.add_argument('--color-depth', type=int, choices=[8, 16, 32], default=16,
                    help='任务3: LVGL 色深 (默认 16)')
    ap.add_argument('--ports', action='store_true',
                    help='任务3: 复制 lv_port_disp/indev 移植模板到工程')
    ap.add_argument('--fatfs', metavar='DIR|auto',
                    help='任务4: FatFS 根目录; 填 auto = 自动下载 ST 官方中间件')
    ap.add_argument('--fatfs-mode', choices=['auto', 'rtos', 'baremetal'], default='auto',
                    help='任务4: auto 自动检测 FreeRTOS，或强制 rtos/baremetal（默认 auto）')
    ap.add_argument('--no-fatfs-app', action='store_true',
                    help='任务4: 不生成 FatFs/App、Target 框架，也不修改 main()')
    ap.add_argument('--rtt', metavar='DIR|auto',
                    help='扩展组件: SEGGER RTT 根目录；填 auto 自动下载官方源码')
    ap.add_argument('--rtt-no-printf', action='store_true',
                    help='SEGGER RTT: 不加入 SEGGER_RTT_printf.c')
    ap.add_argument('--rtt-no-syscalls', action='store_true',
                    help='SEGGER RTT: 不接管 Keil printf/fputc')
    ap.add_argument('--rtt-no-asm', action='store_true',
                    help='SEGGER RTT: 不加入 ARMv7M/ARMv8M 汇编加速')
    ap.add_argument('--littlefs', metavar='DIR|auto',
                    help='扩展组件: LittleFS 根目录；填 auto 自动下载官方稳定版')
    ap.add_argument('--littlefs-mode', choices=['auto', 'rtos', 'baremetal'], default='auto',
                    help='LittleFS: 自动检测 FreeRTOS 或强制 rtos/baremetal')
    ap.add_argument('--no-littlefs-port', action='store_true',
                    help='LittleFS: 不生成 Flash 块设备移植模板')
    ap.add_argument('--cmsis-dsp', metavar='DIR|auto',
                    help='扩展组件: CMSIS-DSP 根目录；填 auto 自动准备兼容版本')
    ap.add_argument('--dsp-module', action='append', default=[], metavar='NAME',
                    help='CMSIS-DSP: 只加入指定算法目录；可重复使用，默认全部')
    ap.add_argument('--dsp-float16', action='store_true',
                    help='CMSIS-DSP: AC6 下加入 Float16 聚合源码')
    ap.add_argument('--rtos-guard', action='store_true',
                    help='扩展组件: 生成并自动初始化 FreeRTOS 外设锁')
    ap.add_argument('--guard-resource', action='append', choices=['UART', 'SPI', 'I2C', 'FLASH'],
                    help='外设锁类型；可重复使用，默认全部')
    ap.add_argument('--lwip', metavar='DIR|auto',
                    help='网络组件: LwIP 根目录；填 auto 自动下载官方稳定版')
    ap.add_argument('--lwip-mode', choices=['auto', 'rtos', 'baremetal'], default='auto',
                    help='LwIP: 自动检测 FreeRTOS，或强制 rtos/baremetal')
    ap.add_argument('--lwip-ipv6', action='store_true', help='LwIP: 加入 IPv6 协议源码')
    ap.add_argument('--lwip-driver', choices=list(LWIP_DRIVERS), default='auto',
                    help='LwIP 网卡适配骨架（默认根据 STM32 自动选择）')
    ap.add_argument('--lwip-app', action='append', choices=list(LWIP_SAFE_APPS), default=[],
                    help='LwIP 应用协议；可重复使用，默认加入全部常用应用')
    ap.add_argument('--tinyusb', metavar='DIR|auto',
                    help='USB 组件: TinyUSB 根目录；填 auto 自动下载官方稳定版')
    ap.add_argument('--tinyusb-mode', choices=['device', 'host', 'both'], default='device',
                    help='TinyUSB 角色（默认 device）')
    ap.add_argument('--tinyusb-class', action='append', choices=list(TINYUSB_CLASSES),
                    help='TinyUSB 协议类；可重复使用，默认 CDC')
    ap.add_argument('-D', '--define', dest='define_values', action='append', metavar='NAME[=VALUE]',
                    help='工程设置: 添加或按名称替换宏；可重复使用')
    ap.add_argument('--remove-define', action='append', default=[], metavar='NAME',
                    help='工程设置: 删除宏；可重复使用')
    ap.add_argument('--optimization', choices=['O0', 'O1', 'O2', 'O3', 'Os', 'Oz'],
                    help='工程设置: 设置所选 Target 的优化等级')
    ap.add_argument('--debug-information', choices=['on', 'off'],
                    help='工程设置: 开启或关闭调试信息')
    ap.add_argument('--clean-includes', action='store_true',
                    help='工程设置: 对 Include Path 做规范化去重')
    ap.add_argument('--remove-missing-includes', action='store_true',
                    help='工程设置: 去重并移除确实不存在的 Include Path')
    ap.add_argument('--scatter', dest='scatter_file', metavar='FILE.sct',
                    help='工程设置: 为所选 Target 配置分散加载文件')
    ap.add_argument('--clear-scatter', action='store_true',
                    help='工程设置: 清除自定义 scatter，恢复 Target 内存布局')
    ap.add_argument('--stack-size', metavar='SIZE',
                    help='工程设置: 修改 startup*.s 的 Stack_Size，支持十进制/0x')
    ap.add_argument('--heap-size', metavar='SIZE',
                    help='工程设置: 修改 startup*.s 的 Heap_Size，支持十进制/0x')
    ap.add_argument('--sdk-dir', metavar='DIR',
                    help='源码下载根目录 (默认: 常用库文件 或 工程上层目录)')
    ap.add_argument('--download-retries', type=int, metavar='N',
                    help='下载总尝试次数 1–10（覆盖 GUI 全局设置）')
    ap.add_argument('--proxy', metavar='URL', help='HTTP/HTTPS 下载代理')
    ap.add_argument('--mirror-prefix', metavar='URL', help='GitHub 下载镜像前缀')
    ap.add_argument('--checksum', action='append', default=[], metavar='COMPONENT|URL=SHA256',
                    help='校验下载归档；可重复使用，例如 --checksum tinyusb=<64位SHA256>')
    ap.add_argument('--no-download', action='store_true',
                    help='禁止自动下载, 找不到源码时直接报错')
    ap.add_argument('--dry-run', action='store_true', help='只预览, 不写任何文件')
    ap.add_argument('--diff-file', metavar='FILE',
                    help='把完整 unified diff 导出到指定文件')
    ap.add_argument('--export-project', metavar='FILE.md|json|csv',
                    help='导出源文件、Include、宏、Target 和编译器清单')
    ap.add_argument('--license-report', metavar='FILE.md',
                    help='导出已移植第三方组件的许可证审查清单')
    ap.add_argument('--update-gitignore', choices=['state', 'third-party'],
                    help='追加工具备份规则；third-party 还会忽略复制的第三方源码')
    build_group = ap.add_mutually_exclusive_group()
    build_group.add_argument('--build', action='store_true', help='处理完成后调用 Keil 增量编译')
    build_group.add_argument('--rebuild', action='store_true', help='处理完成后调用 Keil 全量重编译')
    ap.add_argument('--uv4', metavar='UV4.exe', help='Keil UV4.exe 路径')
    ap.add_argument('--build-log', metavar='FILE', help='Keil 命令行编译日志路径')
    ap.add_argument('--uninstall', metavar='COMPONENT',
                    choices=['add_files', 'freertos', 'rtthread', 'lvgl', 'fatfs', 'segger_rtt',
                             'littlefs', 'cmsis_dsp', 'rtos_guard', 'lwip', 'tinyusb'],
                    help='按安装清单卸载指定组件')
    ap.add_argument('--rollback', action='store_true', help='回滚最近一次成功事务')
    ap.add_argument('-y', '--yes', action='store_true', help='跳过确认直接写入')
    ap.add_argument('--cli', action='store_true',
                    help='没有任务参数时使用旧版命令行交互菜单（默认启动 GUI）')
    verbosity = ap.add_mutually_exclusive_group()
    verbosity.add_argument('--quiet', action='store_true', help='减少非错误输出')
    verbosity.add_argument('--verbose', action='store_true', help='显示下载尝试和外部命令等详情')
    valid_dests = {action.dest for action in ap._actions if action.dest != 'help'}
    unknown_config = sorted(set(cli_defaults) - valid_dests)
    if unknown_config:
        ap.error('JSON 配置包含未知字段: %s' % ', '.join(unknown_config))
    if cli_defaults:
        ap.set_defaults(**cli_defaults)
    args = ap.parse_args()

    global _LOG_LEVEL
    _LOG_LEVEL = 'quiet' if args.quiet else 'verbose' if args.verbose else 'normal'
    if cli_config_path:
        verbose('已加载 CLI 配置: %s' % cli_config_path)
    if args.download_retries is not None:
        if not 1 <= args.download_retries <= 10:
            ap.error('--download-retries 必须是 1–10')
        CONFIG['DOWNLOAD_RETRIES'] = args.download_retries
    if args.proxy is not None:
        CONFIG['DOWNLOAD_PROXY'] = args.proxy.strip()
    if args.mirror_prefix is not None:
        CONFIG['MIRROR_PREFIX'] = args.mirror_prefix.strip()
    for checksum_spec in args.checksum:
        if '=' not in checksum_spec:
            ap.error('--checksum 格式应为 COMPONENT|URL=64位SHA256')
        key, value = checksum_spec.rsplit('=', 1)
        key, value = key.strip(), value.strip().lower()
        if not key or not re.match(r'^[0-9a-f]{64}$', value):
            ap.error('--checksum 必须包含完整的 64 位 SHA-256')
        CONFIG.setdefault('DOWNLOAD_CHECKSUMS', {})[key] = value

    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    tasks = []
    if args.add_files:
        tasks.append('add_files')
    if args.freertos:
        tasks.append('freertos')
    if args.rtthread:
        tasks.append('rtthread')
    if args.lvgl:
        tasks.append('lvgl')
    if args.fatfs:
        tasks.append('fatfs')
    if args.rtt:
        tasks.append('segger_rtt')
    if args.littlefs:
        tasks.append('littlefs')
    if args.cmsis_dsp:
        tasks.append('cmsis_dsp')
    if args.rtos_guard:
        tasks.append('rtos_guard')
    if args.lwip:
        tasks.append('lwip')
    if args.tinyusb:
        tasks.append('tinyusb')
    settings_requested = bool(
        args.define_values or args.remove_define or args.optimization or
        args.debug_information or args.clean_includes or args.remove_missing_includes or
        args.scatter_file or args.clear_scatter or args.stack_size or args.heap_size)
    if settings_requested:
        tasks.append('project_settings')

    management_action = bool(args.uninstall or args.rollback or args.export_project or
                             args.license_report or args.update_gitignore or
                             args.build or args.rebuild)
    if not tasks and not args.cli and not management_action:
        launch_gui(args.project or CONFIG['PROJECT'] or None)
        return

    proj_path = locate_project(args.project or CONFIG['PROJECT'])
    proj = KeilProject(proj_path)
    if args.target:
        proj.select_targets(args.target)

    freertos = None if (args.freertos and
                        args.freertos.strip().lower() in ('auto', 'download')) else args.freertos
    lvgl = None if (args.lvgl and args.lvgl.strip().lower() in ('auto', 'download')) else args.lvgl
    fatfs = None if (args.fatfs and args.fatfs.strip().lower() in ('auto', 'download')) else args.fatfs
    segger_rtt = None if (args.rtt and args.rtt.strip().lower() in ('auto', 'download')) else args.rtt
    littlefs = None if (args.littlefs and args.littlefs.strip().lower() in ('auto', 'download')) else args.littlefs
    cmsis_dsp = None if (args.cmsis_dsp and args.cmsis_dsp.strip().lower() in ('auto', 'download')) else args.cmsis_dsp
    lwip = None if (args.lwip and args.lwip.strip().lower() in ('auto', 'download')) else args.lwip
    tinyusb = None if (args.tinyusb and args.tinyusb.strip().lower() in ('auto', 'download')) else args.tinyusb

    opts = SimpleNamespace(interactive=False, yes=args.yes, dry_run=args.dry_run,
                           scan_dirs=args.scan, include_h=args.include_h,
                           freertos=freertos, no_os2=args.no_os2,
                           rtthread=args.rtthread,
                           freertos_app=not args.no_freertos_app,
                           lvgl=lvgl, color_depth=args.color_depth,
                           ports=args.ports, sdk_dir=args.sdk_dir,
                           fatfs=fatfs, fatfs_mode=args.fatfs_mode,
                           fatfs_app=not args.no_fatfs_app,
                           segger_rtt=segger_rtt,
                           rtt_no_printf=args.rtt_no_printf,
                           rtt_no_syscalls=args.rtt_no_syscalls,
                           rtt_no_asm=args.rtt_no_asm,
                           littlefs=littlefs, littlefs_mode=args.littlefs_mode,
                           littlefs_port=not args.no_littlefs_port,
                           cmsis_dsp=cmsis_dsp, dsp_modules=args.dsp_module,
                           dsp_float16=args.dsp_float16,
                           guard_resources=args.guard_resource or ['UART', 'SPI', 'I2C', 'FLASH'],
                           lwip=lwip, lwip_mode=args.lwip_mode, lwip_ipv6=args.lwip_ipv6,
                           lwip_apps=args.lwip_app or list(LWIP_SAFE_APPS),
                           lwip_driver=args.lwip_driver,
                           tinyusb=tinyusb, tinyusb_mode=args.tinyusb_mode,
                           tinyusb_classes=args.tinyusb_class or ['CDC'],
                           define_values=args.define_values or [],
                           remove_defines=args.remove_define,
                           optimization=args.optimization,
                           debug_information=({'on': True, 'off': False}.get(
                               args.debug_information)),
                           clean_includes=args.clean_includes,
                           remove_missing_includes=args.remove_missing_includes,
                           scatter_file=args.scatter_file,
                           clear_scatter=args.clear_scatter,
                           stack_size=args.stack_size, heap_size=args.heap_size,
                           diff_file=args.diff_file,
                           no_download=args.no_download)

    if args.rollback:
        rollback_last_transaction(proj, yes=args.yes)
    elif args.uninstall:
        uninstall_component(proj, args.uninstall, yes=args.yes)
    elif tasks:
        run_tasks(proj, tasks, opts)
        if getattr(proj, '_planning_failed', False):
            raise ToolError('组件规划失败；本次未写入工程，请查看前面的具体错误。')
    elif not management_action:
        interactive_menu(proj)
    if args.update_gitignore:
        mode_text = ('工具状态、备份与整个 Middlewares/Third_Party'
                     if args.update_gitignore == 'third-party' else '工具状态与备份')
        if args.yes or ask_yn('将向 .gitignore 追加%s规则，确认继续?' % mode_text, True):
            update_project_gitignore(proj, args.update_gitignore == 'third-party')
    if args.export_project:
        export_project_manifest(KeilProject(proj.path), args.export_project)
    if args.license_report:
        export_license_report(KeilProject(proj.path), args.license_report)
    if args.build or args.rebuild:
        build_proj = KeilProject(proj.path)
        build_targets = args.target or build_proj.target_names()
        build_keil_targets(build_proj, targets=build_targets, uv4_path=args.uv4,
                           rebuild=args.rebuild, log_file=args.build_log)


# ===========================================================================
# 默认 FreeRTOSConfig.h 模板 (Keil RVDS 移植层 + CMSIS-RTOS V2 就绪)
# ===========================================================================
DEFAULT_FREERTOS_CONFIG = '''\
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

/*------------------------------------------------------------------------------
 * FreeRTOSConfig.h —— 由 keil_port_tool.py 自动生成
 * 适配: Keil MDK (RVDS/ARMCC 移植层) + CMSIS-RTOS V2 (cmsis_os2.c)
 * 注意: 启动调度器前初始化时钟并更新 CMSIS SystemCoreClock。
 *------------------------------------------------------------------------------*/

#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
extern uint32_t SystemCoreClock;
#ifdef __cplusplus
}
#endif

/* 调度器 */
#define configUSE_PREEMPTION                    1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configUSE_TICKLESS_IDLE                 0
#define configCPU_CLOCK_HZ                      ( ( uint32_t ) SystemCoreClock )
#define configTICK_RATE_HZ                      1000
#define configMAX_PRIORITIES                    56
#define configMINIMAL_STACK_SIZE                128
#define configMAX_TASK_NAME_LEN                 16
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1

/* 调试 / 追踪 */
#define configUSE_TRACE_FACILITY                1
#define configUSE_STATS_FORMATTING_FUNCTIONS    0
/* 需要时可改为 2, 并在代码中实现 vApplicationStackOverflowHook() */
#define configCHECK_FOR_STACK_OVERFLOW          0

/* 内存分配 (配合 portable/MemMang/heap_4.c) */
#define configTOTAL_HEAP_SIZE                   15360
/* 打开需实现 vApplicationMallocFailedHook() */
#define configUSE_MALLOC_FAILED_HOOK            0

/* 同步原语 (CMSIS-RTOS V2 依赖) */
#define configUSE_MUTEXES                       1
#define configUSE_RECURSIVE_MUTEXES             1
#define configUSE_COUNTING_SEMAPHORES           1
#define configQUEUE_REGISTRY_SIZE               8

/* 任务通知 */
#define configUSE_TASK_NOTIFICATIONS            1
#define configUSE_APPLICATION_TASK_TAG          0

/* 静态/动态内存分配 (CMSIS-RTOS V2 依赖) */
#define configSUPPORT_STATIC_ALLOCATION         1
#define configSUPPORT_DYNAMIC_ALLOCATION        1

/* 软件定时器 (CMSIS-RTOS V2 的 osTimer 依赖) */
#define configUSE_TIMERS                        1
#define configTIMER_TASK_PRIORITY               4
#define configTIMER_QUEUE_LENGTH                10
#define configTIMER_TASK_STACK_DEPTH            256
#define configUSE_DAEMON_TASK_STARTUP_HOOK      0

/* Hook 函数 (打开需在代码中实现) */
#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0

/* 中断优先级 —— Keil(RVDS) 移植层使用"已左移 4 位"的原始值 */
/* 255 = 最低优先级 15; configMAX_SYSCALL_INTERRUPT_PRIORITY 切勿设为 0 */
#define configKERNEL_INTERRUPT_PRIORITY         255
#define configMAX_SYSCALL_INTERRUPT_PRIORITY    191

/* CMSIS-RTOS V2 */
#define configUSE_POSIX_ERRNO                   1

/* Cortex-M 端口必须直接接管异常向量，不能经过 CubeMX 生成的空 C 处理函数。 */
#define vPortSVCHandler                         SVC_Handler
#define xPortPendSVHandler                      PendSV_Handler

/* 断言：保存失败的源文件和行号，避免只停在无信息的死循环。 */
void vAssertCalled(const char *file, int line);
#define configASSERT( x ) do { if( ( x ) == 0 ) vAssertCalled(__FILE__, __LINE__); } while( 0 )

/* 可选 API (CMSIS-RTOS V2 依赖, 建议保持开启) */
#define INCLUDE_vTaskPrioritySet                1
#define INCLUDE_uxTaskPriorityGet               1
#define INCLUDE_vTaskDelete                     1
#define INCLUDE_vTaskSuspend                    1
#define INCLUDE_xResumeFromISR                  1
#define INCLUDE_xTaskResumeFromISR              1
#define INCLUDE_xTaskDelayUntil                 1
#define INCLUDE_vTaskDelay                      1
#define INCLUDE_xTaskGetSchedulerState          1
#define INCLUDE_xTaskGetCurrentTaskHandle       1
#define INCLUDE_uxTaskGetStackHighWaterMark     1
#define INCLUDE_xTaskGetIdleTaskHandle          1
#define INCLUDE_eTaskGetState                   1
#define INCLUDE_xEventGroupSetBitFromISR        1
#define INCLUDE_xTimerPendFunctionCall          1
#define INCLUDE_xTaskAbortDelay                 1
#define INCLUDE_xTaskGetHandle                  1
#define INCLUDE_xSemaphoreGetMutexHolder        1

#endif /* FREERTOS_CONFIG_H */
'''


if __name__ == '__main__':
    # Windowed PyInstaller has no stdout/stderr; downloads still use write/flush.
    if sys.stdout is None or sys.stderr is None:
        try:
            log_dir = user_settings_path().parent / 'logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            stream = (log_dir / ('desktop-%s-%s.log' % (datetime.now().strftime('%Y%m%d-%H%M%S'), os.getpid()))).open('a', encoding='utf-8', buffering=1)
        except OSError:
            stream = io.StringIO()
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    try:
        main()
    except KeyboardInterrupt:
        log('')
        log('已退出。')
    except ToolError as e:
        log('[错误] ' + str(e))
        if getattr(sys, 'frozen', False) and tk is not None:
            messagebox.showerror('Keil Port Studio', str(e))
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        if getattr(sys, 'frozen', False) and tk is not None:
            try:
                messagebox.showerror('Keil Port Studio', _gt('程序发生错误。请查看诊断日志：\n',
                                                           'An error occurred. See diagnostic logs:\n') + str(user_settings_path().parent / 'logs'))
            except Exception:
                pass
        sys.exit(1)
