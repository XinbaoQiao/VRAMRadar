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
            '错误代码：offline · 最后成功：从未成功。旧快照仅供参考，不计入顶部实时汇总。',
            '已解析 3 台服务器候选；多来源导入不会绑定单一文件自动同步',
            '服务器返回了无效的进程元数据', 'Slurm 返回了不完整的调度快照',
            '已生成并保护 VRAM Radar 专用 Ed25519 密钥',
            '将为“Lab A”生成一把独立的 Ed25519 密钥。\n\n私钥只保存在本机。是否继续？',
            '将为“Lab A”部署所选密钥的公钥。\n\n私钥不会上传。是否继续？',
        ])

    def test_user_content_is_not_erased_or_replaced(self):
        values = ['实验服务器甲', './中文目录', 'python train.py --name 中文实验']
        self.assertEqual(self.translate(values), values)
