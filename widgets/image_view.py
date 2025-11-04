from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from services.thumbnailer import ThumbnailerService


IMAGE_EXTS: Set[str] = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


class ImageGridView(QListWidget):
    def __init__(self, icon_size: QSize = QSize(128, 128), parent=None) -> None:
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setIconSize(icon_size)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setSpacing(8)

        self._current_dir: Path | None = None
        self._path_to_item: Dict[Path, QListWidgetItem] = {}
        self._thumbnailer = ThumbnailerService()
        self._thumbnailer.thumbnail_ready.connect(self._on_thumbnail_ready)

    def set_icon_size(self, size: QSize) -> None:
        self.setIconSize(size)

    def load_directory(self, directory: Path) -> None:
        self._current_dir = directory
        self._thumbnailer.cancel_pending()
        self.clear()
        self._path_to_item.clear()

        files = self._list_images(directory)
        for p in files:
            item = QListWidgetItem(QIcon(), p.name)
            item.setData(Qt.UserRole, str(p))
            # 占位空图标
            item.setIcon(QIcon())
            self.addItem(item)
            self._path_to_item[p] = item
            self._thumbnailer.request_thumbnail(p, self.iconSize())

    def current_files(self) -> List[Path]:
        result: List[Path] = []
        for i in range(self.count()):
            it = self.item(i)
            p = Path(it.data(Qt.UserRole))
            result.append(p)
        return result

    def _list_images(self, directory: Path) -> List[Path]:
        items = []
        try:
            for child in directory.iterdir():
                if not child.is_file():
                    continue
                if child.suffix.lower() in IMAGE_EXTS:
                    items.append(child)
        except Exception:
            items = []
        items.sort(key=lambda p: p.name)
        return items

    def _on_thumbnail_ready(self, path: Path, pixmap: QPixmap, generation: int) -> None:
        # ThumbnailerService 已内部过滤 generation，无需额外判断
        item = self._path_to_item.get(path)
        if item is not None:
            item.setIcon(QIcon(pixmap))


