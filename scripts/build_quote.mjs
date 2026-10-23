/** Build the editable customer quote using the bundled artifact-tool runtime.
 * Usage: node scripts/build_quote.mjs --modules ABS_BUNDLED_NODE_MODULES
 * Rebuild the matching Word snapshot with: python scripts/build_quote_docx.py
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const moduleDir=process.argv[process.argv.indexOf('--modules')+1];
if(!process.argv.includes('--modules') || !path.isAbsolute(moduleDir)) throw Error('Pass --modules with the bundled runtime node_modules directory');
const resolveRuntime=createRequire(path.join(moduleDir,'..','dhls-runtime-resolver.cjs'));
const {Workbook,SpreadsheetFile}=await import(pathToFileURL(resolveRuntime.resolve('@oai/artifact-tool')).href);
const model=JSON.parse(await fs.readFile(path.join(root,'data/exhibition-quote.json'),'utf8'));
const wb=Workbook.create();
const summary=wb.worksheets.add('报价总览'), fees=wb.worksheets.add('费用明细'), changes=wb.worksheets.add('变更与结算'), inputs=wb.worksheets.add('参数与依据');
const font='Microsoft YaHei', ink='#243B50', muted='#62768A', blue='#234A68', pale='#EDF3F8', yellow='#FFF1CE';
const money='#,##0.00;[Red](#,##0.00);0.00';
const val=(s,a,v)=>{s.getRange(a).values=[[v]];};
const fx=(s,a,f)=>{s.getRange(a).formulas=[[f]];};
function base(s,end,widths){
  const range=s.getRange(`A1:${String.fromCharCode(64+widths.length)}${end}`);
  range.format.font={name:font,size:11,color:ink}; range.format.verticalAlignment='center';
  range.format.rowHeight=26; s.showGridLines=false;
  widths.forEach((w,i)=>s.getRangeByIndexes(0,i,end,1).format.columnWidthPx=w);
}
function title(s,text){val(s,'A2',text);s.getRange('A2').format.font={name:font,size:16,bold:true,color:ink};s.getRange('A2').format.rowHeight=36;}
function head(s,address,values){s.getRange(address).values=[values];s.getRange(address).format={fill:blue,font:{name:font,size:11,bold:true,color:'#FFFFFF'},rowHeight:34,wrapText:true,horizontalAlignment:'center'};}
function input(s,range){s.getRange(range).format.fill=yellow;s.getRange(range).format.font={name:font,size:11,color:'#9B5300'};}
function warning(s,range){s.getRange(range).conditionalFormats.add('containsText',{text:'待',format:{fill:'#FFF0E6',font:{color:'#A4491A',bold:true}}});}

base(inputs,46,[210,235,28,800]);title(inputs,'报价参数与价格依据');inputs.tabColor='#6385A0';
head(inputs,'A4:B4',['可修改参数','示例值或待填写']);
const params=[['城市',model.city],['展会名称及展位号','待确认'],['展位面积 ㎡',model.area],['展览天数',model.show_days],['进场天数',model.setup_days],['撤场天数',model.teardown_days],['展台形式','单层模块化 光地自建'],['报价版本',model.version],['币种',model.currency],['税费口径','未核实 定稿前逐项确认'],['报价有效期至','待双方约定'],['客户授权代表','待填写'],['报价方负责人','待填写'],['可退清场押金参考',model.deposit],['付款比例 首期',model.payment_rates[0]],['付款比例 进场前',model.payment_rates[1]],['付款比例 验收尾款',model.payment_rates[2]]];
inputs.getRange('A5:B21').values=params;input(inputs,'B5:B21');inputs.getRange('B11:B21').format.wrapText=true;inputs.getRange('B11:B21').format.rowHeight=38;inputs.getRange('B19:B21').setNumberFormat('0%');inputs.getRange('B18').setNumberFormat(money);
val(inputs,'D5','仅上海为用户已确认信息；36㎡、展览3天、进场1天、撤场1天均为预算示例。');
val(inputs,'D7','面积联动光地、框架、地毯、管理费及清运；天数只联动按天计费项目。');
val(inputs,'D9','进撤场天数用于排期记录，固定项不自动按天加价；超出包干范围走变更审批。');
val(inputs,'D11','切换标摊、双层、超高、重型展品时须重新核定整套范围；不能只改形式名称。');
val(inputs,'D14',model.tax_note);inputs.getRange('D14').format.wrapText=true;inputs.getRange('D14').format.rowHeight=58;
val(inputs,'D18','押金仅作客户直付场馆的资金占用参考，独立于服务费；实际额度及退款条件待确认。');
val(inputs,'D20','40% / 50% / 10%为建议，可修改；不套用于客户直付项目，第三方按其订单付款。');
val(inputs,'A24','价格依据 核查日期 2026年10月23日');inputs.getRange('A24').format.font={name:font,size:13,bold:true,color:ink};
model.sources.forEach((s,i)=>{const r=27+i*5;val(inputs,`A${r}`,s.id);val(inputs,`B${r}`,s.title);val(inputs,`D${r}`,s.url||'内部预算假设 无供应商实盘');val(inputs,`D${r+1}`,s.note);inputs.getRange(`B${r}`).format.wrapText=true;inputs.getRange(`A${r}:D${r+1}`).format.rowHeight=50;inputs.getRange(`D${r}:D${r+1}`).format.wrapText=true;inputs.getRange(`A${r}:B${r}`).format.fill=pale;});

base(fees,53,[52,100,258,92,82,82,112,116,140,140,315]);title(fees,'全流程费用明细');
val(fees,'A4','黄色为可改输入；金额 = 数量 × 次数或天数 × 单价；按展期计价的项目不再乘展览天数。');
val(fees,'A5','参考预算 税费待逐项确认｜本方收款 / 客户直付 / 不选；不选项目保留单价，但不进入已选合计。');
head(fees,'A7:K7',['编号','环节','费用项目','单位','数量','次数/天','参考单价 元','收款或选用','金额 元','依据编号','交付和计费边界']);
const first=8,last=first+model.items.length-1;
model.items.forEach((row,i)=>{
  const r=first+i;const [id,phase,name,unit,q,n,price,select,source,boundary]=row;
  fees.getRange(`A${r}:K${r}`).values=[[id,phase,name,unit,typeof q==='number'?q:null,typeof n==='number'?n:null,price,select,null,source,boundary]];
  if(q==='area')fx(fees,`E${r}`,"='参数与依据'!$B$7");
  if(n==='show_days')fx(fees,`F${r}`,"='参数与依据'!$B$8");
  fx(fees,`I${r}`,`=IF(H${r}="不选","",IF(OR(H${r}="本方收款",H${r}="客户直付"),IF(COUNTBLANK(E${r}:G${r})>0,"待询价",IF(AND(ISNUMBER(E${r}),ISNUMBER(F${r}),ISNUMBER(G${r})),IF(OR(E${r}<0,F${r}<0,G${r}<0),"待核输入",ROUND(E${r}*F${r}*G${r},2)),"待询价")),"待选收款"))`);
  fees.getRange(`A${r}:K${r}`).format.rowHeight=64;fees.getRange(`B${r}:D${r}`).format.wrapText=true;fees.getRange(`J${r}:K${r}`).format.wrapText=true;
  if(i%2===1)fees.getRange(`A${r}:K${r}`).format.fill='#F5F8FB';
  input(fees,`G${r}:H${r}`);if(typeof q==='number')input(fees,`E${r}`);if(typeof n==='number')input(fees,`F${r}`);
});
fees.getRange(`E${first}:G${last}`).setNumberFormat('#,##0.00');fees.getRange(`I${first}:I${last}`).setNumberFormat(money);
fees.getRange(`H${first}:H${last}`).dataValidation={rule:{type:'list',values:['本方收款','客户直付','不选']}};
warning(fees,`I${first}:I${last}`);fees.freezePanes.freezeRows(7);fees.freezePanes.freezeColumns(3);
function pending(type){return ['待询价','待核输入','待选收款'].map(t=>`COUNTIFS(${type?`H${first}:H${last},"${type}",`:''}I${first}:I${last},"${t}")`).join('+');}
function totalRow(r,label,type){val(fees,`C${r}`,label);fx(fees,`I${r}`,`=IF(${pending(type)}>0,"待补询价",SUMIFS(I${first}:I${last},H${first}:H${last},"${type}"))`);}
totalRow(45,'本方收款预算合计','本方收款');totalRow(46,'客户直付预算合计','客户直付');
val(fees,'C47','已填写价格的已选金额');fx(fees,'I47',`=SUM(I${first}:I${last})`);
val(fees,'C48','已选待处理项目数');fx(fees,'I48',`=${pending()}`);
val(fees,'C49','已选全口径预算 税费待核');fx(fees,'I49','=IF(I48>0,"待补询价",SUM(I45:I46))');
fees.getRange('C45:K49').format.rowHeight=32;fees.getRange('I45:I49').setNumberFormat(money);fees.getRange('I48').setNumberFormat('0');fees.getRange('C49:K49').format.fill=pale;fees.getRange('C49:K49').format.font={name:font,size:11,bold:true,color:ink};warning(fees,'I45:I49');
val(fees,'C52','清场押金不在本表费用合计内；来源编号与限制条件详见 参数与依据。');

base(changes,35,[185,185,320,150,220,140,190,285,150]);title(changes,'变更审批与最终结算');
val(changes,'A4','只将本方服务费用差额填入本表；客户直付变化更新费用明细并保存新版本，避免同时计入。');
val(changes,'A5','空白行是待填模板；只有状态为已批准的费用变更进入修订预算，审批人及证据须由人工核验。');
head(changes,'A6:I6',['变更编号','原报价版本','变更原因及内容','本方差额 元','排期影响','审批状态','客户及本方确认人','确认日期及依据位置','计入预算差额']);
changes.getRange('A7:H14').format.rowHeight=42;input(changes,'A7:H14');changes.getRange('D7:D14').setNumberFormat(money);changes.getRange('F7:F14').dataValidation={rule:{type:'list',values:['待审批','已批准','已驳回']}};
for(let r=7;r<=14;r++)fx(changes,`I${r}`,`=IF(F${r}="已批准",IF(COUNTBLANK(D${r})>0,"待补金额",IF(ISNUMBER(D${r}),D${r},"待补金额")),0)`);
changes.getRange('I7:I14').setNumberFormat(money);warning(changes,'I7:I14');
val(changes,'A17','已批准变更差额');fx(changes,'B17','=IF(COUNTIFS(I7:I14,"待补金额")>0,"待补金额",SUM(I7:I14))');
val(changes,'A18','修订本方服务预算');fx(changes,'B18','=IF(AND(ISNUMBER(B17),ISNUMBER(\'费用明细\'!I45)),ROUND(\'费用明细\'!I45+B17,2),"待补询价")');
val(changes,'A19','最终确认本方服务金额');val(changes,'A20','本方服务款累计已付');val(changes,'A21','本方尾款或应退金额');fx(changes,'B21','=IF(COUNTBLANK(B19:B20)>0,"待填结算及已付",IF(AND(ISNUMBER(B19),ISNUMBER(B20)),ROUND(B19-B20,2),"待核结算输入"))');
val(changes,'D19','以双方签字结算为准，不能直接把预算当实际结算。');
val(changes,'D20','仅填支付给本方的服务款；尚未付款时可明确填写0。');
val(changes,'D21','正值为应付，负值为应退；不混入场馆押金与客户直付。');
val(changes,'A24','清场押金预算参考');fx(changes,'B24',"='参数与依据'!B18");
val(changes,'A25','场馆押金实际缴纳');val(changes,'A26','场馆押金已退金额');val(changes,'A27','场馆确认押金扣款');val(changes,'A28','场馆押金待退余额');fx(changes,'B28','=IF(COUNT(B25:B27)=3,ROUND(B25-B26-B27,2),"待填收退扣款")');
val(changes,'D25','押金假设客户直接缴给场馆，本方服务款不承担代收代退。');
val(changes,'D27','填有凭据的场馆扣款；不得再把同一笔扣款重复加进本方结算。');
val(changes,'D28','退款到账时间以场馆确认凭据为准；不填写虚构退款日期。');
input(changes,'B19:B20');input(changes,'B25:B27');changes.getRange('B17:B28').setNumberFormat(money);warning(changes,'B17:B28');changes.getRange('A17:B28').format.rowHeight=34;
val(changes,'A31','结算确认 客户授权人');val(changes,'A32','结算确认 报价方负责人');input(changes,'B31:C32');
val(changes,'D31','隐私：证件、手机号、开票税号与银行资料另行受控传递，不填入公开仓库中的示例文件。');
val(changes,'D32','留存签字报价、变更版本和验收凭据；保留期限与访问人员由双方约定。');
changes.freezePanes.freezeRows(6);

base(summary,42,[260,220,28,300,355]);title(summary,model.title);summary.tabColor=blue;
val(summary,'A3',`${model.number}  |  ${model.date}`);val(summary,'D3','预算示例 待审核后对外发送');summary.getRange('D3').format.font={name:font,size:11,bold:true,color:'#A4491A'};
const labels=['本方收款预算 元','客户直付预算 元','已选全流程预算 元','可退押金参考 元','安排资金参考 元'];
labels.forEach((x,i)=>val(summary,`A${6+i}`,x));
fx(summary,'B6',"='费用明细'!I45");fx(summary,'B7',"='费用明细'!I46");fx(summary,'B8',"='费用明细'!I49");fx(summary,'B9',"='参数与依据'!B18");fx(summary,'B10','=IF(AND(ISNUMBER(B8),ISNUMBER(B9)),SUM(B8:B9),"待补询价")');
summary.getRange('B6:B10').setNumberFormat(money);summary.getRange('A8:B8').format.fill=pale;summary.getRange('A8:B8').format.font={name:font,size:13,bold:true,color:ink};summary.getRange('A6:B10').format.rowHeight=36;warning(summary,'B6:B10');
[['客户单位','待填写'],['报价单位','待填写'],['展会及展位号','待确认'],['场馆及具体档期','待确认'],['税费及开票条件','待逐项确认']].forEach((r,i)=>{val(summary,`D${5+i}`,r[0]);val(summary,`E${5+i}`,r[1]);input(summary,`E${5+i}`);});
val(summary,'A12','示例范围 上海 36㎡ 单层模块化 展览3天 进场1天 撤场1天');
val(summary,'D12','更改面积和天数请到 参数与依据；更改本行文字不改变计算。');
head(summary,'A15:E15',['本方服务款建议节点','建议比例','', '计划金额 元','付款触发条件']);
['首期款','进场前款','验收尾款'].forEach((x,i)=>{const r=16+i;val(summary,`A${r}`,x);fx(summary,`B${r}`,`='参数与依据'!B${19+i}`);const guard=`AND(ISNUMBER('变更与结算'!B18),COUNTBLANK('参数与依据'!B19:B21)=0,MIN(B16:B18)>=0,MAX(B16:B18)<=1,ROUND(SUM(B16:B18),6)=1)`;fx(summary,`D${r}`,i<2?`=IF(${guard},ROUND('变更与结算'!B18*B${r},2),"待核比例或金额")`:`=IF(${guard},'变更与结算'!B18-SUM(D16:D17),"待核比例或金额")`);});
val(summary,'E16','签字确认报价及图纸后；比例为建议');val(summary,'E17','进场前双方约定日期；先核验报馆条件');val(summary,'E18','完成验收后；最终尾款按签字结算重算');summary.getRange('B16:B18').setNumberFormat('0%');summary.getRange('D16:D18').setNumberFormat(money);summary.getRange('A16:E18').format.rowHeight=38;summary.getRange('E16:E18').format.wrapText=true;warning(summary,'D16:D18');
val(summary,'A20','流程交付与人工确认');summary.getRange('A20').format.font={name:font,size:13,bold:true,color:ink};
head(summary,'A22:E22',['节点','主要交付','','人工确认角色','验收及依据']);
model.milestones.forEach((row,i)=>{const r=23+i;val(summary,`A${r}`,row[0]);val(summary,`B${r}`,row[1]);val(summary,`D${r}`,row[2]);val(summary,`E${r}`,row[3]);summary.getRange(`A${r}:E${r}`).format.rowHeight=80;summary.getRange(`A${r}:E${r}`).format.wrapText=true;});
val(summary,'A31','报价说明');val(summary,'A33','正式询价前确认');val(summary,'B33','展会及场馆、面积及开口、展期、展品重量、设备功率、最终图纸。');
val(summary,'A35','价格与付款口径');val(summary,'B35','本方款、客户直付款分开；不选项目和可退押金不计入服务费用。');
val(summary,'A37','有效期与变更');val(summary,'B37','有效期及取消条款待双方约定；范围变更先审批、再执行。');
val(summary,'D33','建议价含公开方案锚点与内部估算；来源和适用条件在 参数与依据。');
val(summary,'D35','当前税费未核实，不能作为最终含税合同价；审批及签字必须由授权人员完成。');
val(summary,'D37','本文件不代表已锁档、报馆通过或已签约；Excel修改后Word需同步重制。');
summary.getRange('B33:B37').format.wrapText=true;summary.getRange('D33:E37').format.wrapText=true;summary.getRange('A33:E37').format.rowHeight=42;
fx(summary,'A12',`="示例范围 "&'参数与依据'!B5&" "&'参数与依据'!B7&"㎡ "&'参数与依据'!B11&" 展览"&'参数与依据'!B8&"天 进场"&'参数与依据'!B9&"天 撤场"&'参数与依据'!B10&"天"`);
fx(summary,'E7',"='参数与依据'!B6");fx(summary,'E9',"='参数与依据'!B14");summary.getRange('E7').format.fill='#FFFFFF';summary.getRange('E9').format.fill='#FFFFFF';

// Meaningful boundary checks: scaling, zero vs missing price, approval, refunds,
// and payment proportions. All edits are restored before final export.
const amount=(r)=>Number(fees.getRange(`I${r}`).values[0][0]);
const expectedSelf=model.items.filter(x=>x[7]==='本方收款').reduce((s,x)=>s+(x[4]==='area'?model.area:x[4])*(x[5]==='show_days'?model.show_days:x[5])*x[6],0);
const expectedDirect=model.items.filter(x=>x[7]==='客户直付').reduce((s,x)=>s+(x[4]==='area'?model.area:x[4])*(x[5]==='show_days'?model.show_days:x[5])*x[6],0);
wb.recalculate();assert.equal(summary.getRange('B6').values[0][0],expectedSelf);assert.equal(summary.getRange('B7').values[0][0],expectedDirect);
val(inputs,'B7',72);val(inputs,'B8',4);wb.recalculate();assert.equal(amount(11),108000);assert.equal(amount(30),1200);val(inputs,'B7',model.area);val(inputs,'B8',model.show_days);
const unknown=first+31;val(fees,`H${unknown}`,'本方收款');wb.recalculate();assert.equal(summary.getRange('B6').values[0][0],'待补询价');val(fees,`G${unknown}`,0);wb.recalculate();assert.equal(amount(unknown),0);val(fees,`H${unknown}`,'不选');val(fees,`G${unknown}`,null);
val(changes,'D7',300);val(changes,'F7','待审批');wb.recalculate();assert.equal(changes.getRange('B17').values[0][0],0);val(changes,'F7','已批准');wb.recalculate();assert.equal(changes.getRange('B18').values[0][0],expectedSelf+300);val(changes,'D7',null);wb.recalculate();assert.equal(changes.getRange('B17').values[0][0],'待补金额');val(changes,'F7',null);
val(changes,'B19',expectedSelf);val(changes,'B20',expectedSelf+100);val(changes,'B25',10000);val(changes,'B26',9000);val(changes,'B27',500);wb.recalculate();assert.equal(changes.getRange('B21').values[0][0],-100);assert.equal(changes.getRange('B28').values[0][0],500);['B19','B20','B25','B26','B27'].forEach(a=>val(changes,a,null));
val(inputs,'B19',0.5);wb.recalculate();assert.equal(summary.getRange('D16').values[0][0],'待核比例或金额');val(inputs,'B19',model.payment_rates[0]);wb.recalculate();
assert.equal(summary.getRange('D16').values[0][0]+summary.getRange('D17').values[0][0]+summary.getRange('D18').values[0][0],expectedSelf);
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:100},summary:'Final formula error scan'});
console.log(errors.ndjson);console.log(JSON.stringify({self:expectedSelf,direct:expectedDirect,total:expectedSelf+expectedDirect,deposit:model.deposit,checks:'passed'}));
await fs.mkdir(path.join(root,'.research/quote'),{recursive:true});
await fs.writeFile(path.join(root,'.research/quote/calculated.json'),JSON.stringify({self:expectedSelf,direct:expectedDirect,total:expectedSelf+expectedDirect,deposit:model.deposit},null,2));
const out=path.join(root,'docs/reports/上海展会客户全流程报价单_参考版_20261023.xlsx');await (await SpreadsheetFile.exportXlsx(wb)).save(out);
// Artifact-tool emits a sidecar inspection file; keep it in the ignored QA area.
try{await fs.rename(out+'.inspect.ndjson',path.join(root,'.research/quote/export.inspect.ndjson'));}catch(e){if(e.code!=='ENOENT')throw e;}
for(const [s,range,file] of [[summary,'A1:E38','summary'],[fees,'A1:K22','fees-top'],[fees,'A23:K49','fees-bottom'],[changes,'A1:I33','changes'],[inputs,'A1:D23','inputs'],[inputs,'A24:D44','sources']]){
 const pic=await wb.render({sheetName:s.name,range,scale:1.3,format:'png'});await fs.writeFile(path.join(root,`.research/quote/${file}.png`),new Uint8Array(await pic.arrayBuffer()));
}
console.log('XLSX exported and all sheets rendered.');
