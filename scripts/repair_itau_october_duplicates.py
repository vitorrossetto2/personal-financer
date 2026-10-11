"""Repair verified open/paid export duplicates; dry-run unless --apply."""
import argparse
import collections
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repositories.card_identity import equivalent_description

parser = argparse.ArgumentParser()
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
db = sqlite3.connect(root / 'instance/financeiro.db')
db.row_factory = sqlite3.Row
rows = list(db.execute("SELECT * FROM transactions WHERE deleted_at IS NULL AND credit_card_id=1"))
old = [r for r in rows if r['created_at'] < '2026-10-11']
new = [r for r in rows if r['created_at'] == '2026-10-11T17:16:55']
fields = ['due_date', 'purchase_date', 'amount', 'installment_number', 'installment_total', 'cardholder_name', 'card_last_four']
pairs = []
used = set()
for row in old:
    matches = [r for r in new if r['id'] not in used and all(row[k] == r[k] for k in fields)
               and equivalent_description(row['description'], r['description'])]
    if len(matches) == 1:
        pairs.append((row, matches[0]))
        used.add(matches[0]['id'])
report = {'pairs': [{'removed': dict(a), 'kept': dict(b)} for a, b in pairs],
          'by_month': dict(collections.Counter(a['due_date'][:7] for a, b in pairs))}
print(json.dumps({'duplicate_count': len(pairs), 'by_month': report['by_month']}))
if args.apply:
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    folder = root / 'instance/backups'
    folder.mkdir(exist_ok=True)
    backup_path = folder / f'before-itau-cleanup-{stamp}.db'
    with sqlite3.connect(backup_path) as backup:
        db.backup(backup)
    report_path = folder / f'itau-cleanup-{stamp}.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with db:
        for removed, kept in pairs:
            if removed['status'] == 'settled' and kept['status'] != 'settled':
                db.execute("UPDATE transactions SET status='settled', settled_date=? WHERE id=?",
                           (removed['settled_date'], kept['id']))
            if removed['category'] != kept['category']:
                db.execute('UPDATE transactions SET category=? WHERE id=?', (removed['category'], kept['id']))
            db.execute('UPDATE transactions SET deleted_at=? WHERE id=? AND deleted_at IS NULL',
                       (datetime.now().isoformat(timespec='seconds'), removed['id']))
    print('backup:', backup_path)
    print('report:', report_path)
    print('october:', tuple(db.execute("SELECT count(*),round(sum(amount),2) FROM transactions WHERE deleted_at IS NULL AND credit_card_id=1 AND due_date LIKE '2026-10-%'").fetchone()))
db.close()
