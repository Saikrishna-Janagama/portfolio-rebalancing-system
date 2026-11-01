import argparse, csv, json, hashlib, random, sqlite3
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict, Counter

SEED=20261022
ASSETS=('EQUITY','BOND','CASH','COMMODITY')
TARGETS={'CONSERVATIVE':(25,60,10,5),'BALANCED':(50,35,10,5),'AGGRESSIVE':(75,15,5,5)}
TABLES=['InvestorProfile','Portfolio','Instrument','Holding','MarketPrice','TargetAllocation','FeeSchedule','Constraint','Trade','RebalanceProposal','ProposalTrade','ProposalAllocation','EventLog','AnomalyCase']
DDL='''
PRAGMA foreign_keys=ON;
CREATE TABLE InvestorProfile(id TEXT PRIMARY KEY,risk_profile TEXT NOT NULL CHECK(risk_profile IN ('CONSERVATIVE','BALANCED','AGGRESSIVE')),created_on TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE Portfolio(id TEXT PRIMARY KEY,investor_id TEXT NOT NULL REFERENCES InvestorProfile(id),created_on TEXT NOT NULL,status TEXT NOT NULL,base_currency TEXT NOT NULL);
CREATE TABLE Instrument(id TEXT PRIMARY KEY,symbol TEXT UNIQUE NOT NULL,asset_class TEXT NOT NULL CHECK(asset_class IN ('EQUITY','BOND','CASH','COMMODITY')),liquidity_limit REAL NOT NULL CHECK(liquidity_limit>=0),status TEXT NOT NULL);
CREATE TABLE Holding(id TEXT PRIMARY KEY,portfolio_id TEXT NOT NULL REFERENCES Portfolio(id),instrument_id TEXT NOT NULL REFERENCES Instrument(id),quantity REAL NOT NULL CHECK(quantity>=0),as_of TEXT NOT NULL,UNIQUE(portfolio_id,instrument_id));
CREATE TABLE MarketPrice(id TEXT PRIMARY KEY,instrument_id TEXT NOT NULL REFERENCES Instrument(id),price_date TEXT NOT NULL,price REAL NOT NULL CHECK(price>0),currency TEXT NOT NULL,UNIQUE(instrument_id,price_date));
CREATE TABLE TargetAllocation(id TEXT PRIMARY KEY,portfolio_id TEXT NOT NULL REFERENCES Portfolio(id),asset_class TEXT NOT NULL,weight REAL NOT NULL CHECK(weight BETWEEN 0 AND 1),effective_from TEXT NOT NULL,effective_to TEXT,UNIQUE(portfolio_id,asset_class,effective_from));
CREATE TABLE FeeSchedule(id TEXT PRIMARY KEY,asset_class TEXT NOT NULL,tier_min REAL NOT NULL,tier_max REAL,rate REAL NOT NULL,flat_fee REAL NOT NULL,effective_from TEXT NOT NULL);
CREATE TABLE "Constraint"(id TEXT PRIMARY KEY,portfolio_id TEXT REFERENCES Portfolio(id),instrument_id TEXT REFERENCES Instrument(id),constraint_type TEXT NOT NULL,threshold REAL,effective_from TEXT NOT NULL,active INTEGER NOT NULL CHECK(active IN (0,1)));
CREATE TABLE RebalanceProposal(id TEXT PRIMARY KEY,portfolio_id TEXT NOT NULL REFERENCES Portfolio(id),as_of TEXT NOT NULL,status TEXT NOT NULL,portfolio_value REAL NOT NULL,estimated_fees REAL NOT NULL,maximum_drift_before REAL NOT NULL,maximum_drift_after REAL NOT NULL,reason TEXT NOT NULL);
CREATE TABLE Trade(id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL REFERENCES RebalanceProposal(id),portfolio_id TEXT NOT NULL REFERENCES Portfolio(id),instrument_id TEXT NOT NULL REFERENCES Instrument(id),side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),quantity REAL NOT NULL CHECK(quantity>0),price REAL NOT NULL,notional REAL NOT NULL,fee REAL NOT NULL,status TEXT NOT NULL,created_on TEXT NOT NULL);
CREATE TABLE ProposalTrade(proposal_id TEXT NOT NULL REFERENCES RebalanceProposal(id),trade_id TEXT NOT NULL UNIQUE REFERENCES Trade(id),PRIMARY KEY(proposal_id,trade_id));
CREATE TABLE ProposalAllocation(proposal_id TEXT NOT NULL REFERENCES RebalanceProposal(id),asset_class TEXT NOT NULL,weight_before REAL NOT NULL,weight_after REAL NOT NULL,target_weight REAL NOT NULL,PRIMARY KEY(proposal_id,asset_class));
CREATE TABLE EventLog(id TEXT PRIMARY KEY,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,event_type TEXT NOT NULL,event_date TEXT NOT NULL,details TEXT NOT NULL);
CREATE TABLE AnomalyCase(id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL REFERENCES RebalanceProposal(id),issue_type TEXT NOT NULL,ground_truth TEXT NOT NULL,details TEXT NOT NULL);
CREATE INDEX ix_price_lookup ON MarketPrice(instrument_id,price_date);
CREATE INDEX ix_holding_port ON Holding(portfolio_id);
'''

def rows(conn,sql,params=()): return conn.execute(sql,params).fetchall()
def insert_many(conn,table,columns,data):
    if data: conn.executemany(f'INSERT INTO "{table}" ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',data)
def fee_for(value,asset):
    rate=0.001 if value<1000 else 0.0006 if value<10000 else 0.0003
    return round(1+value*rate+(0.0002*value if asset=='COMMODITY' else 0),4)
def write_csv(path,headers,data):
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(headers);w.writerows(data)
def export(conn,out):
    d=out/'csv';d.mkdir(exist_ok=True)
    for t in TABLES:
        if t=='AnomalyCase':continue
        cur=conn.execute(f'SELECT * FROM "{t}" ORDER BY 1');write_csv(d/f'{t}.csv',[x[0] for x in cur.description],cur.fetchall())
    anomalies=rows(conn,'SELECT id,proposal_id,issue_type,ground_truth,details FROM AnomalyCase ORDER BY id')
    write_csv(out/'ground_truth.csv',['id','proposal_id','issue_type','ground_truth','details'],anomalies)
    (out/'ground_truth.json').write_text(json.dumps([dict(zip(['id','proposal_id','issue_type','ground_truth','details'],r)) for r in anomalies],indent=2))
    (out/'schema.sql').write_text(DDL)

def build(out,nport=1000,ninst=5000,days=250,seed=SEED):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    db=out/'rebalance.sqlite';db.unlink(missing_ok=True)
    conn=sqlite3.connect(db);conn.executescript(DDL);rng=random.Random(seed)
    end=date(2026,10,22);dates=[];d=end
    while len(dates)<days:
        if d.weekday()<5:dates.append(d)
        d-=timedelta(days=1)
    dates.reverse();asof=dates[-1].isoformat()
    investors=[];ports=[];instruments=[];holdings=[];prices=[];targets=[];fees=[];constraints=[]
    profiles=list(TARGETS)
    for i in range(1,nport+1):
        iid=f'INV-{i:06d}';pid=f'PORT-{iid}-01';risk=profiles[(i-1)%3]
        investors.append((iid,risk,dates[0].isoformat(),'ACTIVE'))
        ports.append((pid,iid,dates[0].isoformat(),'ACTIVE','USD'))
        for asset,w in zip(ASSETS,TARGETS[risk]):targets.append((f'TA-{i:06d}-{asset}',pid,asset,w/100,dates[0].isoformat(),None))
    for j in range(1,ninst+1):
        ins=f'INS-{j:06d}';asset=ASSETS[(j-1)%4]
        instruments.append((ins,f'SYN{j:05d}',asset,round(rng.uniform(1000,50000),2),'ACTIVE'))
    # Five positions per portfolio by default, while all instruments have independent price histories.
    for i in range(1,nport+1):
        pid=ports[i-1][0]
        for k in range(5):
            j=((i-1)*5+k)%ninst+1;ins=f'INS-{j:06d}'
            qty=round(rng.uniform(4,60)*(4 if i%19==0 and k==0 else 1),4)
            holdings.append((f'HOLD-{pid}-{ins}',pid,ins,qty,asof))
    lastprice={};missing=0
    for j in range(1,ninst+1):
        ins=f'INS-{j:06d}';asset=ASSETS[(j-1)%4];p=rng.uniform(15,180)
        for idx,dt in enumerate(dates):
            p=max(0.5,p*(1+rng.gauss(0,0.009 if asset=='EQUITY' else 0.003)))
            if idx==len(dates)-1 and j%97==0:missing+=1;continue
            val=round(p,4);prices.append((f'MP-{j:06d}-{idx+1:03d}',ins,dt.isoformat(),val,'USD'))
            lastprice[ins]=(val,dt.isoformat())
    for a in ASSETS:
        for tier,(lo,hi,rate) in enumerate(((0,1000,.001),(1000,10000,.0006),(10000,None,.0003)),1):
            fees.append((f'FEE-{a}-{tier}',a,lo,hi,rate,1,dates[0].isoformat()))
    for i,p in enumerate(ports,1):
        pid=p[0];constraints.append((f'CON-MIN-{i:06d}',pid,None,'MIN_TRADE',100,dates[0].isoformat(),1))
        if i%17==0:
            ins=holdings[(i-1)*5][2]
            constraints.append((f'CON-EXC-{i:06d}',pid,ins,'EXCLUDED',None,dates[0].isoformat(),1))
    for table,cols,data in [
        ('InvestorProfile','id risk_profile created_on status',investors),('Portfolio','id investor_id created_on status base_currency',ports),
        ('Instrument','id symbol asset_class liquidity_limit status',instruments),('Holding','id portfolio_id instrument_id quantity as_of',holdings),
        ('MarketPrice','id instrument_id price_date price currency',prices),('TargetAllocation','id portfolio_id asset_class weight effective_from effective_to',targets),
        ('FeeSchedule','id asset_class tier_min tier_max rate flat_fee effective_from',fees),('Constraint','id portfolio_id instrument_id constraint_type threshold effective_from active',constraints)]:
        insert_many(conn,table,cols.split(),data)
    assetmap={r[0]:r[2] for r in instruments};limits={r[0]:r[3] for r in instruments}
    hp=defaultdict(list)
    for h in holdings:hp[h[1]].append(h)
    excluded={(r[1],r[2]) for r in constraints if r[3]=='EXCLUDED'}
    proposals=[];trades=[];links=[];allocations=[];events=[];anomalies=[]
    for i,port in enumerate(ports,1):
        pid=port[0];rid=f'RB-2026-{i:06d}';risk=investors[i-1][1];target=dict(zip(ASSETS,[v/100 for v in TARGETS[risk]]))
        vals=defaultdict(float);valid=[];stale=False
        for h in hp[pid]:
            ins=h[2];p,dt=lastprice[ins]
            if dt!=asof:stale=True;continue # price freshness is a hard constraint
            value=h[3]*p;vals[assetmap[ins]]+=value;valid.append((h,assetmap[ins],p,value))
        total=sum(vals.values())
        before={a:(vals[a]/total if total else 0) for a in ASSETS}
        maxbefore=max(abs(before[a]-target[a]) for a in ASSETS)
        # Sell overweight classes first; buy only from available proceeds, net of fees.
        cash=0.;newvals=dict(vals);porttrades=[];reason='NO_ACTION'
        if stale:reason='MISSING_PRICE_PARTIAL'
        if total>0 and maxbefore>0.03:
            for h,a,p,value in valid:
                excess=max(0,newvals[a]-target[a]*total)
                desired=min(excess,value,limits[h[2]])
                qty=min(h[3],int(desired/p*10000)/10000)
                notional=round(qty*p,4)
                if notional>=100 and (pid,h[2]) not in excluded:
                    f=fee_for(notional,a)
                    if notional>f:
                        porttrades.append((h[2],'SELL',qty,p,notional,f))
                        newvals[a]-=notional;cash+=notional-f
            for h,a,p,value in valid:
                if (pid,h[2]) in excluded:continue
                deficit=max(0,target[a]*total-newvals[a]);desired=min(deficit,limits[h[2]],cash)
                qty=int(desired/p*10000)/10000;notional=round(qty*p,4)
                f=fee_for(notional,a)
                if notional>=100 and notional+f<=cash+1e-7:
                    porttrades.append((h[2],'BUY',qty,p,notional,f))
                    newvals[a]+=notional;cash-=notional+f
            if porttrades:reason='REBALANCE'
            elif reason=='NO_ACTION':reason='CONSTRAINT_BLOCKED'
        fees_total=round(sum(t[5] for t in porttrades),4)
        after_total=total-fees_total
        # Cash proceeds not reinvested remain in the CASH asset class.
        newvals['CASH']+=cash
        after={a:(newvals[a]/after_total if after_total>0 else 0) for a in ASSETS}
        maxafter=max(abs(after[a]-target[a]) for a in ASSETS)
        status='PROPOSED' if porttrades else 'NO_TRADE'
        proposals.append((rid,pid,asof,status,round(total,4),fees_total,round(maxbefore,8),round(maxafter,8),reason))
        for a in ASSETS:allocations.append((rid,a,round(before[a],10),round(after[a],10),target[a]))
        for ins,side,qty,p,notional,f in porttrades:
            tid=f'TRD-{len(trades)+1:08d}'
            trades.append((tid,rid,pid,ins,side,qty,p,notional,f,'PROPOSED',asof));links.append((rid,tid))
        events.append((f'EVT-{i:08d}','RebalanceProposal',rid,'CREATED',asof,json.dumps({'status':status,'trade_count':len(porttrades)})))
        # Negative scenarios are labelled test cases; baseline trade/holding records remain valid.
        if i<=5:
            issue=['WEIGHTS_NOT_100','TRADE_EXCEEDS_HOLDING','EXCLUDED_RECOMMENDED','FEES_OMITTED','STALE_PRICE_UNFLAGGED'][i-1]
            anomalies.append((f'CASE-{i:06d}',rid,issue,'REJECT',json.dumps({'scenario':issue,'injection':'isolated hypothetical candidate','baseline_mutated':False})))
    for table,cols,data in [
        ('RebalanceProposal','id portfolio_id as_of status portfolio_value estimated_fees maximum_drift_before maximum_drift_after reason',proposals),
        ('Trade','id proposal_id portfolio_id instrument_id side quantity price notional fee status created_on',trades),
        ('ProposalTrade','proposal_id trade_id',links),('ProposalAllocation','proposal_id asset_class weight_before weight_after target_weight',allocations),
        ('EventLog','id entity_type entity_id event_type event_date details',events),('AnomalyCase','id proposal_id issue_type ground_truth details',anomalies)]:
        insert_many(conn,table,cols.split(),data)
    conn.commit();export(conn,out)
    checks={
      'foreign_key_errors':len(rows(conn,'PRAGMA foreign_key_check')),
      'target_weight_errors':len(rows(conn,'SELECT portfolio_id FROM TargetAllocation GROUP BY portfolio_id HAVING ABS(SUM(weight)-1)>0.000001')),
      'post_weight_errors':len(rows(conn,'SELECT proposal_id FROM ProposalAllocation GROUP BY proposal_id HAVING ABS(SUM(weight_after)-1)>0.00001')),
      'negative_holding_errors':len(rows(conn,'SELECT id FROM Holding WHERE quantity<0')),
      'excluded_trade_errors':len(rows(conn,"SELECT t.id FROM Trade t JOIN \"Constraint\" c ON c.portfolio_id=t.portfolio_id AND c.instrument_id=t.instrument_id AND c.constraint_type='EXCLUDED' AND c.active=1")),
      'fee_errors':sum(abs(t[8]-fee_for(t[7],assetmap[t[3]]))>0.00001 for t in trades),
      'oversell_errors':len(rows(conn,"SELECT t.id FROM Trade t JOIN Holding h ON h.portfolio_id=t.portfolio_id AND h.instrument_id=t.instrument_id WHERE t.side='SELL' AND t.quantity>h.quantity+0.0000001")),
      'arithmetic_errors':sum(abs(t[5]*t[6]-t[7])>0.02 for t in trades),
      'missing_latest_prices':missing,
      'proposals_with_trades':sum(p[3]=='PROPOSED' for p in proposals),
      'drift_improved':sum(p[7]<p[6]-0.000001 for p in proposals),
      'known_negative_cases':len(anomalies),
    }
    counts={t:conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in TABLES}
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((out/'csv').glob('*.csv'))}
    report={'seed':seed,'parameters':{'portfolios':nport,'instruments':ninst,'trading_days':days},'row_counts':counts,'checks':checks,'csv_sha256':hashes,'pass':all(checks[k]==0 for k in ('foreign_key_errors','target_weight_errors','post_weight_errors','negative_holding_errors','excluded_trade_errors','fee_errors','oversell_errors','arithmetic_errors'))}
    (out/'validation_summary.json').write_text(json.dumps(report,indent=2));(out/'source_manifest.json').write_text(json.dumps({'provenance':'Entirely synthetic; no external data','seed':seed,'as_of':asof,'price_model':'Seeded Gaussian daily returns; simulated USD prices','license':'Synthetic educational demonstration'},indent=2))
    conn.close();return report

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',default=str(Path(__file__).resolve().parent / 'output'));parser.add_argument('--portfolios',type=int,default=1000);parser.add_argument('--instruments',type=int,default=5000);parser.add_argument('--days',type=int,default=250);parser.add_argument('--seed',type=int,default=SEED);parser.add_argument('--verify-repeat',action='store_true');args=parser.parse_args()
    r=build(args.out,args.portfolios,args.instruments,args.days,args.seed)
    if args.verify_repeat:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            r2=build(tmp,args.portfolios,args.instruments,args.days,args.seed)
            r['reproducible']=r['row_counts']==r2['row_counts'] and r['csv_sha256']==r2['csv_sha256']
        (Path(args.out)/'validation_summary.json').write_text(json.dumps(r,indent=2))
    print(json.dumps({'pass':r['pass'],'reproducible':r.get('reproducible'),'row_counts':r['row_counts'],'checks':r['checks']},indent=2))
if __name__=='__main__':main()
