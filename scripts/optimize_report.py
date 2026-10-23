from pathlib import Path
from collections import Counter
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree
from docx import Document
from docx.shared import Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / '.research/work/report_base.docx'
STEM = '展会排期与需求变更项目调研报告_20261023_优化版'
OUT = ROOT / 'docs/reports' / (STEM + '.docx')
MD = ROOT / 'docs/reports' / (STEM + '.md')
FONT = 'Microsoft YaHei'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = {'w': W, 'r': R}
doc = Document(SOURCE)

def font_props(rpr):
    fs = rpr.find(qn('w:rFonts'))
    if fs is None:
        fs = OxmlElement('w:rFonts'); rpr.insert(0, fs)
    fs.attrib.clear()
    for key in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
        fs.set(qn('w:' + key), FONT)
    lang = rpr.find(qn('w:lang'))
    if lang is None:
        lang = OxmlElement('w:lang'); rpr.append(lang)
    for key in ('val','eastAsia','bidi'):
        lang.set(qn('w:' + key), 'zh-CN')

for style in doc.styles:
    font_props(style.element.get_or_add_rPr())
    if style.type == 1:
        style.font.italic = False
        style.paragraph_format.widow_control = True

for name,size in [('Normal',10.5),('Title',25),('Subtitle',10.5),
                  ('Heading 1',18),('Heading 2',12),('Heading 3',11),
                  ('Header',8),('Footer',8)]:
    style = doc.styles[name]
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor(0,0,0)
    style.font.italic = False
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.12
for name in ['Title','Heading 1','Heading 2','Heading 3']:
    doc.styles[name].font.bold = True
    doc.styles[name].paragraph_format.keep_with_next = True
doc.styles['Subtitle'].font.bold = False
doc.styles['Heading 1'].paragraph_format.space_after = Pt(14)
doc.styles['Heading 2'].paragraph_format.space_before = Pt(10)

for par in doc.paragraphs:
    if 'V1.0' in par.text:
        for run in par.runs:
            run.text = run.text.replace('V1.0','V1.1')
for table in doc.tables:
    for row in table.rows:
        for cell in row.cells:
            for par in cell.paragraphs:
                for run in par.runs:
                    run.text = run.text.replace('本项目必须补齐','本项目需验证或适配')
for par in doc.paragraphs:
    for run in par.runs:
        run.text = run.text.replace('“必须补齐”指本轮证据未证明其原生满足本项目要求，不能直接理解为该项目绝对没有该功能。',
                                    '“需验证或适配”指本轮证据未证明其原生满足本项目要求，功能范围应结合选定版本确认。')

# Normalize every run, including hyperlinks and field results in all story parts.
for part in doc.part.package.parts:
    element = getattr(part, 'element', None)
    if element is None:
        continue
    for run in element.iter(qn('w:r')):
        rp = run.find(qn('w:rPr'))
        if rp is None:
            rp = OxmlElement('w:rPr'); run.insert(0,rp)
        font_props(rp)
        for tag in ('i','iCs'):
            for child in list(rp.findall(qn('w:'+tag))): rp.remove(child)
            flag = OxmlElement('w:'+tag); flag.set(qn('w:val'),'0'); rp.append(flag)
    for fs in list(element.iter(qn('w:rFonts'))):
        fs.attrib.clear()
        for key in ('ascii','hAnsi','eastAsia','cs'): fs.set(qn('w:'+key), FONT)

doc.core_properties.version = '1.1'
doc.core_properties.comments = '统一中英文字体和样式 同步保留Markdown版本'
doc.save(OUT)

# Update theme fonts as well, so subsequently added paragraphs stay consistent.
with ZipFile(OUT) as zin:
    members = {item.filename: (item, zin.read(item.filename)) for item in zin.infolist()}
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
for name,(info,data) in list(members.items()):
    if name.startswith('word/theme/') and name.endswith('.xml'):
        el = etree.fromstring(data)
        for group in el.findall('.//{%s}majorFont'%A)+el.findall('.//{%s}minorFont'%A):
            for font in group:
                if etree.QName(font).localname in ('latin','ea','cs','font'):
                    font.set('typeface',FONT)
        members[name]=(info,etree.tostring(el,xml_declaration=True,encoding='UTF-8',standalone=True))
with ZipFile(OUT,'w',ZIP_DEFLATED) as zout:
    for info,data in members.values(): zout.writestr(info,data)

# Produce portable Markdown from the final DOCX body, retaining block order,
# tables, source labels and actual hyperlink destinations. No headers/footers.
final = Document(OUT)
rels = final.part.rels
def inline(el):
    out=[]
    for child in el:
        tag=etree.QName(child).localname
        if tag=='r':
            for token in child:
                k=etree.QName(token).localname
                if k=='t': out.append(token.text or '')
                elif k in ('br','cr'): out.append('<br>')
                elif k=='tab': out.append(' ')
        elif tag=='hyperlink':
            label=inline(child)
            target=rels[child.get(qn('r:id'))].target_ref
            out.append('['+label.replace('[',r'\[').replace(']',r'\]')+']('+target+')')
    return ''.join(out)

def cell_text(cell):
    vals=[inline(par) for par in cell.findall(qn('w:p'))]
    return '<br>'.join(vals).replace('|',r'\|')

blocks=[]
for block in final.element.body:
    tag=etree.QName(block).localname
    if tag=='p':
        text=inline(block).strip()
        if not text: continue
        pp=block.find(qn('w:pPr'))
        sty=pp.find(qn('w:pStyle')) if pp is not None else None
        sid=sty.get(qn('w:val')) if sty is not None else ''
        if sid=='Title': blocks.append('# '+text.replace('<br>',' '))
        elif sid.startswith('Heading'):
            level=int(sid[-1])+1
            blocks.append('#'*level+' '+text)
        else: blocks.append(text)
    elif tag=='tbl':
        rows=[]
        for row in block.findall(qn('w:tr')):
            rows.append([cell_text(c) for c in row.findall(qn('w:tc'))])
        lines=['| '+' | '.join(row)+' |' for row in rows]
        lines.insert(1,'| '+' | '.join('---' for _ in rows[0])+' |')
        blocks.append('\n'.join(lines))
# Put the descriptive title first in Markdown.
title_index=next(i for i,b in enumerate(blocks) if b.startswith('# '))
title=blocks.pop(title_index)
blocks.insert(0,title)
for i,b in enumerate(blocks):
    if b.startswith('## 一 '): break
    if b.startswith('### '): blocks[i]=b[1:]
MD.write_text('\n\n'.join(blocks)+'\n',encoding='utf-8')

# Check complete content parity at the paragraph/table-cell level.
text_nodes=final.element.body.findall('.//w:t',NS)
missing=[n.text for n in text_nodes if n.text and n.text.strip()
         and n.text.replace('|',r'\|').replace('[',r'\[').replace(']',r'\]') not in MD.read_text(encoding='utf-8')
         and n.text not in MD.read_text(encoding='utf-8')]
fonts=Counter()
with ZipFile(OUT) as z:
    for name in ['word/document.xml','word/styles.xml','word/header1.xml','word/footer1.xml']:
        el=etree.fromstring(z.read(name))
        for fs in el.findall('.//w:rFonts',NS):
            for key,value in fs.attrib.items(): fonts[(etree.QName(key).localname,value)]+=1
print('DOCX',OUT)
print('MARKDOWN',MD)
print('FONT_AUDIT',dict(fonts))
print('MISSING_TEXT',missing)
print('TABLES',len(final.tables),'HYPERLINKS',len(final.element.body.findall('.//w:hyperlink',NS)))
assert not missing
assert all(v==FONT for (_,v) in fonts)
