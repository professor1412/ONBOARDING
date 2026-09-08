"""Shared, deterministic onboarding and airport loading and calculations.

Both the notebook and the dashboard call these functions. Expensive campaign
estimation stays in the notebook; the dashboard reads its verified exports.
Airport aggregations, sample diagnostics and intervention scenarios are shared too.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / 'Data'
OUTPUT_DIR = ROOT / 'outputs' / 'part_a'
DELIVERABLES_DIR = ROOT / 'deliverables'
CUTOFF = pd.Timestamp('2026-06-30 23:59:00', tz='Asia/Kolkata')
COHORT_START = pd.Timestamp('2026-01-01', tz='Asia/Kolkata')
COHORT_END = pd.Timestamp('2026-06-01', tz='Asia/Kolkata')
HORIZON = pd.Timedelta(days=30)
MONTHS = 5
DOCS = ['DL', 'RC', 'AADHAAR', 'PERMIT', 'FITNESS', 'INSURANCE']
FILES = ['captains', 'doc_events', 'approvals', 'activation', 'nudges']
QUALITY_REASONS = ['image_blurred', 'ocr_low_confidence', 'details_not_legible']
SEGMENTS = ['city', 'vehicle_type', 'acquisition_channel', 'device_tier',
            'app_language', 'age_band', 'signup_month']

@dataclass
class AuditResult:
    data: dict
    captains: pd.DataFrame
    attempts: pd.DataFrame
    manifest: list
    tables: dict

@dataclass
class FunnelResult:
    captains: pd.DataFrame
    cohort: pd.DataFrame
    deadline: pd.Series
    event30: pd.DataFrame
    stage_masks: dict
    funnel: pd.DataFrame
    docs_complete: pd.Series
    final_lost: pd.Series
    kpi: pd.DataFrame
    tables: dict



def wilson(k, n, confidence=.95):
    k, n = np.asarray(k, dtype=float), np.asarray(n, dtype=float)
    z = norm.ppf((1 + confidence) / 2)
    p = np.divide(k, n, out=np.zeros_like(k), where=n > 0)
    den = 1 + z*z / n
    centre = (p + z*z / (2*n)) / den
    half = z * np.sqrt(p*(1-p)/n + z*z/(4*n*n)) / den
    return centre-half, centre+half

def independent_difference_ci(y1, y0):
    # Newcombe interval from independent Wilson score intervals.
    p1, p0 = np.mean(y1), np.mean(y0)
    l1, u1 = wilson(np.sum(y1), len(y1)); l0, u0 = wilson(np.sum(y0), len(y0))
    d = p1-p0
    return d, d-np.sqrt((p1-l1)**2+(u0-p0)**2), d+np.sqrt((u1-p1)**2+(p0-l0)**2)

def transported_rate(reference, target, group_cols, outcome='a2o30'):
    stats=reference.groupby(group_cols)[outcome].agg(['mean','size'])
    merged=target[group_cols].join(stats,on=group_cols)
    assert merged['mean'].notna().all(), 'Unsupported transport stratum; review explicitly.'
    return float(merged['mean'].mean()), int(merged['size'].min())

def load_and_audit(data_dir=DATA_DIR, cutoff=CUTOFF):
    """Read the five onboarding CSVs, validate event histories and collect quality findings.

    Raises on invalid keys, dates, document order or attempt histories. Known
    summary/telemetry inconsistencies are retained and explicitly reported.
    """
    data_dir = Path(data_dir)
    tables = {}
    def show_table(name, frame):
        tables[name] = frame.copy()
    raw = {name: pd.read_csv(data_dir / f'{name}.csv') for name in FILES}
    data = {name: frame.copy() for name, frame in raw.items()}
    manifest = []
    for name, frame in data.items():
        for col in frame.select_dtypes(include=['object', 'string']).columns:
            frame[col] = frame[col].str.strip().replace('', pd.NA)
        for col in [c for c in frame.columns if c.endswith('_ts')]:
            parsed = pd.to_datetime(frame[col], format='mixed', errors='coerce')
            assert not (frame[col].notna() & parsed.isna()).any(), (name, col, 'invalid timestamp')
            frame[col] = parsed.dt.tz_localize('Asia/Kolkata')
            assert not frame[col].gt(cutoff).any(), (name, col, 'after extraction')
        path = data_dir / f'{name}.csv'
        manifest.append({'file': path.name, 'rows': len(frame),
                         'captains': frame.captain_id.nunique(),
                         'exact_duplicate_rows': int(frame.duplicated().sum()),
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    captains, events, approvals, activation, nudges = [data[n] for n in FILES]
    show_table('input_manifest', pd.DataFrame(manifest))
    show_table('missing_values', pd.concat(
        [f.isna().sum().rename(n) for n, f in data.items()], axis=1).fillna('—'))

    for frame in [captains, approvals, activation]:
        assert frame.captain_id.notna().all() and frame.captain_id.is_unique
    for name, frame in data.items():
        assert not frame.duplicated().any(), (name, 'exact duplicates require review')
        assert set(frame.captain_id).issubset(set(captains.captain_id)), (name, 'orphan IDs')
    assert set(approvals.captain_id) == set(captains.captain_id)
    assert set(activation.captain_id) == set(approvals.loc[approvals.final_status.eq('approved'), 'captain_id'])
    assert events.event_id.is_unique
    assert not events.duplicated(['captain_id', 'doc_type', 'attempt_no', 'event_type']).any()
    assert set(events.doc_type) == set(DOCS)
    assert set(events.event_type) == {'upload_success', 'verification_pass', 'verification_fail'}
    assert set(approvals.final_status) == {'approved', 'rejected', 'dropped_in_docs', 'in_progress'}
    assert set(captains.vehicle_type) == {'Auto', 'Cab', 'ERickshaw'}
    assert events.attempt_no.isin([1, 2, 3]).all()
    assert nudges.delivered.isin([0, 1]).all() and nudges.clicked.isin([0, 1]).all()
    assert events.failure_reason.notna().eq(events.event_type.eq('verification_fail')).all()

    s = (captains.merge(approvals, on='captain_id', validate='one_to_one')
         .merge(activation, on='captain_id', how='left', validate='one_to_one').set_index('captain_id'))
    s['signup_month'] = s.signup_ts.dt.strftime('%Y-%m')
    s['age_days'] = (cutoff-s.signup_ts).dt.total_seconds()/86400
    s['approved_at'] = s.decision_ts.where(s.final_status.eq('approved'))
    for frame, timecol in [(events, 'event_ts'), (nudges, 'sent_ts')]:
        assert frame[timecol].ge(frame.captain_id.map(s.signup_ts)).all()
    assert not s.decision_ts.lt(s.signup_ts).any()
    assert not s.first_order_ts.lt(s.approved_at).any()
    assert s.loc[s.final_status.isin(['approved', 'rejected']), 'decision_ts'].notna().all()

    attempts = events.pivot(index=['captain_id', 'doc_type', 'attempt_no'],
                            columns='event_type', values='event_ts')
    assert attempts.upload_success.notna().all()
    assert not (attempts.verification_pass.notna() & attempts.verification_fail.notna()).any()
    attempts['result_ts'] = attempts.verification_pass.fillna(attempts.verification_fail)
    assert not attempts.result_ts.lt(attempts.upload_success).any()
    attempts['verification_hours'] = (attempts.result_ts-attempts.upload_success).dt.total_seconds()/3600
    attempt_order = attempts.reset_index().sort_values(['captain_id', 'doc_type', 'attempt_no'])
    prev_no = attempt_order.groupby(['captain_id', 'doc_type']).attempt_no.shift()
    prev_fail = attempt_order.groupby(['captain_id', 'doc_type']).verification_fail.shift()
    retry = attempt_order.attempt_no.gt(1)
    assert (attempt_order.loc[retry, 'attempt_no']-prev_no[retry]).eq(1).all()
    assert prev_fail[retry].notna().all()
    assert attempt_order.loc[retry, 'upload_success'].ge(prev_fail[retry]).all()

    for event_type, prefix in [('verification_pass', 'pass_'), ('upload_success', 'upload_')]:
        times = events.loc[events.event_type.eq(event_type)].pivot_table(
            index='captain_id', columns='doc_type', values='event_ts', aggfunc='min').reindex(s.index)
        s = s.join(times.add_prefix(prefix))
    previous = s.signup_ts.copy()
    for doc in DOCS:
        applicable = s.vehicle_type.ne('ERickshaw') if doc == 'PERMIT' else pd.Series(True, index=s.index)
        observed = s['upload_'+doc].notna() & applicable
        assert previous[observed].notna().all(), (doc, 'missing prerequisite')
        assert s.loc[observed, 'upload_'+doc].ge(previous[observed]).all(), (doc, 'sequence error')
        if doc == 'PERMIT':
            assert s.loc[~applicable, ['upload_PERMIT', 'pass_PERMIT']].isna().all().all()
        previous = s['pass_'+doc].where(applicable, previous)
    approved = s.approved_at.notna()
    assert s.loc[approved, 'pass_INSURANCE'].notna().all()
    assert s.loc[approved, 'approved_at'].ge(s.loc[approved, 'pass_INSURANCE']).all()
    s['observed_docs_cleared'] = s[['pass_'+d for d in DOCS]].notna().sum(axis=1)
    s['summary_count_mismatch'] = s.docs_cleared.ne(s.observed_docs_cleared)
    anomalies = pd.DataFrame([
        ['Summary docs_cleared disagrees with timestamped passes', int(s.summary_count_mismatch.sum()),
         'Reconstruct from events; do not use summary counts for features or historical stages.'],
        ['Clicked=1 but delivered=0', int((nudges.clicked.eq(1)&nudges.delivered.eq(0)).sum()),
         'Use send assignment as exposure; exclude click/delivery from adjustment. Request telemetry reconciliation.'],
        ['orders_d7 exceeds orders_d30 (both observed)', int(s.orders_d7.gt(s.orders_d30).sum()),
         'Quarantine aggregate counts from outcome inference; clarify clock and aggregation rules.'],
        ['Uploaded attempts with no verification result by extraction', int(attempts.result_ts.isna().sum()),
         'Pending at cutoff, not automatic verification failures.'],
        ['Approved with no first order observed', int((s.approved_at.notna()&s.first_order_ts.isna()).sum()),
         'No observed activation; do not impute timestamps or mature order counts.'],
    ], columns=['check', 'rows_or_captains', 'treatment'])
    show_table('data_quality_findings', anomalies)
    show_table('summary_mismatches_by_month_status', s.loc[s.summary_count_mismatch]
               .groupby(['signup_month','final_status']).size().rename('captains').reset_index())
    return AuditResult(data, s, attempts, manifest, tables)


def build_onboarding_funnel(audit):
    """Count each captain once in the fixed Jan-May signup / D30 funnel.

    A captain's first unpassed required document owns their loss. ERickshaw
    skips Permit. An uploaded retry awaiting verification remains pending.
    """
    s = audit.captains.copy()
    events = audit.data['doc_events']
    tables = {}
    def show_table(name, frame):
        tables[name] = frame.copy()
    s['approval_days'] = (s.approved_at-s.signup_ts).dt.total_seconds()/86400
    s['first_order_days'] = (s.first_order_ts-s.signup_ts).dt.total_seconds()/86400
    s['a2o30'] = s.approval_days.between(0, 30)
    s['r2a30'] = s.first_order_days.between(0, 30)
    cohort = s.loc[s.signup_ts.ge(COHORT_START)&s.signup_ts.lt(COHORT_END)].copy()
    assert cohort.age_days.ge(30).all()
    assert not cohort.summary_count_mismatch.any(), 'Unexpected mature-cohort summary discrepancy.'
    N = len(cohort)
    deadline = cohort.signup_ts+HORIZON
    event30 = events.merge(cohort[['signup_ts']], left_on='captain_id', right_index=True)
    event30 = event30.loc[event30.event_ts.le(event30.signup_ts+HORIZON)].copy()
    remaining = pd.Series(True, index=cohort.index)
    funnel_rows, stage_masks = [], {}
    cohort['first_unpassed_stage'] = 'Approved'
    cohort['blocked_state'] = 'Approved'
    for doc in DOCS:
        applicable = cohort.vehicle_type.ne('ERickshaw') if doc=='PERMIT' else pd.Series(True,index=cohort.index)
        eligible = remaining & applicable
        uploaded = cohort['upload_'+doc].le(deadline) & eligible
        passed = cohort['pass_'+doc].le(deadline) & eligible
        latest_doc_event = (event30.loc[event30.doc_type.eq(doc)].sort_values('event_ts')
                            .drop_duplicates('captain_id',keep='last'))
        failed_ids = latest_doc_event.loc[latest_doc_event.event_type.eq('verification_fail'),'captain_id']
        lost = eligible & ~passed
        no_upload = lost & ~uploaded
        failed_unresolved = lost & uploaded & cohort.index.isin(failed_ids)
        pending = lost & uploaded & ~cohort.index.isin(failed_ids)
        skipped = remaining & ~applicable
        stage_masks[doc] = {'eligible':eligible, 'uploaded':uploaded, 'passed':passed, 'lost':lost,
                            'no_upload':no_upload, 'failed_unresolved':failed_unresolved, 'pending':pending}
        remaining = passed | skipped
        cohort.loc[lost, 'first_unpassed_stage'] = doc
        for label, mask in [('No successful upload',no_upload), ('Failed, no pass',failed_unresolved), ('Pending',pending)]:
            cohort.loc[mask, 'blocked_state'] = label
        funnel_rows.append({'stage':doc, 'eligible':int(eligible.sum()), 'uploaded':int(uploaded.sum()),
                            'passed':int(passed.sum()), 'not_applicable_skips':int(skipped.sum()),
                            'cumulative_remaining':int(remaining.sum()), 'lost':int(lost.sum()),
                            'no_upload':int(no_upload.sum()), 'failed_unresolved':int(failed_unresolved.sum()),
                            'pending':int(pending.sum()), 'conditional_pass_rate':passed.sum()/eligible.sum(),
                            'cumulative_rate':remaining.sum()/N})
    docs_complete = remaining.copy()
    final_lost = docs_complete & ~cohort.a2o30
    cohort.loc[final_lost, 'first_unpassed_stage'] = 'Final review'
    cohort.loc[final_lost, 'blocked_state'] = 'Rejected or pending review'
    assert not (cohort.a2o30 & ~docs_complete).any()
    funnel = pd.DataFrame(funnel_rows).set_index('stage')
    funnel['share_of_all_signup_losses'] = funnel.lost/(N-cohort.a2o30.sum())
    assert int(funnel.lost.sum()+final_lost.sum()+cohort.a2o30.sum()) == N
    assert (funnel[['no_upload','failed_unresolved','pending']].sum(axis=1)==funnel.lost).all()
    assert cohort.first_unpassed_stage.eq('Approved').eq(cohort.a2o30).all()
    show_table('funnel', funnel)
    final_review = cohort.loc[docs_complete].groupby('final_status').size().rename('captains').reset_index()
    show_table('final_review', final_review)
    kpi = pd.DataFrame({'metric':['A2O D30','R2A D30','R2A D30 among A2O D30'],
                        'numerator':[cohort.a2o30.sum(),cohort.r2a30.sum(),cohort.r2a30.sum()],
                        'denominator':[N,N,cohort.a2o30.sum()]})
    kpi['rate'] = kpi.numerator/kpi.denominator
    kpi['ci95_low'],kpi['ci95_high'] = wilson(kpi.numerator,kpi.denominator)
    show_table('primary_kpis', kpi)
    show_table('observed_approval_timing_days', cohort.loc[cohort.a2o30,'approval_days']
               .quantile([.5,.9,.95,.99,1]).rename('days').to_frame())


    return FunnelResult(s, cohort, deadline, event30, stage_masks, funnel,
                        docs_complete, final_lost, kpi, tables)


def summarize_segments(cohort, dimensions=SEGMENTS):
    """Signup-denominator segment rates and Wilson intervals; descriptive, not causal."""
    segments=[]
    for field in dimensions:
        t = cohort.groupby(field).agg(signups=('signup_ts','size'),approved=('a2o30','sum'),active=('r2a30','sum'))
        t['a2o30'] = t.approved/t.signups
        t['not_approved_d30'] = t.signups-t.approved
        t['ci95_low'],t['ci95_high'] = wilson(t.approved,t.signups)
        t['dimension']=field
        t=t.reset_index().rename(columns={field:'segment'})
        segments.append(t)
    segment_table = pd.concat(segments,ignore_index=True)

    return segment_table


def input_fingerprint(data_dir=DATA_DIR):
    """Content-based cache key for the onboarding inputs (airport CSVs are excluded)."""
    data_dir = Path(data_dir)
    return tuple((f'{name}.csv', hashlib.sha256((data_dir / f'{name}.csv').read_bytes()).hexdigest())
                 for name in FILES)


def load_result_table(name, output_dir=OUTPUT_DIR):
    """Read an exported notebook table, preserving its recorded index."""
    if not name.isidentifier():
        raise ValueError('Expected an exported table name, without an extension.')
    return pd.read_csv(Path(output_dir) / f'{name}.csv', index_col=0)


def load_part_a_results(output_dir=OUTPUT_DIR, data_dir=DATA_DIR):
    """Load completed notebook results only if their raw input hashes still match.

    Stale campaign estimates must never be shown beside newly computed funnel
    counts. Rerun analysis_partA.ipynb when the input data changes.
    """
    output_dir = Path(output_dir)
    manifest = json.loads((output_dir / 'run_manifest.json').read_text())
    expected = {row['file']: row['sha256'] for row in manifest['raw_inputs']}
    observed = dict(input_fingerprint(data_dir))
    if expected != observed:
        raise ValueError('Part A exports are stale. Rerun analysis_partA.ipynb from the current CSVs.')
    summary = json.loads((output_dir / 'summary.json').read_text())
    return summary, manifest


# Part B: marketplace counts and sampled trips have different denominators.
AIRPORT_FILES = ['airport_hourly', 'airport_trips']
AIRPORT_OUTPUT_DIR = ROOT / 'outputs' / 'part_b'
AIRPORT_START = pd.Timestamp('2026-05-01', tz='Asia/Kolkata')
CRITICAL_HOURS = [21, 22, 23, 0, 1, 2, 3]  # May-discovered, June-validated window
PERIODS = ['Critical 21:00–04:00', 'Daytime 09:00–19:00', 'Shoulder hours']


@dataclass
class AirportAudit:
    hourly: pd.DataFrame
    trips: pd.DataFrame
    tables: dict
    manifest: list


def airport_input_fingerprint(data_dir=DATA_DIR):
    data_dir = Path(data_dir)
    return tuple((f'{name}.csv', hashlib.sha256((data_dir/f'{name}.csv').read_bytes()).hexdigest())
                 for name in AIRPORT_FILES)


def load_airport_data(data_dir=DATA_DIR):
    """Validate raw airport files without silently reconciling incompatible counts."""
    data_dir=Path(data_dir)
    data={name:pd.read_csv(data_dir/f'{name}.csv') for name in AIRPORT_FILES}
    manifest=[]
    for name,frame in data.items():
        for col in frame.select_dtypes(include=['object','string']):
            frame[col]=frame[col].str.strip().replace('',pd.NA)
        assert not frame.isna().any().any(), (name,'missing values require review')
        assert not frame.duplicated().any(), (name,'duplicate rows require review')
        timecol='hour_ts' if name=='airport_hourly' else 'request_ts'
        frame[timecol]=pd.to_datetime(frame[timecol],format='mixed',errors='raise').dt.tz_localize('Asia/Kolkata')
        assert frame[timecol].between(AIRPORT_START,CUTOFF).all(), (name,'out-of-window timestamp')
        manifest.append({'file':f'{name}.csv','rows':len(frame),
                         'sha256':hashlib.sha256((data_dir/f'{name}.csv').read_bytes()).hexdigest()})
    h,t=data['airport_hourly'],data['airport_trips']
    assert not h.duplicated(['zone_id','hour_ts']).any()
    assert t.trip_id.is_unique
    assert h.hour_ts.eq(h.hour_ts.dt.floor('h')).all()
    assert h.groupby('zone_id').zone_type.nunique().eq(1).all()
    for col in ['requests','fulfilled_requests','unfulfilled_requests','online_captains']:
        assert h[col].ge(0).all() and h[col].mod(1).eq(0).all(), col
    assert h.requests.eq(h.fulfilled_requests+h.unfulfilled_requests).all()
    assert h.avg_eta_min.ge(0).all() and h.avg_surge_multiplier.gt(0).all()
    assert t.trip_distance_km.gt(0).all() and t.fare_inr.ge(0).all()
    assert t.captain_cancelled.isin([0,1]).all() and t.got_return_fare_within_20min.isin([0,1]).all()
    zone_map=h.drop_duplicates('zone_id').set_index('zone_id').zone_type
    assert t.pickup_zone_id.map(zone_map).eq('airport_terminal').all()
    assert t.drop_zone_id.map(zone_map).eq(t.drop_zone_type).all()
    expected_hours=pd.date_range(AIRPORT_START,CUTOFF.floor('h'),freq='h')
    grid=pd.MultiIndex.from_product([h.zone_id.unique(),expected_hours],names=['zone_id','hour_ts'])
    missing=grid.difference(pd.MultiIndex.from_frame(h[['zone_id','hour_ts']]))
    assert len(missing)==0, 'Missing zone-hours; do not average over incomplete coverage.'
    for frame,timecol in [(h,'hour_ts'),(t,'request_ts')]:
        frame['hour']=frame[timecol].dt.hour
        frame['date']=frame[timecol].dt.strftime('%Y-%m-%d')
        frame['month']=frame[timecol].dt.strftime('%Y-%m')
        frame['weekend']=frame[timecol].dt.dayofweek.ge(5)
        frame['critical']=frame.hour.isin(CRITICAL_HOURS)
    h['period']=np.select([h.critical,h.hour.between(9,18)],PERIODS[:2],default=PERIODS[2])
    h['eta_request_sum']=h.requests*h.avg_eta_min
    h['surge_request_sum']=h.requests*h.avg_surge_multiplier
    t['hour_ts']=t.request_ts.dt.floor('h')
    samples=t.groupby(['pickup_zone_id','hour_ts']).size().rename('sample_rows').reset_index()
    reconciliation=samples.merge(h[['zone_id','hour_ts','requests','fulfilled_requests']],
        left_on=['pickup_zone_id','hour_ts'],right_on=['zone_id','hour_ts'],how='left',validate='one_to_one')
    assert reconciliation.requests.notna().all()
    findings=pd.DataFrame([
        ['Missing zone-hours',len(missing),'Complete grid; retain all reported hours.'],
        ['Sample terminal-hours with sample rows > total requests',int(reconciliation.sample_rows.gt(reconciliation.requests).sum()),
         'Sampling/frame reconciliation is unavailable. Never multiply sample rates by marketplace totals.'],
        ['Sample terminal-hours with sample rows > fulfilled requests',int(reconciliation.sample_rows.gt(reconciliation.fulfilled_requests).sum()),
         'Trip sample is not a validated subset of completed marketplace requests.'],
        ['Cancelled records carrying return_fare=1',int((t.captain_cancelled.eq(1)&t.got_return_fare_within_20min.eq(1)).sum()),
         'Exclude all cancelled records from the next-fare analysis; retain for cancellation rate.'],
        ['Cancelled records with positive fare',int((t.captain_cancelled.eq(1)&t.fare_inr.gt(0)).sum()),
         'Fare cannot be assumed to be realized revenue or captain earnings.'],
        ['Trip timestamps exactly on the hour',int(t.request_ts.eq(t.request_ts.dt.floor('h')).sum()),
         'Treat as coarse request-time buckets; no trip duration or exact event ordering can be recovered.'],
        ['Non-cancelled records inside final 24 hours',int((t.captain_cancelled.eq(0)&t.request_ts.gt(CUTOFF-pd.Timedelta(hours=24))).sum()),
         'Exclude from primary return-fare comparison; test 0/6/24/48-hour buffers. Completion timestamps remain missing.'],
        ['Zone-hours with exactly one unfulfilled request',int(h.unfulfilled_requests.eq(1).sum()),
         'Possible metric floor; request source definition. Do not reinterpret as true zero loss.'],
    ],columns=['check','count','treatment'])
    tables={'input_manifest':pd.DataFrame(manifest),'data_quality_findings':findings,
            'zone_hour_coverage':h.groupby(['zone_id','zone_type']).agg(rows=('hour_ts','size'),first_hour=('hour_ts','min'),last_hour=('hour_ts','max')).reset_index(),
            'trip_marketplace_reconciliation':reconciliation,
            'cancel_return_crosscheck':pd.crosstab(t.captain_cancelled,t.got_return_fare_within_20min)}
    return AirportAudit(h,t,tables,manifest)


def aggregate_airport_hours(frame, keys):
    """Use ratios of totals; online captains remains a mean of zone-hour observations."""
    z=frame.groupby(keys,observed=True).agg(zone_hours=('requests','size'),requests=('requests','sum'),
        fulfilled=('fulfilled_requests','sum'),unfulfilled=('unfulfilled_requests','sum'),
        mean_online_per_zone_hour=('online_captains','mean'),eta_request_sum=('eta_request_sum','sum'),
        surge_request_sum=('surge_request_sum','sum')).reset_index()
    denominator=z.requests.replace(0,np.nan)
    z['fulfilment_rate']=z.fulfilled/denominator
    z['request_weighted_eta_min']=z.eta_request_sum/denominator
    z['request_weighted_surge']=z.surge_request_sum/denominator
    return z.drop(columns=['eta_request_sum','surge_request_sum'])


def analyse_airport_marketplace(audit, bootstraps=2000, seed=20260630):
    h=audit.hourly
    a=h.loc[h.zone_type.eq('airport_terminal')].copy()
    days=a.date.nunique()
    profile=aggregate_airport_hours(a,['hour'])
    monthly_hour=aggregate_airport_hours(a,['month','hour'])
    may=monthly_hour.loc[monthly_hour.month.eq('2026-05')]
    discovered=sorted(may.loc[may.fulfilment_rate.lt(.5),'hour'].tolist())
    assert discovered==sorted(CRITICAL_HOURS), 'Revisit the critical window if the May discovery data change.'
    periods=aggregate_airport_hours(a,['period']).set_index('period').reindex(PERIODS).reset_index()
    periods['requests_per_calendar_day']=periods.requests/days
    periods['unfulfilled_per_calendar_day']=periods.unfulfilled/days
    periods['share_of_airport_unfulfilled']=periods.unfulfilled/a.unfulfilled_requests.sum()
    monthly=aggregate_airport_hours(a,['month','period'])
    critical=a.loc[a.critical].copy()
    critical['night']=(critical.hour_ts-pd.Timedelta(hours=12)).dt.strftime('%Y-%m-%d')
    nights=aggregate_airport_hours(critical,['night'])
    full=nights.loc[nights.zone_hours.eq(2*len(CRITICAL_HOURS))].copy()
    assert len(full)>=30
    rng=np.random.default_rng(seed)
    ix=rng.integers(0,len(full),(bootstraps,len(full)))
    boot_rate=full.fulfilled.to_numpy()[ix].sum(axis=1)/full.requests.to_numpy()[ix].sum(axis=1)
    boot_ci=np.quantile(boot_rate,[.025,.975])
    critical_row=periods.set_index('period').loc[PERIODS[0]]
    day_row=periods.set_index('period').loc[PERIODS[1]]
    june=monthly.loc[monthly.month.eq('2026-06')&monthly.period.eq(PERIODS[0])].iloc[0]
    may_period=monthly.loc[monthly.month.eq('2026-05')&monthly.period.eq(PERIODS[0])].iloc[0]
    total_requests=int(a.requests.sum());total_fulfilled=int(a.fulfilled_requests.sum())
    peak=profile.loc[profile.unfulfilled.idxmax()]
    summary={'calendar_days':int(days),'terminal_requests':total_requests,'terminal_fulfilled':total_fulfilled,
        'terminal_unfulfilled':int(a.unfulfilled_requests.sum()),'terminal_fulfilment':total_fulfilled/total_requests,
        'critical_hours':CRITICAL_HOURS,'critical_requests':int(critical_row.requests),
        'critical_fulfilled':int(critical_row.fulfilled),'critical_unfulfilled':int(critical_row.unfulfilled),
        'critical_fulfilment':float(critical_row.fulfilment_rate),
        'critical_unfulfilled_share':float(critical_row.share_of_airport_unfulfilled),
        'critical_unfulfilled_per_day':float(critical_row.unfulfilled_per_calendar_day),
        'daytime_fulfilment':float(day_row.fulfilment_rate),
        'critical_mean_online_per_terminal_hour':float(critical_row.mean_online_per_zone_hour),
        'daytime_mean_online_per_terminal_hour':float(day_row.mean_online_per_zone_hour),
        'critical_eta':float(critical_row.request_weighted_eta_min),'critical_surge':float(critical_row.request_weighted_surge),
        'may_critical_fulfilment':float(may_period.fulfilment_rate),'june_critical_fulfilment':float(june.fulfilment_rate),
        'highest_loss_hour':int(peak.hour),'highest_loss_per_terminal_hour':float(peak.unfulfilled/peak.zone_hours),
        'full_nights':len(full),'full_night_rate':float(full.fulfilled.sum()/full.requests.sum()),
        'full_night_rate_ci':[float(v) for v in boot_ci],
        'night_fill_sd':float(full.fulfilment_rate.std(ddof=1))}
    end_sensitivity=[]
    for exclude in [False,True]:
        z=a.loc[a.hour_ts.lt(CUTOFF.floor('h'))] if exclude else a
        end_sensitivity.append({'exclude_final_hour':exclude,'requests':int(z.requests.sum()),
                                'fulfilment_rate':z.fulfilled_requests.sum()/z.requests.sum()})
    tables={'hour_profile':profile,'terminal_hour_profile':aggregate_airport_hours(a,['zone_id','hour']),
        'period_summary':periods,'monthly_period_summary':monthly,'monthly_hour_profile':monthly_hour,
        'zone_summary':aggregate_airport_hours(h,['zone_id','zone_type']),
        'zone_type_summary':aggregate_airport_hours(h,['zone_type']),
        'weekday_weekend_summary':aggregate_airport_hours(a,['weekend','period']),
        'night_blocks':nights,'complete_night_blocks':full,
        'final_hour_sensitivity':pd.DataFrame(end_sensitivity),
        'night_bootstrap_interval':pd.DataFrame([{'complete_nights':len(full),'point':summary['full_night_rate'],
            'ci95_low':boot_ci[0],'ci95_high':boot_ci[1],'resamples':bootstraps}])}
    return tables,summary


def analyse_airport_trips(audit, buffer_hours=24, bootstraps=2000, seed=20260630):
    """Sample-only cancellation/next-fare evidence, common support and explicit deadhead scenarios."""
    t=audit.trips.copy()
    r=t.loc[t.captain_cancelled.eq(0)&t.request_ts.le(CUTOFF-pd.Timedelta(hours=buffer_hours))].copy()
    destination=t.groupby('drop_zone_type').agg(sample_rows=('trip_id','size'),
        cancelled=('captain_cancelled','sum'),cancel_rate=('captain_cancelled','mean'),
        mean_distance_km=('trip_distance_km','mean'),reported_mean_fare_inr=('fare_inr','mean'))
    ret=r.groupby('drop_zone_type').agg(return_eligible_records=('trip_id','size'),
        next_fare=('got_return_fare_within_20min','sum'),next_fare_rate=('got_return_fare_within_20min','mean'))
    destination=destination.join(ret)
    destination['no_next_fare_rate']=1-destination.next_fare_rate
    # Resample whole calendar days; this is sample uncertainty, not a representativeness guarantee.
    dates=sorted(t.date.unique())
    rng=np.random.default_rng(seed);idx=rng.integers(0,len(dates),(bootstraps,len(dates)))
    boot_outcomes={}
    for field in destination.index:
        for outcome,frame in [('cancel',t),('next_fare',r)]:
            outcome_col='captain_cancelled' if outcome=='cancel' else 'got_return_fare_within_20min'
            daily=frame.loc[frame.drop_zone_type.eq(field)].groupby('date')[outcome_col].agg(['sum','size']).reindex(dates,fill_value=0)
            rates=daily['sum'].to_numpy()[idx].sum(axis=1)/daily['size'].to_numpy()[idx].sum(axis=1)
            boot_outcomes[(field,outcome)]=rates
            low,high=np.quantile(rates,[.025,.975])
            destination.loc[field,outcome+'_ci95_low']=low
            destination.loc[field,outcome+'_ci95_high']=high
    gaps=[]
    for outcome in ['cancel','next_fare']:
        draws=boot_outcomes[('suburban',outcome)]-boot_outcomes[('city_core',outcome)]
        low,high=np.quantile(draws,[.025,.975])
        gaps.append({'outcome':outcome,'suburban_minus_city_core':destination.loc['suburban',outcome+'_rate']-
            destination.loc['city_core',outcome+'_rate'],'ci95_low':low,'ci95_high':high})
    sensitivity=[]
    for hours in [0,6,24,48]:
        rr=t.loc[t.captain_cancelled.eq(0)&t.request_ts.le(CUTOFF-pd.Timedelta(hours=hours))]
        z=rr.groupby('drop_zone_type').got_return_fare_within_20min.agg(['size','mean']).reset_index()
        z['buffer_hours']=hours;sensitivity.append(z)
    # Directly standardize cancellation rates on shared terminal/month/hour/distance strata.
    b=t.loc[t.drop_zone_type.isin(['city_core','suburban'])].copy()
    b['distance_band']=pd.cut(b.trip_distance_km,[0,10,15,20,25,35,np.inf],right=False)
    keys=['pickup_zone_id','month','hour','distance_band']
    strata=b.groupby(keys+['drop_zone_type'],observed=True).captain_cancelled.agg(['size','mean']).unstack('drop_zone_type')
    supported=strata.loc[strata[('size','city_core')].ge(10)&strata[('size','suburban')].ge(10)]
    weights=supported[('size','city_core')]+supported[('size','suburban')]
    std_city=np.average(supported[('mean','city_core')],weights=weights)
    std_sub=np.average(supported[('mean','suburban')],weights=weights)
    standardized=pd.DataFrame([{'supported_strata':len(supported),'supported_rows':int(weights.sum()),
        'eligible_sample_rows':len(b),'fraction_supported':weights.sum()/len(b),
        'city_core_standardized_cancel':std_city,'suburban_standardized_cancel':std_sub,
        'gap_pp':100*(std_sub-std_city)}])
    economics=[]
    for rho in [0,.5,1.0]:
        rr=r.copy()
        rr['assumed_unpaid_km']=rho*rr.trip_distance_km*(1-rr.got_return_fare_within_20min)
        rr['scenario_km']=rr.trip_distance_km+rr.assumed_unpaid_km
        z=rr.groupby('drop_zone_type').agg(records=('trip_id','size'),reported_fare=('fare_inr','sum'),
            loaded_km=('trip_distance_km','sum'),assumed_unpaid_km=('assumed_unpaid_km','sum'),
            total_scenario_km=('scenario_km','sum')).reset_index()
        z['unpaid_distance_fraction_if_no_next_fare']=rho
        z['gross_fare_per_scenario_km']=z.reported_fare/z.total_scenario_km
        economics.append(z)
    by_period=t.groupby(['drop_zone_type','critical']).agg(sample_rows=('trip_id','size'),cancel_rate=('captain_cancelled','mean'))
    by_period=by_period.join(r.groupby(['drop_zone_type','critical']).got_return_fare_within_20min.agg(
        next_fare_n='size',next_fare_rate='mean')).reset_index()
    by_month=t.groupby(['month','drop_zone_type']).captain_cancelled.agg(sample_rows='size',cancel_rate='mean').join(
        r.groupby(['month','drop_zone_type']).got_return_fare_within_20min.agg(next_fare_n='size',next_fare_rate='mean')).reset_index()
    economics=pd.concat(economics,ignore_index=True)
    summary={'sample_rows':len(t),'return_eligible_records':len(r),'return_buffer_hours':buffer_hours,
        'sample_cancel_rate':float(t.captain_cancelled.mean()),
        'suburban_cancel_rate':float(destination.loc['suburban','cancel_rate']),
        'core_cancel_rate':float(destination.loc['city_core','cancel_rate']),
        'suburban_next_fare_rate':float(destination.loc['suburban','next_fare_rate']),
        'core_next_fare_rate':float(destination.loc['city_core','next_fare_rate']),
        'suburban_mean_distance_km':float(destination.loc['suburban','mean_distance_km']),
        'suburban_reported_fare':float(destination.loc['suburban','reported_mean_fare_inr']),
        'core_reported_fare':float(destination.loc['city_core','reported_mean_fare_inr']),
        'standardized_cancel_gap_pp':float((std_sub-std_city)*100),'standardization_coverage':float(weights.sum()/len(b)),
        'sample_exceeds_requests_cells':int(audit.tables['trip_marketplace_reconciliation'].eval('sample_rows > requests').sum()),
        'cancelled_with_return':int((t.captain_cancelled.eq(1)&t.got_return_fare_within_20min.eq(1)).sum())}
    for d in ['suburban','city_core']:
        summary[d+'_gross_fare_per_scenario_km']=float(economics.loc[
            economics.drop_zone_type.eq(d)&economics.unpaid_distance_fraction_if_no_next_fare.eq(1),'gross_fare_per_scenario_km'].iloc[0])
    tables={'destination_sample_outcomes':destination.reset_index(),'sample_destination_gaps':pd.DataFrame(gaps),
        'return_followup_sensitivity':pd.concat(sensitivity,ignore_index=True),
        'destination_period_outcomes':by_period,'destination_month_outcomes':by_month,
        'standardized_cancellation_association':standardized,'reported_fare_distance_scenarios':economics,
        'sample_hour_distribution':t.groupby('hour').size().rename('sample_rows').to_frame()}
    return tables,summary


def airport_intervention_scenarios(market_summary, roster=20, attendance=.8,
        hourly_incentive_inr=30., fallback_inr=60., fallback_nightly_cap=20):
    """Capacity/cost scenarios, not an estimate of unique captains or causal treatment effects."""
    lost=market_summary['critical_unfulfilled_per_day'];window=len(CRITICAL_HOURS)
    rows=[]
    for recovery in [.1,.25,.5]:
        extra=lost*recovery
        for productivity in [.5,.75,1.]:
            rows.append({'recovered_fraction_of_unfulfilled':recovery,
                'extra_fulfilled_per_night':extra,'extra_per_30_day_month':extra*30,
                'assumed_completions_per_effective_captain_hour':productivity,
                'extra_effective_captain_hours':extra/productivity,
                'seven_hour_roster_slots_at_80pct_attendance':extra/productivity/window/attendance})
    target=lost*.1
    expected_cost=roster*window*attendance*hourly_incentive_inr+fallback_inr*fallback_nightly_cap
    max_cost=roster*window*hourly_incentive_inr+fallback_inr*fallback_nightly_cap
    expected_capacity=roster*window*attendance*.75
    sd=market_summary['night_fill_sd']
    mde=(norm.ppf(.975)+norm.ppf(.8))*sd*np.sqrt(2/14)*1.5
    summary={'pilot_roster_slots':roster,'pilot_attendance_assumption':attendance,
        'pilot_recovery_target':.1,'pilot_extra_fulfilled_per_night':target,
        'pilot_extra_per_30_day_month':target*30,'pilot_capacity_at_assumed_productivity':expected_capacity,
        'pilot_expected_nightly_variable_cost':expected_cost,'pilot_max_nightly_variable_cost':max_cost,
        'pilot_cost_per_incremental_fulfilment_at_target':expected_cost/target,
        'pilot_expected_30_day_cost':expected_cost*30,'pilot_max_30_day_cost':max_cost*30,
        'pilot_14_treatment_night_expected_cost':expected_cost*14,'pilot_14_treatment_night_cost_cap':max_cost*14,
        'pilot_hourly_incentive_inr':hourly_incentive_inr,'pilot_fallback_inr':fallback_inr,
        'pilot_fallback_nightly_cap':fallback_nightly_cap,'trial_nights':28,'trial_planning_mde_pp':mde*100,
        'acquisition_decision':'Do not fund a blanket catchment signup-bonus push yet; test timed availability and the return loop.'}
    costs=[]
    for recovery in [0,.05,.1,.2]:
        extra=lost*recovery
        costs.append({'recovered_fraction':recovery,'extra_per_night':extra,
                      'illustrative_variable_cost_per_night':expected_cost,
                      'cost_per_extra_fulfilled_request_inr':expected_cost/extra if extra else np.nan})
    return {'capacity_scenarios':pd.DataFrame(rows),'cost_sensitivity':pd.DataFrame(costs)},summary


def load_part_b_results(output_dir=AIRPORT_OUTPUT_DIR, data_dir=DATA_DIR):
    output_dir=Path(output_dir)
    manifest=json.loads((output_dir/'run_manifest.json').read_text())
    expected={row['file']:row['sha256'] for row in manifest['raw_inputs']}
    if expected!=dict(airport_input_fingerprint(data_dir)):
        raise ValueError('Part B exports are stale. Rerun analysis_partB.ipynb from the current CSVs.')
    return json.loads((output_dir/'summary.json').read_text()),manifest
