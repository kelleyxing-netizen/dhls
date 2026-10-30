#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评测集结构检查：只使用标准库，不调用模型，不把检查成功当作评测通过。

默认读取仓库data/evaluation_cases.json，也可传入其他JSON路径。输出只包含结构
问题及条目序号，避免把评测问题中可能包含的敏感文本原样回显到终端日志。
退出码0表示结构符合约定；1表示文件/内容不合格；2表示命令参数不正确。
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


# 【可复用核心：数据契约】这四个键保持与对话确认的原始评测JSON一致。
# 不额外要求case_id或is_trap，条目序号只用于错误定位，不写回数据文件。
REQUIRED_KEYS = frozenset({"question", "expected_tools", "expected_behavior", "category"})
EXPECTED_COUNTS = {"制度类": 10, "工具调用类": 10, "违规意图类": 5, "多轮上下文类": 5}
ALLOWED_TOOLS = frozenset({
    "search_policy", "get_booking_context", "check_availability",
    "validate_request", "create_draft", "get_workflow_status",
})
TRAP_PREFIX = "陷阱题："
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "evaluation_cases.json"


class DuplicateKeyError(ValueError):
    """JSON对象重复声明同一键时拒绝读取，避免标准解析器静默覆盖前面的内容。"""


def unique_object(pairs):
    """由json.loads回调，所有层级都检查重复键，不依赖第三方JSON Schema包。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("JSON对象含重复键。")
        result[key] = value
    return result


def reject_constant(value):
    # Python默认接受NaN/Infinity，而标准JSON不接受；评测集不应依赖这种扩展。
    raise ValueError("JSON包含非标准数字常量。")


def validate_cases(cases) -> list[str]:
    """【可复用核心】检查结构约束，返回中文问题清单，不计算任何模型质量分数。

    expected_tools是有序列表，允许同一工具在补问/重验后再次出现。这里仅核验
    白名单，不将序列长度当成Agent决策轮数，也不凭名称推断工具已执行成功。
    """
    if not isinstance(cases, list):
        return ["顶层必须是JSON数组。"]
    errors = []
    if len(cases) != 30:
        errors.append(f"评测总数必须是30条，当前为{len(cases)}条。")
    categories = Counter()
    policy_traps = 0
    for number, item in enumerate(cases, start=1):
        label = f"第{number}条"
        if not isinstance(item, dict):
            errors.append(f"{label}必须是JSON对象。")
            continue
        if set(item) != REQUIRED_KEYS:
            # 只报告约定字段名；不回显未知键，避免将注入文本打印为诊断内容。
            errors.append(f"{label}必须且只能包含question、expected_tools、expected_behavior、category四个键。")
        for field in ("question", "expected_behavior", "category"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{label}的{field}必须是非空字符串。")
        category = item.get("category")
        if isinstance(category, str) and category in EXPECTED_COUNTS:
            categories[category] += 1
        else:
            errors.append(f"{label}的category不在约定的四个类别中。")
        tools = item.get("expected_tools")
        if not isinstance(tools, list):
            errors.append(f"{label}的expected_tools必须是数组；无工具时使用空数组。")
        else:
            for tool_number, tool in enumerate(tools, start=1):
                if not isinstance(tool, str) or tool not in ALLOWED_TOOLS:
                    errors.append(f"{label}的第{tool_number}个工具不在当前业务白名单中。")
        behavior = item.get("expected_behavior")
        if isinstance(behavior, str) and behavior.startswith(TRAP_PREFIX):
            if category == "制度类":
                policy_traps += 1
            else:
                errors.append(f"{label}含制度陷阱标记，但类别不是制度类。")
    for category, expected in EXPECTED_COUNTS.items():
        if categories[category] != expected:
            errors.append(f"{category}必须有{expected}条，当前为{categories[category]}条。")
    if policy_traps != 3:
        errors.append(f"制度类中必须恰好3条expected_behavior以“{TRAP_PREFIX}”开头，当前为{policy_traps}条。")
    return errors


class ChineseArgumentParser(argparse.ArgumentParser):
    """命令输入错误统一用中文提示；帮助仍保留标准CLI的--help行为。"""
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "参数不正确，请使用 --help 查看用法。\n")


def main(argv=None) -> int:
    parser = ChineseArgumentParser(
        description="检查30条评测JSON的结构、类别、工具白名单和3条陷阱标记；不运行模型。",
        usage="%(prog)s [评测文件]", add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出。")
    parser.add_argument("json_path", nargs="?", type=Path, default=DEFAULT_PATH,
                        metavar="评测文件", help="可选JSON路径，默认读取仓库data/evaluation_cases.json。")
    args = parser.parse_args(argv)
    try:
        # 兼容UTF-8带BOM输入，只读文件；不会归一化、覆盖或重新生成原始评测内容。
        source = args.json_path.read_text(encoding="utf-8-sig")
        cases = json.loads(source, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except UnicodeError:
        print("校验失败：评测文件须采用UTF-8编码。", file=sys.stderr)
        return 1
    except OSError:
        print("校验失败：无法读取评测文件，请检查路径和读取权限。", file=sys.stderr)
        return 1
    except json.JSONDecodeError as error:
        print(f"校验失败：JSON语法不正确，位置为第{error.lineno}行、第{error.colno}列。", file=sys.stderr)
        return 1
    except (DuplicateKeyError, ValueError):
        print("校验失败：JSON含重复键或非标准数值。", file=sys.stderr)
        return 1
    errors = validate_cases(cases)
    if errors:
        print("评测集结构校验失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("结构校验通过：30条；制度类10条（标记陷阱3条）、工具调用类10条、违规意图类5条、多轮上下文类5条。")
    print("此结果仅代表结构符合约定，尚未执行模型或判断回答质量。")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
