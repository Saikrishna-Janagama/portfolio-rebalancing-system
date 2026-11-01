"""Add reviewable negative cases, JSON exports, dictionary, and evaluation to a generated dataset."""
import argparse
import csv
import json
import sqlite3
from pathlib import Path
from collections import Counter

ISSUES = ('WEIGHTS_NOT_100', 'TRADE_EXCEEDS_HOLDING', 'EXCLUDED_RECOMMENDED', 'FEES_OMITTED', 'STALE_PRICE_UNFLAGGED')

def save_json(path, data):
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def assess(case, db):
    p = case['payload']
    kind = case['issue_type']
    if kind == 'WEIGHTS_NOT_100':
        return abs(sum(p['weights'].values()) - 1) > 1e-6
    if kind == 'TRADE_EXCEEDS_HOLDING':
        qty = db.execute('SELECT quantity FROM Holding WHERE portfolio_id=? AND instrument_id=?', (p['portfolio_id'],p['instrument_id'])).fetchone()
        return p['side'] == 'SELL' and (qty is None or p['quantity'] > qty[0] + 1e-8)
    if kind == 'EXCLUDED_RECOMMENDED':
        return db.execute('SELECT 1 FROM "Constraint" WHERE portfolio_id=? AND instrument_id=? AND constraint_type=? AND active=1', (p['portfolio_id'],p['instrument_id'],'EXCLUDED')).fetchone() is not None
    if kind == 'FEES_OMITTED':
        return p['estimated_fee'] is None or abs(p['estimated_fee'] - p['expected_fee']) > 1e-4
    if kind == 'STALE_PRICE_UNFLAGGED':
        return p['price_date'] < p['as_of'] and not p['stale_flag']
    return False

def run(folder):
    folder = Path(folder)
    database_path = folder / 'rebalance.sqlite'
    if not database_path.is_file():
        raise FileNotFoundError(
            f'Database not found: {database_path.resolve()}\n'
            'Run main.py first using the same --out directory.'
        )
    db = sqlite3.connect(database_path)
    db.row_factory = sqlite3.Row
    json_dir = folder / 'json'
    json_dir.mkdir(exist_ok=True)
    dictionary = []
    tables = [r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for table in tables:
        columns = [dict(r) for r in db.execute(f'PRAGMA table_info("{table}")')]
        foreign_keys = [dict(r) for r in db.execute(f'PRAGMA foreign_key_list("{table}")')]
        for col in columns:
            fk = next((x for x in foreign_keys if x['from'] == col['name']), None)
            dictionary.append({'entity':table,'field':col['name'],'sql_type':col['type'], 'required':bool(col['notnull'] or col['pk']), 'primary_key':bool(col['pk']), 'references':f"{fk['table']}.{fk['to']}" if fk else None,'units':'USD' if col['name'] in ('price','notional','fee','portfolio_value','estimated_fees','flat_fee','threshold','liquidity_limit') else 'fraction [0,1]' if 'weight' in col['name'] or col['name']=='rate' else None,'validation':'Enforced by schema constraints; see schema.sql. Effective dates use ISO-8601.'})
        save_json(json_dir / f'{table}.json', [dict(r) for r in db.execute(f'SELECT * FROM "{table}" ORDER BY 1')])
    save_json(folder / 'data_dictionary.json', dictionary)
    with (folder / 'data_dictionary.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=dictionary[0].keys());w.writeheader();w.writerows(dictionary)

    proposal = db.execute('SELECT id,portfolio_id,as_of FROM RebalanceProposal ORDER BY id LIMIT 1').fetchone()
    holding = db.execute('SELECT instrument_id,quantity FROM Holding WHERE portfolio_id=? LIMIT 1',(proposal['portfolio_id'],)).fetchone()
    excluded = db.execute('SELECT portfolio_id,instrument_id FROM "Constraint" WHERE constraint_type="EXCLUDED" AND active=1 LIMIT 1').fetchone()
    stale = db.execute('SELECT h.portfolio_id,h.instrument_id,MAX(mp.price_date) AS last_date FROM Holding h JOIN MarketPrice mp ON mp.instrument_id=h.instrument_id JOIN RebalanceProposal rp ON rp.portfolio_id=h.portfolio_id GROUP BY h.portfolio_id,h.instrument_id HAVING MAX(mp.price_date)<MAX(rp.as_of) LIMIT 1').fetchone()
    if stale is None:
        stale = {'portfolio_id':proposal['portfolio_id'],'instrument_id':holding['instrument_id'],'last_date':'2026-10-21'}
    cases = [
        {'issue_type':ISSUES[0], 'payload':{'portfolio_id':proposal['portfolio_id'],'weights':{'EQUITY':.6,'BOND':.3,'CASH':.05,'COMMODITY':.02}}},
        {'issue_type':ISSUES[1], 'payload':{'portfolio_id':proposal['portfolio_id'],'instrument_id':holding['instrument_id'],'side':'SELL','quantity':holding['quantity']+10}},
        {'issue_type':ISSUES[2], 'payload':{'portfolio_id':excluded['portfolio_id'],'instrument_id':excluded['instrument_id'],'side':'BUY','quantity':1}},
        {'issue_type':ISSUES[3], 'payload':{'portfolio_id':proposal['portfolio_id'],'notional':2000,'expected_fee':2.2,'estimated_fee':None}},
        {'issue_type':ISSUES[4], 'payload':{'portfolio_id':stale['portfolio_id'],'instrument_id':stale['instrument_id'],'price_date':stale['last_date'],'as_of':proposal['as_of'],'stale_flag':False}},
    ]
    # Also test a clean candidate for each scenario to measure false positives.
    for index, item in enumerate(list(cases)):
        good = json.loads(json.dumps(item))
        p=good['payload']
        if index==0:p['weights']={'EQUITY':.5,'BOND':.35,'CASH':.1,'COMMODITY':.05}
        elif index==1:p['quantity']=max(0.0001,holding['quantity']/2)
        elif index==2:p['instrument_id']=holding['instrument_id'];p['portfolio_id']=proposal['portfolio_id']
        elif index==3:p['estimated_fee']=p['expected_fee']
        else:p['stale_flag']=True
        cases.append(good)
    # Ensure clean excluded candidate truly is non-excluded.
    good_ins=db.execute('SELECT h.instrument_id FROM Holding h WHERE h.portfolio_id=? AND NOT EXISTS (SELECT 1 FROM "Constraint" c WHERE c.portfolio_id=h.portfolio_id AND c.instrument_id=h.instrument_id AND c.constraint_type="EXCLUDED" AND c.active=1) LIMIT 1',(proposal['portfolio_id'],)).fetchone()
    cases[7]['payload']['instrument_id']=good_ins['instrument_id']
    truth=[];inputs=[];tp=fp=tn=fn=0
    for i,c in enumerate(cases):
        cid=f'TEST-{i+1:04d}'
        expected = i < 5
        detected = assess(c,db)
        inputs.append({'case_id':cid,**c})
        truth.append({'case_id':cid,'issue_type':c['issue_type'],'expected_decision':'REJECT' if expected else 'ACCEPT','actual_decision':'REJECT' if detected else 'ACCEPT','correct':expected==detected})
        if expected and detected:tp+=1
        elif expected:fn+=1
        elif detected:fp+=1
        else:tn+=1
    save_json(folder/'candidate_inputs.json',inputs)
    # Keep expected labels separate from observed decisions to avoid label leakage.
    labels = [{k: item[k] for k in ('case_id', 'issue_type', 'expected_decision')} for item in truth]
    save_json(folder/'ground_truth_injected.json',labels)
    with (folder/'ground_truth_injected.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=labels[0].keys());w.writeheader();w.writerows(labels)
    save_json(folder/'case_evaluation.json',truth)
    # Review lifecycle is append-only; the proposal and trade tables are left unchanged.
    events=[]
    for i,c in enumerate(cases):
        cid=f'TEST-{i+1:04d}'
        events.append({'event_id':f'REVIEW-{i+1:04d}-01','entity_id':cid,'from_state':'SUBMITTED','to_state':'REJECTED' if i<5 else 'APPROVED','event_type':'POLICY_REVIEW','event_date':proposal['as_of'],'reason':c['issue_type']})
    save_json(folder/'review_events.json',events)
    report={'candidate_cases':len(cases),'positive_cases':5,'negative_cases':5,'true_positive':tp,'false_positive':fp,'true_negative':tn,'false_negative':fn,'precision':tp/(tp+fp) if tp+fp else 0,'recall':tp/(tp+fn) if tp+fn else 0,'accuracy':(tp+tn)/len(cases),'all_cases_pass':all(x['correct'] for x in truth),'json_entities_exported':len(tables),'dictionary_fields':len(dictionary)}
    save_json(folder/'additional_validation.json',report)
    db.close()
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',default=str(Path(__file__).resolve().parent / 'output'));args=parser.parse_args()
    print(json.dumps(run(args.out),indent=2))
