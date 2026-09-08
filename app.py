"""Presentation only: shared calculations in utils.py, campaign estimation in the notebook."""
import pandas as pd
import streamlit as st
from utils import (OUTPUT_DIR, AIRPORT_OUTPUT_DIR, DELIVERABLES_DIR, input_fingerprint,
                   load_and_audit, build_onboarding_funnel,
                   summarize_segments, load_part_a_results, load_part_b_results, load_result_table)

st.set_page_config(page_title='Captain supply analysis', page_icon='📊', layout='wide')


@st.cache_data(show_spinner='Loading validated Part A results…')
def dashboard_data(data_signature, result_signature):
    """Invalidate the cache when raw files or notebook exports change."""
    summary, manifest = load_part_a_results()
    core = build_onboarding_funnel(load_and_audit())
    if (len(core.cohort) != summary['cohort_signups']
            or int(core.cohort.a2o30.sum()) != summary['cohort_approved']
            or int(core.cohort.r2a30.sum()) != summary['cohort_active']):
        raise ValueError('Notebook exports do not match the shared calculations. Rerun Part A.')
    return (summary, core.funnel, summarize_segments(core.cohort),
            load_result_table('ranked_recommendations'), load_result_table('data_quality_findings'), manifest)


st.markdown('''
<style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem;}
    [data-testid="stMetricValue"] {color: #193B50;}
    .decision-card {background: #EDF3F6; border-left: 5px solid #245B78;
                    padding: 1rem 1.2rem; border-radius: .35rem; margin: .5rem 0 1rem 0;}
    .decision-card strong {color: #193B50;}
</style>
''', unsafe_allow_html=True)

st.title('Captain supply analysis')
st.caption('Onboarding and airport supply · Extracted 30 June 2026 at 23:59 IST')

with st.sidebar:
    st.header('Submission files')
    st.caption('The notebooks create these files after a successful run.')
    for filename, label in [('memo.pdf', 'Download decision memo'),
                            ('presentation.pdf', 'Download six-slide deck')]:
        file = DELIVERABLES_DIR / filename
        if file.exists():
            st.download_button(label, file.read_bytes(), file_name=filename,
                               mime='application/pdf', width='stretch')
    st.divider()
    st.markdown('**Workflow**')
    st.caption('Run Part-A_analysis.ipynb and Part-B_analysis.ipynb first. The dashboard then reads their validated exports through utils.py.')

st.markdown('''
<div class="decision-card">
<strong>Decision snapshot:</strong> repair onboarding completion and prove timed airport supply before scaling WA002 communication or airport acquisition.
</div>
''', unsafe_allow_html=True)

part_a, part_b = st.tabs(['Part A — Onboarding', 'Part B — Airport supply'])

with part_a:
    try:
        export_signature = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in
            [OUTPUT_DIR/'summary.json', OUTPUT_DIR/'run_manifest.json', OUTPUT_DIR/'ranked_recommendations.csv'])
        summary, funnel, segments, recommendations, quality, manifest = dashboard_data(
            input_fingerprint(), export_signature)
    except (FileNotFoundError, ValueError, AssertionError) as error:
        st.error(f'Part A results are unavailable or need refreshing: {error}')
        st.code('.venv/bin/python -m jupyter nbconvert --to notebook --execute --inplace Part-A_analysis.ipynb', language='bash')
    else:
        st.info('January–May signups with a complete 30-day follow-up window. June is excluded from the mature cohort so newer signups are not compared with less mature ones.')
        cols = st.columns(4)
        for col, label, value in zip(cols, ['Mature signups', 'Approved by day 30', 'Approval rate', 'First order by day 30'],
                [f"{summary['cohort_signups']:,}", f"{summary['cohort_approved']:,}",
                 f"{summary['a2o30']:.1%}", f"{summary['r2a30']:.1%}"]):
            col.metric(label, value)

        st.subheader('Funnel and document losses')
        st.markdown(f"**RC loses the most captains: {summary['rc_lost']:,}.** Insurance has a late-stage recovery pool "
                    f"of {summary['insurance_no_upload']:,} captains who cleared Fitness but did not upload Insurance by D30.")
        st.image(str(OUTPUT_DIR/'funnel.png'), width='stretch')
        st.caption('Losses belong to the first unpassed document. ERickshaw skips Permit. '
                   'No successful upload does not establish why the captain stopped.')
        with st.expander('Stage counts and conditional rates'):
            detail = funnel[['eligible','uploaded','passed','not_applicable_skips','lost',
                             'no_upload','failed_unresolved','conditional_pass_rate']].copy()
            detail['conditional_pass_rate'] *= 100
            st.dataframe(detail.rename(columns={'conditional_pass_rate':'Conditional pass (%)'}), width='stretch')

        st.subheader('Segment comparisons')
        dimensions = {'City':'city', 'Vehicle':'vehicle_type', 'Acquisition channel':'acquisition_channel',
                      'Device tier':'device_tier', 'Signup month':'signup_month',
                      'App language':'app_language', 'Age band':'age_band'}
        selected = st.selectbox('Compare approval rate by', list(dimensions), index=3, key='segment_dimension')
        comparison = segments.loc[segments.dimension.eq(dimensions[selected])].sort_values('a2o30', ascending=False)
        chart = comparison[['segment','a2o30']].rename(columns={'a2o30':'A2O D30 (%)'}).copy()
        chart['A2O D30 (%)'] *= 100
        st.bar_chart(chart, x='segment', y='A2O D30 (%)', color='#245B78', height=300)
        detail = comparison[['segment','signups','approved','not_approved_d30','a2o30','ci95_low','ci95_high']].copy()
        for col in ['a2o30','ci95_low','ci95_high']:
            detail[col] = (detail[col]*100).round(2)
        st.dataframe(detail.rename(columns={'a2o30':'A2O (%)','ci95_low':'95% CI low (%)',
                                            'ci95_high':'95% CI high (%)'}), hide_index=True, width='stretch')
        st.caption('Descriptive differences, not product effects or acquisition ROI. '
                   'The campaign and recommendations below use their full analysis populations.')

        st.subheader('WA002: should we scale it?')
        left, right = st.columns(2)
        left.metric('Observed adjusted difference', f"{summary['campaign_association_pp']:+.1f} pp")
        right.metric('Unique-reach headroom', f"{summary['campaign_reach_ceiling']:.2f}×")
        low, high = summary['campaign_ci_pp']
        st.markdown(f"95% sampling interval: **{low:+.1f} to {high:+.1f} percentage points**. "
                    f"The raw gap was {summary['campaign_naive_pp']:.1f} points, but every recipient had already cleared RC.")
        st.warning('This is an observed difference, not proof that WA002 caused the improvement. Hold the blanket 5× request and run the randomized RC-stage test.')
        st.image(str(OUTPUT_DIR/'campaign_estimates.png'), width='stretch')
        st.caption(f"{summary['campaign_n']:,} captains who cleared Registration Certificate; send within 24 hours of that pass, approval within 30 days. "
                   'This clock differs from the signup-based funnel. Unmeasured targeting is outside the interval.')
        with st.expander('Campaign design, model sensitivity and trial size'):
            for label, name in [('Primary estimates','campaign_primary_estimates'),
                                ('Model sensitivity','campaign_crossfit_model_sensitivity'),
                                ('Timing sensitivity','campaign_timing_sensitivity'),
                                ('Randomized trial sizing','campaign_randomized_trial_size')]:
                st.markdown(f'**{label}**')
                st.dataframe(load_result_table(name), hide_index=True, width='stretch')

        st.subheader('Three recommendations, ranked')
        st.caption('Operational gains are scenarios using 50% incremental recovery and historical peer conversion. '
                   'They are not measured effects. Cost inputs are illustrative.')
        for row in recommendations.sort_values('rank').to_dict('records'):
            st.markdown(f"**{row['rank']}. {row['action']}**")
            st.write(row['what_to_do'])
            gain = row['expected_impact_base_scenario_per_month']
            st.write(f'Base scenario: **~{gain:.0f} additional approvals/month**.' if pd.notna(gain)
                     else '**No causal gain booked; test before expanding.**')
            with st.expander(f"Working, cost and success measures — recommendation {row['rank']}"):
                st.write(row['working'])
                st.write(f"Illustrative variable cost: INR {row['illustrative_variable_cost_inr_per_month']:,.0f}/month, excluding fixed costs.")
                if row['rank']==3:
                    st.caption('This prices full additional reach, not experiment spend.')
                st.write(f"Owner: {row['owner']}")
                st.write(f"Risks: {row['risks']}")
                st.write(f"Measure: {row['measure']}")
        with st.expander('Data quality and assumptions'):
            st.dataframe(quality, hide_index=True, width='stretch')
            st.image(str(OUTPUT_DIR/'cohort_maturity.png'), width='stretch')
            st.caption(f"Cutoff: {manifest['cutoff_ist']}. Raw files are unchanged. "
                       'Campaign exports are checked against the current input hashes.')

with part_b:
    try:
        b_summary, b_manifest = load_part_b_results()
        b_files = ['marketplace_summary.csv', 'destination_sample_outcomes.csv', 'pilot_summary.csv']
        b_tables = {name[:-4]: load_result_table(name[:-4], AIRPORT_OUTPUT_DIR) for name in b_files}
    except (FileNotFoundError, ValueError, AssertionError) as error:
        st.error(f'Part B results are unavailable or need refreshing: {error}')
        st.code('.venv/bin/python -m jupyter nbconvert --to notebook --execute --inplace Part-B_analysis.ipynb', language='bash')
    else:
        st.info('The hourly file measures marketplace demand and supply. The trip file is a separate sample, so its rates are not multiplied into marketplace totals.')
        cols = st.columns(4)
        for col, label, value in zip(cols,
            ['Night fulfilment (21–04)', 'Unfulfilled per day', 'Daytime fulfilment', 'Night surge'],
            [f"{b_summary['critical_fulfilment']:.1%}", f"{b_summary['critical_unfulfilled_per_day']:.0f}",
             f"{b_summary['daytime_fulfilment']:.1%}", f"{b_summary['critical_surge']:.2f}×"]):
            col.metric(label, value)

        st.subheader('B1 · When and how much is the airport short?')
        st.markdown(f"The **21:00–04:00** window (crossing midnight) fulfils **{b_summary['critical_fulfilment']:.1%}** of requests, "
                    f"leaving **{b_summary['critical_unfulfilled']:,}** unfulfilled over the 61-day period "
                    f"(**{b_summary['critical_unfulfilled_per_day']:.0f}/day**). It accounts for "
                    f"**{b_summary['critical_unfulfilled_share']:.1%}** of airport-terminal unfulfilled requests; daytime "
                    f"fulfilment is **{b_summary['daytime_fulfilment']:.1%}**.")
        st.image(str(AIRPORT_OUTPUT_DIR / 'airport_period_mismatch.png'), width='stretch')
        st.image(str(AIRPORT_OUTPUT_DIR / 'airport_month_validation.png'), width='stretch')
        with st.expander('Hourly and period tables'):
            st.dataframe(load_result_table('period_summary', AIRPORT_OUTPUT_DIR), hide_index=True, width='stretch')
            st.dataframe(load_result_table('hour_profile', AIRPORT_OUTPUT_DIR), hide_index=True, width='stretch')

        st.subheader('B2 · What happens after an airport trip?')
        trip = b_tables['destination_sample_outcomes']
        st.markdown(f"Suburban trips cancel at **{b_summary['suburban_cancel_rate']:.1%}** versus "
                    f"**{b_summary['core_cancel_rate']:.1%}** for city-core. Only **{b_summary['suburban_next_fare_rate']:.1%}** "
                    f"of suburban trips get a return fare within 20 minutes versus **{b_summary['core_next_fare_rate']:.1%}** "
                    'for city-core. This points to a deadhead/return-loop problem; the sample has no unique captain ID or earnings.')
        st.image(str(AIRPORT_OUTPUT_DIR / 'airport_trip_outcomes.png'), width='stretch')
        st.dataframe(trip, hide_index=True, width='stretch')
        with st.expander('Trip sensitivity and data quality'):
            st.dataframe(load_result_table('return_followup_sensitivity', AIRPORT_OUTPUT_DIR), hide_index=True, width='stretch')
            st.dataframe(load_result_table('data_quality_findings', AIRPORT_OUTPUT_DIR), hide_index=True, width='stretch')

        st.subheader('B3 · Intervention recommendation')
        st.warning('Do not fund a blanket airport-catchment signup bonus yet. Run a timed availability and return-utilisation pilot first.')
        st.markdown(f"A 10% recovery target is **{b_summary['pilot_extra_fulfilled_per_night']:.0f} extra fulfilments/night**. "
                    f"The illustrative 20-slot roster has capacity for **{b_summary['pilot_capacity_at_assumed_productivity']:.0f}/night** "
                    f"at an expected variable cost of **INR {b_summary['pilot_expected_nightly_variable_cost']:,.0f}/night**, "
                    f"or **INR {b_summary['pilot_cost_per_incremental_fulfilment_at_target']:.0f} per incremental fulfilment** at the target. "
                    'These are planning assumptions, not a permanent-program forecast.')
        st.image(str(AIRPORT_OUTPUT_DIR / 'airport_pilot_capacity.png'), width='stretch')
        with st.expander('Pilot scenarios'):
            st.dataframe(load_result_table('capacity_scenarios', AIRPORT_OUTPUT_DIR), hide_index=True, width='stretch')
            st.dataframe(load_result_table('cost_sensitivity', AIRPORT_OUTPUT_DIR), hide_index=True, width='stretch')
        st.caption(f"Validated May critical fulfilment {b_summary['may_critical_fulfilment']:.1%}; June "
                   f"{b_summary['june_critical_fulfilment']:.1%}. Raw airport files are unchanged; export hashes are checked.")
