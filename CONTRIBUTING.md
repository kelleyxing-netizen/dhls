# 贡献指南

提交前请先阅读 README、AGENTS.md 与 docs/design.md。

1. 从最新主分支创建自己的工作分支，避免修改他人的未提交成果。
2. 新增事实须记录一手来源、访问日期和适用边界；保留合成材料标识。
3. 修改文档时同步检查 Markdown、DOCX、图片、流程说明和链接的一致性。图像与 Word 文档须视觉检查。
4. 运行 `python scripts/build.py check` 和 `python -m unittest discover -s tests -v`。
5. 在 CHANGELOG.md 记录变化，提交 PR 并说明验证结果及已知限制。

请勿提交真实客户个人信息、未授权合同资料、密钥、字体文件、依赖目录或临时渲染结果。第三方资料只按其实际授权引用；本仓库的 MIT 许可证不改变第三方权利。
