#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
=============================================================================
 keil_port_tool.py —— Keil MDK 工程一键移植小助手
=============================================================================
 功能:
   1. 添加新增的 .c/.h 文件到 Keil 工程 (.uvprojx / .uvproj 均支持)
      - 新的 .c 文件  -> 加入工程文件树 (按目录自动分组)
      - 新的 .h 文件  -> 将其所在目录加入 include path (工程检索路径)
      - (可选) 同时把 .h 文件加入工程文件树, 便于浏览
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

 三个功能可独立执行, 也可任意组合。无任务参数时默认启动图形界面；
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
   python keil_port_tool.py MyProj.uvprojx -a --freertos auto --lvgl auto --yes  # 混合
   python keil_port_tool.py MyProj.uvprojx --freertos D:\FreeRTOSv10.5.1\FreeRTOS

 依赖: 仅 Python 3.6+ 标准库, 无任何第三方包。
 说明: 运行前请先关闭 Keil; 脚本写入工程前会自动备份原文件 (*.bak_时间戳)。
=============================================================================
"""

import argparse
import io
import json
import os
import re
import shutil
import sys
import traceback
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
    'SDK_DIR': '',            # 源码下载根目录, 留空 = 自动选择 (常用库文件/工程上层目录)
    'FREERTOS_URL': '',       # FreeRTOS 下载地址覆盖 (留空=自动获取 GitHub 最新版, 可填镜像 zip)
    'LVGL_URL': '',           # LVGL 下载地址覆盖 (同上)
    'CMSIS_OS2_URL': '',      # cmsis_os2.h 下载地址覆盖 (留空=ARM-software/CMSIS_5 官方仓库)
    'CMSIS_OS2_BASE': '',     # CMSIS-V2 适配层(cmsis_os2.c 等3个文件)下载前缀覆盖, 留空=ARM 官方
    'LVGL_COLOR_DEPTH': 16,   # LVGL 色深 8/16/32
    'INCLUDE_H_IN_TREE': False,
}

# FreeRTOS / LVGL 下载默认版本
# 注意: FreeRTOS 主仓库 2024 年起把内核源码移除 (历史 tag 也被重写), 内核源码
#       现在在独立仓库 FreeRTOS-Kernel; CMSIS-RTOS V2 封装用 ARM 官方
#       CMSIS-FreeRTOS 适配层 (Keil 软件包同源)。
FREERTOS_KERNEL_TAG = 'V10.5.1'
LVGL_V8_TAG = 'v8.4.0'          # AC5 工程用 v8 (v9 不支持 AC5)
LVGL_V9_TAG = 'v9.3.0'          # AC6 工程兜底
CMSIS_OS2_H_URL = ('https://raw.githubusercontent.com/ARM-software/CMSIS_5/develop/'
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


class ToolError(Exception):
    pass


# ===========================================================================
# 基础工具函数
# ===========================================================================
_LOG_SINK = None


def log(msg=''):
    """统一日志出口：CLI 打印到终端，GUI 同时写入日志框。"""
    print(msg)
    if _LOG_SINK is not None:
        try:
            _LOG_SINK(str(msg))
        except Exception:
            pass


def info(msg):
    log('[信息] ' + msg)


def warn(msg):
    log('[警告] ' + msg)


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
def _fetch_json(url):
    req = urllib.request.Request(url, headers=GITHUB_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def download_file(url, dest, desc=''):
    """下载文件 (支持 http/https/file), 带进度显示。失败抛出 ToolError。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    info('开始下载 %s ...' % desc)
    info('  地址: %s' % url)
    req = urllib.request.Request(url, headers=GITHUB_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=180) as r, open(str(dest), 'wb') as f:
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
                        sys.stdout.write('\r  进度: %d%% (%d/%d KB)    ' %
                                         (pct, done // 1024, total // 1024))
                        sys.stdout.flush()
            if total:
                sys.stdout.write('\n')
    except Exception as e:
        try:
            dest.unlink()
        except OSError:
            pass
        raise ToolError('下载失败 (请检查网络后重试): %s' % e)
    info('下载完成: %s (%.1f MB)' % (dest, dest.stat().st_size / 1048576.0))


def extract_zip(zip_path, dest_root, keep_prefixes=(), keep_files=()):
    """解压 zip; 可只保留需要的部分 (节省磁盘)。返回解压出的顶层目录。"""
    with zipfile.ZipFile(str(zip_path)) as z:
        names = z.namelist()
        top = names[0].split('/')[0]
        members = []
        for n in names:
            if not n.startswith(top + '/'):
                continue
            rel = n[len(top) + 1:]
            if not rel:
                continue
            if keep_prefixes or keep_files:
                if not (rel.startswith(keep_prefixes) or rel in keep_files):
                    continue
            members.append(n)
        if not members:
            z.extractall(str(dest_root))
        else:
            z.extractall(str(dest_root), members=members)
    return Path(dest_root) / top


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
    local_root = (project_content_root(proj) / 'Middlewares' / 'Third_Party' /
                  folder_name).resolve()
    if source_root == local_root:
        return source_root, local_root

    existing = locator(local_root) if local_root.is_dir() else None
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
        text = task_h.read_text(encoding='utf-8', errors='ignore')
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
            wrapper = (os2_dir / 'cmsis_os2.c').read_text(encoding='utf-8', errors='ignore')
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
    download_file(url, zip_path, 'FreeRTOS 内核 (%s)' % tag)
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
        try:
            rel = _fetch_json('https://api.github.com/repos/lvgl/lvgl/releases/latest')
            url = rel.get('zipball_url')
            tag = rel.get('tag_name') or 'latest'
            if not url:
                raise ValueError('zipball_url missing')
        except Exception:
            tag = LVGL_V9_TAG
            url = 'https://codeload.github.com/lvgl/lvgl/zip/refs/tags/' + tag
    else:
        tag = LVGL_V8_TAG
        url = 'https://codeload.github.com/lvgl/lvgl/zip/refs/tags/' + tag
        info('当前工程为 AC5 编译器, 自动选择 LVGL v8 (%s)' % tag)
    zip_path = sdk / ('lvgl-%s.zip' % tag)
    download_file(url, zip_path, 'LVGL (%s)' % tag)
    top = extract_zip(zip_path, sdk,
                      keep_prefixes=('src/', 'examples/porting/'),
                      keep_files=('lvgl.h', 'lv_conf_template.h', 'LICENSE.txt', 'README.md'))
    root = locate_lvgl_root(top)
    if root is None:
        raise ToolError('下载解压后仍未找到 LVGL 源码结构, '
                        '请手动下载 lvgl 源码 zip 并用 --lvgl 指定目录')
    info('LVGL 已就绪: %s' % top)
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
        'add_files': '添加新增 .c/.h 文件',
        'freertos': '移植 FreeRTOS (CMSIS-RTOS V2)',
        'lvgl': '移植 LVGL',
    }

    def __init__(self, key):
        self.key = key
        self.files = []       # (组名, 文件名)
        self.inc = []         # 新增 include path
        self.defines = []     # 新增宏定义
        self.copy_trees = []  # (源目录, 目标目录, 说明) 待复制的源码树
        self.gen_files = []   # (路径, 内容, 说明) 待写入的文件
        self.obsolete_files = []  # (路径, 说明) 写入时移为时间戳备份
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
        self.tree = ET.parse(str(self.path))
        self.root = self.tree.getroot()
        self.legacy = self.path.suffix.lower() == '.uvproj'
        self.targets = self.root.findall('Targets/Target')
        if not self.targets:
            self.targets = self.root.findall('Target')
        self.dirty = False

    # ---------- 基本信息 ----------
    def device(self):
        for t in self.targets:
            d = t.findtext('TargetOption/TargetCommonOption/Device', '')
            if d:
                return d
        return ''

    def target_names(self):
        return [t.findtext('TargetName', '') for t in self.targets]

    def core_info(self):
        """返回 (内核名, 是否有 FPU)。优先从 CPUTYPE 解析, 其次按芯片型号猜。"""
        for t in self.targets:
            cpu = t.findtext('TargetOption/TargetCommonOption/Cpu', '') or ''
            dev = t.findtext('TargetOption/TargetCommonOption/Device', '') or ''
            m = re.search(r'CPUTYPE\("([^"]+)"\)', cpu)
            if m:
                return m.group(1), ('FPU' in cpu)
            core = core_from_device(dev)
            if core:
                return core, core in ('Cortex-M4', 'Cortex-M7')
        return None, False

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
            file_cads = set()
            for f in t.iter('File'):
                for c in f.findall('.//Cads'):
                    file_cads.add(id(c))
            for c in t.findall('.//Cads'):
                if id(c) in file_cads:
                    continue
                out.append(c)
        return out

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
            tokens = [t.strip() for t in (d.text or '').split(',') if t.strip()]
            if macro in tokens:
                continue
            tokens.append(macro)
            d.text = ','.join(tokens)
            added = True
            self.dirty = True
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
        target = Path(target).resolve()
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

    def save(self):
        """备份后写回工程文件。"""
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        bak = self.path.with_name(self.path.name + '.bak_' + ts)
        shutil.copy2(str(self.path), str(bak))
        buf = io.BytesIO()
        self.tree.write(buf, encoding='utf-8', xml_declaration=True)
        text = buf.getvalue().decode('utf-8')
        text = re.sub(r'^<\?xml[^?]*\?>',
                      '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>',
                      text, count=1)
        if not text.endswith('\n'):
            text += '\n'
        with open(str(self.path), 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        return bak


# ===========================================================================
# 任务1: 添加新增 .c/.h 文件
# ===========================================================================
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

    selected = getattr(opts, 'scan_files', None)
    if selected is not None:
        selected = {os.path.normcase(os.path.normpath(str(x))) for x in selected}
    existing = {proj.norm_file(fp) for fp in proj.files_in_project()}
    inc_dirs = proj.include_dirs_abs()
    inc_dirs.add(os.path.normcase(str(proj.dir)))
    new_c = []
    new_h_dirs = []
    h_files = []
    for d in dirs:
        for f in sorted(d.rglob('*')):
            if not f.is_file():
                continue
            suf = f.suffix.lower()
            if suf not in ('.c', '.h'):
                continue
            if selected is not None and os.path.normcase(os.path.normpath(str(f))) not in selected:
                continue
            try:
                rel = f.relative_to(d)
            except ValueError:
                continue
            if any(p.startswith('.') for p in rel.parts[:-1]):
                continue
            if any(p.lower() in SCAN_SKIP_DIRS for p in rel.parts[:-1]):
                continue
            if suf == '.c':
                key = proj.norm_file(str(f))
                if key in existing:
                    continue
                existing.add(key)
                new_c.append(f)
            else:
                parent_key = os.path.normcase(str(f.parent.resolve()))
                if parent_key in inc_dirs:
                    continue
                inc_dirs.add(parent_key)
                new_h_dirs.append(f.parent)
                if opts.include_h:
                    h_files.append(f)

    for f in new_c:
        fp = rel_or_abs(f, proj.dir)
        rel = fp.rstrip('\\')
        parts = [x for x in rel.split('\\')[:-1] if x not in ('.', '..')]
        group = '/'.join(parts) or 'ProjectRoot'
        if proj.add_file(group, f.name, 1, fp):
            rep.files.append((group, f.name))
    for d in new_h_dirs:
        proj.add_include_path(rel_or_abs(d, proj.dir), rep)
    if opts.include_h:
        for f in h_files:
            fp = rel_or_abs(f, proj.dir)
            parts = [x for x in fp.split('\\')[:-1] if x not in ('.', '..')]
            group = '/'.join(parts) or 'ProjectRoot'
            if proj.add_file(group, f.name, 5, fp):
                rep.files.append((group, f.name))
    if not rep.has_changes():
        rep.notes.append('没有发现新文件, 工程无需修改')
    else:
        rep.notes.append('新增 %d 个 .c 文件, %d 个 .h 检索目录' % (len(new_c), len(new_h_dirs)))


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
                head = src.read_text(encoding='utf-8', errors='ignore')[:12000]
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


def find_project_systick_source(proj, exclude=()):
    excluded = [Path(x).resolve() for x in exclude if x]
    roots = [proj.dir, proj.dir.parent]
    seen = set()
    for root in roots:
        try:
            files = sorted(root.rglob('*.c'))
        except OSError:
            continue
        for path in files:
            rp = path.resolve()
            if rp in seen or any(rp == e or e in rp.parents for e in excluded):
                continue
            seen.add(rp)
            try:
                text = path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            if re.search(r'\bvoid\s+SysTick_Handler\s*\(\s*void\s*\)', text):
                return path, text
    return None, None


def patch_cmsis_wrapper_systick(text):
    """让 ARM v10.5.x cmsis_os2.c 支持由 STM32Cube 接管 SysTick。"""
    if 'USE_CUSTOM_SYSTICK_HANDLER_IMPLEMENTATION' in text:
        return text, False
    pat = re.compile(r'#if\s+defined\s*\(SysTick\)\s*\r?\n(#undef\s+SysTick_Handler)')
    m = pat.search(text)
    if not m:
        return text, False
    replacement = ('#if defined(SysTick) && !defined(USE_CUSTOM_SYSTICK_HANDLER_IMPLEMENTATION)\n'
                   + m.group(1))
    return text[:m.start()] + replacement + text[m.end():], True


def patch_project_systick(text):
    """在 CubeMX USER CODE 区中加入 FreeRTOS tick，返回 (文本, 是否修改)。"""
    if 'xPortSysTickHandler();' in text:
        return text, False
    inc_end = '/* USER CODE END Includes */'
    irq_begin = '/* USER CODE BEGIN SysTick_IRQn 0 */'
    if inc_end not in text or irq_begin not in text:
        return text, False
    includes = ('#include "FreeRTOS.h"\n#include "task.h"\n'
                'extern void xPortSysTickHandler(void);\n')
    text = text.replace(inc_end, includes + inc_end, 1)
    tick = ('\n  if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED)\n'
            '  {\n'
            '    xPortSysTickHandler();\n'
            '  }\n')
    text = text.replace(irq_begin, irq_begin + tick, 1)
    return text, True


def patch_freertos_config(text, device_header=None, custom_systick=False):
    """补齐 CMSIS-RTOS V2 所需宏 (对已有 FreeRTOSConfig.h 使用)。"""
    ensures = [
        ('configUSE_MUTEXES', '1'),
        ('configUSE_RECURSIVE_MUTEXES', '1'),
        ('configUSE_COUNTING_SEMAPHORES', '1'),
        ('configSUPPORT_STATIC_ALLOCATION', '1'),
        ('configSUPPORT_DYNAMIC_ALLOCATION', '1'),
        ('configUSE_POSIX_ERRNO', '1'),
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
                text = text[:m.start()] + newline + text[m.end():]
                changed = True
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
    if device_header:
        macro_line = '#define CMSIS_device_header "%s"' % device_header
        pat = re.compile(r'^[ \t]*#define[ \t]+CMSIS_device_header\b.*$', re.M)
        m = pat.search(text)
        if m:
            if m.group(0).strip() != macro_line:
                text = text[:m.start()] + macro_line + text[m.end():]
                changed = True
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
    # 最后在工程上层做一次受控搜索，避开 Example 目录。
    try:
        hits = sorted(proj.dir.parent.rglob('os_systick.c'))
    except OSError:
        hits = []
    for p in hits:
        if p.is_file() and not any(x.lower() in ('example', 'examples') for x in p.parts):
            return p.resolve()
    return None


def read_ioc_sysclk(project_root):
    """从 CubeMX 的 .ioc 文件读取系统主频 (Hz), 用于自动填写 configCPU_CLOCK_HZ。"""
    root = Path(project_root)
    if not root.is_dir():
        return 0
    for f in sorted(root.glob('*.ioc')):
        try:
            text = f.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        m = re.search(r'RCC\.SYSCLKFreq_VALUE=(\d+)', text)
        if m:
            return int(m.group(1))
    return 0


def patch_main_start_scheduler(text, use_os2=True):
    """在 CubeMX main.c 的 USER CODE 区初始化任务并启动调度器。"""
    changed = False
    old_header = '#include "freertos.h"'
    new_header = '#include "freertos_app.h"'
    if old_header in text:
        text = text.replace(old_header, new_header, 1)
        changed = True
    if re.search(r'\b(osKernelStart|vTaskStartScheduler)\s*\(', text):
        return text, changed
    inc_end = '/* USER CODE END Includes */'
    init_end = '/* USER CODE END 2 */'
    if inc_end not in text or init_end not in text:
        return text, changed
    header = new_header + '\n'
    if new_header not in text:
        text = text.replace(inc_end, header + inc_end, 1)
        changed = True
    if use_os2:
        start = ('\n  /* Create the RTOS objects and start scheduling. */\n'
                 '  osKernelInitialize();\n'
                 '  MX_FREERTOS_Init();\n'
                 '  osKernelStart();\n')
    else:
        start = ('\n  /* Create the RTOS objects and start scheduling. */\n'
                 '  MX_FREERTOS_Init();\n'
                 '  vTaskStartScheduler();\n')
    text = text.replace(init_end, start + init_end, 1)
    return text, True


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
 * Add thread handles/attributes in this file and create them in MX_FREERTOS_Init().
 * Put each task's work in its corresponding task function.
 */
#include "FreeRTOS.h"
#include "task.h"
#include "freertos_app.h"

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
        /* Put the default task processing here. */
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
/* FreeRTOS application layer generated by keil_port_tool.py. */
#include "FreeRTOS.h"
#include "freertos_app.h"

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
            old_h_text = old_h.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            old_h_text = ''
        if 'FREERTOS_APP_H' in old_h_text:
            rep.obsolete_files.append((old_h, '旧文件名与内核 FreeRTOS.h 冲突'))
    if old_c.is_file():
        try:
            old_c_text = old_c.read_text(encoding='utf-8', errors='ignore')
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
            existing_app = app_c.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            existing_app = ''
        if 'MX_FREERTOS_Init' not in existing_app:
            rep.warnings.append('已有 freertos_app.c 但没有 MX_FREERTOS_Init()，未覆盖该文件')
        elif ('FreeRTOS application layer generated by keil_port_tool.py' in existing_app
              and '#include "task.h"' not in existing_app):
            upgraded_app = existing_app.replace(
                '#include "FreeRTOS.h"',
                '#include "FreeRTOS.h"\n#include "task.h"', 1)
            rep.gen_files.append((app_c, upgraded_app, '升级 FreeRTOS 应用层头文件依赖'))
    if proj.add_file('FreeRTOS/Application', app_c.name, 1, rel_or_abs(app_c, proj.dir)):
        rep.files.append(('FreeRTOS/Application', app_c.name))
    proj.add_include_path(rel_or_abs(inc_dir, proj.dir), rep)

    main_candidates = [project_root / 'Core' / 'Src' / 'main.c', proj.dir / 'main.c']
    main_c = next((p for p in main_candidates if p.is_file()), None)
    if main_c:
        main_text = main_c.read_text(encoding='utf-8', errors='ignore')
        new_main, changed = patch_main_start_scheduler(main_text, use_os2)
        if changed:
            rep.gen_files.append((main_c, new_main, '在 main() 中自动启动 FreeRTOS 调度器'))
        elif not re.search(r'\b(osKernelStart|vTaskStartScheduler)\s*\(', main_text):
            rep.warnings.append('main.c 没有标准 CubeMX USER CODE 标记，无法自动插入调度器启动代码')
    else:
        rep.warnings.append('未找到 main.c，无法自动插入调度器启动代码')
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
    if use_os2 and os_tick_c and chosen(os_tick_c):
        if proj.add_file('FreeRTOS/CMSIS-RTOS_V2', os_tick_c.name, 1,
                         rel_or_abs(os_tick_c, proj.dir)):
            rep.files.append(('FreeRTOS/CMSIS-RTOS_V2', os_tick_c.name))
    elif use_os2 and not os_tick_c:
        rep.warnings.append('未找到 CMSIS/RTOS2/Source/os_systick.c，CMSIS 系统计时函数将无法链接')
    if use_os2:
        if os_tick_c and chosen(os_tick_c):
            if proj.add_file('FreeRTOS/CMSIS-RTOS_V2', os_tick_c.name, 1,
                             rel_or_abs(os_tick_c, proj.dir)):
                rep.files.append(('FreeRTOS/CMSIS-RTOS_V2', os_tick_c.name))
        elif os_tick_c is None:
            rep.warnings.append('未找到 CMSIS RTOS2 Source/os_systick.c，链接时将缺少 OS_Tick_Get*')

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
            wrapper_text = os2_c.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            wrapper_text = ''
        if re.search(r'\bvoid\s+SysTick_Handler\s*\(\s*void\s*\)', wrapper_text):
            systick_file, systick_text = find_project_systick_source(
                proj, exclude=[base, project_base])
            if systick_file:
                custom_systick = True
                new_wrapper, wrapper_changed = patch_cmsis_wrapper_systick(wrapper_text)
                if wrapper_changed:
                    rep.gen_files.append((project_path(os2_c), new_wrapper,
                                          '为 CMSIS-RTOS2 SysTick 添加可配置保护'))
                new_irq, irq_changed = patch_project_systick(systick_text)
                if irq_changed:
                    rep.gen_files.append((systick_file, new_irq,
                                          '在现有 SysTick_Handler 中接入 FreeRTOS tick'))
                    rep.notes.append('已复用工程 SysTick_Handler: %s' % systick_file)
                elif 'xPortSysTickHandler();' not in systick_text:
                    rep.warnings.append(
                        '发现重复 SysTick_Handler，但该文件没有 CubeMX USER CODE 标记，'
                        '请手动调用 xPortSysTickHandler(): %s' % systick_file)

    # 3) FreeRTOSConfig.h
    device_header = detect_cmsis_device_header(proj) if use_os2 else None
    if use_os2:
        if device_header:
            rep.notes.append('CMSIS 设备头文件: %s' % device_header)
            # os_systick.c 不包含 FreeRTOSConfig.h，因此还需在工程编译宏中定义。
            proj.add_define('CMSIS_device_header=\\"%s\\"' % device_header, rep)
        else:
            rep.warnings.append('无法自动识别 CMSIS_device_header；请在 FreeRTOSConfig.h 中定义它')
    existing = find_in_tree(proj.dir, 'FreeRTOSConfig.h', exclude=[base, project_base])
    if existing:
        text = existing.read_text(encoding='utf-8', errors='ignore')
        if use_os2:
            text, changed = patch_freertos_config(text, device_header, custom_systick)
            if changed:
                rep.gen_files.append((existing, text,
                                      '修补已有 FreeRTOSConfig.h (补齐 CMSIS-V2 所需宏)'))
        proj.add_include_path(rel_or_abs(existing.parent, proj.dir), rep)
        rep.notes.append('使用已有 FreeRTOSConfig.h: %s' % existing)
    else:
        cfg = proj.dir / 'FreeRTOS' / 'Config' / 'FreeRTOSConfig.h'
        content = DEFAULT_FREERTOS_CONFIG
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
            content = content.replace('72000000UL', '%dUL' % clock)
            rep.notes.append('已从 .ioc 自动读取系统主频: %d Hz' % clock)
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
    rep.notes.append('请把 FreeRTOSConfig.h 中的 configCPU_CLOCK_HZ 改成你的主频')


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
            text = cand.read_text(encoding='utf-8', errors='ignore')
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


def do_lvgl(proj, opts, rep):
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
    if conf.is_file():
        rep.notes.append('检测到已有 lv_conf.h, 未做修改: %s' % conf)
    elif shared_conf.is_file():
        text = shared_conf.read_text(encoding='utf-8', errors='ignore')
        rep.gen_files.append((conf, text, '复制 LVGL 配置到工程'))
        rep.notes.append('将共享 lv_conf.h 复制到工程: %s' % conf)
    elif inner_conf.is_file():
        text = inner_conf.read_text(encoding='utf-8', errors='ignore')
        rep.gen_files.append((conf, text, '复制到 LVGL 默认查找位置'))
        rep.notes.append('将 lv_conf.h 放到 LVGL 目录同级: %s' % conf)
    else:
        tpl = root / 'lv_conf_template.h'
        if not tpl.is_file():
            raise ToolError('未找到 lv_conf_template.h')
        text = tpl.read_text(encoding='utf-8', errors='ignore')
        text = enable_lv_conf(text)
        cd = int(getattr(opts, 'color_depth', 16) or 16)
        text = re.sub(r'(?m)^(\s*#define\s+LV_COLOR_DEPTH\s+)\d+',
                      lambda m: m.group(1) + str(cd), text, count=1)
        rep.gen_files.append((conf, text,
                              '由 lv_conf_template.h 生成并启用 (色深 %d bit)' % cd))
        rep.notes.append('lv_conf.h 生成在 LVGL 目录同级: %s' % conf)

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
            rep.gen_files.append((dst, sf.read_text(encoding='utf-8', errors='ignore'),
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
# 任务调度: 收集报告 -> 确认 -> 写文件
# ===========================================================================
TASK_FUNCS = {'add_files': do_add_files, 'freertos': do_freertos, 'lvgl': do_lvgl}


def run_tasks(proj, tasks, opts):
    reports = []
    for t in tasks:
        rep = Report(t)
        try:
            TASK_FUNCS[t](proj, opts, rep)
        except ToolError as e:
            rep.warnings.append(str(e))
            log('[错误] %s: %s' % (Report.TITLES.get(t, t), e))
        except Exception:
            rep.warnings.append('发生未预期异常, 详见堆栈')
            log('[异常] %s' % Report.TITLES.get(t, t))
            traceback.print_exc()
        reports.append(rep)

    for rep in reports:
        rep.print()

    if not proj.dirty and not any(r.copy_trees or r.gen_files or r.obsolete_files for r in reports):
        info('没有需要写入的更改, 工程保持原样。')
        return
    if getattr(opts, 'dry_run', False):
        info('DRY-RUN 模式: 仅预览, 未写入任何文件。')
        return
    log('[提醒] 如果该工程正在 Keil 中打开, 请先关闭工程再运行本脚本 (或运行后重新打开); '
         '否则 Keil 可能会用内存里的旧内容覆盖磁盘上的修改。')
    if not getattr(opts, 'yes', False):
        if not ask_yn('确认将以上更改写入工程 (写入前会自动备份)?', True):
            info('已取消, 未写入任何文件。')
            return

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    for rep in reports:
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
            path.replace(obsolete_bak)
            log('[已停用] %s: %s -> %s' % (desc, path, obsolete_bak))
        for path, content, desc in rep.gen_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                file_bak = path.with_name(path.name + '.bak_' + ts)
                shutil.copy2(str(path), str(file_bak))
                log('[备份] 原文件已备份到: %s' % file_bak)
            with open(str(path), 'w', encoding='utf-8', newline='') as f:
                f.write(content if content.endswith('\n') else content + '\n')
            log('[已生成] %s -> %s' % (desc, path))
    if proj.dirty:
        bak = proj.save()
        log('[备份] 原工程已备份到: %s' % bak)
        log('[已保存] %s' % proj.path)
    info('完成! 请在 Keil 中重新打开 / 重新加载工程 (Project -> Reload)。')


# ===========================================================================
# 交互菜单
# ===========================================================================
def print_banner(proj):
    log('=' * 60)
    log('  Keil 工程一键移植工具')
    log('  [1] 添加新增 .c/.h     [2] FreeRTOS CMSIS-V2     [3] LVGL')
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
        s = input('  请选择功能 (可多选, 如 12 或 123; 0 退出): ').strip()
        if s in ('0', '', 'q', 'quit', 'exit'):
            break
        tasks = []
        if '1' in s:
            tasks.append('add_files')
        if '2' in s:
            tasks.append('freertos')
        if '3' in s:
            tasks.append('lvgl')
        if not tasks:
            log('  输入无效, 请重新输入。')
            log('')
            continue

        opts = SimpleNamespace(interactive=True, yes=False, dry_run=False,
                               scan_dirs=None, include_h=CONFIG['INCLUDE_H_IN_TREE'],
                               freertos=None, no_os2=False, freertos_app=True, lvgl=None,
                               color_depth=CONFIG['LVGL_COLOR_DEPTH'], ports=False,
                               sdk_dir=CONFIG['SDK_DIR'] or None, no_download=False)

        if 'add_files' in tasks:
            d = ask('任务1: 扫描目录 (默认: 工程根目录递归; 多个用 ; 分隔)',
                    CONFIG['SCAN_DIRS'] or None)
            opts.scan_dirs = d or str(proj.dir)
            opts.include_h = ask_yn('任务1: 同时把新 .h 文件加入工程文件树 (便于浏览)?', False)
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

        log('')
        run_tasks(proj, tasks, opts)
        log('')


# ===========================================================================
# Tkinter 图形界面
# ===========================================================================
class CheckTree(ttk.Frame if tk is not None else object):
    """带三级状态的文件树。单击复选框/名称或按空格切换，目录会级联。"""

    CHECKED = '☑ '
    EMPTY = '☐ '
    PARTIAL = '◩ '

    def __init__(self, master, height=14):
        ttk.Frame.__init__(self, master, style='TreeCard.TFrame')
        self.tree = ttk.Treeview(self, columns=('note',), height=height,
                                 selectmode='browse', style='Modern.Treeview')
        self.tree.heading('#0', text='文件 / 目录', anchor='w')
        self.tree.heading('note', text='用途', anchor='w')
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

    def _new_id(self):
        self._counter += 1
        return 'n%d' % self._counter

    def set_files(self, root, files):
        """files: iterable[(Path, 用途说明)]，默认全部勾选。"""
        self.clear()
        root = Path(root).resolve()
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
                                     text=self.CHECKED + part, values=('目录',), open=True,
                                     tags=('folder',))
                    groups[key] = iid
                parent = groups[key]
            iid = self._new_id()
            self._label[iid] = rel.name
            self._state[iid] = True
            self._path[iid] = path
            self.tree.insert(parent, 'end', iid=iid,
                             text=self.CHECKED + rel.name, values=(note,), tags=('file',))

    def _paint(self, iid):
        state = self._state.get(iid)
        mark = self.CHECKED if state is True else self.EMPTY if state is False else self.PARTIAL
        self.tree.item(iid, text=mark + self._label[iid])

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

    def _click(self, event):
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

    def checked_paths(self):
        return {p for iid, p in self._path.items() if self._state.get(iid) is True}

    def file_count(self):
        return len(self._path)


def available_rvds_ports(base):
    rvds = Path(base) / 'portable' / 'RVDS'
    out = []
    if rvds.is_dir():
        for p in rvds.rglob('port.c'):
            out.append(str(p.parent.relative_to(rvds)).replace('\\', '/'))
    return sorted(set(out))


class KeilPortGUI:
    def __init__(self, initial_project=None):
        if tk is None:
            raise ToolError('当前 Python 没有 Tkinter，请安装带 Tcl/Tk 的 Python，或使用 --cli')
        self.root = tk.Tk()
        self.root.title('Keil Port Studio')
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = min(1180, max(900, screen_w - 100))
        win_h = min(900, max(650, screen_h - 100))
        pos_x = max(0, (screen_w - win_w) // 2)
        pos_y = max(0, (screen_h - win_h) // 2)
        self.root.geometry('%dx%d+%d+%d' % (win_w, win_h, pos_x, pos_y))
        self.root.minsize(860, 620)
        try:
            self.root.option_add('*Font', ('Microsoft YaHei UI', 10))
        except Exception:
            pass
        self._configure_styles()

        self.project_var = tk.StringVar(value=str(initial_project or ''))
        self.sdk_var = tk.StringVar()
        self.scan_var = tk.StringVar()
        self.add_enabled = tk.BooleanVar(value=True)
        self.freertos_enabled = tk.BooleanVar(value=False)
        self.lvgl_enabled = tk.BooleanVar(value=False)
        self.include_h = tk.BooleanVar(value=False)
        self.freertos_dir = tk.StringVar()
        self.heap_var = tk.StringVar(value='heap_4.c')
        self.freertos_app = tk.BooleanVar(value=True)
        self.port_var = tk.StringVar()
        self.lvgl_dir = tk.StringVar()
        self.color_var = tk.StringVar(value='16')
        self.lv_ports = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value='请选择 Keil 工程，然后扫描要添加的文件。')
        self.log_visible = False
        self.freertos_root = None
        self.lvgl_root = None

        self._build()
        global _LOG_SINK
        _LOG_SINK = self.append_log
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
            'header': '#0B1220', 'success': '#0F766E', 'selection': '#DBEAFE',
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
        style.configure('TreeCard.TFrame', background=c['surface'], relief='solid', borderwidth=1)
        style.configure('Header.TFrame', background=c['header'])
        style.configure('HeaderTitle.TLabel', background=c['header'], foreground='#FFFFFF',
                        font=('Microsoft YaHei UI', 20, 'bold'))
        style.configure('HeaderSub.TLabel', background=c['header'], foreground='#A8B3C7',
                        font=('Microsoft YaHei UI', 9))
        style.configure('Badge.TLabel', background='#172554', foreground='#BFDBFE',
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
                        insertcolor=c['text'], padding=(10, 8))
        style.map('TEntry', bordercolor=[('focus', c['primary'])],
                  lightcolor=[('focus', c['primary'])], darkcolor=[('focus', c['primary'])])
        style.configure('TCombobox', fieldbackground='#FFFFFF', foreground=c['text'],
                        bordercolor=c['border'], arrowcolor=c['muted'], padding=(8, 6))
        style.map('TCombobox', bordercolor=[('focus', c['primary'])],
                  fieldbackground=[('readonly', '#FFFFFF')])
        style.configure('TButton', background='#FFFFFF', foreground='#334155',
                        bordercolor=c['border'], lightcolor=c['border'], darkcolor=c['border'],
                        padding=(13, 8), font=('Microsoft YaHei UI', 9, 'bold'))
        style.map('TButton', background=[('active', '#EEF2F7'), ('pressed', '#E2E8F0')])
        style.configure('Primary.TButton', background=c['primary'], foreground='#FFFFFF',
                        bordercolor=c['primary'], lightcolor=c['primary'], darkcolor=c['primary'],
                        padding=(22, 11), font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Primary.TButton', background=[('active', c['primary_hover']),
                                                 ('pressed', '#1E40AF')],
                  foreground=[('disabled', '#CBD5E1')])
        style.configure('Quiet.TButton', background=c['surface'], foreground=c['primary'],
                        borderwidth=0, padding=(9, 6))
        style.map('Quiet.TButton', background=[('active', '#EFF6FF')])
        style.configure('Modern.TCheckbutton', background=c['surface'], foreground=c['text'],
                        padding=(3, 5))
        style.map('Modern.TCheckbutton', background=[('active', c['surface'])],
                  indicatorcolor=[('selected', c['primary']), ('!selected', '#FFFFFF')])
        style.configure('Modern.TNotebook', background=c['bg'], borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure('Modern.TNotebook.Tab', background='#E8EDF5', foreground=c['muted'],
                        borderwidth=0, padding=(22, 11), font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Modern.TNotebook.Tab', background=[('selected', c['surface']), ('active', '#F1F5F9')],
                  foreground=[('selected', c['primary']), ('active', c['text'])])
        style.configure('Modern.Treeview', background='#FFFFFF', fieldbackground='#FFFFFF',
                        foreground='#334155', borderwidth=0, rowheight=29,
                        font=('Microsoft YaHei UI', 9))
        style.configure('Modern.Treeview.Heading', background='#F1F5F9', foreground='#475569',
                        relief='flat', padding=(10, 8), font=('Microsoft YaHei UI', 9, 'bold'))
        style.map('Modern.Treeview', background=[('selected', c['selection'])],
                  foreground=[('selected', c['text'])])

    def _build(self):
        app = ttk.Frame(self.root, style='App.TFrame')
        app.pack(fill='both', expand=True)

        header = ttk.Frame(app, style='Header.TFrame', padding=(24, 8))
        header.pack(fill='x')
        self.header_frame = header
        header.columnconfigure(0, weight=1)
        title_box = ttk.Frame(header, style='Header.TFrame')
        title_box.grid(row=0, column=0, sticky='w')
        ttk.Label(title_box, text='Keil Port Studio', style='HeaderTitle.TLabel').pack(anchor='w')
        ttk.Label(title_box, text='把常用嵌入式组件安全地复制、配置并接入每一个工程',
                  style='HeaderSub.TLabel').pack(anchor='w', pady=(2, 0))
        ttk.Label(header, text='STM32  ·  MDK  ·  CMSIS', style='Badge.TLabel').grid(
            row=0, column=1, sticky='e')

        outer = ttk.Frame(app, style='App.TFrame', padding=(20, 12, 20, 12))
        outer.pack(fill='both', expand=True)
        self.main_frame = outer
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        project_card = ttk.Frame(outer, style='Surface.TFrame', padding=(14, 8))
        project_card.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        self.project_card = project_card
        project_card.columnconfigure(1, weight=3)
        project_card.columnconfigure(4, weight=2)
        ttk.Label(project_card, text='工程', style='Body.TLabel').grid(
            row=0, column=0, sticky='w', padx=(0, 8))
        ttk.Entry(project_card, textvariable=self.project_var).grid(row=0, column=1, sticky='ew')
        ttk.Button(project_card, text='选择工程', command=self.browse_project).grid(
            row=0, column=2, padx=(8, 18))
        ttk.Label(project_card, text='库仓库', style='Body.TLabel').grid(
            row=0, column=3, sticky='w', padx=(0, 8))
        ttk.Entry(project_card, textvariable=self.sdk_var).grid(
            row=0, column=4, sticky='ew')
        ttk.Button(project_card, text='选择目录', command=lambda: self.browse_dir(self.sdk_var)).grid(
            row=0, column=5, padx=(8, 0))

        nb = ttk.Notebook(outer, style='Modern.TNotebook')
        nb.grid(row=1, column=0, sticky='nsew')
        self.notebook = nb
        self._build_add_tab(nb)
        self._build_freertos_tab(nb)
        self._build_lvgl_tab(nb)

        log_frame = ttk.Frame(outer, style='Surface.TFrame', padding=(16, 10))
        log_frame.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        ttk.Label(log_frame, text='运行日志', style='Section.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Button(log_frame, text='清空', style='Quiet.TButton', command=self.clear_log).grid(
            row=0, column=1, sticky='e')
        self.log_box = ScrolledText(log_frame, height=6, wrap='word', state='disabled',
                                    relief='flat', borderwidth=0, padx=12, pady=10,
                                    background='#0F172A', foreground='#CBD5E1',
                                    insertbackground='#FFFFFF', selectbackground='#334155',
                                    font=('Cascadia Mono', 9))
        self.log_box.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(7, 0))
        self.log_frame = log_frame
        self.log_frame.grid_remove()

        bottom = ttk.Frame(outer, style='App.TFrame')
        bottom.grid(row=3, column=0, sticky='ew', pady=(10, 0))
        self.bottom_frame = bottom
        ttk.Label(bottom, text='●', foreground=self.colors['success'],
                  background=self.colors['bg']).grid(row=0, column=0, sticky='w')
        ttk.Label(bottom, textvariable=self.status_var, style='Status.TLabel').grid(
            row=0, column=1, sticky='w', padx=(7, 12))
        bottom.columnconfigure(1, weight=1)
        self.log_toggle_button = ttk.Button(bottom, text='查看日志', style='Quiet.TButton',
                                            command=self.toggle_log)
        self.log_toggle_button.grid(row=0, column=2, sticky='e', padx=(0, 10))
        ttk.Button(bottom, text='开始执行所选任务  →', style='Primary.TButton',
                   command=self.execute).grid(row=0, column=3, sticky='e')

    def _task_header(self, parent, title, description, variable):
        box = ttk.Frame(parent, style='Surface.TFrame')
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text=title, style='TaskTitle.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(box, text=description, style='Muted.TLabel').grid(row=1, column=0, sticky='w', pady=(3, 0))
        ttk.Checkbutton(box, text='启用任务', variable=variable,
                        style='Modern.TCheckbutton').grid(row=0, column=1, rowspan=2, sticky='e')
        return box

    def _tree_buttons(self, parent, tree, scan_command):
        bar = ttk.Frame(parent, style='Surface.TFrame')
        ttk.Button(bar, text='↻  扫描刷新', command=scan_command).pack(side='left')
        ttk.Button(bar, text='全选', style='Quiet.TButton',
                   command=lambda: tree.select_all(True)).pack(side='left', padx=(8, 2))
        ttk.Button(bar, text='清空选择', style='Quiet.TButton',
                   command=lambda: tree.select_all(False)).pack(side='left')
        ttk.Label(bar, text='单击文件夹可整组切换，初次扫描默认全选',
                  style='Muted.TLabel').pack(side='left', padx=12)
        return bar

    def _build_add_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(16, 10))
        nb.add(tab, text='01   C / H 文件')
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(2, weight=1)
        self._task_header(tab, '添加 C / H 文件',
                          '扫描业务代码，将源文件加入 Keil，并自动补齐头文件搜索路径。',
                          self.add_enabled).grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0, 13))
        ttk.Label(tab, text='扫描目录', style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 12))
        ttk.Entry(tab, textvariable=self.scan_var).grid(row=1, column=1, sticky='ew')
        ttk.Button(tab, text='添加目录', command=self.add_scan_dir).grid(
            row=1, column=2, padx=(10, 0))
        self.add_tree = CheckTree(tab, height=15)
        self.add_tree.grid(row=2, column=0, columnspan=3, sticky='nsew', pady=(13, 0))
        bar = self._tree_buttons(tab, self.add_tree, self.scan_new_files)
        bar.grid(row=3, column=0, columnspan=3, sticky='w', pady=(9, 0))
        ttk.Checkbutton(bar, text='.h 文件也显示在工程树', variable=self.include_h,
                        style='Modern.TCheckbutton').pack(side='left', padx=(10, 0))

    def _build_freertos_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(16, 10))
        nb.add(tab, text='02   FreeRTOS')
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)
        self._task_header(tab, 'FreeRTOS + CMSIS-RTOS V2',
                          '创建工程独立副本、任务入口和默认任务，并自动启动调度器。',
                          self.freertos_enabled).grid(row=0, column=0, columnspan=4, sticky='ew', pady=(0, 13))
        ttk.Label(tab, text='共享源码', style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 12))
        ttk.Entry(tab, textvariable=self.freertos_dir).grid(row=1, column=1, sticky='ew')
        ttk.Button(tab, text='浏览', command=lambda: self.browse_dir(self.freertos_dir)).grid(
            row=1, column=2, padx=(10, 0))
        ttk.Button(tab, text='自动准备', command=self.prepare_freertos).grid(
            row=1, column=3, padx=(7, 0))
        opts = ttk.Frame(tab, style='Surface.TFrame')
        opts.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(11, 10))
        ttk.Label(opts, text='内存方案', style='Body.TLabel').pack(side='left')
        ttk.Combobox(opts, textvariable=self.heap_var, width=11, state='readonly',
                     values=['heap_1.c', 'heap_2.c', 'heap_3.c', 'heap_4.c', 'heap_5.c']).pack(side='left', padx=(5, 16))
        ttk.Label(opts, text='CPU 移植层', style='Body.TLabel').pack(side='left')
        self.port_combo = ttk.Combobox(opts, textvariable=self.port_var, width=25)
        self.port_combo.pack(side='left', padx=5)
        ttk.Label(opts, text='自动识别', style='Muted.TLabel').pack(side='left')
        ttk.Checkbutton(opts, text='生成 freertos_app.c/h 并自动启动调度器',
                        variable=self.freertos_app, style='Modern.TCheckbutton').pack(side='left', padx=(18, 0))
        self.freertos_tree = CheckTree(tab, height=15)
        self.freertos_tree.grid(row=3, column=0, columnspan=4, sticky='nsew')
        self._tree_buttons(tab, self.freertos_tree, self.prepare_freertos).grid(
            row=4, column=0, columnspan=4, sticky='w', pady=(7, 0))

    def _build_lvgl_tab(self, nb):
        tab = ttk.Frame(nb, style='Surface.TFrame', padding=(16, 10))
        nb.add(tab, text='03   LVGL')
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)
        self._task_header(tab, 'LVGL 图形库',
                          '复制独立源码、生成配置，并按模块选择要加入工程的组件。',
                          self.lvgl_enabled).grid(row=0, column=0, columnspan=4,
                                                  sticky='ew', pady=(0, 13))
        ttk.Label(tab, text='共享源码', style='Body.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 12))
        ttk.Entry(tab, textvariable=self.lvgl_dir).grid(row=1, column=1, sticky='ew')
        ttk.Button(tab, text='浏览', command=lambda: self.browse_dir(self.lvgl_dir)).grid(
            row=1, column=2, padx=(10, 0))
        ttk.Button(tab, text='自动准备', command=self.prepare_lvgl).grid(
            row=1, column=3, padx=(7, 0))
        opts = ttk.Frame(tab, style='Surface.TFrame')
        opts.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(11, 10))
        ttk.Label(opts, text='颜色深度', style='Body.TLabel').pack(side='left')
        ttk.Combobox(opts, textvariable=self.color_var, state='readonly', width=9,
                     values=['8', '16', '32']).pack(side='left', padx=(5, 16))
        ttk.Checkbutton(opts, text='复制显示/输入设备 porting 模板', variable=self.lv_ports,
                        style='Modern.TCheckbutton').pack(side='left')
        self.lvgl_tree = CheckTree(tab, height=15)
        self.lvgl_tree.grid(row=3, column=0, columnspan=4, sticky='nsew')
        self._tree_buttons(tab, self.lvgl_tree, self.prepare_lvgl).grid(
            row=4, column=0, columnspan=4, sticky='w', pady=(7, 0))

    def clear_log(self):
        self.log_box.configure(state='normal')
        self.log_box.delete('1.0', 'end')
        self.log_box.configure(state='disabled')

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.grid()
            self.log_toggle_button.configure(text='收起日志')
        else:
            self.log_frame.grid_remove()
            self.log_toggle_button.configure(text='查看日志')
        self.root.update_idletasks()

    def append_log(self, msg):
        if not hasattr(self, 'log_box'):
            return
        self.log_box.configure(state='normal')
        self.log_box.insert('end', str(msg) + '\n')
        self.log_box.see('end')
        self.log_box.configure(state='disabled')
        self.root.update_idletasks()

    def _close(self):
        global _LOG_SINK
        _LOG_SINK = None
        self.root.destroy()

    def browse_project(self):
        p = filedialog.askopenfilename(title='选择 Keil 工程',
                                       filetypes=[('Keil 工程', '*.uvprojx *.uvproj'), ('全部文件', '*.*')])
        if p:
            self.project_var.set(p)
            self._project_changed()

    def browse_dir(self, variable):
        p = filedialog.askdirectory(title='选择目录')
        if p:
            variable.set(p)

    def add_scan_dir(self):
        p = filedialog.askdirectory(title='选择要扫描的源码目录')
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
            raise ToolError('请选择有效的 .uvprojx 或 .uvproj 工程文件')
        return KeilProject(p)

    def _project_changed(self):
        try:
            proj = self._project()
            if not self.scan_var.get():
                self.scan_var.set(str(proj.dir))
            if not self.sdk_var.get():
                self.sdk_var.set(str(default_sdk_dir(proj)))
            self.status_var.set('工程：%s；芯片：%s' % (proj.path.name, proj.device() or '未识别'))
        except Exception as e:
            self.status_var.set(str(e))

    def scan_new_files(self):
        try:
            proj = self._project()
            specs = self.scan_var.get().strip() or str(proj.dir)
            dirs = [Path(x.strip().strip('"')).expanduser().resolve()
                    for x in specs.split(';') if x.strip()]
            existing = {proj.norm_file(x) for x in proj.files_in_project()}
            items = []
            for d in dirs:
                if not d.is_dir():
                    continue
                for f in sorted(d.rglob('*')):
                    if not f.is_file() or f.suffix.lower() not in ('.c', '.h'):
                        continue
                    rel = f.relative_to(d)
                    if any(x.startswith('.') or x.lower() in SCAN_SKIP_DIRS for x in rel.parts[:-1]):
                        continue
                    if f.suffix.lower() == '.c' and proj.norm_file(f) in existing:
                        continue
                    note = '加入工程并编译' if f.suffix.lower() == '.c' else '加入所在目录到 Include Path'
                    items.append((f, note))
            try:
                common = Path(os.path.commonpath([str(d) for d in dirs])) if dirs else proj.dir
            except ValueError:  # Windows 跨盘扫描没有共同路径
                common = proj.dir
            self.add_tree.set_files(common, items)
            self.status_var.set('发现 %d 个可处理的 C/H 文件，默认已全选。' % len(items))
        except Exception as e:
            messagebox.showerror('扫描失败', str(e), parent=self.root)

    def _sdk_opts(self):
        return SimpleNamespace(interactive=False, yes=True, dry_run=False,
                               sdk_dir=self.sdk_var.get().strip() or None,
                               no_download=False, port_rel=self.port_var.get().strip() or None)

    def prepare_freertos(self):
        try:
            self.status_var.set('正在准备 FreeRTOS 源码，请稍候…')
            self.root.update_idletasks()
            proj = self._project()
            specified = self.freertos_dir.get().strip()
            if specified:
                base = locate_freertos_source(specified)
                if base is None:
                    raise ToolError('所选目录中未找到 include/FreeRTOS.h')
            else:
                rep = Report('freertos')
                top = ensure_freertos_sdk(proj, self._sdk_opts(), rep)
                base = locate_freertos_source(top) if top else None
                if base is None:
                    raise ToolError('FreeRTOS 下载/定位失败')
                self.freertos_dir.set(str(base))
            try:
                _ensure_cmsis_os2_wrapper(base, self._sdk_opts())
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
                    raise ToolError('无法自动识别内核，请从“RVDS 移植层”下拉框选择一项')
            port_rel = self.port_var.get().strip()
            files = []
            notes = {
                'tasks.c': '任务、调度器、任务通知（核心）',
                'list.c': '内核链表（核心）',
                'queue.c': '队列、信号量、互斥量（核心）',
                'timers.c': '软件定时器（按需）',
                'event_groups.c': '事件组（按需）',
                'stream_buffer.c': '流缓冲区、消息缓冲区（按需）',
                'croutine.c': '协程（按需，较少使用）',
            }
            for name, note in notes.items():
                f = Path(base) / name
                if f.is_file():
                    files.append((f, note))
            mem_dir = Path(base) / 'portable' / 'MemMang'
            heaps = [p.name for p in sorted(mem_dir.glob('heap_[1-5].c'))]
            if not heaps:
                raise ToolError('未找到 portable/MemMang/heap_[1-5].c')
            if self.heap_var.get() not in heaps:
                self.heap_var.set('heap_4.c' if 'heap_4.c' in heaps else heaps[0])
            heap = mem_dir / self.heap_var.get()
            if not heap.is_file():
                raise ToolError('未找到所选内存管理文件：%s' % heap)
            files.append((heap, '动态内存管理方案（请选择且仅使用一个）'))
            port_c = Path(base) / 'portable' / 'RVDS' / port_rel / 'port.c'
            if not port_c.is_file():
                raise ToolError('未找到移植层 port.c：%s' % port_c)
            files.append((port_c, 'CPU/编译器移植层（核心）'))
            os2_c = Path(base) / 'CMSIS_RTOS_V2' / 'cmsis_os2.c'
            if os2_c.is_file():
                files.append((os2_c, 'CMSIS-RTOS V2 API 封装'))
                os_tick_c = find_cmsis_os_tick_source(proj)
                if os_tick_c:
                    files.append((os_tick_c, 'CMSIS-RTOS2 系统节拍实现（必需）'))
                else:
                    warn('未找到 CMSIS RTOS2 Source/os_systick.c')
            else:
                warn('未找到 cmsis_os2.c；当前只能移植原生 FreeRTOS')
            self.freertos_tree.set_files(base, files)
            self.freertos_root = Path(base).resolve()
            self.status_var.set('FreeRTOS 共 %d 个源文件，默认已全选；可取消按需组件。' % len(files))
        except Exception as e:
            messagebox.showerror('FreeRTOS 准备失败', str(e), parent=self.root)
            self.status_var.set('FreeRTOS 准备失败。')

    def prepare_lvgl(self):
        try:
            self.status_var.set('正在准备 LVGL 源码，请稍候…')
            self.root.update_idletasks()
            proj = self._project()
            specified = self.lvgl_dir.get().strip()
            if specified:
                root = locate_lvgl_root(specified)
                if root is None:
                    raise ToolError('所选目录中未找到 lvgl.h 与 src 目录')
            else:
                rep = Report('lvgl')
                top = ensure_lvgl_sdk(proj, self._sdk_opts(), rep)
                root = locate_lvgl_root(top) if top else None
                if root is None:
                    raise ToolError('LVGL 下载/定位失败')
                self.lvgl_dir.set(str(root))
            files = []
            src = Path(root) / 'src'
            for f in sorted(src.rglob('*.c')):
                rel = f.relative_to(src)
                section = rel.parts[0] if len(rel.parts) > 1 else 'core'
                files.append((f, 'LVGL %s 模块' % section))
            self.lvgl_tree.set_files(src, files)
            self.lvgl_root = Path(root).resolve()
            self.status_var.set('LVGL 共 %d 个源文件，默认已全选；可按目录或单文件取消。' % len(files))
        except Exception as e:
            messagebox.showerror('LVGL 准备失败', str(e), parent=self.root)
            self.status_var.set('LVGL 准备失败。')

    def execute(self):
        try:
            proj = self._project()
            tasks = []
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
            if not tasks:
                messagebox.showwarning('未选择任务', '请至少勾选一个任务。', parent=self.root)
                return

            selected_fr = self.freertos_tree.checked_paths()
            if 'freertos' in tasks:
                required = {'tasks.c', 'list.c', 'queue.c', 'port.c'}
                chosen_names = {p.name for p in selected_fr}
                if not required.issubset(chosen_names) or not any(n.startswith('heap_') for n in chosen_names):
                    if not messagebox.askyesno('核心文件未全选',
                        'FreeRTOS 的 tasks.c、list.c、queue.c、port.c 或 heap_x.c 未全部选择，工程很可能无法链接。仍要继续吗？',
                        parent=self.root):
                        return
                if 'cmsis_os2.c' in chosen_names:
                    cmsis_deps = {'tasks.c', 'list.c', 'queue.c', 'timers.c',
                                  'event_groups.c', 'os_systick.c'}
                    missing = sorted(cmsis_deps - chosen_names)
                    if missing:
                        messagebox.showerror(
                            'CMSIS V2 依赖缺失',
                            'cmsis_os2.c 会直接使用这些组件。请重新勾选：\n' +
                            '、'.join(missing) +
                            '\n\n如果只想使用原生 FreeRTOS，也可以取消 cmsis_os2.c。',
                            parent=self.root)
                        return
            if not messagebox.askyesno('确认写入',
                    '将修改工程并自动创建时间戳备份。请先关闭 Keil 中的该工程。\n\n继续吗？',
                    parent=self.root):
                return

            opts = SimpleNamespace(
                interactive=False, yes=True, dry_run=False,
                scan_dirs=self.scan_var.get().strip() or str(proj.dir),
                scan_files=self.add_tree.checked_paths(), include_h=self.include_h.get(),
                freertos=self.freertos_dir.get().strip() or None, no_os2=False,
                freertos_files=selected_fr, heap_file=self.heap_var.get(),
                freertos_app=self.freertos_app.get(),
                port_rel=self.port_var.get().strip() or None,
                lvgl=self.lvgl_dir.get().strip() or None,
                lvgl_files=self.lvgl_tree.checked_paths(),
                color_depth=int(self.color_var.get()), ports=self.lv_ports.get(),
                sdk_dir=self.sdk_var.get().strip() or None, no_download=False)
            self.append_log('=' * 62)
            self.append_log('开始处理：%s' % proj.path)
            self.status_var.set('正在写入工程…')
            self.root.update_idletasks()
            run_tasks(proj, tasks, opts)
            self.status_var.set('处理完成。请重新打开或 Reload Keil 工程。')
            messagebox.showinfo('完成', '处理完成。原工程已在同目录生成时间戳备份。\n请在 Keil 中重新加载工程。',
                                parent=self.root)
        except Exception as e:
            traceback.print_exc()
            self.append_log('[错误] ' + str(e))
            self.status_var.set('执行失败。')
            messagebox.showerror('执行失败', str(e), parent=self.root)

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


def main():
    ap = argparse.ArgumentParser(
        prog='keil_port_tool.py',
        description='Keil MDK 工程一键移植小助手: 添加新文件 / FreeRTOS CMSIS-V2 / LVGL, '
                    '三功能可独立或混合执行。',
        epilog='示例:\n'
               '  python keil_port_tool.py                                   # 交互菜单\n'
               '  python keil_port_tool.py MyProj.uvprojx -a --scan User;MyLib\n'
               '  python keil_port_tool.py MyProj.uvprojx --freertos D:\\FreeRTOS\n'
               '  python keil_port_tool.py MyProj.uvprojx --lvgl D:\\lvgl --color-depth 16\n'
               '  python keil_port_tool.py MyProj.uvprojx -a --freertos D:\\FreeRTOS '
               '--lvgl D:\\lvgl --yes\n',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', nargs='?',
                    help='工程文件(.uvprojx/.uvproj)或所在目录; 省略则自动查找')
    ap.add_argument('-a', '--add-files', action='store_true',
                    help='任务1: 添加新增的 .c/.h 文件')
    ap.add_argument('--scan', help='任务1扫描目录 (多个用 ; 分隔), 默认工程根目录')
    ap.add_argument('--include-h', action='store_true',
                    help='任务1: 同时把 .h 文件加入工程文件树')
    ap.add_argument('--freertos', metavar='DIR|auto',
                    help='任务2: FreeRTOS 根目录; 填 auto = 自动下载最新发行版')
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
    ap.add_argument('--sdk-dir', metavar='DIR',
                    help='源码下载根目录 (默认: 常用库文件 或 工程上层目录)')
    ap.add_argument('--no-download', action='store_true',
                    help='禁止自动下载, 找不到源码时直接报错')
    ap.add_argument('--dry-run', action='store_true', help='只预览, 不写任何文件')
    ap.add_argument('-y', '--yes', action='store_true', help='跳过确认直接写入')
    ap.add_argument('--cli', action='store_true',
                    help='没有任务参数时使用旧版命令行交互菜单（默认启动 GUI）')
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    tasks = []
    if args.add_files:
        tasks.append('add_files')
    if args.freertos:
        tasks.append('freertos')
    if args.lvgl:
        tasks.append('lvgl')

    if not tasks and not args.cli:
        launch_gui(args.project or CONFIG['PROJECT'] or None)
        return

    proj_path = locate_project(args.project or CONFIG['PROJECT'])
    proj = KeilProject(proj_path)

    freertos = None if (args.freertos and
                        args.freertos.strip().lower() in ('auto', 'download')) else args.freertos
    lvgl = None if (args.lvgl and args.lvgl.strip().lower() in ('auto', 'download')) else args.lvgl

    opts = SimpleNamespace(interactive=False, yes=args.yes, dry_run=args.dry_run,
                           scan_dirs=args.scan, include_h=args.include_h,
                           freertos=freertos, no_os2=args.no_os2,
                           freertos_app=not args.no_freertos_app,
                           lvgl=lvgl, color_depth=args.color_depth,
                           ports=args.ports, sdk_dir=args.sdk_dir,
                           no_download=args.no_download)

    if tasks:
        run_tasks(proj, tasks, opts)
    else:
        interactive_menu(proj)


# ===========================================================================
# 默认 FreeRTOSConfig.h 模板 (Keil RVDS 移植层 + CMSIS-RTOS V2 就绪)
# ===========================================================================
DEFAULT_FREERTOS_CONFIG = '''\
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

/*------------------------------------------------------------------------------
 * FreeRTOSConfig.h —— 由 keil_port_tool.py 自动生成
 * 适配: Keil MDK (RVDS/ARMCC 移植层) + CMSIS-RTOS V2 (cmsis_os2.c)
 * 注意: 请将 configCPU_CLOCK_HZ 修改为你的芯片主频!
 *------------------------------------------------------------------------------*/

/* 调度器 */
#define configUSE_PREEMPTION                    1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configUSE_TICKLESS_IDLE                 0
#define configCPU_CLOCK_HZ                      72000000UL   /* <-- 改成你的主频 */
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

/* 断言 */
#define configASSERT( x ) if( ( x ) == 0 ) { taskDISABLE_INTERRUPTS(); for( ;; ); }

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
    try:
        main()
    except KeyboardInterrupt:
        log('')
        log('已退出。')
    except ToolError as e:
        log('[错误] ' + str(e))
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
