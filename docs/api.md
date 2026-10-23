# 命令与模块接口

本项目没有 HTTP API，也不会连接生产场馆系统。

## 命令行

```sh
python scripts/build.py check
python scripts/build.py report
python scripts/build.py flow
python scripts/build.py all
python scripts/sync.py --message "docs: 更新成果"
```

`check` 成功退出码为0，发现成果缺失或链接、字体、结构不一致时为1。生成命令的文件、依赖或字体异常会向调用者报告，不会静默改写原始访谈。

## Python 模块

- `dhls.main.main(argv=None) -> int`：分发构建及校验命令。
- `dhls.core.generate.build_report()`：写入 `docs/reports/`，中间基稿写入 `.research/work/`。
- `dhls.core.generate.build_flow()`：写入 `docs/assets/`，预览写入 `.research/`。
- `dhls.core.validate.check_repository(root=ROOT) -> list[str]`：返回问题列表，空列表表示结构检查通过。
- `dhls.core.validate.local_link_errors(path, root=ROOT) -> list[str]`：检查本地 Markdown 链接，支持中文、URL 编码和锚点，不联网验证外链。
- `dhls.utils.paths.artifact_paths(root=ROOT) -> dict[str, Path]`：读取 `config/default.json`，拒绝越出仓库的配置路径。
- `dhls.utils.fonts.resolve_fonts() -> tuple[str, str]`：解析普通与粗体字体文件。

直接导入源码时，把仓库 `src/` 加入 `PYTHONPATH`；仓库自带的脚本入口与示例已处理此路径。

## 发布接口

`sync.py` 仅在 `main` 且 origin URL 匹配本仓库时运行。它先校验，再 fetch；远程不是当前 HEAD 的祖先时停止，不自动合并冲突。随后展示并暂存项目文件、按需提交、普通 push，并核对远程提交号。需要现有 Git 凭据，不接收或保存令牌参数。

如果上一轮已提交但推送失败，无新文件变更时该脚本仍会重试推送已有提交。GitHub Actions 只做校验，不自动写入仓库。
