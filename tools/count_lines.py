#!/usr/bin/env python3
"""递归统计指定目录下特定扩展名文件的总行数。

用法:
    python count_lines.py <目录路径> [--ext .py]

示例:
    python count_lines.py ./src
    python count_lines.py ./src --ext .js
    python count_lines.py ./src --ext py
"""

import argparse
import os
import sys


def count_lines_in_file(filepath: str) -> int | None:
    """统计单个文件的行数。

    Args:
        filepath: 文件路径

    Returns:
        文件行数；若编码错误则返回 None 并在 stderr 输出警告。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except UnicodeDecodeError:
        print(f"Warning: encoding error in {filepath}, skipping", file=sys.stderr)
        return None
    except OSError as e:
        print(f"Warning: OS error accessing {filepath}: {e}, skipping", file=sys.stderr)
        return None


def scan_directory(directory: str, ext: str) -> tuple[int, int]:
    """递归扫描目录，统计匹配扩展名的文件行数。

    Args:
        directory: 要扫描的目录路径
        ext: 目标文件扩展名（如 .py）

    Returns:
        (总行数, 文件数) 的元组
    """
    total_lines: int = 0
    file_count: int = 0

    for root, _dirs, files in os.walk(directory):
        for filename in files:
            if filename.endswith(ext):
                filepath = os.path.join(root, filename)
                lines = count_lines_in_file(filepath)
                if lines is not None:
                    total_lines += lines
                    file_count += 1

    return total_lines, file_count


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    Args:
        argv: 命令行参数列表，为 None 时使用 sys.argv

    Returns:
        解析后的参数命名空间，ext 字段已确保以点号开头
    """
    parser = argparse.ArgumentParser(
        description="递归统计目录下指定扩展名文件的总行数"
    )
    parser.add_argument(
        "directory",
        help="要扫描的目录路径",
    )
    parser.add_argument(
        "--ext",
        default=".py",
        help="目标文件扩展名（默认: .py）。无需加前导点号，py 会自动补全为 .py",
    )
    args = parser.parse_args(argv)

    # 自动补全点号：--ext py → .py，避免 endswith("py") 误匹配如 copy 等文件名
    if not args.ext.startswith("."):
        args.ext = "." + args.ext

    return args


def main(argv: list[str] | None = None) -> None:
    """程序入口：解析参数、校验目录、扫描统计并输出结果。"""
    args = parse_args(argv)

    # 目录不存在时输出错误并退出
    if not os.path.isdir(args.directory):
        print(
            f"Error: directory '{args.directory}' does not exist",
            file=sys.stderr,
        )
        sys.exit(1)

    total_lines, file_count = scan_directory(args.directory, args.ext)

    print(f"Total lines: {total_lines}")
    print(f"Files scanned: {file_count}")


if __name__ == "__main__":
    main()
