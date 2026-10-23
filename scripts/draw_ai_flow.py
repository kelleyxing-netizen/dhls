"""Render the proposed transformation diagram and its matching Markdown."""

import math
import re
from PIL import Image, ImageDraw, ImageFont
from dhls.core.target_flow import NODES, EDGES, mermaid
from dhls.utils.fonts import resolve_fonts
from dhls.utils.paths import ROOT, artifact_paths

W,H=1800,4050
im=Image.new('RGB',(W,H),'white');d=ImageDraw.Draw(im)
FONT,BOLD=resolve_fonts()
INK='#2B4050';MUTED='#617F93';LINE='#627F93'
PALETTE={
    'system':('#F1F6FB','#7895AA','系统自动处理'),
    'ai':('#EAF7F4','#72A99C','AI 辅助生成'),
    'human':('#F3EFFA','#A28BB9','人工审核与办理'),
    'decision':('#FFF8E8','#B6944F','判断闸门'),
}
BOXES={
    'A01':(150,430,670,140),'A02':(150,640,670,150),
    'A03':(150,860,670,150),'A05':(1020,1090,650,150),
    'A06':(150,1330,670,160),'A08':(1020,1550,650,160),
    'A09':(1020,1810,650,170),'A11':(150,1810,670,160),
    'A12':(150,2060,670,160),'A14':(1020,2290,650,170),
    'A15':(150,2540,670,160),'A16':(150,2780,670,160),
    'A17':(150,3020,670,160),
}
DIAMONDS={'A04':(485,1160,230,85),'A07':(485,1630,230,85),
          'A10':(1345,2130,240,85),'A13':(485,2370,230,85)}
ROUTES={
    ('A01','A02'):[(485,570),(485,640)],
    ('A02','A03'):[(485,790),(485,860)],
    ('A03','A04'):[(485,1010),(485,1075)],
    ('A04','A05'):[(715,1160),(1020,1160)],
    ('A05','A03'):[(1345,1090),(1345,970),(820,970)],
    ('A04','A06'):[(485,1245),(485,1330)],
    ('A06','A07'):[(485,1490),(485,1545)],
    ('A07','A08'):[(715,1630),(1020,1630)],
    ('A08','A09'):[(1345,1710),(1345,1810)],
    ('A09','A10'):[(1345,1980),(1345,2045)],
    ('A10','A09'):[(1105,2130),(945,2130),(945,1895),(1020,1895)],
    ('A10','A03'):[(1585,2130),(1750,2130),(1750,900),(820,900)],
    ('A07','A11'):[(485,1715),(485,1810)],
    ('A11','A12'):[(485,1970),(485,2060)],
    ('A12','A13'):[(485,2220),(485,2285)],
    ('A13','A14'):[(715,2370),(1020,2370)],
    ('A14','A03'):[(1670,2370),(1710,2370),(1710,3215),(75,3215),(75,940),(150,940)],
    ('A13','A15'):[(485,2455),(485,2540)],
    ('A15','A16'):[(485,2700),(485,2780)],
    ('A16','A17'):[(485,2940),(485,3020)],
}

def font(size,bold=False):return ImageFont.truetype(BOLD if bold else FONT,size)
def text(x,y,value,size=23,color=INK,bold=False):d.text((x,y),value,font=font(size,bold),fill=color)
def center(x,y,value,size=25,color=INK,bold=False):
    text(x-d.textlength(value,font=font(size,bold))/2,y,value,size,color,bold)
def lines(value,width,size=21):
    result=[]
    for raw in value.split('\n'):
        current=''
        for token in re.findall(r'\[[^\]]+\]|[A-Za-z0-9_./-]+|.',raw):
            if current and d.textlength(current+token,font=font(size))>width:
                result.append(current);current=token
            else:current+=token
        result.append(current)
    return result
def paragraph(x,y,value,width,size=21,color=MUTED,spacing=31):
    for line in lines(value,width,size):text(x,y,line,size,color);y+=spacing
    return y
def arrow(points,dashed=False):
    for (x1,y1),(x2,y2) in zip(points,points[1:]):
        length=math.hypot(x2-x1,y2-y1)
        if dashed:
            for start in range(0,int(length),18):
                end=min(start+10,length)
                d.line((x1+(x2-x1)*start/length,y1+(y2-y1)*start/length,
                        x1+(x2-x1)*end/length,y1+(y2-y1)*end/length),fill=LINE,width=3)
        else:d.line((x1,y1,x2,y2),fill=LINE,width=3)
    (x0,y0),(x,y)=points[-2:];a=math.atan2(y-y0,x-x0)
    d.polygon([(x,y),(x-16*math.cos(a)+8*math.sin(a),y-16*math.sin(a)-8*math.cos(a)),
               (x-16*math.cos(a)-8*math.sin(a),y-16*math.sin(a)+8*math.cos(a))],fill=LINE)
def label(x,y,value,size=21):
    b=d.textbbox((x,y),value,font=font(size));d.rectangle((b[0]-5,b[1]-3,b[2]+5,b[3]+3),fill='white')
    text(x,y,value,size,MUTED)

text(70,42,'展会排期与需求变更',50,bold=True)
text(70,116,'AI 数智化转型后目标流程',35,bold=True)
text(70,174,'目标设计 · 尚未实施 · 17 个节点 / 4 个判断 · 基于合成访谈',24,MUTED)
d.rounded_rectangle((70,230,1730,296),radius=10,fill='#F1F5F8',outline='#D9E4EC',width=2)
text(94,247,'资料先脱敏 → AI 提取草稿 → 系统核验与冲突检测 → 人工审批 → 可追溯交接',26)
x=70
for kind in ('system','ai','human','decision'):
    fill,stroke,caption=PALETTE[kind];width=int(d.textlength(caption,font=font(21)))+36
    d.rounded_rectangle((x,326,x+width,368),radius=8,fill=fill,outline=stroke,width=1)
    text(x+18,333,caption,21);x+=width+20

assert {(s,t) for s,t,_,_ in EDGES}==set(ROUTES)
for source,target,_,returning in EDGES:arrow(ROUTES[(source,target)],returning)

for node in NODES:
    fill,stroke,_=PALETTE[node.kind]
    if node.kind=='decision':
        cx,cy,rx,ry=DIAMONDS[node.id]
        d.polygon([(cx,cy-ry),(cx+rx,cy),(cx,cy+ry),(cx-rx,cy)],fill=fill,outline=stroke,width=3)
        for i,line in enumerate(node.title.split('\n')):
            center(cx,cy-42+i*34,(node.id[1:]+'  ' if i==0 else '')+line,25)
        center(cx,cy+36,'人工或规则闸门',18,MUTED)
    else:
        x,y,w,h=BOXES[node.id]
        d.rounded_rectangle((x,y,x+w,y+h),radius=13,fill=fill,outline=stroke,width=2)
        title=node.id[1:]+'  '+node.title
        assert d.textlength(title,font=font(27))<w-30,(node.id,'title')
        center(x+w/2,y+16,title,27)
        detail=lines(node.detail,w-42,21)
        for i,line in enumerate(detail):center(x+w/2,y+62+i*30,line,21,MUTED)
        center(x+w/2,y+h-35,'需求依据 ['+node.refs+']',18,MUTED)
        assert 62+(len(detail)-1)*30+24<=h-38,(node.id,'detail')

# Supplementary controls are not connected as extra business steps.
d.rounded_rectangle((1020,430,1670,750),radius=12,fill='#F8FAFC',outline='#D9E4EC',width=2)
text(1046,451,'权限与数据保护  贯穿全部节点',26,'#2D7768',True)
y=paragraph(1046,505,'• 仅向经授权的模型环境提供脱敏必要字段\n• 原始资料、候选草稿与已确认基线分开\n• 不开放写回、外部消息或执行接口\n• 读取失败、低把握或资料矛盾转人工',596,23,INK,39)
text(1046,698,'来源、规则与模型结果均需留痕 [f32 / f41 / f45]',20,MUTED)

label(756,1125,'否');label(505,1280,'是')
label(1050,932,'人工修订留痕后重新校验')
label(756,1594,'是');label(505,1750,'否')
label(1602,2093,'是');label(1040,2089,'否')
label(1030,862,'确认草案后回查版本与冲突')
label(870,2008,'补充条件',18);label(870,2035,'后再评审',18)
label(756,2334,'否');label(505,2488,'是')
label(930,3228,'退回补证或重算后，重新校验版本与冲突',20)

text(70,3290,'转型后改变什么  保留什么',31,bold=True)
cards=[
    (70,'系统自动处理  减少重复查表','system','01 / 03 / 06 / 15 / 17\n负责资料导入、版本核验、确定性冲突检测和留痕；计算结果仍须按流程复核。'),
    (635,'AI 辅助生成  减少阅读整理','ai','02 / 08 / 11\n生成字段草稿、解释与交接清单；不自行补造时间、判断最新版本或承诺容量。'),
    (1200,'人工审核办理  保留责任','human','05 / 09 / 12 / 14 / 16\n解决异常、确认口径和正式结论。未确认不放行；各业务阶段审批仍保留。'),
]
for x,title,kind,body in cards:
    fill,stroke,_=PALETTE[kind]
    d.rounded_rectangle((x,3345,x+530,3560),radius=12,fill=fill,outline=stroke,width=2)
    text(x+20,3365,title,23,bold=True)
    yy=paragraph(x+20,3412,body,490,21,INK,31);assert yy<=3557,(title,yy)

d.rounded_rectangle((70,3610,1730,3795),radius=12,fill='#F8FAFC',outline='#D9E4EC',width=2)
text(94,3630,'试点落地边界与效益验证',26,bold=True)
y=paragraph(94,3677,'首期仍为单批次、脱敏只读。15 只保存到独立试点库；16 由人员回到原系统办理，不自动锁档、签约或通知客户。\n预期减少查找、逐项比对和摘要整理，但不承诺节省比例或金额。以30样本回放、误报漏报、实际工时与确认完整率验证。',1605,23,INK,36)
assert y<=3788,y

text(70,3840,'读图与依据',27,bold=True)
notes=[
    '实线表示顺序或判断分支，虚线表示补证或调整后的回算。节点颜色区分系统、AI、人工；菱形判断不授予 AI 审批权。',
    '本图为基于访谈需求提出的目标方案，不代表能力已建成或收益已验证。AI 提取、解释与摘要均需另行评测。',
    'fNN = dlg-venue-schedule-fNN；来源 data/source/discovery-report.md。所有正式结论须授权确认，模型不得绕过人工。',
    '具体审批岗位、资源规则、敏感字段、留存期限、模型环境和接口条件待核实。日期：2026年10月23日。',
]
y=3884
for value in notes:y=paragraph(70,y,value,1660,21,MUTED,29)+8
assert y<H-10,y

paths=artifact_paths();paths['ai_flow_png'].parent.mkdir(parents=True,exist_ok=True)
im.save(paths['ai_flow_png'],dpi=(200,200))
(ROOT/'.research').mkdir(exist_ok=True)
im.resize((900,2025)).save(ROOT/'.research/ai_flow_preview.png')

kinds={'system':'系统自动处理','ai':'AI辅助生成','human':'人工审核或办理','decision':'判断闸门'}
rows=['| 节点 | 类型 | 动作或判断 | 处理边界 | 需求依据 |','| --- | --- | --- | --- | --- |']
for node in NODES:
    rows.append(f'| {node.id[1:]} | {kinds[node.kind]} | {node.title.replace(chr(10)," ")} | {node.detail} | {node.refs} |')
md='''# 展会排期与需求变更 AI 数智化转型后目标流程

目标设计日期：2026年10月23日。此图为建议方案，尚未实施，依据合成访谈整理，不代表企业真实运营现状或已验证能力。

![AI数智化转型后目标流程](../assets/展会排期与需求变更_AI数智化转型后流程图.png)

## 设计结论

将多源人工查找和逐项核对转为“脱敏导入、AI生成候选、系统版本校验、确定性规则检测、人工确认、可追溯交接”。AI减少阅读和草稿整理；规则系统负责可计算的校验；业务人员保留异常处置、正式审批与原系统执行责任。

图中17个节点包含13个操作和4个判断点。颜色区分系统自动处理、AI辅助生成和人工审核办理，菱形为判断闸门。虚线表示补证、调整或退回后的重新计算。

**目标能力不等于现有能力。** 原访谈支持的是需求与约束；AI提取、解释、摘要，以及自动关联、规则检测和版本控制均为拟建能力，需用真实授权样本验证。图中“需求依据”不表示访谈已经证明工具具备该能力。

## 节点与边界

'''+ '\n'.join(rows)+'''

## 与现状流程相比

| 现状痛点 | 转型后建议 | 仍需人工或系统约束 |
| --- | --- | --- |
| 表格、邮件、群消息反复查找和比对 | 01只读导入，02提取候选字段，03关联申请并校验版本 | 人工确认标识映射；保留来源定位；AI不能判断哪个版本正式生效 |
| 临时变更逐项核查，受影响资源难追踪 | 06以明确资源和时间规则计算冲突，08生成带来源的解释和备选草案 | 资源互斥、缓冲、容量和缺项规则须先确认；规则缺失转人工，不默认无冲突 |
| 跨团队口径和责任反复沟通 | 09集中处理澄清，10确认条件后回到03及06重新校验 | 业务负责人确认变更条件；确认草案不等于自动占档或对外承诺 |
| 交接只见“已处理”，缺项或版本遗漏 | 11生成结构化交接摘要，12由授权岗位审核，15保存确认链 | 已确认版本、未结项、接收岗位、截止时间和来源不得被摘要替代 |
| 访问和处理过程只留最终结论 | 17记录查询者、来源版本、AI建议、规则版本和确认人 | 留存期限、删除责任人和访问范围先确认；模型与规则更新也需人工审核 |

## AI 规则系统与人工的分工

AI在02、08、11生成候选字段、冲突解释、差异摘要与交接清单。每条候选应关联来源片段，空值、未确认值、歧义和模型低把握均应标记；不能自行补造时间、容量或客户承诺。模型置信度不能替代业务风险判断，也不能成为跳过审核的理由。

系统在03、06实施确定性校验。有效版本以业务认可的有效期及确认状态判断，同时记录业务发生时间和系统接收时间；不能只按文件名、接收时间或AI解释确定最新版本。冲突计算覆盖区域、装卸口、进撤场和恢复检查；规则未确定或容量未知转人工。

人工在05核实来源、纠正候选并留痕；09协调业务目标和变更条件；12复核本次业务结论；14处理退回、补证及重算；16回到原系统办理占档、合同和进撤场相关工作。正式变更由对应业务负责人确认，其他正式结论由授权人员确认，具体岗位与层级仍需核实。

## 三条返回路径与审批一致性

1. 04无法确认来源、字段或版本时进入05；补证后回到03重新校验。人工已确认的修订与AI候选分开，重跑不得覆盖人工修订。读取失败时保留上次快照和时间，等待人工核查，不伪装成最新数据。
2. 07发现冲突或待澄清项时进入08至10。条件未确认就留在09；经人工确认后仍回03及06复核，不允许直接进入已确认版本。
3. 13未获通过或审批期间基线改变时进入14。补证或重算后回03；如原始资料有变化，应先重新取得经授权的脱敏快照并按需重跑02。旧审批不能沿用于新版本。

15只能在13通过后保存本次复核结果。实施时需要在同一事务中检查基线版本并保存确认记录；任何并发版本变化都使本次审批失效并重新复核。这是目标设计约束，当前仓库不实现生产事务或业务审批服务。

## 数据隐私与执行边界

- 01导入前确认用途、范围与授权，按必要字段脱敏；AI仅在已授权的模型环境处理允许字段。不得默认把原始合同、邮件或联系人信息发送给外部模型。
- 原始记录、AI候选、人工修订与已确认基线分别保存；访问按角色和明确授权控制，跨企业资料不能自由共享。
- 15仅在独立试点库中保存已复核版本、来源与确认链，不写回原业务系统。16由业务人员在原系统办理；各阶段正式审批保留，不是一处审批放行全部合同与执行动作。
- 试点不开放写回、外部消息及执行接口，不自动锁档、签约、承诺容量或通知客户。
- 17记录来源、规则版本、模型与提示模板版本、候选、人工确认及访问事件。按项目确认的期限留存和删除，练习中的虚构个人字段按约清理；AI不自行决定删除范围或期限。

## 预期效益与验证

目标是减少重复查找、字段比对、影响解释和摘要整理，使人工集中在异常与决策。流程中的系统处理和AI调用也会增加建设、维护、推理与复核成本。本图不声称成本必然下降，不提供未经测试的节省比例或金额。

先在单批次30个经业务与质量岗位标注的样本上回放：统计已标注冲突召回、误报漏报、字段提取错误与遗漏、来源关联完整率、人工确认完整率、实际操作与等待时间，以及AI与维护投入。原报告45单/月和35分钟/单为虚构基线，不可作为实际ROI依据。

## 可编辑 Mermaid 源码

PNG与本节由同一节点及分支定义生成；不同编辑器的自动布局可能不同。

```mermaid
'''+mermaid()+'''
```

## 来源与待核实事项

依据 [discovery-report.md](../../data/source/discovery-report.md)；fNN表示dlg-venue-schedule-fNN。现状图见 [访谈流程图说明](展会排期与需求变更_访谈流程图说明.md)。

仍需核实真实数据授权、统一编号映射、资源及缓冲规则、审批岗位、模型环境、字段敏感级别、留存期限和接口条件。此图为转型后的目标工作方式，不修改原始访谈，也不证明任何业务流程已部署。
'''
paths['ai_flow_md'].write_text(md,encoding='utf-8')
print(paths['ai_flow_png'])
print(paths['ai_flow_md'])
