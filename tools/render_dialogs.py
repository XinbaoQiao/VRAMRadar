"""Render VRAM Radar's dialogs/toasts to PNG (design review / docs).
Usage: python tools/render_dialogs.py OUTDIR"""
import ctypes, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
ctypes.windll.user32.SetProcessDPIAware()
import clr  # noqa: F401  (pythonnet runtime)
clr.AddReference("System.Drawing"); clr.AddReference("System.Windows.Forms")
from vram_radar import ui_dialogs as ui
from vram_radar.usage_surface import CONSENT_ETA_SECONDS, consent_precheck

out = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(out, exist_ok=True)
apps = {"grok": ("Grok", r"D:\Download\Grok Bot\Grok Bot.exe"), "kimi": ("Kimi", r"D:\Download\Kimi\Kimi.exe")}
made = []
for theme, dark in (("light", False), ("dark", True)):
    for pid, (name, exe) in apps.items():
        made.append(ui.save_preview(ui.consent_spec(name, name, CONSENT_ETA_SECONDS), os.path.join(out, f"consent_{pid}_{theme}.png"),
                                    dark=dark, icon_path=exe))
    made.append(ui.save_preview(ui.revoke_spec("Grok"), os.path.join(out, f"revoke_grok_{theme}.png"), dark=dark,
                                icon_path=apps["grok"][1]))
    made.append(ui.save_preview(ui.notice_spec(consent_precheck({"installed": False}, "Kimi"), "Kimi"),
                                os.path.join(out, f"notice_not_installed_{theme}.png"), dark=dark, icon_path=None))
    made.append(ui.save_preview(ui.notice_spec(consent_precheck({"installed": True, "signed_in": False}, "Grok"), "Grok"),
                                os.path.join(out, f"notice_signed_out_{theme}.png"), dark=dark, icon_path=apps["grok"][1]))
    made.append(ui.save_preview(ui.toast_spec("正在读取 Grok 额度…", f"约 {CONSENT_ETA_SECONDS} 秒内显示在任务栏", "Grok"),
                                os.path.join(out, f"toast_reading_{theme}.png"), dark=dark, icon_path=apps["grok"][1]))
    made.append(ui.save_preview(ui.toast_spec("最多同时显示 4 个", "请先取消一个再勾选", "显存雷达"),
                                os.path.join(out, f"toast_limit_{theme}.png"), dark=dark, icon_path=r"D:\Download\VRAM Radar\VRAMRadar.exe"))
print(len(made), "files")