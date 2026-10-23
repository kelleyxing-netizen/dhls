"""Proposed AI transformation diagram data, not a production workflow engine."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    title: str
    detail: str
    refs: str


NODES = (
    Node("A01", "system", "授权后脱敏只读导入", "限定用途与可见范围；保留原始来源及快照", "f32 / f40 / f41"),
    Node("A02", "ai", "AI 提取字段与变更草稿", "提取区域、时间窗和修改点；结果待核实，不覆盖原值", "f10 / f14 / f31"),
    Node("A03", "system", "申请关联与有效版本校验", "统一编号映射；核对有效期、确认状态与双时间", "f24 / f30 / f34"),
    Node("A04", "decision", "来源 字段与版本\n是否可确认", "缺项、歧义、断链或读取失败均不默认通过", "f31 / f33 / f35 / f44"),
    Node("A05", "human", "人工补证与字段复核", "返回来源核实；人工修订留痕，不能被 AI 草稿覆盖", "f14 / f33 / f44"),
    Node("A06", "system", "规则引擎检测冲突与影响", "核对展区、装卸口、进撤场及恢复检查时间", "f11 / f31 / f50"),
    Node("A07", "decision", "是否存在冲突\n或需人工澄清", "规则不明确或容量未知时也进入人工路径", "f44 / f50 / f53"),
    Node("A08", "ai", "AI 解释影响并整理调整选项", "关联来源和规则；只生成候选，不承诺档期或容量", "f05 / f15 / f52"),
    Node("A09", "human", "业务协同评审调整草案", "运营与客户协同澄清；由业务负责人确认变更条件", "f22 / f25 / f50"),
    Node("A10", "decision", "调整条件是否齐备\n且已获人工确认", "人工确认草案条件后，仍须重新检测冲突", "f22 / f41 / f44"),
    Node("A11", "ai", "AI 生成交接清单与差异摘要", "列明版本、未结项、来源、责任岗位与截止时间", "f15 / f20 / f23"),
    Node("A12", "human", "授权人员审核本次业务结论", "复核字段、来源、冲突处理及责任；AI 无审批权", "f14 / f22 / f41"),
    Node("A13", "decision", "审批是否通过\n且基线版本未变", "审批中基线变化使本次结论失效，须重新计算", "f24 / f34 / f41"),
    Node("A14", "human", "退回补证或安排重新检测", "记录退回原因；无法确认时挂起，必要时暂停试点", "f35 / f44"),
    Node("A15", "system", "保存已复核版本与确认链", "仅写入独立试点库；保存来源、差异与确认记录", "f25 / f32 / f43 / f45"),
    Node("A16", "human", "人工衔接占档 合同与进撤场", "回到原业务流程办理；各正式节点仍按授权审批", "f01 / f05 / f41 / f52"),
    Node("A17", "system", "审计监测与周期复盘", "记录访问、版本、建议与确认；按约留存及删除", "f43 / f45 / f55"),
)

# (source, target, branch label, return path)
EDGES = (
    ("A01", "A02", "", False),
    ("A02", "A03", "", False),
    ("A03", "A04", "", False),
    ("A04", "A05", "否", False),
    ("A05", "A03", "补证后重新校验", True),
    ("A04", "A06", "是", False),
    ("A06", "A07", "", False),
    ("A07", "A08", "是", False),
    ("A08", "A09", "", False),
    ("A09", "A10", "", False),
    ("A10", "A09", "否 补充条件后再评审", True),
    ("A10", "A03", "是 确认草案后回查版本与冲突", True),
    ("A07", "A11", "否", False),
    ("A11", "A12", "", False),
    ("A12", "A13", "", False),
    ("A13", "A14", "否", False),
    ("A14", "A03", "补证或重算后重新校验", True),
    ("A13", "A15", "是", False),
    ("A15", "A16", "", False),
    ("A16", "A17", "", False),
)


def mermaid():
    lines = ["flowchart TB"]
    for node in NODES:
        label = f'{node.id[1:]} {node.title.replace(chr(10), "<br/>")}<br/>{node.detail}'
        if node.kind == "decision":
            lines.append(f'    {node.id}{{"{label}"}}')
        else:
            lines.append(f'    {node.id}["{label}"]')
    for source, target, label, returning in EDGES:
        if returning:
            lines.append(f"    {source} -. {label} .-> {target}")
        else:
            caption = f"|{label}|" if label else ""
            lines.append(f"    {source} -->{caption} {target}")
    for kind, fill, stroke in (
        ("system", "#f1f6fb", "#7895aa"),
        ("ai", "#eaf7f4", "#72a99c"),
        ("human", "#f3effa", "#a28bb9"),
        ("decision", "#fff8e8", "#b6944f"),
    ):
        lines.append(f"    classDef {kind} fill:{fill},stroke:{stroke},color:#2b4050;")
        ids = ",".join(node.id for node in NODES if node.kind == kind)
        lines.append(f"    class {ids} {kind};")
    return "\n".join(lines)
