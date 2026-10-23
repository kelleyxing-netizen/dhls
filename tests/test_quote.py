"""Verify the published quotation's financial reconciliation and identity."""
import json
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class QuoteOutputTests(unittest.TestCase):
    def test_exported_totals_reconcile_without_deposit_or_options(self):
        with ZipFile(ROOT / 'docs/reports/上海展会客户全流程报价单_参考版_20261023.xlsx') as z:
            xml = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            values = {c.get('r'): c.findtext(S + 'v') for c in xml.iter(S + 'c')}
            self.assertEqual(34252, float(values['B6']))
            self.assertEqual(59938, float(values['B7']))
            self.assertEqual(float(values['B6']) + float(values['B7']), float(values['B8']))
            self.assertEqual(10000, float(values['B9']))
            self.assertEqual(float(values['B8']) + float(values['B9']), float(values['B10']))
            self.assertEqual(float(values['B6']), sum(float(values[c]) for c in ['D16', 'D17', 'D18']))
            for name in z.namelist():
                if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'):
                    sheet = ET.fromstring(z.read(name))
                    self.assertFalse([c.get('r') for c in sheet.iter(S + 'c') if c.get('t') == 'e'], name)

    def test_word_and_workbook_share_scope_and_price_references(self):
        model = json.loads((ROOT / 'data/exhibition-quote.json').read_text(encoding='utf-8'))
        with ZipFile(ROOT / 'docs/reports/上海展会客户全流程报价单_参考版_20261023.docx') as z:
            xml = ET.fromstring(z.read('word/document.xml'))
            text = ''.join(t.text or '' for t in xml.iter(W + 't'))
            for row in model['items']:
                self.assertIn(row[0], text)
                self.assertIn(row[2], text)
            self.assertIn('94,190.00', text)
            self.assertIn('税费待', text)
            rels = z.read('word/_rels/document.xml.rels').decode()
            for source in model['sources']:
                if source['url']:
                    self.assertIn(source['url'].replace('&', '&amp;'), rels)
            for name in ['word/document.xml', 'word/styles.xml', 'word/header1.xml', 'word/footer1.xml']:
                for rf in ET.fromstring(z.read(name)).iter(W + 'rFonts'):
                    self.assertTrue(all(x == 'Microsoft YaHei' for x in rf.attrib.values()), name)


if __name__ == '__main__':
    unittest.main()
