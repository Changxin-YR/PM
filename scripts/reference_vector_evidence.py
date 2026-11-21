"""Join frozen case names to actual pytest JUnit outcomes; unmatched rows are NOT_RUN."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = 'docs/spec/04_PMO_V1_规则测试向量.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('junit', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    vectors = json.loads((ROOT / REFERENCE).read_text(encoding='utf-8'))
    wanted = {f'{group}/{item["case"]}': group for group, rows in vectors.items() if isinstance(rows, list) for item in rows}
    found = {}
    for node in ElementTree.parse(args.junit).iter('testcase'):
        module = node.attrib['classname']
        if not re.fullmatch(r'tests\.test_reference_vectors(?:_\w+)?', module):
            continue
        test = node.attrib['name']
        match = re.fullmatch(r'(\w+)\[(.+)]', test)
        if not match or match[2] not in wanted or match[2] in found:
            raise ValueError(f'Unmatched or duplicate JUnit record: {test}')
        status = ('FAIL' if node.find('failure') is not None or node.find('error') is not None
                  else 'NOT_RUN' if node.find('skipped') is not None else 'PASS')
        found[match[2]] = (status, module.replace('.', '/') + '.py::' + test)
    if not found:
        raise ValueError('No frozen vector tests in the JUnit report')
    with args.output.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(('case', 'category', 'source', 'status', 'productTest'))
        for key, group in wanted.items():
            writer.writerow((key, group, REFERENCE, *found.get(key, ('NOT_RUN', ''))))
    counts = Counter(found.get(key, ('NOT_RUN',))[0] for key in wanted)
    print(f'{len(wanted)} reference cases: ' + ', '.join(f'{status} {counts[status]}' for status in ('PASS', 'FAIL', 'NOT_RUN')))
    if args.require_complete and counts['PASS'] != len(wanted):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
