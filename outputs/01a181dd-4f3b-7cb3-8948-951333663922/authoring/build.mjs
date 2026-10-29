import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { Workbook } from '@oai/artifact-tool';

const root = 'C:/OSPanel/home/bervel.local';
const authoring = path.dirname(fileURLToPath(import.meta.url));
const output = path.dirname(authoring);
const sourceName = 'Контур.Закупки_28.10.2026.xlsx';
const sourceHash = '71e8fb2a47dd47d3752499ba92f0dfc48451d8d665ee185563fdf0b27a48f91c';
const targetNumbers = ['0373200069626001183', '32616510553', '32616504131', '0302300048726001375', '2086016', '32616496933'];
const sourceScript = `
import os,json,hashlib
from pathlib import Path
from bervel.modules.tender_radar.infrastructure.importer import TenderImporter
h=${JSON.stringify(sourceHash)}
b=(Path(os.environ['BERVEL_OBJECT_ROOT'])/h[:2]/h).read_bytes()
assert hashlib.sha256(b).hexdigest()==h
data=TenderImporter().parse(b,'source.xlsx')
numbers=set(${JSON.stringify(targetNumbers)})
rows=[{k:r[k] for k in ('number','title','price','currency','deadline_at','stage','origin')} for r in data['records'] if r['number'] in numbers]
assert len(rows)==len(numbers)
print(json.dumps({'filename':${JSON.stringify(sourceName)},'sha256':h,'total':len(data['records']),'selected':rows},ensure_ascii=False))
`;
const getSource = () => JSON.parse(execFileSync('docker', ['exec', 'bervel-api-1', 'python', '-c', sourceScript], { encoding:'utf8', maxBuffer:2_000_000 }));
const source = getSource();
const selected = new Map(source.selected.map(row => [row.number,row]));
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
async function sourceFiles(directory) {
  const result=[];
  for (const entry of await fs.readdir(directory,{withFileTypes:true})) {
    if(entry.isDirectory() && entry.name !== '__pycache__') result.push(...await sourceFiles(path.join(directory,entry.name)));
    else if(entry.isFile() && entry.name.endsWith('.py')) result.push(path.join(directory,entry.name));
  }
  return result;
}
const protectedFiles = [...await sourceFiles(path.join(root,'src/bervel/modules/tender_radar')),
  path.join(root,'frontend/src/RadarPage.tsx'),path.join(root,'frontend/src/RadarReport.tsx'),path.join(root,'.git/index')];
const before = Object.fromEntries(await Promise.all(protectedFiles.map(async name => [name,digest(await fs.readFile(name))])));
const headBefore = execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();
const branchBefore = execFileSync('git',['branch','--show-current'],{cwd:root,encoding:'utf8'}).trim();

// Product groups are taken from the export. Missing dimensions/grades remain blank.
// Synthetic quantities and prices are never presented as tender requirements.
const common = [targetNumbers[0],targetNumbers[1],targetNumbers[2]];
const definitions = [
  ['CT2810-001','Болты','болт',common,1000000,40,0.5],
  ['CT2810-002','Гайки','га',common,1000000,16,0.2],
  ['CT2810-003','Шайбы','шайб',[targetNumbers[0],targetNumbers[2]],1000000,8,0.1],
  ['CT2810-004','Шурупы','шуруп',[targetNumbers[0]],1000000,12,0.2],
  ['CT2810-005','Винты','винт',[targetNumbers[0],targetNumbers[2],targetNumbers[4]],1000000,20,0.2],
  ['CT2810-006','Анкеры','анкер',[targetNumbers[0]],500000,80,1],
  ['CT2810-007','Болты для крепления грейдерных ножей','болт',[targetNumbers[3]],100000,120,1],
  ['CT2810-008','Гайки для крепления грейдерных ножей','гаек',[targetNumbers[3]],100000,32,0.5],
  ['CT2810-009','Шайбы для крепления грейдерных ножей','шайб',[targetNumbers[3]],100000,16,0.2],
  ['CT2810-010','Шайба зажимная для вагонов метрополитена','шайб',[targetNumbers[5]],100000,160,1],
];
const stockHeaders = ['sku','product','free_quantity','unit','standard','thread','length_mm','strength','coating','as_of','data_kind','source_reference','source_tender_numbers','source_product_quotes','specification_status','notes'];
const economyHeaders = ['sku','product','unit','currency','sale_price','unit_cost','fixed_cost','logistics_cost','packaging_per_unit','capacity_per_day','production_lead_days','delivery_lead_days','data_kind','source_reference','source_tender_numbers','assumption_note'];
const stockRows = [];
const economyRows = [];
const calibration = [];
const asOf = new Date().toISOString();
for (const [sku,product,stem,numbers,quantity,sale,packaging] of definitions) {
  const tenders=numbers.map(number=>selected.get(number));
  if(tenders.some(t=>!t || !t.title.toLocaleLowerCase('ru').includes(stem))) throw new Error(`Unsupported source product: ${sku}`);
  const references=tenders.map(t=>`Лист1!${t.origin.row}, закупка ${t.number}`).join('; ');
  const sourceReference=`${sourceName}: ${references}`;
  stockRows.push([sku,product,quantity,'шт','','','','','',asOf,'mock',sourceReference,numbers.join('; '),tenders.map(t=>t.title).join(' | '),
    'Из выгрузки подтверждается товарная группа. Стандарт, размеры, прочность и покрытие не раскрыты.',
    'МОК: свободный остаток условный. Количество потребности и точное техническое соответствие тендеру не установлены.']);
  economyRows.push([sku,product,'шт','RUB',sale,Number((sale/4-packaging).toFixed(2)),0,0,packaging,100000,0,1,'mock',sourceReference,numbers.join('; '),
    'МОК: цены, остатки, расходы, мощности и сроки условные. Наценка 300%, маржа 75% при положительном количестве. Полные удельные затраты равны sale_price/4. unit_cost включает распределённые постоянные расходы и логистику; упаковка учтена отдельно. fixed_cost и logistics_cost равны нулю во избежание повторного учёта. Единая условная налоговая база. НМЦ из выгрузки является бюджетом всего тендера, а не ценой единицы.']);
  const minBudget = Math.min(...tenders.map(t=>Number(t.price)));
  const testQuantity = Math.floor(minBudget*0.8/sale);
  if(testQuantity<=0 || testQuantity>quantity) throw new Error(`Calibration does not fit stock/budget: ${sku}`);
  calibration.push({sku,quantity:String(testQuantity),budget:String(minBudget),kind:'synthetic_arithmetic_example_not_tender_demand'});
}

const workbook = Workbook.create();
const sheets=[];
for(const [name,headers,rows] of [['Склад',stockHeaders,stockRows],['Экономика',economyHeaders,economyRows]]) {
  const sheet=workbook.worksheets.add(name);
  sheet.getRangeByIndexes(0,0,rows.length+1,headers.length).values=[headers,...rows];
  sheet.showGridLines=false;
  sheet.getRangeByIndexes(0,0,rows.length+1,headers.length).format.font={name:'Arial',size:10};
  sheet.getRangeByIndexes(0,0,1,headers.length).format={fill:'#243748',font:{name:'Arial',bold:true,color:'#FFFFFF'},rowHeight:25};
  sheet.getRangeByIndexes(1,0,rows.length,headers.length).format.rowHeight=24;
  sheet.getRange('A:A').format.columnWidth=18;
  sheet.getRange('B:B').format.columnWidth=54;
  sheet.getRange('C:H').format.columnWidth=15;
  sheet.freezePanes.freezeRows(1);
  sheets.push(sheet);
}
workbook.recalculate();
const inspection = await workbook.inspect({kind:'table',range:'Склад!A1:F5',include:'values',tableMaxRows:5,tableMaxCols:6,maxChars:2500});
await fs.writeFile(path.join(authoring,'inspection.txt'),inspection.ndjson,'utf8');
const errors=await workbook.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},maxChars:1500});
await fs.writeFile(path.join(authoring,'formula-errors.txt'),errors.ndjson,'utf8');
const quote = value => {
  const text=value===null || value===undefined ? '' : value instanceof Date ? value.toISOString() : String(value);
  return /[",\r\n]/.test(text) ? '"'+text.replaceAll('"','""')+'"' : text;
};
const texts=sheets.map((sheet,index)=>'\uFEFF'+sheet.getRangeByIndexes(0,0,definitions.length+1,index===0?stockHeaders.length:economyHeaders.length).values.map(row=>row.map(quote).join(',')).join('\r\n')+'\r\n');
const filenames=['Данные_по_складу_МОК_28.10.2026.csv','Экономика_и_производство_МОК_28.10.2026.csv'];
for(let index=0;index<texts.length;index++) await fs.writeFile(path.join(output,filenames[index]),texts[index],'utf8');

const verifier = `
import sys,json
from decimal import Decimal
from datetime import datetime,timezone
from bervel.modules.tender_radar.infrastructure.stock import StockImporter
from bervel.modules.tender_radar.infrastructure.economics import EconomicsImporter
from bervel.modules.tender_radar.stock import stock_check
from bervel.modules.tender_radar.economics import economics_check
x=json.load(sys.stdin)
s=StockImporter().parse(x['stock'].encode('utf-8'),'stock.csv')
e=EconomicsImporter().parse(x['economy'].encode('utf-8'),'economics.csv')
assert s['kind']==e['kind']=='mock'
assert {v['sku'] for v in s['items']}=={v['sku'] for v in e['items']}
assert len(s['items'])==len(e['items'])==10
checks=[]
for item,example in zip(s['items'],x['calibration']):
    assert item['sku']==example['sku']
    quantity=example['quantity']
    q=dict(sku=item['sku'],exact_match=True,quantity=quantity,unit=item['unit'],quote=item['product']+': '+quantity+' шт')
    stock=stock_check([q],True,s,Decimal('0'))
    assert stock['status']=='pass' and all(Decimal(v['shortage'])==0 for v in stock['checks'])
    # Explicit artificial arithmetic input. No real procurement is changed or claimed evaluated.
    row=dict(title='Условная проверка арифметики',description='поставка в течение 10 дней',price=example['budget'],currency='RUB')
    economy=economics_check(row,stock,e,Decimal('15'),datetime.now(timezone.utc))
    assert economy['status']=='pass',economy
    assert Decimal(economy['margin_percent'])==Decimal('75')
    assert Decimal(economy['profit'])/Decimal(economy['total_cost'])*100==Decimal('300')
    assert Decimal(economy['revenue'])<=Decimal(row['price'])
    checks.append(dict(sku=item['sku'],example_quantity=quantity,unit_markup_percent='300',margin_percent=economy['margin_percent'],stock_status=stock['status'],economics_status=economy['status'],basis='synthetic calibration, not actual tender requirements'))
print(json.dumps({'stock_count':len(s['items']),'economics_count':len(e['items']),'checks':checks,'live_radar_run':False,'real_tender_technical_match_confirmed':False},ensure_ascii=False))
`;
const validation=JSON.parse(execFileSync('docker',['exec','-i','bervel-api-1','python','-c',verifier],{
  input:JSON.stringify({stock:texts[0],economy:texts[1],calibration}),encoding:'utf8',maxBuffer:2_000_000
}));
for (let index=0;index<sheets.length;index++) {
  const preview=await workbook.render({sheetName:sheets[index].name,range:index===0?'A1:D11':'A1:F11',scale:1.5,format:'png'});
  await fs.writeFile(path.join(authoring,index===0?'stock-preview.png':'economics-preview.png'),new Uint8Array(await preview.arrayBuffer()));
}
const after=Object.fromEntries(await Promise.all(protectedFiles.map(async name=>[name,digest(await fs.readFile(name))])));
if(JSON.stringify(before)!==JSON.stringify(after)) throw new Error('Protected source/index changed during authoring');
if(execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim()!==headBefore) throw new Error('HEAD changed');
if(execFileSync('git',['branch','--show-current'],{cwd:root,encoding:'utf8'}).trim()!==branchBefore) throw new Error('Branch changed');
if(JSON.stringify(source)!==JSON.stringify(getSource())) throw new Error('Original source changed');
await fs.writeFile(path.join(authoring,'source-selection.json'),JSON.stringify(source,null,2),'utf8');
await fs.writeFile(path.join(authoring,'verification.json'),JSON.stringify({created_at:asOf,...validation,source_hash_unchanged:true,protected_files_unchanged:true,branch:branchBefore,head:headBefore,
  output:await Promise.all(filenames.map(async name=>({filename:name,sha256:digest(await fs.readFile(path.join(output,name)))}))),protected_sha256:before},null,2),'utf8');
console.log(JSON.stringify({output:filenames,stock_rows:stockRows.length,economics_rows:economyRows.length,source_tenders:source.selected.length,validation:'passed',real_tender_exact_match:false,radar_logic_unchanged:true}));
