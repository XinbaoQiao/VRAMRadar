"""Exercise actual translation lookup, including UI paths absent from snapshots."""
import ast
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u3400-\u9fff]")


class MarkupText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = set()

    def handle_data(self, value):
        if CJK.search(value):
            self.text.add(value.strip())

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {"title", "aria-label", "placeholder", "alt", "data-label"} and value and CJK.search(value):
                self.text.add(value)


@unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
class LocalizationCoverageTests(unittest.TestCase):
    def translate(self, values):
        script = """
const fs = require('fs');
const i18n = require('./src/vram_radar/web/localization.js');
const values = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(values.map(value => i18n.translateText(value, 'en'))));
"""
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT,
                                input=json.dumps(values), text=True, encoding="utf-8",
                                capture_output=True, check=True)
        return json.loads(result.stdout)

    def assert_covered(self, values):
        values = sorted(set(values))
        translated = self.translate(values)
        missing = [source for source, output in zip(values, translated) if CJK.search(output)]
        self.assertEqual(missing, [], "Untranslated application text")

    def test_frontend_static_strings_and_accessibility_attributes(self):
        parser = MarkupText()
        parser.feed((ROOT / "src/vram_radar/web/index.html").read_text(encoding="utf-8"))
        values = parser.text
        source = (ROOT / "src/vram_radar/web/app.js").read_text(encoding="utf-8")
        for match in re.finditer(r'''(?<![\w])(['"])((?:\\.|(?!\1)[^\\\n])*)\1''', source):
            value = match[2]
            if CJK.search(value) and '<' not in value and '${' not in value:
                values.add(value)
        self.assert_covered(values)

    def test_backend_literal_exception_messages(self):
        values = set()
        for path in (ROOT / "src/vram_radar").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                    for argument in [*node.exc.args, *(keyword.value for keyword in node.exc.keywords)]:
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str) and CJK.search(argument.value):
                            values.add(argument.value)
        self.assert_covered(values)

    def test_dynamic_states_durations_and_native_confirmation(self):
        self.assert_covered([
            '0秒', '59秒', '1天', '3天5分钟', '2天4小时', '8小时3分钟',
            '当前：Lab A · 我的进程', '当前：Lab B · 工作目录',
            '错误代码：offline · 最后成功：从未成功。上次读取的数据仅供参考，不计入顶部当前统计。',
            '已解析 3 台服务器候选；多来源导入不会绑定单一文件自动同步',
            '服务器返回了无效的进程信息', 'Slurm 返回了不完整的调度数据',
            '已生成并保护 VRAM Radar 专用 Ed25519 密钥',
            '将为“Lab A”生成一把独立的 Ed25519 密钥。\n\n私钥只保存在本机。是否继续？',
            '将为“Lab A”部署所选密钥的公钥。\n\n私钥不会上传。是否继续？',
            '同一条 Host 设置中的其他别名已合并为一台服务器',
            '已跳过 2 个与现有服务器连接设置相同的 SSH 别名',
            '已跳过 1 台你主动移除过的服务器；SSH 配置中新增加的其他 Host 仍会自动导入',
            'cloud-dup 与 cloud-primary 的 GPU 完全一致，判断为同一台服务器，已移除重复条目',
            '发现可能重复的服务器',
            '这些 SSH 别名写在同一条 Host 设置中',
            '这些 SSH 别名可能指向同一台服务器：部分别名写在同一条 Host 设置中，部分读取到的 GPU 完全一致。',
            '全部保留，作为独立服务器',
            '每条连接路径各对应一台服务器',
            '另有别名：4090',
            'researcher@192.0.2.10:10022 · 经中转命令',
        ])

    def test_main_window_banners_navigator_and_durations(self):
        self.assert_covered([
            '1 台服务器尚未监控就绪', '3 台服务器尚未监控就绪',
            '每台卡片会区分网络、认证、配置和资源读取错误；上次读取的数据不会计入当前可用容量。',
            '全部重新验证', '复制诊断', '打开日志', '已置顶', '取消收藏服务器', '恢复监控这台服务器',
            '41分钟10秒', '2小时5秒', '1天30秒', '3天2小时', '12秒',
            '2 排队', '1 暂停', '3 个我的 GPU 进程', '30.5 GiB 可用', '3/8 GPU 调度器报告空闲 · 240 GiB 可用',
            '4090 Workstation，监控就绪，2 GPU · 30.5 GiB 可用，1 个我的 GPU 进程',
            'Lab A6000，数据已过期，数据已过期', 'Local GPU，正在配置中，正在配置中',
            '服务器目录在右侧；拖动或按回车移至左侧', '拖动以调整服务器顺序', '打开通知中心，3 条未读',
            '错误代码：ssh_timeout · 约 240 秒后自动重试。当前没有可显示的 GPU 数据。',
            '正在连接 SSH、检查 GPU 监控组件并读取第一份 GPU 数据；完成前不会显示为错误。',
            '在线显存池，可用 679.1 GiB，共 1648 GiB，可用率 41%，状态充足',
            '过去 48 小时结果', '正在刷新 · 上次目录数据 · 12 项 · 已截断', '未知（共 8 张）',
            '已解析 3 台服务器候选（合并 2 个来源）；尚未保存，尚未连接验证；已保留 1 台本次移除项',
            '配置已保存，正在自动识别 2 台服务器', '2 台服务器未能自动识别，请在设置中手动确认连接类型或私钥路径',
            '当前已是最新版本 1.0.0', '发现新版本 1.0.1', '应用初始化失败：timeout',
            'finetune-7b 已结束。', 'VRAM Radar 1.0.1 已可下载。', 'user[已隐藏]@host',
        ])

    def test_backend_messages_shown_in_the_main_window(self):
        self.assert_covered([
            'SSH 已连接，但远端账号检测失败（远端退出码 1）：当前账号无权读取该类 Slurm 信息',
            '不支持的后端：foo', 'Intel UHD · 共享显存',
            'OpenSSH 静态配置无法判断直连或 Slurm；将在保存验证时自动识别，失败时可手动选择',
            '服务器设置文件不存在：servers.toml', '无法读取 OpenSSH 配置文件：denied',
            '已跳过 2 条条件 Host/Match 中的 Include；这类规则需由 OpenSSH 在实际连接时判断',
            '已确认 3 台服务器由 OpenSSH 配置管理私钥；不会固化路径', '已安全解析 2 个 OpenSSH Include 文件',
            'lab: 缺少 OpenSSH Alias，已跳过', 'config: lab: 与 lab2 重复，保留优先来源',
            'config: lab: ID 与 lab 冲突，已保存为 lab-2', 'GPU 数量必须在 1 到 10000 之间',
            '连接地址或账号已变化；为避免向新端点发送旧密码，已移除 2 台服务器的已保存密码，请重新确认后输入',
            '服务器自动同步失败，当前列表未按本地配置更新：bad。请在设置中检查服务器配置文件路径和内容。',
            '请选择SSH 私钥', 'SSH 私钥路径无效', '找不到SSH 公钥', 'GitHub 返回 HTTP 503，请稍后重试',
            '找不到对应的服务器', '服务器配置版本缺失或已经变化，请重新打开设置',
        ])

    def test_specific_english_wording(self):
        sources = ['41分钟10秒', '1 台服务器尚未监控就绪', '全部重新验证', '。',
                   '4090 Workstation，监控就绪，2 GPU · 30.5 GiB 可用']
        self.assertEqual(self.translate(sources), [
            '41m 10s', '1 server is not monitoring ready yet', 'Revalidate all', '.',
            '4090 Workstation, monitoring ready, 2 GPUs · 30.5 GiB free',
        ])

    def test_chinese_mode_is_unchanged(self):
        script = """
const fs = require('fs');
const i18n = require('./src/vram_radar/web/localization.js');
const values = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(values.map(value => i18n.translateText(value, 'zh-CN'))));
"""
        values = ['41分钟10秒', '全部重新验证', '。', '4090 Workstation，监控就绪，2 GPU · 30.5 GiB 可用']
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, input=json.dumps(values),
                                text=True, encoding="utf-8", capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), values)

    def test_gpu_counts_agree_in_english(self):
        sources = ['1 GPU · 20 GiB 可用', '2 GPU · 20 GiB 可用', '1 张', '3 张', '1 张卡', '1 张（共 1 张）',
                   '未知（共 1 张）', '0/1 GPU 调度器报告空闲', '16 GPU · 空闲容量未知', '1/1 台监控就绪 · 1 张 GPU',
                   'Lab，监控就绪，1 GPU · 2 GiB 可用', 'Lab，监控就绪，4 GPU · 2 GiB 可用，1 运行']
        self.assertEqual(self.translate(sources), [
            '1 GPU · 20 GiB free', '2 GPUs · 20 GiB free', '1 GPU', '3 GPUs', '1 GPU', '1 free of 1 GPU',
            'Unknown (1 GPU total)', '0/1 GPU idle per scheduler', '16 GPUs · Free capacity unknown',
            '1/1 servers ready · 1 GPU', 'Lab, monitoring ready, 1 GPU · 2 GiB free',
            'Lab, monitoring ready, 4 GPUs · 2 GiB free, 1 running'])

    def test_other_counts_agree_in_english(self):
        sources = ['1 项', '2 项', '1 台', '12 台', '1 匹配', '1 天', '3 天', '1 小时', '1 秒', '1 位', '1 条',
                   '1 个节点 · 先按卡型和分区汇总', '匹配 1 台', '匹配 3 台', '过去 1 小时结果', '已跳过 1 台你主动移除过的服务器',
                   '配置已保存，正在自动识别 1 台服务器', '1,000 台']
        self.assertEqual(self.translate(sources), [
            '1 item', '2 items', '1 server', '12 servers', '1 match', '1 day', '3 days', '1 hour', '1 second', '1 user',
            '1 job', '1 node · summarized by GPU model and partition', '1 server matches', '3 servers match',
            'Results from the last 1 hour', 'Skipped 1 server you previously removed',
            'Settings saved; detecting 1 server automatically', '1,000 servers'])

    def test_navigator_label_does_not_repeat_the_state(self):
        self.assertEqual(self.translate(['Lab A6000，数据已过期，数据已过期', 'Local GPU，正在配置中，正在配置中']),
                         ['Lab A6000, Data is stale', 'Local GPU, Configuring'])

    def test_user_content_is_not_erased_or_replaced(self):
        values = ['实验服务器甲', './中文目录', 'python train.py --name 中文实验']
        self.assertEqual(self.translate(values), values)
