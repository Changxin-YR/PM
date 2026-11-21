"""Join frozen case names to actual pytest JUnit outcomes; unmatched rows are NOT_RUN."""
import argparse
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
    args = parser.parse_args()
    vectors = json.loads((ROOT / REFERENCE).read_text(encoding='utf-8'))
    wanted = {f'{group}/{item["case"]}': group for group, rows in vectors.items() if isinstance(rows, list) for item in rows}
    found = {}
    for node in ElementTree.parse(args.junit).iter('testcase'):
        if node.attrib['classname'] != 'tests.test_reference_vectors':
            continue
        test = node.attrib['name']
        match = re.fullmatch(r'(\w+)\[(.+)]', test)
        if not match or match[2] not in wanted or match[2] in found:
            raise ValueError(f'Unmatched or duplicate JUnit record: {test}')
        status = ('FAIL' if node.find('failure') is not None or node.find('error') is not None
                  else 'NOT_RUN' if node.find('skipped') is not None else 'PASS')
        found[match[2]] = (status, 'tests/test_reference_vectors.py::' + test)
    with args.output.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(('case', 'category', 'source', 'status', 'productTest'))
        for key, group in wanted.items():
            writer.writerow((key, group, REFERENCE, *found.get(key, ('NOT_RUN', ''))))
    print(f'{len(wanted)} reference cases: ' + ', '.join(f'{status} {sum(state == status for state, _ in found.values()) if status != "NOT_RUN" else sum(found.get(key, ("NOT_RUN",))[0] == status for key in wanted)}' for status in ('PASS', 'FAIL', 'NOT_RUN')))


if __name__ == '__main__':
    main()
