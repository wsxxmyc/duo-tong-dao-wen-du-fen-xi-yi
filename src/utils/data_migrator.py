# -*- coding: utf-8 -*-
"""旧版本数据迁移（%APPDATA% → 软件根目录）。

绿色版改造后，所有用户数据统一存放在软件根目录的「用户数据/」。首次运行新版本时，
若检测到旧版本遗留在 %APPDATA% 的数据（配置 / 数据库），弹窗询问用户是否迁移。

迁移策略：复制（shutil.copy2），**不删除原文件**——%APPDATA% 中的原数据保留作备份，
避免迁移异常导致数据丢失。用户的决定记入「用户数据/.migration_decided」标记文件，
后续启动不再重复弹窗（除非用户清空该标记）。
"""
import os
import shutil

from .helpers import _resolve_app_root, USER_DATA_DIR


# 旧版本数据位置（Windows，绿色版改造前的两套目录）
def _legacy_config_dir():
    """旧配置目录：%APPDATA%/温度分析工具/config。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "温度分析工具", "config")


def _legacy_data_dir():
    """旧数据库目录：%APPDATA%/多通道温度分析仪/data。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "多通道温度分析仪", "data")


# 迁移文件清单
_CONFIG_FILES = ("settings.json", "secrets.json")
_DB_FILES = ("temperature_history.db",
             "temperature_history.db-wal",
             "temperature_history.db-shm")


def _decided_marker():
    """迁移决定标记文件（独立于 settings.json，避免依赖尚未建立的配置）。"""
    return os.path.join(_resolve_app_root(), USER_DATA_DIR, ".migration_decided")


def _has_legacy_data():
    """旧位置是否存在任何数据文件。"""
    for d, files in ((_legacy_config_dir(), _CONFIG_FILES),
                     (_legacy_data_dir(), _DB_FILES)):
        for name in files:
            if os.path.isfile(os.path.join(d, name)):
                return True
    return False


def _has_new_data():
    """新位置是否已有数据（有则跳过，避免覆盖用户已产生的新数据）。"""
    root = _resolve_app_root()
    for sub, files in (("config", _CONFIG_FILES), ("data", _DB_FILES)):
        d = os.path.join(root, USER_DATA_DIR, sub)
        for name in files:
            if os.path.isfile(os.path.join(d, name)):
                return True
    return False


def _is_decided():
    return os.path.isfile(_decided_marker())


def _mark_decided(choice):
    try:
        marker = _decided_marker()
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(choice)
    except Exception:
        pass


def _copy_files(src_dir, dst_dir, names):
    """复制 src_dir 下指定文件到 dst_dir；目标已存在则跳过（不覆盖）。"""
    copied, skipped = [], []
    if not os.path.isdir(src_dir):
        return copied, skipped
    os.makedirs(dst_dir, exist_ok=True)
    for name in names:
        src = os.path.join(src_dir, name)
        dst = os.path.join(dst_dir, name)
        if not os.path.isfile(src):
            continue
        if os.path.exists(dst):
            skipped.append(name)
            continue
        try:
            shutil.copy2(src, dst)
            copied.append(name)
        except Exception as exc:
            skipped.append("%s（失败：%s）" % (name, exc))
    return copied, skipped


def _do_migrate(with_data):
    """执行迁移。with_data=True 迁配置+数据库；False 只迁配置。返回 (copied, skipped)。"""
    root = _resolve_app_root()
    new_cfg = os.path.join(root, USER_DATA_DIR, "config")
    new_data = os.path.join(root, USER_DATA_DIR, "data")
    all_copied, all_skipped = [], []
    c, s = _copy_files(_legacy_config_dir(), new_cfg, _CONFIG_FILES)
    all_copied += c
    all_skipped += s
    if with_data:
        c, s = _copy_files(_legacy_data_dir(), new_data, _DB_FILES)
        all_copied += c
        all_skipped += s
    return all_copied, all_skipped


def _ask_user(parent_widget=None):
    """弹出迁移询问对话框。返回 'all'/'config'/'skip'/'later'，None 表示用户关闭。"""
    try:
        from PyQt5.QtWidgets import QMessageBox, QApplication
    except Exception:
        return "skip"  # 无 PyQt5 环境不弹窗，默认不迁移
    if QApplication.instance() is None:
        return "skip"

    msg = QMessageBox(parent_widget)
    msg.setWindowTitle("数据迁移")
    msg.setIcon(QMessageBox.Question)
    msg.setText(
        "检测到旧版本的数据（配置和历史记录）存放在系统目录（%APPDATA%）。\n\n"
        "本版本为绿色版，数据将存放在软件所在文件夹的「用户数据/」目录。\n\n"
        "是否把旧数据迁移过来？\n"
        "（原数据会保留作备份，不会被删除）"
    )
    btn_all = msg.addButton("迁移全部（配置+历史记录）", QMessageBox.AcceptRole)
    btn_cfg = msg.addButton("只迁移配置", QMessageBox.AcceptRole)
    btn_skip = msg.addButton("不迁移（空白开始）", QMessageBox.RejectRole)
    btn_later = msg.addButton("以后不再提示", QMessageBox.RejectRole)
    msg.setDefaultButton(btn_all)
    msg.exec_()
    clicked = msg.clickedButton()
    if clicked is btn_all:
        return "all"
    if clicked is btn_cfg:
        return "config"
    if clicked is btn_later:
        return "later"
    if clicked is btn_skip:
        return "skip"
    return None


def check_and_migrate(parent_widget=None, ask=_ask_user):
    """启动时调用：检测旧数据并询问是否迁移。

    不触发的情况（返回 None）：已标记过决定 / 新位置已有数据 / 旧位置无数据。
    否则弹窗询问并按用户选择执行，返回 (choice, copied, skipped)。choice 取值
    'all'/'config'/'skip'/'later'。ask 参数供测试注入以避免真弹窗。

    安全保证：全程复制不删除；任何异常都不会阻断启动（调用方无需 try/except）。
    """
    try:
        if _is_decided() or _has_new_data() or not _has_legacy_data():
            return None

        choice = ask(parent_widget)
        if choice is None:
            return None  # 用户直接关闭，不记录，下次启动再问

        _mark_decided(choice)
        if choice in ("all", "config"):
            with_data = (choice == "all")
            copied, skipped = _migrate_with_feedback(with_data)
            return (choice, copied, skipped)
        # 'skip' / 'later'：不复制任何文件
        return (choice, [], [])
    except Exception:
        # 迁移任何异常都不应阻断程序启动
        return None


def _migrate_with_feedback(with_data):
    """迁移复制期间显示「正在迁移历史数据」提示（大库复制可达秒级以上）。

    启动阶段尚无主窗口：复用置顶无边框的 ThemeBusyPopup（可设文字），
    显示后 processEvents 让其先画出再复制；任何反馈失败都不阻断迁移。
    """
    try:
        from PyQt5.QtWidgets import QApplication
    except Exception:
        return _do_migrate(with_data=with_data)
    app = QApplication.instance()
    if app is None:
        return _do_migrate(with_data=with_data)
    try:
        from ui.widgets.theme_busy import ThemeBusyPopup
        popup = ThemeBusyPopup.show_busy()
        popup.set_message("正在迁移历史数据，请稍候…")
        app.processEvents()
        try:
            return _do_migrate(with_data=with_data)
        finally:
            popup.close()
    except Exception:
        return _do_migrate(with_data=with_data)
