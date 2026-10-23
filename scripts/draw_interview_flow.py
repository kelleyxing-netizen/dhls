from pathlib import Path
import math
import re
from PIL import Image, ImageDraw, ImageFont
from dhls.utils.fonts import resolve_fonts

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'docs/assets/展会排期与需求变更_访谈流程图.png'
W,H=1800,3650
im=Image.new('RGB',(W,H),'white'); d=ImageDraw.Draw(im)
FONT,BOLD=resolve_fonts()
def f(n,b=False):return ImageFont.truetype(BOLD if b else FONT,n)
INK='#2B4050'; MUTED='#617F93'; LINE='#627F93'; BORDER='#7895AA'; FILL='#F3F7FA'
TAGS={
 '时':('费时','#FFF0D7','#9A5A04'),
 '钱':('潜在成本*','#FCE8E5','#A34437'),
 '人':('人工审核或核对','#EDEAF8','#67538A'),
 '隐':('隐私与授权','#E2F3EE','#2D7768')}
checks=[]
def text(x,y,s,size=24,color=INK,bold=False):
    d.text((x,y),s,font=f(size,bold),fill=color)
def center(x,y,s,size=26,color=INK,bold=False):
    box=d.textbbox((0,0),s,font=f(size,bold));text(x-(box[2]-box[0])/2,y,s,size,color,bold)
def wrapped(s,maxw,size=22):
    lines=[]
    for raw in s.split('\n'):
        line=''
        for c in re.findall(r'\[[^\]]+\]|[A-Za-z0-9_./-]+|.',raw):
            if d.textlength(line+c,font=f(size))>maxw and line:
                lines.append(line);line=c
            else:line+=c
        lines.append(line)
    return lines
def para(x,y,s,maxw,size=22,color=MUTED,lineh=34):
    ls=wrapped(s,maxw,size)
    for line in ls:text(x,y,line,size,color);y+=lineh
    return y
def pills(x,y,keys,size=18):
    for key in keys:
        label,bg,fg=TAGS[key];ww=int(d.textlength(label,font=f(size)))+28
        d.rounded_rectangle((x,y,x+ww,y+32),radius=8,fill=bg)
        text(x+14,y+4,label,size,fg);x+=ww+12
def arrow(points,dashed=False,color=LINE,width=3):
    if dashed:
        for (x1,y1),(x2,y2) in zip(points,points[1:]):
            length=math.hypot(x2-x1,y2-y1)
            for dist in range(0,int(length),18):
                end=min(dist+10,length)
                d.line((x1+(x2-x1)*dist/length,y1+(y2-y1)*dist/length,
                        x1+(x2-x1)*end/length,y1+(y2-y1)*end/length),fill=color,width=width)
    else:d.line(points,fill=color,width=width,joint='curve')
    (x0,y0),(x,y)=points[-2:];a=math.atan2(y-y0,x-x0)
    tip=[(x,y),(x-16*math.cos(a)+8*math.sin(a),y-16*math.sin(a)-8*math.cos(a)),
                (x-16*math.cos(a)-8*math.sin(a),y-16*math.sin(a)+8*math.cos(a))]
    d.polygon(tip,fill=color)
def label(x,y,s,size=21):
    box=d.textbbox((x,y),s,font=f(size))
    d.rectangle((box[0]-5,box[1]-3,box[2]+5,box[3]+3),fill='white')
    text(x,y,s,size,MUTED)
def node(num,x,y,w,h,title,sub,keys=''):
    d.rounded_rectangle((x,y,x+w,y+h),radius=13,fill=FILL,outline=BORDER,width=2)
    title_s=f'{num:02d}  {title}'
    center(x+w/2,y+15,title_s,28)
    ls=wrapped(sub,w-44,21)
    for i,line in enumerate(ls):center(x+w/2,y+59+i*29,line,21,MUTED)
    if keys:
        tagw=sum(int(d.textlength(TAGS[k][0],font=f(18)))+40 for k in keys)-12
        pills(x+(w-tagw)/2,y+h-43,keys)
    assert 59+(len(ls)-1)*29+24 <= (h-43 if keys else h-8), (num,ls,h)
    checks.append((num,x,y,x+w,y+h))
def diamond(num,cx,cy,title,sub,rx=230,ry=85):
    d.polygon([(cx,cy-ry),(cx+rx,cy),(cx,cy+ry),(cx-rx,cy)],fill='#FFF8E8',outline='#B6944F',width=3)
    ls=title.split('\n')
    for i,line in enumerate(ls):center(cx,cy-41+i*32,(f'{num:02d}  ' if i==0 else '')+line,25)
    center(cx,cy+34,sub,18,MUTED)
    checks.append((num,cx-rx,cy-ry,cx+rx,cy+ry))

# Title, scope and legend.
text(70,46,'展会排期与需求变更',50,bold=True)
text(70,120,'六人访谈整理的业务流程与风险位置',32)
text(70,174,'15 个节点 · 11 个操作 + 4 个判断 · 人工处理与交接',23,MUTED)
d.rounded_rectangle((70,230,1730,292),radius=9,fill='#F1F5F8',outline='#D9E4EC',width=2)
text(94,245,'主链：意向询期 → 占档确认 → 合同审核 → 进撤场计划；异常核查贯穿各交接点。',25)
pills(70,320,'时钱人隐',20)
text(945,323,'* 潜在成本为分析推断，未取得真实金额。',22,MUTED)

# Arrows first, leaving labels above the lines.
for a,b in [(560,620),(760,815),(985,1060),(1210,1265),(1435,1530),
            (1670,1760),(1900,1990),(2140,2195),(2365,2450)]:arrow([(480,a),(480,b)])
arrow([(710,900),(1020,900)])
arrow([(1345,830),(1345,730),(820,730)])
arrow([(710,1350),(1020,1350)])
arrow([(1345,1425),(1345,1480)])
arrow([(1585,1565),(1750,1565),(1750,650),(820,650)])
arrow([(1345,1650),(1345,1740)])
arrow([(1020,1810),(945,1810),(945,1565),(1105,1565)],True)
arrow([(710,2280),(1020,2280)])
arrow([(1345,2205),(1345,2070),(820,2070)],True)

node(1,140,430,680,130,'意向询期与需求登记',
     '核实区域、时间窗等关键字段 [f01 / f31]','隐')
node(2,140,620,680,140,'汇集台账 邮件与原始版本',
     '三类工具缺少统一关联编号；人工比对 [f10 / f30]','时钱隐')
diamond(3,480,900,'字段与有效版本\n能否确认','[f24 / f31 / f44]')
node(4,1020,830,650,140,'人工补证并逐项核对',
     '查群消息、原始版本与原行 [f11 / f13 / f33]','时人隐')
node(5,140,1060,680,150,'核对场地与进撤场资源时间',
     '装卸口重叠；客户延时与恢复检查需协调 [f11 / f50]','时钱')
diamond(6,480,1350,'是否存在冲突\n或跨团队口径分歧','[f11 / f50 / f53]')
node(7,1020,1275,650,150,'协商口径并提交需求变更',
     '一线提交变更；明确影响对象与责任 [f10 / f22 / f25]','时钱人')
diamond(8,1345,1565,'变更是否已获\n业务负责人正式确认','[f22 / f25]',240,85)
node(9,1020,1740,650,140,'挂未结项并等待人工确认',
     '不默认通过，不转嫁未确认责任 [f25 / f44]','时人')
node(10,140,1530,680,140,'占档确认',
     '正式结论须授权人员确认；不自动锁档 [f01 / f05 / f41]','人')
node(11,140,1760,680,140,'合同审核',
     '业务主链明确；审核岗位与层级待核实 [f01 / f41]','人隐')
node(12,140,1990,680,150,'进撤场计划与交接核对',
     '当班复核关键字段；双方核对状态与截止时间 [f14 / f53]','时钱人')
diamond(13,480,2280,'正式结论是否已获\n授权人员确认','[f14 / f41]')
node(14,1020,2205,650,150,'暂缓结论并继续人工核查',
     '版本或字段不清进入人工队列；必要时暂停 [f35 / f44]','时人隐')
node(15,140,2450,680,160,'资料交接并保存确认链',
     '列明最新版本、未结项、接收人与截止时间\n保存修订原因、确认人与访问记录 [f20 / f23 / f43 / f45]','人隐')

# A non-flow control note at upper right.
d.rounded_rectangle((1020,430,1670,564),radius=12,fill='#F6FAF8',outline='#CFE6DD',width=2)
text(1044,446,'横向要求  数据访问与用途控制',25,'#2D7768',True)
para(1044,490,'只读脱敏导出；跨企业资料按明确授权访问。\n适用于全链路，不是新增业务步骤。[f32 / f40 / f41]',600,21,'#567E73',29)

label(752,866,'否')
label(499,1008,'是')
label(1040,695,'补充信息后重新比对')
label(752,1317,'是')
label(499,1466,'否')
label(1597,1529,'是')
label(1370,1685,'否')
label(1005,609,'确认后回查版本与冲突')
label(872,1647,'补证后',18);label(872,1674,'再确认',18)
label(752,2248,'否')
label(499,2390,'是')
label(1020,2032,'补齐信息后重新复核')

# Three compact explanation cards.
text(70,2698,'怎样读出费时 成本 审核与隐私位置',31,bold=True)
cards=[
 (70,'费时  重点看 02 04 07 09 14','#9A5A04','#FFFBF4',
  '多源比对、逐项核查、协商与等待会反复发生。返工路径比单个顺序步骤更值得测时。[f10 / f13 / f15 / f44]'),
 (635,'成本  重点看 02 05 07 12','#A34437','#FFF8F6',
  '重复核对消耗人力；冲突协调、资源重排与等待可能增加投入。后者为推断，未核实采购、加班或损失金额。'),
 (1200,'隐私  贯穿 01 至 15','#2D7768','#F5FAF8',
  '采集与访问限授权用途；共享只读脱敏资料。保存原始版本与审计记录，按约删除虚构个人字段。[f32 / f41 / f43 / f45]')
]
for x,title,fg,bg,body in cards:
    d.rounded_rectangle((x,2752,x+530,2955),radius=11,fill=bg,outline='#DAE4EA',width=2)
    text(x+20,2770,title,23,fg,True)
    yy=para(x+20,2815,body,490,21,INK,31)
    assert yy<=2950,(title,yy)

text(70,3000,'六位访谈对象在流程中的信息与职责',31,bold=True)
roles=[
 ('顾明岚 · 业务负责人','目标、范围、试点决策与边界 [f00 / f04 / f05]'),
 ('林亦舟 · 一线服务专员','异常记录、追溯与当班复核 [f10–f15]'),
 ('唐映秋 · 项目流程协调员','交接、版本、未结项与责任清单 [f20–f25]'),
 ('秦芮 · 数字化工程师','数据关联、只读导出与快照 [f30–f35]'),
 ('傅景 · 审核与质量负责人','授权确认、兜底、审计与验收 [f40–f45]'),
 ('叶书 · 客户协同代表','双方口径、结果用途与反馈 [f50–f55]')
]
for i,(name,desc) in enumerate(roles):
    x=70+(i%3)*565;y=3057+(i//3)*120
    d.rounded_rectangle((x,y,x+530,y+102),radius=9,fill='white',outline='#D9E4EC',width=2)
    text(x+20,y+15,name,24)
    para(x+20,y+55,desc,495,20,MUTED,27)

text(70,3335,'读图与证据边界',27,bold=True)
notes=[
 '实线表示整理后的顺序或判断分支；虚线表示补充信息后的返回。菱形为人工判断点，并非系统自动审批。',
 '人工标签区分核对与正式审批：当班人员复核字段；变更由业务负责人确认；所有正式结论由授权人员确认。',
 '数据隐私包括个人信息保护；合同、档期等还涉及商业保密。具体敏感字段、留存期限与删除责任人尚未核实。',
 '本图依据合成演练访谈整理；人物与问题并非企业已核实事实。金额与真实工时均未知，45单/月、35分钟/单为虚构基线。',
 'fNN = dlg-venue-schedule-fNN；来源 discovery-report.md。主链据 f01 / f20，判断与回路为整理，具体顺序及岗位映射待核实。'
]
y=3385
for line in notes:
    y=para(70,y,line,1660,21,MUTED,30)+9
assert y<H-30,y
assert len(checks)==15
for num,x1,y1,x2,y2 in checks:assert 0<=x1<x2<=W and 0<=y1<y2<=H,num
im.save(OUT,dpi=(200,200))
im.resize((900,1825)).save(ROOT/'.research/flow_preview.png')
print(OUT)
