from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Tuple
import uuid
import time
import errno


ILLEGAL_CHARS = set('/\\:*?"<>|')  # Windows 非法字符


def natural_key(s: str) -> List[object]:
    # 预留自然排序函数（默认不用）
    import re
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]


def compute_number_width(total_images: int) -> int:
    return max(3, len(str(max(1, total_images))))


def _has_illegal_chars(name: str) -> bool:
    return any(ch in ILLEGAL_CHARS for ch in name)


@dataclass
class PreviewRow:
    old_path: Path
    new_path: Path
    status: str  # "OK" | 错误说明


def generate_preview_mappings(directory: Path, files: List[Path], prefix: str) -> List[PreviewRow]:
    # files 已按文件名升序
    width = compute_number_width(len(files))
    rows: List[PreviewRow] = []
    # 构建现有目标名集合（大小写不敏感的文件系统注意：Windows 默认不区分大小写）
    existing_lower = {p.name.lower() for p in directory.iterdir() if p.is_file()}

    new_names_counter: Dict[str, int] = {}

    for idx, old in enumerate(files, start=1):
        ext = old.suffix  # 保留原扩展
        number = str(idx).zfill(width)
        new_name = f"{prefix}{number}{ext}"
        status = "OK"

        # 非法字符（仅主文件名，不含扩展）
        if _has_illegal_chars(Path(new_name).stem):
            status = "非法名称"

        # 新名重复检测（在生成的清单中）
        low = new_name.lower()
        new_names_counter[low] = new_names_counter.get(low, 0) + 1
        if new_names_counter[low] > 1:
            status = "新名重复"

        # 与现有文件冲突（若最终新名与旧名相同则不算冲突）
        if old.name.lower() != low and low in existing_lower:
            status = "与现有文件冲突"

        rows.append(PreviewRow(old_path=old, new_path=directory / new_name, status=status))

    return rows


def has_preview_conflicts(rows: Iterable[PreviewRow]) -> bool:
    return any(r.status != "OK" for r in rows)


def two_phase_rename(
    mappings: List[Tuple[Path, Path]],
    progress_callback: Callable[[], None] | None = None,
) -> List[Tuple[Path, Path, bool, str | None]]:
    """
    执行两阶段重命名。返回 (old, new, success, error_message)。
    仅处理需要更名的条目（调用方应已过滤）。
    progress_callback: 每完成一个阶段操作回调一次。
    """
    # 阶段 A：改为唯一临时名
    temp_map: Dict[Path, Path] = {}
    results: List[Tuple[Path, Path, bool, str | None]] = []
    errors: Dict[Path, str] = {}

    def tick() -> None:
        if progress_callback:
            progress_callback()

    for old, new in mappings:
        try:
            temp = old.with_name(f"{old.stem}.__tmp__{uuid.uuid4().hex}{old.suffix}")
            _rename_with_retry(old, temp)
            temp_map[temp] = new
            tick()
        except Exception as e:
            errors[old] = f"阶段A失败: {e}"
            results.append((old, new, False, errors[old]))
            tick()  # 仍然推进一次以匹配总进度

    # 阶段 B：从临时名改为目标名
    for temp, target in list(temp_map.items()):
        # 如果该条在阶段A已失败则跳过
        # 但这里 temp_map 仅包含A成功的项
        try:
            # 再次检查目标是否被占用
            if target.exists():
                raise OSError("目标已存在")
            _rename_with_retry(temp, target)
            results.append((target, target, True, None))
            tick()
        except Exception as e:
            # 尝试回滚：把临时名改回原名（最佳努力）
            origin = target.with_name(target.stem.split(".__tmp__")[0] + target.suffix)
            try:
                if not origin.exists():
                    _rename_with_retry(temp, origin)
            except Exception:
                pass
            results.append((origin, target, False, f"阶段B失败: {e}"))
            tick()

    return results


def _rename_with_retry(src: Path, dst: Path, retries: int = 8, base_delay: float = 0.05) -> None:
    """在 Windows 上对占用错误进行指数退避重试。"""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            src.rename(dst)
            return
        except Exception as e:  # noqa: BLE001
            last_exc = e
            # 判定是否为可重试的临时性错误
            winerr = getattr(e, "winerror", None)
            err_no = getattr(e, "errno", None)
            msg = str(e).lower()
            transient = (
                isinstance(e, PermissionError)
                or winerr in (5, 32, 33)  # 5=拒绝访问, 32=正被另一进程使用, 33=进程无法访问文件
                or err_no in (errno.EACCES, errno.EBUSY)
                or ("used by another process" in msg or "being used" in msg)
            )
            if attempt < retries - 1 and transient:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise
    if last_exc:
        raise last_exc


