from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from PySide6.QtCore import QObject, Signal, QSize, QThreadPool, QRunnable, Qt
from PySide6.QtGui import QImageReader, QPixmap, QImage


@dataclass
class _Task:
    path: Path
    size: QSize
    generation: int


class _ThumbRunnable(QRunnable):
    def __init__(self, task: _Task, emitter: "ThumbnailerService") -> None:
        super().__init__()
        self._task = task
        self._emitter = emitter

    def run(self) -> None:
        try:
            # 加载并缩放
            reader = QImageReader(str(self._task.path))
            reader.setAutoTransform(True)
            img: QImage = reader.read()
            if img.isNull():
                return
            max_w, max_h = self._task.size.width(), self._task.size.height()
            # 使用位置参数，兼容不同 PySide6 版本的关键字
            img = img.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            px = QPixmap.fromImage(img)
            # 缓存检查在发射前由服务完成
            self._emitter._on_worker_ready(self._task, px)
        except Exception:
            # 忽略单个缩略图失败，避免刷屏报错
            return


class ThumbnailerService(QObject):
    thumbnail_ready = Signal(Path, QPixmap, int)  # path, pixmap, generation

    def __init__(self) -> None:
        super().__init__()
        self._pool = QThreadPool.globalInstance()
        self._cache: Dict[Tuple[Path, Tuple[int, int]], QPixmap] = {}
        self._generation = 0

    def cancel_pending(self) -> None:
        # 通过增加代次号让旧任务结果自动丢弃
        self._generation += 1

    def request_thumbnail(self, path: Path, size: QSize) -> None:
        key = (path, (size.width(), size.height()))
        # 命中缓存
        if key in self._cache:
            self.thumbnail_ready.emit(path, self._cache[key], self._generation)
            return
        task = _Task(path=path, size=size, generation=self._generation)
        runnable = _ThumbRunnable(task, self)
        self._pool.start(runnable)

    # 由工作线程回调
    def _on_worker_ready(self, task: _Task, pixmap: QPixmap) -> None:
        # generation 过滤
        if task.generation != self._generation:
            return
        key = (task.path, (task.size.width(), task.size.height()))
        self._cache[key] = pixmap
        self.thumbnail_ready.emit(task.path, pixmap, task.generation)


