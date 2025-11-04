from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple, Dict

from PySide6.QtCore import Qt, QSize, QObject, Signal, QThread
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QToolBar,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QProgressBar,
    QLabel,
    QAbstractItemView,
)

from widgets.image_view import ImageGridView
from services.renamer import (
    compute_number_width,
    generate_preview_mappings,
    has_preview_conflicts,
    two_phase_rename,
)


class RenameWorker(QObject):
    progress_changed = Signal(int, int)  # current, total
    finished = Signal(dict)  # summary

    def __init__(self, mappings: List[Tuple[Path, Path]]):
        super().__init__()
        self._mappings = mappings

    def run(self) -> None:
        total_ops = len(self._mappings) * 2  # A阶段+B阶段
        completed_ops = 0

        def cb() -> None:
            nonlocal completed_ops
            completed_ops += 1
            self._emit_progress(total_ops, completed_ops)

        results = two_phase_rename(self._mappings, progress_callback=cb)

        # 汇总
        success_count = sum(1 for r in results if r[2] is True)
        fail_count = sum(1 for r in results if r[2] is False)
        errors: List[str] = [r[3] for r in results if r[3]]
        summary = {
            "total": len(results),
            "success": success_count,
            "failed": fail_count,
            "errors": errors,
        }
        # 结束
        self.finished.emit(summary)

    def _emit_progress(self, total_ops: int, completed_ops: int) -> None:
        self.progress_changed.emit(completed_ops, total_ops)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("图片批量重命名与缩略图浏览器")
        self.resize(1100, 700)

        # 中心布局
        central = QWidget(self)
        self.setCentralWidget(central)
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(6)

        # 工具栏
        toolbar = QToolBar("工具", self)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        toolbar.setMovable(False)

        self.btn_choose = QPushButton("选择文件夹")
        self.path_display = QLineEdit()
        self.path_display.setReadOnly(True)
        self.path_display.setPlaceholderText("当前路径")
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("输入前缀，例如 Trip_")
        self.btn_preview = QPushButton("生成预览")
        self.btn_rename = QPushButton("执行重命名")
        self.btn_refresh = QPushButton("刷新")

        toolbar.addWidget(self.btn_choose)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("路径:"))
        toolbar.addWidget(self.path_display)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("前缀:"))
        toolbar.addWidget(self.prefix_edit)
        toolbar.addWidget(self.btn_preview)
        toolbar.addWidget(self.btn_rename)
        toolbar.addWidget(self.btn_refresh)

        # 分栏：左侧缩略图，右侧预览表
        splitter = QSplitter(Qt.Horizontal)
        vbox.addWidget(splitter)

        self.image_view = ImageGridView(icon_size=QSize(128, 128))
        splitter.addWidget(self.image_view)

        self.preview_table = QTableWidget(0, 3)
        self.preview_table.setHorizontalHeaderLabels(["旧名称", "新名称", "状态"])
        self.preview_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self.preview_table)
        splitter.setSizes([750, 350])

        # 状态栏与进度
        self.statusBar()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)

        # 连接信号
        self.btn_choose.clicked.connect(self._choose_directory)
        self.btn_refresh.clicked.connect(self._refresh_current)
        self.btn_preview.clicked.connect(self._generate_preview)
        self.btn_rename.clicked.connect(self._execute_rename)

        # 初始状态
        self._current_dir: Path | None = None
        self._current_preview: List[Tuple[Path, Path, str]] = []  # (old, new, status)
        self._has_conflict: bool = False
        self._rename_thread: QThread | None = None
        self._rename_worker: RenameWorker | None = None
        self._set_actions_enabled(True)
        self._update_buttons_state()

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.btn_choose.setEnabled(enabled)
        self.btn_refresh.setEnabled(enabled)
        self.btn_preview.setEnabled(enabled)
        # btn_rename 根据冲突与enabled共同控制
        if enabled:
            self._update_buttons_state()
        else:
            self.btn_rename.setEnabled(False)

    def _update_buttons_state(self) -> None:
        self.btn_rename.setEnabled(bool(self._current_preview) and not self._has_conflict)

    def _choose_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not directory:
            return
        self._load_directory(Path(directory))

    def _refresh_current(self) -> None:
        if self._current_dir:
            self._load_directory(self._current_dir)

    def _load_directory(self, path: Path) -> None:
        self._current_dir = path
        self.path_display.setText(str(path))
        self.statusBar().showMessage("正在加载目录…", 2000)
        self.image_view.load_directory(path)
        # 清空预览
        self._current_preview = []
        self._has_conflict = False
        self.preview_table.setRowCount(0)
        self._update_buttons_state()

    def _generate_preview(self) -> None:
        if not self._current_dir:
            QMessageBox.information(self, "提示", "请先选择目录")
            return
        prefix = self.prefix_edit.text() or ""
        files = self.image_view.current_files()
        if not files:
            QMessageBox.information(self, "提示", "当前目录无图片文件")
            return
        mappings = generate_preview_mappings(self._current_dir, files, prefix)
        self._current_preview = [(m.old_path, m.new_path, m.status) for m in mappings]
        self._has_conflict = has_preview_conflicts(mappings)
        self._fill_preview_table(self._current_preview)
        self._update_buttons_state()
        if self._has_conflict:
            self.statusBar().showMessage("预览包含冲突/非法名称，请修正前缀或文件名后再试", 5000)
        else:
            self.statusBar().showMessage("预览生成完成，可执行重命名", 3000)

    def _fill_preview_table(self, rows: List[Tuple[Path, Path, str]]) -> None:
        self.preview_table.setRowCount(0)
        for old_path, new_path, status in rows:
            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            old_item = QTableWidgetItem(old_path.name)
            new_item = QTableWidgetItem(new_path.name if new_path else "")
            status_item = QTableWidgetItem(status)
            if status != "OK":
                for it in (old_item, new_item, status_item):
                    it.setForeground(Qt.red)
            self.preview_table.setItem(row, 0, old_item)
            self.preview_table.setItem(row, 1, new_item)
            self.preview_table.setItem(row, 2, status_item)

    def _execute_rename(self) -> None:
        if not self._current_dir or not self._current_preview:
            return
        if self._has_conflict:
            QMessageBox.warning(self, "冲突", "存在冲突或非法名称，无法执行重命名")
            return
        reply = QMessageBox.question(
            self,
            "确认",
            "是否执行重命名？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # 仅包含需要更名的映射
        mappings: List[Tuple[Path, Path]] = [
            (old_p, new_p)
            for (old_p, new_p, status) in self._current_preview
            if status == "OK" and old_p.name != new_p.name
        ]
        if not mappings:
            QMessageBox.information(self, "提示", "没有需要修改的文件")
            return

        self._set_actions_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(mappings) * 2)
        self.progress.setValue(0)

        # 线程执行
        self._rename_thread = QThread(self)
        self._rename_worker = RenameWorker(mappings)
        self._rename_worker.moveToThread(self._rename_thread)
        self._rename_thread.started.connect(self._rename_worker.run)
        self._rename_worker.progress_changed.connect(self._on_rename_progress)
        self._rename_worker.finished.connect(self._on_rename_finished)
        self._rename_worker.finished.connect(self._rename_thread.quit)
        self._rename_worker.finished.connect(self._rename_worker.deleteLater)
        self._rename_thread.finished.connect(self._rename_thread.deleteLater)
        self._rename_thread.start()

    def _on_rename_progress(self, current: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)

    def _on_rename_finished(self, summary: Dict[str, int | List[str]]) -> None:
        self.progress.setVisible(False)
        self._set_actions_enabled(True)
        # 刷新视图
        if self._current_dir:
            self._load_directory(self._current_dir)
        # 展示摘要
        msg = f"重命名完成\n总数: {summary['total']}\n成功: {summary['success']}\n失败: {summary['failed']}"
        if summary.get("errors"):
            err_lines = "\n".join(str(e) for e in summary["errors"])  # type: ignore[index]
            msg += f"\n错误详情:\n{err_lines}"
        QMessageBox.information(self, "结果", msg)


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


